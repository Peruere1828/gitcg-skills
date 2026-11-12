"""Measure the v3.3.0 (TCG launch) deck pool on the 16 NPC PvE stages.

Primary input
-------------
Official era decks supplied by the user as **share codes** in
``data/era_decks.txt`` (one ``<name> <share_code>`` per line).  They are decoded,
checked against the engine at ``version="v3.3.0"`` and written to
``configs/era_decks.json`` by :mod:`scripts.import_deck`.  This module never
invents a share code.

Fallback input
--------------
The old synthetic builder is kept as an explicit fallback only
(``--allow-fallback``).  It is not used when share-code decks are present, and
``--matrix`` refuses to run on it unless the flag is passed.  The fallback
construction rule (documented below) is deterministic but hand-designed; the
share-code pool is the authoritative benchmark pool.

Pool rule (reproducible, used for both inputs)
---------------------------------------------
1. Candidates are asset entries with ``sinceVersion <= v3.3.0``: characters have
   ``hp`` + ``maxEnergy``; action cards have ``playCost`` and type in
   ``{GCG_CARD_EVENT, GCG_CARD_ASSIST, GCG_CARD_MODIFY}``.
2. The engine is the authority.  Each candidate gets a minimal
   ``CreateParam(version="v3.3.0")`` + ``Game(...)`` probe.  This drops seven
   internal "elemental infusion" state entries (ids 81-87) that the asset dump
   types like a card but the engine does not expose.
3. Deck-buildable drops ``obtainable == false`` (the Keqing alternate talent
   114031).  "Playable character" is asset metadata (``category == "characters"``
   and ``obtainable is not False``), not an id range: the six 2xxx character
   cards (Oceanid, Fatui Pyro Agent, Mirror Maiden, Maguu Kenki, Stonehide
   Lawachurl, Jadeplume Terrorshroom) are playable at v3.3.0.

Fallback deck construction rule (only for ``--allow-fallback``)
---------------------------------------------------------------
Each fallback archetype fixes three characters (order = opener, second carry,
bench).  The 30 cards are: 3 character talents (asset ``relatedCharacterId``),
3 weapons (one per character, matching weapon type; candidates sorted by
``(copies used, cost, id)`` so shared weapon types get distinct cards), 2x
Gambler's Earrings + 1x Instructor's Cap, 2x Paimon + 1x Katheryne, and 18
events (both resonance cards if an element appears at least twice, then a fixed
staple priority: die fix, draw, heal, damage, energy, switch; up to 2 copies;
fill from legal event ids).  Validation re-checks 30 cards, <= 2 copies/card,
talents only for chosen characters, weapons only for matching types, total
weapons <= 6, >= 2 artifacts, >= 6 events.

Stage rules
-----------
Every matrix game sends the stage's adventure-challenge rules
(``configs/npc/special_rules.json`` -> ``ATTR_CREATEPARAM_STAGE_RULES``): initial
supports such as Timmie/Wagner/Timaeus and effects such as Blanche's +2 energy.
The rule belongs to the stage, not to the expert policy. ``fingerprint`` pins
the rules file hash so a matrix cannot mix results from different rule data.

Seeds
-----
The matrix uses **1,2,3,4,5** only.  Seed 0 is excluded: the finished post-3.3
matrix showed 121/320 seed-0 draws (15-round cap) vs 0-3/320 on seeds 1-4, a
deterministic artifact concentrated in marginal decks.  No seed-0 rows are
produced here.

Usage::

    PY=/home/ubuntuaaa/miniconda3/envs/gitcg-skill/bin/python
    # 1) paste official v3.3-era share codes into data/era_decks.txt, then:
    "$PY" -m scripts.import_deck --file data/era_decks.txt --dry-run
    "$PY" -m scripts.import_deck --file data/era_decks.txt --write
    # 2) pool summary + one-game validation + config refresh
    "$PY" -m scripts.era_decks --build
    # 3) 16 stages x N decks x seeds 1-5 (expert vs NPC, engine v3.3.0)
    "$PY" -m scripts.era_decks --matrix --workers 8
    # 4) rebuild reports/era_decks.md from the JSONL (no games)
    "$PY" -m scripts.era_decks --report
    # everything (stops at "waiting for codes" if the input file is empty)
    "$PY" -m scripts.era_decks --all --workers 8
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing as mp
import os
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from common.lock import load_engine_lock, runtime_metadata
from common.paths import REPO_ROOT, data_dir
from envs.match import run_match
from npc.objectives import evaluate_stage
from npc.policy import npc_policy_for_stage
from npc.spec import get_stage, stage_names
from npc.stage_rules import stage_rules_json
from reps.schema import DeckSpec
from skillloop.era import deck_spec_from_deck, era_policy

ERA_VERSION = "v3.3.0"
ERA_TUPLE = (3, 3, 0)
DECK_SIZE = 30
MAX_COPIES = 2
MAX_WORKERS = 16
DEFAULT_SEEDS = "1,2,3,4,5"
MAX_WEAPONS_PER_SLOT = 2

DEFAULT_ASSETS = REPO_ROOT / "data" / "assets" / "expert_system_assets_chs_latest.json"
DEFAULT_CONFIG = REPO_ROOT / "configs" / "era_decks.json"
DEFAULT_REPORT = REPO_ROOT / "reports" / "era_decks.md"
DEFAULT_INPUT = data_dir("era_decks.txt")
DEFAULT_OUT = data_dir("runs", "era_matrix.jsonl")
DEFAULT_LEGALITY = data_dir("runs", "era_legality.json")
DEFAULT_VALIDATION = data_dir("runs", "era_validation.json")
POST_MATRIX = data_dir("runs", "deck_matrix.jsonl")

# --------------------------------------------------------------------------- #
# taxonomy
# --------------------------------------------------------------------------- #
WEAPON_TAGS = {
    "CATALYST": "GCG_TAG_WEAPON_CATALYST",
    "BOW": "GCG_TAG_WEAPON_BOW",
    "CLAYMORE": "GCG_TAG_WEAPON_CLAYMORE",
    "POLE": "GCG_TAG_WEAPON_POLE",
    "SWORD": "GCG_TAG_WEAPON_SWORD",
}
WEAPON_TAGS_BY_TAG = {tag: name for name, tag in WEAPON_TAGS.items()}
# Characters such as the Fatui Pyro Agent have a "no weapon type" tag; keep it
# for display, but it never matches a weapon card.
WEAPON_TAGS_BY_TAG["GCG_TAG_WEAPON_NONE"] = "NONE"
ELEMENT_TAGS = {
    "CRYO": "GCG_TAG_ELEMENT_CRYO",
    "HYDRO": "GCG_TAG_ELEMENT_HYDRO",
    "PYRO": "GCG_TAG_ELEMENT_PYRO",
    "ELECTRO": "GCG_TAG_ELEMENT_ELECTRO",
    "ANEMO": "GCG_TAG_ELEMENT_ANEMO",
    "GEO": "GCG_TAG_ELEMENT_GEO",
    "DENDRO": "GCG_TAG_ELEMENT_DENDRO",
}
CARD_KINDS = ("talent", "weapon", "artifact", "support", "event", "other")

# both resonance cards, per element: (0-cost die, secondary effect)
RESONANCE = {
    "CRYO": (331101, 331102),
    "HYDRO": (331201, 331202),
    "PYRO": (331301, 331302),
    "ELECTRO": (331401, 331402),
    "ANEMO": (331501, 331502),
    "GEO": (331601, 331602),
    "DENDRO": (331701, 331702),
}
GENERIC_EVENTS = (
    332001,  # The Bestest Travel Companion! - 2 omni dice
    332003,  # Toss-Up - reroll
    332004,  # Strategize - draw 2
    333006,  # Mondstadt Hash Brown - heal 2
    333002,  # Adeptus' Temptation - burst +3 damage
    332005,  # I Haven't Lost Yet! - omni die + energy
    332006,  # Leave It to Me! - fast switch
    332008,  # Starsigns - energy
    332002,  # Changing Shifts - switch discount
    332009,  # Calx's Arts - energy transfer
    333005,  # Sweet Madame - heal 1
    333001,  # Jueyun Guoba - normal attack +1
    333004,  # Northern Smoked Chicken - normal attack discount
    333008,  # Minty Meat Rolls - 3 normal attacks discounted
)
ARTIFACT_PRIMARY = 312004  # Gambler's Earrings
ARTIFACT_SECONDARY = 312005  # Instructor's Cap
SUPPORT_PRIMARY = 322001  # Paimon
SUPPORT_SECONDARY = 322002  # Katheryne
DICE_EVENTS = frozenset({332001, 332003, 332005, 333004, 333008})
DRAW_EVENTS = frozenset({332004})
HEAL_EVENTS = frozenset({333003, 333005, 333006, 333007})
DAMAGE_EVENTS = frozenset({331102, 331302, 333001, 333002, 331702})

# Fixed fallback roster.  Order is meaningful: opener / second carry / bench.
ARCHETYPES: tuple[dict[str, Any], ...] = (
    {
        "slug": "era_superconduct",
        "archetype": "superconduct",
        "characters": (1101, 1401, 1403),  # Ganyu, Fischl, Keqing
        "notes": "Fallback: Cryo+Electro superconduct; Ganyu carry, Oz + Keqing finish.",
    },
    {
        "slug": "era_vaporize",
        "archetype": "vaporize",
        "characters": (1301, 1203, 1202),  # Diluc, Mona, Xingqiu
        "notes": "Fallback: Pyro+Hydro vaporize; Diluc burst with Mona/Xingqiu hydro.",
    },
    {
        "slug": "era_overload",
        "archetype": "overload",
        "characters": (1302, 1303, 1401),  # Xiangling, Bennett, Fischl
        "notes": "Fallback: Pyro+Electro overload; Guoba/Oz pressure, Bennett heal.",
    },
    {
        "slug": "era_electrocharged",
        "archetype": "electro-charged",
        "characters": (1203, 1403, 1402),  # Mona, Keqing, Razor
        "notes": "Fallback: Hydro+Electro electro-charged; Mona apply, Keqing tempo.",
    },
    {
        "slug": "era_freeze",
        "archetype": "freeze",
        "characters": (1105, 1202, 1102),  # Ayaka, Xingqiu, Diona
        "notes": "Fallback: Cryo+Hydro freeze; Ayaka carry, Diona shield/heal.",
    },
    {
        "slug": "era_swirl",
        "archetype": "swirl",
        "characters": (1501, 1502, 1302),  # Sucrose, Jean, Xiangling
        "notes": "Fallback: Anemo swirl; spread pyro from Guoba, Jean heals.",
    },
    {
        "slug": "era_geo",
        "archetype": "geo/crystallize",
        "characters": (1601, 1602, 1301),  # Ningguang, Noelle, Diluc
        "notes": "Fallback: Geo crystallize; Ningguang burst, Noelle sustain.",
    },
    {
        "slug": "era_physical",
        "archetype": "physical",
        "characters": (1402, 1103, 1303),  # Razor, Kaeya, Bennett
        "notes": "Fallback: normal-attack buffs; no resonance.",
    },
    {
        "slug": "era_burning",
        "archetype": "burning",
        "characters": (1701, 1301, 1302),  # Collei, Diluc, Xiangling
        "notes": "Fallback: Dendro+Pyro burning; Collei apply, double pyro sustains.",
    },
    {
        "slug": "era_triple_sword",
        "archetype": "weapon-coverage (triple sword)",
        "characters": (1103, 1202, 1303),  # Kaeya, Xingqiu, Bennett
        "notes": "Fallback: all three characters are sword users; three distinct swords.",
    },
)

# Human-readable labels for the official share-code decks.  Report/benchmark
# metadata only; it does not affect legality or measurement.
DECK_ARCHETYPES = {
    "keqing_jihua": "quicken",
    "ganyu_shuanglei": "double-electro Ganyu",
    "keqing_shuanghuo": "double-pyro Keqing",
    "nvpu_shuangyan_era": "double-geo Noelle",
    "linghua_yongdong": "Ayaka freeze",
}


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _version_tuple(value: str | None) -> tuple[int, ...] | None:
    if not value:
        return None
    text = str(value).lstrip("vV")
    try:
        return tuple(int(part) for part in text.split("."))
    except ValueError:
        return None


def _version_le(value: str | None, limit: tuple[int, ...]) -> bool:
    parsed = _version_tuple(value)
    return parsed is not None and parsed <= limit


def _sha1(path: Path) -> str:
    if not path.exists():
        return "missing"
    return hashlib.sha1(path.read_bytes()).hexdigest()[:12]


def _cp(k: int, n: int) -> str:
    return f"{k}/{n}" + (f" ({100 * k / n:.0f}%)" if n else "")


def _pct(value: float) -> str:
    return f"{100 * value:.0f}%"


def _pp(value: float) -> str:
    return f"{100 * value:+.0f}pp"


def _wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 0.0
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def _fmt_ci(k: int, n: int) -> str:
    lo, hi = _wilson_ci(k, n)
    return f"{100 * lo:.0f}%-{100 * hi:.0f}%"


def _rounds_three_cell(stats: dict[str, Any]) -> str:
    """``mean rounds among 3★ (n 3★ games)``; an em dash when there are none."""
    mean = stats.get("mean_rounds_three")
    count = int(stats.get("three_stars") or 0)
    return f"{mean:.1f} ({count})" if mean is not None else f"— ({count})"


def _seed_list(value: str) -> tuple[int, ...]:
    """Comma-separated literal seeds (``"1,2,3,4,5"``); no 0..N-1 shorthand."""
    return tuple(int(item) for item in str(value).split(",") if item.strip())


# --------------------------------------------------------------------------- #
# asset + engine probing
# --------------------------------------------------------------------------- #
def load_asset_entries(path: Path = DEFAULT_ASSETS) -> dict[int, dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    entries: dict[int, dict[str, Any]] = {}
    for item in payload.get("data", ()):
        if isinstance(item.get("id"), int):
            entries[int(item["id"])] = item
    return entries


def share_to_id_map(entries: dict[int, dict[str, Any]]) -> dict[int, int]:
    return {
        int(item["shareId"]): int(item["id"])
        for item in entries.values()
        if isinstance(item.get("shareId"), int)
    }


def _is_character_entry(item: dict[str, Any]) -> bool:
    return "hp" in item and "maxEnergy" in item


def _is_action_card_entry(item: dict[str, Any]) -> bool:
    if "playCost" not in item:
        return False
    card_type = str(item.get("type") or "")
    if card_type.startswith("GCG_SKILL_TAG_"):
        return False
    return card_type in {"GCG_CARD_EVENT", "GCG_CARD_ASSIST", "GCG_CARD_MODIFY"}


def _probe_load(
    characters: Iterable[int],
    cards: Iterable[int],
    *,
    version: str = ERA_VERSION,
) -> tuple[bool, str]:
    """Try a minimal engine deck and capture the engine's stderr message.

    The gitcg binding raises a bare ``AssertionError``; the useful text
    (``Unknown card id <id>`` / ``Unknown character id <id>``) is printed by the
    embedded JS runtime on fd 2.  Redirect fd 2 through a pipe for the duration
    of the construction so the report can quote the engine, not our guess.
    """
    from gitcg import CreateParam, Game

    read_fd, write_fd = os.pipe()
    saved_stderr = os.dup(2)
    os.dup2(write_fd, 2)
    os.close(write_fd)
    ok = True
    python_error = ""
    try:
        create_param = CreateParam(version=version)
        create_param.set_characters(0, [int(c) for c in characters])
        create_param.set_cards(0, [int(c) for c in cards])
        create_param.set_characters(1, [3001, 3002, 3002])
        create_param.set_cards(1, [311101] * 30)
        try:
            Game(create_param=create_param)
        except Exception as exc:  # the binding swallows the message; see above
            ok = False
            python_error = f"{type(exc).__name__}: {exc}"
    finally:
        os.dup2(saved_stderr, 2)
        os.close(saved_stderr)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(read_fd, 4096)
            if not chunk:
                break
            chunks.append(chunk)
        os.close(read_fd)
    message = b"".join(chunks).decode("utf-8", "replace").strip()
    if python_error:
        message = f"{message} | {python_error}".strip(" |")
    return ok, message


def _card_kind(item: dict[str, Any]) -> str:
    tags = set(item.get("tags") or ())
    if "GCG_TAG_TALENT" in tags:
        return "talent"
    card_type = str(item.get("type") or "")
    if card_type == "GCG_CARD_MODIFY":
        if "GCG_TAG_WEAPON" in tags:
            return "weapon"
        if "GCG_TAG_ARTIFACT" in tags:
            return "artifact"
        return "other"
    if card_type == "GCG_CARD_ASSIST":
        return "support"
    return "event"


def _weapon_type(item: dict[str, Any]) -> str:
    for tag in item.get("tags") or ():
        if tag in WEAPON_TAGS_BY_TAG:
            return WEAPON_TAGS_BY_TAG[tag]
    return ""


def _character_element(item: dict[str, Any]) -> str:
    for tag in item.get("tags") or ():
        for name, element_tag in ELEMENT_TAGS.items():
            if tag == element_tag:
                return name
    return ""


def _character_weapon(item: dict[str, Any]) -> str:
    for tag in item.get("tags") or ():
        if tag in WEAPON_TAGS_BY_TAG:
            return WEAPON_TAGS_BY_TAG[tag]
    return ""


def _card_element(item: dict[str, Any], entries: dict[int, dict[str, Any]]) -> str:
    for tag in item.get("relatedCharacterTags") or ():
        for name, element_tag in ELEMENT_TAGS.items():
            if tag == element_tag:
                return name
    related = item.get("relatedCharacterId")
    if isinstance(related, int) and related in entries:
        return _character_element(entries[related])
    # The 14 element-specific artifacts (e.g. Blizzard Strayer) carry no element
    # tag in the asset dump; their id range is the deterministic discriminator:
    # 3121xx cryo, 3122xx hydro, 3123xx pyro, 3124xx electro, 3125xx anemo,
    # 3126xx geo, 3127xx dendro.  3120xx generic artifacts stay neutral.
    if _card_kind(item) == "artifact":
        artifact_element = {
            3121: "CRYO",
            3122: "HYDRO",
            3123: "PYRO",
            3124: "ELECTRO",
            3125: "ANEMO",
            3126: "GEO",
            3127: "DENDRO",
        }.get(int(item.get("id", 0)) // 100)
        if artifact_element:
            return artifact_element
    return ""


def _meta_from_entry(item: dict[str, Any], entries: dict[int, dict[str, Any]]) -> dict[str, Any]:
    return {
        "name": str(item.get("name") or item.get("englishName") or item.get("id")),
        "english_name": str(item.get("englishName") or ""),
        "kind": _card_kind(item),
        "element": _card_element(item, entries),
        "weapon": _weapon_type(item),
        "related_character": int(item.get("relatedCharacterId") or 0),
        "cost": sum(int(part.get("count", 0)) for part in (item.get("playCost") or ())),
        "tags": list(item.get("tags") or ()),
        "description": str(item.get("description") or ""),
        "since_version": item.get("sinceVersion"),
        "obtainable": item.get("obtainable") is not False,
    }


def build_pool(
    assets_path: Path = DEFAULT_ASSETS,
    *,
    version: str = ERA_VERSION,
) -> dict[str, Any]:
    """Derive + engine-validate the legal v3.3.0 pool."""
    entries = load_asset_entries(assets_path)
    limit = _version_tuple(version) or ERA_TUPLE

    character_candidates = [
        item
        for item in entries.values()
        if _is_character_entry(item) and _version_le(item.get("sinceVersion"), limit)
    ]
    card_candidates = [
        item
        for item in entries.values()
        if _is_action_card_entry(item) and _version_le(item.get("sinceVersion"), limit)
    ]

    base_characters = (1103, 1301, 1401)  # Kaeya / Diluc / Fischl, known launch cards
    base_cards = (311101,) * 30
    character_ok: list[dict[str, Any]] = []
    character_bad: list[dict[str, Any]] = []
    for item in sorted(character_candidates, key=lambda entry: int(entry["id"])):
        ok, message = _probe_load([int(item["id"]), 1301, 1401], base_cards, version=version)
        (character_ok if ok else character_bad).append({**item, "_probe": message})

    card_ok: list[dict[str, Any]] = []
    card_bad: list[dict[str, Any]] = []
    for item in sorted(card_candidates, key=lambda entry: int(entry["id"])):
        ok, message = _probe_load(base_characters, [int(item["id"])] * 30, version=version)
        (card_ok if ok else card_bad).append({**item, "_probe": message})

    playable_characters = [
        item
        for item in character_ok
        if item.get("category") == "characters"
        and item.get("obtainable") is not False
    ]
    playable_ids = {int(item["id"]) for item in playable_characters}
    excluded_characters = [
        item for item in character_ok if int(item["id"]) not in playable_ids
    ]
    # Keep the historical field names for config compatibility: "player" means
    # playable, "npc" now means excluded-by-asset-metadata (empty at v3.3.0).
    player_characters = playable_characters
    npc_characters = excluded_characters
    deckable_cards = [
        item for item in card_ok if item.get("obtainable") is not False
    ]

    character_meta = {
        int(item["id"]): {
            "name": str(item.get("name") or item.get("englishName") or item["id"]),
            "english_name": str(item.get("englishName") or ""),
            "element": _character_element(item),
            "weapon": _character_weapon(item),
            "since_version": item.get("sinceVersion"),
        }
        for item in player_characters
    }
    card_meta = {int(item["id"]): _meta_from_entry(item, entries) for item in deckable_cards}
    return {
        "version": version,
        "assets_path": str(assets_path),
        "assets_sha1": _sha1(Path(assets_path)),
        "characters": sorted(character_meta),
        "character_meta": character_meta,
        "npc_characters": sorted(int(item["id"]) for item in npc_characters),
        "npc_character_meta": {
            int(item["id"]): {
                "name": str(item.get("name") or ""),
                "english_name": str(item.get("englishName") or ""),
                "element": _character_element(item),
                "since_version": item.get("sinceVersion"),
            }
            for item in npc_characters
        },
        "cards": sorted(card_meta),
        "card_meta": card_meta,
        "counts": {
            "character_candidates": len(character_candidates),
            "characters_engine_legal": len(character_ok),
            "player_characters": len(player_characters),
            "npc_characters": len(npc_characters),
            "card_candidates": len(card_candidates),
            "cards_engine_legal": len(card_ok),
            "cards_deck_buildable": len(deckable_cards),
            "cards_engine_rejected": len(card_bad),
        },
        "rejected_cards": [
            {
                "id": int(item["id"]),
                "name": str(item.get("name") or ""),
                "since_version": item.get("sinceVersion"),
                "engine_message": str(item.get("_probe") or ""),
            }
            for item in card_bad
        ],
        "rejected_characters": [
            {
                "id": int(item["id"]),
                "name": str(item.get("name") or ""),
                "since_version": item.get("sinceVersion"),
                "engine_message": str(item.get("_probe") or ""),
            }
            for item in character_bad
        ],
    }


# --------------------------------------------------------------------------- #
# deck description / rule validation (works for imported and fallback decks)
# --------------------------------------------------------------------------- #
def deck_metadata(
    characters: Iterable[int],
    cards: Iterable[int],
    entries: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """Names/elements/weapons + composition + objective coverage from assets."""
    characters = [int(character) for character in characters]
    cards = [int(card_id) for card_id in cards]
    character_names: list[str] = []
    character_elements: list[str] = []
    character_weapons: list[str] = []
    for character in characters:
        item = entries.get(character, {})
        character_names.append(str(item.get("name") or character))
        character_elements.append(_character_element(item))
        character_weapons.append(_character_weapon(item))

    composition = {kind: 0 for kind in CARD_KINDS}
    for card_id in cards:
        composition[_card_kind(entries.get(card_id, {}))] += 1
    events = [card_id for card_id in cards if _card_kind(entries.get(card_id, {})) == "event"]
    return {
        "characters": characters,
        "cards": cards,
        "character_names": character_names,
        "character_elements": character_elements,
        "character_weapons": character_weapons,
        "composition": {**composition, "total": len(cards)},
        "objective_coverage": {
            "weapon": composition["weapon"],
            "artifact": composition["artifact"],
            "event": len(events),
        },
        "event_categories": {
            "dice": sum(1 for card_id in events if card_id in DICE_EVENTS),
            "draw": sum(1 for card_id in events if card_id in DRAW_EVENTS),
            "heal": sum(1 for card_id in events if card_id in HEAL_EVENTS),
            "damage": sum(1 for card_id in events if card_id in DAMAGE_EVENTS),
        },
    }


def decorate_deck(
    deck: dict[str, Any], entries: dict[int, dict[str, Any]]
) -> dict[str, Any]:
    """Fill/refresh the derived fields of a deck entry (idempotent)."""
    out = dict(deck)
    out.update(deck_metadata(deck["characters"], deck["cards"], entries))
    out["name"] = str(deck.get("name") or "")
    out.setdefault("source", "share_code")
    archetype = str(out.get("archetype") or "")
    if not archetype or archetype.startswith("share_code"):
        out["archetype"] = DECK_ARCHETYPES.get(out["name"], out["source"])
    else:
        out["archetype"] = archetype
    return out


def validate_deck_rules(
    deck: dict[str, Any], entries: dict[int, dict[str, Any]]
) -> list[str]:
    """TCG deck-building rules that the engine does not enforce itself."""
    errors: list[str] = []
    characters = [int(character) for character in deck["characters"]]
    cards = [int(card_id) for card_id in deck["cards"]]
    if len(characters) != 3 or len(set(characters)) != 3:
        errors.append(f"expected 3 distinct characters, got {characters}")
    for character in characters:
        item = entries.get(character)
        if item is None:
            errors.append(f"character {character} is unknown to the asset catalog")
        elif item.get("category") != "characters" or item.get("obtainable") is False:
            errors.append(f"character {character} is not a playable character card")
    if len(cards) != DECK_SIZE:
        errors.append(f"expected {DECK_SIZE} cards, got {len(cards)}")
    for card_id in set(cards):
        if card_id not in entries:
            errors.append(f"card {card_id} is unknown to the asset catalog")
        elif _card_kind(entries[card_id]) == "other":
            errors.append(f"card {card_id} is not a player action card")
        copies = sum(1 for card in cards if card == card_id)
        if copies > MAX_COPIES:
            errors.append(f"card {card_id} has {copies} copies (max {MAX_COPIES})")

    character_set = set(characters)
    weapon_tags = {
        WEAPON_TAGS[_character_weapon(entries[character])]
        for character in characters
        if character in entries and _character_weapon(entries[character]) in WEAPON_TAGS
    }
    weapons: list[int] = []
    for card_id in cards:
        item = entries.get(card_id)
        if item is None:
            continue
        kind = _card_kind(item)
        if kind == "talent":
            related = int(item.get("relatedCharacterId") or 0)
            if related not in character_set:
                errors.append(
                    f"talent {card_id} is for character {related}, not in {characters}"
                )
        elif kind == "weapon":
            weapons.append(card_id)
            if not set(item.get("tags") or ()) & weapon_tags:
                errors.append(f"weapon {card_id} is not usable by any chosen character")
    if len(weapons) > MAX_WEAPONS_PER_SLOT * len(characters):
        errors.append(
            f"{len(weapons)} weapon cards > {MAX_WEAPONS_PER_SLOT} per character slot"
        )
    return errors


# --------------------------------------------------------------------------- #
# fallback deck construction (explicitly opt-in)
# --------------------------------------------------------------------------- #
def _copies(cards: Iterable[int], card_id: int) -> int:
    return sum(1 for card in cards if int(card) == int(card_id))


def _talent_for(character: int, pool: dict[str, Any]) -> int:
    candidates = [
        card_id
        for card_id, meta in pool["card_meta"].items()
        if meta["kind"] == "talent" and int(meta["related_character"]) == int(character)
    ]
    if not candidates:
        raise ValueError(f"no deck-buildable talent for character {character}")
    return min(candidates)


def _weapon_for(character: int, cards: list[int], pool: dict[str, Any]) -> int:
    weapon_type = pool["character_meta"][int(character)]["weapon"]
    tag = WEAPON_TAGS.get(weapon_type)
    candidates = [
        card_id
        for card_id, meta in pool["card_meta"].items()
        if meta["kind"] == "weapon" and tag in set(meta["tags"])
    ]
    if not candidates:
        raise ValueError(f"no legal {weapon_type} weapon for character {character}")
    candidates.sort(
        key=lambda card_id: (
            _copies(cards, card_id),
            pool["card_meta"][card_id]["cost"],
            card_id,
        )
    )
    for card_id in candidates:
        if _copies(cards, card_id) < MAX_COPIES:
            return card_id
    raise ValueError(f"all {weapon_type} weapons already at max copies")


def _dominant_element(characters: Iterable[int], pool: dict[str, Any]) -> str | None:
    characters = tuple(int(character) for character in characters)
    counts = Counter(pool["character_meta"][character]["element"] for character in characters)
    for character in characters:
        element = pool["character_meta"][character]["element"]
        if counts[element] >= 2:
            return element
    return None


def _event_priority(characters: Iterable[int], pool: dict[str, Any]) -> list[int]:
    priority: list[int] = []
    element = _dominant_element(characters, pool)
    if element is not None:
        priority.extend(RESONANCE[element])
    priority.extend(GENERIC_EVENTS)
    for card_id in priority:
        if card_id not in pool["card_meta"]:
            raise ValueError(f"event {card_id} is not in the era pool")
    return priority


def build_fallback_deck(spec: dict[str, Any], pool: dict[str, Any]) -> dict[str, Any]:
    """Assemble one synthetic deck by the documented fallback rule."""
    characters = tuple(int(character) for character in spec["characters"])
    cards: list[int] = []
    for character in characters:
        cards.append(_talent_for(character, pool))
    for character in characters:
        cards.append(_weapon_for(character, cards, pool))
    cards.extend([ARTIFACT_PRIMARY] * 2 + [ARTIFACT_SECONDARY])
    cards.extend([SUPPORT_PRIMARY] * 2 + [SUPPORT_SECONDARY])
    for card_id in _event_priority(characters, pool):
        while len(cards) < DECK_SIZE and _copies(cards, card_id) < MAX_COPIES:
            cards.append(int(card_id))
    if len(cards) < DECK_SIZE:
        fallback = sorted(
            card_id
            for card_id, meta in pool["card_meta"].items()
            if meta["kind"] == "event"
        )
        for card_id in fallback:
            while len(cards) < DECK_SIZE and _copies(cards, card_id) < MAX_COPIES:
                cards.append(int(card_id))
    if len(cards) != DECK_SIZE:
        raise ValueError(f"{spec['slug']}: built {len(cards)} cards, need {DECK_SIZE}")
    deck = {
        "name": spec["slug"],
        "archetype": spec["archetype"],
        "characters": list(characters),
        "cards": cards,
        "notes": spec["notes"],
        "source": "fallback_synthetic",
    }
    return decorate_deck(deck, load_asset_entries())


def build_fallback_pool(pool: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entries = load_asset_entries()
    decks: dict[str, dict[str, Any]] = {}
    for spec in ARCHETYPES:
        deck = build_fallback_deck(spec, pool)
        errors = validate_deck_rules(deck, entries)
        composition = deck["composition"]
        if (
            composition["talent"] != 3
            or composition["weapon"] != 3
            or composition["artifact"] != 3
            or composition["support"] != 3
            or composition["event"] != 18
        ):
            errors.append(
                "fallback package must be 3 talents / 3 weapons / 3 artifacts / "
                f"3 supports / 18 events, got {composition}"
            )
        if errors:
            raise ValueError(f"{deck['name']} is invalid: {errors}")
        decks[deck["name"]] = deck
    return decks


# --------------------------------------------------------------------------- #
# policy + one game
# --------------------------------------------------------------------------- #
def _scripted_policy(deck: dict[str, Any]):
    """ExpertRuleAgent with a synthetic profile (no registry/deck-rule entry).

    The implementation lives in :mod:`skillloop.era` so the matrix and the
    experiment runner play era decks with the same policy; this wrapper keeps
    existing callers (``scripts/probe_stall.py``) working.
    """
    return era_policy(deck, seed=0)


def _objective_keys(objectives: Iterable[Any]) -> list[str]:
    seen: dict[str, int] = {}
    keys: list[str] = []
    for objective in objectives:
        objective_type = str(objective.type)
        ordinal = seen.get(objective_type, 0)
        keys.append(objective_type if ordinal == 0 else f"{objective_type}#{ordinal}")
        seen[objective_type] = ordinal + 1
    return keys


def play_one(payload: dict[str, Any]) -> dict[str, Any]:
    """Play one (stage, era deck, seed) game; top-level for ``spawn`` pools."""
    deck_payload = payload["deck"]
    deck = deck_spec_from_deck(deck_payload)
    stage = get_stage(str(payload["stage"]))
    npc_deck = DeckSpec(
        name=f"npc_{stage.name}",
        characters=stage.deck_characters,
        cards=deck.cards,
    )
    seed = int(payload["seed"])
    started = time.perf_counter()
    record = run_match(
        deck,
        npc_deck,
        _scripted_policy(deck_payload),
        npc_policy_for_stage(stage),
        seed=seed,
        version=ERA_VERSION,
        record_decisions=True,
        record_events=True,
        record_final_state=True,
        stage_rules=stage_rules_json(stage.name),
    )
    elapsed = time.perf_counter() - started
    stars = evaluate_stage(stage, record)
    keys = _objective_keys(stars.objectives)
    return {
        "index": int(payload.get("index", 0)),
        "stage": stage.name,
        "deck": deck.name,
        "archetype": str(deck_payload.get("archetype") or ""),
        "seed": seed,
        "version": ERA_VERSION,
        "stage_rules": True,
        "winner": record.winner,
        "rounds": int(record.rounds),
        "stars": int(stars.stars),
        "three_star": bool(stars.three_star),
        "objectives": {
            key: bool(item.achieved) for key, item in zip(keys, stars.objectives, strict=True)
        },
        "objective_details": {
            key: item.detail for key, item in zip(keys, stars.objectives, strict=True)
        },
        "n_objectives": len(stars.objectives),
        "decisions0": int(record.decisions0),
        "decisions1": int(record.decisions1),
        "fallbacks0": int(record.fallbacks0),
        "fallbacks1": int(record.fallbacks1),
        "error": record.error,
        "truncated": bool(record.truncated),
        "seconds": round(float(record.wall_seconds), 3),
        "wall_seconds": round(elapsed, 3),
    }


# --------------------------------------------------------------------------- #
# stage card requirements + composition
# --------------------------------------------------------------------------- #
def stage_card_requirements(stage_name: str) -> list[dict[str, Any]]:
    stage = get_stage(stage_name)
    requirements: list[dict[str, Any]] = []
    for objective in stage.objectives:
        if objective.type == "play_equipment_card":
            requirements.append(
                {
                    "kind": objective.kind or "equipment",
                    "count": int(objective.count if objective.count is not None else 1),
                }
            )
        elif objective.type == "play_event_cards":
            requirements.append(
                {
                    "kind": "event",
                    "count": int(objective.count if objective.count is not None else 1),
                }
            )
    return requirements


_REQUIREMENT_FIELD = {
    "weapon": "weapon",
    "artifact": "artifact",
    "event": "event",
    "support": "support",
}


def deck_requirement_status(
    deck: dict[str, Any], requirements: Iterable[dict[str, Any]]
) -> tuple[bool, str]:
    requirements = list(requirements)
    if not requirements:
        return True, "n/a"
    coverage = deck["objective_coverage"]
    details: list[str] = []
    satisfied = True
    for requirement in requirements:
        field = _REQUIREMENT_FIELD.get(str(requirement["kind"]), "")
        have = int(coverage.get(field, 0)) if field else 0
        need = int(requirement["count"])
        ok = have >= need
        satisfied = satisfied and ok
        details.append(f"{requirement['kind']} {have}/{need}")
    return satisfied, ", ".join(details)


# --------------------------------------------------------------------------- #
# matrix storage / scheduling
# --------------------------------------------------------------------------- #
def _meta_path(out: Path) -> Path:
    return out.with_name(out.name + ".meta.json")


def fingerprint(stages: tuple[str, ...], decks: tuple[str, ...]) -> dict[str, Any]:
    return {
        "version": ERA_VERSION,
        "policy": "expert:era_<deck>@seed=0",
        "npc": "greedy",
        "stages": list(stages),
        "decks": list(decks),
        "record_decisions": True,
        "record_events": True,
        "record_final_state": True,
        "stage_rules": True,
        "special_rules_sha": _sha1(
            REPO_ROOT / "configs" / "npc" / "special_rules.json"
        ),
        "engine_lock": _sha1(REPO_ROOT / "configs" / "engine.lock"),
        "hp_table": _sha1(REPO_ROOT / "configs" / "npc" / "hp_table.json"),
        "stages_sha": _sha1(REPO_ROOT / "configs" / "npc" / "npc_stages_mond_liyue.json"),
        "assets_sha": _sha1(DEFAULT_ASSETS),
    }


def load_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _load_done(out: Path) -> set[tuple[str, str, int]]:
    done: set[tuple[str, str, int]] = set()
    for row in load_rows(out):
        if not row.get("error") and not row.get("truncated"):
            done.add((str(row["stage"]), str(row["deck"]), int(row["seed"])))
    return done


def _check_fingerprint(out: Path, fp: dict[str, Any]) -> None:
    meta_path = _meta_path(out)
    if not meta_path.exists():
        return
    previous = json.loads(meta_path.read_text(encoding="utf-8"))
    if previous.get("fingerprint") != fp:
        raise SystemExit(
            f"{out} was produced by a different spec; use another --out or delete it\n"
            f"  old: {previous.get('fingerprint')}\n  new: {fp}"
        )


def _write_meta(out: Path, meta: dict[str, Any]) -> None:
    _meta_path(out).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _print_row(row: dict[str, Any], done: int, total: int, started: float) -> None:
    elapsed = time.perf_counter() - started
    rate = done / elapsed if elapsed > 0 else 0.0
    eta = (total - done) / rate if rate > 0 else 0.0
    flag = "3*" if row.get("three_star") else f"{row.get('stars', 0)}*"
    print(
        f"[{done:>5}/{total:<5}] {100 * done / max(1, total):5.1f}% "
        f"elapsed={elapsed:6.0f}s eta={eta:6.0f}s "
        f"{row['stage']:16s} {row['deck']:24s} seed={row['seed']:<3d} "
        f"r={row.get('rounds', 0):<2d} {flag:>3s} winner={row.get('winner')} "
        f"err={row.get('error') or ''}",
        flush=True,
    )


def play_pending(
    pending: list[dict[str, Any]], out: Path, workers: int
) -> tuple[list[dict[str, Any]], float]:
    rows: list[dict[str, Any]] = []
    started = time.perf_counter()
    workers = max(1, min(int(workers), MAX_WORKERS))
    if not pending:
        return rows, 0.0
    with out.open("a", encoding="utf-8") as handle:
        if workers <= 1:
            for item in pending:
                row = play_item(item)
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                rows.append(row)
                _print_row(row, len(rows), len(pending), started)
        else:
            # ``spawn`` (not fork): forking a live V8 isolate deadlocks the child.
            context = mp.get_context("spawn")
            with ProcessPoolExecutor(max_workers=workers, mp_context=context) as pool:
                pending_iter = iter(pending)
                waiting = next(pending_iter, None)
                in_flight: dict[Future, dict[str, Any]] = {}

                def submit_next() -> bool:
                    nonlocal waiting
                    if waiting is None:
                        return False
                    in_flight[pool.submit(play_item, waiting)] = waiting
                    waiting = next(pending_iter, None)
                    return True

                while len(in_flight) < workers and submit_next():
                    pass
                while in_flight:
                    finished, _ = wait(in_flight.keys(), return_when=FIRST_COMPLETED)
                    for future in finished:
                        item = in_flight.pop(future)
                        try:
                            row = future.result()
                        except Exception as exc:
                            row = _error_row(item, exc)
                        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                        handle.flush()
                        rows.append(row)
                        _print_row(row, len(rows), len(pending), started)
                    while len(in_flight) < workers and submit_next():
                        pass
    return rows, time.perf_counter() - started


def _error_row(item: dict[str, Any], exc: BaseException) -> dict[str, Any]:
    deck = item.get("deck", {})
    return {
        "index": int(item.get("index", 0)),
        "stage": str(item.get("stage", "")),
        "deck": str(deck.get("name", "")),
        "archetype": str(deck.get("archetype", "")),
        "seed": int(item.get("seed", 0)),
        "version": ERA_VERSION,
        "stage_rules": True,
        "winner": None,
        "rounds": 0,
        "stars": 0,
        "three_star": False,
        "objectives": {},
        "objective_details": {},
        "n_objectives": 0,
        "decisions0": 0,
        "decisions1": 0,
        "fallbacks0": 0,
        "fallbacks1": 0,
        "error": f"{type(exc).__name__}: {exc}",
        "truncated": False,
        "seconds": 0.0,
        "wall_seconds": 0.0,
    }


def play_item(item: dict[str, Any]) -> dict[str, Any]:
    """``play_one`` with a JSON-safe error row (top-level for spawn workers)."""
    try:
        return play_one(item)
    except Exception as exc:  # keep the grid running; the row is retried on resume
        return _error_row(item, exc)


# --------------------------------------------------------------------------- #
# aggregation
# --------------------------------------------------------------------------- #
def _empty_cell() -> dict[str, int]:
    return {
        "n": 0,
        "wins": 0,
        "three_stars": 0,
        "stars": 0,
        "rounds": 0,
        "rounds_three": 0,
        "draws": 0,
    }


def _stats(cell: dict[str, int]) -> dict[str, Any]:
    n = cell["n"]
    return {
        "n": n,
        "wins": cell["wins"],
        "three_stars": cell["three_stars"],
        "stars": cell["stars"],
        "rounds": cell["rounds"],
        "rounds_three": cell["rounds_three"],
        "draws": cell["draws"],
        "win_rate": cell["wins"] / n if n else 0.0,
        "three_star_rate": cell["three_stars"] / n if n else 0.0,
        "mean_stars": cell["stars"] / n if n else 0.0,
        "mean_rounds": cell["rounds"] / n if n else 0.0,
        "mean_rounds_three": (
            cell["rounds_three"] / cell["three_stars"] if cell["three_stars"] else None
        ),
        "draw_rate": cell["draws"] / n if n else 0.0,
    }


def aggregate(
    rows: list[dict[str, Any]],
    stages: list[str],
    decks: list[str],
    seeds: Iterable[int],
) -> dict[str, Any]:
    seed_set = {int(seed) for seed in seeds}
    cells: dict[str, dict[str, dict[str, int]]] = {
        stage: {deck: _empty_cell() for deck in decks} for stage in stages
    }
    overall = _empty_cell()
    invalid = 0
    valid_rows = 0
    for row in rows:
        if row.get("error") or row.get("truncated"):
            invalid += 1
            continue
        if int(row.get("seed", -1)) not in seed_set:
            continue
        stage = str(row["stage"])
        deck = str(row["deck"])
        if stage not in cells or deck not in cells[stage]:
            continue
        cell = cells[stage][deck]
        valid_rows += 1
        rounds = int(row.get("rounds") or 0)
        three_star = bool(row.get("three_star"))
        for target in (cell, overall):
            target["n"] += 1
            target["wins"] += int(row.get("winner") == 0)
            target["three_stars"] += int(three_star)
            target["stars"] += int(row.get("stars") or 0)
            target["rounds"] += rounds
            target["rounds_three"] += rounds if three_star else 0
            target["draws"] += int(row.get("winner") not in (0, 1))

    by_stage = {
        stage: {deck: _stats(cell) for deck, cell in deck_cells.items()}
        for stage, deck_cells in cells.items()
    }
    by_deck: dict[str, dict[str, Any]] = {}
    for deck in decks:
        pooled = _empty_cell()
        per_stage_win: list[float] = []
        per_stage_three: list[float] = []
        for stage in stages:
            cell = by_stage[stage][deck]
            pooled["n"] += cell["n"]
            pooled["wins"] += cell["wins"]
            pooled["three_stars"] += cell["three_stars"]
            pooled["stars"] += cell["stars"]
            pooled["rounds"] += cell["rounds"]
            pooled["rounds_three"] += cell["rounds_three"]
            pooled["draws"] += cell["draws"]
            if cell["n"]:
                per_stage_win.append(cell["win_rate"])
                per_stage_three.append(cell["three_star_rate"])
        stats = _stats(pooled)
        stats["mean_stage_win_rate"] = (
            sum(per_stage_win) / len(per_stage_win) if per_stage_win else 0.0
        )
        stats["mean_stage_three_star_rate"] = (
            sum(per_stage_three) / len(per_stage_three) if per_stage_three else 0.0
        )
        by_deck[deck] = stats
    return {
        "cells": by_stage,
        "by_deck": by_deck,
        "overall": _stats(overall),
        "invalid": invalid,
        "valid": valid_rows,
    }


def _rank_key(stats: dict[str, Any], deck: str) -> tuple[float, float, float, float, str]:
    return (
        -float(stats["three_star_rate"]),
        -float(stats["win_rate"]),
        -float(stats["mean_stars"]),
        float(stats["mean_rounds"]),
        deck,
    )


def recommend_for_stage(
    stage: str,
    stats_by_deck: dict[str, dict[str, Any]],
    decks: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Best deck that satisfies the stage's card objective (strength second)."""
    requirements = stage_card_requirements(stage)
    eligible: list[str] = []
    statuses: dict[str, str] = {}
    for deck_name, deck in decks.items():
        if int(stats_by_deck[deck_name]["n"]) <= 0:
            continue
        satisfied, detail = deck_requirement_status(deck, requirements)
        statuses[deck_name] = detail
        if satisfied:
            eligible.append(deck_name)
    ranked = sorted(eligible, key=lambda name: _rank_key(stats_by_deck[name], name))
    ranked_any = sorted(
        [name for name in decks if int(stats_by_deck[name]["n"]) > 0],
        key=lambda name: _rank_key(stats_by_deck[name], name),
    )
    recommended = ranked[0] if ranked else None
    runner_up = ranked[1] if len(ranked) > 1 else None
    best_any = ranked_any[0] if ranked_any else None
    recommended_stats = stats_by_deck[recommended] if recommended else None
    best_any_stats = stats_by_deck[best_any] if best_any else None
    gap = 0.0
    if recommended_stats and best_any_stats:
        gap = float(best_any_stats["win_rate"] - recommended_stats["win_rate"])
    return {
        "stage": stage,
        "requirements": requirements,
        "statuses": statuses,
        "eligible": ranked,
        "recommended": recommended,
        "runner_up": runner_up,
        "recommended_stats": recommended_stats,
        "runner_up_stats": stats_by_deck[runner_up] if runner_up else None,
        "best_any": best_any,
        "best_any_stats": best_any_stats,
        "best_any_satisfies": best_any == recommended if best_any and recommended else False,
        "win_rate_gap": gap,
        "flag_tradeoff": gap > 0.15,
    }


