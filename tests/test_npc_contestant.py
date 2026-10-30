"""Contestant policy for the PvE-v1 gate (``npc.contestant``).

Fast tests drive ``choose`` with hand-built decision contexts (objective
injection + delegation). One slow test plays a real match twice and asserts the
traces match (determinism).
"""

from __future__ import annotations

import pytest

from decklab.builder import build_pool_c_superconduct
from reps.action_adapter import BuiltDecisionContext
from reps.schema import (
    CharacterSnapshot,
    DecisionContext,
    DecisionType,
    LowLevelActionSpec,
    OptionKind,
    PlayerSnapshot,
    StateSnapshot,
)
from npc.contestant import build_contestant_policy, build_contestant_profile
from npc.spec import get_stage

WEAPON = 311501  # 旅行剑 (sword)
ARTIFACT = 312001  # 冒险家头带
EVENT = 332001  # 最好的伙伴！


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
    definition_id = (1101, 1403, 1405)[index]
    return CharacterSnapshot(
        id=100 + index,
        definition_id=definition_id,
        health=5,
        max_health=5,
        energy=0,
        max_energy=2,
        defeated=False,
        is_active=active,
    )


def _snapshot() -> StateSnapshot:
    chars = tuple(_character(i, active=(i == 0)) for i in range(3))
    return StateSnapshot(
        phase="action",
        round_number=1,
        current_turn=1,
        winner=None,
        players=(_player(chars), _player(chars)),
    )


def _built(specs: list[LowLevelActionSpec]) -> BuiltDecisionContext:
    codes = tuple(range(len(specs)))
    materialized = tuple(
        LowLevelActionSpec(
            action_code=index,
            request_type=DecisionType.ACTION,
            kind=spec.kind,
            label=spec.label,
            subject_definition_id=spec.subject_definition_id,
            used_dice=spec.used_dice,
        )
        for index, spec in enumerate(specs)
    )
    snapshot = _snapshot()
    context = DecisionContext(
        acting_player=0,
        request_type=DecisionType.ACTION,
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
        action_code=-1,
        request_type=DecisionType.ACTION,
        kind=OptionKind.ACTION_USE_SKILL,
        label="use_skill",
        subject_definition_id=skill_id,
    )


def _card(card_id: int) -> LowLevelActionSpec:
    return LowLevelActionSpec(
        action_code=-1,
        request_type=DecisionType.ACTION,
        kind=OptionKind.ACTION_PLAY_CARD,
        label="play_card",
        subject_definition_id=card_id,
    )


def _end() -> LowLevelActionSpec:
    return LowLevelActionSpec(
        action_code=-1,
        request_type=DecisionType.ACTION,
        kind=OptionKind.ACTION_DECLARE_END,
        label="declare_end",
    )


def test_profile_slug_has_no_deck_specific_rules():
    from agents.scripted.deck_rules import ACTION_RULES

    profile = build_contestant_profile(build_pool_c_superconduct())
    assert profile.slug not in ACTION_RULES
    assert len(profile.character_names) == 3


def test_injection_plays_required_weapon_card():
    deck = build_pool_c_superconduct()
    stage = get_stage("wagner")  # needs a weapon
    policy = build_contestant_policy(deck, stage)
    built = _built([_skill(14031), _card(WEAPON), _end()])
    choice = policy.choose(built)
    assert built.context.legal_low_level_specs[choice].kind == OptionKind.ACTION_PLAY_CARD
    assert built.context.legal_low_level_specs[choice].subject_definition_id == WEAPON
    assert policy._equipment_by_kind.get("weapon") == 1


def test_injection_plays_required_event_cards():
    deck = build_pool_c_superconduct()
    stage = get_stage("uncletian")  # needs 2 event cards
    policy = build_contestant_policy(deck, stage)
    built = _built([_skill(14031), _card(EVENT), _end()])
    choice = policy.choose(built)
    assert built.context.legal_low_level_specs[choice].kind == OptionKind.ACTION_PLAY_CARD
    assert policy._events_played == 1


def test_injection_ignored_when_card_not_available():
    deck = build_pool_c_superconduct()
    stage = get_stage("wagner")  # needs a weapon
    policy = build_contestant_policy(deck, stage)
    built = _built([_skill(14031), _card(ARTIFACT), _end()])
    choice = policy.choose(built)
    # no weapon legal -> delegate to the generic expert, still a legal code
    assert choice in set(built.context.legal_low_level_codes)


def test_plain_policy_does_not_inject():
    deck = build_pool_c_superconduct()
    stage = get_stage("wagner")
    policy = build_contestant_policy(deck, stage, inject_objectives=False)
    built = _built([_skill(14031), _card(WEAPON), _end()])
    raw = int(policy.base.choose(built))
    assert policy.choose(built) == raw


def test_injection_uses_required_burst():
    deck = build_pool_c_superconduct()
    stage = get_stage("blanche")  # needs 1 elemental burst
    policy = build_contestant_policy(deck, stage)
    built = _built([_skill(14031), _skill(14033), _end()])  # 14033 = 刻晴 burst
    choice = policy.choose(built)
    assert built.context.legal_low_level_specs[choice].subject_definition_id == 14033
    assert policy._bursts_used == 1


@pytest.mark.slow
def test_contestant_policy_same_seed_reproducible():
    from envs.match import run_match
    from reps.schema import DeckSpec

    deck = build_pool_c_superconduct()
    stage = get_stage("timaeus")
    npc_deck = DeckSpec(name="npc_timaeus", characters=stage.deck_characters, cards=deck.cards)

    def play():
        from npc.policy import npc_policy_for_stage

        return run_match(
            deck,
            npc_deck,
            build_contestant_policy(deck, stage),
            npc_policy_for_stage(stage),
            seed=3,
            record_decisions=True,
        )

    first, second = play(), play()
    trace = lambda record: [(d.player, d.chosen_kind, d.chosen_action_code) for d in record.decisions]
    assert first.winner == second.winner
    assert first.rounds == second.rounds
    assert trace(first) == trace(second)
