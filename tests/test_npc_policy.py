"""``NpcPolicy`` behaviour + determinism (PLAN.md §4.4, PvE-B2).

Fast tests are engine-free: they drive ``choose`` with hand-built decision
contexts. One slow test plays a real match twice and asserts identical traces.
"""

from __future__ import annotations

import pytest

from reps.action_adapter import BuiltDecisionContext
from reps.schema import (
    CharacterSnapshot,
    DecisionContext,
    DecisionType,
    DeckSpec,
    LowLevelActionSpec,
    OptionKind,
    PlayerSnapshot,
    StateSnapshot,
)
from npc.policy import NpcPolicy, npc_policy_for_stage
from npc.spec import get_stage


def _player(characters: tuple[CharacterSnapshot, ...]) -> PlayerSnapshot:
    return PlayerSnapshot(
        active_character_id=characters[0].id if characters else None,
        characters=characters,
        combat_statuses=(),
        summons=(),
        supports=(),
        hand_cards=(),
        pile_cards=(),
        dice=(),
        declared_end=False,
        legend_used=False,
    )


def _character(index: int, *, active: bool) -> CharacterSnapshot:
    return CharacterSnapshot(
        id=100 + index,
        definition_id=200 + index,
        health=5,
        max_health=5,
        energy=0,
        max_energy=2,
        defeated=False,
        is_active=active,
    )


def _snapshot(active_slot: int = 0, characters: int = 3) -> StateSnapshot:
    chars = tuple(_character(i, active=(i == active_slot)) for i in range(characters))
    return StateSnapshot(
        phase="action",
        round_number=1,
        current_turn=1,
        winner=None,
        players=(_player(chars), _player(chars)),
    )


def _built(
    specs: list[LowLevelActionSpec],
    *,
    request_type: DecisionType = DecisionType.ACTION,
    active_slot: int = 0,
) -> BuiltDecisionContext:
    codes = tuple(index for index in range(len(specs)))
    materialized = tuple(
        LowLevelActionSpec(
            action_code=index,
            request_type=request_type,
            kind=spec.kind,
            label=spec.label,
            subject_definition_id=spec.subject_definition_id,
            target_slots=spec.target_slots,
            used_dice=spec.used_dice,
            choose_active_slot=spec.choose_active_slot,
            reroll_dice_mask=spec.reroll_dice_mask,
            switch_hand_slot_mask=spec.switch_hand_slot_mask,
            select_card_definition_id=spec.select_card_definition_id,
        )
        for index, spec in enumerate(specs)
    )
    snapshot = _snapshot(active_slot=active_slot)
    context = DecisionContext(
        acting_player=1,
        request_type=request_type,
        step_index=0,
        full_state=snapshot,
        player_view=snapshot,
        legal_low_level_codes=codes,
        legal_low_level_specs=materialized,
    )
    return BuiltDecisionContext(
        context=context,
        payload_by_low_level_code={},
        label_by_low_level_code={},
        low_to_high_code={},
    )


def _skill(skill_id: int) -> LowLevelActionSpec:
    return LowLevelActionSpec(
        action_code=-1, request_type=DecisionType.ACTION, kind=OptionKind.ACTION_USE_SKILL, label="use_skill", subject_definition_id=skill_id
    )


def _end() -> LowLevelActionSpec:
    return LowLevelActionSpec(
        action_code=-1, request_type=DecisionType.ACTION, kind=OptionKind.ACTION_DECLARE_END, label="declare_end"
    )


def _card(card_id: int) -> LowLevelActionSpec:
    return LowLevelActionSpec(
        action_code=-1, request_type=DecisionType.ACTION, kind=OptionKind.ACTION_PLAY_CARD, label="play_card", subject_definition_id=card_id
    )


def _switch() -> LowLevelActionSpec:
    return LowLevelActionSpec(
        action_code=-1, request_type=DecisionType.ACTION, kind=OptionKind.ACTION_SWITCH_ACTIVE, label="switch_active"
    )


def _policy(damage: dict[int, int], kinds: dict[int, str], **kwargs) -> NpcPolicy:
    return NpcPolicy(damage_by_skill_id=damage, skill_kind_by_skill_id=kinds, **kwargs)


def test_greedy_picks_max_damage():
    policy = _policy({1: 1, 2: 5, 3: 3}, {1: "A", 2: "E", 3: "Q"})
    built = _built([_skill(1), _skill(2), _skill(3)])
    assert built.context.legal_low_level_codes[policy.choose(built)] == 1


def test_greedy_tie_break_by_fixed_order():
    # Equal damage -> lower skill-definition id (A before E before Q).
    policy = _policy({33021: 3, 33022: 3, 33023: 3}, {})
    built = _built([_skill(33023), _skill(33021), _skill(33022)])
    assert built.context.legal_low_level_codes[policy.choose(built)] == 1  # 33021


