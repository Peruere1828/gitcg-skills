"""Deck plumbing: expert deck profiles -> DeckSpec / gitcg.Deck.

The 20 scripted expert decks (``agents/scripted``) double as the fixed opponent
pool (PLAN.md WS2) and as a sanity baseline deck pool.
"""

from __future__ import annotations

from functools import lru_cache

from gitcg import Deck as GitcgDeck

from reps.schema import DeckSpec


@lru_cache(maxsize=1)
def expert_deck_specs() -> tuple[DeckSpec, ...]:
    """Deck specs for every ported scripted expert (ordered by registry)."""
    from agents.scripted.registry import RAW_DECKS
    from agents.scripted.profiles import load_deck_profiles_by_slug

    profiles = load_deck_profiles_by_slug()
    specs: list[DeckSpec] = []
    for entry in RAW_DECKS:
        profile = profiles[entry.slug]
        specs.append(
            DeckSpec(
                name=entry.slug,
                characters=tuple(int(c) for c in profile.characters),
                cards=tuple(int(c) for c in profile.cards),
            )
        )
    return tuple(specs)


@lru_cache(maxsize=1)
def expert_decks_by_name() -> dict[str, DeckSpec]:
    return {spec.name: spec for spec in expert_deck_specs()}


def deck_spec(name: str) -> DeckSpec:
    decks = expert_decks_by_name()
    if name not in decks:
        raise KeyError(f"unknown deck: {name!r}; known: {sorted(decks)}")
    return decks[name]


def to_gitcg_deck(spec: DeckSpec) -> GitcgDeck:
    return GitcgDeck(characters=list(spec.characters), cards=list(spec.cards))
