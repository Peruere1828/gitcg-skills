"""Synthetic fixtures for the two-dimensional paired gates.

The three-star gate is unchanged; these tests pin the new ``rounds_gate``:
rounds are paired on ``(stage, seed)`` and only compared when both arms reached
three stars (a non-3★ game has no "rounds to 3★").
"""

from __future__ import annotations

import json

import pytest

from scripts.compare_groups import main


def _row(stage: str, seed: int, three_star: bool, rounds: int, **extra) -> dict:
    return {
        "stage": stage,
        "seed": seed,
        "three_star": three_star,
        "rounds": rounds,
        "winner": 0 if three_star else 1,
        "error": None,
        "truncated": False,
        **extra,
    }


def _write(path, rows) -> str:
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    return str(path)


def _compare(tmp_path, rows_a, rows_b) -> dict:
    out = tmp_path / "gate.json"
    code = main(
        [
            "--a",
            _write(tmp_path / "a.jsonl", rows_a),
            "--b",
            _write(tmp_path / "b.jsonl", rows_b),
            "--label-a",
            "a",
            "--label-b",
            "b",
            "--out",
            str(out),
        ]
    )
    assert code == 0
    return json.loads(out.read_text(encoding="utf-8"))


def test_b_faster_on_every_three_star_pair_is_accepted(tmp_path):
    rows_a = [_row(stage, seed, True, 8) for stage in ("s1", "s2", "s3") for seed in (1, 2)]
    rows_b = [_row(stage, seed, True, 6) for stage in ("s1", "s2", "s3") for seed in (1, 2)]

    payload = _compare(tmp_path, rows_a, rows_b)
    gate = payload["rounds_gate"]

    assert gate["insufficient"] is False
    assert gate["accept"] is True
    assert gate["mean_delta"] == pytest.approx(-2.0)
    assert gate["ci_low"] == pytest.approx(-2.0)
    assert gate["ci_high"] == pytest.approx(-2.0)
    assert gate["n_pairs"] == 6 and gate["n_stages"] == 3
    assert gate["n_dropped"] == 0
    # The three-star gate is unchanged and sees no difference.
    assert payload["gate"]["mean_delta"] == pytest.approx(0.0)
    assert payload["gate"]["accept"] is False


def test_b_slower_on_three_star_pairs_is_not_accepted(tmp_path):
    rows_a = [_row(stage, seed, True, 6) for stage in ("s1", "s2", "s3") for seed in (1, 2)]
    rows_b = [_row(stage, seed, True, 8) for stage in ("s1", "s2", "s3") for seed in (1, 2)]

    gate = _compare(tmp_path, rows_a, rows_b)["rounds_gate"]

    assert gate["insufficient"] is False
    assert gate["accept"] is False
    assert gate["mean_delta"] == pytest.approx(2.0)
    assert gate["ci_low"] == pytest.approx(2.0)
    assert gate["ci_high"] == pytest.approx(2.0)


def test_pairs_with_one_non_three_star_arm_are_dropped(tmp_path):
    # 8 shared pairs; 2 have exactly one 3★ arm and must leave the conditional set.
    rows_a = [_row(stage, seed, True, 8) for stage in ("s1", "s2", "s3", "s4") for seed in (1, 2)]
    rows_b = [_row(stage, seed, True, 7) for stage in ("s1", "s2", "s3", "s4") for seed in (1, 2)]
    # A fast loss must not buy a speed credit: a=20 rounds and not 3★.
    rows_a[0] = _row("s1", 1, False, 20)
    rows_b[0] = _row("s1", 1, True, 2)
    # A slow 3★ game on the other arm is also unpaired (different stage so both
    # s1 and s2 keep a usable pair).
    rows_a[2] = _row("s2", 1, True, 9)
    rows_b[2] = _row("s2", 1, False, 5)

    gate = _compare(tmp_path, rows_a, rows_b)["rounds_gate"]

    assert gate["n_all_pairs"] == 8
    assert gate["n_pairs"] == 6
    assert gate["n_dropped"] == 2
    assert gate["dropped_not_three_star"] == 2
    assert gate["dropped_missing_rounds"] == 0
    assert gate["n_stages"] == 4
    assert gate["insufficient"] is False
    # Conditional delta uses only 3★-vs-3★ pairs: 7 - 8 = -1.
    assert gate["mean_delta"] == pytest.approx(-1.0)
    assert gate["accept"] is True
    # Unconditional context includes the dropped pairs: (20+9+8*6)/8 and
    # (2+5+7*6)/8, i.e. a paired delta of -3.5.
    assert gate["mean_rounds_a"] == pytest.approx(9.625)
    assert gate["mean_rounds_b"] == pytest.approx(6.125)
    assert gate["mean_rounds_delta"] == pytest.approx(-3.5)
    assert gate["n_rounds_pairs"] == 8


def test_rounds_zero_or_missing_is_dropped_from_the_conditional_set(tmp_path):
    rows_a = [_row(stage, seed, True, 8) for stage in ("s1", "s2", "s3") for seed in (1, 2)]
    rows_b = [_row(stage, seed, True, 7) for stage in ("s1", "s2", "s3") for seed in (1, 2)]
    rows_a[0]["rounds"] = 0  # unmeasurable length, not a legitimate 0-round clear

    gate = _compare(tmp_path, rows_a, rows_b)["rounds_gate"]

    assert gate["n_pairs"] == 5
    assert gate["dropped_missing_rounds"] == 1
    assert gate["n_rounds_pairs"] == 5


def test_one_stage_is_insufficient_and_produces_no_ci(tmp_path):
    rows_a = [_row("s1", seed, True, 8) for seed in range(1, 7)]
    rows_b = [_row("s1", seed, True, 6) for seed in range(1, 7)]

    gate = _compare(tmp_path, rows_a, rows_b)["rounds_gate"]

    assert gate["insufficient"] is True
    assert gate["accept"] is False
    assert gate["ci_low"] is None and gate["ci_high"] is None
    assert gate["n_stages"] == 1 and gate["n_pairs"] == 6
    assert "2 stages" in gate["reason"]


def test_fewer_than_five_pairs_is_insufficient(tmp_path):
    rows_a = [_row(stage, seed, True, 8) for stage in ("s1", "s2") for seed in (1, 2)]
    rows_b = [_row(stage, seed, True, 6) for stage in ("s1", "s2") for seed in (1, 2)]

    gate = _compare(tmp_path, rows_a, rows_b)["rounds_gate"]

    assert gate["insufficient"] is True
    assert gate["accept"] is False
    assert gate["ci_low"] is None and gate["ci_high"] is None
    assert gate["n_stages"] == 2 and gate["n_pairs"] == 4
    assert "5 paired" in gate["reason"]


def test_no_usable_three_star_pairs_reports_insufficient_instead_of_raising(tmp_path):
    # The keys are shared (so the three-star gate can run), but no pair has both
    # arms at three stars: there is nothing to compare on rounds.
    rows_a = [_row("s1", 1, True, 8), _row("s2", 1, False, 9)]
    rows_b = [_row("s1", 1, False, 6), _row("s2", 1, True, 7)]

    gate = _compare(tmp_path, rows_a, rows_b)["rounds_gate"]

    assert gate["insufficient"] is True
    assert gate["n_all_pairs"] == 2 and gate["n_pairs"] == 0
    assert gate["dropped_not_three_star"] == 2
    assert gate["mean_delta"] is None and gate["ci_high"] is None
    # Unconditional context still has both pairs.
    assert gate["n_rounds_pairs"] == 2
    assert gate["mean_rounds_delta"] == pytest.approx((6 - 8 + 7 - 9) / 2)
