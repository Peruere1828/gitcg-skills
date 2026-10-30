"""Observation encoder factory (PLAN.md WS1).

Builds the tokenized observation encoder with a vocabulary that covers every card
and character appearing in the scripted expert deck pool. Inputs (tokens / option
features) derive from ``context.player_view`` (masked); training targets derive
from ``context.full_state`` (god view). See ``tests/test_info_leak.py``.
"""

from __future__ import annotations

from functools import lru_cache

from reps.observation_encoder import TokenObservationEncoder, TokenObservationEncoderConfig


@lru_cache(maxsize=1)
def expert_vocabulary() -> tuple[tuple[int, ...], tuple[int, ...]]:
    """(character_vocabulary, card_vocabulary) over all scripted expert decks."""
    from envs.decks import expert_deck_specs

    specs = expert_deck_specs()
    characters = sorted({int(c) for spec in specs for c in spec.characters})
    cards = sorted({int(c) for spec in specs for c in spec.cards})
    return tuple(characters), tuple(cards)


@lru_cache(maxsize=1)
def default_encoder() -> TokenObservationEncoder:
    characters, cards = expert_vocabulary()
    return TokenObservationEncoder(
        TokenObservationEncoderConfig(
            card_vocabulary=cards,
            character_vocabulary=characters,
        )
    )
