from __future__ import annotations

import json
from types import SimpleNamespace

import skillloop.extract as extract
from skillloop.extract import (
    SYSTEM,
    ReplaySample,
    StageMetrics,
    StageProfile,
    build_prompt,
    failure_profiles,
    parse_metrics,
    parse_skills,
    propose_candidates,
)
from skillloop.render import TaskSpec


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


def test_profile_text_is_readable_and_does_not_repeat_objectives():
    profile = StageProfile(
        stage="wagner", n=10, three_star_rate=0.3, mean_rounds=6.2,
        failed_objectives={"win_within_rounds": 7}, objectives=("5回合内胜利",),
    )
    text = profile.as_text()
    assert "wagner" in text and "win_within_rounds" in text
    # H11: the system message already renders task.describe() + 目标判定; the user
    # statistics line must not repeat the objective texts a third time.
    assert "关卡目标：" not in text and "5回合内胜利" not in text


# -- replay sampling ---------------------------------------------------------
def test_failure_replay_represents_the_most_failed_objective():
    """B4: the loss shown must be evidence for the dominant failure, not a rarer one."""
    rows = [
        _row("chefmao", three_star=False, rounds=6, seed=4,
             objectives={"win": True, "win_within_rounds": False,
                         "defeated_less_than": True},
             trace=["R1 P0 技能 超时局4", "结局 timeout"]),
        _row("chefmao", three_star=False, rounds=6, seed=5,
             objectives={"win": True, "win_within_rounds": False,
                         "defeated_less_than": True},
             trace=["R1 P0 技能 超时局5", "结局 timeout"]),
        _row("chefmao", three_star=False, rounds=5, seed=1,
             objectives={"win": True, "win_within_rounds": True,
                         "defeated_less_than": False},
             trace=["R1 P0 技能 少被击倒局1", "结局 defeat"]),
        _row("chefmao", three_star=True, rounds=5, seed=2,
             objectives={"win": True, "win_within_rounds": True,
                         "defeated_less_than": True},
             trace=["R1 P0 技能 三星局2", "结局 win"]),
        _row("chefmao", three_star=True, rounds=4, seed=3,
             objectives={"win": True, "win_within_rounds": True,
                         "defeated_less_than": True},
             trace=["R1 P0 技能 三星局3", "结局 win"]),
    ]
    profile = failure_profiles(rows, round_caps_by_stage={"chefmao": 5})[0]

    assert profile.failed_objectives == {"win_within_rounds": 2, "defeated_less_than": 1}
    assert [sample.label for sample in profile.replays] == ["失败", "三星"]
    loss = profile.replays[0]
    # win_within_rounds fails 2/5 vs defeated_less_than 1/5: the timeout game is
    # the evidence the statistics line talks about (this used to pick seed 1).
    assert loss.missing == ("win_within_rounds",)
    assert loss.seed in (4, 5)
    assert "超时局" in loss.lines[0]


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


# -- quantitative metrics ----------------------------------------------------
_SYNTHETIC_TRACE = [
    "R0 P0 抽牌：「甲」、「乙」",
    "R1 我方骰子：[万,雷,雷,草,水,水,水,岩]（8个）",
    "R1 P0 重掷 [万,雷] → [万,万,万,雷,雷,水,水,岩]（脚本策略）",
    "R1 对方骰子：[万,万,万,万,万,万,万,万]（8个）",
    "R1 P0 打牌 抵天雷罚 (天赋) 自动支付[雷,雷,雷]",
    "R1 P0 抽牌：「雷楔」",
    "R1 P0 技能 刻晴·云来剑法（造成2点物理伤害） 自动支付[万,岩]",
    "R1 P0 调和 送你一程 自动支付[水]",
    "R1 P1(NPC) 技能 愚人众·藏镜仕女·潑波绽破（造成3点水元素伤害） 自动支付[万,万,万]",
    "R1 P1(NPC) 技能 愚人众·藏镜仕女·潑波绽破（造成3点水元素伤害） 自动支付[万,万,万]",
    "R1 反应 超载 P0",
    "R1 反应 蒸发 P1(NPC)",
    "R2 我方骰子：[万,雷,雷,草]（4个）",
    "R2 P0 打牌 飞叶迴斜 (天赋) 自动支付[草,草]",
    "R2 P0 结束",
]


def test_parse_metrics_aggregates_all_seven_fields():
    metrics = parse_metrics([_SYNTHETIC_TRACE])

    assert metrics.games == 1
    # R1: pool 8, then the reroll's 8-element new pool wins; R2: 4.
    assert metrics.dice_available == 12
    # P0 autopay: 3 (card) + 2 (skill) + 1 (tuning) + 2 (card) = 8.
    assert metrics.dice_spent == 8
    assert metrics.tuning == 1
    assert metrics.cards_played == 2
    # R0 opening hand 2 + R1 draw 1.
    assert metrics.cards_drawn == 3
    # P1's vaporize is not ours.
    assert metrics.reactions == {"超载": 1}
    # effect text and the paid-dice suffix are stripped; counted per use.
    assert metrics.opponent_skills == {"愚人众·藏镜仕女·潑波绽破": 2}
    assert metrics.as_text() == (
        "本关量化（1 局）：骰子利用率 67%（可用 12 / 支付 8）；"
        "调和 1 次；出牌 2 张；抽牌 3 张；"
        "我方触发的反应：超载 1；"
        "对方最常用：愚人众·藏镜仕女·潑波绽破 2。"
    )


