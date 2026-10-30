"""NPC PvE stage specs + enemy definitions (PLAN.md §4.4, PvE-B3).

This module is the typed, validated view over the two authoritative data files
under ``configs/npc/``:

* ``npc_enemies_all.json`` — the enemy cards that appear in the Mondstadt/Liyue
  "Adventure Challenge" stages (HP / element / energy / A-E-Q / passive), and
* ``npc_stages_mond_liyue.json`` — the 16 stage specs (lineup / reserve /
  special rules / challenge objectives).

It also carries the two things the rest of ``npc/`` needs:

* the **Chinese/English name -> engine character id** mapping, cross-checked
  against each enemy's ``appears_in`` field, and
* a **skill damage table** (skill definition id -> damage) derived from the
  A/E/Q data. Skill definition ids follow the engine convention
  ``character_id * 10 + k`` where ``k`` counts the character's skills in the
  order Normal (A), Elemental (E, may be absent), Burst (Q), Passive; an extra
  prepared skill (e.g. the Fatui Anemoboxer's counter) is appended after the
  passive. ``tests/test_npc_policy.py`` cross-checks the computed ids and damage
  against the engine ``.gts`` sources.

Data provenance (authoritative, see ``configs/npc/README.md``): from
``refs/``/engine data only — nothing here is hand-invented.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from common.paths import REPO_ROOT

DEFAULT_ENEMIES_PATH = REPO_ROOT / "configs" / "npc" / "npc_enemies_all.json"
DEFAULT_STAGES_PATH = REPO_ROOT / "configs" / "npc" / "npc_stages_mond_liyue.json"

# Health of the objectives the data can express (see objective_types in the data).
OBJECTIVE_TYPES = (
    "win",
    "win_within_rounds",
    "defeated_less_than",
    "use_elemental_burst",
    "trigger_elemental_reactions",
    "play_equipment_card",
    "play_event_cards",
)

# Engine character (card) ids for the NPC enemy data slugs. These are the
# official GCG enemy-card ids used by the ported ``.gts`` (PvE-B1) and the two
# upstream Fatui cards; see ``tests/test_npc_enemies.py``.
ENEMY_ENGINE_IDS: dict[str, int] = {
    "hydroslime": 3204,
    "electroslime": 3406,
    "cryoslime": 3107,
    "cryohilichurlshooter": 3102,
    "electrohilichurlshooter": 3402,
    "hilichurlshooter": 3002,
    "hilichurlfighter": 3001,
    "blazingaxemitachurl": 3302,
    "iceshieldwallmitachurl": 3101,
    "cracklingaxemitachurl": 3403,
    "anemosamachurl": 3501,
    "dendrosamachurl": 3703,
    "cryoabyssmage": 3103,
    "pyroabyssmage": 3301,
    "hydroabyssmage": 3201,
    "fatuiskirmisheranemoboxervanguard": 3502,
    "fatuiskirmisherhydrogunnerlegionnaire": 3203,
    "fatuipyroagent": 2301,
    "mirrormaiden": 2202,
}

_SKILL_KINDS = ("A", "E", "Q")


@dataclass(frozen=True)
class NpcSkill:
    kind: str  # "A" | "E" | "Q"
    name: str
    name_cn: str | None
    cost: str | None
    effect: str
    damage: int
    damage_element: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "cost": self.cost,
            "effect": self.effect,
            "damage": self.damage,
            "damage_element": self.damage_element,
        }


@dataclass(frozen=True)
class NpcEnemy:
    id: str
    name: str
    en: str
    element: str
    hp: int
    energy: int
    skills: dict[str, NpcSkill | None]
    extra_skills: tuple[NpcSkill, ...] = ()
    appears_in: tuple[str, ...] = ()
    engine_id: int = 0
    aliases: tuple[str, ...] = ()

    def skill_kind_order(self) -> tuple[str, ...]:
        """The A/E/Q skills present, in engine order (A, E, Q)."""
        return tuple(kind for kind in _SKILL_KINDS if self.skills.get(kind) is not None)

    def skill_definition_ids(self) -> dict[str, int]:
        """Map ``{"A"|"E"|"Q": skill_definition_id}`` for this character."""
        return {
            kind: self.engine_id * 10 + index
            for index, kind in enumerate(self.skill_kind_order(), start=1)
        }

    def extra_skill_definition_ids(self) -> tuple[int, ...]:
        """Ids of appended extra (prepared) skills, after the passive slot.

        The skill slots run A, E, Q, then the passive (if any), then any extra
        prepared skills; so an extra skill occupies ``len(present) + 2`` and on.
        """
        base = len(self.skill_kind_order()) + 2  # the passive occupies +1
        return tuple(self.engine_id * 10 + base + index for index in range(len(self.extra_skills)))

    def damage_by_skill_definition_id(self) -> dict[int, int]:
        """Damage estimate for every actionable skill id of this character."""
        table: dict[int, int] = {}
        for kind, definition_id in self.skill_definition_ids().items():
            skill = self.skills.get(kind)
            table[definition_id] = int(skill.damage) if skill is not None else 0
        for definition_id, skill in zip(self.extra_skill_definition_ids(), self.extra_skills):
            table[definition_id] = int(skill.damage)
        return table

    def skill_kind_by_definition_id(self) -> dict[int, str]:
        table = {
            definition_id: kind
            for kind, definition_id in self.skill_definition_ids().items()
        }
        for definition_id in self.extra_skill_definition_ids():
            table[definition_id] = "X"  # prepared/extra skill
        return table


@dataclass(frozen=True)
class NpcLineupEntry:
    slot: int
    id: str
    name: str
    en: str
    hp: int
    count: int
    engine_id: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot,
            "id": self.id,
            "name": self.name,
            "en": self.en,
            "hp": self.hp,
            "count": self.count,
            "engine_id": self.engine_id,
        }


@dataclass(frozen=True)
class NpcObjective:
    type: str
    text: str = ""
    verified: bool = True
    count: int | None = None
    n: int | None = None
    k: int | None = None
    kind: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "text": self.text,
            "verified": self.verified,
            "count": self.count,
            "n": self.n,
            "k": self.k,
            "kind": self.kind,
        }


@dataclass(frozen=True)
class NpcStage:
    name: str
    display: str
    region: str
    lineup: tuple[NpcLineupEntry, ...]
    reserve: tuple[NpcLineupEntry, ...]
    special_rules: tuple[str, ...]
    objectives: tuple[NpcObjective, ...]
    objectives_verified: bool = True
    display_en: str = ""
    stage_title: str | None = None

    @property
    def deck_characters(self) -> tuple[int, ...]:
        """The NPC's engine character list: the lineup expanded by count."""
        characters: list[int] = []
        for entry in self.lineup:
            characters.extend([entry.engine_id] * int(entry.count))
        return tuple(characters)

    @property
    def reserve_characters(self) -> tuple[int, ...]:
        characters: list[int] = []
        for entry in self.reserve:
            characters.extend([entry.engine_id] * int(entry.count))
        return tuple(characters)

    def objective_of(self, objective_type: str) -> NpcObjective | None:
        for objective in self.objectives:
            if objective.type == objective_type:
                return objective
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display": self.display,
            "region": self.region,
            "lineup": [entry.as_dict() for entry in self.lineup],
            "reserve": [entry.as_dict() for entry in self.reserve],
            "special_rules": list(self.special_rules),
            "objectives": [objective.as_dict() for objective in self.objectives],
            "objectives_verified": self.objectives_verified,
            "deck_characters": list(self.deck_characters),
            "reserve_characters": list(self.reserve_characters),
        }


