from __future__ import annotations

import json
from types import SimpleNamespace

from skillloop.extract import (
    SYSTEM,
    ReplaySample,
    StageProfile,
    build_prompt,
    failure_profiles,
    parse_skills,
    propose_candidates,
)


def _row(stage: str, *, three_star: bool, rounds: int = 5, objectives=None, **extra) -> dict:
    return {
        "stage": stage,
        "seed": extra.pop("seed", 1),
        "three_star": three_star,
        "rounds": rounds,
        "objectives": objectives or {"win": True},
        **extra,
    }


# -- profiles ----------------------------------------------------------------
def test_failure_profiles_aggregate_per_stage():
    rows = [
        _row("wagner", three_star=False, rounds=6, objectives={"win": True, "win_within_rounds": False}),
        _row("wagner", three_star=False, rounds=7, objectives={"win": True, "win_within_rounds": False}),
        _row("wagner", three_star=True, rounds=4, objectives={"win": True, "win_within_rounds": True}),
        _row("timmie", three_star=True, rounds=5, objectives={"win": True}),
        _row("timmie", three_star=True, rounds=5, objectives={"win": True}, error="boom"),
    ]
    profiles = {p.stage: p for p in failure_profiles(rows)}
    assert set(profiles) == {"wagner", "timmie"}          # errored game dropped
    wagner = profiles["wagner"]
    assert wagner.n == 3
    assert wagner.three_star_rate == 1 / 3
    assert wagner.failed_objectives == {"win_within_rounds": 2}
    assert wagner.mean_rounds > 5


def test_profile_text_is_readable():
    profile = StageProfile(
        stage="wagner", n=10, three_star_rate=0.3, mean_rounds=6.2,
        failed_objectives={"win_within_rounds": 7}, objectives=("5回合内胜利",),
    )
    text = profile.as_text()
    assert "wagner" in text and "win_within_rounds" in text and "5回合内胜利" in text


# -- replay sampling ---------------------------------------------------------
def test_failure_profiles_pick_close_loss_and_fast_three_star():
    rows = [
        _row("wagner", three_star=False, rounds=9, seed=2,
             objectives={"win": True, "win_within_rounds": False,
                         "play_equipment_card": False},
             trace=["R1 P0 技能 #1", "结局 loser"]),
        _row("wagner", three_star=False, rounds=6, seed=1,
             objectives={"win": True, "win_within_rounds": False,
                         "play_equipment_card": True},
             trace=["R1 P0 技能 #2", "结局 close"]),
        _row("wagner", three_star=True, rounds=5, seed=4,
             objectives={"win": True, "win_within_rounds": True,
                         "play_equipment_card": True},
             trace=["R1 P0 技能 #4", "结局 slow win"]),
        _row("wagner", three_star=True, rounds=4, seed=3,
             objectives={"win": True, "win_within_rounds": True,
                         "play_equipment_card": True},
             trace=["R1 P0 技能 #3", "结局 fast win"]),
        _row("timmie", three_star=False, rounds=6, objectives={"win": False}),
    ]
    profiles = {
        profile.stage: profile
        for profile in failure_profiles(
            rows, round_caps_by_stage={"wagner": 5}, max_replays=2
        )
    }
    wagner = profiles["wagner"]
    assert [sample.label for sample in wagner.replays] == ["失败", "三星"]
    # close loss: one missing objective beats the two-missing, longer game
    assert wagner.replays[0].seed == 1 and wagner.replays[0].rounds == 6
    assert wagner.replays[0].missing == ("win_within_rounds",)
    # fastest 3★ game
    assert wagner.replays[1].seed == 3 and wagner.replays[1].lines[0] == "R1 P0 技能 #3"
    # a stage whose rows predate --trace still gets statistics, no replay block
    assert profiles["timmie"].replays == []


def test_failure_profiles_respect_max_replays():
    rows = [
        _row("wagner", three_star=False, rounds=6, objectives={"win": False},
             trace=["R1 P0 技能"]),
        _row("wagner", three_star=True, rounds=4, objectives={"win": True},
             trace=["R1 P0 技能"]),
    ]
    assert len(failure_profiles(rows, max_replays=1)[0].replays) == 1
    assert failure_profiles(rows, max_replays=0)[0].replays == []


