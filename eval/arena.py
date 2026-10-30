"""Arena: run scripted/agent matchups at fixed paired seeds and summarize.

Produces a win-rate matrix (with Wilson CIs) plus an Elo ladder. Raw per-match
records go to ``data/arena/`` (gitignored); the small summary goes to
``reports/arena/`` (committed).
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import hashlib
import json
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable

from common.jsonl import ensure_trailing_newline
from common.lock import load_engine_lock, runtime_metadata
from common.paths import data_dir, ensure_dir, reports_dir
from envs.rollout import MatchTask, make_task_grid, normalize_workers, run_tasks
from eval.ladder import compute_elo
from eval.opponents import Opponent, baseline_opponents, scripted_opponents
from eval.stats import estimate_rate


@dataclass
class ArenaSpec:
    contestants: tuple[Opponent, ...]
    pool: tuple[Opponent, ...]
    seeds: tuple[int, ...]
    workers: int | None = 1
    swap: bool = False
    record_decisions: bool = False
    policy0: str | None = None
    policy1: str | None = None
    tag: str = "arena"
    base_seed: int = 0

    def deck_pairs(self) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        for contestant in self.contestants:
            for opponent in self.pool:
                pairs.append((contestant.deck, opponent.deck))
                if self.swap:
                    pairs.append((opponent.deck, contestant.deck))
        return pairs

    def tasks(self) -> list[MatchTask]:
        pairs = self.deck_pairs()
        tasks = make_task_grid(
            pairs,
            self.seeds,
            policy0=self.policy0 or "expert",
            policy1=self.policy1 or "expert",
            tag=self.tag,
            base_seed=self.base_seed,
        )
        if self.record_decisions:
            tasks = [
                replace(task, record_decisions=True, record_views=True) for task in tasks
            ]
        return tasks


@dataclass
class ArenaResult:
    spec: ArenaSpec
    matches: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def pairing_summary(self) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for match in self.matches:
            key = (match["deck0"], match["deck1"])
            grouped.setdefault(key, []).append(match)
        rows: list[dict[str, Any]] = []
        for (deck0, deck1), group in grouped.items():
            wins0 = sum(1 for m in group if m["winner"] == 0)
            wins1 = sum(1 for m in group if m["winner"] == 1)
            draws = sum(1 for m in group if m["winner"] is None)
            errors = sum(1 for m in group if m["error"])
            rate = estimate_rate(wins0 + 0.5 * draws, len(group))
            rows.append(
                {
                    "deck0": deck0,
                    "deck1": deck1,
                    "games": len(group),
                    "wins0": wins0,
                    "wins1": wins1,
                    "draws": draws,
                    "errors": errors,
                    **{f"rate0_{k}": v for k, v in rate.as_dict().items()},
                }
            )
        rows.sort(key=lambda row: (row["deck0"], row["deck1"]))
        return rows

    def contestant_summary(self) -> list[dict[str, Any]]:
        """Aggregate each contestant over the whole pool (as player 0)."""
        grouped: dict[str, list[dict[str, Any]]] = {}
        contestant_decks = {c.deck for c in self.spec.contestants}
        for match in self.matches:
            if match["deck0"] in contestant_decks:
                grouped.setdefault(match["deck0"], []).append(match)
        rows: list[dict[str, Any]] = []
        for deck, group in grouped.items():
            wins = sum(1 for m in group if m["winner"] == 0)
            draws = sum(1 for m in group if m["winner"] is None)
            losses = sum(1 for m in group if m["winner"] == 1)
            errors = sum(1 for m in group if m["error"])
            rate = estimate_rate(wins + 0.5 * draws, len(group))
            rows.append(
                {
                    "deck": deck,
                    "games": len(group),
                    "wins": wins,
                    "losses": losses,
                    "draws": draws,
                    "errors": errors,
                    **rate.as_dict(),
                }
            )
        rows.sort(key=lambda row: (-row["rate"], row["deck"]))
        return rows

    def elo(self) -> dict[str, float]:
        return compute_elo(self.matches)

    def summary(self) -> dict[str, Any]:
        return {
            "tag": self.spec.tag,
            "seeds": list(self.spec.seeds),
            "workers": self.spec.workers,
            "swap": self.spec.swap,
            "n_matches": len(self.matches),
            "metadata": self.metadata,
            "contestants": self.contestant_summary(),
            "pairings": self.pairing_summary(),
            "elo": self.elo(),
        }


def _spec_fingerprint(spec: ArenaSpec) -> str:
    """Identity of the task grid; guards resumed runs against config drift."""
    blob = json.dumps(
        {
            "deck_pairs": spec.deck_pairs(),
            "seeds": list(spec.seeds),
            "policy0": spec.policy0,
            "policy1": spec.policy1,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _stream_state(
    spec: ArenaSpec, raw_path: Path
) -> tuple[list[dict[str, Any]], Any, Any]:
    """Load completed matches and open the append-only result stream.

    Returns ``(done, on_result, close)``. ``on_result`` is called from rollout
    reader threads, so writes are serialised with a lock and flushed per match.
    """
    meta_path = raw_path.with_name(raw_path.name.removesuffix(".jsonl") + ".meta.json")
    fingerprint = _spec_fingerprint(spec)
    done: list[dict[str, Any]] = []
    if raw_path.exists():
        if not meta_path.exists():
            raise RuntimeError(
                f"{raw_path} exists but {meta_path} is missing; cannot resume "
                "safely (delete the raw file or use another --tag)"
            )
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("fingerprint") != fingerprint:
            raise RuntimeError(
                f"{raw_path} was produced by a different spec (fingerprint "
                "mismatch); use another --tag or delete the old raw/meta files"
            )
        with raw_path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    done.append(json.loads(line))
                except json.JSONDecodeError:
                    # A torn trailing line from a hard kill is expected on resume.
                    continue
    else:
        meta_path.write_text(
            json.dumps(
                {"fingerprint": fingerprint, "tag": spec.tag}, ensure_ascii=False
            ),
            encoding="utf-8",
        )

    ensure_trailing_newline(raw_path)
    handle = raw_path.open("a", encoding="utf-8")
    lock = threading.Lock()

    def _on_result(match: dict[str, Any]) -> None:
        with lock:
            handle.write(json.dumps(match, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()

    def _close() -> None:
        handle.close()

    return done, _on_result, _close


def run_arena(
    spec: ArenaSpec,
    *,
    progress: bool = False,
    raw_path: Path | None = None,
) -> ArenaResult:
    """Run every (pairing, seed) match and collect the records.

    When ``raw_path`` is given, results are streamed to that JSONL file as they
    complete and matches already present (by ``task_index``) are skipped, so an
    interrupted run resumes instead of restarting (PLAN.md D7). A ``.meta.json``
    sidecar pins the spec fingerprint; resuming against a different spec is
    refused rather than silently mixing results.
    """
    tasks = spec.tasks()
    done: list[dict[str, Any]] = []
    on_result = None
    close_stream = None
    if raw_path is not None:
        done, on_result, close_stream = _stream_state(spec, raw_path)
    done_indices = {match.get("task_index") for match in done}
    pending = [task for task in tasks if task.index not in done_indices]
    try:
        fresh = run_tasks(
            pending, workers=spec.workers, progress=progress, on_result=on_result
        )
    finally:
        if close_stream is not None:
            close_stream()
    matches = sorted(done + fresh, key=lambda item: item.get("task_index", -1))
    metadata = {
        "engine_lock": load_engine_lock(),
        "runtime": runtime_metadata(),
        "policies": {
            "contestant": spec.policy0 or "expert:<deck>",
            "pool": spec.policy1 or "expert:<deck>",
        },
        "resumed_matches": len(done),
    }
    return ArenaResult(spec=spec, matches=matches, metadata=metadata)


def _run_id(spec: ArenaSpec) -> str:
    stamp = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    return f"{stamp}_{spec.tag}"


def write_artifacts(
    result: ArenaResult, *, run_id: str | None = None, raw_path: Path | None = None
) -> dict[str, Path]:
    run_id = run_id or _run_id(result.spec)
    if raw_path is None:
        raw_path = ensure_dir(data_dir("arena")) / f"{run_id}.jsonl"
        with raw_path.open("w", encoding="utf-8") as handle:
            for match in result.matches:
                handle.write(json.dumps(match, ensure_ascii=False, sort_keys=True) + "\n")

    out_dir = ensure_dir(reports_dir("arena"))
    summary_path = out_dir / f"{run_id}.json"
    summary_path.write_text(
        json.dumps(result.summary(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    csv_path = out_dir / f"{run_id}.pairings.csv"
    rows = result.pairing_summary()
    if rows:
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    return {"raw": raw_path, "summary": summary_path, "csv": csv_path}


def _print_report(result: ArenaResult, *, top: int = 30) -> None:
    print(f"[arena] tag={result.spec.tag} matches={len(result.matches)} "
          f"seeds={len(result.spec.seeds)} workers={result.spec.workers}")
    errors = [m for m in result.matches if m["error"]]
    truncated = [m for m in result.matches if m.get("truncated")]
    print(f"[arena] errors={len(errors)} truncated={len(truncated)}")
    print("[arena] contestant win rates over pool (player 0):")
    for row in result.contestant_summary()[:top]:
        print(
            f"  {row['deck']:<34} {row['rate']:.3f} "
            f"[{row['ci_low']:.3f},{row['ci_high']:.3f}] "
            f"({row['wins']}-{row['losses']}-{row['draws']})"
        )
    elo = result.elo()
    if elo:
        print("[arena] Elo:")
        for name, rating in sorted(elo.items(), key=lambda kv: -kv[1])[:top]:
            print(f"  {name:<34} {rating:7.1f}")


def _seeds(count: int, start: int = 0) -> tuple[int, ...]:
    return tuple(range(start, start + count))


def _select_pool(names: Iterable[str] | None) -> tuple[Opponent, ...]:
    pool = scripted_opponents()
    if not names:
        return pool
    wanted = set(names)
    selected = tuple(opp for opp in pool if opp.name in wanted)
    missing = wanted - {opp.name for opp in selected}
    if missing:
        raise SystemExit(f"unknown opponent(s): {sorted(missing)}")
    return selected


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the scripted-expert arena.")
    parser.add_argument("--tag", default="arena")
    parser.add_argument("--seeds", type=int, default=50, help="number of paired seeds")
    parser.add_argument("--seed-start", type=int, default=0)
    parser.add_argument("--workers", type=int, default=None,
                        help="worker subprocesses (unset = all cores; 1 = single process)")
    parser.add_argument("--opponents", nargs="*", default=None, help="pool subset (opponent slugs)")
    parser.add_argument(
        "--contestants",
        nargs="*",
        default=None,
        help="contestant decks (default: all scripted); use with --policy0",
    )
    parser.add_argument(
        "--policy0",
        default=None,
        help="player-0 policy spec, e.g. expert:<deck> | neural:<ckpt> (default expert:<deck>)",
    )
    parser.add_argument(
        "--policy1",
        default=None,
        help="player-1 (pool) policy spec (default expert:<deck>)",
    )
    parser.add_argument("--swap", action="store_true", help="also play each pairing swapped")
    parser.add_argument("--smoke", action="store_true", help="tiny fast subset")
    parser.add_argument("--no-write", action="store_true", help="do not write artifacts")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.smoke:
        pool = _select_pool(
            args.opponents
            or ["superconduct_aggro", "natlan_battleship", "dual_mualani_stacks"]
        )
        seeds = _seeds(min(args.seeds, 3), args.seed_start)
        tag = f"{args.tag}_smoke"
    else:
        pool = _select_pool(args.opponents)
        seeds = _seeds(args.seeds, args.seed_start)
        tag = args.tag
    contestants = _select_pool(args.contestants) if args.contestants else scripted_opponents()
    if args.smoke and not args.contestants:
        contestants = tuple(c for c in contestants if c.deck in {o.deck for o in pool})
    try:
        workers = normalize_workers(args.workers)
    except ValueError as exc:
        raise SystemExit(str(exc))
    spec = ArenaSpec(
        contestants=contestants,
        pool=pool,
        seeds=seeds,
        workers=workers,
        swap=args.swap,
        policy0=args.policy0,
        policy1=args.policy1,
        tag=tag,
    )
    raw_path = ensure_dir(data_dir("arena")) / f"{tag}.jsonl"
    result = run_arena(spec, raw_path=None if args.no_write else raw_path)
    _print_report(result)
    if result.metadata.get("resumed_matches"):
        print(f"[arena] resumed {result.metadata['resumed_matches']} matches from {raw_path}")
    if not args.no_write:
        paths = write_artifacts(result, raw_path=raw_path)
        print(f"[arena] wrote {paths['summary']}")
        print(f"[arena] raw     {paths['raw']} (streamed; re-run the same command to resume)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
