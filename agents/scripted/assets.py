# Ported from Rebel_base_RL research/world_model (AGPL-3.0, github.com/.../Rebel_base_RL) by genshin-GITCG.
# Keep upstream semantics; adapt imports only. See NOTICE.md.
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from .models import CardInfo, CharacterInfo, SkillInfo

ASSETS_URL = "https://gi-tcg-assets-api-hf.guyutongxue.site/api/v4/data/latest/CHS/all"
ASSETS_HEADERS = {
    "X-Gi-Tcg-Assets-Manager": "1",
    "User-Agent": "genshin-gitcg",
}


def _repo_root() -> Path:
    # agents/scripted/assets.py -> repo root
    return Path(__file__).resolve().parents[2]


def _cache_path() -> Path:
    override = os.environ.get("GITCG_ASSETS_CACHE")
    if override:
        return Path(override)
    return _repo_root() / "data" / "assets" / "expert_system_assets_chs_latest.json"


def _normalize_cost(raw_cost: list[dict[str, Any]] | None) -> tuple[tuple[str, int], ...]:
    if not raw_cost:
        return ()
    return tuple((str(item.get("type", "")), int(item.get("count", 0))) for item in raw_cost)


def _normalize_tags(raw_tags: list[str] | None) -> tuple[str, ...]:
    return tuple(str(tag) for tag in raw_tags or ())


@dataclass(frozen=True)
class AssetCatalog:
    characters: dict[int, CharacterInfo]
    cards: dict[int, CardInfo]
    skills: dict[int, SkillInfo]
    share_to_id: dict[int, int]
    name_to_id: dict[str, int]

    def character(self, definition_id: int) -> CharacterInfo:
        return self.characters[int(definition_id)]

    def card(self, definition_id: int) -> CardInfo:
        return self.cards[int(definition_id)]

    def skill(self, definition_id: int) -> SkillInfo:
        return self.skills[int(definition_id)]

    def element_for_character(self, definition_id: int) -> str:
        character = self.character(definition_id)
        for tag in character.tags:
            if tag.startswith("GCG_TAG_ELEMENT_"):
                return tag
        return ""


def load_assets(*, refresh: bool = False) -> AssetCatalog:
    cache_path = _cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if refresh or not cache_path.exists():
        if os.environ.get("GITCG_ASSETS_OFFLINE") == "1":
            raise FileNotFoundError(
                f"assets cache missing and GITCG_ASSETS_OFFLINE=1: {cache_path}"
            )
        request = Request(ASSETS_URL, headers=ASSETS_HEADERS)
        with urlopen(request, timeout=60) as response:
            payload = json.load(response)
        cache_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    else:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    return _build_catalog(payload)


def _build_catalog(payload: dict[str, Any]) -> AssetCatalog:
    characters: dict[int, CharacterInfo] = {}
    cards: dict[int, CardInfo] = {}
    skills: dict[int, SkillInfo] = {}
    share_to_id: dict[int, int] = {}
    name_to_id: dict[str, int] = {}
    for item in payload.get("data", ()):
        definition_id = item.get("id")
        if not isinstance(definition_id, int):
            continue
        name = str(item.get("name") or item.get("englishName") or definition_id)
        name_to_id[name] = definition_id
        share_id = item.get("shareId")
        if isinstance(share_id, int):
            share_to_id[share_id] = definition_id
        if isinstance(item.get("skills"), list) and "hp" in item and "maxEnergy" in item:
            character_skills: list[int] = []
            for skill in item.get("skills", ()):
                skill_id = skill.get("id")
                if not isinstance(skill_id, int):
                    continue
                character_skills.append(skill_id)
                skills[skill_id] = SkillInfo(
                    id=skill_id,
                    character_id=definition_id,
                    character_name=name,
                    skill_type=str(skill.get("type", "")),
                    name=str(skill.get("name", skill_id)),
                    play_cost=_normalize_cost(skill.get("playCost")),
                    description=str(skill.get("description", "")),
                )
            characters[definition_id] = CharacterInfo(
                id=definition_id,
                share_id=share_id if isinstance(share_id, int) else None,
                name=name,
                tags=_normalize_tags(item.get("tags")),
                skill_ids=tuple(character_skills),
                max_health=int(item.get("hp", 0)),
                max_energy=int(item.get("maxEnergy", 0)),
            )
            continue
        if isinstance(item.get("type"), str) and str(item.get("type")).startswith("GCG_SKILL_TAG_") and "playCost" in item:
            skills[definition_id] = SkillInfo(
                id=definition_id,
                character_id=int(item.get("relatedCharacterId", 0) or 0),
                character_name="",
                skill_type=str(item.get("type", "")),
                name=name,
                play_cost=_normalize_cost(item.get("playCost")),
                description=str(item.get("description", "")),
            )
            continue
        if "playCost" in item:
            cards[definition_id] = CardInfo(
                id=definition_id,
                share_id=share_id if isinstance(share_id, int) else None,
                name=name,
                tags=_normalize_tags(item.get("tags")),
                related_character_id=(
                    int(item["relatedCharacterId"])
                    if isinstance(item.get("relatedCharacterId"), int)
                    else None
                ),
                play_cost=_normalize_cost(item.get("playCost")),
                description=str(item.get("description", "")),
            )
    return AssetCatalog(
        characters=characters,
        cards=cards,
        skills=skills,
        share_to_id=share_to_id,
        name_to_id=name_to_id,
    )
