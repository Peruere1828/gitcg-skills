#!/usr/bin/env python3
"""Where the three stars are lost: objective-level accounting over the six 160-game runs.

Read-only over existing JSONL (no engine, no LLM). Prints the tables used in
``reports/star_gap_analysis.md``.

Usage::

    python scripts/star_gap_analysis.py            # markdown tables to stdout
    python scripts/star_gap_analysis.py --json     # same, machine-readable

Groups (16 stages x 10 seeds = 160 rows each):
  t5b_static         static_v1, seeds 0-9
  t5b_dynamic        dyn_v1,    seeds 0-9
  evo_r3_dynamic     dyn_v3,    seeds 0-9
  evo_hold_static    static_v1, seeds 10-19
  evo_hold_dynamic   dyn_v3,    seeds 10-19
  evo_hold_llm_empty empty lib, seeds 10-19
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

GROUPS = {
    "t5b_static": "static_v1 · s0-9",
    "t5b_dynamic": "dyn_v1 · s0-9",
    "evo_r3_dynamic": "dyn_v3 · s0-9",
    "evo_hold_static": "static_v1 · s10-19",
    "evo_hold_dynamic": "dyn_v3 · s10-19",
    "evo_hold_llm_empty": "empty · s10-19",
}
OBJECTIVES = [
    "win",
    "win_within_rounds",
    "defeated_less_than",
    "use_elemental_burst",
    "trigger_elemental_reactions",
    "play_equipment_card",
    "play_event_cards",
]
SHORT = {
    "win": "win",
    "win_within_rounds": "wwr(≤5)",
    "defeated_less_than": "deaths<2",
    "use_elemental_burst": "burst",
    "trigger_elemental_reactions": "reactions",
    "play_equipment_card": "equipment",
    "play_event_cards": "events",
}
RUNS = Path("data/runs")


def load(name: str) -> list[dict]:
    return [json.loads(line) for line in (RUNS / f"{name}.jsonl").read_text().splitlines() if line.strip()]


def table_failures(rows_by_group: dict[str, list[dict]]) -> list[str]:
    out = ["| objective | " + " | ".join(GROUPS[g] for g in GROUPS) + " |",
           "|---|" + "---:|" * len(GROUPS)]
    for objective in OBJECTIVES:
        cells = []
        for group in GROUPS:
            rows = rows_by_group[group]
            fails = [r for r in rows if r["objectives"].get(objective) is False]
            only = [r for r in fails if sum(1 for v in r["objectives"].values() if not v) == 1]
            cells.append(f"{len(fails)} ({len(only)} alone)")
        out.append(f"| {SHORT[objective]} | " + " | ".join(cells) + " |")
    return out


def table_stars(rows_by_group: dict[str, list[dict]]) -> list[str]:
    out = ["| group | 3-star | objective stars | wins |", "|---|---:|---:|---:|"]
    for group in GROUPS:
        rows = rows_by_group[group]
        stars = sum(r["three_star"] for r in rows) / len(rows)
        total = sum(r["stars"] for r in rows)
        possible = sum(len(r["objectives"]) for r in rows)
        wins = sum(1 for r in rows if r["objectives"].get("win")) / len(rows)
        out.append(f"| {GROUPS[group]} | {stars:.4f} | {total}/{possible} | {wins:.4f} |")
    return out


def table_counterfactual(rows_by_group: dict[str, list[dict]]) -> list[str]:
    """3-star rate if one objective were granted for free (upper bound on its cost)."""
    out = ["| group | actual | " + " | ".join(SHORT[o] for o in OBJECTIVES) + " |",
           "|---|---:|" + "---:|" * len(OBJECTIVES)]
    for group in GROUPS:
        rows = rows_by_group[group]
        base = sum(r["three_star"] for r in rows) / len(rows)
        cells = []
        for objective in OBJECTIVES:
            granted = sum(
                1 for r in rows
                if all(v for k, v in r["objectives"].items() if k != objective)
            ) / len(rows)
            cells.append(f"{granted - base:+.3f}")
        out.append(f"| {GROUPS[group]} | {base:.4f} | " + " | ".join(cells) + " |")
    return out


def table_wwr(rows_by_group: dict[str, list[dict]]) -> list[str]:
    out = ["| group | wwr fails | rounds=6 | 7 | 8 | 9 | 10+ | ≤6 cumulative | won-but-slow |",
           "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for group in GROUPS:
        rows = rows_by_group[group]
        fails = [r for r in rows if r["objectives"].get("win_within_rounds") is False]
        rounds = Counter(int(r["rounds"]) for r in fails)
        n = len(fails)
        won = sum(1 for r in fails if r["objectives"].get("win"))
        out.append(
            f"| {GROUPS[group]} | {n} | {rounds[6]} | {rounds[7]} | {rounds[8]} | "
            f"{rounds[9]} | {sum(v for k, v in rounds.items() if k >= 10)} | "
            f"{(rounds[6]) / n:.0%} | {won} |"
        )
    return out


def table_stage_gap(rows_by_group: dict[str, list[dict]]) -> list[str]:
    """Per stage: static -> dynamic 3-star on the held-out seeds, with failure deltas."""
    static = {(r["stage"], r["seed"]): r for r in rows_by_group["evo_hold_static"]}
    dynamic = {(r["stage"], r["seed"]): r for r in rows_by_group["evo_hold_dynamic"]}
    out = [
        "| stage | static 3★ | dyn 3★ | Δ | failure deltas (static→dyn) | flips +/− |",
        "|---|---:|---:|---:|---|---|",
    ]
    for stage in sorted({k[0] for k in static}):
        pairs = [(static[k], dynamic[k]) for k in sorted(static) if k[0] == stage]
        s = sum(a["three_star"] for a, _ in pairs)
        d = sum(b["three_star"] for _, b in pairs)
        deltas = []
        for objective in OBJECTIVES:
            fs = sum(1 for a, _ in pairs if a["objectives"].get(objective) is False)
            fd = sum(1 for _, b in pairs if b["objectives"].get(objective) is False)
            if fs != fd:
                deltas.append(f"{SHORT[objective]} {fs}→{fd}")
        gain = sum(1 for a, b in pairs if not a["three_star"] and b["three_star"])
        loss = sum(1 for a, b in pairs if a["three_star"] and not b["three_star"])
        out.append(
            f"| {stage} | {s}/10 | {d}/10 | {d - s:+d} | "
            f"{', '.join(deltas) or '—'} | +{gain}/−{loss} |"
        )
    return out


def table_paired_flips(rows_by_group: dict[str, list[dict]]) -> list[str]:
    """Train vs holdout: how many games flip each way on 3-star, and at what round."""
    out = []
    for static_name, dynamic_name, label in (
        ("t5b_static", "t5b_dynamic", "train s0-9 (static_v1 → dyn_v1)"),
        ("evo_hold_static", "evo_hold_dynamic", "holdout s10-19 (static_v1 → dyn_v3)"),
    ):
        static = {(r["stage"], r["seed"]): r for r in rows_by_group[static_name]}
        dynamic = {(r["stage"], r["seed"]): r for r in rows_by_group[dynamic_name]}
        gains = [k for k in static if not static[k]["three_star"] and dynamic[k]["three_star"]]
        losses = [k for k in static if static[k]["three_star"] and not dynamic[k]["three_star"]]
        gain_rounds = Counter(int(static[k]["rounds"]) for k in gains)
        loss_rounds = Counter(int(dynamic[k]["rounds"]) for k in losses)
        out.append(
            f"- **{label}**: +{len(gains)} / −{len(losses)} (net {len(gains) - len(losses)}). "
            f"Won games lost at rounds {dict(sorted(gain_rounds.items()))}; "
            f"lost games now at rounds {dict(sorted(loss_rounds.items()))}."
        )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    rows_by_group = {name: load(name) for name in GROUPS}

    if args.json:
        print(json.dumps({name: {"n": len(rows), "three_star": sum(r["three_star"] for r in rows)}
                          for name, rows in rows_by_group.items()}, indent=2))
        return 0

    for title, table in (
        ("Three-star and wins", table_stars(rows_by_group)),
        ("Objective failures — total (sole failure)", table_failures(rows_by_group)),
        ("Counterfactual: 3-star if this objective were free", table_counterfactual(rows_by_group)),
        ("win_within_rounds failures by total rounds", table_wwr(rows_by_group)),
        ("Per-stage held-out gap", table_stage_gap(rows_by_group)),
    ):
        print(f"\n### {title}\n")
        print("\n".join(table))
    print("\n### Paired flips\n")
    print("\n".join(table_paired_flips(rows_by_group)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