# -- parsing -----------------------------------------------------------------
def test_parse_skills_reads_array():
    raw = json.dumps([
        {"skill_name": "速攻少装备", "description": "只打一张装备", "conditions": {"objective_types": ["win_within_rounds"]}},
        {"skill_name": "", "description": "没有名字应被丢弃"},
    ])
    skills = parse_skills(raw, stage="wagner")
    assert len(skills) == 1
    skill = skills[0]
    assert skill.skill_id.endswith("_wagner")
    assert skill.status == "candidate"
    assert skill.layer == "strategy"
    assert skill.conditions.objective_types == ("win_within_rounds",)


def test_parse_skills_tolerates_fences_and_wrappers():
    fenced = "```json\n[{\"skill_name\": \"A\", \"description\": \"B\"}]\n```"
    assert len(parse_skills(fenced, stage="s")) == 1
    wrapped = json.dumps({"skills": [{"skill_name": "A", "description": "B", "layer": "stage_specific"}]})
    skills = parse_skills(wrapped, stage="s")
    assert len(skills) == 1 and skills[0].layer == "stage_specific"


def test_parse_skills_rejects_garbage():
    assert parse_skills("nope", stage="s") == []
    assert parse_skills(None, stage="s") == []


def test_parse_skills_pins_stage_specific_to_current_stage():
    raw = json.dumps([
        {
            "skill_name": "本关专用", "description": "只用于本关", "layer": "stage_specific",
            "conditions": {"stages": ["other"], "objective_types": ["win"],
                           "round_max": 5},
        },
        {
            "skill_name": "跨关通用", "description": "可用于多关", "layer": "strategy",
            "conditions": {"stages": ["other"]},
        },
    ])
    stage_specific, strategy = parse_skills(raw, stage="chefmao")
    assert stage_specific.conditions.stages == ("chefmao",)   # model's stages ignored
    assert stage_specific.conditions.objective_types == ("win",)
    assert stage_specific.conditions.round_max == 5
    assert strategy.layer == "strategy"
    assert strategy.conditions.stages == ("other",)            # strategy stays as-is


def test_backfill_stage_specific_stages_uses_longest_id_suffix():
    from scripts.extract_skills import backfill_stage_specific_stages
    from skillloop.library import SkillLibrary
    from skillloop.schema import Skill, SkillConditions

    library = SkillLibrary.from_skills([
        Skill("hydro_line_liusu", "水雷线", "旧库漏了 stages",
              conditions=SkillConditions(objective_types=("win",)),
              layer="stage_specific"),
        Skill("artifact_route_mulan", "木兰花", "最长后缀优先",
              conditions=SkillConditions(), layer="stage_specific"),
        Skill("unmatched_specific", "无后缀", "不应猜关卡",
              conditions=SkillConditions(), layer="stage_specific"),
        Skill("already_pinned_liusu", "已固定", "不要覆盖",
              conditions=SkillConditions(stages=("shitou",)), layer="stage_specific"),
        Skill("strategy_liusu", "通用", "strategy 不动",
              conditions=SkillConditions(), layer="strategy"),
    ])
    changed = backfill_stage_specific_stages(library, ("liusu", "lan", "mulan"))
    assert changed == ["artifact_route_mulan", "hydro_line_liusu"]
    assert library.get("hydro_line_liusu").conditions.stages == ("liusu",)
    assert library.get("hydro_line_liusu").conditions.objective_types == ("win",)
    assert library.get("artifact_route_mulan").conditions.stages == ("mulan",)
    assert library.get("unmatched_specific").conditions.stages == ()
    assert library.get("already_pinned_liusu").conditions.stages == ("shitou",)
    assert library.get("strategy_liusu").conditions.stages == ()


# -- proposal ----------------------------------------------------------------
class _Client:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[list[dict]] = []

    def chat(self, messages, **_kwargs) -> str | None:
        self.calls.append(messages)
        return self.reply


