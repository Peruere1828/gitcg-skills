"""Engine-free strength scoreboard for the evaluation protocol (PLAN.md WS2).

This module holds the *statistics and aggregation* half of the strength-
evaluation protocol described in ``reports/explore/eval_protocol.md``:

- per-opponent + pooled win rates with Wilson 95% CIs and an honest
  **opponent-cluster** interval (opponents are clusters, games are not i.i.d.);
- paired-seed A/B comparison (``paired_difference``) so "beating the old self"
  is distinguishable from seed noise;
- a fixed-anchor **Bradley-Terry / Elo** rating with a bootstrap CI
  (``anchored_bt_rating``) + the online ``eval.ladder`` Elo;
- decision-quality proxies (fallback/illegal rate, round/decision efficiency);
- a **best-response probe** lower bound (worst matchup) as a cheap
  exploitability proxy;
- sample-size tables (``sample_size_table`` / ``floor_sample_size``).

Everything here is pure Python (stdlib only) and independent of the engine, so
it can be unit-tested on synthetic match outcomes (``tests/test_eval_protocol.py``).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from eval.stats import wilson_interval

Z95 = 1.96
# Elo logistic scale: eval.ladder uses 10**((rb-ra)/400) == sigmoid((ra-rb)/S).
ELO_SCALE = 400.0 / math.log(10.0)  # ~= 173.72

# Two-sided 95% Student-t critical values for df 1..30 (z afterwards). Used for
# cluster intervals over a small number of opponents.
_T95: dict[int, float] = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
    8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145,
    15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080,
    22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048,
    29: 2.045, 30: 2.042,
}


@dataclass(frozen=True)
class MatchOutcome:
    """One match from the **contestant's** perspective.

    ``score`` is 1.0 win / 0.5 draw / 0.0 loss. ``side`` is which player the
    contestant controlled (0 or 1), so that first-player bias can be cancelled by
    running both sides on the same seed. ``key`` identifies a match uniquely so
    two runs can be paired seed-for-seed.
    """

    opponent: str
    seed: int
    side: int
    score: float
    rounds: int = 0
    decisions: int = 0
    fallbacks: int = 0
    error: str | None = None
    truncated: bool = False

    @property
    def key(self) -> tuple[str, int, int]:
        return (self.opponent, int(self.seed), int(self.side))


# --------------------------------------------------------------------------- #
# basic estimators
# --------------------------------------------------------------------------- #
def t95(df: int) -> float:
    """Two-sided 95% Student-t critical value (z for df > 30)."""
    if df <= 0:
        return float("inf")
    return _T95.get(df, Z95)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _sd(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def _t_ci(values: Sequence[float], *, z: float = Z95) -> tuple[float, float, float]:
    """Mean and a t-based CI for a small sample of cluster means."""
    n = len(values)
    mean = _mean(values)
    if n < 2:
        return mean, 0.0, 1.0
    crit = t95(n - 1) if z == Z95 else z
    se = _sd(values) / math.sqrt(n)
    return mean, mean - crit * se, mean + crit * se


def _normal_ci(mean: float, se: float, *, z: float = Z95) -> tuple[float, float]:
    return mean - z * se, mean + z * se


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    low = int(math.floor(pos))
    high = min(low + 1, len(sorted_values) - 1)
    frac = pos - low
    return sorted_values[low] * (1 - frac) + sorted_values[high] * frac


def cluster_bootstrap_ci(
    values: Sequence[float],
    *,
    n_boot: int = 2000,
    level: float = 0.95,
    seed: int = 12345,
) -> tuple[float, float, float]:
    """Bootstrap CI of the mean of cluster-level values (resample clusters)."""
    n = len(values)
    mean = _mean(values)
    if n < 1:
        return mean, 0.0, 1.0
    if n == 1:
        return mean, 0.0, 1.0
    rng = random.Random(seed)
    means = []
    for _ in range(int(n_boot)):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo = (1 - level) / 2
    return mean, _percentile(means, lo), _percentile(means, 1 - lo)


def valid_outcomes(outcomes: Iterable[MatchOutcome]) -> list[MatchOutcome]:
    """Outcomes usable for win-rate estimation (no engine error / truncation)."""
    return [o for o in outcomes if o.error is None and not o.truncated]


# --------------------------------------------------------------------------- #
# pooled + per-opponent summary
# --------------------------------------------------------------------------- #
def summarize(
    outcomes: Sequence[MatchOutcome],
    *,
    floor: float = 0.45,
    z: float = Z95,
    n_boot: int = 2000,
    boot_seed: int = 12345,
) -> dict[str, Any]:
    """Pooled + stratified win rates, cluster CI, and quality proxies.

    Only error/truncated-free games enter the rates; error/draw/truncation counts
    are reported alongside. ``pooled_ci_*`` is the (optimistic) binomial Wilson
    interval over all games; ``cluster_ci_*`` treats each opponent as a cluster
    and is the interval that should gate a go/no-go (see the protocol doc).
    """
    valid = valid_outcomes(outcomes)
    n_total = len(valid)
    total_score = sum(o.score for o in valid)
    pooled_rate = total_score / n_total if n_total else 0.0
    p_low, p_high = wilson_interval(total_score, n_total, z=z)

    grouped: dict[str, list[MatchOutcome]] = {}
    for outcome in valid:
        grouped.setdefault(outcome.opponent, []).append(outcome)

    per_opponent: dict[str, dict[str, Any]] = {}
    for opponent, group in sorted(grouped.items()):
        n = len(group)
        score = sum(o.score for o in group)
        rate = score / n if n else 0.0
        lo, hi = wilson_interval(score, n, z=z)
        rounds = [o.rounds for o in group]
        win_rounds = [o.rounds for o in group if o.score == 1.0]
        loss_rounds = [o.rounds for o in group if o.score == 0.0]
        per_opponent[opponent] = {
            "games": n,
            "score": score,
            "rate": rate,
            "ci_low": lo,
            "ci_high": hi,
            "mean_rounds": _mean(rounds),
            "mean_rounds_wins": _mean(win_rounds),
            "mean_rounds_losses": _mean(loss_rounds),
        }

    cluster_rates = [row["rate"] for row in per_opponent.values() if row["games"] > 0]
    cluster_mean, cluster_low, cluster_high = _t_ci(cluster_rates, z=z)
    _, boot_low, boot_high = cluster_bootstrap_ci(
        cluster_rates, n_boot=n_boot, seed=boot_seed
    )

    decisions = sum(o.decisions for o in valid)
    fallbacks = sum(o.fallbacks for o in valid)
    round_values = [o.rounds for o in valid]
    errors = sum(1 for o in outcomes if o.error is not None)
    truncated = sum(1 for o in outcomes if o.truncated)
    invalid = len(outcomes) - n_total
    quality = {
        "decisions": decisions,
        "fallbacks": fallbacks,
        "fallback_rate": (fallbacks / decisions) if decisions else 0.0,
        "illegal_rate": (fallbacks / decisions) if decisions else 0.0,
        "mean_rounds": _mean(round_values),
        "mean_decisions_per_round": (decisions / sum(round_values))
        if sum(round_values)
        else 0.0,
        "draws": sum(1 for o in valid if o.score == 0.5),
        "draw_rate": (sum(1 for o in valid if o.score == 0.5) / n_total)
        if n_total
        else 0.0,
        "errors": errors,
        "truncated": truncated,
        "invalid": invalid,
        "error_rate": (invalid / len(outcomes)) if outcomes else 0.0,
        "opponents_covered": len([1 for row in per_opponent.values() if row["games"] > 0]),
    }

    floor_violations = sorted(
        opponent
        for opponent, row in per_opponent.items()
        if row["games"] > 0 and row["rate"] < floor
    )

    return {
        "games": n_total,
        "matches": len(outcomes),
        "pooled_rate": pooled_rate,
        "pooled_ci_low": p_low,
        "pooled_ci_high": p_high,
        "cluster_mean": cluster_mean,
        "cluster_ci_low": min(cluster_low, cluster_high),
        "cluster_ci_high": max(cluster_low, cluster_high),
        "cluster_boot_ci_low": boot_low,
        "cluster_boot_ci_high": boot_high,
        "per_opponent": per_opponent,
        "quality": quality,
        "floor": floor,
        "floor_violations": floor_violations,
    }


def evaluate_gates(
    summary: Mapping[str, Any],
    *,
    pooled_threshold: float = 0.50,
    floor: float | None = None,
    require_clean: bool = True,
    strict_ci: bool = False,
) -> dict[str, Any]:
    """Map a summary to the protocol's go/no-go gates.

    ``strict_ci=True`` requires the *cluster* CI lower bound to clear the pooled
    threshold (certification-grade); otherwise only the point estimate must clear
    it (screening / training-gate grade). See ``reports/explore/eval_protocol.md``.
    """
    floor = summary.get("floor", 0.45) if floor is None else floor
    pooled = float(summary.get("pooled_rate", 0.0))
    cluster_low = float(summary.get("cluster_ci_low", 0.0))
    quality = summary.get("quality", {})
    per_opponent = summary.get("per_opponent", {})
    if strict_ci:
        # Certification form (§2.4 / §5.4): the per-opponent Wilson *lower*
        # bound must clear the floor, not just the noisy point estimate. The
        # screening form below keeps the point estimate so a `< floor` opponent
        # is flagged for investigation rather than auto-failed.
        violations = sorted(
            opponent
            for opponent, row in per_opponent.items()
            if row.get("games", 0) > 0 and float(row.get("ci_low", 0.0)) < floor
        )
        floor_mode = "wilson_low"
    else:
        violations = list(summary.get("floor_violations", []))
        floor_mode = "point"
    pooled_gate = pooled >= pooled_threshold and (
        cluster_low >= pooled_threshold if strict_ci else True
    )
    return {
        "pooled_threshold": pooled_threshold,
        "pooled_pass": bool(pooled_gate),
        "strict_ci": bool(strict_ci),
        "floor": floor,
        "floor_mode": floor_mode,
        "floor_pass": len(violations) == 0,
        "floor_violations": violations,
        "clean_pass": (quality.get("fallback_rate", 0.0) == 0.0
                       and quality.get("error_rate", 0.0) == 0.0)
        if require_clean
        else True,
        "pass": bool(
            pooled_gate
            and len(violations) == 0
            and (
                (quality.get("fallback_rate", 0.0) == 0.0
                 and quality.get("error_rate", 0.0) == 0.0)
                if require_clean
                else True
            )
        ),
    }


# --------------------------------------------------------------------------- #
# paired-seed A/B comparison
# --------------------------------------------------------------------------- #
def paired_difference(
    outcomes_a: Sequence[MatchOutcome],
    outcomes_b: Sequence[MatchOutcome],
    *,
    z: float = Z95,
    n_boot: int = 2000,
    boot_seed: int = 12345,
) -> dict[str, Any] | None:
    """Paired A-B delta over shared (opponent, seed, side) keys.

    The paired design cancels most seed/deck luck: the per-match difference
    ``d = scoreA - scoreB`` has much smaller variance than two independent rate
    CIs, so far fewer games are needed to resolve a real change. Returns ``None``
    when the two runs share no keys.
    """
    a_all = {o.key: o for o in outcomes_a}
    b_all = {o.key: o for o in outcomes_b}
    shared = set(a_all) & set(b_all)
    if not shared:
        return None
    # Only clean games on *both* sides are comparable: the runner scores an
    # engine error/truncation as 0.5, so pairing a crash (0.5) against a real
    # win (0.0) would inject a spurious +0.5 into the delta and bias the merge
    # gate. Drop any pair where either side is invalid.
    a = {k: a_all[k] for k in shared if a_all[k].error is None and not a_all[k].truncated}
    b = {k: b_all[k] for k in shared if b_all[k].error is None and not b_all[k].truncated}
    keys = sorted(set(a) & set(b))
    if not keys:
        return None
    diffs = [a[k].score - b[k].score for k in keys]
    n = len(diffs)
    mean = _mean(diffs)
    se = _sd(diffs) / math.sqrt(n) if n else 0.0
    low, high = _normal_ci(mean, se, z=z)

    by_opp: dict[str, list[float]] = {}
    for key in keys:
        by_opp.setdefault(key[0], []).append(a[key].score - b[key].score)
    per_opponent = {opp: {"n": len(d), "delta": _mean(d)} for opp, d in sorted(by_opp.items())}
    cluster_mean, cluster_low, cluster_high = _t_ci(
        [row["delta"] for row in per_opponent.values()], z=z
    )
    _, boot_low, boot_high = cluster_bootstrap_ci(
        [row["delta"] for row in per_opponent.values()], n_boot=n_boot, seed=boot_seed
    )

    return {
        "n_pairs": n,
        "n_shared": len(shared),
        "n_pairs_dropped": len(shared) - n,
        "invalid_a": sum(1 for o in outcomes_a if o.error is not None or o.truncated),
        "invalid_b": sum(1 for o in outcomes_b if o.error is not None or o.truncated),
        "delta": mean,
        "se": se,
        "ci_low": low,
        "ci_high": high,
        "rate_a": _mean([a[k].score for k in keys]),
        "rate_b": _mean([b[k].score for k in keys]),
        "opponent_cluster_ci_low": min(cluster_low, cluster_high),
        "opponent_cluster_ci_high": max(cluster_low, cluster_high),
        "cluster_boot_ci_low": boot_low,
        "cluster_boot_ci_high": boot_high,
        "per_opponent": per_opponent,
        "significant_positive": low > 0.0,
    }


def noninferiority_gate(
    paired: Mapping[str, Any], *, tolerance: float = 0.02
) -> dict[str, Any]:
    """Training merge gate: new checkpoint must not regress beyond ``tolerance``.

    Accept when the paired 95% CI lower bound of ``new - incumbent`` is above
    ``-tolerance`` (a one-sided non-inferiority test); this avoids rejecting a
    good checkpoint because of seed noise (PLAN.md WS5 merge gate).
    """
    low = float(paired.get("ci_low", -1.0))
    return {
        "tolerance": tolerance,
        "ci_low": low,
        "accept": low >= -tolerance,
    }


def merge_gate(
    paired: Mapping[str, Any] | None,
    gates: Mapping[str, Any] | None,
    *,
    tolerance: float = 0.02,
) -> dict[str, Any]:
    """WS5 training-merge decision: non-inferior on paired seeds AND a clean run.

    The paired CI alone is not sufficient: dropping invalid pairs removes the
    crash bias in one direction but leaves survivorship in the other, so a
    contestant that crashes on games it would lose can still look non-inferior
    on its surviving pairs. The merge therefore also requires
    ``gates.clean_pass`` (no error/truncation and no fallbacks). ``available`` is
    ``False`` when there is no paired comparison to decide on.
    """
    if not paired:
        return {
            "available": False,
            "accept": False,
            "noninferior": None,
            "clean": None,
            "tolerance": tolerance,
        }
    noninferior = bool(noninferiority_gate(paired, tolerance=tolerance)["accept"])
    clean = bool((gates or {}).get("clean_pass", False))
    return {
        "available": True,
        "tolerance": tolerance,
        "noninferior": noninferior,
        "clean": clean,
        "accept": bool(noninferior and clean),
    }


# --------------------------------------------------------------------------- #
# fixed-anchor rating (Bradley-Terry / Elo MLE)
# --------------------------------------------------------------------------- #
def win_probability(rating_a: float, rating_b: float) -> float:
    """Elo/Bradley-Terry win probability for A vs B (scale 400)."""
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def _fit_anchor_rating(games: Sequence[tuple[float, float]]) -> float:
    """MLE of the contestant rating against fixed opponent anchor ratings.

    ``games`` are ``(anchor_rating, score)`` tuples. The log-likelihood gradient
    is monotone decreasing in the contestant rating, so a bisection is exact.
    """
    if not games:
        return 1500.0
    lo = min(anchor for anchor, _ in games) - 1500.0
    hi = max(anchor for anchor, _ in games) + 1500.0

    def gradient(rating: float) -> float:
        return sum(score - win_probability(rating, anchor) for anchor, score in games)

    if gradient(lo) <= 0.0:
        return lo
    if gradient(hi) >= 0.0:
        return hi
    for _ in range(120):
        mid = 0.5 * (lo + hi)
        if gradient(mid) > 0.0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def anchored_bt_rating(
    outcomes: Sequence[MatchOutcome],
    anchors: Mapping[str, float],
    *,
    n_boot: int = 2000,
    boot_seed: int = 12345,
    level: float = 0.95,
) -> dict[str, Any] | None:
    """Contestant rating vs the fixed scripted pool, with a bootstrap CI.

    Anchoring to the *historical* L5.1 20x20 Elo makes the number comparable
    across runs and checkpoints (the pool is a fixed ruler). Returns ``None`` when
    no anchor is known for any played opponent.
    """
    valid = valid_outcomes(outcomes)
    games = [
        (float(anchors[o.opponent]), float(o.score))
        for o in valid
        if o.opponent in anchors
    ]
    if not games:
        return None
    rating = _fit_anchor_rating(games)
    rng = random.Random(boot_seed)
    boots = []
    for _ in range(int(n_boot)):
        sample = [games[rng.randrange(len(games))] for _ in range(len(games))]
        boots.append(_fit_anchor_rating(sample))
    boots.sort()
    lo_q = (1 - level) / 2
    expected = _mean([win_probability(rating, anchor) for anchor, _ in games])
    return {
        "rating": rating,
        "ci_low": _percentile(boots, lo_q),
        "ci_high": _percentile(boots, 1 - lo_q),
        "n_games": len(games),
        "mean_expected_score": expected,
    }


def rating_difference(
    rating_a: Mapping[str, Any] | None,
    rating_b: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Difference of two anchored ratings (for 'beating the old self')."""
    if not rating_a or not rating_b:
        return None
    delta = float(rating_a["rating"]) - float(rating_b["rating"])
    return {
        "delta": delta,
        "a": float(rating_a["rating"]),
        "b": float(rating_b["rating"]),
        "significant_positive": delta > 0.0,
    }