def test_parse_metrics_honours_reroll_pool_and_coalesced_reactions():
    """The post-reroll pool is the available dice; ` xK` means K reactions."""
    trace = [
        "R1 我方骰子：[雷,雷,水,水,水,水]（6个）",
        "R1 P0 重掷 [雷,雷] → [雷,雷,雷,雷]（脚本策略）",
        "R1 反应 燃烧 P0 x3",
        "R1 反应 超载 P0",
    ]
    metrics = parse_metrics([trace])
    assert metrics.dice_available == 4
    assert metrics.reactions == {"燃烧": 3, "超载": 1}


def test_parse_metrics_keeps_top_five_opponent_skills_and_ignores_malformed():
    trace = [
        "R1 P1(NPC) 技能 A（效果） 自动支付[万]",
        "R1 P1(NPC) 技能 A（效果） 自动支付[万]",
        "R1 P1(NPC) 技能 B（效果） 自动支付[万]",
        "R1 P1(NPC) 技能 C（效果） 自动支付[万]",
        "R1 P1(NPC) 技能 D（效果） 自动支付[万]",
        "R1 P1(NPC) 技能 E（效果） 自动支付[万]",
        "R1 P1(NPC) 技能 F（效果） 自动支付[万]",
        "R1 反应 未知反应",
        "不是一条回放行",
        "R1 P0 技能 没有支付",
        "R? 我方骰子：（无）",
    ]
    metrics = parse_metrics([trace, None or []])
    assert metrics.opponent_skills == {"A": 2, "B": 1, "C": 1, "D": 1, "E": 1}
    assert metrics.reactions == {}
    assert metrics.dice_spent == 0


def test_failure_profiles_attach_metrics_from_traces():
    rows = [
        _row("wagner", three_star=False, rounds=6, objectives={"win": False},
             trace=_SYNTHETIC_TRACE),
        # A row without a trace contributes statistics but not metrics evidence.
        _row("wagner", three_star=True, rounds=4, objectives={"win": True}),
    ]
    profile = failure_profiles(rows)[0]
    assert profile.n == 2
    assert profile.metrics is not None
    assert profile.metrics.games == 1          # only the traced game was scanned
    assert profile.metrics.dice_spent == 8


def test_metrics_block_sits_between_statistics_and_replay():
    metrics = parse_metrics([_SYNTHETIC_TRACE])
    profile = StageProfile(
        "chefmao", 1, 0.0, 2.0, {"win": 1}, (),
        replays=[ReplaySample(label="失败", seed=1, rounds=2,
                              missing=("win",), lines=("R1 P0 技能",))],
        metrics=metrics,
    )
    user = build_prompt(profile)[1]["content"]
    assert "本关量化（1 局）" in user
    assert user.index("三星率") < user.index("本关量化") < user.index("代表局 A")


def test_build_prompt_without_metrics_is_unchanged():
    profile = StageProfile("wagner", 10, 0.3, 6.0, {"win": 3}, ("胜利",))
    user = build_prompt(profile)[1]["content"]
    assert "本关量化" not in user
    # An explicitly empty metrics object renders nothing either.
    empty = StageProfile("wagner", 10, 0.3, 6.0, {"win": 3}, ("胜利",),
                         metrics=StageMetrics())
    assert build_prompt(empty)[1]["content"] == user


def test_coach_role_demands_four_quantitative_analyses():
    from skillloop.extract import COACH_ROLE

    for needle in ("骰子利用率", "手牌利用率", "元素反应", "对方规律"):
        assert needle in COACH_ROLE
    # At least one candidate must quote a quantitative number.
    assert "量化数字" in COACH_ROLE and "可用 8 骰只支付 3" in COACH_ROLE


# -- parsing -----------------------------------------------------------------
def test_parse_skills_reads_array():
    raw = json.dumps([
        {"skill_name": "速攻少装备", "description": "只打一张装备", "layer": "strategy",
         "conditions": {"objective_types": ["win_within_rounds"]}},
        {"skill_name": "", "description": "没有名字应被丢弃"},
    ])
    skills = parse_skills(raw, stage="wagner")
    assert len(skills) == 1
    skill = skills[0]
    assert skill.skill_id.endswith("_wagner")
    assert skill.status == "candidate"
    # extraction only sees wagner's failures: a declared global strategy is
    # downgraded and pinned to this stage, never admitted as cross-stage advice.
    assert skill.layer == "stage_specific"
    assert skill.conditions.stages == ("wagner",)
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
    # a declared strategy is downgraded too: single-stage evidence cannot be global.
    assert strategy.layer == "stage_specific"
    assert strategy.conditions.stages == ("chefmao",)


def test_parse_skills_downgrades_declared_strategy_and_never_rewrites_global(capsys):
    """v10: one stage's text declared ``strategy`` must not become a global rule.

    The reply also pointed ``replaces`` at ``strat_burst_timing``; the parser
    keeps that id (it exists) but the writer must refuse the cross-layer rewrite.
    """
    from scripts.extract_skills import replacement_target
    from skillloop.library import SkillLibrary
    from skillloop.schema import Skill

    raw = json.dumps([{
        "skill_name": "限回合内的爆发节奏",
        "description": "本关五回合内击杀火之债务处理人。",
        "layer": "strategy",
        "replaces": "strat_burst_timing",
    }])
    skill = parse_skills(
        raw, stage="blanche", existing_ids=("strat_burst_timing",)
    )[0]
    assert skill.layer == "stage_specific"
    assert skill.conditions.stages == ("blanche",)
    assert skill.replaces == "strat_burst_timing"   # parser only checks existence
    assert "layer strategy is human-maintained" in capsys.readouterr().err

    library = SkillLibrary.from_skills([
        Skill("strat_burst_timing", "爆发时机", "全局：留骰子给爆发。", layer="strategy"),
    ])
    assert replacement_target(skill, library) == ""


