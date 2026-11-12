"""Fast tests for the frozen per-stage skill loadout planner."""

from __future__ import annotations

import hashlib
import json

from npc.spec import get_stage
from skillloop.library import SkillLibrary
from skillloop.loadout import (
    Loadout,
    build_messages,
    engine_lock_hash,
    load_loadouts,
    load_stage_decks,
    loadout_matches,
    plan_loadout,
    save_loadout,
    stage_context,
    stage_task,
)
from skillloop.schema import Skill, SkillConditions


class _Config:
    model = "stub-model"


class _Client:
    def __init__(self, replies: list[str | None]) -> None:
        self.replies = list(replies)
        self.config = _Config()
        self.calls: list[list[dict[str, str]]] = []
        self.kwargs: list[dict] = []

    def chat(self, messages, **kwargs):
        self.calls.append(list(messages))
        self.kwargs.append(dict(kwargs))
        return self.replies.pop(0) if self.replies else None


def _library(*skill_ids: str, version: int = 4) -> SkillLibrary:
    skills = [
        Skill(
            skill_id=skill_id,
            skill_name=f"技能{skill_id}",
            description=f"{skill_id} 的一句话描述，用于检索和提示",
            layer="rule" if skill_id.startswith("rule") else "strategy",
        )
        for skill_id in skill_ids
    ]
    return SkillLibrary.from_skills(skills, version=version)


def _plan(replies, library, *, max_skills: int = 2, deck: str = "superconduct_mika_attack"):
    client = _Client(replies)
    loadout = plan_loadout(
        get_stage("wagner"), library, deck, client, max_skills=max_skills,
    )
    return loadout, client


def test_prompt_contains_stage_goal_enemy_deck_and_full_skill_list():
    library = _library("alpha", "beta", "rule_one")
    loadout, client = _plan(
        ['{"skills": ["beta", "alpha"], "rationale": "先装备"}'], library
    )
    system, user = client.calls[0]
    text = user["content"]

    assert system["role"] == "system"
    assert "瓦格纳（wagner）" in text
    assert "打出1张武器牌" in text
    assert "5回合内胜利（回合上限 5）" in text
    assert "水史莱姆" in text and "风丘丘萨满" in text
    assert "出战牌组：superconduct_mika_attack" in text
    assert "卡牌：" in text and "武器x" in text and "圣遗物x" in text
    assert "- alpha | strategy | 技能alpha |" in text
    assert "- beta | strategy | 技能beta |" in text
    assert "- rule_one | rule | 技能rule_one |" in text     # full list, not top-k
    assert "最多选 2 条" in text
    assert '"skills"' in text and '"rationale"' in text

    # one call, temperature 0, recorded model
    assert len(client.calls) == 1
    assert client.kwargs[0]["temperature"] == 0.0
    assert client.kwargs[0]["reasoning_effort"] == "none"
    assert loadout.model == "stub-model"
    assert loadout.fallback is False
    assert loadout.deck == "superconduct_mika_attack"
    assert loadout.skills == ("beta", "alpha")

    # prompt hash covers exactly the system + user content
    digest = hashlib.sha1()
    for message in (system, user):
        digest.update(message["content"].encode("utf-8"))
        digest.update(b"\n")
    assert loadout.prompt_hash == digest.hexdigest()[:12]
    assert len(loadout.prompt_hash) == 12


def test_duplicates_are_deduped_and_capped():
    library = _library("a", "b", "c", "d")
    loadout, _client = _plan(
        ['{"skills": ["a", "b", "a", "c", "d"], "rationale": "x"}'],
        library,
        max_skills=2,
    )
    assert loadout.skills == ("a", "b")


def test_unknown_skill_id_retries_then_falls_back():
    library = _library("alpha", "beta")
    loadout, client = _plan(
        ['{"skills": ["invented"], "rationale": "x"}', '{"skills": ["beta"]}'],
        library,
    )
    # second reply is valid, so no fallback; unknown ids are a validation failure
    assert len(client.calls) == 2
    assert loadout.fallback is False
    assert loadout.skills == ("beta",)


def test_malformed_then_valid_retries_once():
    library = _library("alpha", "beta")
    loadout, client = _plan(["not json", '{"skills": ["alpha"]}'], library)
    assert len(client.calls) == 2
    assert loadout.fallback is False
    assert loadout.skills == ("alpha",)


def test_two_failures_fall_back_to_bm25_for_the_stage():
    library = _library("alpha", "beta", "gamma")
    loadout, client = _plan([None, '{"skills": ["invented"]}'], library)

    assert len(client.calls) == 2
    assert loadout.fallback is True
    stage = get_stage("wagner")
    expected = library.retrieve(
        stage_task(stage, "superconduct_mika_attack").describe(),
        stage_context(stage),
        k=2,
    )
    assert loadout.skills == tuple(skill.skill_id for skill in expected)
    assert "fallback" in loadout.rationale


