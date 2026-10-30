#!/usr/bin/env python3
"""Re-derive the ``win_within_rounds`` star threshold from measured games.

The difficulty patch (PLAN.md D4) raised every enemy's HP, so the old round limits
(mostly "win within 5 rounds") were calibrated for a much easier task. Rather than
guessing, we measure: play the reference policy (``npc/contestant.py``, the
hand-written objective-aware one) on N seeds per stage and set the limit at the
**P70 of winning games' round count**. That keeps the objective achievable for a
competent policy (~70% of wins clear it) while still discriminating.

The script is intentionally *proposal-first*: it prints a table, writes
``reports/calibration/thresholds.json``, and only edits configs with ``--apply``.

Usage::

    python -m skillloop.run --group contestant --stages all --seeds 24 \\
        --out data/runs/calib_contestant.jsonl
    python scripts/calibrate_thresholds.py --rows data/runs/calib_contestant.jsonl
    python scripts/calibrate_thresholds.py --rows ... --apply
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STAGES_JSON = REPO / "configs/npc/npc_stages_mond_liyue.json"
OUT_JSON = REPO / "reports/calibration/thresholds.json"


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    low = math.floor(pos)
    high = math.ceil(pos)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def load_rows(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if not row.get("error") and not row.get("truncated"):
                rows.append(row)
    return rows


def proposals(rows: list[dict], *, quantile: float = 0.70, stages_path: Path = STAGES_JSON) -> dict[str, dict]:
    by_stage: dict[str, list[dict]] = {}
    for row in rows:
        by_stage.setdefault(str(row["stage"]), []).append(row)

    stages_doc = json.loads(stages_path.read_text(encoding="utf-8"))
    stages = stages_doc if isinstance(stages_doc, list) else stages_doc.get("stages", [])
    current = {}
    for stage in stages:
        limit = next((o.get("n") for o in stage.get("objectives") or []
                      if o.get("type") == "win_within_rounds"), None)
        current[stage["name"]] = limit

    out: dict[str, dict] = {}
    for name, items in sorted(by_stage.items()):
        wins = [float(r["rounds"]) for r in items if r.get("winner") == 0]
        all_rounds = [float(r["rounds"]) for r in items]
        three_star = sum(1 for r in items if r.get("three_star")) / len(items)
        proposed = None
        if wins:
            proposed = int(math.ceil(_percentile(wins, quantile)))
        out[name] = {
            "n": len(items),
            "wins": len(wins),
            "three_star_rate": round(three_star, 3),
            "mean_rounds": round(statistics.fmean(all_rounds), 2) if all_rounds else None,
            "win_round_p": {f"p{int(q * 100)}": round(_percentile(wins, q), 2)
                            for q in (0.5, 0.7, 0.8)} if wins else {},
            "current_limit": current.get(name),
            "proposed_limit": proposed,
        }
    return out


def apply(proposed: dict[str, dict], *, only: str | None = None, path: Path = STAGES_JSON) -> list[str]:
    stages_doc = json.loads(path.read_text(encoding="utf-8"))
    stages = stages_doc if isinstance(stages_doc, list) else stages_doc.get("stages", [])
    changed: list[str] = []
    for stage in stages:
        name = stage["name"]
        if only and name != only:
            continue
        entry = proposed.get(name)
        if not entry or entry.get("proposed_limit") is None:
            continue
        limit = int(entry["proposed_limit"])
        objectives = stage.setdefault("objectives", [])
        target = next((o for o in objectives if o.get("type") == "win_within_rounds"), None)
        if target is None:
            objectives.append({
                "type": "win_within_rounds",
                "n": limit,
                "text": f"{limit}回合内胜利",
                "verified": True,
            })
            changed.append(f"{name}: +win_within_rounds({limit})")
        elif int(target.get("n") or 0) != limit:
            changed.append(f"{name}: win_within_rounds {target.get('n')} -> {limit}")
            target["n"] = limit
            target["text"] = f"{limit}回合内胜利"
    path.write_text(json.dumps(stages_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", required=True, help="per-game JSONL from skillloop.run")
    ap.add_argument("--quantile", type=float, default=0.70)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only", default=None, help="apply a single stage (debugging)")
    args = ap.parse_args(argv)

    rows = load_rows(Path(args.rows))
    if not rows:
        raise SystemExit(f"no valid rows in {args.rows}")
    proposed = proposals(rows, quantile=args.quantile)

    print(f"{'stage':16s} {'n':>3s} {'3★':>6s} {'mean':>5s} {'p70':>5s} {'current':>7s} {'proposed':>8s}")
    for name, entry in proposed.items():
        p70 = entry["win_round_p"].get("p70")
        print(f"{name:16s} {entry['n']:3d} {entry['three_star_rate']:6.3f} "
              f"{entry['mean_rounds']:5.1f} {str(p70):>5s} {str(entry['current_limit']):>7s} "
              f"{str(entry['proposed_limit']):>8s}")

    overall = sum(e["three_star_rate"] * e["n"] for e in proposed.values()) / sum(e["n"] for e in proposed.values())
    print(f"\npooled three-star rate = {overall:.3f} over {sum(e['n'] for e in proposed.values())} games")

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(
        json.dumps({"rows": args.rows, "quantile": args.quantile, "stages": proposed}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[write] {OUT_JSON}")

    if args.apply:
        changed = apply(proposed, only=args.only)
        print("[apply] " + ("; ".join(changed) if changed else "no changes"))
    else:
        print("[dry-run] pass --apply to edit configs/npc/npc_stages_mond_liyue.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
