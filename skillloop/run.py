"""Experiment runner: one group x stage x seed grid, streamed to JSONL.

Reuses the environment as-is (``envs.match.run_match`` + ``npc`` judging) and adds
only what the course experiments need:

* a **group** switch (scripted / contestant / llm_empty / static / dynamic) so the
  four conditions differ in exactly one thing: the skill library;
* a per-game row with the fields the paired gate needs (``stage``, ``seed``,
  ``three_star``) plus cost/audit fields (``llm_calls``, ``library_version``,
  ``model``, ``prompt`` hashes are inside the agent decisions);
* resume by ``(stage, seed)`` and a fingerprint sidecar so a run cannot be mixed
  with a different library version or engine build.

Usage::

    python -m skillloop.run --group scripted --stages all --seeds 3 --workers 6
    python -m skillloop.run --group dynamic --library data/skills/lib1 --seeds 50 \
        --out data/runs/dynamic.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from common.seeding import derive_seed
from envs.decks import deck_spec
from envs.match import run_match
from envs.policy import expert_policy
from npc.objectives import evaluate_stage
from npc.policy import npc_policy_for_stage
from npc.spec import NpcStage, get_stage, stage_names
from skillloop.agent import AgentConfig, SkillAgentPolicy
from skillloop.library import SkillLibrary
from skillloop.render import StateRenderer, TaskSpec

GROUPS = ("scripted", "contestant", "llm_empty", "static", "dynamic")


@dataclass(frozen=True)
class RunSpec:
    group: str
    stages: tuple[str, ...]
    seeds: tuple[int, ...]
    deck: str
    workers: int = 1
    library_root: str | None = None
    top_k: int = 5
    model: str | None = None
    max_calls: int = 80
    rotation: str | None = None
    out: str = "data/runs/run.jsonl"

    def fingerprint(self) -> dict[str, Any]:
        library_version = 0
        if self.library_root:
            try:
                library_version = SkillLibrary.load(self.library_root).version
            except FileNotFoundError:
                library_version = -1
        return {
            "group": self.group,
            "stages": list(self.stages),
            "seeds": list(self.seeds),
            "deck": self.deck,
            "library_root": self.library_root,
            "library_version": library_version,
            "top_k": self.top_k,
            "model": self.model,
            "rotation": self.rotation,
            "engine_lock": _sha1(Path("configs/engine.lock")),
            "hp_table": _sha1(Path("configs/npc/hp_table.json")),
            "stages": _sha1(Path("configs/npc/npc_stages_mond_liyue.json")),
        }


def _sha1(path: Path) -> str:
    if not path.exists():
        return "missing"
    return hashlib.sha1(path.read_bytes()).hexdigest()[:12]


def _assets():
    from envs.policy import _asset_catalog

    return _asset_catalog()


def task_spec(stage: NpcStage, deck: str) -> TaskSpec:
    return TaskSpec(
        stage=stage.name,
        display=stage.display or stage.name,
        objectives=tuple(obj.text for obj in stage.objectives),
        deck=deck,
        objective_types=tuple(obj.type for obj in stage.objectives),
    )


def build_policy(spec: RunSpec, stage: NpcStage, *, client: Any = None):
    """Return the policy for one group (the only thing that differs across groups)."""
    if spec.group == "scripted":
        return expert_policy(spec.deck, seed=0)
    if spec.group == "contestant":
        from npc.contestant import build_contestant_policy

        return build_contestant_policy(deck_spec(spec.deck), stage, inject_objectives=True)
    if spec.group not in GROUPS:
        raise SystemExit(f"unknown group {spec.group!r}; expected one of {GROUPS}")

    library = None
    if spec.group in ("static", "dynamic"):
        if not spec.library_root:
            raise SystemExit(f"group {spec.group!r} needs --library")
        library = SkillLibrary.load(spec.library_root)
    renderer = StateRenderer(_assets(), opponent_name=f"NPC({stage.display or stage.name})")
    return SkillAgentPolicy(
        expert_policy(spec.deck, seed=0),
        client,
        renderer=renderer,
        task=task_spec(stage, spec.deck),
        library=library,
        config=AgentConfig(model=spec.model, max_calls_per_game=spec.max_calls, top_k=spec.top_k),
    )


def play_one(payload: dict[str, Any]) -> dict[str, Any]:
    """Play one (stage, seed) game and return a JSON-safe row.

    Runs inside a worker process: the LLM client and the library are constructed
    here so nothing unpicklable (sockets, file handles) crosses the process boundary.
    """
    spec = RunSpec(**payload["spec"])
    stage = get_stage(payload["stage"])
    seed = int(payload["seed"])

    client = None
    if spec.group.startswith("llm") or spec.group in ("static", "dynamic"):
        from common.llm import build_llm_client

        client = build_llm_client()
        if client is None:
            return {"stage": stage.name, "seed": seed, "group": spec.group, "error": "no_llm_config"}

    policy0 = build_policy(spec, stage, client=client)
    contestant = deck_spec(spec.deck)
    npc_deck = type(contestant)(
        name=f"npc_{stage.name}", characters=stage.deck_characters, cards=contestant.cards
    )
    started = time.perf_counter()
    record = run_match(
        contestant,
        npc_deck,
        policy0,
        npc_policy_for_stage(stage, rotation=spec.rotation),
        seed=seed,
        record_decisions=False,
        record_events=True,
        record_final_state=True,
    )
    elapsed = time.perf_counter() - started
    stars = evaluate_stage(stage, record)
    decisions = getattr(policy0, "decisions", []) or []
    usage = getattr(client, "usage", None)
    return {
        "task_index": payload.get("task_index", 0),
        "stage": stage.name,
        "seed": seed,
        "deck": spec.deck,
        "group": spec.group,
        "model": spec.model,
        "library_version": (SkillLibrary.load(spec.library_root).version if spec.library_root else 0),
        "winner": record.winner,
        "rounds": record.rounds,
        "three_star": bool(stars.three_star),
        "stars": int(stars.stars),
        "n_objectives": len(stars.objectives),
        "objectives": {item.type: bool(item.achieved) for item in stars.objectives},
        "error": record.error,
        "truncated": record.truncated,
        "seconds": round(elapsed, 2),
        "llm_calls": len(decisions),
        "llm_errors": sum(1 for d in decisions if d.error),
        "llm_seconds": round(sum(d.latency for d in decisions), 2),
        "cited_skills": sorted({sid for d in decisions for sid in d.used}),
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "cached_tokens": int(getattr(usage, "cached_tokens", 0) or 0),
        "cost_usd": round(float(getattr(usage, "cost_usd", 0.0) or 0.0), 6),
    }


def run(spec: RunSpec, *, progress: bool = True) -> dict[str, Any]:
    out = Path(spec.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    meta_path = out.with_suffix(out.suffix + ".meta.json")
    fingerprint = spec.fingerprint()

    if meta_path.exists():
        previous = json.loads(meta_path.read_text(encoding="utf-8"))
        if previous.get("fingerprint") != fingerprint:
            raise SystemExit(
                f"{out} was produced by a different spec; use another --out or delete it\n"
                f"  old: {previous.get('fingerprint')}\n  new: {fingerprint}"
            )
    else:
        meta_path.write_text(json.dumps({"fingerprint": fingerprint}, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")

    done: set[tuple[str, int]] = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if not row.get("error"):
                done.add((str(row["stage"]), int(row["seed"])))

    pending = [
        {"spec": spec.__dict__, "stage": stage, "seed": seed, "task_index": index}
        for index, (stage, seed) in enumerate(
            (stage, seed) for stage in spec.stages for seed in spec.seeds
        )
        if (stage, seed) not in done
    ]
    if progress:
        print(f"[run] {len(pending)} pending / {len(done)} done -> {out}", flush=True)

    rows: list[dict[str, Any]] = []
    started = time.perf_counter()
    with out.open("a", encoding="utf-8") as handle:
        if spec.workers <= 1:
            for item in pending:
                row = play_one(item)
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                rows.append(row)
                if progress:
                    _print_row(row)
        else:
            # ``spawn`` (not fork): the parent has already imported the engine, and
            # forking a process with a live V8 isolate deadlocks the child on its first
            # engine call. Spawn costs ~2 s per worker and is safe.
            context = mp.get_context("spawn")
            with ProcessPoolExecutor(max_workers=spec.workers, mp_context=context) as pool:
                futures = {pool.submit(play_one, item): item for item in pending}
                for future in as_completed(futures):
                    row = future.result()
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                    handle.flush()
                    rows.append(row)
                    if progress:
                        _print_row(row)

    summary = summarize(rows)
    summary["seconds"] = round(time.perf_counter() - started, 1)
    summary["out"] = str(out)
    return summary


def _print_row(row: dict[str, Any]) -> None:
    flag = "3★" if row.get("three_star") else f"{row.get('stars', 0)}★"
    print(f"  {row['stage']:16s} seed={row['seed']:<5d} {flag:>2s} calls={row.get('llm_calls', 0):<3d}"
          f" {row.get('seconds', 0):>5.1f}s {row.get('error') or ''}", flush=True)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [row for row in rows if not row.get("error") and not row.get("truncated")]
    if not valid:
        return {"games": 0, "valid": 0}
    per_stage: dict[str, dict[str, Any]] = {}
    for row in valid:
        bucket = per_stage.setdefault(row["stage"], {"n": 0, "three_star": 0, "stars": 0})
        bucket["n"] += 1
        bucket["three_star"] += int(bool(row["three_star"]))
        bucket["stars"] += int(row.get("stars") or 0)
    return {
        "games": len(rows),
        "valid": len(valid),
        "errors": len(rows) - len(valid),
        "three_star_rate": round(sum(1 for r in valid if r["three_star"]) / len(valid), 4),
        "mean_stars_per_objective": round(
            sum((r.get("stars") or 0) / max(1, r.get("n_objectives") or 1) for r in valid) / len(valid), 4
        ),
        "llm_calls": sum(int(r.get("llm_calls") or 0) for r in valid),
        "prompt_tokens": sum(int(r.get("prompt_tokens") or 0) for r in valid),
        "completion_tokens": sum(int(r.get("completion_tokens") or 0) for r in valid),
        "cached_tokens": sum(int(r.get("cached_tokens") or 0) for r in valid),
        "cost_usd": round(sum(float(r.get("cost_usd") or 0.0) for r in valid), 6),
        "per_stage": {
            stage: {
                "n": bucket["n"],
                "three_star_rate": round(bucket["three_star"] / bucket["n"], 4),
                "mean_stars": round(bucket["stars"] / bucket["n"], 3),
            }
            for stage, bucket in sorted(per_stage.items())
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", required=True, choices=GROUPS)
    parser.add_argument("--stages", default="all", help="'all' or comma-separated stage names")
    parser.add_argument("--seeds", default="1,2,3", help="count (N -> 0..N-1) or comma-separated list")
    parser.add_argument("--deck", default="superconduct_aggro")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--library", default=None, help="skill library directory (static/dynamic)")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-calls", type=int, default=80)
    parser.add_argument("--rotation", default=None)
    parser.add_argument("--out", default=None)
    return parser


def _seed_list(value: str) -> tuple[int, ...]:
    if "," in value:
        return tuple(int(item) for item in value.split(",") if item.strip())
    return tuple(range(int(value)))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    stages = tuple(stage_names()) if args.stages == "all" else tuple(
        item.strip() for item in args.stages.split(",") if item.strip()
    )
    spec = RunSpec(
        group=args.group,
        stages=stages,
        seeds=_seed_list(args.seeds),
        deck=args.deck,
        workers=max(1, args.workers),
        library_root=args.library,
        top_k=args.top_k,
        model=args.model,
        max_calls=args.max_calls,
        rotation=args.rotation,
        out=args.out or f"data/runs/{args.group}_{args.deck}.jsonl",
    )
    summary = run(spec)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("valid") else 1


if __name__ == "__main__":
    sys.exit(main())
