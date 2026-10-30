"""Deck flex-card swap sensitivity."""

from __future__ import annotations

import pytest

from decklab.sensitivity import candidate_cards, evaluate_swaps, mutate_deck
from envs.decks import deck_spec


def test_mutate_deck_preserves_size_and_swaps():
    base = deck_spec("superconduct_aggro")
    counts = {}
    for card in base.cards:
        counts[card] = counts.get(card, 0) + 1
    remove = next(card for card, n in counts.items() if n >= 2)
    pool = candidate_cards(exclude=base.cards, limit=1)
    add = pool[0]
    mutated = mutate_deck("superconduct_aggro", remove, add)
    assert len(mutated.cards) == len(base.cards) == 30
    assert mutated.cards.count(remove) == counts[remove] - 1
    assert mutated.cards.count(add) == base.cards.count(add) + 1


def test_mutate_deck_rejects_missing_card():
    with pytest.raises(ValueError):
        mutate_deck("superconduct_aggro", 999999999, 332002)


def test_candidate_cards_excludes_base():
    base = deck_spec("superconduct_aggro")
    pool = candidate_cards(exclude=base.cards, limit=10)
    assert pool
    assert not (set(pool) & set(base.cards))


@pytest.mark.slow
def test_evaluate_swaps_smoke():
    base = deck_spec("superconduct_aggro")
    remove = base.cards[0]
    add = candidate_cards(exclude=base.cards, limit=1)[0]
    result = evaluate_swaps(
        "superconduct_aggro",
        opponents=["natlan_battleship"],
        seeds=(0, 1),
        swaps=[(remove, add)],
        workers=1,
    )
    assert result["base"]["games"] == 2
    assert len(result["swaps"]) == 1
    assert result["swaps"][0]["games"] == 2
    assert 0.0 <= result["swaps"][0]["ci_low"] <= result["swaps"][0]["ci_high"] <= 1.0