# --------------------------------------------------------------------------- #
# loading
# --------------------------------------------------------------------------- #
def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _parse_skill(kind: str, payload: dict[str, Any] | None) -> NpcSkill | None:
    if not payload:
        return None
    damage = payload.get("damage")
    return NpcSkill(
        kind=kind,
        name=str(payload.get("name") or ""),
        name_cn=payload.get("name_cn"),
        cost=payload.get("cost"),
        effect=str(payload.get("effect") or ""),
        damage=int(damage) if damage is not None else 0,
        damage_element=payload.get("damage_element"),
    )


@lru_cache(maxsize=4)
def load_enemies(path: str | Path | None = None) -> dict[str, NpcEnemy]:
    """Load the authoritative enemy definitions keyed by data slug."""
    payload = _read_json(Path(path) if path is not None else DEFAULT_ENEMIES_PATH)
    enemies: dict[str, NpcEnemy] = {}
    for entry in payload.get("enemies", ()):
        slug = str(entry["id"])
        skills = {kind: _parse_skill(kind, entry.get("skills", {}).get(kind)) for kind in _SKILL_KINDS}
        extra = tuple(
            NpcSkill(
                kind="X",
                name=str(skill.get("name") or ""),
                name_cn=skill.get("name_cn"),
                cost=skill.get("cost"),
                effect=str(skill.get("effect") or ""),
                damage=int(skill.get("damage") or 0),
                damage_element=skill.get("damage_element"),
            )
            for skill in entry.get("extra_skills", ())
        )
        engine_id = ENEMY_ENGINE_IDS.get(slug)
        if engine_id is None:
            raise KeyError(f"enemy slug {slug!r} has no engine character id in ENEMY_ENGINE_IDS")
        enemies[slug] = NpcEnemy(
            id=slug,
            name=str(entry.get("name") or ""),
            en=str(entry.get("en") or ""),
            element=str(entry.get("element") or ""),
            hp=int(entry.get("hp_card") or 0),
            energy=int(entry.get("energy") or 0),
            skills=skills,
            extra_skills=extra,
            appears_in=tuple(str(stage) for stage in entry.get("appears_in", ())),
            engine_id=int(engine_id),
            aliases=tuple(str(alias) for alias in entry.get("aliases", ())),
        )
    return enemies


