"""Reroll labels must describe *this* decision, not the code's first registration.

``HierarchicalActionCodebook`` is process-global and keys low-level specs by a
positional ``reroll_dice_mask``. Two different dice pools that discard the same
positions therefore share one action code, and the codebook keeps the label of
whichever pool registered it first. Before the fix ``label_by_low_level_code``
(and the decision record's ``chosen_label``) came from that stale materialized
spec, so replay printed ``弃[...]`` with dice that were not in the pool.
"""

from __future__ import annotations

import pytest

from envs.match import PolicyPlayer
from reps.action_adapter import build_decision_context
from reps.action_hierarchy import reset_default_hierarchical_action_codebook
from reps.schema import (
    CharacterSnapshot,
    DecisionType,
    PlayerSnapshot,
    StateSnapshot,
)


@pytest.fixture(autouse=True)
def _fresh_codebook():
    reset_default_hierarchical_action_codebook()
    yield
    reset_default_hierarchical_action_codebook()


def _view(dice: tuple[int, ...]) -> StateSnapshot:
    character = CharacterSnapshot(
        id=1,
        definition_id=1403,  # 刻晴, only used by the high-level reroll bucket
        health=10,
        max_health=10,
        energy=0,
        max_energy=3,
        defeated=False,
        is_active=True,
    )
    player = PlayerSnapshot(
        active_character_id=1,
        characters=(character,),
        combat_statuses=(),
        summons=(),
        supports=(),
        hand_cards=(),
        pile_cards=(),
        dice=dice,
        declared_end=False,
        legend_used=False,
    )
    return StateSnapshot(
        phase="reroll",
        round_number=1,
        current_turn=0,
        winner=None,
        players=(player, player),
    )


def _built(dice: tuple[int, ...]):
    view = _view(dice)
    return build_decision_context(
        acting_player=0,
        request_type=DecisionType.REROLL_DICE,
        request=None,  # engine request is unused by the reroll enumerator
        full_state=view,
        player_view=view,
        full_state_json=None,
        step_index=1,
    )


def _code_for_dice(built, dice: list[int]) -> int:
    wanted = sorted(int(value) for value in dice)
    for code in built.context.legal_low_level_codes:
        payload = built.payload_by_low_level_code[int(code)]
        if sorted(int(value) for value in payload["dice_to_reroll"]) == wanted:
            return int(code)
    raise AssertionError(f"no legal reroll code discards {wanted}")


def _label_dice(label: str) -> list[int]:
    prefix, _, values = label.partition(":")
    assert prefix == "reroll"
    return sorted(int(value) for value in values.split(",")) if values and values != "none" else []


def test_reroll_label_follows_the_current_pool_for_a_shared_mask():
    # Same positions 2..4 discarded -> same mask (0b11100), different dice.
    pool_a = (1, 2, 3, 4, 5, 6, 7, 8)
    pool_b = (8, 8, 1, 2, 3, 4, 5, 6)

    built_a = _built(pool_a)
    code_a = _code_for_dice(built_a, [3, 4, 5])
    assert built_a.label_by_low_level_code[code_a] == "reroll:3,4,5"

    built_b = _built(pool_b)
    code_b = _code_for_dice(built_b, [1, 2, 3])

    # The shared codebook reused the same code (this is the bug's trigger).
    assert code_b == code_a
    assert (
        built_b.spec_by_low_level_code[code_b].reroll_dice_mask
        == built_a.spec_by_low_level_code[code_a].reroll_dice_mask
    )

    # The current decision's label/payload pair is internally consistent ...
    assert built_b.label_by_low_level_code[code_b] == "reroll:1,2,3"
    assert _label_dice(built_b.label_by_low_level_code[code_b]) == sorted(
        int(value)
        for value in built_b.payload_by_low_level_code[code_b]["dice_to_reroll"]
    )
    # ... and the "current spec" map agrees, unlike the stale codebook copy.
    assert built_b.spec_by_low_level_code[code_b].label == "reroll:1,2,3"
    assert built_b.label_by_low_level_code[code_b] != built_a.label_by_low_level_code[code_a]


class _FixedPolicy:
    last_decider = "script"

    def __init__(self, code: int) -> None:
        self.code = int(code)

    def choose(self, built) -> int:  # noqa: ARG002 - the fixed code is the point
        return self.code


def test_decide_records_the_current_label_and_payload_for_a_shared_reroll_code():
    pool_a = (1, 2, 3, 4, 5, 6, 7, 8)
    pool_b = (8, 8, 1, 2, 3, 4, 5, 6)
    built_a = _built(pool_a)
    code = _code_for_dice(built_a, [3, 4, 5])
    built_b = _built(pool_b)
    assert _code_for_dice(built_b, [1, 2, 3]) == code

    player = PolicyPlayer(
        who=0,
        policy=_FixedPolicy(code),
        recorder=[],
        record_views=False,
    )
    player.notification = object()
    player._build = lambda request_type, request: built_b  # type: ignore[method-assign]

    _built_result, chosen = player._decide(DecisionType.REROLL_DICE, None)

    assert chosen == code
    record = player.recorder[-1]
    payload = _built_result.payload_by_low_level_code[code]
    assert record.chosen_label == _built_result.label_by_low_level_code[code]
    assert _label_dice(record.chosen_label) == sorted(
        int(value) for value in payload["dice_to_reroll"]
    )
    assert record.chosen_label == "reroll:1,2,3"
