"""Rule-based deck construction from a card pool (PLAN.md D19 / §4.4 PvE-v1).

``decklab`` currently ships **no learned or agentic deck builder** — see
``decklab/README.md`` ("待办：LLM 提议换卡…"), and :mod:`decklab.sensitivity`
only evaluates single-card swaps inside a fixed 30-card template. The PvE-v1
gate (``PLAN.md`` §4.4 / D19) asks for "decklab 自动组牌（池 C）", so to stay
honest this module provides a **deterministic, rule-based constructor** and the
gate report records the deviation from an "自动组牌 Agent":

* a *meta core* — the pool-eligible subset (with multiplicities) of a fixed
  meta deck (``superconduct_aggro``), the "固定 meta deck" part of the fallback
  wording in the B5 brief;
* a *fill order* — a documented, hand-authored priority list of pool cards
  (tempo/aggro + objective coverage), the "decklab 变体" part.

The result is a legal 30-card deck (``<= 2`` copies per card) drawn **only** from
the pool manifest. Card-kind classification uses the scripted-expert asset
catalog (``agents.scripted.assets``); it never touches the network.

This is intentionally *not* a search/agent: every deck is a pure function of the
(pool, characters, core, fill) inputs, so a gate run is reproducible.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Sequence

from common.paths import REPO_ROOT
from reps.schema import DeckSpec

DEFAULT_POOL_PATH = REPO_ROOT / "configs" / "pools" / "pool_C_v34_mondliyue.json"
MAX_COPIES = 2
DECK_SIZE = 30

# Card-kind buckets (matching ``envs.match._card_kind_from_entity`` semantics).
CARD_KINDS = ("weapon", "artifact", "talent", "support", "event")


@dataclass(frozen=True)
class CardPool:
    name: str
    characters: tuple[int, ...]
    cards: tuple[int, ...]

    def has_character(self, character_id: int) -> bool:
        return int(character_id) in self.characters

    def has_card(self, card_id: int) -> bool:
        return int(card_id) in self.cards


def load_pool(path: str | Path | None = None) -> CardPool:
    """Load a pool manifest (``configs/pools/*.json``): characters + action cards."""
    payload = json.loads(Path(path or DEFAULT_POOL_PATH).read_text(encoding="utf-8"))
    return CardPool(
        name=str(payload.get("pool") or Path(path or DEFAULT_POOL_PATH).stem),
        characters=tuple(int(c) for c in payload.get("characters", ())),
        cards=tuple(int(c) for c in payload.get("cards", ())),
    )


@lru_cache(maxsize=1)
def _asset_catalog():
    from agents.scripted.assets import load_assets

    return load_assets()


@lru_cache(maxsize=1)
def _card_kind_index() -> dict[int, str]:
    """Map every known action card id to ``weapon|artifact|talent|support|event``."""
    index: dict[int, str] = {}
    for card_id, card in _asset_catalog().cards.items():
        tags = set(card.tags)
        if "GCG_TAG_WEAPON" in tags:
            index[int(card_id)] = "weapon"
        elif "GCG_TAG_ARTIFACT" in tags:
            index[int(card_id)] = "artifact"
        elif "GCG_TAG_TALENT" in tags:
            index[int(card_id)] = "talent"
        elif {"GCG_TAG_ALLY", "GCG_TAG_PLACE", "GCG_TAG_ITEM"} & tags:
            index[int(card_id)] = "support"
        else:
            index[int(card_id)] = "event"
    return index


def card_kind(card_id: int) -> str:
    return _card_kind_index().get(int(card_id), "event")


def build_deck(
    pool: CardPool,
    characters: Sequence[int],
    *,
    name: str,
    core: Sequence[int] = (),
    fill: Sequence[int] = (),
    size: int = DECK_SIZE,
    copies: int = MAX_COPIES,
) -> DeckSpec:
    """Assemble a legal ``size``-card deck from ``pool`` for ``characters``.

    ``core`` is expanded at ``copies`` each first, then ``fill``, then the rest of
    the pool in ascending id order. Every card must be in the pool; characters
    must be 3 distinct pool characters.
    """
    characters = tuple(int(c) for c in characters)
    if len(characters) != 3 or len(set(characters)) != 3:
        raise ValueError(f"a deck needs exactly 3 distinct characters, got {characters}")
    for character_id in characters:
        if not pool.has_character(character_id):
            raise ValueError(f"character {character_id} is not in pool {pool.name!r}")
    return DeckSpec(
        name=name,
        characters=characters,
        cards=fill_cards(
            pool, size=size, core=core, fill=fill, copies=copies
        ),
    )


def fill_cards(
    pool: CardPool,
    *,
    size: int = DECK_SIZE,
    core: Sequence[int] = (),
    fill: Sequence[int] = (),
    copies: int = MAX_COPIES,
) -> tuple[int, ...]:
    """按 ``core`` → ``fill`` → 池内顺序补满 ``size`` 张牌（每卡 ≤ ``copies`` 张）。

    只管牌、**不做角色校验**——需要「角色不限 3 个」的牌组填充时用这个，
    `build_deck` 也复用它（它自己负责那条「恰好 3 个不同角色」的校验）。
    """
    ordered: list[int] = []
    for card_id in (*core, *fill):
        card_id = int(card_id)
        if not pool.has_card(card_id):
            raise ValueError(f"card {card_id} is not in pool {pool.name!r}")
        if ordered.count(card_id) < copies:
            ordered.append(card_id)

    # Top up from the rest of the pool until the deck is full. Repeat passes over
    # the pool so a card that is still short of ``copies`` can gain its remaining
    # copies (a single pass added at most one copy per pool card).
    while len(ordered) < size:
        added = False
        for card_id in pool.cards:
            if len(ordered) >= size:
                break
            if ordered.count(int(card_id)) < copies:
                ordered.append(int(card_id))
                added = True
        if not added:
            break

    if len(ordered) < size:
        raise ValueError(
            f"pool {pool.name!r} only yields {len(ordered)} cards at <= {copies} copies; "
            f"need {size}"
        )
    return tuple(ordered[:size])


# --------------------------------------------------------------------------- #
# Pool-C superconduct deck (the PvE-v1 gate contestant)
# --------------------------------------------------------------------------- #
# Meta core: the pool-C-eligible cards (with multiplicity) of the fixed
# ``superconduct_aggro`` expert deck. 17 cards.
POOL_C_META_CORE: tuple[int, ...] = (
    331401, 331401,  # 元素共鸣：交织之雷  (electro resonance: die)
    331402, 331402,  # 元素共鸣：强能之雷  (electro resonance: energy)
    332004, 332004,  # 运筹帷幄          (draw 2)
    332005, 332005,  # 本大爷还没有输！    (omni die + energy)
    332006,           # 交给我吧！        (fast switch)
    332009,           # 白垩之术          (energy transfer)
    333002,           # 仙跳墙            (burst +3 damage)
    312004, 312004,  # 赌徒的耳环        (2 omni on KO)
    312006,           # 流放者头冠        (backline energy after burst)
    322002,           # 凯瑟琳            (fast switch)
    322003, 322003,  # 蒂玛乌斯          (artifact fetch/discount)
)
# Fill: pool-C aggro/tempo cards that also guarantee an equipment objective
# (a sword for 刻晴) is satisfiable.
POOL_C_FILL: tuple[int, ...] = (
    311501, 311501,  # 旅行剑  (weapon, sword -> matches 刻晴)
    332001, 332001,  # 最好的伙伴！(2 omni)
    332008,           # 星天之兆 (energy -> bursts)
    332003, 332003,  # 一掷乾坤 (reroll)
    312005, 312005,  # 教官的帽子 (die on reaction -> superconduct synergy)
    322011, 322011,  # 田铁嘴 (end-phase energy)
    322012, 322012,  # 刘苏 (energy on switch)
)
# The superconduct trio inside pool C: 甘雨 (cryo) + 刻晴/北斗 (electro).
POOL_C_SUPERCONDUCT_CHARACTERS: tuple[int, int, int] = (1101, 1403, 1405)


def build_pool_c_superconduct(
    *,
    pool_path: str | Path | None = None,
    characters: Sequence[int] = POOL_C_SUPERCONDUCT_CHARACTERS,
    name: str = "decklab_poolc_superconduct",
) -> DeckSpec:
    """Build the PvE-v1 contestant deck from pool C (see module docstring)."""
    pool = load_pool(pool_path)
    return build_deck(
        pool,
        characters,
        name=name,
        core=POOL_C_META_CORE,
        fill=POOL_C_FILL,
    )


def deck_summary(deck: DeckSpec) -> dict[str, object]:
    """Small reporting helper: per-kind card counts of a deck."""
    kinds: dict[str, int] = {kind: 0 for kind in CARD_KINDS}
    for card_id in deck.cards:
        kinds[card_kind(card_id)] = kinds.get(card_kind(card_id), 0) + 1
    return {
        "name": deck.name,
        "characters": list(deck.characters),
        "n_cards": len(deck.cards),
        "card_kinds": kinds,
        "cards": list(deck.cards),
    }


__all__ = [
    "CARD_KINDS",
    "CardPool",
    "DECK_SIZE",
    "DEFAULT_POOL_PATH",
    "MAX_COPIES",
    "POOL_C_FILL",
    "POOL_C_META_CORE",
    "POOL_C_SUPERCONDUCT_CHARACTERS",
    "build_deck",
    "build_pool_c_superconduct",
    "card_kind",
    "deck_summary",
    "load_pool",
]
