"""Statistics helpers for small-sample evaluation (PLAN.md §4.1 L1).

Small sample sizes are the norm locally, so every win rate is reported with a
Wilson score interval (better than normal approximation at the extremes).
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class RateEstimate:
    wins: float
    games: int
    rate: float
    low: float
    high: float

    def as_dict(self) -> dict[str, float | int]:
        return {
            "wins": self.wins,
            "games": self.games,
            "rate": self.rate,
            "ci_low": self.low,
            "ci_high": self.high,
        }


def wilson_interval(successes: float, n: int, *, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (draws count 0.5)."""
    if n <= 0:
        return (0.0, 1.0)
    phat = successes / n
    denom = 1.0 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    margin = (z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def estimate_rate(successes: float, n: int) -> RateEstimate:
    low, high = wilson_interval(successes, n)
    return RateEstimate(successes, n, (successes / n) if n else 0.0, low, high)


def outcome_score(winner: int | None, perspective: int) -> float:
    """Score a match result from ``perspective``'s point of view (draw = 0.5)."""
    if winner is None:
        return 0.5
    return 1.0 if int(winner) == int(perspective) else 0.0