def elo_from_records(records: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """Thin re-export of the online Elo ladder over raw match records."""
    from eval.ladder import compute_elo

    return compute_elo(records)


# --------------------------------------------------------------------------- #
# exploitability proxy
# --------------------------------------------------------------------------- #
def best_response_probe(
    per_opponent: Mapping[str, Mapping[str, Any]],
    *,
    floor: float = 0.45,
) -> dict[str, Any] | None:
    """Cheap exploitability proxy: the worst matchup is a fixed best-responder.

    Exact game-theoretic exploitability is out of reach (PLAN.md §7), so we report
    a **lower bound**: ``max_opponent(1 - p_o) = 1 - min_o p_o`` is the maximum
    score any single fixed scripted opponent achieves against the contestant. A
    high value means *some* pure opponent still beats us, i.e. we are far from
    Nash on that matchup. The mean-minus-worst gap measures matchup fragility.
    """
    rates = {
        opponent: float(row["rate"])
        for opponent, row in per_opponent.items()
        if row.get("games", 0) > 0
    }
    if not rates:
        return None
    worst_opponent = min(rates, key=rates.get)
    worst_rate = rates[worst_opponent]
    mean_rate = _mean(list(rates.values()))
    return {
        "worst_opponent": worst_opponent,
        "worst_rate": worst_rate,
        "pool_mean_rate": mean_rate,
        "exploitability_lower_bound": 1.0 - worst_rate,
        "matchup_gap": mean_rate - worst_rate,
        "floor": floor,
        "floor_gap": worst_rate - floor,
        "worst_clear_floor": worst_rate >= floor,
    }


# --------------------------------------------------------------------------- #
# sample-size planning
# --------------------------------------------------------------------------- #
def sample_size_for_halfwidth(
    halfwidth: float, *, z: float = Z95, p: float = 0.5
) -> int:
    """Games needed for a ``halfwidth`` Wilson/normal 95% CI at proportion ``p``."""
    return int(math.ceil(z * z * p * (1 - p) / (halfwidth * halfwidth)))


def sample_size_table(
    halfwidths: Sequence[float] = (0.10, 0.075, 0.05, 0.04, 0.03, 0.02, 0.01),
    *,
    pool_size: int = 20,
    throughput_games_per_hour: float = 4900.0,
    z: float = Z95,
) -> list[dict[str, Any]]:
    """Total / per-opponent sample sizes for a target pooled CI half-width."""
    rows = []
    for halfwidth in halfwidths:
        total = sample_size_for_halfwidth(halfwidth, z=z)
        per_opponent = int(math.ceil(total / pool_size))
        rows.append(
            {
                "halfwidth": halfwidth,
                "total_games": total,
                "games_per_opponent": per_opponent,
                "hours_at_4900gph": total / throughput_games_per_hour,
            }
        )
    return rows


def floor_sample_size(
    target_rate: float,
    *,
    floor: float = 0.45,
    z: float = Z95,
    confidence: float = 0.95,
) -> int:
    """Games per opponent so the Wilson **lower** bound clears ``floor``.

    Uses the Wilson interval at the observed ``target_rate``; the required ``n``
    grows sharply as ``target_rate`` approaches ``floor`` (you cannot certify a
    floor you barely beat). Used to size the M4 gate honestly.
    """
    zz = z if confidence == 0.95 else _z_for(confidence)
    for n in range(5, 200001, 5):
        lo, _ = wilson_interval(target_rate * n, n, z=zz)
        if lo >= floor:
            return n
    return 200000


def _z_for(confidence: float) -> float:
    """Two-sided z for a confidence level (inverse normal via bisection)."""
    target = (1 + confidence) / 2

    def cdf(x: float) -> float:
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))

    lo, hi = -8.0, 8.0
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if cdf(mid) < target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def detection_sample_size(
    delta: float, *, z_alpha: float = Z95, power: float = 0.80, p: float = 0.5
) -> int:
    """Games **per arm** to detect an absolute rate change ``delta`` (two-sided)."""
    z_beta = _z_for(power) if power != 0.80 else 0.8416
    return int(math.ceil((z_alpha + z_beta) ** 2 * 2 * p * (1 - p) / (delta * delta)))


__all__ = [
    "MatchOutcome",
    "Z95",
    "ELO_SCALE",
    "t95",
    "cluster_bootstrap_ci",
    "valid_outcomes",
    "summarize",
    "evaluate_gates",
    "paired_difference",
    "noninferiority_gate",
    "merge_gate",
    "win_probability",
    "anchored_bt_rating",
    "rating_difference",
    "elo_from_records",
    "best_response_probe",
    "sample_size_for_halfwidth",
    "sample_size_table",
    "floor_sample_size",
    "detection_sample_size",
]
