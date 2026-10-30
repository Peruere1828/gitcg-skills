"""CLI worker resolution: 0/None mean all cores; negatives are rejected."""

from __future__ import annotations

import pytest

from envs.rollout import normalize_workers


def test_normalize_workers_zero_and_none_mean_all_cores():
    assert normalize_workers(None) is None
    assert normalize_workers(0) is None
    assert normalize_workers(4) == 4


def test_normalize_workers_rejects_negative():
    with pytest.raises(ValueError):
        normalize_workers(-1)
