"""Rule-based pool deck construction (``decklab.builder``, PLAN.md D19).

Fast, engine-free: the builder is a pure function of the pool manifest + inputs.
Requires the scripted-expert asset cache (card-kind tags), like the rest of the
suite.
"""

from __future__ import annotations

from collections import Counter

import pytest

from decklab.builder import (
    MAX_COPIES,
    POOL_C_FILL,
    POOL_C_META_CORE,
    POOL_C_SUPERCONDUCT_CHARACTERS,
    CardPool,
    build_deck,
    build_pool_c_superconduct,
    card_kind,
    deck_summary,
    load_pool,
)


def test_pool_c_superconduct_deck_is_legal():
    pool = load_pool()
    deck = build_pool_c_superconduct()
    assert len(deck.cards) == 30
    assert deck.characters == POOL_C_SUPERCONDUCT_CHARACTERS
    assert len(set(deck.characters)) == 3
    for character_id in deck.characters:
        assert pool.has_character(character_id)
    counts = Counter(deck.cards)
    assert max(counts.values()) <= MAX_COPIES
    assert all(pool.has_card(card_id) for card_id in deck.cards)


def test_pool_c_deck_covers_objective_kinds():
    deck = build_pool_c_superconduct()
    kinds = Counter(card_kind(card_id) for card_id in deck.cards)
    # A weapon and an artifact must be present so play_equipment_card(weapon) and
    # play_equipment_card(artifact) objectives stay reachable.
    assert kinds["weapon"] >= 1
    assert kinds["artifact"] >= 1
    assert kinds["event"] >= 1


def test_build_deck_is_deterministic():
    assert build_pool_c_superconduct() == build_pool_c_superconduct()
    assert tuple(build_pool_c_superconduct().cards) == tuple(build_pool_c_superconduct().cards)


def test_build_deck_rejects_out_of_pool_card():
    pool = load_pool()
    with pytest.raises(ValueError):
        build_deck(pool, POOL_C_SUPERCONDUCT_CHARACTERS, name="x", core=(999999,))


def test_build_deck_rejects_out_of_pool_character():
    pool = load_pool()
    with pytest.raises(ValueError):
        build_deck(pool, (1, 2, 3), name="x", core=())


def test_build_deck_rejects_wrong_character_count():
    pool = load_pool()
    with pytest.raises(ValueError):
        build_deck(pool, (1101, 1403), name="x", core=())


def test_deck_summary_counts_kinds():
    deck = build_pool_c_superconduct()
    summary = deck_summary(deck)
    assert summary["n_cards"] == 30
    assert sum(summary["card_kinds"].values()) == 30
    assert summary["characters"] == list(deck.characters)


def test_core_and_fill_fit_within_deck_size():
    assert len(POOL_C_META_CORE) + len(POOL_C_FILL) <= 30


def test_top_up_fills_deck_with_multiple_copies():
    # A pool with fewer distinct cards than the deck size forces the top-up to
    # add up to ``copies`` of each card (a single pass used to add only one).
    pool = CardPool(name="tiny", characters=(1101, 1403, 1405), cards=tuple(range(100, 110)))
    deck = build_deck(pool, (1101, 1403, 1405), name="tiny", size=20, copies=2)
    assert len(deck.cards) == 20
    assert Counter(deck.cards) == Counter({card_id: 2 for card_id in range(100, 110)})


def test_top_up_raises_when_pool_is_too_small():
    pool = CardPool(name="tiny", characters=(1101, 1403, 1405), cards=(100, 101))
    with pytest.raises(ValueError):
        build_deck(pool, (1101, 1403, 1405), name="tiny", size=30, copies=2)
