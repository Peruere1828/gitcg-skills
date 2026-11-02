"""Cheap card / character facts read from the cached asset dump.

The scripted-expert ``AssetCatalog`` only keeps names, costs and tags, which is not
enough to (a) describe a deck to the loadout planner (weapon / artifact / event /
support counts) or (b) tell a burst skill from a normal attack in the per-decision
progress block. Both need the raw ``type`` field, so this module reads the same JSON
cache once and exposes a tiny, deterministic lookup.

No network: if the cache is missing every lookup degrades to ``"unknown"`` rather
than fetching the asset API. That keeps offline tests and CI safe.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from common.paths import REPO_ROOT

DEFAULT_ASSETS_PATH = REPO_ROOT / "data" / "assets" / "expert_system_assets_chs_latest.json"

ELEMENT_LABELS = {
    "GCG_TAG_ELEMENT_CRYO": "冰",
    "GCG_TAG_ELEMENT_HYDRO": "水",
    "GCG_TAG_ELEMENT_PYRO": "火",
    "GCG_TAG_ELEMENT_ELECTRO": "雷",
    "GCG_TAG_ELEMENT_ANEMO": "风",
    "GCG_TAG_ELEMENT_GEO": "岩",
    "GCG_TAG_ELEMENT_DENDRO": "草",
}

CARD_KINDS = ("weapon", "artifact", "event", "support", "other", "unknown")


def _assets_path() -> Path:
    override = os.environ.get("GITCG_ASSETS_CACHE")
    return Path(override) if override else DEFAULT_ASSETS_PATH


@lru_cache(maxsize=1)
def _index() -> dict[int, dict[str, Any]]:
    path = _assets_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    index: dict[int, dict[str, Any]] = {}
    for item in payload.get("data", ()):
        definition_id = item.get("id")
        if isinstance(definition_id, int):
            index[definition_id] = item
    return index


def card_kind(definition_id: int) -> str:
    """``weapon | artifact | event | support | other | unknown`` for one card.

    ``weapon`` uses the same definition as the course data audit:
    ``type == GCG_CARD_MODIFY`` and the ``GCG_TAG_WEAPON`` tag. Talents are
    ``GCG_CARD_MODIFY`` without either equipment tag, so they land in ``other``
    (the engine calls them ``talent``); that is deliberately *not* guessed.
    """
    item = _index().get(int(definition_id))
    if item is None:
        return "unknown"
    kind = item.get("type")
    tags = set(item.get("tags") or ())
    if kind == "GCG_CARD_MODIFY" and "GCG_TAG_WEAPON" in tags:
        return "weapon"
    if kind == "GCG_CARD_MODIFY" and "GCG_TAG_ARTIFACT" in tags:
        return "artifact"
    if kind == "GCG_CARD_EVENT":
        return "event"
    if kind == "GCG_CARD_ASSIST":
        return "support"
    return "other"


def skill_kind(definition_id: int) -> str | None:
    """``normal | elemental | burst | passive`` for a skill definition id."""
    item = _index().get(int(definition_id))
    if item is None:
        return None
    return {
        "GCG_SKILL_TAG_A": "normal",
        "GCG_SKILL_TAG_E": "elemental",
        "GCG_SKILL_TAG_Q": "burst",
        "GCG_SKILL_TAG_PASSIVE": "passive",
    }.get(str(item.get("type") or ""))


def character_name(definition_id: int) -> str:
    item = _index().get(int(definition_id))
    if item is None:
        return f"#{definition_id}"
    return str(item.get("name") or item.get("englishName") or f"#{definition_id}")


def character_element(definition_id: int) -> str:
    item = _index().get(int(definition_id))
    if item is None:
        return "?"
    for tag in item.get("tags") or ():
        label = ELEMENT_LABELS.get(str(tag))
        if label:
            return label
    return "?"


def deck_facts(deck: Any) -> dict[str, Any]:
    """Facts the loadout prompt shows about the fixed stage deck.

    ``deck`` is a ``reps.schema.DeckSpec`` (duck-typed: ``characters`` / ``cards``).
    Counts always add up to ``len(deck.cards)``; a missing asset cache only turns
    individual labels into ``unknown``.
    """
    characters: list[str] = []
    elements: dict[str, int] = {}
    for definition_id in deck.characters:
        element = character_element(int(definition_id))
        characters.append(f"{character_name(int(definition_id))}({element})")
        elements[element] = elements.get(element, 0) + 1

    counts = {kind: 0 for kind in CARD_KINDS}
    for definition_id in deck.cards:
        counts[card_kind(int(definition_id))] += 1
    return {
        "name": str(getattr(deck, "name", "")),
        "characters": tuple(characters),
        "elements": dict(sorted(elements.items())),
        "cards": counts,
    }
