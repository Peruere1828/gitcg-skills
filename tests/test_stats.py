"""Wilson interval + rate estimation sanity."""

from __future__ import annotations

from eval.stats import estimate_rate, outcome_score, wilson_interval


def test_wilson_interval_bounds_and_center():
    low, high = wilson_interval(50, 100)
    assert 0.0 <= low < 0.5 < high <= 1.0
    assert low < 0.41 and high > 0.59  # roughly the normal approx


def test_wilson_extremes_are_not_degenerate():
    low, high = wilson_interval(0, 10)
    assert low == 0.0 and 0.0 < high < 1.0
    low, high = wilson_interval(10, 10)
    assert 0.0 < low < 1.0 and high == 1.0


def test_empty_sample_is_maximally_uncertain():
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_estimate_rate_dict():
    est = estimate_rate(3, 4)
    payload = est.as_dict()
    assert payload["games"] == 4
    assert payload["rate"] == 0.75
    assert payload["ci_low"] <= 0.75 <= payload["ci_high"]


def test_outcome_score_counts_draws_as_half():
    assert outcome_score(0, 0) == 1.0
    assert outcome_score(1, 0) == 0.0
    assert outcome_score(None, 0) == 0.5
