from __future__ import annotations

from types import SimpleNamespace

import pytest

from skillloop import prompt_v2
from skillloop.render import ObjectiveDetail, TaskSpec
from skillloop.prompt_v2 import (
    GAME_RULES_V2,
    glossary_section,
    glossary_terms,
    objective_lines,
    objective_message,
)


# -- segment 2: fixed rules ------------------------------------------------
def test_rules_text_follows_the_wiki_section_order():
    headers = [
        line for line in GAME_RULES_V2.splitlines()
        if line.startswith("【") and line.endswith("】")
    ]
    assert headers[:9] == [
        "【游戏准备】",
        "【对局流程】",
        "【投掷阶段】",
        "【行动阶段】",
        "【结束阶段】",
        "【行动：战斗行动与快速行动】",
        "【战斗行动·使用技能】",
        "【战斗行动·切换角色】",
        "【快速行动·打出手牌】",
    ]
    # Wiki wording, not our own paraphrase of it.
    assert "每个回合都会按照以下顺序进行：" in GAME_RULES_V2
    assert "- （1）「投掷阶段」双方重新获得8个元素骰子，进行投掷。" in GAME_RULES_V2
    assert "【其他规则·对局回合上限】" in GAME_RULES_V2
    assert "第15轮结束时，如果仍未分出胜负，则双方都会输掉这场对局。" in GAME_RULES_V2
    assert "第15回合开始时" not in GAME_RULES_V2


def test_preview_precedence_note_is_the_single_canonical_wording():
    """The 「预计」 precedence note is fixed per game, so it lives in the system prefix.

    It is a plain constant: the renderer only chooses where it goes (right before the
    skills block), so a change here cannot drift from the assertion in
    ``tests/test_rich_render.py``.
    """
    assert prompt_v2.PREVIEW_PRECEDENCE_NOTE == (
        "候选行的「预计」是引擎结算预测（已计附魔/装备/护盾/元素反应），"
        "与技能/卡牌描述里的基础数值冲突时，以「预计」为准。"
    )
    assert "预计" in prompt_v2.PREVIEW_PRECEDENCE_NOTE
    assert "引擎结算预测" in prompt_v2.PREVIEW_PRECEDENCE_NOTE
    assert "\n" not in prompt_v2.PREVIEW_PRECEDENCE_NOTE


def test_rules_text_has_no_version_label_paraphrase_or_special_skill():    # The card pool is pinned per run (configs/engine.lock); a version label in the
    # text would silently go stale.
    assert "（3.3）" not in GAME_RULES_V2
    assert "3.3" not in GAME_RULES_V2
    # 特技 is out of scope for this rules text and was removed with the special entry.
    assert "特技" not in GAME_RULES_V2
    # The old hand-written rules block must not creep back in.
    assert "规则要点" not in GAME_RULES_V2
    assert "[rule_" not in GAME_RULES_V2


def test_rules_text_uses_the_same_quote_marks_for_state_names():
    # The wiki mixes "[冻结]" with "「燃烧烈焰」"; one prompt uses one style.
    for name in ("冻结", "燃烧烈焰", "草原核", "激化领域"):
        assert f"「{name}」" in GAME_RULES_V2
        assert f"[{name}]" not in GAME_RULES_V2


def test_reaction_lines_are_merged_once_each():
    # The wiki lists 扩散 and 结晶 once per element (4 lines each, one with a typo);
    # they are merged into a single line each -- the only edit allowed.
    assert GAME_RULES_V2.count("- 扩散：") == 1
    assert GAME_RULES_V2.count("- 结晶：") == 1
    assert "扩散：对目标以外的所有敌方角色造成1点冰/水/火/雷元素伤害" in GAME_RULES_V2
    assert "结晶：本伤害+1，提供1点护盾，保护我方出战角色" in GAME_RULES_V2


def test_rules_text_fields_follow_the_runtime_asset_dump():
    # 激化领域 id=117 (v3.3.0): "可用次数：2" -- the asset dump wins over the wiki.
    line = next(line for line in GAME_RULES_V2.splitlines() if line.startswith("- 原激化："))
    assert "可用次数2" in line
    assert "可用次数3" not in line