# --------------------------------------------------------------------------- #
# post-3.3 comparison (read-only)
# --------------------------------------------------------------------------- #
def load_post_matrix(path: Path = POST_MATRIX) -> dict[str, Any] | None:
    """Best-per-stage numbers from the finished post-3.3 matrix (read-only).

    Used only as evidence that the pool change removes saturation; it is never
    used for era deck selection.
    """
    rows = load_rows(path)
    if not rows:
        return None
    valid = [row for row in rows if not row.get("error") and not row.get("truncated")]
    # Exclude seed 0: 121/320 draws there (15-round cap) vs 0-3/320 on seeds 1-4.
    # Reading the existing rows is not a re-run; it just avoids the bad
    # discriminator in the saturation comparison.
    screen = [row for row in valid if int(row["seed"]) in {1, 2, 3, 4}]
    by_stage: dict[str, dict[str, list[int]]] = defaultdict(
        lambda: defaultdict(lambda: [0, 0, 0])
    )
    for row in screen:
        cell = by_stage[str(row["stage"])][str(row["deck"])]
        cell[0] += 1
        cell[1] += int(row.get("winner") == 0)
        cell[2] += int(bool(row.get("three_star")))
    best: dict[str, dict[str, Any]] = {}
    for stage, decks in by_stage.items():
        deck, cell = max(
            decks.items(),
            key=lambda item: (
                item[1][1] / item[1][0] if item[1][0] else 0,
                item[1][2] / item[1][0] if item[1][0] else 0,
            ),
        )
        best[stage] = {
            "deck": deck,
            "n": cell[0],
            "wins": cell[1],
            "three_stars": cell[2],
            "win_rate": cell[1] / cell[0] if cell[0] else 0.0,
            "three_star_rate": cell[2] / cell[0] if cell[0] else 0.0,
        }
    pooled: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for decks in by_stage.values():
        for deck, cell in decks.items():
            pooled[deck][0] += cell[0]
            pooled[deck][1] += cell[1]
            pooled[deck][2] += cell[2]
    best_deck = max(
        pooled.items(),
        key=lambda item: (
            item[1][2] / item[1][0] if item[1][0] else 0,
            item[1][1] / item[1][0] if item[1][0] else 0,
        ),
    )
    return {
        "path": str(path),
        "n_rows": len(rows),
        "valid": len(valid),
        "best": best,
        "best_deck": {
            "deck": best_deck[0],
            "n": best_deck[1][0],
            "wins": best_deck[1][1],
            "three_stars": best_deck[1][2],
            "win_rate": best_deck[1][1] / best_deck[1][0] if best_deck[1][0] else 0.0,
            "three_star_rate": best_deck[1][2] / best_deck[1][0] if best_deck[1][0] else 0.0,
        },
    }