def test_propose_candidates_skips_perfect_and_rare_failures():
    profiles = [
        StageProfile("perfect", 10, 1.0, 4.0, {}, ()),
        StageProfile("rare", 10, 0.9, 4.0, {"win": 1}, ()),          # 10% < 15% threshold
        StageProfile("bad", 10, 0.3, 6.0, {"win_within_rounds": 7}, ()),
    ]
    client = _Client(json.dumps([{"skill_name": "快", "description": "别拖"}]))
    skills = propose_candidates(client, profiles)
    assert len(client.calls) == 1                     # only the bad stage triggered a call
    assert len(skills) == 1 and skills[0].skill_id.endswith("_bad")


def test_build_prompt_includes_rules_and_profile():
    profile = StageProfile("wagner", 10, 0.3, 6.0, {"win_within_rounds": 7}, ("5回合内胜利",))
    messages = build_prompt(profile, rules_note="rule_dice: 骰子规则")
    assert messages[0]["role"] == "system" and "JSON" in messages[0]["content"]
    assert "rule_dice" in messages[1]["content"]
    assert "wagner" in messages[1]["content"]


def test_build_prompt_includes_both_replays_and_self_prompt_instruction():
    profile = StageProfile(
        "wagner", 10, 0.3, 6.0, {"win_within_rounds": 7}, ("5回合内胜利",),
        replays=[
            ReplaySample(
                label="失败", seed=1, rounds=6,
                missing=("play_equipment_card",),
                lines=("R1 P0 技能 #32031 骰1,2", "结局 winner=1（对方胜），6 回合；目标 win✔"),
            ),
            ReplaySample(
                label="三星", seed=3, rounds=4, missing=(),
                lines=("R1 P0 打牌 artifact#1 骰2", "结局 winner=0（我方胜），4 回合；目标 win✔"),
            ),
        ],
    )
    messages = build_prompt(profile)
    system, user = messages[0]["content"], messages[1]["content"]

    assert "代表局 A（失败，6 回合，缺 play_equipment_card，seed=1）:" in user
    assert "代表局 B（三星，4 回合，目标全达成，seed=3）:" in user
    assert "R1 P0 技能 #32031 骰1,2" in user
    assert "R1 P0 打牌 artifact#1 骰2" in user
    # statistics block is still there (replays are additive)
    assert "三星率 0.30" in user
    # the extraction prompt is prompt engineering for the future decision prompt:
    # no prescribed skill format (especially no mandatory budget/abort clause)
    assert "原样进入未来对局的系统提示" in system
    assert "任何形式都行" in system and "越具体" in system
    assert "≤N 回合 / ≤K 个行动" not in system
    assert "否则放弃" not in system
    assert '"skill_name"' in system                     # JSON schema unchanged


def test_build_prompt_includes_deck_rules_and_existing_skills_in_order():
    profile = StageProfile(
        "chefmao", 5, 0.4, 6.0, {"trigger_elemental_reactions": 3}, ("触发元素反应",),
        replays=[
            ReplaySample(label="失败", seed=1, rounds=6, missing=("win_within_rounds",),
                         lines=("R1 P0 技能 #1",)),
        ],
        deck="keqing_jihua",
        deck_facts="刻晴(雷)、柯莱(草)；元素：雷x2、草x1；卡牌：事件x20",
        special_rules="开局我方支援区有「卯师傅」；行动阶段开始为双方出战角色附着火元素",
        existing_skills=("[use_fire_support_chefmao] 火支援先手: 行动阶段开始先切火角色",),
    )
    messages = build_prompt(profile, rules_note="rule_dice: 骰子规则")
    user = messages[1]["content"]

    assert "固定牌组：keqing_jihua；角色/卡牌：刻晴(雷)" in user
    assert "关卡特殊规则：开局我方支援区有「卯师傅」" in user
    assert "当前技能库中与本关相关/通用的技能（可批判、可替换；不要重复）：" in user
    assert "- [use_fire_support_chefmao] 火支援先手: 行动阶段开始先切火角色" in user

    # replay -> existing skills -> rules note, so the model critiques the library
    # after seeing the evidence but before the always-on rules.
    replay_at = user.index("代表局 A")
    existing_at = user.index("当前技能库中")
    rules_at = user.index("已知规则：")
    assert replay_at < existing_at < rules_at


