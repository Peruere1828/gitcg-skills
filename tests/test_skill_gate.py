from __future__ import annotations

import json

import pytest

from skillloop.gate import GateResult, load_rows, paired_delta, t95


def _row(stage: str, seed: int, three_star: float, **extra) -> dict:
    return {"stage": stage, "seed": seed, "three_star": three_star, "error": None, **extra}


def test_identical_runs_have_zero_delta_and_are_not_admitted():
    a = [_row("s1", 1, 1.0), _row("s1", 2, 0.0), _row("s2", 1, 0.0)]
    result = paired_delta(a, list(a))
    assert result.mean_delta == pytest.approx(0.0)
    assert result.ci_low == pytest.approx(0.0)
    assert result.accept is False  # rule is CI_low > 0, not >= 0


def test_uniform_improvement_is_admitted():
    a = [_row("s1", 1, 0.0), _row("s2", 1, 0.0), _row("s3", 1, 0.0)]
    b = [_row("s1", 1, 1.0), _row("s2", 1, 1.0), _row("s3", 1, 1.0)]
    result = paired_delta(a, b)
    assert result.mean_delta == pytest.approx(1.0)
    assert result.ci_low == pytest.approx(1.0)  # zero variance across stages
    assert result.accept is True
    assert result.n_stages == 3 and result.n_pairs == 3


def test_mixed_deltas_use_t_interval_across_stages():
    a = [_row("s1", 1, 0.0), _row("s2", 1, 0.0), _row("s3", 1, 0.0), _row("s4", 1, 0.0)]
    b = [_row("s1", 1, 1.0), _row("s2", 1, 1.0), _row("s3", 1, 1.0), _row("s4", 1, 0.0)]
    result = paired_delta(a, b)
    assert result.mean_delta == pytest.approx(0.75)
    # deltas are [1,1,1,0] -> sd = 0.5, se = 0.25, t(3)=3.182 -> half = 0.7955
    half = t95(3) * 0.25
    assert result.ci_low == pytest.approx(0.75 - half)
    assert result.ci_high == pytest.approx(0.75 + half)
    assert result.accept is False


def test_pairs_are_matched_on_stage_and_seed():
    a = [_row("s1", 1, 1.0), _row("s1", 2, 1.0)]
    b = [_row("s1", 2, 0.0), _row("s2", 9, 0.0)]
    result = paired_delta(a, b)
    assert result.n_pairs == 1           # only (s1, 2) is shared
    assert result.dropped == 2   # one unpaired row on each side
    assert result.mean_delta == pytest.approx(-1.0)


def test_error_and_truncated_rows_are_ignored():
    a = [_row("s1", 1, 1.0), _row("s1", 2, 0.0, error="boom"), _row("s1", 3, 0.0, truncated=True)]
    b = [_row("s1", 1, 1.0), _row("s1", 2, 1.0), _row("s1", 3, 1.0)]
    result = paired_delta(a, b)
    assert result.n_pairs == 1
    assert result.mean_delta == pytest.approx(0.0)


def test_no_overlap_raises():
    with pytest.raises(ValueError):
        paired_delta([_row("s1", 1, 1.0)], [_row("s2", 1, 1.0)])


def test_table_and_serialisation(tmp_path):
    a = [_row("s1", 1, 0.0), _row("s2", 1, 0.0)]
    b = [_row("s1", 1, 1.0), _row("s2", 1, 1.0)]
    result = paired_delta(a, b)
    assert isinstance(result, GateResult)
    assert "MEAN" in result.table()
    payload = result.as_dict()
    assert payload["accept"] is True and payload["n_stages"] == 2

    path = tmp_path / "a.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in a) + "\n", encoding="utf-8")
    assert len(load_rows(path)) == 2
