"""M1 acceptance (part 1): every legal concrete action is representable and is
covered by the abstract action hierarchy.

The action adapter must expose a stable code + payload for 100% of the legal
concrete actions the engine offers, with kind-appropriate payload fields, and the
hierarchy must partition all legal actions (PLAN.md §6.2, go/no-go gate).
"""

from __future__ import annotations

import pytest

from envs.decks import deck_spec
from envs.match import run_match
from envs.policy import expert_policy

_EXPECTED_PAYLOAD_KEYS = {
    "action": "chosen_action_index",
    "choose_active": "active_character_id",
    "reroll_dice": "dice_to_reroll",
    "select_card": "selected_definition_id",
    "switch_hands": "removed_hand_ids",
}

# Guardrail against action-space blow-up (PLAN.md §7). Report, don't silently pass.
_MAX_LEGAL_OPTIONS = 512


class _Capture:
    def __init__(self, inner):
        self.inner = inner
        self.name = f"capture:{getattr(inner, 'name', '?')}"
        self.contexts = []

    def choose(self, built):
        self.contexts.append(built)
        return self.inner.choose(built)


@pytest.fixture(scope="module")
def captured_game():
    cap = _Capture(expert_policy("superconduct_aggro"))
    opp = expert_policy("natlan_battleship")
    record = run_match(
        deck_spec("superconduct_aggro"), deck_spec("natlan_battleship"), cap, opp, seed=3
    )
    return record, cap


def test_game_reaches_completion(captured_game):
    record, cap = captured_game
    assert record.error is None
    assert cap.contexts, "no decision contexts captured"
    assert record.fallbacks0 == 0


def test_every_legal_action_has_a_code_and_payload(captured_game):
    record, cap = captured_game
    max_options = 0
    for built in cap.contexts:
        context = built.context
        codes = list(context.legal_low_level_codes)
        assert codes, "a decision must offer at least one legal action"
        assert len(codes) == len(set(codes)), "action codes must be unique"
        assert len(context.legal_low_level_specs) == len(codes)
        max_options = max(max_options, len(codes))

        for code, spec in zip(codes, context.legal_low_level_specs):
            payload = built.payload_by_low_level_code.get(int(code))
            assert payload is not None, f"missing payload for legal code {code}"
            expected_key = _EXPECTED_PAYLOAD_KEYS[context.request_type.value]
            assert expected_key in payload, (
                f"payload for {spec.kind} lacks key {expected_key!r}: {payload}"
            )

    assert max_options <= _MAX_LEGAL_OPTIONS, (
        f"action space too wide: max {max_options} options at one decision"
    )


def test_abstract_actions_cover_every_legal_concrete_action(captured_game):
    """M1 gate: the hierarchy partitions 100% of legal concrete actions."""
    from reps.action_hierarchy import (
        high_action_vocab_size,
        legal_high_to_low_dict,
        match_low_level_code_index,
    )

    _record, cap = captured_game
    vocab = high_action_vocab_size()
    for built in cap.contexts:
        context = built.context
        codes = set(int(c) for c in context.legal_low_level_codes)
        assert codes

        high_to_low = legal_high_to_low_dict(context)
        mapped_lows = {low for lows in high_to_low.values() for low in lows}
        assert codes == mapped_lows, (
            f"abstract actions do not cover all legal actions: "
            f"missing={sorted(codes - mapped_lows)} extra={sorted(mapped_lows - codes)}"
        )
        assert set(high_to_low) == set(int(h) for h in context.legal_high_level_codes)
        assert all(0 <= high < vocab for high in high_to_low)

        # Round-trip: every legal code is recoverable from its semantic key.
        for code in context.legal_low_level_codes:
            assert match_low_level_code_index(context, int(code)) is not None
