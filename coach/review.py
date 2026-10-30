"""CLI: review arena extraction logs with the replay coach.

Usage::

    python -m coach.review --input data/arena/<run>.jsonl --limit 2
    python -m coach.review --input ... --no-llm        # deterministic only
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from common.llm import build_llm_client, load_llm_config
from common.paths import ensure_dir, reports_dir
from coach.replay import ReplayCoach, load_matches, rule_based_flags
from envs.policy import _asset_catalog


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review arena games with the coach agent.")
    parser.add_argument("--input", required=True, type=Path, help="arena raw .jsonl")
    parser.add_argument("--limit", type=int, default=3, help="number of games to review")
    parser.add_argument("--no-llm", action="store_true", help="rule-based review only")
    parser.add_argument("--model", default="deepseek-flash", help="offline review model")
    parser.add_argument("--out", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    matches = [m for m in load_matches(str(args.input)) if m.get("decisions")]
    if not matches:
        raise SystemExit(
            f"no games with decision traces in {args.input}; "
            "run the arena with record_decisions=True"
        )
    selected = matches[: args.limit]
    client = None if args.no_llm else build_llm_client()
    if not args.no_llm and client is None:
        print("[coach] no LLM config found; falling back to rule-based review")
    coach = ReplayCoach(assets=_asset_catalog(), client=client, model=args.model)
    reviews = [coach.review(match) for match in selected]
    for review in reviews:
        print(json.dumps(review, ensure_ascii=False, indent=2)[:2000])
    out = args.out or ensure_dir(reports_dir("coach")) / f"{args.input.stem}.coach.json"
    out.write_text(json.dumps(reviews, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[coach] wrote {out}")
    if client is not None:
        print(f"[coach] llm usage: {client.usage.as_dict()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