def test_parse_skills_missing_layer_defaults_to_stage_specific(capsys):
    """A missing layer must not become a cross-stage ``strategy`` rule."""
    raw = json.dumps([
        {"skill_name": "缺层", "description": "本关专用动作",
         "conditions": {"objective_types": ["win_within_rounds"]}},
    ])
    skill = parse_skills(raw, stage="chefmao")[0]
    assert skill.layer == "stage_specific"
    assert skill.conditions.stages == ("chefmao",)
    assert skill.conditions.objective_types == ("win_within_rounds",)
    err = capsys.readouterr().err
    assert "layer missing/invalid" in err and "-> stage_specific" in err


def test_parse_skills_invalid_layer_falls_back_to_stage_specific(capsys):
    """An unknown layer is downgraded to ``stage_specific``, not dropped (H13)."""
    raw = json.dumps([
        {"skill_name": "坏层", "description": "仍然存活但只用于本关", "layer": "general"},
        {"skill_name": "好层", "description": "跨关通用", "layer": "strategy"},
    ])
    skills = parse_skills(raw, stage="wagner")
    assert [skill.skill_name for skill in skills] == ["坏层", "好层"]
    bad, good = skills
    assert bad.layer == "stage_specific"
    assert bad.conditions.stages == ("wagner",)
    # "strategy" is now also pinned to the current stage (see parse_skills docstring)
    assert good.layer == "stage_specific" and good.conditions.stages == ("wagner",)
    err = capsys.readouterr().err
    assert "layer missing/invalid ('general' -> stage_specific)" in err
    assert "layer strategy is human-maintained" in err


def test_parse_skills_truncates_to_two_per_stage(capsys):
    """H14: the parser enforces the prompt's '每关最多 2 条' contract."""
    raw = json.dumps([
        {"skill_name": f"技能{i}", "description": f"第{i}条动作"} for i in range(3)
    ])
    skills = parse_skills(raw, stage="wagner")
    assert [skill.skill_name for skill in skills] == ["技能0", "技能1"]
    assert "keeping the first 2" in capsys.readouterr().err


def test_parse_skills_keeps_two_same_name_candidates_with_distinct_ids(capsys):
    """B7: a shared slug must not silently delete the second failure's candidate.

    chefmao got two rules (one for ``win_within_rounds``, one for
    ``defeated_less_than``) whose Chinese names slug to the same id; the old code
    parsed both and the writer's dedupe kept only the first, so one failure had no
    candidate at all.
    """
    raw = json.dumps([
        {"skill_name": "开局节奏", "layer": "stage_specific",
         "description": "缺 win_within_rounds：前两轮先打武器，别急着切人。",
         "conditions": {"objective_types": ["win_within_rounds"], "round_max": 5}},
        {"skill_name": "开局节奏", "layer": "stage_specific",
         "description": "缺 defeated_less_than：先集火火债，雷角色不要先手送死。",
         "conditions": {"objective_types": ["defeated_less_than"]}},
    ])
    skills = parse_skills(raw, stage="chefmao")

    assert len(skills) == 2
    first, second = skills
    assert first.skill_id != second.skill_id
    assert second.skill_id == f"{first.skill_id}_2"
    # the rename keeps it a stage-specific chefmao skill
    for skill in skills:
        assert skill.layer == "stage_specific"
        assert skill.conditions.stages == ("chefmao",)
    assert first.conditions.objective_types == ("win_within_rounds",)
    assert second.conditions.objective_types == ("defeated_less_than",)
    err = capsys.readouterr().err
    assert "duplicate id" in err and "renamed to" in err


def test_parse_skills_dedupes_only_identical_name_and_description():
    entry = {"skill_name": "同一条", "description": "完全相同的规则。",
             "layer": "strategy"}
    skills = parse_skills(json.dumps([entry, dict(entry)]), stage="wagner")
    assert len(skills) == 1 and skills[0].skill_id == "skill_wagner"


def test_parse_skills_keeps_replaces_only_for_known_ids(capsys):
    """B6: a candidate can rewrite an existing skill, but never a hallucinated id."""
    raw = json.dumps([
        {"skill_name": "改写旧规则", "description": "先手打武器。",
         "replaces": "old_wagner"},
        {"skill_name": "瞎写目标", "description": "不存在的引用。",
         "replaces": "__nope__"},
    ])
    known, unknown = parse_skills(raw, stage="wagner", existing_ids=("old_wagner",))
    assert known.replaces == "old_wagner"
    assert unknown.replaces == ""
    assert "replaces unknown skill '__nope__'" in capsys.readouterr().err


def test_parse_skills_maps_enemy_names_and_drops_foreign_objectives(capsys):
    """H12: conditions must be matchable on this stage (real values, ids)."""
    raw = json.dumps([{
        "skill_name": "火债前置",
        "description": "先集火火之债务处理人。",
        "conditions": {
            "objective_types": ["win_within_rounds", "play_event_cards"],
            "enemies": ["愚人众·火之债务处理人"],
            "round_max": 5,
        },
    }])
    skill = parse_skills(raw, stage="chefmao")[0]
    assert skill.conditions.objective_types == ("win_within_rounds",)
    assert skill.conditions.enemies == ("fatuipyroagent",)
    assert skill.conditions.round_max == 5
    assert "play_event_cards" in capsys.readouterr().err