def test_build_prompt_omits_empty_fact_blocks():
    profile = StageProfile("wagner", 10, 0.3, 6.0, {"win": 3}, ("胜利",))
    user = build_prompt(profile)[1]["content"]
    assert "固定牌组" not in user
    assert "关卡特殊规则" not in user
    assert "当前技能库中" not in user


def test_system_prompt_contract_is_specific_and_structured():
    assert "每关最多 2 条" in SYSTEM
    assert "触发条件" in SYSTEM and "具体动作/优先级" in SYSTEM
    assert "1–3 句" in SYSTEM
    assert "空泛" in SYSTEM and "不要复述关卡目标" in SYSTEM
    assert "固定牌组的角色/卡牌和关卡特殊规则" in SYSTEM
    assert "stage_specific" in SYSTEM and "strategy" in SYSTEM
    assert "objective_types" in SYSTEM and "enemies" in SYSTEM and "round_max" in SYSTEM
    assert '"skill_name"' in SYSTEM and "只输出 JSON 数组" in SYSTEM


def test_relevant_existing_skills_excludes_rules_and_keeps_stage_matches():
    from scripts.extract_skills import relevant_existing_skills
    from skillloop.library import SkillLibrary
    from skillloop.schema import Skill, SkillConditions

    library = SkillLibrary.from_skills([
        Skill("rule_dice", "骰子", "规则层不应重复注入",
              conditions=SkillConditions(), layer="rule"),
        Skill("stage_specific_wagner", "瓦格纳速攻", "本关专用：先打武器",
              conditions=SkillConditions(), layer="stage_specific"),
        Skill("strategy_round_cap", "回合预算", "五回合内结束",
              conditions=SkillConditions(objective_types=("win_within_rounds",)),
              layer="strategy"),
        Skill("strategy_unrelated", "别的目标", "只和事件牌有关",
              conditions=SkillConditions(objective_types=("play_event_cards",)),
              layer="strategy"),
        Skill("strategy_other_stage", "别的关卡", "只适用于 timmie",
              conditions=SkillConditions(stages=("timmie",)), layer="strategy"),
    ])
    rendered = relevant_existing_skills(
        library, stage="wagner",
        objective_types=("win_within_rounds", "win"),
        enemies=("hydroslime",),
    )
    joined = "\n".join(rendered)
    assert "[stage_specific_wagner] 瓦格纳速攻" in joined
    assert "[strategy_round_cap] 回合预算" in joined
    assert "rule_dice" not in joined
    assert "strategy_unrelated" not in joined
    assert "strategy_other_stage" not in joined


class _ScriptClient:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[list[dict]] = []
        self.usage = SimpleNamespace(calls=0, cost_usd=0.0)

    def chat(self, messages, **_kwargs) -> str | None:
        self.calls.append(messages)
        self.usage.calls += 1
        return self.reply


def test_extract_skills_script_accepts_rows_with_and_without_traces(tmp_path, monkeypatch):
    rows = [
        _row("wagner", three_star=False, rounds=6,
             objectives={"win": True, "win_within_rounds": False},
             trace=["R1 P0 技能 #1", "结局 winner=0（我方胜），6 回合；目标 win✔"]),
        _row("timmie", three_star=False, rounds=7, objectives={"win": False}),
    ]
    path = tmp_path / "rows.jsonl"
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                    encoding="utf-8")
    client = _ScriptClient(json.dumps([{
        "skill_name": "五回合预算",
        "description": "在 ≤5 回合 / ≤20 个行动内完成；否则放弃",
        "conditions": {"objective_types": ["win_within_rounds"]},
    }]))
    import common.llm

    monkeypatch.setattr(common.llm, "build_llm_client", lambda: client)

    from scripts.extract_skills import main

    code = main(["--rows", str(path), "--base", str(tmp_path / "missing"),
                 "--dry-run", "--max-replays", "2"])
    assert code == 0
    assert len(client.calls) == 2
    prompts = {call[1]["content"]: call for call in client.calls}
    wagner_prompt = next(text for text in prompts if "关卡 wagner" in text)
    timmie_prompt = next(text for text in prompts if "关卡 timmie" in text)
    assert "代表局 A（失败，6 回合，缺 win_within_rounds" in wagner_prompt
    assert "代表局" not in timmie_prompt               # statistics-only fallback
