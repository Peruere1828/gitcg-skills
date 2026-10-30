from __future__ import annotations

import json

import pytest

from skillloop.library import SkillLibrary, UsageLog, match_context, tokenize
from skillloop.schema import Skill, SkillConditions


def _skill(skill_id: str, *, layer: str = "strategy", desc: str = "保持骰子节奏", **kwargs) -> Skill:
    return Skill(
        skill_id=skill_id,
        skill_name=kwargs.pop("name", skill_id),
        description=desc,
        conditions=kwargs.pop("conditions", SkillConditions()),
        layer=layer,
        **kwargs,
    )


# -- schema ------------------------------------------------------------------
def test_skill_requires_core_fields():
    with pytest.raises(ValueError):
        Skill(skill_id="", skill_name="x", description="y")
    with pytest.raises(ValueError):
        Skill(skill_id="a", skill_name="x", description="y", layer="nope")
    with pytest.raises(ValueError):
        Skill(skill_id="a", skill_name="x", description="y", status="nope")


def test_conditions_roundtrip():
    cond = SkillConditions(stages=("wagner",), objective_types=("win_within_rounds",), round_max=3)
    assert SkillConditions.from_dict(cond.as_dict()) == cond
    payload = _skill("a", conditions=cond).as_dict()
    assert payload["conditions"] == {
        "stages": ["wagner"],
        "objective_types": ["win_within_rounds"],
        "round_max": 3,
    }
    assert Skill.from_dict(payload).conditions == cond


# -- matching ----------------------------------------------------------------
def test_match_context_wildcards_and_filters():
    any_skill = SkillConditions()
    assert match_context(any_skill, {})
    stage_skill = SkillConditions(stages=("wagner",))
    assert match_context(stage_skill, {"stage": "wagner"})
    assert not match_context(stage_skill, {"stage": "timmie"})
    obj_skill = SkillConditions(objective_types=("play_equipment_card",))
    assert match_context(obj_skill, {"objective_types": ["win", "play_equipment_card"]})
    assert not match_context(obj_skill, {"objective_types": ["win"]})
    round_skill = SkillConditions(round_min=2, round_max=3)
    assert match_context(round_skill, {"round": 2})
    assert not match_context(round_skill, {"round": 4})
    assert not match_context(round_skill, {})  # rounds unknown -> not applicable


def test_tokenize_handles_chinese_and_ascii():
    tokens = tokenize("保留 Pyro 骰子")
    assert "pyro" in tokens
    assert "骰" in tokens and "骰子" in tokens


# -- retrieval ---------------------------------------------------------------
def test_retrieve_prefers_more_specific_layer():
    # the rules layer is injected as a fixed prefix, so retrieval ranks only the
    # strategy layers: stage_specific must outrank strategy on the same query.
    lib = SkillLibrary.from_skills([
        _skill("strategy_a", layer="strategy", desc="装备牌可以增加护盾"),
        _skill("stage_a", layer="stage_specific", desc="装备牌在速攻关里只打一张"),
    ])
    hits = lib.retrieve("装备牌 速攻", {"stage": "wagner"}, k=2)
    assert [s.skill_id for s in hits] == ["stage_a", "strategy_a"]


def test_retrieve_respects_conditions_and_k():
    lib = SkillLibrary.from_skills([
        _skill("only_wagner", conditions=SkillConditions(stages=("wagner",))),
        _skill("everywhere"),
    ])
    assert [s.skill_id for s in lib.retrieve("x", {"stage": "timmie"}, k=2)] == ["everywhere"]
    assert lib.retrieve("x", {"stage": "wagner"}, k=0) == []


def test_retrieve_excludes_retired_and_is_deterministic():
    lib = SkillLibrary.from_skills([_skill("a"), _skill("b", status="retired")])
    hits = lib.retrieve("骰子", {}, k=5)
    assert [s.skill_id for s in hits] == ["a"]
    assert [s.skill_id for s in lib.retrieve("骰子", {}, k=5)] == ["a"]


# -- lifecycle ---------------------------------------------------------------
def test_update_bumps_version_and_retire_marks_status():
    lib = SkillLibrary.from_skills([_skill("a")])
    lib.update("a", description="新的原则")
    assert lib.get("a").version == 2
    lib.retire("a", reason="消融为负")
    assert lib.get("a").status == "retired"
    assert "retired" in lib.get("a").evidence.note


def test_save_load_and_snapshot(tmp_path):
    lib = SkillLibrary.from_skills([_skill("a")], version=3)
    lib.bump_version(note="admit batch 1", snapshot_root=tmp_path)
    path = lib.save(tmp_path)
    assert path.exists()
    assert (tmp_path / "history" / "v4.json").exists()
    loaded = SkillLibrary.load(tmp_path)
    assert loaded.version == 4
    assert [s.skill_id for s in loaded] == ["a"]
    assert loaded.meta["history"][-1]["note"] == "admit batch 1"


def test_usage_log_roundtrip(tmp_path):
    log = UsageLog()
    log.record(["a", "b"], cited=["a"])
    log.record(["a"])
    log.save(tmp_path)
    loaded = UsageLog.load(tmp_path)
    assert loaded.decisions == 2
    assert loaded.retrieved["a"] == 2
    assert loaded.cited["a"] == 1
    assert json.loads((tmp_path / "usage.json").read_text())["decisions"] == 2


def test_rules_layer_is_injected_separately_from_retrieval():
    from skillloop.rules import build_rules_library

    lib = build_rules_library({"win_within_rounds", "play_equipment_card"})
    text = lib.rules_text()
    assert "rule_dice" in text and "规则_目标_回合上限" in text
    # retrieval must not return the always-on rules layer
    assert all(skill.layer != "rule" for skill in lib.retrieve("速攻 装备", {"stage": "wagner"}, k=5))
    # but asking for rules explicitly still works
    assert lib.retrieve("骰子", {}, k=3, layers=("rule",))
