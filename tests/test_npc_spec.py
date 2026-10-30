"""NpcSpec loading/validation + skill damage table (PLAN.md §4.4, PvE-B3).

Engine-free: only reads ``configs/npc/*.json`` and the ``.gts`` sources under
``engine-overlay/`` (and ``refs/`` when present) to cross-check the derived
skill definition ids / damage.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from common.paths import REPO_ROOT
from npc.spec import (
    ENEMY_ENGINE_IDS,
    OBJECTIVE_TYPES,
    engine_id_for_name,
    enemy_damage_table,
    get_stage,
    load_enemies,
    load_stages,
    stage_damage_table,
)

OVERLAY_CHARACTERS = REPO_ROOT / "engine-overlay" / "overlay" / "packages" / "data" / "src" / "characters"
REFS_CHARACTERS = REPO_ROOT / "refs" / "genius-invokation" / "packages" / "data" / "src" / "characters"


def _parse_gts_skills(path: Path) -> dict[int, tuple[str | None, int]]:
    text = path.read_text(encoding="utf-8")
    skills: dict[int, tuple[str | None, int]] = {}
    for match in re.finditer(r"define skill \{(.*?)\n\};", text, re.S):
        block = match.group(1)
        skill_id = re.search(r"id\s+(\d+)\s+as", block)
        if not skill_id:
            continue
        skill_type = re.search(r"skillType\s+(\w+)", block)
        damages = re.findall(r":damage\(DamageType\.\w+,\s*(\d+)", block)
        skills[int(skill_id.group(1))] = (
            skill_type.group(1) if skill_type else None,
            max((int(value) for value in damages), default=0),
        )
    return skills


def _engine_skill_definitions() -> dict[int, tuple[str | None, int]]:
    definitions: dict[int, tuple[str | None, int]] = {}
    for path in OVERLAY_CHARACTERS.rglob("*.gts"):
        definitions.update(_parse_gts_skills(path))
    if REFS_CHARACTERS.exists():
        for path in REFS_CHARACTERS.rglob("*.gts"):
            definitions.update(_parse_gts_skills(path))
    return definitions


def test_all_stages_load_and_validate():
    stages = load_stages()
    assert len(stages) == 16
    assert {stage.region for stage in stages.values()} == {"mondstadt", "liyue"}
    for stage in stages.values():
        assert stage.deck_characters, stage.name
        assert stage.objectives, stage.name
        for objective in stage.objectives:
            assert objective.type in OBJECTIVE_TYPES


def test_lineup_name_mapping_cross_checks_appears_in():
    enemies = load_enemies()
    for stage in load_stages().values():
        for entry in (*stage.lineup, *stage.reserve):
            enemy = enemies[entry.id]
            assert entry.engine_id == ENEMY_ENGINE_IDS[entry.id]
            assert entry.name == enemy.name or entry.name  # name resolved
            assert stage.name in enemy.appears_in, (stage.name, entry.id)


def test_every_stage_references_known_enemy_engine_ids():
    for stage in load_stages().values():
        for character_id in stage.deck_characters:
            assert character_id in ENEMY_ENGINE_IDS.values()


def test_chinese_name_resolves_to_engine_id():
    assert engine_id_for_name("火斧丘丘暴徒") == 3302
    assert engine_id_for_name("冰箭丘丘人") == 3102
    assert engine_id_for_name("愚人众·火之债务处理人") == 2301
    # English names resolve too.
    assert engine_id_for_name("Mirror Maiden") == 2202
    with pytest.raises(KeyError):
        engine_id_for_name("不存在的敌人")
    # Every stage lineup's Chinese name resolves to that entry's engine id.
    for stage in load_stages().values():
        for entry in (*stage.lineup, *stage.reserve):
            assert engine_id_for_name(entry.name) == entry.engine_id


def test_skill_damage_table_matches_engine_gts():
    definitions = _engine_skill_definitions()
    if not definitions:
        pytest.skip("engine .gts sources not available")
    table = enemy_damage_table()
    assert table
    mismatches = []
    for enemy in load_enemies().values():
        for skill_id, damage in enemy.damage_by_skill_definition_id().items():
            found = definitions.get(skill_id)
            if found is None:
                mismatches.append((enemy.id, skill_id, "missing in .gts"))
                continue
            _skill_type, gts_damage = found
            if int(damage) != int(gts_damage):
                mismatches.append((enemy.id, skill_id, damage, gts_damage))
    assert not mismatches, mismatches


def test_stage_damage_table_is_a_subset_scoped_to_lineup():
    stage = get_stage("timaeus")
    table = stage_damage_table(stage)
    # Blazing Axe Mitachurl (3302): A=2, E=3, Q=5.
    assert table[33021] == 2
    assert table[33022] == 3
    assert table[33023] == 5
    # Cryo Hilichurl Shooter (3102) present, but an unrelated enemy is not.
    assert 31021 in table
    assert 32041 not in table  # hydro slime is not in this stage


def test_stage_deck_characters_expand_counts():
    assert get_stage("timmie").deck_characters == (3302, 3302)
    assert get_stage("timaeus").deck_characters == (3102, 3101, 3302)
    assert get_stage("chefmao").deck_characters == (2301, 2202)
    assert get_stage("wagner").reserve_characters == (3402, 3402)


def test_unknown_objective_type_is_rejected(tmp_path: Path):
    import json

    payload = {
        "stages": [
            {
                "name": "x",
                "display": "x",
                "region": "mondstadt",
                "lineup": [{"slot": 1, "id": "hydroslime", "name": "水史莱姆", "count": 1}],
                "reserve": [],
                "special_rules": [],
                "objectives": [{"type": "not_a_real_objective"}],
            }
        ]
    }
    path = tmp_path / "stages.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        load_stages(path)
