#!/usr/bin/env python3
"""Runnable parked-game scheduler (same rows as ``skillloop.run``, higher concurrency).

Only ``action`` requests are parked; the expert answers mulligan / active-character
requests (see ``skillloop.parked``). Rows are written in the same shape as
``skillloop.run`` so ``scripts/compare_groups.py`` works unchanged.

Evaluated with the same protocol as ``skillloop.run``::

    python -m scripts.run_parked --group llm_empty --stages all --seeds 10 \\
        --stage-decks configs/stage_decks.json --card-version v3.3.0 --stage-rules \\
        --concurrency 96 --out data/runs/parked_llm_empty.jsonl

    python -m scripts.run_parked --group dynamic --stages all --seeds 10 \\
        --stage-decks configs/stage_decks.json --loadout reports/loadouts/v4 \\
        --card-version v3.3.0 --stage-rules \\
        --concurrency 96 --out data/runs/parked_dynamic.jsonl

``llm_empty`` is fixed to ``data/skills/rules_v1`` (LLM + game rules only) and does
not accept a loadout; ``static``/``dynamic`` default to ``static_v1``/``dyn_v4``
and may override with ``--library``. ``--loadout`` only applies to static/dynamic.
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
    ap.add_argument(
        "--library", default=None,
        help=(
            "skill library override for static/dynamic (defaults: "
            "data/skills/static_v1 / data/skills/dyn_v4); llm_empty is fixed to "
            "data/skills/rules_v1 and ignores this flag"
        ),
    )
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--max-calls", type=int, default=80)
    ap.add_argument("--model", default=None)
    ap.add_argument(
        "--stage-decks", default=None,
        help="per-stage fixed decks (evaluation: configs/stage_decks.json)",
    )
    ap.add_argument(
        "--loadout", default=None,
        help=(
            "frozen skill loadouts (evaluation: reports/loadouts/v1 for static, "
            "reports/loadouts/v4 for dynamic); static/dynamic only"
        ),
    )
    ap.add_argument("--card-version", default=None,
                    help="pin the engine card pool (e.g. v3.3.0); default: engine default pool")
    ap.add_argument(
        "--stage-rules", action="store_true",
        help="send each stage's special rules (configs/npc/special_rules.json)",
    )
    ap.add_argument("--scratchpad", action="store_true",
                    help="in-game notebook (局内记录板), default off; for A/B only")
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
        card_version=args.card_version, stage_rules=args.stage_rules,
        scratchpad=args.scratchpad,
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
