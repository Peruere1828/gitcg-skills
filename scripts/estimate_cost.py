#!/usr/bin/env python3
"""Project LLM experiment cost from a completed run's per-game JSONL.

The run rows already carry ``cost_usd``, ``llm_calls`` and ``completion_tokens``,
so the estimate needs no engine import and no network. Games without any LLM
calls (scripted/contestant groups) are ignored: they do not predict LLM spend.

Usage::

    python -m scripts.estimate_cost --rows data/runs/smoke2_llm_empty.jsonl --games 800
    python -m scripts.estimate_cost --rows data/runs/smoke2_llm_empty.jsonl \\
        --per-stage-seeds 10 --stages 16
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable


def load_rows(path: str | Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()]


def llm_games(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows that actually spent LLM calls (drop pure-policy groups)."""
    return [row for row in rows
            if int(row.get("llm_calls") or 0) > 0 or float(row.get("cost_usd") or 0.0) > 0.0]


def estimate_cost(rows: Iterable[dict[str, Any]], target_games: int) -> dict[str, Any]:
    """Measured per-game metrics plus the projection for ``target_games``."""
    if target_games < 0:
        raise ValueError("target_games must be >= 0")
    games = llm_games(rows)
    n = len(games)
    if n == 0:
        raise ValueError("no LLM games in rows (llm_calls/cost_usd are all zero)")

    total_cost = sum(float(row.get("cost_usd") or 0.0) for row in games)
    calls = sum(int(row.get("llm_calls") or 0) for row in games)
    completion = sum(int(row.get("completion_tokens") or 0) for row in games)
    return {
        "n_games": n,
        "total_cost_usd": round(total_cost, 6),
        "usd_per_game": round(total_cost / n, 6),
        "calls_per_game": round(calls / n, 2),
        "completion_tokens_per_call": round(completion / calls, 2) if calls else 0.0,
        "target_games": target_games,
        "projected_cost_usd": round(total_cost / n * target_games, 4),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rows", required=True, help="per-game JSONL from skillloop.run")
    target = ap.add_mutually_exclusive_group(required=True)
    target.add_argument("--games", type=int, help="target number of games")
    target.add_argument("--per-stage-seeds", type=int, help="target seeds per stage")
    ap.add_argument("--stages", type=int, default=16, help="stage count for --per-stage-seeds")
    args = ap.parse_args(argv)

    target_games = args.games if args.games is not None else args.per_stage_seeds * args.stages
    rows = load_rows(args.rows)
    try:
        est = estimate_cost(rows, target_games)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    print(f"rows      : {args.rows} ({len(rows)} lines, {est['n_games']} LLM games)")
    print(f"measured  : ${est['usd_per_game']:.6f}/game, {est['calls_per_game']:.2f} calls/game, "
          f"{est['completion_tokens_per_call']:.2f} completion tokens/call")
    source = (f"--games {args.games}" if args.games is not None
              else f"--per-stage-seeds {args.per_stage_seeds} x --stages {args.stages}")
    print(f"target    : {est['target_games']} games ({source})")
    print(f"projected : ${est['projected_cost_usd']:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