def test_output_schema_uses_real_values_and_replaces():
    from skillloop.extract import COACH_ROLE, OUTPUT_SCHEMA

    assert '"objective_types": ["win_within_rounds"]' in OUTPUT_SCHEMA
    assert '"round_max": 5' in OUTPUT_SCHEMA
    assert '"replaces"' in OUTPUT_SCHEMA
    assert "fatuipyroagent" in COACH_ROLE      # enemies: english id, mapping is a fallback
    assert "layer 固定填 stage_specific" in COACH_ROLE
    assert "跨关 strategy 由人工维护" in COACH_ROLE
    # the prompt no longer promises a drop, and no longer prints an illegal
    # ``"strategy" 或 "stage_specific"`` value, so a copy-paste reply parses.
    assert "整条丢弃" not in COACH_ROLE
    assert '或 "stage_specific"' not in OUTPUT_SCHEMA
    assert '"layer": "stage_specific"' in OUTPUT_SCHEMA
    assert '"replaces": ""' in OUTPUT_SCHEMA


def test_output_schema_example_parses_as_json():
    """The example inside the system message is valid JSON: models copy it verbatim."""
    from skillloop.extract import SYSTEM

    start, end = SYSTEM.find("{"), SYSTEM.rfind("}")
    assert 0 <= start < end
    example = json.loads(SYSTEM[start:end + 1])
    assert example["layer"] == "stage_specific"
    assert example["replaces"] == ""
    assert isinstance(example["conditions"], dict)


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


def test_build_prompt_includes_profile_and_schema():
    profile = StageProfile("wagner", 10, 0.3, 6.0, {"win_within_rounds": 7}, ("5回合内胜利",))
    messages = build_prompt(profile)
    assert messages[0]["role"] == "system" and "JSON" in messages[0]["content"]
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
    messages = build_prompt(profile)
    user = messages[1]["content"]

    assert "固定牌组：keqing_jihua；角色/卡牌：刻晴(雷)" in user
    assert "关卡特殊规则：开局我方支援区有「卯师傅」" in user
    assert "当前技能库中与本关相关/通用的技能（可批判、可替换；不要重复）：" in user
    assert "- [use_fire_support_chefmao] 火支援先手: 行动阶段开始先切火角色" in user

    # replay evidence comes before the library block the model is asked to critique.
    assert user.index("代表局 A") < user.index("当前技能库中")


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


