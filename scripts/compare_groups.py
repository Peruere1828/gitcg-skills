#!/usr/bin/env python3
"""Compare two group runs with the paired per-stage gate.

Usage::

    python scripts/compare_groups.py --a data/runs/smoke_scripted.jsonl \\
        --b data/runs/smoke_contestant.jsonl --label-a scripted --label-b contestant

Positive Δ means ``b`` is better (the gate's convention). The decision rule from
``docs/EVAL_PROTOCOL.md`` -- ``CI_low > 0`` -- is printed as ``accept``; it is only
meaningful when the two runs share ``(stage, seed)`` pairs.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from skillloop.gate import load_rows, paired_delta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--a", required=True)
    ap.add_argument("--b", required=True)
    ap.add_argument("--label-a", default="a")
    ap.add_argument("--label-b", default="b")
    ap.add_argument("--value", default="three_star")
    ap.add_argument("--out", default=None, help="write the gate result JSON here")
    args = ap.parse_args(argv)

    rows_a = load_rows(args.a)
    rows_b = load_rows(args.b)
    result = paired_delta(rows_a, rows_b, value=args.value)

    print(f"A = {args.label_a} ({len(rows_a)} rows)")
    print(f"B = {args.label_b} ({len(rows_b)} rows)")
    print(f"paired on (stage, seed); value = {args.value}; dropped = {result.dropped}\n")
    print(result.table())

    def _rate(rows):
        valid = [r for r in rows if not r.get("error") and not r.get("truncated")]
        return sum(float(r.get(args.value, 0.0)) for r in valid) / len(valid) if valid else float("nan")

    print(f"\npooled {args.label_a} = {_rate(rows_a):.4f}   pooled {args.label_b} = {_rate(rows_b):.4f}")

    payload = {
        "a": {"path": args.a, "label": args.label_a},
        "b": {"path": args.b, "label": args.label_b},
        "value": args.value,
        "gate": result.as_dict(),
    }
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[write] {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