@lru_cache(maxsize=4)
def enemy_name_index(enemies_path: str | Path | None = None) -> dict[str, str]:
    """Index from Chinese/English/alias name -> enemy slug (first match wins)."""
    index: dict[str, str] = {}
    for slug, enemy in load_enemies(enemies_path).items():
        for key in (enemy.name, enemy.en, enemy.id, *enemy.aliases):
            if key:
                index.setdefault(key, slug)
    return index


def engine_id_for_name(name: str, *, enemies_path: str | Path | None = None) -> int:
    """Resolve a Chinese (or English/alias) enemy name to its engine card id."""
    slug = enemy_name_index(enemies_path).get(name)
    if slug is None:
        raise KeyError(f"unknown NPC enemy name: {name!r}")
    return int(load_enemies(enemies_path)[slug].engine_id)


def _parse_lineup(entries: Any, *, enemies: dict[str, NpcEnemy]) -> tuple[NpcLineupEntry, ...]:
    parsed: list[NpcLineupEntry] = []
    for entry in entries or ():
        slug = str(entry["id"])
        enemy = enemies.get(slug)
        if enemy is None:
            raise KeyError(f"stage lineup references unknown enemy {slug!r}")
        parsed.append(
            NpcLineupEntry(
                slot=int(entry.get("slot", len(parsed) + 1)),
                id=slug,
                name=str(entry.get("name") or enemy.name),
                en=str(entry.get("en") or enemy.en),
                hp=int(entry.get("hp") or enemy.hp),
                count=int(entry.get("count", 1)),
                engine_id=int(enemy.engine_id),
            )
        )
    return tuple(parsed)


def _parse_objectives(entries: Any) -> tuple[NpcObjective, ...]:
    parsed: list[NpcObjective] = []
    for entry in entries or ():
        objective_type = str(entry.get("type", ""))
        if objective_type not in OBJECTIVE_TYPES:
            raise ValueError(f"unknown objective type: {objective_type!r}")
        parsed.append(
            NpcObjective(
                type=objective_type,
                text=str(entry.get("text") or ""),
                verified=bool(entry.get("verified", False)),
                count=int(entry["count"]) if entry.get("count") is not None else None,
                n=int(entry["n"]) if entry.get("n") is not None else None,
                k=int(entry["k"]) if entry.get("k") is not None else None,
                kind=str(entry["kind"]) if entry.get("kind") is not None else None,
            )
        )
    return tuple(parsed)