# -- segment 4: objective explanations -------------------------------------
def _task(**overrides) -> TaskSpec:
    base = dict(stage="s", display="关卡", objectives=(), deck="d")
    base.update(overrides)
    return TaskSpec(**base)


def test_objective_lines_cover_every_known_type():
    task = _task(
        objective_types=(
            "win",
            "win_within_rounds",
            "defeated_less_than",
            "use_elemental_burst",
            "trigger_elemental_reactions",
            "play_event_cards",
            "play_equipment_card",
        ),
        max_rounds=5,
        objective_details=(
            ObjectiveDetail(type="win", text="赢得胜利"),
            ObjectiveDetail(type="win_within_rounds", text="5回合内胜利", n=5),
            ObjectiveDetail(type="defeated_less_than", text="少被击倒", k=2),
            ObjectiveDetail(type="use_elemental_burst", text="使用1次元素爆发", count=1),
            ObjectiveDetail(type="trigger_elemental_reactions", text="触发2次反应", count=2),
            ObjectiveDetail(type="play_event_cards", text="打出2张事件牌", count=2),
            ObjectiveDetail(type="play_equipment_card", text="打出1张装备牌", count=1),
        ),
    )
    assert objective_lines(task) == [
        "- win：赢得胜利——击倒对方全部角色。",
        "- win_within_rounds：回合上限——在第5轮结束前取胜，拖过上限即该目标失败。",
        "- defeated_less_than：少被击倒——胜利时，我方被击倒的角色次数必须小于2。",
        "- use_elemental_burst：元素爆发——实际释放1次元素爆发。",
        "- trigger_elemental_reactions：元素反应——触发2次元素反应。",
        "- play_event_cards：事件牌——打出2张事件牌。",
        "- play_equipment_card：装备牌——打出1张武器或圣遗物牌。",
    ]


def test_objective_lines_only_include_this_stage_types_and_fall_back_for_unknown():
    task = _task(
        objectives=("赢得胜利", "奇怪的限制"),
        objective_types=("win", "future_type"),
        objective_details=(
            ObjectiveDetail(type="win", text="赢得胜利"),
            ObjectiveDetail(type="future_type", text="奇怪的限制"),
        ),
    )
    assert objective_lines(task) == [
        "- win：赢得胜利——击倒对方全部角色。",
        "- future_type：奇怪的限制",
    ]


def test_objective_message_carries_task_description_and_lines():
    task = _task(
        display="布兰琪",
        deck="keqing_shuanghuo",
        objectives=("赢得胜利",),
        objective_types=("win",),
        objective_details=(ObjectiveDetail(type="win", text="赢得胜利"),),
    )
    text = objective_message(task)
    assert text.startswith("当前对局的获胜目标：\n")
    assert task.describe() in text
    assert "- win：赢得胜利——击倒对方全部角色。" in text


def test_objective_message_omits_the_block_when_the_stage_has_no_objectives():
    text = objective_message(_task(display="空关卡", objectives=()))
    assert text == "当前对局的获胜目标：\n关卡【空关卡】用【d】出战。三星目标：赢得胜利。"


# -- segment 3 appendix: glossary ------------------------------------------
@pytest.fixture(autouse=True)
def _fresh_glossary_cache():
    prompt_v2.clear_glossary_cache()
    yield
    prompt_v2.clear_glossary_cache()


def test_glossary_terms_follow_first_mention_order():
    text = (
        "我方角色与技能：\n"
        "- 愚人众：本角色附属潜行。……生成鼓舞领域。\n"
        "- 对方：初始附属流焰磨刃。"
    )
    terms = glossary_terms(text)
    names = [name for name, _ in terms]
    assert names[:3] == ["潜行", "鼓舞领域", "流焰磨刃"]
    by_name = dict(terms)
    # One description per term, no ①② numbering and no " / " joining.
    for description in by_name.values():
        assert "①" not in description and "②" not in description
        assert " / " not in description
        assert len(description) <= prompt_v2.GLOSSARY_DESC_LIMIT
    assert by_name["流焰磨刃"] == "角色造成的物理伤害变为火元素伤害 可用次数：3"


