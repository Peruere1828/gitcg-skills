"""WS1 hard gate: observation encoder must not leak opponent hidden information.

Two decision contexts that differ *only* in opponent hidden information (hand
contents, pile contents, dice) must produce identical encoder **inputs** (tokens,
option features, masks). Training-target fields (belief targets / privileged
state) are allowed to differ -- they are labels, not inputs.

A companion check ensures we do not over-hide: altering a *public* feature
(opponent active character health) must change the tokens.
"""

from __future__ import annotations

import dataclasses

import pytest

from envs.decks import deck_spec
from envs.match import run_match
from envs.observation import default_encoder
from envs.policy import expert_policy
from reps.schema import EntitySnapshot, PlayerSnapshot, StateSnapshot


class _Capture:
    def __init__(self, inner):
        self.inner = inner
        self.name = "capture"
        self.contexts = []

    def choose(self, built):
        self.contexts.append(built)
        return self.inner.choose(built)


def _mid_context():
    cap = _Capture(expert_policy("superconduct_aggro"))
    record = run_match(
        deck_spec("superconduct_aggro"),
        deck_spec("natlan_battleship"),
        cap,
        expert_policy("natlan_battleship"),
        seed=3,
        full_state_for_agents=True,
    )
    assert record.error is None
    action_contexts = [
        b.context for b in cap.contexts if b.context.request_type.value == "action"
    ]
    assert action_contexts
    return action_contexts[len(action_contexts) // 2]


_INPUT_FIELDS = (
    "token_features",
    "token_mask",
    "opponent_token_mask",
    "option_features",
    "option_mask",
    "low_level_action_codes",
    "low_to_high_codes",
    "high_level_mask",
    "opponent_tag_id",
    "opponent_entity_id",
    "opponent_deck_id",
)


def _hidden_entities(count: int, defs: tuple[int, ...]) -> tuple[EntitySnapshot, ...]:
    return tuple(
        EntitySnapshot(id=10_000 + i, definition_id=int(defs[i % len(defs)]), variables={})
        for i in range(count)
    )


def _scramble_hidden(
    state: StateSnapshot, *, opponent: int, defs: tuple[int, ...]
) -> StateSnapshot:
    players = list(state.players)
    players[opponent] = dataclasses.replace(
        players[opponent],
        hand_cards=_hidden_entities(len(players[opponent].hand_cards), defs),
        pile_cards=_hidden_entities(len(players[opponent].pile_cards), defs),
        dice=(7, 7, 7),
    )
    return dataclasses.replace(state, players=(players[0], players[1]))


def _bump_public_health(state: StateSnapshot, *, opponent: int) -> StateSnapshot:
    players = list(state.players)
    characters = list(players[opponent].characters)
    for index, character in enumerate(characters):
        if not character.defeated and character.health > 0:
            characters[index] = dataclasses.replace(
                character, health=max(0, character.health - 1)
            )
            break
    else:
        return state
    players[opponent] = dataclasses.replace(
        players[opponent], characters=tuple(characters)
    )
    return dataclasses.replace(state, players=(players[0], players[1]))


@pytest.mark.slow
def test_encoder_inputs_ignore_opponent_hidden_info():
    context = _mid_context()
    opponent = 1 - context.acting_player
    encoder = default_encoder()
    defs = encoder.config.card_vocabulary

    baseline = encoder.encode_context(context, history=())
    scrambled = encoder.encode_context(
        dataclasses.replace(
            context,
            full_state=_scramble_hidden(
                context.full_state, opponent=opponent, defs=defs
            ),
        ),
        history=(),
    )

    for field in _INPUT_FIELDS:
        assert getattr(baseline, field) == getattr(scrambled, field), (
            f"encoder input {field!r} changed when only opponent hidden info changed"
        )


@pytest.mark.slow
def test_encoder_tokens_react_to_public_changes():
    context = _mid_context()
    opponent = 1 - context.acting_player
    encoder = default_encoder()

    baseline = encoder.encode_context(context, history=())
    changed = encoder.encode_context(
        dataclasses.replace(
            context,
            player_view=_bump_public_health(context.player_view, opponent=opponent),
        ),
        history=(),
    )
    assert baseline.token_features != changed.token_features, (
        "public opponent health change must be observable"
    )


@pytest.mark.slow
def test_belief_targets_use_full_state_not_player_view():
    context = _mid_context()
    opponent = 1 - context.acting_player
    encoder = default_encoder()
    defs = encoder.config.card_vocabulary
    baseline = encoder.encode_context(context, history=())
    scrambled = encoder.encode_context(
        dataclasses.replace(
            context,
            full_state=_scramble_hidden(
                context.full_state, opponent=opponent, defs=defs
            ),
        ),
        history=(),
    )
    # Labels *should* reflect the god view, i.e. they change. If they don't, the
    # training targets are being computed from the masked view (silent quality loss).
    assert (
        baseline.belief_target.hand_histogram
        != scrambled.belief_target.hand_histogram
    )
