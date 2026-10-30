"""L5.4 engine bridge: snapshot properties + replay-branch teacher determinism.

The snapshot tests document what the pybinding supports (faithful
round-trip, clone-deterministic resume, exact fork at ``is_resumable()`` boundary
points when game-level attrs are mirrored — see ``envs.snapshot.FORK_LIMITATION``)
and what it does not (fork at ``canResume:false`` mid-phase pauses). The
replay-branch tests
assert the D12 fallback teacher is deterministic and reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from envs.decks import deck_spec
from envs.match import PolicyPlayer, _build_create_param
from envs.policy import CallablePolicy, expert_policy
from envs.snapshot import (
    capture_snapshot,
    fork_game,
    snapshot_roundtrip_is_faithful,
)
from eval.stats import outcome_score
from train.replay_branch import (
    InjectionPolicy,
    capture_trajectory,
    evaluate_decision,
    replay_branch,
)

A, B = "superconduct_aggro", "natlan_battleship"


# --------------------------------------------------------------------------- #
# fast unit tests (no engine play)
# --------------------------------------------------------------------------- #
@dataclass
class _Ctx:
    legal_low_level_codes: tuple[int, ...]
    request_type: object = None
    step_index: int = 0


@dataclass
class _Built:
    context: _Ctx


class _Fixed:
    """Policy stub returning the option index equal to its counter."""

    name = "fixed"

    def __init__(self, value: int) -> None:
        self.value = value

    def choose(self, built) -> int:
        return int(built.context.legal_low_level_codes[self.value])


def test_injection_policy_forces_option_only_at_ordinal():
    prefix = _Fixed(0)
    rollout = _Fixed(0)
    inject = InjectionPolicy(prefix=prefix, rollout=rollout, ordinal=2, option_index=1)
    built = _Built(_Ctx(legal_low_level_codes=(100, 101, 102)))
    first = inject.choose(built)   # ordinal 1 -> prefix -> option 0
    second = inject.choose(built)  # ordinal 2 -> forced option 1
    third = inject.choose(built)   # ordinal 3 -> rollout -> option 0
    assert (first, second, third) == (100, 101, 100)


def test_injection_policy_clamps_out_of_range_option():
    inject = InjectionPolicy(prefix=_Fixed(0), rollout=_Fixed(0), ordinal=1, option_index=99)
    built = _Built(_Ctx(legal_low_level_codes=(100, 101)))
    assert inject.choose(built) == 101  # clamped to last option


# --------------------------------------------------------------------------- #
# engine-play tests
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def base_trajectory():
    record, points = capture_trajectory(A, B, 3)
    assert record.error is None
    assert points
    return record, points


def _live_game_with_snapshot(seed: int, steps: int):
    from gitcg import Game, low_level

    game = Game(create_param=_build_create_param(deck_spec(A), deck_spec(B), seed=seed))
    game.set_attr(low_level.ATTR_PLAYER_ALWAYS_OMNI_0, 1)
    game.set_attr(low_level.ATTR_PLAYER_ALWAYS_OMNI_1, 1)
    game.set_player(0, PolicyPlayer(0, _first_option_policy()))
    game.set_player(1, PolicyPlayer(1, _first_option_policy()))
    game.start()
    for _ in range(steps):
        if not game.is_running():
            break
        game.step()
    return game, capture_snapshot(game)


def _first_option_policy():
    def choose(built):
        codes = list(built.context.legal_low_level_codes)
        return int(codes[0]) if codes else -1

    return CallablePolicy(choose, name="first")


@pytest.mark.slow
def test_snapshot_roundtrip_is_faithful():
    _game, snap = _live_game_with_snapshot(7, 5)
    assert snapshot_roundtrip_is_faithful(snap)


@pytest.mark.slow
def test_snapshot_resume_is_clone_deterministic():
    _game, snap = _live_game_with_snapshot(7, 5)
    terminals = []
    for _ in range(2):
        game = fork_game(snap)
        game.set_player(0, PolicyPlayer(0, _first_option_policy()))
        game.set_player(1, PolicyPlayer(1, _first_option_policy()))
        game.start()
        while game.is_running():
            game.step()
        terminals.append(capture_snapshot(game))
    assert terminals[0] == terminals[1]


@pytest.mark.slow
def test_boundary_snapshot_fork_reproduces_live_terminal():
    """A snapshot from an ``is_resumable()`` point is an exact fork (PLAN.md I10).

    Both sides use the *stateless* first-option policy so any divergence is
    attributable to the engine, not policy state; the fork mirrors the live
    game's game-level attrs (they live outside ``GameState``).
    """
    from gitcg import Game, low_level

    attrs = {
        low_level.ATTR_PLAYER_ALWAYS_OMNI_0: 1,
        low_level.ATTR_PLAYER_ALWAYS_OMNI_1: 1,
    }
    game = Game(create_param=_build_create_param(deck_spec(A), deck_spec(B), seed=7))
    for attr, value in attrs.items():
        game.set_attr(attr, value)
    game.set_player(0, PolicyPlayer(0, _first_option_policy()))
    game.set_player(1, PolicyPlayer(1, _first_option_policy()))
    game.start()
    steps = 0
    while game.is_running() and not (steps >= 2 and game.is_resumable()):
        game.step()
        steps += 1
    assert game.is_resumable(), "expected a boundary pause within the first steps"
    snap = capture_snapshot(game)
    while game.is_running():
        game.step()
    live_terminal = capture_snapshot(game)

    forked = fork_game(snap, game_attrs=attrs)
    forked.set_player(0, PolicyPlayer(0, _first_option_policy()))
    forked.set_player(1, PolicyPlayer(1, _first_option_policy()))
    forked.start()
    while forked.is_running():
        forked.step()
    assert capture_snapshot(forked) == live_terminal


@pytest.mark.slow
def test_replay_branch_base_choice_reproduces_base(base_trajectory):
    record, points = base_trajectory
    point = points[len(points) // 2]
    forked = replay_branch(
        A, B, 3, ordinal=point.ordinal, option_index=point.chosen_option
    )
    assert forked.error is None
    assert (forked.winner, forked.rounds, forked.decisions0) == (
        record.winner,
        record.rounds,
        record.decisions0,
    )


@pytest.mark.slow
def test_replay_branch_identical_forks_agree(base_trajectory):
    _record, points = base_trajectory
    point = min(points[1:20], key=lambda p: p.option_count)
    alternates = [i for i in range(point.option_count) if i != point.chosen_option]
    if not alternates:
        pytest.skip("decision has a single option")
    alt = alternates[0]
    first = replay_branch(A, B, 3, ordinal=point.ordinal, option_index=alt)
    second = replay_branch(A, B, 3, ordinal=point.ordinal, option_index=alt)
    assert first.error is None and second.error is None
    assert (first.winner, first.rounds, first.decisions0) == (
        second.winner,
        second.rounds,
        second.decisions0,
    )


@pytest.mark.slow
def test_evaluate_decision_covers_candidates(base_trajectory):
    _record, points = base_trajectory
    point = min(points[1:20], key=lambda p: p.option_count)
    evaluation = evaluate_decision(A, B, 3, ordinal=point.ordinal, rollouts=1, top_k=2)
    assert evaluation.request_type == point.request_type
    assert evaluation.base_choice == point.chosen_option
    assert evaluation.base_choice in evaluation.options
    assert 1 <= len(evaluation.options) <= 2
    assert evaluation.best_option in evaluation.options
    for value in evaluation.options.values():
        assert value.n == 1
        assert value.mean in (0.0, 0.5, 1.0)
    assert 0.0 <= outcome_score(1, 0) <= 1.0  # stats helper sanity
