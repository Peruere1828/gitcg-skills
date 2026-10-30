"""P0 eval-seed regression: ``--seed-start`` must not be added twice."""

from __future__ import annotations

from scripts.eval_protocol import (
    ProtocolSpec,
    ProtocolTask,
    _enrich,
    _protocol_seeds,
)


def _spec(**overrides) -> ProtocolSpec:
    base = dict(
        contestant="heuristic",
        contestant_deck="superconduct_aggro",
        opponents=("natlan_battleship",),
        seeds=(0, 1),
        sides=(0, 1),
        base_seed=900,
    )
    base.update(overrides)
    return ProtocolSpec(**base)


def test_protocol_seeds_are_relative_offsets():
    assert _protocol_seeds(10) == tuple(range(10))


def test_seed_start_is_applied_exactly_once():
    spec = _spec()
    matches = [task.to_match(spec) for task in spec.tasks()]
    assert [match.seed for match in matches] == [900, 900, 901, 901]


def test_enrich_records_engine_match_seed():
    spec = _spec()
    task = ProtocolTask(index=0, opponent="natlan_battleship", seed=0, side=0)
    row = _enrich(
        {
            "task_index": 0,
            "winner": 0,
            "rounds": 7,
            "decisions0": 12,
            "fallbacks0": 0,
            "decisions1": 11,
            "fallbacks1": 0,
        },
        task,
        spec,
    )
    assert row["protocol_seed"] == 0
    assert row["match_seed"] == 900
