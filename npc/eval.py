"""NPC PvE evaluation entry point (PLAN.md §4.4, PvE-B3; feeds the PvE-v1 gate).

Runs a contestant deck against a stage's NPC lineup (player 1 = the stage
enemies driven by :class:`npc.policy.NpcPolicy`) over a grid of stages x seeds,
and reports **win rate** plus **three-star rate** (all challenge objectives met).

Execution habits mirror ``eval/`` (PLAN §0.1 C8):

* per-match rows are streamed to ``data/npc/<run>.jsonl`` as they finish and
  resumed by ``task_index``;
* a ``.meta.json`` sidecar pins a spec fingerprint so a resumed run refuses to
  mix results from a different grid;
* a small aggregate summary is written to ``reports/npc/<run>.json``.

Concurrency: ``workers`` > 1 fans the grid out over a process pool; each worker
rebuilds the stage and policies from serializable inputs so the result is
identical to the single-process run.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import hashlib
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from common.jsonl import ensure_trailing_newline
from common.lock import load_engine_lock, runtime_metadata
from common.paths import data_dir, ensure_dir, reports_dir
from envs.decks import deck_spec
from envs.match import run_match
from envs.policy import build_policy
from reps.schema import DeckSpec

from .objectives import evaluate_stage
from .policy import npc_policy_for_stage
from .spec import get_stage, load_stages

CONTESTANT_POLICY_SPEC = "contestant"  # npc.contestant.CONTESTANT_POLICY_SPEC
CONTESTANT_PLAIN_POLICY_SPEC = "contestant_plain"  # same, without objective injection


@dataclass(frozen=True)
class NpcEvalSpec:
    stage_names: tuple[str, ...]
    contestant: DeckSpec
    seeds: tuple[int, ...]
    policy0: str = "heuristic"
    rotation: str | None = None
    tag: str = "npc_eval"
    base_seed: int = 0

    def task_grid(self) -> list[tuple[str, int]]:
        # ``base_seed`` is folded into every match seed (the ``base_seed + raw``
        # convention used by ``envs.rollout.make_task_grid``), so two specs that
        # differ only in ``base_seed`` play *different* games — consistent with
        # the fingerprint, which already includes ``base_seed``. Previously the
        # field was read only by the fingerprint, so callers passing a nonzero
        # ``base_seed`` got identical games under a different fingerprint and a
        # resumed run was wrongly rejected.
        return [
            (stage, int(self.base_seed) + int(seed))
            for stage in self.stage_names
            for seed in self.seeds
        ]


@dataclass(frozen=True)
class NpcTask:
    """Serializable single-match request for the ``npc.eval_worker`` subprocess."""

    index: int
    stage: str
    seed: int
    deck: dict[str, Any]
    policy0: str = "heuristic"
    rotation: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": int(self.index),
            "stage": self.stage,
            "seed": int(self.seed),
            "deck": self.deck,
            "policy0": self.policy0,
            "rotation": self.rotation,
        }


@dataclass
class NpcEvalResult:
    spec: NpcEvalSpec
    rows: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "tag": self.spec.tag,
            "deck": self.spec.contestant.name,
            "policy0": self.spec.policy0,
            "rotation": self.spec.rotation,
            "seeds": list(self.spec.seeds),
            "n_matches": len(self.rows),
            "metadata": self.metadata,
            "overall": _aggregate(self.rows),
            "stages": _aggregate_by(self.rows, "stage"),
            "objectives": _objective_rates(self.rows),
        }


# --------------------------------------------------------------------------- #
# one match
# --------------------------------------------------------------------------- #
def run_npc_match(
    *,
    stage_name: str,
    contestant: DeckSpec,
    seed: int,
    policy0_spec: str = "heuristic",
    rotation: str | None = None,
    task_index: int = 0,
) -> dict[str, Any]:
    """Play one contestant-vs-NPC match and return a JSON-safe result row."""
    from common.seeding import derive_seed

    stage = get_stage(stage_name)
    npc_deck = DeckSpec(
        name=f"npc_{stage.name}",
        characters=stage.deck_characters,
        cards=contestant.cards,
    )
    if policy0_spec in (CONTESTANT_POLICY_SPEC, CONTESTANT_PLAIN_POLICY_SPEC):
        # Stage-aware contestant (scripted expert base + optional objective
        # injection); see npc/contestant.py. Purely additive branch: every other
        # spec keeps going through envs.policy.build_policy.
        from .contestant import build_contestant_policy

        policy0 = build_contestant_policy(
            contestant, stage, inject_objectives=(policy0_spec == CONTESTANT_POLICY_SPEC)
        )
    else:
        policy0 = build_policy(
            policy0_spec, seed=derive_seed(seed, "npc_contestant"), role="contestant"
        )
    policy1 = npc_policy_for_stage(stage, rotation=rotation)
    record = run_match(
        contestant,
        npc_deck,
        policy0,
        policy1,
        seed=int(seed),
        record_decisions=True,
        record_events=True,
        record_final_state=True,
    )
    stars = evaluate_stage(stage, record)
    objective_keys = _objective_keys(stars.objectives)
    row: dict[str, Any] = {
        "task_index": int(task_index),
        "index": int(task_index),
        "stage": stage.name,
        "region": stage.region,
        "deck": contestant.name,
        "policy0": policy0_spec,
        "rotation": rotation,
        "seed": int(seed),
        "npc_characters": list(stage.deck_characters),
        "n_objectives": len(stage.objectives),
        "objectives_verified": bool(stage.objectives_verified),
        "winner": record.winner,
        "rounds": int(record.rounds),
        "stars": int(stars.stars),
        "three_star": bool(stars.three_star),
        "objectives": {
            key: objective.achieved
            for key, objective in zip(objective_keys, stars.objectives, strict=True)
        },
        "objective_details": {
            key: objective.detail
            for key, objective in zip(objective_keys, stars.objectives, strict=True)
        },
        "decisions0": int(record.decisions0),
        "decisions1": int(record.decisions1),
        "fallbacks0": int(record.fallbacks0),
        "fallbacks1": int(record.fallbacks1),
        "io_errors0": list(record.io_errors0),
        "io_errors1": list(record.io_errors1),
        "error": record.error,
        "truncated": bool(record.truncated),
        "wall_seconds": round(float(record.wall_seconds), 4),
    }
    return row


def _worker(payload: dict[str, Any]) -> dict[str, Any]:
    """In-process single-match runner (also used by ``npc.eval_worker``)."""
    return run_npc_match(
        stage_name=payload["stage"],
        contestant=DeckSpec(
            name=payload["deck"]["name"],
            characters=tuple(payload["deck"]["characters"]),
            cards=tuple(payload["deck"]["cards"]),
        ),
        seed=payload["seed"],
        policy0_spec=payload.get("policy0", "heuristic"),
        rotation=payload.get("rotation"),
        task_index=payload.get("index", payload.get("task_index", 0)),
    )


# --------------------------------------------------------------------------- #
def _rate(values: Iterable[bool]) -> dict[str, float]:
    from eval.stats import estimate_rate

    flags = [bool(value) for value in values]
    wins = sum(1 for flag in flags if flag)
    return estimate_rate(wins, len(flags)).as_dict()


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    games = len(rows)
    wins = sum(1 for row in rows if row["winner"] == 0)
    three_star = sum(1 for row in rows if row["three_star"])
    errors = sum(1 for row in rows if row["error"])
    mean_stars = (sum(int(row["stars"]) for row in rows) / games) if games else 0.0
    mean_rounds = (sum(int(row["rounds"]) for row in rows) / games) if games else 0.0
    return {
        "games": games,
        "wins": wins,
        "losses": sum(1 for row in rows if row["winner"] == 1),
        "draws": sum(1 for row in rows if row["winner"] is None),
        "errors": errors,
        "win_rate": (wins / games) if games else 0.0,
        "three_star": three_star,
        "three_star_rate": (three_star / games) if games else 0.0,
        "mean_stars": mean_stars,
        "mean_rounds": mean_rounds,
        "win_rate_ci": _rate(row["winner"] == 0 for row in rows),
        "three_star_ci": _rate(row["three_star"] for row in rows),
    }


def _aggregate_by(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row[key]), []).append(row)
    return {name: _aggregate(group) for name, group in sorted(grouped.items())}


def _objective_keys(objectives: Iterable[Any]) -> list[str]:
    """Per-objective dict keys that stay unique when a type repeats.

    The challenge schema allows several objectives of the same type (e.g. two
    ``play_event_cards`` with different counts). Keying the result dicts by
    ``type`` alone collapsed them and made ``n_objectives`` disagree with the
    recorded dict; duplicates get a ``#<n>`` suffix while a unique type keeps its
    bare name, so existing per-type reports are unchanged.
    """
    seen: dict[str, int] = {}
    keys: list[str] = []
    for objective in objectives:
        objective_type = str(objective.type)
        ordinal = seen.get(objective_type, 0)
        keys.append(objective_type if ordinal == 0 else f"{objective_type}#{ordinal}")
        seen[objective_type] = ordinal + 1
    return keys


def _objective_rates(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[bool]] = {}
    for row in rows:
        for objective_type, achieved in row.get("objectives", {}).items():
            grouped.setdefault(objective_type, []).append(bool(achieved))
    return {
        objective_type: {
            "games": len(flags),
            "achieved": sum(1 for flag in flags if flag),
            "achieved_rate": (sum(1 for flag in flags if flag) / len(flags)) if flags else 0.0,
        }
        for objective_type, flags in sorted(grouped.items())
    }


# --------------------------------------------------------------------------- #
# spec fingerprint + streaming
# --------------------------------------------------------------------------- #
def _spec_fingerprint(spec: NpcEvalSpec) -> str:
    blob = json.dumps(
        {
            "stage_names": list(spec.stage_names),
            "seeds": list(spec.seeds),
            "deck": {
                "name": spec.contestant.name,
                "characters": list(spec.contestant.characters),
                "cards": list(spec.contestant.cards),
            },
            "policy0": spec.policy0,
            "rotation": spec.rotation,
            "base_seed": spec.base_seed,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _read_rows(raw_path: Path) -> list[dict[str, Any]]:
    """Parse a raw JSONL file, skipping blank/torn trailing lines."""
    rows: list[dict[str, Any]] = []
    if not raw_path.exists():
        return rows
    with raw_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # torn trailing line from a hard kill
    return rows


def _stream_state(spec: NpcEvalSpec, raw_path: Path):
    meta_path = raw_path.with_name(raw_path.name.removesuffix(".jsonl") + ".meta.json")
    fingerprint = _spec_fingerprint(spec)
    done: list[dict[str, Any]] = []
    if raw_path.exists():
        if not meta_path.exists():
            raise RuntimeError(
                f"{raw_path} exists but {meta_path} is missing; cannot resume safely "
                "(delete the raw file or use another --tag)"
            )
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("fingerprint") != fingerprint:
            raise RuntimeError(
                f"{raw_path} was produced by a different spec (fingerprint mismatch); "
                "use another --tag or delete the old raw/meta files"
            )
        done = _read_rows(raw_path)
    else:
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(
            json.dumps({"fingerprint": fingerprint, "tag": spec.tag}, ensure_ascii=False),
            encoding="utf-8",
        )
    ensure_trailing_newline(raw_path)
    return done


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #
def run_npc_eval(
    spec: NpcEvalSpec,
    *,
    raw_path: Path | None = None,
    workers: int = 1,
    progress: bool = False,
) -> NpcEvalResult:
    """Run the stage x seed grid, streaming rows and resuming by ``task_index``.

    When ``raw_path`` is given the parent streams each finished row straight to
    disk (via the worker's ``on_result``) and does not retain it in memory during
    the run; the rows are read back once at the end to build the summary. Without
    ``raw_path`` there is nowhere to stream to, so rows are retained.
    """
    for stage_name in spec.stage_names:
        get_stage(stage_name)  # fail fast on an unknown stage

    grid = list(enumerate(spec.task_grid()))
    done: list[dict[str, Any]] = []
    handle = None
    if raw_path is not None:
        done = _stream_state(spec, raw_path)
        ensure_trailing_newline(raw_path)
        handle = raw_path.open("a", encoding="utf-8")

    done_indices = {int(row.get("task_index", -1)) for row in done}
    pending = [(index, item) for index, item in grid if index not in done_indices]

    # Only populated when there is no raw file to re-read from (see docstring).
    fresh: list[dict[str, Any]] = []
    write_lock = threading.Lock()
    completed = len(done)

    def _record(row: dict[str, Any]) -> None:
        nonlocal completed
        with write_lock:
            if handle is not None:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
            else:
                fresh.append(row)
            completed += 1
        if progress:
            print(
                f"[npc-eval] {completed}/{len(grid)} "
                f"{row['stage']} seed={row['seed']} winner={row['winner']} "
                f"stars={row['stars']} three_star={row['three_star']}",
                flush=True,
            )

    deck_payload = {
        "name": spec.contestant.name,
        "characters": list(spec.contestant.characters),
        "cards": list(spec.contestant.cards),
    }

    try:
        if workers <= 1:
            for index, (stage_name, seed) in pending:
                _record(
                    run_npc_match(
                        stage_name=stage_name,
                        contestant=spec.contestant,
                        seed=seed,
                        policy0_spec=spec.policy0,
                        rotation=spec.rotation,
                        task_index=index,
                    )
                )
        else:
            # Reuse the repo's deadlock-safe JSON-lines subprocess workers
            # (multiprocessing fork deadlocks the engine's embedded JS runtime).
            # ``stream_task_shard`` (not ``run_task_shard``) so the parent never
            # accumulates worker results: each row goes to ``_record`` and is
            # dropped, keeping parent RSS bounded on large grids (A8 pattern).
            from envs.rollout import stream_task_shard

            tasks = [
                NpcTask(
                    index=index,
                    stage=stage_name,
                    seed=seed,
                    deck=deck_payload,
                    policy0=spec.policy0,
                    rotation=spec.rotation,
                )
                for index, (stage_name, seed) in pending
            ]
            if tasks:
                stream_task_shard(
                    tasks,
                    worker_module="npc.eval_worker",
                    workers=int(workers),
                    on_result=_record,
                )
    finally:
        if handle is not None:
            handle.close()

    if handle is not None:
        # Re-read the (resumed + freshly streamed) rows from disk.
        rows = sorted(_read_rows(raw_path), key=lambda row: int(row.get("task_index", -1)))
    else:
        rows = sorted(done + fresh, key=lambda row: int(row.get("task_index", -1)))
    metadata = {
        "engine_lock": load_engine_lock(),
        "runtime": runtime_metadata(),
        "resumed_matches": len(done),
        "workers": int(workers),
        "fingerprint": _spec_fingerprint(spec),
    }
    return NpcEvalResult(spec=spec, rows=rows, metadata=metadata)


def _run_id(spec: NpcEvalSpec) -> str:
    stamp = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    return f"{stamp}_{spec.tag}"


def write_artifacts(result: NpcEvalResult, *, run_id: str | None = None) -> dict[str, Path]:
    """Write the aggregate summary (JSON) + a compact per-stage CSV under reports/."""
    run_id = run_id or _run_id(result.spec)
    out_dir = ensure_dir(reports_dir("npc"))
    summary_path = out_dir / f"{run_id}.json"
    summary_path.write_text(
        json.dumps(result.summary(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    csv_path = out_dir / f"{run_id}.stages.csv"
    fields = [
        "stage",
        "games",
        "wins",
        "win_rate",
        "three_star",
        "three_star_rate",
        "mean_stars",
        "mean_rounds",
        "errors",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for stage_name, stats in result.summary()["stages"].items():
            writer.writerow({"stage": stage_name, **{key: stats.get(key) for key in fields[1:]}})
    return {"summary": summary_path, "stages_csv": csv_path}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _parse_stages(value: str | None) -> tuple[str, ...]:
    if not value or value == "all":
        return tuple(sorted(load_stages()))
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _parse_seeds(value: str | None) -> tuple[int, ...]:
    if not value:
        return (1,)
    return tuple(int(part.strip()) for part in value.split(",") if part.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate a deck against NPC PvE stages")
    parser.add_argument("--stages", default="all", help="comma-separated stage names or 'all'")
    parser.add_argument("--deck", default="superconduct_aggro", help="contestant deck slug (expert pool)")
    parser.add_argument("--seeds", default="1,2,3", help="comma-separated seeds")
    parser.add_argument("--policy", dest="policy0", default="heuristic", help="contestant policy spec (e.g. 'contestant', 'heuristic', 'expert:<slug>')")
    parser.add_argument("--rotation", default=None, help="NPC rotation name (default: greedy)")
    parser.add_argument("--tag", default="npc_eval")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--raw", default=None, help="raw JSONL path (enables resume)")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--progress", action="store_true")
    args = parser.parse_args(argv)

    spec = NpcEvalSpec(
        stage_names=_parse_stages(args.stages),
        contestant=deck_spec(args.deck),
        seeds=_parse_seeds(args.seeds),
        policy0=args.policy0,
        rotation=args.rotation,
        tag=args.tag,
    )
    raw_path = Path(args.raw) if args.raw else None
    if raw_path is None and args.run_id is not None:
        raw_path = ensure_dir(data_dir("npc")) / f"{args.run_id}.jsonl"
    result = run_npc_eval(spec, raw_path=raw_path, workers=args.workers, progress=args.progress)
    paths = write_artifacts(result, run_id=args.run_id)
    summary = result.summary()
    overall = summary["overall"]
    print(
        f"[npc-eval] games={overall['games']} win_rate={overall['win_rate']:.3f} "
        f"three_star_rate={overall['three_star_rate']:.3f} "
        f"-> {paths['summary']}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