def test_multi_version_terms_print_every_distinct_wording_with_a_source_label():
    # 潜行 has two asset entries (可用次数：2 then 可用次数：3 + 物理伤害转火): both
    # wordings are printed, each labelled with a card/skill whose text names 潜行.
    section = glossary_section("本角色附属潜行。")
    lines = section.splitlines()
    assert lines[0] == "名词解释（上文出现的状态/出战状态/召唤物）："
    assert len(lines) == 3
    assert lines[1].startswith("- 潜行：所附属角色受到的伤害-1，造成的伤害+1。 可用次数：2")
    assert lines[2].startswith("- 潜行：所附属角色受到的伤害-1，造成的伤害+1。 可用次数：3")
    labels = [line.rsplit("（", 1)[1].rstrip("）") for line in lines[1:]]
    assert all(label.startswith("来自「") or label == "另一版本" for label in labels)
    # The first version's label is the first asset entry whose description mentions
    # the term (no guessing: the dump has no state -> card link).
    sources = prompt_v2.glossary_sources("潜行")
    first_label = lines[1].rsplit("（", 1)[1].rstrip("）")
    assert first_label == f"来自「{sources[0]}」"
    # glossary_terms still yields one row per term, the first version.
    assert dict(glossary_terms("本角色附属潜行。"))["潜行"] == (
        "所附属角色受到的伤害-1，造成的伤害+1。 可用次数：2"
    )


def test_identical_version_descriptions_collapse_to_one_line():
    # 雷元素附魔 has three asset entries but only two distinct descriptions, so the
    # block prints two lines (not three) and never repeats a wording.
    section = glossary_section("本角色附属雷元素附魔。")
    lines = [line for line in section.splitlines() if line.startswith("- 雷元素附魔：")]
    assert len(lines) == 2
    bodies = [line.split("：", 1)[1].rsplit("（", 1)[0] for line in lines]
    assert len(set(bodies)) == 2
    assert any("雷元素伤害+1" in body for body in bodies)


def test_single_version_term_keeps_the_plain_line():
    section = glossary_section("本角色附属寒冰大盾。")
    lines = section.splitlines()
    assert len(lines) == 2
    assert lines[1].startswith("- 寒冰大盾：所附属角色所受伤害-2。")


def test_multi_version_term_without_a_source_is_marked_another_version(monkeypatch, tmp_path):
    import json

    payload = {
        "data": [
            {"type": "GCG_CARD_STATE", "name": "测试状态", "description": "第一版说明。"},
            {"type": "GCG_CARD_STATE", "name": "测试状态", "description": "第二版说明。"},
        ]
    }
    fixture = tmp_path / "assets.json"
    fixture.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(prompt_v2, "ASSET_PATH", fixture)
    prompt_v2.clear_glossary_cache()
    lines = glossary_section("本角色附属测试状态。").splitlines()
    assert len(lines) == 3
    assert lines[1] == "- 测试状态：第一版说明。（另一版本）"
    assert lines[2] == "- 测试状态：第二版说明。（另一版本）"


def test_state_name_inside_a_longer_skill_name_is_not_explained():
    # 菲谢尔's elemental skill is 夜巡影翼 and the asset dump also has a state 夜巡.
    # The skill name is the only thing in the prompt, so the state must not be
    # explained -- it was a substring hit, not a mention.
    text = (
        "我方角色与技能：\n"
        "  - 菲谢尔[雷·弓] HP上限10，能量上限3\n"
        "    - 夜巡影翼（元素战技［3雷］）：造成1点雷元素伤害，召唤奥兹。"
    )
    names = [name for name, _ in glossary_terms(text)]
    assert "夜巡" not in names
    assert "奥兹" in names          # the summon the skill actually creates


