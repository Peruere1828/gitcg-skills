"""Passive skill sheets from the engine's GTS character data.

The official asset dump used by ``agents.scripted.assets`` lists active skills
only. Passives live in ``build/engine/packages/data/src/characters/**/*.gts`` as
``define skill { skillType passive ... }`` and are linked to a character through
the ``skills`` list of ``define character``. This module parses that once and
returns ``{skill_id, name, description, cost}`` per character.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_DATA = _REPO_ROOT / "build/engine/packages/data/src/characters"

_BLOCK = re.compile(r"/\*\*(.*?)\*/\s*(.*?)(?=/\*\*|\Z)", re.S)
_ID = re.compile(r"@id\s+(\d+)")
_NAME = re.compile(r"@name\s+(.+)")
_COST = re.compile(r"@cost[^\n]*")
_DESCRIPTION = re.compile(r"@description\s*\n(.*?)(?=\n\s*\*\s*@|\Z)", re.S)
_ALIAS = re.compile(r"id\s+\d+\s+as\s+(\w+)\s*;")
_CHARACTER = re.compile(r"define\s+character\s*\{(.*?)\n?\};", re.S)
_SKILLS = re.compile(r"\bskills\s+(.*?);", re.S)


def _clean_comment(block: str) -> str:
    lines = []
    for line in block.splitlines():
        line = re.sub(r"^\s*\*\s?", "", line).rstrip()
        lines.append(line)
    return "\n".join(lines).strip()


def _data_root() -> Path:
    override = os.environ.get("GITCG_CHARACTER_DATA")
    if override:
        return Path(override)
    return _DEFAULT_DATA


@lru_cache(maxsize=1)
def _index() -> dict[int, tuple[dict[str, Any], ...]]:
    root = _data_root()
    if not root.exists():
        return {}
    passives_by_alias: dict[str, dict[str, Any]] = {}
    characters: dict[int, list[str]] = {}
    for path in root.rglob("*.gts"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for raw_comment, code in _BLOCK.findall(text):
            comment = _clean_comment(raw_comment)
            if "define skill" in code and "skillType passive" in code:
                match_id = _ID.search(comment)
                alias = _ALIAS.search(code)
                if not match_id or not alias:
                    continue
                match_name = _NAME.search(comment)
                match_cost = _COST.search(comment)
                match_desc = _DESCRIPTION.search(comment)
                passives_by_alias[alias.group(1)] = {
                    "id": int(match_id.group(1)),
                    "name": (match_name.group(1).strip() if match_name else ""),
                    "cost": (
                        match_cost.group(0).split("@cost", 1)[1].strip()
                        if match_cost and not match_cost.group(0).strip().endswith("@description")
                        else ""
                    ),
                    "description": (
                        " ".join(match_desc.group(1).split()) if match_desc else ""
                    ),
                }
            if "define character" in code:
                match_id = _ID.search(comment)
                skills = _SKILLS.search(code)
                if not match_id or not skills:
                    continue
                aliases = [
                    item.strip()
                    for item in re.split(r"[,\s]+", skills.group(1))
                    if item.strip()
                ]
                characters[int(match_id.group(1))] = aliases
    result: dict[int, tuple[dict[str, Any], ...]] = {}
    for character_id, aliases in characters.items():
        items = [passives_by_alias[a] for a in aliases if a in passives_by_alias]
        unique = []
        seen = set()
        for item in items:
            key = item["description"].replace("【被动】", "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(item)
        if unique:
            result[character_id] = tuple(unique)
    return result


def passives_for(character_id: int) -> tuple[dict[str, Any], ...]:
    return _index().get(int(character_id), ())
