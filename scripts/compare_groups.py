#!/usr/bin/env python3
"""Compare two group runs with the paired per-stage gates.

Usage::

    python scripts/compare_groups.py --a data/runs/smoke_scripted.jsonl \\
        --b data/runs/smoke_contestant.jsonl --label-a scripted --label-b contestant

Two paired analyses are emitted:

* ``gate`` -- the original three-star gate.  Positive Δ means ``b`` is better
  (``CI_low > 0`` is the admission rule).
* ``rounds_gate`` -- rounds-to-three-star, restricted to ``(stage, seed)`` pairs
  where **both** arms are three-star (otherwise there is no "rounds to 3★").
  ``delta = rounds_b - rounds_a``, so negative means ``b`` is faster; the rule is
  ``CI_high < 0``.  Unconditional mean rounds over all shared pairs are included
  as context only.

Both gates need shared ``(stage, seed)`` pairs to be meaningful.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from skillloop.gate import load_rows, paired_delta, paired_rounds


def _rate(rows, value="three_star"):
    valid = [r for r in rows if not r.get("error") and not r.get("truncated")]
    return (
        sum(float(r.get(value, 0.0)) for r in valid) / len(valid)
        if valid
        else float("nan")
    )


def _fmt(value):
    return "n/a" if value is None else f"{value:.2f}"


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
    rounds = paired_rounds(rows_a, rows_b)

    print(f"A = {args.label_a} ({len(rows_a)} rows)")
    print(f"B = {args.label_b} ({len(rows_b)} rows)")
    print(f"paired on (stage, seed); value = {args.value}; dropped = {result.dropped}\n")
    print(result.table())

    print(
        "\nrounds gate (both arms three-star; Δ = rounds_b - rounds_a, "
        "negative = B faster)"
    )
    print(
        f"pairs: {rounds.n_pairs}/{rounds.n_all_pairs} usable "
        f"(dropped {rounds.n_dropped}: {rounds.dropped_not_three_star} not both 3★, "
        f"{rounds.dropped_missing_rounds} missing rounds)"
    )
    print(rounds.table())
    print(
        f"\nunconditional rounds over {rounds.n_rounds_pairs} shared pair(s): "
        f"A={_fmt(rounds.mean_rounds_a)} B={_fmt(rounds.mean_rounds_b)} "
        f"Δ={_fmt(rounds.mean_rounds_delta)}"
    )

    print(
        f"\npooled {args.label_a} = {_rate(rows_a, args.value):.4f}   "
        f"pooled {args.label_b} = {_rate(rows_b, args.value):.4f}"
    )

    payload = {
        "a": {"path": args.a, "label": args.label_a},
        "b": {"path": args.b, "label": args.label_b},
        "value": args.value,
        "gate": result.as_dict(),
        "rounds_gate": rounds.as_dict(),
    }
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"[write] {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
