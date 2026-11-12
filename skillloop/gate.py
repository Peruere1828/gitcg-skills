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


@dataclass
class RoundsStageDelta:
    stage: str
    n: int
    rounds_a: float
    rounds_b: float
    delta: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "n": self.n,
            "rounds_a": round(self.rounds_a, 4),
            "rounds_b": round(self.rounds_b, 4),
            "delta": round(self.delta, 4),
        }


@dataclass
class RoundsGateResult:
    """Paired rounds-to-three-star comparison.

    Rounds are only comparable when both arms reached three stars: a game that
    failed an objective has no "rounds to 3★".  ``delta = rounds_b - rounds_a``,
    so a negative delta means arm ``b`` is **faster**.  The decision rule is
    ``ci_high < 0``.

    The unconditional means (``mean_rounds_a/b/delta``) are context only; they
    are computed over every shared ``(stage, seed)`` pair with a measurable
    round count, whether or not either arm was three-star.
    """

    mean_delta: float | None
    ci_low: float | None
    ci_high: float | None
    n_stages: int
    n_pairs: int
    n_dropped: int
    per_stage: list[RoundsStageDelta] = field(default_factory=list)
    mean_rounds_a: float | None = None
    mean_rounds_b: float | None = None
    mean_rounds_delta: float | None = None
    n_all_pairs: int = 0
    n_rounds_pairs: int = 0
    dropped_not_three_star: int = 0
    dropped_missing_rounds: int = 0
    insufficient: bool = False
    reason: str = ""

    @property
    def accept(self) -> bool:
        return not self.insufficient and self.ci_high is not None and self.ci_high < 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "mean_delta": _round_or_none(self.mean_delta),
            "ci_low": _round_or_none(self.ci_low),
            "ci_high": _round_or_none(self.ci_high),
            "n_stages": self.n_stages,
            "n_pairs": self.n_pairs,
            "n_dropped": self.n_dropped,
            "n_all_pairs": self.n_all_pairs,
            "n_rounds_pairs": self.n_rounds_pairs,
            "dropped_not_three_star": self.dropped_not_three_star,
            "dropped_missing_rounds": self.dropped_missing_rounds,
            "insufficient": self.insufficient,
            "reason": self.reason,
            "accept": self.accept,
            "mean_rounds_a": _round_or_none(self.mean_rounds_a),
            "mean_rounds_b": _round_or_none(self.mean_rounds_b),
            "mean_rounds_delta": _round_or_none(self.mean_rounds_delta),
            "per_stage": [item.as_dict() for item in self.per_stage],
        }

    def table(self) -> str:
        lines = [f"{'stage':16s} {'n':>4s} {'A':>7s} {'B':>7s} {'Δ':>7s}"]
        for item in sorted(self.per_stage, key=lambda d: d.delta):
            lines.append(
                f"{item.stage:16s} {item.n:4d} {item.rounds_a:7.2f} "
                f"{item.rounds_b:7.2f} {item.delta:+7.2f}"
            )
        if self.insufficient:
            lines.append(
                f"{'MEAN':16s} {'':>4s} {'':>7s} {'':>7s} "
                f"{'—' if self.mean_delta is None else f'{self.mean_delta:+.2f}':>7s}"
                f"   insufficient ({self.reason})  accept=False"
            )
        else:
            lines.append(
                f"{'MEAN':16s} {'':>4s} {'':>7s} {'':>7s} {self.mean_delta:+7.2f}"
                f"   95% CI [{self.ci_low:+.2f}, {self.ci_high:+.2f}]"
                f"  accept={self.accept}"
            )
        return "\n".join(lines)


def _round_or_none(value: float | None) -> float | None:
    return None if value is None else round(float(value), 4)


def _rounds_value(row: dict[str, Any]) -> int | None:
    """Measurable round count; ``None``/``0`` means no usable length."""
    raw = row.get("rounds")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _row_index(rows: Iterable[dict[str, Any]]) -> dict[tuple[str, int], dict[str, Any]]:
    out: dict[tuple[str, int], dict[str, Any]] = {}
    for row in rows:
        if row.get("error") or row.get("truncated"):
            continue
        stage = str(row.get("stage") or "")
        seed = row.get("seed")
        if not stage or seed is None:
            continue
        out[(stage, int(seed))] = row
    return out