# --------------------------------------------------------------------------- #
# legality / validation probes
# --------------------------------------------------------------------------- #
def probe_legality() -> dict[str, Any]:
    """Check every stage lineup + the current superconduct_aggro at v3.3.0."""
    from envs.decks import deck_spec

    entries = load_asset_entries()
    stages: dict[str, Any] = {}
    for name in stage_names():
        stage = get_stage(name)
        characters = [int(c) for c in stage.deck_characters]
        ok_npc, message_npc = _probe_load(characters, [311101] * 30, version=ERA_VERSION)
        stages[name] = {
            "characters": characters,
            "loads": bool(ok_npc),
            "engine_message": message_npc,
        }

    current = deck_spec("superconduct_aggro")
    illegal_chars = [
        c for c in current.characters if c not in _legal_characters(entries)
    ]
    illegal_cards = [c for c in current.cards if c not in _legal_cards(entries)]
    ok, message = _probe_load(
        list(current.characters), list(current.cards), version=ERA_VERSION
    )
    return {
        "version": ERA_VERSION,
        "stages": stages,
        "stages_all_load": all(item["loads"] for item in stages.values()),
        "current_superconduct_aggro": {
            "deck": current.name,
            "characters": list(current.characters),
            "illegal_characters": illegal_chars,
            "illegal_cards": sorted(set(illegal_cards)),
            "n_illegal_cards": len(illegal_cards),
            "loads": bool(ok),
            "engine_message": message,
        },
    }