def validate_stage(stage: NpcStage, *, enemies: dict[str, NpcEnemy]) -> None:
    """Cross-check a stage against the enemies' ``appears_in`` index.

    Every lineup/reserve enemy must list this stage in ``appears_in`` (a
    mismatch means the name->id mapping or the stage data drifted), and its
    Chinese name must resolve back to the same engine character id.
    """
    name_index: dict[str, str] = {}
    for slug, enemy in enemies.items():
        for key in (enemy.name, enemy.en, enemy.id, *enemy.aliases):
            if key:
                name_index.setdefault(key, slug)
    for entry in (*stage.lineup, *stage.reserve):
        enemy = enemies[entry.id]
        if stage.name not in enemy.appears_in:
            raise ValueError(
                f"stage {stage.name!r} uses {entry.id!r} but that enemy's "
                f"appears_in={list(enemy.appears_in)} does not include it"
            )
        if entry.name not in name_index:
            raise ValueError(
                f"stage {stage.name!r} enemy name {entry.name!r} is not a known NPC enemy name"
            )
        if name_index[entry.name] != entry.id:
            raise ValueError(
                f"stage {stage.name!r} name {entry.name!r} maps to "
                f"{name_index[entry.name]!r}, expected {entry.id!r}"
            )
    if not stage.deck_characters:
        raise ValueError(f"stage {stage.name!r} has an empty lineup")
    if not stage.objectives:
        raise ValueError(f"stage {stage.name!r} has no objectives")


@lru_cache(maxsize=4)
def load_stages(
    path: str | Path | None = None,
    enemies_path: str | Path | None = None,
) -> dict[str, NpcStage]:
    """Load + validate the stage specs keyed by stage name."""
    payload = _read_json(Path(path) if path is not None else DEFAULT_STAGES_PATH)
    enemies = load_enemies(enemies_path)
    stages: dict[str, NpcStage] = {}
    for entry in payload.get("stages", ()):
        name = str(entry["name"])
        stage = NpcStage(
            name=name,
            display=str(entry.get("display") or name),
            region=str(entry.get("region") or ""),
            lineup=_parse_lineup(entry.get("lineup"), enemies=enemies),
            reserve=_parse_lineup(entry.get("reserve"), enemies=enemies),
            special_rules=tuple(str(rule) for rule in entry.get("special_rules", ())),
            objectives=_parse_objectives(entry.get("objectives")),
            objectives_verified=bool(entry.get("objectives_verified", False)),
            display_en=str(entry.get("display_en") or ""),
            stage_title=(entry.get("stage_title") or {}).get("text")
            if isinstance(entry.get("stage_title"), dict)
            else None,
        )
        validate_stage(stage, enemies=enemies)
        stages[name] = stage
    return stages


def stage_names() -> list[str]:
    return sorted(load_stages())


def get_stage(name: str) -> NpcStage:
    stages = load_stages()
    if name not in stages:
        raise KeyError(f"unknown NPC stage: {name!r}; known: {sorted(stages)}")
    return stages[name]


def load_npc_specs(
    path: str | Path | None = None,
    enemies_path: str | Path | None = None,
) -> dict[str, NpcStage]:
    """Alias matching the "load NpcSpec (stage)" wording in PLAN §4.4."""
    return load_stages(path, enemies_path)


# --------------------------------------------------------------------------- #
# skill damage table
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=4)
def enemy_damage_table(enemies_path: str | Path | None = None) -> dict[int, int]:
    """Global skill-definition-id -> damage table over every known enemy."""
    table: dict[int, int] = {}
    for enemy in load_enemies(enemies_path).values():
        table.update(enemy.damage_by_skill_definition_id())
    return table


@lru_cache(maxsize=4)
def enemy_skill_kind_table(enemies_path: str | Path | None = None) -> dict[int, str]:
    """Global skill-definition-id -> "A"/"E"/"Q"/"X" table over every enemy."""
    table: dict[int, str] = {}
    for enemy in load_enemies(enemies_path).values():
        table.update(enemy.skill_kind_by_definition_id())
    return table


def stage_damage_table(stage: NpcStage, *, enemies_path: str | Path | None = None) -> dict[int, int]:
    """Skill damage table restricted to the enemies appearing in ``stage``."""
    enemies = load_enemies(enemies_path)
    slugs = {entry.id for entry in (*stage.lineup, *stage.reserve)}
    table: dict[int, int] = {}
    for slug in sorted(slugs):
        table.update(enemies[slug].damage_by_skill_definition_id())
    return table


def stage_skill_kind_table(
    stage: NpcStage, *, enemies_path: str | Path | None = None
) -> dict[int, str]:
    """Skill-kind table restricted to the enemies appearing in ``stage``."""
    enemies = load_enemies(enemies_path)
    slugs = {entry.id for entry in (*stage.lineup, *stage.reserve)}
    table: dict[int, str] = {}
    for slug in sorted(slugs):
        table.update(enemies[slug].skill_kind_by_definition_id())
    return table
