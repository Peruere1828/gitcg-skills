"""Paired comparison between two ``eval_protocol`` raw runs.

Both runs must cover the same ``(opponent, protocol_seed, side)`` keys (e.g. the
same deck / seed range, different checkpoints).  Prints the paired delta with its
95% CI plus the per-opponent breakdown, reusing the exact estimator the protocol
uses for its non-inferiority gate.

Usage::

    python -m scripts.compare_eval_runs a0_double_geo_navia_s900 \\
        a2ft_double_geo_navia_s900
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from common.jsonl import read_jsonl
from common.paths import data_dir
from eval.scoreboard import paired_difference
from scripts.eval_protocol import outcomes_from_rows


def _rows(tag: str) -> list[dict]:
    path = data_dir() / "eval" / f"{tag}.jsonl"
    if not path.exists():
        raise SystemExit(f"missing raw run: {path}")
    return read_jsonl(path, tolerate_torn=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("a", help="tag of run A (e.g. the old checkpoint)")
    parser.add_argument("b", help="tag of run B (e.g. the new checkpoint)")
    parser.add_argument("--json-out", default=None, help="optional report path")
    args = parser.parse_args(argv)

    a = outcomes_from_rows(_rows(args.a))
    b = outcomes_from_rows(_rows(args.b))
    diff = paired_difference(a, b)
    if diff is None:
        raise SystemExit("runs share no comparable (opponent, seed, side) keys")
    payload = {
        "a": args.a,
        "b": args.b,
        "n_a": len(a),
        "n_b": len(b),
        "paired": diff,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    print(text)
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