def test_never_plays_action_card_or_switches():
    policy = _policy({}, {})
    built = _built([_card(999), _switch(), _end()])
    choice = built.context.legal_low_level_codes[policy.choose(built)]
    assert built.context.legal_low_level_specs[choice].kind == OptionKind.ACTION_DECLARE_END


def test_skills_beat_cards_even_for_zero_damage():
    policy = _policy({}, {})
    built = _built([_card(999), _skill(12345)])
    choice = built.context.legal_low_level_codes[policy.choose(built)]
    assert built.context.legal_low_level_specs[choice].kind == OptionKind.ACTION_USE_SKILL


def test_reroll_keeps_all_dice():
    policy = _policy({}, {})
    specs = [
        LowLevelActionSpec(
            action_code=-1,
            request_type=DecisionType.REROLL_DICE,
            kind=OptionKind.REROLL_DICE,
            label="reroll:5,6",
            reroll_dice_mask=0b11,
        ),
        LowLevelActionSpec(
            action_code=-1,
            request_type=DecisionType.REROLL_DICE,
            kind=OptionKind.REROLL_DICE,
            label="reroll:none",
            reroll_dice_mask=0,
        ),
    ]
    built = _built(specs, request_type=DecisionType.REROLL_DICE)
    choice = built.context.legal_low_level_codes[policy.choose(built)]
    assert built.context.legal_low_level_specs[choice].reroll_dice_mask == 0


def test_choose_active_prefers_first_slot():
    policy = _policy({}, {})
    specs = [
        LowLevelActionSpec(
            action_code=-1,
            request_type=DecisionType.CHOOSE_ACTIVE,
            kind=OptionKind.CHOOSE_ACTIVE,
            label="choose_active:2",
            choose_active_slot=2,
        ),
        LowLevelActionSpec(
            action_code=-1,
            request_type=DecisionType.CHOOSE_ACTIVE,
            kind=OptionKind.CHOOSE_ACTIVE,
            label="choose_active:0",
            choose_active_slot=0,
        ),
    ]
    built = _built(specs, request_type=DecisionType.CHOOSE_ACTIVE)
    choice = built.context.legal_low_level_codes[policy.choose(built)]
    assert built.context.legal_low_level_specs[choice].choose_active_slot == 0


def test_rotation_prefers_its_cycle_over_raw_damage():
    policy = _policy({1: 3, 2: 5}, {1: "E", 2: "Q"}, rotation=("E", "A", "Q"), rotation_name="cycle_e_a_q")
    built = _built([_skill(1), _skill(2)])
    choice = built.context.legal_low_level_codes[policy.choose(built)]
    assert built.context.legal_low_level_specs[choice].subject_definition_id == 1  # E preferred


def test_rotation_falls_back_to_greedy_when_preferred_absent():
    policy = _policy({2: 5}, {2: "Q"}, rotation=("E", "A"), rotation_name="cycle_e_a")
    built = _built([_skill(2)])
    choice = built.context.legal_low_level_codes[policy.choose(built)]
    assert built.context.legal_low_level_specs[choice].subject_definition_id == 2


def test_npc_policy_for_stage_damage_table():
    stage = get_stage("timaeus")
    policy = npc_policy_for_stage(stage)
    assert policy.damage_by_skill_id[33023] == 5  # Blazing Axe Q
    assert policy.skill_kind_by_skill_id[33022] == "E"
    assert policy.rotation is None  # greedy default


def test_npc_policy_rejects_unknown_rotation():
    with pytest.raises(KeyError):
        npc_policy_for_stage(get_stage("timaeus"), rotation="nope")


@pytest.mark.slow
def test_npc_policy_same_seed_reproducible():
    from envs.decks import deck_spec
    from envs.match import run_match
    from envs.policy import legal_random_policy
    from reps.schema import DeckSpec as _DeckSpec

    base = deck_spec("superconduct_aggro")
    stage = get_stage("timaeus")
    npc_deck = _DeckSpec(name="npc_timaeus", characters=stage.deck_characters, cards=base.cards)

    def play(rotation=None):
        return run_match(
            base,
            npc_deck,
            legal_random_policy(5),
            npc_policy_for_stage(stage, rotation=rotation),
            seed=9,
            record_decisions=True,
        )

    first, second = play(), play()
    trace = lambda record: [(d.player, d.chosen_kind, d.chosen_action_code) for d in record.decisions]
    assert first.winner == second.winner
    assert first.rounds == second.rounds
    assert trace(first) == trace(second)

    rotated = [play("cycle_a_e_q"), play("cycle_a_e_q")]
    assert trace(rotated[0]) == trace(rotated[1])