def test_state_name_that_also_stands_alone_is_still_explained():
    # 潜行 is a substring of the passive 潜行大师. Inside that name it is not a
    # mention; where the description says 潜行 on its own it is the state.
    assert "潜行" not in [n for n, _ in glossary_terms("    - 潜行大师（被动）：……")]
    text = "    - 潜行大师（被动）：本角色附属潜行。"
    terms = dict(glossary_terms(text))
    assert "潜行" in terms
    assert terms["潜行"] == "所附属角色受到的伤害-1，造成的伤害+1。 可用次数：2"


def test_longest_match_wins_and_spans_never_overlap():
    # A longer name claims its span first, so the shorter name only matches outside.
    assert prompt_v2._free_positions("夜巡影翼", ("夜巡", "夜巡影翼")) == {
        "夜巡影翼": [0],
    }
    assert prompt_v2._free_positions("夜巡影翼。夜巡", ("夜巡", "夜巡影翼")) == {
        "夜巡影翼": [0],
        "夜巡": [5],
    }
    # Overlapping occurrences of one name are consumed once.
    assert prompt_v2._free_positions("aaa", ("a", "aa")) == {"aa": [0], "a": [2]}


def test_existing_glossary_hits_still_match_in_first_mention_order():
    text = (
        "本角色附属潜行。\n"
        "生成鼓舞领域。\n"
        "对方：初始附属流焰磨刃。\n"
        "本角色附属寒冰大盾。\n"
        "本角色附属雷元素附魔。"
    )
    names = [name for name, _ in glossary_terms(text)]
    assert names[:5] == ["潜行", "鼓舞领域", "流焰磨刃", "寒冰大盾", "雷元素附魔"]


def test_equipment_cards_and_generic_words_are_not_explainable():
    index = prompt_v2.glossary_index()
    # GCG_CARD_MODIFY (equipment/talent) descriptions already sit in the deck block.
    for name in ("冒险憧憬", "流放者头冠", "旅行剑", "抵天雷罚", "焦灼的魔女帽"):
        assert name not in index
    # Generic words some asset entry uses as a name are skipped.
    for word in sorted(prompt_v2.GLOSSARY_STOPWORDS):
        assert word not in index
    assert "目标" in prompt_v2.GLOSSARY_STOPWORDS
    assert glossary_section("目标角色与效果") == ""


def test_glossary_section_is_omitted_when_nothing_matches():
    assert glossary_section("我方牌组（0张）：") == ""
    assert glossary_section("") == ""


def test_glossary_terms_are_limited_to_twelve_first_mentions():
    names = list(prompt_v2.glossary_index())
    assert len(names) > prompt_v2.GLOSSARY_MAX_TERMS
    text = "\n".join(names[:20])
    terms = glossary_terms(text)
    assert len(terms) == prompt_v2.GLOSSARY_MAX_TERMS
    assert all(name in names[:20] for name, _ in terms)


def test_glossary_section_renders_the_documented_shape():
    section = glossary_section("本角色附属潜行。")
    lines = section.splitlines()
    assert lines[0] == "名词解释（上文出现的状态/出战状态/召唤物）："
    # 潜行 has two rule versions, so its first line carries the source label.
    assert lines[1].startswith("- 潜行：所附属角色受到的伤害-1，造成的伤害+1。 可用次数：2（")
    assert len(lines) == 3


def test_missing_asset_dump_disables_the_glossary_without_failing(monkeypatch, tmp_path):
    monkeypatch.setattr(prompt_v2, "ASSET_PATH", tmp_path / "missing.json")
    prompt_v2.clear_glossary_cache()
    assert prompt_v2.glossary_index() == {}
    assert glossary_section("本角色附属潜行。") == ""


def test_broken_asset_dump_disables_the_glossary_without_failing(monkeypatch, tmp_path):
    broken = tmp_path / "assets.json"
    broken.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(prompt_v2, "ASSET_PATH", broken)
    prompt_v2.clear_glossary_cache()
    assert prompt_v2.glossary_index() == {}


def test_glossary_index_only_indexes_explainable_types():
    index = prompt_v2.glossary_index()
    assert index, "asset dump should provide glossary entries"
    # 甘雨 is a character (no type), 运筹帷幄 an event card: neither is explainable.
    assert "甘雨" not in index
    assert "运筹帷幄" not in index
    assert "潜行" in index