def paired_rounds(
    rows_a: Sequence[dict[str, Any]],
    rows_b: Sequence[dict[str, Any]],
    *,
    value: str = "three_star",
) -> RoundsGateResult:
    """Paired ``(stage, seed)`` comparison of rounds, conditional on 3★.

    A pair enters the test only when **both** arms have ``value`` truthy
    (``three_star`` by default).  Rounds are then compared per stage; the mean
    per-stage delta is tested with the same t interval as :func:`paired_delta`.
    ``insufficient`` is set (and no CI is produced) when the subset has fewer
    than two stages or fewer than five pairs.
    """
    index_a = _row_index(rows_a)
    index_b = _row_index(rows_b)
    shared = sorted(set(index_a) & set(index_b))

    both_three: list[tuple[str, int]] = []
    dropped_not_three = 0
    for key in shared:
        if bool(index_a[key].get(value)) and bool(index_b[key].get(value)):
            both_three.append(key)
        else:
            dropped_not_three += 1

    usable: list[tuple[str, int]] = []
    dropped_missing_rounds = 0
    for key in both_three:
        if _rounds_value(index_a[key]) is None or _rounds_value(index_b[key]) is None:
            dropped_missing_rounds += 1
        else:
            usable.append(key)

    # Unconditional context: every shared pair with a measurable length on both
    # sides, regardless of the three-star outcome.
    all_rounds = [
        (key, _rounds_value(index_a[key]), _rounds_value(index_b[key]))
        for key in shared
        if _rounds_value(index_a[key]) is not None
        and _rounds_value(index_b[key]) is not None
    ]

    per_stage_values: dict[str, list[float]] = {}
    for key in usable:
        per_stage_values.setdefault(key[0], []).append(
            float(_rounds_value(index_b[key]) - _rounds_value(index_a[key]))
        )

    stage_deltas: list[RoundsStageDelta] = []
    for stage, deltas in sorted(per_stage_values.items()):
        keys = [key for key in usable if key[0] == stage]
        stage_deltas.append(
            RoundsStageDelta(
                stage=stage,
                n=len(deltas),
                rounds_a=statistics.fmean(_rounds_value(index_a[key]) for key in keys),
                rounds_b=statistics.fmean(_rounds_value(index_b[key]) for key in keys),
                delta=statistics.fmean(deltas),
            )
        )

    n_pairs = len(usable)
    if stage_deltas:
        mean_delta: float | None = statistics.fmean(item.delta for item in stage_deltas)
    else:
        mean_delta = None

    insufficient = len(stage_deltas) < 2 or n_pairs < 5
    ci_low: float | None = None
    ci_high: float | None = None
    if not insufficient and mean_delta is not None:
        values = [item.delta for item in stage_deltas]
        se = statistics.stdev(values) / math.sqrt(len(values))
        half = t95(len(values) - 1) * se
        ci_low = mean_delta - half
        ci_high = mean_delta + half

    if insufficient:
        reason = (
            "needs at least 2 stages and 5 paired three-star games; "
            f"got {len(stage_deltas)} stage(s) and {n_pairs} pair(s)"
        )
    else:
        reason = ""

    if all_rounds:
        mean_rounds_a: float | None = statistics.fmean(item[1] for item in all_rounds)
        mean_rounds_b: float | None = statistics.fmean(item[2] for item in all_rounds)
        mean_rounds_delta: float | None = statistics.fmean(
            item[2] - item[1] for item in all_rounds
        )
    else:
        mean_rounds_a = mean_rounds_b = mean_rounds_delta = None

    return RoundsGateResult(
        mean_delta=mean_delta,
        ci_low=ci_low,
        ci_high=ci_high,
        n_stages=len(stage_deltas),
        n_pairs=n_pairs,
        n_dropped=len(shared) - n_pairs,
        per_stage=stage_deltas,
        mean_rounds_a=mean_rounds_a,
        mean_rounds_b=mean_rounds_b,
        mean_rounds_delta=mean_rounds_delta,
        n_all_pairs=len(shared),
        n_rounds_pairs=len(all_rounds),
        dropped_not_three_star=dropped_not_three,
        dropped_missing_rounds=dropped_missing_rounds,
        insufficient=insufficient,
        reason=reason,
    )


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