def test_relevant_existing_skills_excludes_legacy_rules_and_keeps_stage_matches(tmp_path):
    from scripts.extract_skills import relevant_existing_skills
    from skillloop.library import SkillLibrary
    from skillloop.schema import Skill, SkillConditions

    library = SkillLibrary.from_skills([
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
    # A legacy snapshot still carries a rule entry; loading strips it, so extraction
    # can never spend a slot re-injecting the system's rules text.
    library.save(tmp_path)
    payload = json.loads((tmp_path / "library.json").read_text(encoding="utf-8"))
    payload["skills"].append(
        {"skill_id": "rule_dice", "skill_name": "骰子",
         "description": "规则层不应重复注入", "layer": "rule", "conditions": {}}
    )
    (tmp_path / "library.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    library = SkillLibrary.load(tmp_path)
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


def test_relevant_existing_skills_chefmao_keeps_generics_and_full_sentences():
    """B5: chefmao must see generic advice, not other stages' strategy text.

    After the 2026-11-25 lineage reset the dynamic root is ``dyn_v4``
    (round 0 = ``static_v1`` v2), which carries no stage-named strategy rows yet.
    The no-blanche/shitou/buyun assertions therefore act as a guard: such text is
    demoted and must never appear once extraction starts adding it again.
    """
    from pathlib import Path

    from scripts.extract_skills import (
        relevant_existing_skills,
        stage_context,
        stage_objectives,
    )
    from skillloop.library import SkillLibrary

    root = Path(__file__).resolve().parents[1] / "data/skills/dyn_v4"
    library = SkillLibrary.load(root)
    objective_types, enemies = stage_context("chefmao")
    rendered = relevant_existing_skills(
        library,
        stage="chefmao",
        objective_types=objective_types,
        enemies=enemies,
        all_stages=tuple(stage_objectives()),
    )

    assert len(rendered) == 8
    joined = "\n".join(rendered)
    # Stage-named strategy rows (blanche/buyun/shitou/...) are demoted, not shown.
    for other in ("blanche", "shitou", "buyun"):
        assert other not in joined.lower()
    # The generic cross-stage reserve survives (the prompt must still show them).
    assert sum(line.startswith("[strat_") for line in rendered) >= 2
    # Descriptions keep the full trigger→action text and end at a sentence end.
    for line in rendered:
        description = line.split(": ", 1)[1]
        assert len(description) <= 300
        assert description.endswith(("。", "；", "！", "？"))
        assert "…" not in description


def test_relevant_existing_skills_reserves_generic_slots_and_dedupes():
    from scripts.extract_skills import relevant_existing_skills
    from skillloop.library import SkillLibrary
    from skillloop.schema import Skill, SkillConditions

    long_description = (
        "触发条件：每轮开局先看手牌。动作：先按本轮技能计算并锁死骰子，"
        "剩余骰子才允许打事件牌；切人后当轮若仍有骰子，应直接用新出战角色技能输出，"
        "不要在击杀轮连打不产生伤害的牌。" + "补充说明。" * 60
    )
    library = SkillLibrary.from_skills([
        Skill("stage_skill_chefmao", "本关打法", "本关专用：开局先上支援。",
              conditions=SkillConditions(stages=("chefmao",)), layer="stage_specific"),
        # Same opening sentence = same advice; only the higher-ranked one is kept.
        Skill("dup_one", "同义一", long_description, layer="strategy"),
        Skill("dup_two", "同义二", long_description, layer="strategy"),
        # Names another stage -> demoted behind every generic row.
        Skill("other_stage_shitou", "别的关", "别的关专属：先打反应。", layer="strategy"),
        Skill("skill_wagner", "装备目标前置化", "装备牌前两轮打。", layer="strategy"),
        *[
            Skill(f"strat_{name}", f"通用{name}", f"通用策略：{name}。", layer="strategy")
            for name in ("a", "b", "c", "d", "e", "f")
        ],
    ])
    rendered = relevant_existing_skills(
        library,
        stage="chefmao",
        objective_types=("win", "win_within_rounds"),
        all_stages=("chefmao", "wagner", "shitou"),
    )
    joined = "\n".join(rendered)
    assert rendered[0].startswith("[stage_skill_chefmao]")
    assert "other_stage_shitou" not in joined and "skill_wagner" not in joined
    assert "dup_one" in joined and "dup_two" not in joined     # synonym dedupe
    assert sum(line.startswith("[strat_") for line in rendered) >= 2
    # The 300-char cap cuts at a sentence end, never mid-clause with "…".
    for line in rendered:
        description = line.split(": ", 1)[1]
        assert len(description) <= 300
        assert not description.endswith("…")
        assert description.endswith(("。", "；", "！", "？"))


def test_build_prompt_user_line_does_not_repeat_the_objectives(monkeypatch):
    """H11: objectives live in the system prompt (task.describe + 目标判定) once."""
    monkeypatch.setattr(extract, "_render_assets", lambda: None)
    profile = _profile_for_new_prompt()
    messages = build_prompt(profile, task=_task())
    user = messages[1]["content"]
    assert "关卡目标：" not in user
    assert "5回合内胜利" not in user
    assert "win_within_rounds 失败" in user
    assert "5回合内胜利" in messages[0]["content"]


def test_replay_block_explains_script_policy_rows():
    """H10: the replay mixes script-policy and LLM rows; the block says which is which."""
    profile = _profile_for_new_prompt()
    user = build_prompt(profile)[1]["content"]
    assert (
        "说明：重掷由脚本策略执行（只留万能与我方角色元素）；换牌/选人为 LLM 决策"
        in user
    )
    assert "自动支付为引擎选骰；其余 P0 行为为 LLM 决策。" in user
    header = user.index("代表局回放（用于定位失误；不要复述单局操作）：")
    assert header < user.index("说明：重掷由脚本策略执行") < user.index("代表局 A")


def test_extract_skills_script_rewrites_a_replaced_skill_in_place(tmp_path, monkeypatch):
    """B6: ``replaces`` hits an existing id -> update in place, no contradictory twin."""
    from skillloop.library import SkillLibrary
    from skillloop.schema import Skill, SkillConditions

    base = tmp_path / "base"
    SkillLibrary.from_skills([
        Skill(
            "old_wagner", "旧节奏规则", "旧规则：先铺场，后期再补伤害。",
            conditions=SkillConditions(stages=("wagner",)), layer="stage_specific",
        ),
    ], version=1).save(base)
    rows = [
        _row("wagner", three_star=False, rounds=6,
             objectives={"win": True, "win_within_rounds": False}),
    ]
    path = tmp_path / "rows.jsonl"
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    client = _ScriptClient(json.dumps([{
        "skill_name": "新节奏规则",
        "description": "先手打武器，五回合内击杀。",
        "conditions": {"objective_types": ["win_within_rounds"], "round_max": 5},
        "layer": "stage_specific",
        "replaces": "old_wagner",
    }]))
    import common.llm

    monkeypatch.setattr(common.llm, "build_llm_client", lambda: client)

    from scripts.extract_skills import main

    out = tmp_path / "out"
    assert main(["--rows", str(path), "--base", str(base), "--out", str(out)]) == 0

    library = SkillLibrary.load(out)
    assert list(library.skills) == ["old_wagner"]        # id kept, no twin added
    updated = library.get("old_wagner")
    assert updated.description.startswith("先手打武器")
    assert updated.version == 2                          # update() bumped it
    assert updated.conditions.objective_types == ("win_within_rounds",)


class _ScriptClient:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[list[dict]] = []
        self.usage = SimpleNamespace(calls=0, cost_usd=0.0)

    def chat(self, messages, **_kwargs) -> str | None:
        self.calls.append(messages)
        self.usage.calls += 1
        return self.reply


def test_replacement_target_requires_same_layer_and_stage():
    """B7: ``replaces`` may not cross layers, and stage skills stay in their stage."""
    from scripts.extract_skills import replacement_target
    from skillloop.library import SkillLibrary
    from skillloop.schema import Skill, SkillConditions

    library = SkillLibrary.from_skills([
        Skill("strat_burst_timing", "爆发时机", "通用：留骰子给爆发。", layer="strategy"),
        Skill("skill_wagner", "瓦格纳节奏", "瓦格纳专用。",
              conditions=SkillConditions(stages=("wagner",)), layer="stage_specific"),
        Skill("skill_timmie", "提米节奏", "提米专用。",
              conditions=SkillConditions(stages=("timmie",)), layer="stage_specific"),
    ])
    stage_specific = dict(conditions=SkillConditions(stages=("wagner",)),
                          layer="stage_specific")
    wrong_layer = Skill("cand_a", "本关规则", "本关专用。",
                        replaces="strat_burst_timing", **stage_specific)
    other_stage = Skill("cand_b", "本关规则", "本关专用。",
                        replaces="skill_timmie", **stage_specific)
    same_stage = Skill("cand_c", "新节奏", "改写本关。",
                       replaces="skill_wagner", **stage_specific)
    strategy = Skill("cand_d", "通用", "跨关。", layer="strategy",
                     replaces="strat_burst_timing")

    assert replacement_target(wrong_layer, library) == ""
    assert replacement_target(other_stage, library) == ""
    assert replacement_target(same_stage, library) == "skill_wagner"
    # Global strategy rules are hand-maintained: even same-layer strategy is refused.
    assert replacement_target(strategy, library) == ""
    assert replacement_target(
        Skill("cand_e", "无引用", "没有 replaces。", layer="strategy"), library
    ) == ""


def test_replacement_target_same_stage_suffix_and_no_strategy_rewrite():
    """``stage_specific`` needs the same stage; ``strategy`` is never rewritten."""
    from scripts.extract_skills import replacement_target
    from skillloop.library import SkillLibrary
    from skillloop.schema import Skill, SkillConditions

    library = SkillLibrary.from_skills([
        # No ``conditions.stages``: only the id suffix names the stage.
        Skill("hydro_line_chefmao", "水雷线", "旧：先打水。", layer="stage_specific"),
        Skill("strat_round_budget", "回合预算", "通用：五回合内结束。",
              layer="strategy", conditions=SkillConditions(stages=("chefmao",))),
    ])
    by_suffix = Skill(
        "cand_f", "水雷线", "先打水再切雷。", layer="stage_specific",
        conditions=SkillConditions(stages=("chefmao",)),
        replaces="hydro_line_chefmao",
    )
    assert replacement_target(by_suffix, library) == "hydro_line_chefmao"

    # strategy -> strategy used to rewrite in place; global rules are off limits.
    strategy_rewrite = Skill(
        "cand_g", "回合预算", "三个回合内结束。", layer="strategy",
        replaces="strat_round_budget",
    )
    assert replacement_target(strategy_rewrite, library) == ""

    # even an empty-conditions strategy candidate cannot claim a global target
    bare_strategy = Skill(
        "cand_i", "回合预算", "三个回合内结束。", layer="strategy",
        conditions=SkillConditions(), replaces="strat_round_budget",
    )
    assert replacement_target(bare_strategy, library) == ""

    # a stage_specific candidate with no known stage cannot claim any target
    unknown_stage = Skill(
        "cand_h", "无关卡", "不知道属于哪关。", layer="stage_specific",
        replaces="hydro_line_chefmao",
    )
    assert replacement_target(unknown_stage, library) == ""


def test_extract_skills_script_adds_candidate_instead_of_overwriting_strategy(
    tmp_path, monkeypatch, capsys
):
    """B7: a stage_specific candidate must not rewrite a generic strategy rule."""
    from skillloop.library import SkillLibrary
    from skillloop.schema import Skill

    base = tmp_path / "base"
    SkillLibrary.from_skills([
        Skill("strat_burst_timing", "爆发时机", "通用：把骰子留给爆发回合。",
              layer="strategy"),
    ], version=1).save(base)
    rows = [
        _row("wagner", three_star=False, rounds=6,
             objectives={"win": True, "win_within_rounds": False}),
    ]
    path = tmp_path / "rows.jsonl"
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    client = _ScriptClient(json.dumps([{
        "skill_name": "本关节奏",
        "description": "瓦格纳前两轮先打武器。",
        "layer": "stage_specific",
        "replaces": "strat_burst_timing",
    }]))
    import common.llm

    monkeypatch.setattr(common.llm, "build_llm_client", lambda: client)

    from scripts.extract_skills import main

    out = tmp_path / "out"
    assert main(["--rows", str(path), "--base", str(base), "--out", str(out)]) == 0

    library = SkillLibrary.load(out)
    generic = library.get("strat_burst_timing")
    assert generic.description == "通用：把骰子留给爆发回合。"
    assert generic.version == 1                       # untouched: no update()
    added = [
        skill for skill_id, skill in library.skills.items()
        if skill_id != "strat_burst_timing"
    ]
    assert len(added) == 1
    assert added[0].layer == "stage_specific"
    assert added[0].conditions.stages == ("wagner",)
    assert added[0].replaces == ""                    # dangling reference cleared
    assert "keeping it as a new skill" in capsys.readouterr().err


def test_prompt_samples_parses_with_base_library_ids(tmp_path, capsys):
    """Bug B7: sample generation must not warn about a ``replaces`` that exists."""
    from scripts.prompt_samples import render_reflection
    from skillloop.library import SkillLibrary
    from skillloop.schema import Skill

    base = tmp_path / "base"
    SkillLibrary.from_skills([
        Skill("strat_burst_timing", "爆发时机", "通用：留骰子给爆发。", layer="strategy"),
    ], version=3).save(base)
    record = {
        "finish_reason": "stop",
        "content": json.dumps([{
            "skill_name": "本关节奏",
            "description": "瓦格纳前两轮先打武器。",
            "layer": "stage_specific",
            "replaces": "strat_burst_timing",
        }]),
        "usage": {},
    }
    rows = [_row("wagner", three_star=False, rounds=6,
                 objectives={"win": True, "win_within_rounds": False})]

    text = render_reflection([record], rows, str(base), "cmd")
    assert "replaces unknown skill" not in capsys.readouterr().err
    assert "解出 1 条候选" in text
    assert "version 3" in text



# -- new decision-shaped system message --------------------------------------
def _task(**overrides) -> TaskSpec:
    payload = {
        "stage": "wagner",
        "display": "瓦格纳",
        "objectives": ("5回合内胜利",),
        "deck": "keqing_jihua",
        "objective_types": ("win_within_rounds",),
        "max_rounds": 5,
    }
    payload.update(overrides)
    return TaskSpec(**payload)


def test_build_system_orders_segments_and_shares_decision_rules(monkeypatch):
    from skillloop.prompt_v2 import CARD_INFO_HEADER, GAME_RULES_V2, OBJECTIVE_HEADER

    monkeypatch.setattr(
        extract, "_knowledge_block",
        lambda task: CARD_INFO_HEADER + "\n  - 测试角色[雷] HP上限10",
    )
    system = extract.build_system(_task(), special_rules="开局我方支援区有「凯瑟琳」")
    assert system.startswith(extract.COACH_ROLE)
    assert GAME_RULES_V2 in system                       # same constant as decisions
    markers = [
        extract.COACH_ROLE,
        extract.GAME_RULES_HEADER,
        GAME_RULES_V2,
        extract.SPECIAL_RULES_HEADER,
        "「凯瑟琳」",
        CARD_INFO_HEADER,
        OBJECTIVE_HEADER,
        "关卡【瓦格纳】用【keqing_jihua】出战",
        "目标判定：",
        extract.OUTPUT_HEADER,
        '"skill_name"',
    ]
    positions = [system.index(marker) for marker in markers]
    assert positions == sorted(positions)


def test_build_system_degrades_without_assets(monkeypatch):
    from skillloop.prompt_v2 import CARD_INFO_HEADER

    monkeypatch.setattr(extract, "_render_assets", lambda: None)
    system = extract.build_system(_task())
    assert extract.GAME_RULES_V2 in system
    assert "当前对局的获胜目标" in system
    assert CARD_INFO_HEADER not in system
    assert '"skill_name"' in system


def test_build_system_omits_empty_special_rules():
    assert extract.SPECIAL_RULES_HEADER not in extract.build_system(_task())
    with_rules = extract.build_system(_task(), special_rules="   ")
    assert extract.SPECIAL_RULES_HEADER not in with_rules


def test_build_system_says_defeated_less_than_needs_a_win(monkeypatch):
    """``npc.objectives`` scores ``won and defeated < k``: the wording must say 胜利时."""
    monkeypatch.setattr(extract, "_render_assets", lambda: None)
    task = _task(
        objectives=("胜利；少被击倒",),
        objective_types=("win", "defeated_less_than"),
    )
    system = extract.build_system(task)
    assert "胜利时，我方被击倒的角色次数必须小于" in system
    # stages without that objective get no such clarification
    assert "胜利时，我方被击倒" not in extract.build_system(_task())


def _profile_for_new_prompt() -> StageProfile:
    return StageProfile(
        "wagner", 10, 0.3, 6.0, {"win_within_rounds": 7}, ("5回合内胜利",),
        replays=[
            ReplaySample(
                label="失败", seed=1, rounds=6,
                missing=("win_within_rounds",),
                lines=("R1 P0 技能 #1",),
            ),
        ],
        deck="keqing_jihua",
        deck_facts="刻晴(雷)；元素：雷x1；卡牌：事件x20",
        special_rules="开局我方支援区有「凯瑟琳」",
        existing_skills=("[x_wagner] 旧技能: 先打武器",),
    )


def test_build_prompt_with_task_moves_context_to_system(monkeypatch):
    monkeypatch.setattr(extract, "_render_assets", lambda: None)
    profile = _profile_for_new_prompt()
    messages = build_prompt(
        profile,
        task=_task(),
        special_rules=profile.special_rules,
    )
    system, user = messages[0]["content"], messages[1]["content"]

    assert "已知规则" not in user
    assert "固定牌组" not in user and "关卡特殊规则" not in user
    assert "三星率 0.30" in user
    assert "代表局 A" in user
    assert "当前技能库中" in user
    assert "【游戏规则】" in system and extract.GAME_RULES_V2 in system
    assert "【关卡特殊规则】" in system and "「凯瑟琳」" in system
    assert "当前对局的获胜目标" in system


def test_build_prompt_task_none_keeps_legacy_layout():
    profile = _profile_for_new_prompt()
    messages = build_prompt(profile)
    user = messages[1]["content"]
    assert messages[0]["content"] == SYSTEM
    assert "固定牌组：keqing_jihua" in user
    assert "关卡特殊规则：开局我方支援区有「凯瑟琳」" in user
    assert "已知规则" not in user


def test_propose_candidates_injects_per_stage_task_and_special_rules():
    profiles = [
        StageProfile("wagner", 10, 0.3, 6.0, {"win": 7}, ()),
        StageProfile("timmie", 10, 0.5, 5.0, {"win": 6}, ()),
    ]
    tasks = {
        "wagner": _task(stage="wagner", display="瓦格纳", deck="deck_a"),
        "timmie": _task(stage="timmie", display="提米", deck="deck_b"),
    }
    client = _Client(json.dumps([{"skill_name": "A", "description": "B"}]))
    propose_candidates(
        client,
        profiles,
        tasks=tasks,
        special_rules_by_stage={
            "wagner": "开局我方支援区有「甲」",
            "timmie": "行动阶段开始为双方出战角色附着火元素",
        },
    )
    systems = [call[0]["content"] for call in client.calls]
    assert len(systems) == 2
    wagner = next(text for text in systems if "关卡【瓦格纳】" in text)
    timmie = next(text for text in systems if "关卡【提米】" in text)
    assert "「甲」" in wagner and "附着火元素" not in wagner
    assert "附着火元素" in timmie and "「甲」" not in timmie
    assert "deck_a" in wagner and "deck_b" not in wagner


def test_build_stage_tasks_uses_config_deck_then_row_deck():
    from scripts.extract_skills import build_stage_tasks

    tasks = build_stage_tasks(["lan"], decks={"lan": "keqing_jihua"})
    assert tasks["lan"].stage == "lan"
    assert tasks["lan"].deck == "keqing_jihua"
    row_tasks = build_stage_tasks(
        ["lan"], decks={}, rows=[{"stage": "lan", "deck": "keqing_jihua"}]
    )
    assert row_tasks["lan"].deck == "keqing_jihua"
    assert build_stage_tasks(["__no_such_stage__"], decks={}) == {}


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
    wagner_call = next(
        call for call in client.calls if "关卡 wagner" in call[1]["content"]
    )
    timmie_call = next(
        call for call in client.calls if "关卡 timmie" in call[1]["content"]
    )
    wagner_prompt = wagner_call[1]["content"]
    timmie_prompt = timmie_call[1]["content"]
    assert "代表局 A（失败，6 回合，缺 win_within_rounds" in wagner_prompt
    assert "代表局" not in timmie_prompt               # statistics-only fallback

    # Each stage's system carries its own TaskSpec + special rules, and the rules
    # note no longer appears as a user-side block.
    wagner_system = wagner_call[0]["content"]
    timmie_system = timmie_call[0]["content"]
    for system in (wagner_system, timmie_system):
        assert "【游戏规则】" in system
        assert "已知规则" not in system
    for text in (wagner_prompt, timmie_prompt):
        assert "已知规则" not in text
    assert "开局我方支援区有「瓦格纳」" in wagner_system
    assert "开局我方支援区有「提米」" in timmie_system
    assert "开局我方支援区有「提米」" not in wagner_system
    assert "关卡【瓦格纳】" in wagner_system
    assert "关卡【提米】" in timmie_system


# -- truncation (finish_reason=length) retry ----------------------------------
class _TruncatingClient:
    """Reports ``length`` on the first call, then a chosen finish_reason rest."""

    def __init__(self, replies, finish_reasons) -> None:
        self.replies = list(replies)
        self.finish_reasons = list(finish_reasons)
        self.calls: list[list[dict]] = []
        self.max_tokens: list[int] = []
        self.last_finish_reason: str | None = None
        self.usage = SimpleNamespace(calls=0, cost_usd=0.0)

    def chat(self, messages, *, max_tokens=None, **_kwargs) -> str | None:
        self.calls.append(messages)
        self.max_tokens.append(max_tokens)
        self.usage.calls += 1
        self.last_finish_reason = self.finish_reasons.pop(0)
        return self.replies.pop(0)


def _failing_stage(name: str = "wagner") -> StageProfile:
    return StageProfile(name, 10, 0.3, 6.0, {"win_within_rounds": 7}, ())


def test_propose_candidates_retries_once_with_doubled_max_tokens_on_length(capsys):
    profile = _failing_stage()
    reply = json.dumps([{"skill_name": "回合预算", "description": "五回合内速攻"}])
    client = _TruncatingClient(
        replies=['[{"skill_name": "回合预', reply],   # first reply was cut mid-JSON
        finish_reasons=["length", "stop"],
    )
    skills = propose_candidates(client, [profile], max_tokens=4096)

    # same messages, budget doubled exactly once, then parsed normally
    assert client.max_tokens == [4096, 8192]
    assert len(client.calls) == 2 and client.calls[0] == client.calls[1]
    assert len(skills) == 1 and skills[0].skill_id.endswith("_wagner")
    assert "retrying once at max_tokens=8192" in capsys.readouterr().err


def test_propose_candidates_caps_retry_budget_and_skips_still_truncated_stage(capsys):
    profiles = [_failing_stage("wagner"), _failing_stage("timmie")]
    ok = json.dumps([{"skill_name": "提米局", "description": "先打事件牌"}])
    client = _TruncatingClient(
        replies=["cut", "cut again", ok],
        finish_reasons=["length", "length", "stop"],
    )
    skills = propose_candidates(client, profiles, max_tokens=40000)

    # wagner: 40000 -> retry capped at 65536, still length -> skipped (logged, not 0-silent)
    # timmie: first call is fine -> parsed
    assert client.max_tokens == [40000, 65536, 40000]
    assert [skill.skill_id.endswith("_timmie") for skill in skills] == [True]
    err = capsys.readouterr().err
    assert "still truncated after retry" in err and "skipping this stage" in err