def test_too_many_skills_are_capped_not_rejected():
    library = _library(*(f"s{i}" for i in range(8)))
    loadout, _client = _plan(
        ['{"skills": ["s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7"]}'],
        library,
        max_skills=3,
    )
    assert loadout.skills == ("s0", "s1", "s2")
    assert loadout.fallback is False


def test_empty_skill_list_is_valid():
    library = _library("alpha")
    loadout, _client = _plan(['{"skills": [], "rationale": "不需要"}'], library)
    assert loadout.skills == ()
    assert loadout.fallback is False


def test_save_load_round_trip_records_library_and_engine(tmp_path):
    library = _library("alpha", "beta", version=7)
    loadout = Loadout(
        stage="wagner",
        deck="superconduct_mika_attack",
        skills=("alpha", "beta"),
        rationale="测试",
        model="stub-model",
        prompt_hash="abc123def456",
        fallback=True,
        library_version=library.version,
        engine_lock=engine_lock_hash(),
    )
    path = save_loadout(tmp_path, loadout)
    assert path == tmp_path / "v7" / "wagner.json"

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["library_version"] == 7
    assert payload["engine_lock"] == engine_lock_hash()
    assert payload["skills"] == ["alpha", "beta"]

    loaded = load_loadouts(tmp_path)
    assert loaded["wagner"] == loadout
    assert loadout_matches(loadout, library_version=7, engine_lock=engine_lock_hash())
    assert not loadout_matches(loadout, library_version=8, engine_lock=engine_lock_hash())
    assert not loadout_matches(loadout, library_version=7, engine_lock="stale")


def _stage_scoped_library() -> SkillLibrary:
    return SkillLibrary.from_skills([
        Skill("rule_one", "规则", "通用规则", layer="rule"),
        Skill("chefmao_specific", "主厨专用", "只用于 chefmao",
              conditions=SkillConditions(stages=("chefmao",), round_max=5),
              layer="stage_specific"),
        Skill("liusu_specific", "流苏专用", "只用于 liusu",
              conditions=SkillConditions(stages=("liusu",), round_max=5),
              layer="stage_specific"),
        Skill("generic_strategy", "通用策略", "所有关卡都可用",
              conditions=SkillConditions(), layer="strategy"),
    ], version=9)


def test_prompt_lists_only_skills_applicable_to_current_stage():
    library = _stage_scoped_library()
    _system, user = build_messages(
        get_stage("chefmao"), library, "keqing_jihua", max_skills=3
    )
    text = user["content"]
    assert "chefmao_specific | stage_specific" in text
    assert "liusu_specific" not in text          # other stage's stage_specific
    assert "rule_one | rule" in text             # rule layer still applicable
    assert "generic_strategy | strategy" in text
    assert "当前关卡适用 3 条" in text


def test_out_of_scope_ids_are_dropped_and_valid_ids_kept():
    library = _stage_scoped_library()
    client = _Client(['{"skills": ["chefmao_specific", "liusu_specific"]}'])
    loadout = plan_loadout(
        get_stage("chefmao"), library, "keqing_jihua", client, max_skills=2
    )
    assert loadout.fallback is False
    assert loadout.skills == ("chefmao_specific",)


def test_all_out_of_scope_ids_fall_back_to_bm25():
    library = _stage_scoped_library()
    client = _Client([
        '{"skills": ["liusu_specific"]}',
        '{"skills": ["liusu_specific"]}',
    ])
    loadout = plan_loadout(
        get_stage("chefmao"), library, "keqing_jihua", client, max_skills=2
    )
    assert len(client.calls) == 2
    assert loadout.fallback is True
    assert "out_of_scope:liusu_specific" in loadout.rationale
    assert "liusu_specific" not in loadout.skills
    assert set(loadout.skills) <= {"rule_one", "chefmao_specific", "generic_strategy"}


def test_current_stage_specific_skill_can_be_selected():
    library = _stage_scoped_library()
    client = _Client(['{"skills": ["chefmao_specific"]}'])
    loadout = plan_loadout(
        get_stage("chefmao"), library, "keqing_jihua", client, max_skills=1
    )
    assert loadout.fallback is False
    assert loadout.skills == ("chefmao_specific",)


def test_load_stage_decks_tolerates_missing_entries(tmp_path):
    path = tmp_path / "stage_decks.json"
    path.write_text(
        json.dumps({"stages": {"wagner": {"deck": "superconduct_mika_attack"}}}),
        encoding="utf-8",
    )
    mapping = load_stage_decks(path)
    assert mapping == {"wagner": "superconduct_mika_attack"}
    assert "timmie" not in mapping
