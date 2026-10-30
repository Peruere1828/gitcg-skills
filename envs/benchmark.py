"""Throughput benchmark (PLAN.md §4.1 L0 acceptance).

Measures games/hour and decisions/hour on this machine for a fixed scripted
matchup, then translates that into core-hours for the M0 (10k games/h @128 cores)
and M4 (large self-play) targets. Writes a small report under ``reports/``.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import resource
import time
from dataclasses import dataclass
from typing import Any

from common.lock import load_engine_lock, runtime_metadata
from common.paths import ensure_dir, reports_dir
from envs.rollout import MatchTask, run_tasks


@dataclass
class BenchmarkResult:
    games: int
    workers: int
    wall_seconds: float
    cpu_seconds: float
    games_per_hour_wall: float
    games_per_hour_per_core: float
    decisions: int
    decisions_per_second_cpu: float
    errors: int
    metadata: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "games": self.games,
            "workers": self.workers,
            "wall_seconds": round(self.wall_seconds, 3),
            "cpu_seconds": round(self.cpu_seconds, 3),
            "games_per_hour_wall": round(self.games_per_hour_wall, 1),
            "games_per_hour_per_core": round(self.games_per_hour_per_core, 1),
            "decisions": self.decisions,
            "decisions_per_second_cpu": round(self.decisions_per_second_cpu, 1),
            "errors": self.errors,
            "metadata": self.metadata,
        }


def run_benchmark(
    *,
    games: int,
    workers: int,
    deck0: str = "superconduct_aggro",
    deck1: str = "natlan_battleship",
    seed_start: int = 0,
) -> BenchmarkResult:
    tasks = [
        MatchTask(index=i, deck0=deck0, deck1=deck1, seed=seed_start + i)
        for i in range(games)
    ]
    # Warm up in-process caches (imports, asset catalog) for the single-core path
    # so the reported rate reflects steady-state, not first-game startup.
    if workers <= 1:
        run_tasks(
            [MatchTask(index=-1, deck0=deck0, deck1=deck1, seed=seed_start - 1)],
            workers=1,
        )
    usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    start = time.perf_counter()
    results = run_tasks(tasks, workers=workers)
    wall = time.perf_counter() - start
    usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu_seconds = (usage_after.ru_utime + usage_after.ru_stime) - (
        usage_before.ru_utime + usage_before.ru_stime
    )
    if workers <= 1:
        # Children usage is zero for sequential runs; use the parent's own usage.
        parent = resource.getrusage(resource.RUSAGE_SELF)
        cpu_seconds = parent.ru_utime + parent.ru_stime
    decisions = sum(int(m["decisions0"]) + int(m["decisions1"]) for m in results)
    errors = sum(1 for m in results if m["error"])
    games_per_hour_wall = (games / wall) * 3600.0 if wall else 0.0
    return BenchmarkResult(
        games=games,
        workers=workers,
        wall_seconds=wall,
        cpu_seconds=cpu_seconds,
        games_per_hour_wall=games_per_hour_wall,
        games_per_hour_per_core=(games_per_hour_wall / max(1, workers)),
        decisions=decisions,
        decisions_per_second_cpu=(decisions / cpu_seconds) if cpu_seconds else 0.0,
        errors=errors,
        metadata={
            "deck0": deck0,
            "deck1": deck1,
            "engine_lock": load_engine_lock(),
            "runtime": runtime_metadata(),
        },
    )


def core_hours_for(games: int, games_per_hour_per_core: float) -> float | None:
    if games_per_hour_per_core <= 0:
        return None
    return games / games_per_hour_per_core


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="gitcg throughput benchmark.")
    parser.add_argument("--games", type=int, default=24)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--deck0", default="superconduct_aggro")
    parser.add_argument("--deck1", default="natlan_battleship")
    parser.add_argument("--seed-start", type=int, default=0)
    parser.add_argument("--no-write", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_benchmark(
        games=args.games,
        workers=args.workers,
        deck0=args.deck0,
        deck1=args.deck1,
        seed_start=args.seed_start,
    )
    payload = result.as_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    per_core = result.games_per_hour_per_core
    print(
        f"[bench] {per_core:.0f} games/h/core; "
        f"{result.games_per_hour_wall:.0f} games/h on {result.workers} worker(s); "
        f"{result.decisions_per_second_cpu:.0f} decisions/s/core"
    )
    for target in (100_000, 1_000_000):
        ch = core_hours_for(target, per_core)
        if ch is not None:
            print(f"[bench] {target:,} games ~= {ch:,.1f} core-hours")
    if not args.no_write:
        stamp = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
        path = ensure_dir(reports_dir("benchmarks")) / f"bench_{stamp}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[bench] wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