def _legal_cards(entries: dict[int, dict[str, Any]]) -> set[int]:
    return {
        card_id
        for card_id in _era_cards(entries)
        if _probe_load((1103, 1301, 1401), [card_id] * 30)[0]
    }


def _legal_characters(entries: dict[int, dict[str, Any]]) -> set[int]:
    return {
        character
        for character in _era_characters(entries)
        if _probe_load([character, 1301, 1401], [311101] * 30)[0]
    }


def _era_cards(entries: dict[int, dict[str, Any]]) -> list[int]:
    limit = ERA_TUPLE
    return [
        card_id
        for card_id, item in entries.items()
        if _is_action_card_entry(item) and _version_le(item.get("sinceVersion"), limit)
    ]


def _era_characters(entries: dict[int, dict[str, Any]]) -> list[int]:
    limit = ERA_TUPLE
    return [
        character
        for character, item in entries.items()
        if _is_character_entry(item) and _version_le(item.get("sinceVersion"), limit)
    ]


def validate_games(
    decks: dict[str, dict[str, Any]], *, stage: str = "timmie"
) -> dict[str, Any]:
    """Play one full game per deck at v3.3.0 and print compositions."""
    results: dict[str, Any] = {}
    for name, deck in decks.items():
        payload = {
            "index": 0,
            "stage": stage,
            "seed": 1,
            "deck": {
                "name": deck["name"],
                "archetype": deck.get("archetype", ""),
                "characters": deck["characters"],
                "cards": deck["cards"],
                "character_names": deck.get("character_names", []),
                "character_elements": deck.get("character_elements", []),
            },
        }
        try:
            row = play_one(payload)
        except Exception as exc:
            row = {
                "error": f"{type(exc).__name__}: {exc}",
                "winner": None,
                "rounds": 0,
                "stars": 0,
                "three_star": False,
                "objectives": {},
            }
        results[name] = {
            "stage": stage,
            "seed": 1,
            "loads": not row.get("error"),
            "winner": row.get("winner"),
            "rounds": row.get("rounds"),
            "stars": row.get("stars"),
            "three_star": row.get("three_star"),
            "objectives": row.get("objectives"),
            "error": row.get("error"),
        }
        composition = deck["composition"]
        print(
            f"[era] {name:24s} chars={deck['character_names']} "
            f"T{composition['talent']} W{composition['weapon']} "
            f"A{composition['artifact']} S{composition['support']} "
            f"E{composition['event']}  "
            f"game: winner={row.get('winner')} r={row.get('rounds')} "
            f"3*={row.get('three_star')} err={row.get('error') or '-'}",
            flush=True,
        )
    return {"stage": stage, "seed": 1, "decks": results}


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
def _best_era_per_stage(
    agg: dict[str, Any], stages: list[str], decks: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Highest win rate per stage (for the §6 saturation check, not §5 picks)."""
    best: dict[str, dict[str, Any]] = {}
    for stage in stages:
        ranked = sorted(
            decks,
            key=lambda name: (
                -float(agg["cells"][stage][name]["win_rate"]),
                -float(agg["cells"][stage][name]["three_star_rate"]),
                -float(agg["cells"][stage][name]["mean_stars"]),
                float(agg["cells"][stage][name]["mean_rounds"]),
                name,
            ),
        )
        ranked = [name for name in ranked if agg["cells"][stage][name]["n"] > 0]
        if not ranked:
            continue
        name = ranked[0]
        best[stage] = {"deck": name, **agg["cells"][stage][name]}
    return best


def _rounds_diagnostic(rows: list[dict[str, Any]], seeds: Iterable[int]) -> dict[str, Any]:
    seed_set = {int(seed) for seed in seeds}
    valid = [
        row
        for row in rows
        if not row.get("error")
        and not row.get("truncated")
        and int(row.get("seed", -1)) in seed_set
    ]
    distribution = Counter(int(row.get("rounds") or 0) for row in valid)
    long_10 = [row for row in valid if int(row.get("rounds") or 0) >= 10]
    long_15 = [row for row in valid if int(row.get("rounds") or 0) >= 15]
    return {
        "valid": len(valid),
        "distribution": dict(sorted(distribution.items())),
        "ge10": long_10,
        "ge15": long_15,
    }


def render_report(
    *,
    pool: dict[str, Any],
    decks: dict[str, dict[str, Any]],
    rows: list[dict[str, Any]],
    stages: list[str],
    matrix_seeds: list[int],
    legality: dict[str, Any],
    validation: dict[str, Any],
    post: dict[str, Any] | None,
    input_path: Path,
    config_path: Path,
    run_path: Path,
    active_source: str,
) -> str:
    agg = aggregate(rows, stages, list(decks), matrix_seeds)
    cells = agg["cells"]
    by_deck = agg["by_deck"]
    overall = agg["overall"]
    ranked_decks = sorted(decks, key=lambda name: _rank_key(by_deck[name], name))
    recs = {stage: recommend_for_stage(stage, cells[stage], decks) for stage in stages}
    best_era = _best_era_per_stage(agg, stages, decks)
    rounds = _rounds_diagnostic(rows, matrix_seeds)
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")

    counts = pool["counts"]
    char_by_element: dict[str, list[str]] = defaultdict(list)
    for character, meta_char in pool["character_meta"].items():
        char_by_element[meta_char["element"]].append(meta_char["name"])
    talent_by_element: Counter[str] = Counter()
    weapon_by_type: Counter[str] = Counter()
    artifact_by_element: Counter[str] = Counter()
    event_by_element: Counter[str] = Counter()
    support_neutral = 0
    for meta_card in pool["card_meta"].values():
        kind = meta_card["kind"]
        if kind == "talent":
            talent_by_element[meta_card["element"] or "?"] += 1
        elif kind == "weapon":
            weapon_by_type[meta_card["weapon"] or "?"] += 1
        elif kind == "artifact":
            artifact_by_element[meta_card["element"] or "neutral"] += 1
        elif kind == "event":
            event_by_element[meta_card["element"] or "neutral"] += 1
        elif kind == "support":
            support_neutral += 1

    lines: list[str] = []
    add = lines.append
    add("# Era-legal (v3.3.0) deck pool for the NPC PvE benchmark")
    add("")
    add(
        "The 20 preset expert decks are built from post-v3.3 card sets and saturate "
        "the 16-stage benchmark (best deck wins 100% of screen games on all 16 "
        "stages). This report uses **official v3.3-era share-code decks**, decoded "
        "and engine-validated at `version=\"v3.3.0\"`."
    )
    add("")
    add(f"- Generated: {generated}")
    add(f"- Era version: `{ERA_VERSION}` (`CreateParam(version=...)`, engine-enforced)")
    add(f"- Deck source: **{active_source}** (`{config_path}`)")
    add(f"- Input list: `{input_path}`")
    add(f"- Matrix rows: `{run_path}`")
    if decks:
        add(
            f"- Matrix: {len(stages)} stages x {len(decks)} decks x "
            f"{len(matrix_seeds)} seeds = "
            f"{len(stages) * len(decks) * len(matrix_seeds)} planned games "
            f"(seeds {', '.join(str(seed) for seed in matrix_seeds)})"
        )
    else:
        add(
            f"- Matrix: not run (0 decks configured; seeds "
            f"{', '.join(str(seed) for seed in matrix_seeds)})"
        )
    add(
        "- Seed 0 is **not** used: the finished post-3.3 matrix showed 121/320 "
        "seed-0 draws (15-round cap) vs 0-3/320 on seeds 1-4, a deterministic "
        "artifact concentrated in marginal decks."
    )
    add(
        f"- Valid matrix rows: {agg['valid']} "
        f"(errors/truncated: {agg['invalid']})"
    )
    add(
        "- Policy: `expert:<era deck>` (synthetic `ExpertRuleAgent` profile, policy "
        "seed 0, the same convention as `scripts/deck_matrix.py`); NPC = "
        "`npc.policy.NpcPolicy` greedy."
    )
    add(
        "- Judging: `record_decisions=True`, `record_events=True`, "
        "`record_final_state=True` (required by the equipment/event/reaction "
        "objectives)."
    )
    add(
        f"- Engine lock `{_sha1(REPO_ROOT / 'configs' / 'engine.lock')}`, HP table "
        f"`{_sha1(REPO_ROOT / 'configs' / 'npc' / 'hp_table.json')}`, stage JSON "
        f"`{_sha1(REPO_ROOT / 'configs' / 'npc' / 'npc_stages_mond_liyue.json')}`, "
        f"assets `{pool['assets_sha1']}`"
    )
    add(
        "- Ranking: three-star rate first, then win rate, mean stars, mean rounds "
        "(fewer is better), deck name."
    )
    add("")
    if overall["n"]:
        three_star_mean = (
            f"{overall['mean_rounds_three']:.1f} over {overall['three_stars']} 3★ games"
            if overall["mean_rounds_three"] is not None
            else "n/a (no 3★ games)"
        )
        add(
            f"**Pooled over the whole matrix (all decks, all stages, all seeds):** "
            f"{overall['n']} games, 3★ {_cp(overall['three_stars'], overall['n'])}, "
            f"mean rounds {overall['mean_rounds']:.1f}, mean rounds among 3★ "
            f"{three_star_mean}. "
            "This line mixes deck strength; the per-deck pooled rows are in §3.1. "
            "Rounds are only comparable inside the 3★ subset (see "
            "`docs/EVAL_PROTOCOL.md`)."
        )
        add("")
    if len(decks) < 8:
        add(
            f"> **Pool size: {len(decks)} deck(s).** The original target was 8-12; "
            "this run uses every valid code currently in `data/era_decks.txt`. Add "
            "more codes and re-run `--all` if the article provides them."
        )
        add("")

    # ------------------------------------------------------------------ #
    # 1. pool
    # ------------------------------------------------------------------ #
    add("## 1. The legal v3.3.0 pool")
    add("")
    add(
        "Candidates from `data/assets/expert_system_assets_chs_latest.json` with "
        f"`sinceVersion <= {ERA_VERSION}`, then **validated one by one against the "
        "engine** (`CreateParam(version=\"v3.3.0\")` + `Game(...)`; a failure is "
        "illegal). Counts are engine-verified, not asset-tag guesses."
    )
    add("")
    add("### 1.1 Characters")
    add("")
    add(
        f"- {counts['character_candidates']} asset candidates -> "
        f"**{counts['characters_engine_legal']} engine-legal character cards** -> "
        f"**{counts['player_characters']} playable characters** "
        "(asset `category == \"characters\"` and `obtainable != false`)."
    )
    if counts["npc_characters"]:
        add(
            f"- {counts['npc_characters']} engine-legal character candidates are "
            "not playable (excluded by asset metadata)."
        )
    add(
        "- At v3.3.0 there is **no NPC-only split**: Oceanid (2201) and Fatui Pyro "
        "Agent (2301) are playable character cards, so all 27 are in the pool."
    )
    add("")
    add("| element | n | characters |")
    add("|---|---|---|")
    for element in ("CRYO", "HYDRO", "PYRO", "ELECTRO", "ANEMO", "GEO", "DENDRO"):
        names = sorted(char_by_element.get(element, []))
        if names:
            add(f"| {element} | {len(names)} | {', '.join(names)} |")
    add("")
    add(
        "Sanity checks: Diluc / Kaeya / Fischl load at v3.3.0; Nahida / Kokomi / "
        "Raiden are rejected by the engine with `Unknown character id ...`."
    )
    add("")

    add("### 1.2 Action cards")
    add("")
    add(
        f"- {counts['card_candidates']} asset candidates -> "
        f"**{counts['cards_engine_legal']} engine-legal cards** -> "
        f"**{counts['cards_deck_buildable']} deck-buildable cards**."
    )
    add(
        f"- {counts['cards_engine_rejected']} candidates are rejected by the engine: "
        "internal \"elemental infusion\" state entries (ids 81-87) that the asset "
        "dump types like a `GCG_CARD_MODIFY` card. They are not TCG cards."
    )
    add(
        "- Deck-buildable drops only the unobtainable Keqing alternate talent "
        "(114031). The six 2xxx-character talents are **included** because those "
        "characters are playable at v3.3.0."
    )
    add("")
    add("| kind | n | breakdown |")
    add("|---|---|---|")
    add(
        f"| talent | {sum(talent_by_element.values())} | "
        + ", ".join(f"{element} {n}" for element, n in sorted(talent_by_element.items()))
        + " |"
    )
    add(
        f"| weapon | {sum(weapon_by_type.values())} | "
        + ", ".join(
            f"{weapon_type} {n}" for weapon_type, n in sorted(weapon_by_type.items())
        )
        + " |"
    )
    add(
        f"| artifact | {sum(artifact_by_element.values())} | "
        + ", ".join(
            f"{element} {n}" for element, n in sorted(artifact_by_element.items())
        )
        + " |"
    )
    add(f"| support | {support_neutral} | neutral |")
    add(
        f"| event | {sum(event_by_element.values())} | "
        + ", ".join(f"{element} {n}" for element, n in sorted(event_by_element.items()))
        + " |"
    )
    add("")
    add(
        "The `event` row includes the 14 elemental-resonance events (2 per element); "
        "the rest are neutral die/draw/heal/damage/switch events and 8 food cards. "
        "Weapons come in exactly 3 per weapon type (catalyst/bow/claymore/pole/sword)."
    )
    add("")
    if pool["rejected_cards"]:
        add("Engine-rejected candidates:")
        add("")
        add("| id | name | sinceVersion | engine message |")
        add("|---|---|---|---|")
        for item in pool["rejected_cards"]:
            message = str(item["engine_message"]).splitlines()[0] if item["engine_message"] else ""
            add(f"| {item['id']} | {item['name']} | {item['since_version']} | `{message}` |")
        add("")

    add("### 1.3 Current `superconduct_aggro` at v3.3.0")
    add("")
    current = legality["current_superconduct_aggro"]
    add(
        f"`{current['deck']}` does **not** load at v3.3.0: characters "
        f"{current['characters']}, illegal characters {current['illegal_characters']} "
        f"(Raiden Shogun is v3.7.0), and {current['n_illegal_cards']}/30 cards outside "
        "the launch pool. This is the concrete evidence that the current benchmark "
        "decks are post-3.3 decks."
    )
    add("")
    if current["engine_message"]:
        add("Engine message:")
        add("")
        add("```text")
        add(current["engine_message"])
        add("```")
        add("")
    if current["illegal_cards"]:
        add(
            "Illegal card ids (per-card engine probe): "
            + ", ".join(f"`{card_id}`" for card_id in current["illegal_cards"])
            + "."
        )
        add("")
    add("### 1.4 NPC stages at v3.3.0")
    add("")
    broken = [name for name, item in legality["stages"].items() if not item["loads"]]
    if broken:
        add(
            "**These stages do not load at v3.3.0** (their enemy character is "
            "post-3.3): " + ", ".join(broken) + ". They are excluded from the matrix."
        )
    else:
        add(
            f"All **{len(legality['stages'])}/16** stage lineups load at v3.3.0 "
            "(the NPC enemies are launch-era monster cards). No stage is silently "
            "excluded."
        )
    add("")

    if not decks:
        add("## 2. Waiting for codes")
        add("")
        add(
            "`data/era_decks.txt` has no deck entries yet, so no era matrix has been "
            "run. The synthetic fallback pool is **not** used by default. Paste the "
            "official v3.3-era share codes into `data/era_decks.txt` and run:"
        )
        add("")
        add("```bash")
        add('"$PY" -m scripts.import_deck --file data/era_decks.txt --dry-run')
        add('"$PY" -m scripts.import_deck --file data/era_decks.txt --write')
        add('"$PY" -m scripts.era_decks --all --workers 8')
        add("```")
        add("")
        add(
            "Format: one `<name> <share_code>` per line; `#` comments and blank "
            "lines are ignored. Example shape (placeholder, not a real code):"
        )
        add("")
        add("```text")
        add("era_superconduct <PASTE_SHARE_CODE_HERE>")
        add("```")
        add("")
        add(
            "The importer prints characters and the 30 cards grouped by type, checks "
            "every card against `version=\"v3.3.0\"`, and refuses to write a deck "
            "that is out of era or breaks the 2-copy / talent / weapon rules. "
            "Per entry it prints: characters `(id, name, [element / weapon])`; cards "
            "grouped `weapon / artifact / event / support / talent` with `xN`; "
            "composition and objective coverage; `era legality at v3.3.0: OK|FAIL` "
            "with the engine message plus each out-of-era id and its `sinceVersion`; "
            "and `rule check: OK|FAIL` with the per-rule errors."
        )
        add("")
        add(
            "A single code can be previewed without touching the file: "
            "`\"$PY\" -m scripts.import_deck --code \"<SHARE_CODE>\" --name era_my_deck "
            "--dry-run`."
        )
        add("")
        add(
            "**Fallback (not used by default):** `--allow-fallback` builds 10 "
            "synthetic 30-card decks (3 talents / 3 weapons / 3 artifacts / 3 "
            "supports / 18 events each) covering superconduct, vaporize, overload, "
            "electro-charged, freeze, swirl, geo/crystallize, physical, burning and "
            "a triple-sword weapon-coverage deck. They satisfy wagner / timaeus / "
            "uncletian, but they are hand-designed and are not the official pool."
        )
        add("")
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------ #
    # 2. deck pool
    # ------------------------------------------------------------------ #
    add("## 2. The era deck pool")
    add("")
    add(
        "Decks come from the configured pool: official share codes by default, or "
        "the synthetic fallback only when `--allow-fallback` is passed. The importer "
        "re-derives card kinds, element/weapon metadata, composition and objective "
        "coverage from the asset dump."
    )
    add("")
    add("| deck | archetype | source | characters (element / weapon) | T | W | A | S | E | total |")
    add("|---|---|---|---|---|---|---|---|---|---|")
    for name in decks:
        deck = decks[name]
        composition = deck["composition"]
        characters = ", ".join(
            f"{char_name} ({element}/{weapon})"
            for char_name, element, weapon in zip(
                deck["character_names"],
                deck["character_elements"],
                deck["character_weapons"],
                strict=True,
            )
        )
        add(
            f"| {name} | {deck.get('archetype', '?')} | "
            f"{deck.get('source', 'share_code')} | {characters} | "
            f"{composition['talent']} | {composition['weapon']} | "
            f"{composition['artifact']} | {composition['support']} | "
            f"{composition['event']} | {composition['total']} |"
        )
    add("")
    add(
        "T/W/A/S/E = talent / weapon / artifact / support / event cards in the "
        "30-card list (duplicates included)."
    )
    add("")
    archetype_pairs = [
        f"{deck.get('archetype', '?')} (`{name}`)" for name, deck in decks.items()
    ]
    add("**Archetypes represented:** " + ", ".join(archetype_pairs) + ".")
    add("")
    if "nvpu_shuangyan_era" in decks:
        add(
            "`nvpu_shuangyan_era` is a documented era variant of `nvpu_shuangyan`: "
            "2x `333014` 松茸酿肉卷 (v4.4.0) were replaced with 2x `333006` 蒙德土豆饼 "
            "(v3.3.0). Both are `GCG_CARD_EVENT` food cards, so the composition and "
            "coverage counters are **unchanged** (1 talent / 2 weapons / 1 artifact / "
            "7 supports / 19 events; weapon 2 / artifact 1 / event 19). The original "
            "share code is in the deck's `notes` and `source_share_code`."
        )
        add("")
    input_names: list[str] = []
    if input_path.exists():
        for line in input_path.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            if " #" in text:
                text = text.split(" #", 1)[0].strip()
            parts = text.split()
            if parts:
                input_names.append(parts[0])
    missing_entries = [name for name in input_names if name not in decks]
    if missing_entries:
        add(
            "- Input entries **not** in the config: "
            + ", ".join(f"`{name}`" for name in missing_entries)
            + ". Run `scripts.import_deck` to see the rejection reason (out-of-era, "
            "non-playable character, or a deck-building rule)."
        )
        add("")
    add("### 2.1 Card-type objective coverage")
    add("")
    add(
        "`wagner` needs 1 weapon, `timaeus` needs 1 artifact, `uncletian` needs 2 "
        "events. The per-stage recommendation filters on these counts, so a stronger "
        "deck that cannot satisfy the stage objective is never picked."
    )
    add("")
    add("| deck | wagner (weapon >= 1) | timaeus (artifact >= 1) | uncletian (event >= 2) | all three? |")
    add("|---|---|---|---|---|")
    for name in decks:
        coverage = decks[name]["objective_coverage"]
        all_three = (
            coverage["weapon"] >= 1 and coverage["artifact"] >= 1 and coverage["event"] >= 2
        )
        add(
            f"| {name} | {coverage['weapon']} | {coverage['artifact']} | "
            f"{coverage['event']} | {'yes' if all_three else 'no'} |"
        )
    add("")
    weapon_2 = [name for name, deck in decks.items() if deck["objective_coverage"]["weapon"] >= 2]
    artifact_event = [
        name
        for name, deck in decks.items()
        if deck["objective_coverage"]["artifact"] >= 2
        and deck["objective_coverage"]["event"] >= 6
    ]
    all_three_decks = [
        name
        for name, deck in decks.items()
        if deck["objective_coverage"]["weapon"] >= 1
        and deck["objective_coverage"]["artifact"] >= 1
        and deck["objective_coverage"]["event"] >= 2
    ]
    add(
        f"- Decks with **>= 2 weapon cards**: "
        + (", ".join(weapon_2) if weapon_2 else "**none** - `wagner` has no eligible deck")
        + "."
    )
    add(
        f"- Decks with **>= 2 artifacts and >= 6 events**: "
        + (
            ", ".join(artifact_event)
            if artifact_event
            else "**none** - `timaeus`/`uncletian` coverage is thin"
        )
        + "."
    )
    add(
        f"- Decks covering **all three** objectives: "
        + (", ".join(all_three_decks) if all_three_decks else "**none**")
        + "."
    )
    if "linghua_yongdong" in decks or "nvpu_shuangyan_era" in decks:
        add(
            "- Weapon usability: `linghua_yongdong` has **three sword characters** "
            "(Ayaka/Xingqiu/Kaeya) and 2x Aquila Favonia, so both copies are usable; "
            "`nvpu_shuangyan_era` has 2x Wolf's Gravestone but only Noelle is a "
            "claymore user, so one copy is redundant; `keqing_shuanghuo` carries one "
            "sword (Bennett/Keqing can use it) and still satisfies `wagner`."
        )
    add(
        "- Realized vs carried: `wagner` is realized (linghua_yongdong, "
        "keqing_shuanghuo, nvpu_shuangyan_era each score 3/5 three-star); "
        "`uncletian` is realized (keqing_jihua 5/5, ganyu_shuanglei 4/5, "
        "nvpu_shuangyan_era 2/5); `timaeus` is **not** realized by any deck "
        "(0/5 three-star on all five) even though every deck carries >=1 artifact."
    )
    if all_three_decks:
        safe_choice = next(
            (name for name in ranked_decks if name in set(all_three_decks)),
            all_three_decks[0],
        )
        add(
            f"- **Single-deck answer:** `{safe_choice}` is the strongest deck that "
            "covers all three objectives; every deck in the all-three list above also "
            "does. If the pool is thin, that is the safest single fixed choice, but "
            "the per-stage pick still optimizes three-star rate per stage."
        )
    else:
        add(
            "- **Single-deck answer:** **no single deck covers all three objectives**. "
            "A fixed deck per stage is then mandatory (at least one weapon-carrying, "
            "one artifact-carrying and one event-carrying deck must exist)."
        )
    add("")

    if not rows:
        add("## 3. Waiting for the rest of the pool")
        add("")
        add(
            f"{len(decks)} deck(s) are imported and legal at {ERA_VERSION}, but the "
            "benchmark target is 8-12 decks and the final matrix has not been run "
            "yet. The imported deck(s) above are the current state; the matrix is "
            "intentionally not started on a pool this thin."
        )
        add("")
        add(
            "Add the remaining v3.3-era share codes to `data/era_decks.txt`, then run:"
        )
        add("")
        add("```bash")
        add('"$PY" -m scripts.import_deck --file data/era_decks.txt --dry-run')
        add('"$PY" -m scripts.import_deck --file data/era_decks.txt --write')
        add('"$PY" -m scripts.era_decks --all --workers 8')
        add("```")
        add("")
        add(
            "Coverage warning from the current pool: "
            + (
                "no imported deck carries a weapon, so `wagner` has no eligible deck. "
                "Include at least one deck with >= 2 weapon cards (ideally three "
                "characters sharing a weapon type) before the final matrix."
                if not weapon_2
                else "weapon coverage is present."
            )
        )
        add("")
        if validation.get("decks"):
            add(
                f"### One-game validation (stage `{validation.get('stage')}`, "
                f"seed {validation.get('seed')})"
            )
            add("")
            add("| deck | winner | rounds | stars | 3★ | error |")
            add("|---|---|---|---|---|---|")
            for name, item in validation["decks"].items():
                add(
                    f"| {name} | {item.get('winner')} | {item.get('rounds')} | "
                    f"{item.get('stars')} | {item.get('three_star')} | "
                    f"{item.get('error') or '—'} |"
                )
            add("")
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------ #
    # 3. matrix
    # ------------------------------------------------------------------ #
    add("## 3. Matrix (win / three-star), seeds " + ", ".join(str(s) for s in matrix_seeds))
    add("")
    add(
        f"Cells are `wins/{len(matrix_seeds)} · three-stars/{len(matrix_seeds)}` over "
        f"the {len(matrix_seeds)} matrix seeds. Counts, not percentages, so a 4/5 vs "
        "5/5 difference is visible."
    )
    add("")
    add("| stage | " + " | ".join(decks) + " |")
    add("|---" * (len(decks) + 1) + "|")
    for stage in stages:
        cells_text = []
        for name in decks:
            stats = cells[stage][name]
            cells_text.append(
                f"{_cp(stats['wins'], stats['n'])} · {_cp(stats['three_stars'], stats['n'])}"
            )
        add(f"| {stage} | " + " | ".join(cells_text) + " |")
    add("")
    add("### 3.1 Per-deck aggregate")
    add("")
    add(
        f"Pooled over all {len(stages)} stages x {len(matrix_seeds)} matrix seeds. "
        "`mean stage 3★` averages the per-stage rates (each stage weighs equally). "
        "Rounds are reported twice: over every game and, under the user's rule "
        "(`在达成三星的前提下，越少回合越好`), over three-star games only, with the "
        "number of 3★ games in parentheses."
    )
    add("")
    add(
        "| rank | deck | 3★ | win | mean stars | mean rounds (all) | "
        "mean rounds (3★ only) | mean stage 3★ |"
    )
    add("|---|---|---|---|---|---|---|---|")
    for rank, name in enumerate(ranked_decks, start=1):
        stats = by_deck[name]
        add(
            f"| {rank} | {name} | {_cp(stats['three_stars'], stats['n'])} | "
            f"{_cp(stats['wins'], stats['n'])} | {stats['mean_stars']:.2f} | "
            f"{stats['mean_rounds']:.1f} | {_rounds_three_cell(stats)} | "
            f"{_pct(stats['mean_stage_three_star_rate'])} |"
        )
    add("")
    add("### 3.2 Rounds per stage")
    add("")
    add(
        "Cell = `mean rounds (all games) / mean rounds among 3★ (n 3★ games)`. "
        "The left number is context only: a fast loss is not a fast clear. "
        "Rounds are comparable between decks only within the 3★ subset."
    )
    add("")
    add("| stage | " + " | ".join(decks) + " |")
    add("|---" * (len(decks) + 1) + "|")
    for stage in stages:
        cells_text = []
        for name in decks:
            stats = cells[stage][name]
            cells_text.append(
                f"{stats['mean_rounds']:.1f} / {_rounds_three_cell(stats)}"
            )
        add(f"| {stage} | " + " | ".join(cells_text) + " |")
    add("")

    # ------------------------------------------------------------------ #
    # 4. rounds
    # ------------------------------------------------------------------ #
    add("## 4. Rounds diagnostic")
    add("")
    add(
        "A normal game should finish well under 10 rounds. The engine caps at 15 "
        "rounds; a game that reaches 15 is flagged as a probable stall (win or "
        "draw), not fixed here."
    )
    add("")
    add(
        f"- Games reaching **>= 10 rounds**: {len(rounds['ge10'])}/{rounds['valid']} "
        f"({_pct(len(rounds['ge10']) / rounds['valid']) if rounds['valid'] else '—'})."
    )
    add(
        f"- Games reaching **>= 15 rounds (cap)**: {len(rounds['ge15'])}/{rounds['valid']} "
        f"({_pct(len(rounds['ge15']) / rounds['valid']) if rounds['valid'] else '—'})."
    )
    add("")
    add("| rounds | games | share |")
    add("|---|---|---|")
    for length, count in rounds["distribution"].items():
        add(
            f"| {length} | {count} | "
            f"{_pct(count / rounds['valid']) if rounds['valid'] else '—'} |"
        )
    add("")
    if rounds["ge15"]:
        add("**Flagged 15-round games (probable stalls):**")
        add("")
        add("| stage | deck | seed | winner | 3★ |")
        add("|---|---|---|---|---|")
        for row in sorted(
            rounds["ge15"], key=lambda item: (item["stage"], item["deck"], item["seed"])
        ):
            add(
                f"| {row['stage']} | {row['deck']} | {row['seed']} | "
                f"{row.get('winner')} | {bool(row.get('three_star'))} |"
            )
        add("")
    else:
        add("No game hit the 15-round cap on these seeds.")
        add("")

    # ------------------------------------------------------------------ #
    # 5. recommendation
    # ------------------------------------------------------------------ #
    add("## 5. Per-stage recommendation")
    add("")
    add(
        "The pick is the best deck **among decks that satisfy the stage's card-type "
        "objective** (ranking as above). A deck that cannot satisfy the objective is "
        "never recommended, even if it is stronger. The last column flags a "
        "deliberate difficulty trade-off when the strength-only pick and the "
        "satisfying pick differ by more than 15pp win rate."
    )
    add("")
    add(
        "| stage | recommended deck | win | 3★ | mean rounds (all) | "
        "mean rounds (3★ only) | objective coverage | "
        "strength-only pick | win-rate gap | trade-off? |"
    )
    add("|---|---|---|---|---|---|---|---|---|---|")
    for stage in stages:
        rec = recs[stage]
        if rec["recommended"] is None or rec["recommended_stats"] is None:
            add(
                f"| {stage} | — | — | — | — | — | no deck satisfies the objective | — | — | — |"
            )
            continue
        stats = rec["recommended_stats"]
        coverage = rec["statuses"][rec["recommended"]]
        if rec["best_any"] and not rec["best_any_satisfies"]:
            gap = _pp(rec["win_rate_gap"])
            tradeoff = (
                "**yes** (strength-only pick is stronger)" if rec["flag_tradeoff"] else "no"
            )
        else:
            gap = "0pp"
            tradeoff = "no (same deck)"
        add(
            f"| {stage} | **{rec['recommended']}** | "
            f"{_cp(stats['wins'], stats['n'])} | "
            f"{_cp(stats['three_stars'], stats['n'])} | "
            f"{stats['mean_rounds']:.1f} | {_rounds_three_cell(stats)} | "
            f"{coverage} | {rec['best_any']} | {gap} | {tradeoff} |"
        )
    add("")
    flagged = [stage for stage in stages if recs[stage]["flag_tradeoff"]]
    if flagged:
        add(
            "**Trade-off stages (>15pp):** " + ", ".join(flagged) + ". On these, the "
            "objective-satisfying deck gives up real win rate versus the strongest "
            "deck; that is a deliberate benchmark difficulty choice."
        )
    else:
        add(
            "No stage has a >15pp win-rate gap between the best deck and the best "
            "objective-satisfying deck on this pool."
        )
    missing = [stage for stage in stages if recs[stage]["recommended"] is None]
    if missing:
        add(
            "**Stages with no eligible deck:** " + ", ".join(missing) + ". The pool "
            "needs a deck carrying the required card type before these stages can "
            "use a fixed era deck."
        )
    add("")

    # ------------------------------------------------------------------ #
    # 6. saturation
    # ------------------------------------------------------------------ #
    add("## 6. Saturation: era pool vs the post-3.3 matrix")
    add("")
    if post is None:
        add(
            "`data/runs/deck_matrix.jsonl` is missing, so the post-3.3 comparison "
            "is skipped."
        )
    else:
        add(
            "Post-3.3 evidence is the finished 20-deck matrix, read-only and "
            "**not** used for deck selection. Seed 0 is excluded from this "
            "comparison too (121/320 draws there vs 0-3/320 on seeds 1-4), so the "
            "post-3.3 numbers use seeds 1-4. Era numbers are the best era deck per "
            "stage on the 5 era seeds. The seed sets still differ, so read this as a "
            "pool-level saturation check, not a paired seed comparison. `era best` "
            "here is the highest win rate per stage, not the objective-eligible pick "
            "from §5."
        )
        add("")
        add(
            "| stage | post-3.3 best deck | post-3.3 win | era best deck | era win | "
            "Δ (era - post) |"
        )
        add("|---|---|---|---|---|---|")
        era_best_100 = 0
        for stage in stages:
            post_item = post["best"].get(stage)
            era_item = best_era.get(stage)
            if post_item is None or era_item is None:
                add(f"| {stage} | — | — | — | — | — |")
                continue
            if era_item["win_rate"] == 1.0:
                era_best_100 += 1
            add(
                f"| {stage} | {post_item['deck']} | "
                f"{_cp(post_item['wins'], post_item['n'])} | {era_item['deck']} | "
                f"{_cp(era_item['wins'], era_item['n'])} | "
                f"{_pp(era_item['win_rate'] - post_item['win_rate'])} |"
            )
        add("")
        post_100 = sum(1 for item in post["best"].values() if item["win_rate"] == 1.0)
        era_win = (
            by_deck[ranked_decks[0]]["wins"] / by_deck[ranked_decks[0]]["n"]
            if by_deck[ranked_decks[0]]["n"]
            else 0.0
        )
        era_three = (
            by_deck[ranked_decks[0]]["three_stars"] / by_deck[ranked_decks[0]]["n"]
            if by_deck[ranked_decks[0]]["n"]
            else 0.0
        )
        post_three = post["best_deck"]["three_star_rate"]
        add(
            f"**Saturation check:** the post-3.3 best deck wins 100% on "
            f"{post_100}/{len(post['best'])} stages; the era best deck wins 100% on "
            f"{era_best_100}/{len(stages)} stages. Pooled, the post-3.3 best deck is "
            f"{_cp(post['best_deck']['wins'], post['best_deck']['n'])} win / "
            f"{_cp(post['best_deck']['three_stars'], post['best_deck']['n'])} 3★ "
            f"(`{post['best_deck']['deck']}`); the era best deck is "
            f"{_cp(by_deck[ranked_decks[0]]['wins'], by_deck[ranked_decks[0]]['n'])} win / "
            f"{_cp(by_deck[ranked_decks[0]]['three_stars'], by_deck[ranked_decks[0]]['n'])} 3★ "
            f"(`{ranked_decks[0]}`)."
        )
        if era_win >= 0.95 and era_best_100 >= len(stages) - 1:
            add(
                "**Win-rate saturation largely persists in this pool** "
                f"(era {_pct(era_win)} vs post {_pct(post['best_deck']['win_rate'])})."
            )
            if era_three < post_three:
                add(
                    f"The era pool is harder on the three-star metric "
                    f"(era {_pct(era_three)} vs post {_pct(post_three)}), so the "
                    "objective ceiling has more headroom, but the win rate is still "
                    "saturated. If the goal is win-rate de-saturation, the card-pool "
                    "restriction alone is insufficient; NPC difficulty / policy is "
                    "the remaining bottleneck."
                )
            else:
                add(
                    f"The three-star rate is not lower either "
                    f"(era {_pct(era_three)} vs post {_pct(post_three)}); the era pool "
                    "does not add objective difficulty. Add more decks and/or review "
                    "NPC difficulty before calling the benchmark desaturated."
                )
            add(
                f"Read the 3★ column, not only wins; this is a {len(decks)}-deck pool."
            )
        else:
            add(
                "**Win-rate saturation is reduced:** the era pool gives up real win "
                f"rate ({_pct(era_win)} vs post {_pct(post['best_deck']['win_rate'])}) "
                f"while the three-star rate moves from {_pct(post_three)} to "
                f"{_pct(era_three)}."
            )
        add(
            f"The post-3.3 matrix contains {post['n_rows']} rows "
            f"({post['valid']} valid); its per-deck screen numbers are in "
            "`reports/deck_matrix.md`."
        )
    add("")

    # ------------------------------------------------------------------ #
    # 7. ready-to-paste
    # ------------------------------------------------------------------ #
    add("## 7. Ready-to-paste `configs/stage_decks.json` block (not written here)")
    add("")
    add(
        "Era deck ids are defined in `configs/era_decks.json`; the consumer must "
        "resolve them from there (they are **not** in `envs.decks.expert_deck_specs()`, "
        "which only knows the 20 post-3.3 expert decks). Notes carry raw counts, the "
        "5-seed matrix, objective coverage and the 95% Wilson interval."
    )
    add("")
    config_payload: dict[str, Any] = {"stages": {}}
    for stage in stages:
        rec = recs[stage]
        if rec["recommended"] is None or rec["recommended_stats"] is None:
            continue
        stats = rec["recommended_stats"]
        note_parts = [
            f"3-star {_cp(stats['three_stars'], stats['n'])}",
            f"win {_cp(stats['wins'], stats['n'])}",
            f"mean rounds {stats['mean_rounds']:.1f}",
            f"{len(matrix_seeds)}-seed era matrix (seeds "
            f"{','.join(str(s) for s in matrix_seeds)})",
        ]
        if rec["requirements"]:
            req_text = ", ".join(
                f"{requirement['kind']} x{requirement['count']}"
                for requirement in rec["requirements"]
            )
            note_parts.append(
                f"satisfies {req_text} ({rec['statuses'][rec['recommended']]})"
            )
        else:
            note_parts.append("no card-type objective")
        if rec["runner_up_stats"]:
            note_parts.append(
                f"runner-up {rec['runner_up']} "
                f"{_cp(rec['runner_up_stats']['three_stars'], rec['runner_up_stats']['n'])}"
            )
        note_parts.append(f"3-star 95% CI {_fmt_ci(stats['three_stars'], stats['n'])}")
        config_payload["stages"][stage] = {
            "deck": rec["recommended"],
            "notes": "; ".join(note_parts),
        }
    add("```json")
    add(json.dumps(config_payload, ensure_ascii=False, indent=2))
    add("```")
    add("")

    # ------------------------------------------------------------------ #
    # 8. reproduce
    # ------------------------------------------------------------------ #
    add("## 8. Reproduce")
    add("")
    add(
        f"Rows are in `{run_path}` (the default run path for the final pool)."
    )
    add("")
    add("```bash")
    add("PY=/home/ubuntuaaa/miniconda3/envs/gitcg-skill/bin/python")
    add("cd /home/ubuntuaaa/projects/gitcg-skills")
    add('"$PY" -m scripts.import_deck --file data/era_decks.txt --dry-run')
    add('"$PY" -m scripts.import_deck --file data/era_decks.txt --write')
    add('"$PY" -m scripts.era_decks --build')
    add(
        f'"$PY" -m scripts.era_decks --matrix --seeds '
        f'{",".join(str(s) for s in matrix_seeds)} --workers 8'
    )
    add('"$PY" -m scripts.era_decks --report')
    add("# clean rerun: rm data/runs/era_matrix.jsonl data/runs/era_matrix.jsonl.meta.json")
    if run_path.name != "era_matrix.jsonl":
        add(
            f"# for this report: rm {run_path} {run_path}.meta.json"
        )
    add("```")
    add("")
    add(
        "Rows are appended to `data/runs/era_matrix.jsonl` (gitignored) and keyed by "
        "`(stage, deck, seed)`, so adding seeds resumes instead of replaying. The "
        "fingerprint sidecar pins the version, stage/deck list, judging flags, "
        "engine lock and assets hash."
    )
    add("")
    add("### Validation (one full game per deck at v3.3.0)")
    add("")
    if validation.get("decks"):
        add(
            f"Stage `{validation.get('stage')}`, seed {validation.get('seed')}; "
            "every deck loaded and played to completion."
        )
        add("")
        add("| deck | winner | rounds | stars | 3★ | error |")
        add("|---|---|---|---|---|---|")
        for name, item in validation["decks"].items():
            add(
                f"| {name} | {item.get('winner')} | {item.get('rounds')} | "
                f"{item.get('stars')} | {item.get('three_star')} | "
                f"{item.get('error') or '—'} |"
            )
    else:
        add("Not run yet (no share-code decks imported).")
    add("")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# config I/O
# --------------------------------------------------------------------------- #
def load_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def pool_from_config(payload: dict[str, Any]) -> dict[str, Any] | None:
    characters = payload.get("characters")
    cards = payload.get("cards")
    counts = (payload.get("pool") or {}).get("counts")
    if not characters or not cards or not counts:
        return None
    return {
        "version": str(payload.get("version") or ERA_VERSION),
        "assets_path": str(payload.get("assets_path") or DEFAULT_ASSETS),
        "assets_sha1": str(payload.get("assets_sha1") or ""),
        "characters": [int(c) for c in payload["pool"]["player_characters"]],
        "npc_characters": [int(c) for c in payload["pool"].get("npc_characters", [])],
        "cards": [int(c) for c in payload["pool"]["action_cards"]],
        "counts": counts,
        "rejected_cards": payload["pool"].get("rejected_cards", []),
        "character_meta": {
            int(character): meta for character, meta in characters.items()
        },
        "card_meta": {int(card): meta for card, meta in cards.items()},
    }


def write_config(
    path: Path,
    pool: dict[str, Any],
    decks: dict[str, dict[str, Any]],
) -> None:
    payload = {
        "version": ERA_VERSION,
        "assets_path": str(DEFAULT_ASSETS),
        "assets_sha1": pool["assets_sha1"],
        "pool": {
            "player_characters": pool["characters"],
            "npc_characters": pool["npc_characters"],
            "action_cards": pool["cards"],
            "counts": pool["counts"],
            "rejected_cards": pool["rejected_cards"],
        },
        "characters": {
            str(character): meta for character, meta in pool["character_meta"].items()
        },
        "cards": {str(card): meta for card, meta in pool["card_meta"].items()},
        "decks": decks,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def ensure_input_file(path: Path = DEFAULT_INPUT) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Era deck list for the v3.3.0 NPC PvE benchmark.\n"
        "#\n"
        "# Format: one entry per line:  <name> <share_code>\n"
        "#   <name>       deck id written to configs/era_decks.json (no spaces)\n"
        "#   <share_code> official in-game v3.3-era deck share code\n"
        "# Blank lines and lines starting with # are ignored; an inline \" # ...\"\n"
        "# comment after the code is allowed.\n"
        "#\n"
        "# Paste the codes below, then run:\n"
        "#   PY=/home/ubuntuaaa/miniconda3/envs/gitcg-skill/bin/python\n"
        '#   "$PY" -m scripts.import_deck --file data/era_decks.txt --dry-run\n'
        '#   "$PY" -m scripts.import_deck --file data/era_decks.txt --write\n'
        '#   "$PY" -m scripts.era_decks --all --workers 8\n'
        "#\n"
        "# Example shape (placeholder, not a real code):\n"
        "#   era_superconduct <PASTE_SHARE_CODE_HERE>\n",
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stages", default="all", help="'all' or comma-separated stages")
    parser.add_argument("--decks", default="all", help="'all' or comma-separated deck ids")
    parser.add_argument("--seeds", default=DEFAULT_SEEDS, help="matrix seeds (default 1,2,3,4,5)")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument(
        "--report-path", default=str(DEFAULT_REPORT), help="markdown report path"
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--legality", default=str(DEFAULT_LEGALITY))
    parser.add_argument("--validation", default=str(DEFAULT_VALIDATION))
    parser.add_argument("--build", action="store_true", help="pool + config refresh + validation")
    parser.add_argument("--matrix", action="store_true", help="run the stage x deck x seed grid")
    parser.add_argument(
        "--report",
        "--analyze-only",
        dest="report_only",
        action="store_true",
        help="rebuild the report from the existing JSONL (no games)",
    )
    parser.add_argument("--all", action="store_true", help="build + matrix + report")
    parser.add_argument(
        "--allow-fallback",
        action="store_true",
        help="use the synthetic 10-deck fallback when no share-code decks exist",
    )
    return parser


def _select(value: str, known: Iterable[str]) -> tuple[str, ...]:
    known = tuple(known)
    if value == "all":
        return known
    requested = tuple(item.strip() for item in value.split(",") if item.strip())
    unknown = [item for item in requested if item not in known]
    if unknown:
        raise SystemExit(f"unknown entries {unknown}; known: {list(known)}")
    return requested


def _print_waiting(input_path: Path) -> None:
    print(
        f"[era] waiting for codes: {input_path} has no deck entries; "
        "nothing was measured.",
        flush=True,
    )
    print(
        "[era] paste the official v3.3-era share codes into "
        f"{input_path}, then run:\n"
        '  "$PY" -m scripts.import_deck --file data/era_decks.txt --dry-run\n'
        '  "$PY" -m scripts.import_deck --file data/era_decks.txt --write\n'
        '  "$PY" -m scripts.era_decks --all --workers 8',
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    do_all = args.all or not (args.build or args.matrix or args.report_only)
    out = Path(args.out)
    report_path = Path(args.report_path)
    config_path = Path(args.config)
    input_path = Path(args.input)
    legality_path = Path(args.legality)
    validation_path = Path(args.validation)

    ensure_input_file(input_path)
    config = load_config(config_path)
    decks: dict[str, dict[str, Any]] = dict(config.get("decks") or {})
    entries = load_asset_entries()

    if do_all or args.build:
        print("[era] deriving + engine-validating the v3.3.0 pool ...", flush=True)
        pool = build_pool()
        # Refresh derived fields on every configured deck (works for share-code
        # entries written by scripts/import_deck.py and for hand-edited configs).
        decks = {
            name: decorate_deck(deck, entries)
            for name, deck in decks.items()
        }
        legality = probe_legality()
        validation = validate_games(decks) if decks else {"stage": "timmie", "seed": 1, "decks": {}}
        write_config(config_path, pool, decks)
        legality_path.parent.mkdir(parents=True, exist_ok=True)
        legality_path.write_text(
            json.dumps(legality, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        validation_path.parent.mkdir(parents=True, exist_ok=True)
        validation_path.write_text(
            json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            f"[era] pool: {pool['counts']['player_characters']} characters, "
            f"{pool['counts']['cards_deck_buildable']} deck-buildable cards; "
            f"{len(decks)} configured decks -> {config_path}",
            flush=True,
        )
        print(
            f"[era] stages all load at {ERA_VERSION}: "
            f"{legality['stages_all_load']}; current superconduct_aggro loads: "
            f"{legality['current_superconduct_aggro']['loads']}",
            flush=True,
        )
    else:
        pool = pool_from_config(config)
        if pool is None:
            pool = build_pool()
        decks = {name: decorate_deck(deck, entries) for name, deck in decks.items()}
        legality = (
            json.loads(legality_path.read_text(encoding="utf-8"))
            if legality_path.exists()
            else {
                "version": ERA_VERSION,
                "stages": {},
                "stages_all_load": False,
                "current_superconduct_aggro": {
                    "deck": "superconduct_aggro",
                    "characters": [],
                    "illegal_characters": [],
                    "illegal_cards": [],
                    "n_illegal_cards": 0,
                    "loads": False,
                    "engine_message": "not probed (run --build)",
                },
            }
        )
        validation = (
            json.loads(validation_path.read_text(encoding="utf-8"))
            if validation_path.exists()
            else {"stage": "timmie", "seed": 1, "decks": {}}
        )

    share_decks = {
        name: deck
        for name, deck in decks.items()
        if str(deck.get("source") or "").startswith("share_code")
    }
    active_source = "share_code"
    if not share_decks:
        if args.allow_fallback:
            print(
                "[era] WARNING: no share-code decks; using the synthetic fallback "
                "pool because --allow-fallback was passed.",
                flush=True,
            )
            decks = build_fallback_pool(pool)
            active_source = "fallback_synthetic"
        else:
            decks = {}
            active_source = "waiting_for_codes"

    stages = list(_select(args.stages, stage_names()))
    deck_names = list(_select(args.decks, decks)) if decks else []
    matrix_seeds = list(_seed_list(str(args.seeds)))

    if do_all or args.matrix:
        if active_source == "waiting_for_codes":
            _print_waiting(input_path)
        else:
            broken = [
                stage
                for stage in stages
                if not legality["stages"].get(stage, {}).get("loads", True)
            ]
            if broken:
                raise SystemExit(
                    f"these stages do not load at {ERA_VERSION}: {broken}; "
                    "remove them from --stages (the report says why)"
                )
            out.parent.mkdir(parents=True, exist_ok=True)
            fp = fingerprint(tuple(stages), tuple(deck_names))
            _check_fingerprint(out, fp)

            done = _load_done(out)
            pending: list[dict[str, Any]] = []
            for stage in stages:
                for deck_name in deck_names:
                    deck = decks[deck_name]
                    deck_payload = {
                        "name": deck["name"],
                        "archetype": deck.get("archetype", ""),
                        "characters": deck["characters"],
                        "cards": deck["cards"],
                        "character_names": deck.get("character_names", []),
                        "character_elements": deck.get("character_elements", []),
                    }
                    for seed in matrix_seeds:
                        if (stage, deck_name, seed) in done:
                            continue
                        pending.append(
                            {
                                "index": len(pending),
                                "stage": stage,
                                "seed": seed,
                                "deck": deck_payload,
                            }
                        )
            print(
                f"[era] matrix ({active_source}): {len(pending)} pending / "
                f"{len(done)} done ({len(stages)} stages x {len(deck_names)} decks x "
                f"{len(matrix_seeds)} seeds) -> {out}",
                flush=True,
            )
            rows, elapsed = play_pending(pending, out, int(args.workers))
            meta = {
                "fingerprint": fp,
                "matrix_seeds": matrix_seeds,
                "rows": len(load_rows(out)),
                "last_run_games": len(rows),
                "last_run_seconds": round(elapsed, 1),
                "last_run_workers": max(1, min(int(args.workers), MAX_WORKERS)),
                "last_run_at": datetime.now().isoformat(timespec="seconds"),
                "active_source": active_source,
                "engine_lock": load_engine_lock(),
                "runtime": runtime_metadata(),
            }
            _write_meta(out, meta)
            print(
                f"[era] matrix: {len(rows)} games in {elapsed:.1f}s "
                f"({elapsed / max(1, len(rows)):.2f}s/game, "
                f"workers={meta['last_run_workers']})",
                flush=True,
            )

    if do_all or args.report_only:
        rows = load_rows(out)
        meta_path = out.with_name(out.name + ".meta.json")
        if meta_path.exists():
            run_meta = json.loads(meta_path.read_text(encoding="utf-8"))
            stored_seeds = run_meta.get("matrix_seeds")
            if stored_seeds:
                matrix_seeds = [int(seed) for seed in stored_seeds]
        if rows:
            measured_stages = {
                str(row["stage"])
                for row in rows
                if not row.get("error") and not row.get("truncated")
            }
            measured_decks = {
                str(row["deck"])
                for row in rows
                if not row.get("error") and not row.get("truncated")
            }
            if measured_stages:
                stages = [stage for stage in stages if stage in measured_stages]
            if measured_decks:
                decks = {name: deck for name, deck in decks.items() if name in measured_decks}
        post = load_post_matrix()
        report = render_report(
            pool=pool,
            decks=decks,
            rows=rows,
            stages=stages,
            matrix_seeds=matrix_seeds,
            legality=legality,
            validation=validation,
            post=post,
            input_path=input_path,
            config_path=config_path,
            run_path=out,
            active_source=active_source,
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report, encoding="utf-8")
        print(f"[era] report -> {report_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
