"""Paired per-stage statistics for skill-library admission (INNOVATION: gate).

The design follows the project's evaluation protocol:

* **Pair at ``(stage, seed)``** — the same deal is played by both library versions, so
  the stage's difficulty cancels instead of dominating the variance (the 16 stages
  range from ~0.2 to ~1.0 three-star rate).
* **Aggregate per stage, then a paired t across stages** — 16 stages give 15 degrees of
  freedom; that is the honest unit because a stage is a cluster of seeds.
* **Decision rule** — a new library version is admitted only when the 95% CI lower
  bound of the mean per-stage delta is above zero (pre-registered in
  ``docs/EVAL_PROTOCOL.md``).

Nothing here talks to the engine: input is rows of per-game results, so the gate can
be unit-tested and re-run on archived logs.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

# two-sided 95% t quantiles (df = 1..30); 1.96 beyond.
_T95 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
    8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145,
    15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080,
    22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048,
    29: 2.045, 30: 2.042,
}


def t95(df: int) -> float:
    return _T95.get(int(df), 1.96)


@dataclass
class StageDelta:
    stage: str
    n: int
    rate_a: float
    rate_b: float
    delta: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "n": self.n,
            "rate_a": round(self.rate_a, 4),
            "rate_b": round(self.rate_b, 4),
            "delta": round(self.delta, 4),
        }


@dataclass
class GateResult:
    mean_delta: float
    ci_low: float
    ci_high: float
    n_stages: int
    n_pairs: int
    per_stage: list[StageDelta] = field(default_factory=list)
    dropped: int = 0

    @property
    def accept(self) -> bool:
        return self.ci_low > 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "mean_delta": round(self.mean_delta, 4),
            "ci_low": round(self.ci_low, 4),
            "ci_high": round(self.ci_high, 4),
            "n_stages": self.n_stages,
            "n_pairs": self.n_pairs,
            "dropped_pairs": self.dropped,
            "accept": self.accept,
            "per_stage": [item.as_dict() for item in self.per_stage],
        }

    def table(self) -> str:
        lines = [f"{'stage':16s} {'n':>4s} {'A':>6s} {'B':>6s} {'Δ':>7s}"]
        for item in sorted(self.per_stage, key=lambda d: d.delta):
            lines.append(
                f"{item.stage:16s} {item.n:4d} {item.rate_a:6.3f} {item.rate_b:6.3f} {item.delta:+7.3f}"
            )
        lines.append(
            f"{'MEAN':16s} {'':>4s} {'':>6s} {'':>6s} {self.mean_delta:+7.3f}"
            f"   95% CI [{self.ci_low:+.3f}, {self.ci_high:+.3f}]  accept={self.accept}"
        )
        return "\n".join(lines)


def load_rows(path: str | Path) -> list[dict[str, Any]]:
    """Read a per-game JSONL log (one row per played game)."""
    rows = []
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _index(rows: Iterable[dict[str, Any]], value: str) -> dict[tuple[str, int], float]:
    out: dict[tuple[str, int], float] = {}
    for row in rows:
        if row.get("error") or row.get("truncated"):
            continue
        stage = str(row.get("stage") or "")
        seed = row.get("seed")
        if not stage or seed is None:
            continue
        out[(stage, int(seed))] = float(row.get(value, 0.0))
    return out


def paired_delta(
    rows_a: Sequence[dict[str, Any]],
    rows_b: Sequence[dict[str, Any]],
    *,
    value: str = "three_star",
) -> GateResult:
    """Paired ``(stage, seed)`` comparison of two libraries / groups.

    ``rows_b − rows_a`` is reported: positive means the candidate (b) is better.
    """
    index_a = _index(rows_a, value)
    index_b = _index(rows_b, value)
    shared = sorted(set(index_a) & set(index_b))
    if not shared:
        raise ValueError("no shared (stage, seed) pairs; the two runs are not comparable")

    per_stage: dict[str, list[float]] = {}
    for key in shared:
        per_stage.setdefault(key[0], []).append(index_b[key] - index_a[key])

    stage_deltas: list[StageDelta] = []
    for stage, deltas in sorted(per_stage.items()):
        n = len(deltas)
        rate_a = statistics.fmean(index_a[(stage, seed)] for (st, seed) in shared if st == stage)
        rate_b = statistics.fmean(index_b[(stage, seed)] for (st, seed) in shared if st == stage)
        stage_deltas.append(
            StageDelta(stage=stage, n=n, rate_a=rate_a, rate_b=rate_b, delta=statistics.fmean(deltas))
        )

    values = [item.delta for item in stage_deltas]
    mean_delta = statistics.fmean(values)
    if len(values) > 1:
        se = statistics.stdev(values) / math.sqrt(len(values))
        half = t95(len(values) - 1) * se
    else:
        half = float("inf")
    return GateResult(
        mean_delta=mean_delta,
        ci_low=mean_delta - half,
        ci_high=mean_delta + half,
        n_stages=len(stage_deltas),
        n_pairs=len(shared),
        per_stage=stage_deltas,
        dropped=len(index_a) + len(index_b) - 2 * len(shared),
    )
