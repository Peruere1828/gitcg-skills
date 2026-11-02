#!/usr/bin/env python3
"""Runnable parked-game scheduler (same rows as ``skillloop.run``, higher concurrency).

Only ``action`` requests are parked; the expert answers mulligan / active-character
requests (see ``skillloop.parked``). Rows are written in the same shape as
``skillloop.run`` so ``scripts/compare_groups.py`` works unchanged.

Usage::

    python -m scripts.run_parked --group llm_empty --stages all --seeds 10 \\
        --concurrency 96 --out data/runs/parked_llm_empty.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from npc.spec import get_stage, stage_names
from skillloop.parked import ParkedGame, ParkedRunner
from skillloop.run import RunSpec, _seed_list


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--group", required=True,
                    choices=("llm_empty", "static", "dynamic"))
    ap.add_argument("--stages", default="all")
    ap.add_argument("--seeds", default="10")
    ap.add_argument("--deck", default="superconduct_aggro")
    ap.add_argument("--library", default=None)
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--max-calls", type=int, default=80)
    ap.add_argument("--model", default=None)
    ap.add_argument("--stage-decks", default=None)
    ap.add_argument("--loadout", default=None)
    ap.add_argument("--card-version", default=None,
                    help="pin the engine card pool (e.g. v3.3.0); default: engine default pool")
    ap.add_argument("--concurrency", type=int, default=96)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    stages = tuple(stage_names()) if args.stages == "all" else tuple(
        s.strip() for s in args.stages.split(",") if s.strip())
    spec = RunSpec(
        group=args.group, stages=stages, seeds=_seed_list(args.seeds), deck=args.deck,
        workers=args.concurrency, library_root=args.library, top_k=args.top_k,
        model=args.model, max_calls=args.max_calls, out=args.out,
        stage_decks_root=args.stage_decks, loadout_root=args.loadout,
        card_version=args.card_version,
    )
    tasks = [
        ParkedGame(stage=get_stage(stage), seed=seed, spec=spec, task_index=index)
        for index, (stage, seed) in enumerate((s, sd) for s in spec.stages for sd in spec.seeds)
    ]
    runner = ParkedRunner(spec, concurrency=args.concurrency)
    rows, summary = runner.run_with_summary(tasks)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary = dict(summary)
    summary["out"] = str(out)
    summary["concurrency"] = args.concurrency
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get("valid") else 1


if __name__ == "__main__":
    sys.exit(main())
