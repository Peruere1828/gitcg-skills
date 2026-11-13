"""Era-deck resolution shared by the runner, the parked scheduler and the reports.

The 20 ported expert decks live in ``envs.decks`` (``agents/scripted`` registry);
the v3.3-era share-code decks live in ``configs/era_decks.json`` and have no rule
module, so their policy is a synthetic ``ExpertRuleAgent`` profile built from the
deck entry. Keeping both lookups here means every consumer resolves a deck name
the same way and an era deck can be played wherever an expert deck can.
"""

from __future__ import annotations

import json
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from common.paths import REPO_ROOT
from envs.decks import deck_spec
from reps.schema import DeckSpec

DEFAULT_ERA_DECKS = "configs/era_decks.json"
ERA_VERSION = "v3.3.0"


def _config_path(path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else REPO_ROOT / candidate


@lru_cache(maxsize=4)
def _load_era_decks_cached(path: str) -> dict[str, dict[str, Any]]:
    payload = json.loads(_config_path(path).read_text(encoding="utf-8"))
    decks = payload.get("decks") if isinstance(payload, dict) else None
    if not isinstance(decks, dict):
        return {}
    return {
        str(name): dict(entry)
        for name, entry in decks.items()
        if isinstance(entry, dict)
    }


def load_era_decks(path: str | Path = DEFAULT_ERA_DECKS) -> dict[str, dict[str, Any]]:
    """Load the configured era decks (``{"decks": {name: entry}}``), cached."""
    return _load_era_decks_cached(str(path))


def is_era(name: str) -> bool:
    """True when ``name`` is defined in ``configs/era_decks.json``."""
    return name in load_era_decks()


def deck_spec_from_deck(deck: Mapping[str, Any], *, name: str | None = None) -> DeckSpec:
    """Build a ``DeckSpec`` from a config deck entry (share-code or fallback)."""
    return DeckSpec(
        name=str(name or deck.get("name") or ""),
        characters=tuple(int(character) for character in deck["characters"]),
        cards=tuple(int(card) for card in deck["cards"]),
    )


def resolve_deck(name: str) -> DeckSpec:
    """Resolve a deck name from the era pool first, then the expert registry."""
    deck = load_era_decks().get(name)
    if deck is not None:
        return deck_spec_from_deck(deck, name=name)
    return deck_spec(name)


def era_policy(
    deck: Mapping[str, Any],
    *,
    seed: int = 0,
    rule_overrides: Mapping[str, Any] | None = None,
):
    """ExpertRuleAgent with a synthetic profile (era decks have no rule module)."""
    from agents.scripted.agent import ExpertRuleAgent
    from agents.scripted.assets import load_assets
    from agents.scripted.models import DeckProfile, DeckRuleConfig
    from envs.policy import ScriptedPolicy

    assets = load_assets()
    characters = tuple(int(character) for character in deck["characters"])
    names = tuple(str(name) for name in deck["character_names"])
    elements = tuple(str(element) for element in deck["character_elements"])

    def _kind(card_id: int) -> str:
        card = assets.cards.get(int(card_id))
        tags = set(card.tags) if card is not None else set()
        if "GCG_TAG_TALENT" in tags:
            return "talent"
        if "GCG_TAG_WEAPON" in tags:
            return "weapon"
        if "GCG_TAG_ARTIFACT" in tags:
            return "artifact"
        if {"GCG_TAG_ALLY", "GCG_TAG_PLACE", "GCG_TAG_ITEM"} & tags:
            return "support"
        return "event"

    talents: list[int] = []
    supports: list[int] = []
    equipment: list[int] = []
    events: list[int] = []
    for card_id in deck["cards"]:
        kind = _kind(card_id)
        if kind == "talent":
            talents.append(int(card_id))
        elif kind == "support":
            supports.append(int(card_id))
        elif kind in {"weapon", "artifact"}:
            equipment.append(int(card_id))
        else:
            events.append(int(card_id))
    rule_config = DeckRuleConfig(
        slug=str(deck["name"]),
        opener=names[0],
        carry=names[:2],
        bench=names[2:],
        preferred_elements=elements,
        style="era",
    )
    if rule_overrides:
        rule_config = replace(rule_config, **dict(rule_overrides))
    profile = DeckProfile(
        slug=str(deck["name"]),
        name=str(deck["name"]),
        author="scripts/era_decks.py",
        tags=("era", ERA_VERSION),
        share_code="",
        characters=characters,
        cards=tuple(int(card_id) for card_id in deck["cards"]),
        character_names=names,
        character_elements=elements,
        rule_config=rule_config,
        talent_cards=tuple(talents),
        support_cards=tuple(supports),
        equipment_cards=tuple(equipment + talents),
        event_cards=tuple(events),
    )
    agent = ExpertRuleAgent(assets=assets, profile=profile, seed=seed)
    return ScriptedPolicy(agent, name=f"expert:{deck['name']}")


def resolve_base_policy(
    name: str,
    seed: int = 0,
    *,
    rule_overrides: Mapping[str, Any] | None = None,
):
    """Base deterministic policy for a deck name (era synthetic or expert rule).

    ``rule_overrides`` tunes the synthetic era profile; non-era expert decks have
    no such knobs and ignore it.
    """
    if is_era(name):
        return era_policy(
            load_era_decks()[name], seed=seed, rule_overrides=rule_overrides
        )
    from envs.policy import expert_policy

    return expert_policy(name, seed=seed)
