"""WS0 smoke test: two scripted experts play a full game without IO errors."""

from __future__ import annotations

from common.seeding import derive_seed
from envs.decks import deck_spec
from envs.match import run_match
from envs.policy import expert_policy

A, B = "superconduct_aggro", "natlan_battleship"


def _policies(seed: int):
    return (
        expert_policy(A, seed=derive_seed(seed, "p0", A)),
        expert_policy(B, seed=derive_seed(seed, "p1", B)),
    )


def test_scripted_match_finishes_cleanly():
    p0, p1 = _policies(7)
    record = run_match(deck_spec(A), deck_spec(B), p0, p1, seed=7)
    assert record.error is None
    assert record.io_errors0 == ()
    assert record.io_errors1 == ()
    assert record.truncated is False
    assert record.winner in (0, 1, None)
    assert record.rounds >= 1
    assert record.decisions0 > 0 and record.decisions1 > 0


def test_no_illegal_action_fallbacks():
    p0, p1 = _policies(7)
    record = run_match(deck_spec(A), deck_spec(B), p0, p1, seed=7)
    # Scripted experts should always pick a legal action; fallbacks signal an
    # adapter/agent bug, not normal play.
    assert record.fallbacks0 == 0
    assert record.fallbacks1 == 0
