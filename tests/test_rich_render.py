from __future__ import annotations

from types import SimpleNamespace

import pytest

from reps.schema import (
    DecisionType,
    LowLevelActionSpec,
    OptionKind,
    PublicSlotTarget,
)
from skillloop.render import (
    ProgressSignals,
    StateRenderer,
    TaskSpec,
    progress_block,
    select_candidates,
)
from skillloop.rich_render import RichStateRenderer


def _assets():
    try:
        from agents.scripted.assets import load_assets

        return load_assets()
    except Exception as exc:  # cache missing / offline: skip rather than fetch
        pytest.skip(f"asset cache unavailable: {exc}")


def _character(definition_id: int, *, aura: int = 0, active: bool = True):
    return SimpleNamespace(
        definition_id=definition_id,
        health=10,
        max_health=10,
        energy=0,
        max_energy=0,
        aura=aura,
        is_active=active,
        defeated=False,
    )


def _player(*characters, hand=(), dice=(1, 2), declared_end=False):
    return SimpleNamespace(
        characters=tuple(characters),
        hand_cards=tuple(SimpleNamespace(definition_id=card) for card in hand),
        dice=tuple(dice),
        declared_end=declared_end,
    )


def _context(opponent_aura: int = 0):
    me = _player(_character(1101), hand=(100,))
    opp = _player(_character(1101, aura=opponent_aura), hand=(100, 100))
    state = SimpleNamespace(
        players=[me, opp],
        round_number=2,
        phase="action",
        current_turn=0,
    )
    return SimpleNamespace(player_view=state, acting_player=0)


def _spec(kind, definition_id: int = 0, *, target_dice: int = 0):
    return SimpleNamespace(
        kind=kind,
        subject_definition_id=definition_id,
        target_slots=(),
        used_dice=(),
        target_dice=target_dice,
        select_card_definition_id=None,
    )


def test_state_renders_aura_names_and_hand_capacity():
    renderer = RichStateRenderer(_assets())
    text = renderer.describe_state(_context(opponent_aura=3))
    assert "附着:火" in text
    assert "无附着" in text
    assert "（1/10）" in text
    assert "对方已宣布结束=否" in text


def test_state_shows_character_tags_and_hand_card_costs_only():
    renderer = RichStateRenderer(_assets())
    context = _context()
    context.player_view.players[0].hand_cards = (
        SimpleNamespace(definition_id=332004),  # 运筹帷幄：1 同色，抓 2 张牌
    )
    text = renderer.describe_state(context)
    assert "[冰·弓]" in text                    # 1101 甘雨：冰元素/弓
    assert "运筹帷幄" in text
    assert "［1同色］" in text
    assert "我方手牌（1/10）" in text
    # Generated cards are not in the deck knowledge, so the hand repeats effects.
    assert "抓2张牌" in text


def test_knowledge_message_lists_character_skills_and_deck_effects():
    renderer = RichStateRenderer(_assets())
    task = TaskSpec(
        stage="ellin",
        display="艾琳",
        objectives=(),
        deck="keqing_jihua",
        characters=(1101,),
        cards=(332004, 332004),
        opponent_characters=(1101,),
    )
    text = renderer.knowledge_message(task)
    assert "当前对局的卡组信息" in text
    assert "整局固定" not in text            # the stale parenthetical is gone (M1)
    assert "对局资料" not in text            # the old block header is gone
    assert "甘雨[冰·弓] 生命值上限12" in text
    assert "普通攻击" in text and "流天射术" in text
    assert "我方牌组（2张）" in text
    assert "运筹帷幄［1同色］×2" in text
    assert "抓2张牌" in text
    # 甘雨's passive mentions 「冰莲」, so the glossary block closes the section.
    assert "名词解释（上文出现的状态/出战状态/召唤物）：" in text
    assert "- 冰莲：" in text


def test_knowledge_message_appends_glossary_for_named_states():
    renderer = RichStateRenderer(_assets())
    task = TaskSpec(
        stage="ellin",
        display="艾琳",
        objectives=(),
        deck="keqing_jihua",
        characters=(2301,),  # 愚人众·火之债务处理人：技能提到「潜行」
    )
    text = renderer.knowledge_message(task)
    header = "名词解释（上文出现的状态/出战状态/召唤物）："
    assert header in text
    assert "- 潜行：" in text
    # The glossary is the last block of the section and every one of its lines is
    # a "- 术语：解释" entry taken from the asset dump.
    lines = text.splitlines()
    start = lines.index(header)
    assert all(line.startswith("- ") for line in lines[start + 1:])
    assert lines[-1].startswith("- ")


def test_knowledge_message_includes_passive_skills():
    renderer = RichStateRenderer(_assets())
    task = TaskSpec(
        stage="ellin",
        display="艾琳",
        objectives=(),
        deck="keqing_jihua",
        characters=(1105,),  # 神里绫华：GTS 里带 skillType passive
    )
    text = renderer.knowledge_message(task)
    assert "神里流·霰步（被动）" in text
    assert "附属冰元素附魔" in text


def test_knowledge_message_uses_npc_skill_sheet_for_enemies():
    renderer = RichStateRenderer(_assets())
    task = TaskSpec(
        stage="ellin",
        display="艾琳",
        objectives=(),
        deck="keqing_jihua",
        opponent_characters=(3102,),
    )
    text = renderer.knowledge_message(task)
    assert "对方角色与技能" in text
    assert "冰箭丘丘人[冰]" in text
    assert "冰箭" in text and "冰元素伤害" in text
    assert "几支一起" in text


def test_state_renders_supports_summons_statuses_and_attachments():
    renderer = RichStateRenderer(_assets())
    context = _context()
    me = context.player_view.players[0]
    me.supports = (SimpleNamespace(definition_id=322005, variables={"usage": 2}),)
    me.summons = (SimpleNamespace(definition_id=115, variables={}),)
    me.combat_statuses = (SimpleNamespace(definition_id=100, variables={}),)
    me.characters[0].entities = (
        SimpleNamespace(definition_id=311101, variables={}),  # 武器
        SimpleNamespace(definition_id=106, variables={}),     # 冻结状态
    )
    text = renderer.describe_state(context)
    assert "我方支援：卯师傅(2次)" in text
    assert "我方召唤物：燃烧烈焰" in text
    assert "我方状态：抵抗之躯" in text
    assert "装备:魔导绪论" in text
    assert "状态:冻结" in text
    assert "对方已宣布结束=否" in text


def test_skill_candidate_adds_type_description_and_reaction():
    renderer = RichStateRenderer(_assets())
    # 23012 is an elemental skill dealing 1 pyro damage; hydro aura -> 蒸发.
    line = renderer.describe_candidate(
        _context(opponent_aura=2),
        _spec(OptionKind.ACTION_USE_SKILL, 23012),
        0,
    )
    assert "元素战技" in line
    assert "使用技能" not in line
    assert "造成1点火元素伤害" in line
    assert "将对附着水的出战角色触发蒸发" in line
    assert "本次伤害+2" in line


def test_candidate_requirement_and_payment_are_distinguished():
    renderer = RichStateRenderer(_assets())
    # 23012 伺机而动 requires 3 pyro dice; the engine paid 2 cryo + 1 hydro here
    # (synthetic, just to pin the two-part rendering).
    spec = _spec(OptionKind.ACTION_USE_SKILL, 23012)
    spec.used_dice = (1, 1, 2)
    line = renderer.describe_candidate(_context(), spec, 0)
    assert "需求［3火］" in line
    assert "支付［2冰+1水］" in line
    assert "花费" not in line
    assert "冰冰水" not in line


def test_skill_requirement_keeps_energy_and_shows_different_payment():
    renderer = RichStateRenderer(_assets())
    # 23013 焚毁之锋: 3 pyro + 2 energy; the engine paid 1 pyro + 2 universal.
    spec = _spec(OptionKind.ACTION_USE_SKILL, 23013)
    spec.used_dice = (3, 8, 8)
    line = renderer.describe_candidate(_context(), spec, 0)
    assert "元素爆发" in line
    assert "需求［3火+2能量］" in line
    assert "支付［1火+2万］" in line
    assert "花费" not in line


def test_skill_payment_is_omitted_when_it_equals_the_requirement():
    renderer = RichStateRenderer(_assets())
    spec = _spec(OptionKind.ACTION_USE_SKILL, 23012)  # 需求［3火］
    spec.used_dice = (3, 3, 3)                        # 支付［3火］
    line = renderer.describe_candidate(_context(), spec, 0)
    assert "需求［3火］" in line
    assert "支付" not in line
    assert "花费" not in line


def test_card_requirement_and_payment_are_shown():
    renderer = RichStateRenderer(_assets())
    # 217031 心识蕴藏之种: 3 dendro + 2 energy.
    spec = _spec(OptionKind.ACTION_PLAY_CARD, 217031)
    spec.used_dice = (7, 8, 8)
    line = renderer.describe_candidate(_context(), spec, 0)
    assert "打出卡牌" in line
    assert "需求［3草+2能量］" in line
    assert "支付［1草+2万］" in line
    assert "花费" not in line


def test_switch_without_asset_cost_reports_payment_only():
    renderer = RichStateRenderer(_assets())
    spec = _spec(OptionKind.ACTION_SWITCH_ACTIVE, 1101)
    spec.used_dice = (2,)
    line = renderer.describe_candidate(_context(), spec, 0)
    assert "支付［1水］" in line
    assert "需求" not in line
    assert "花费" not in line


def _with_full_state(context, opp_dice, *, opp_hand_ids=()):
    """Attach a minimal unmasked ``full_state`` (only dice are read by the renderer)."""
    def character(definition_id, *, active=False):
        return SimpleNamespace(definition_id=definition_id, is_active=active, defeated=False)

    opp = SimpleNamespace(
        characters=tuple(character(1101, active=True) for _ in ()),
        dice=tuple(opp_dice),
        hand_cards=tuple(SimpleNamespace(definition_id=c) for c in opp_hand_ids),
    )
    me = SimpleNamespace(characters=(), dice=(), hand_cards=())
    context.full_state = SimpleNamespace(players=(me, opp))
    return context


def test_opponent_dice_are_read_from_the_unmasked_full_state():
    renderer = RichStateRenderer(_assets())
    context = _context()
    # The engine masks the opponent's dice in *our view*, so an empty tuple there is
    # "masked", not "zero dice".
    context.player_view.players[1].dice = ()
    without = renderer.describe_state(context)
    assert "对方骰子：引擎视图未提供" in without
    assert "骰子0个" not in without
    assert "不公开" not in without          # the rules do make them public
    # The unmasked full state carries the real dice: a dedicated line, symmetric with
    # "我方骰子：", shows the types and the count.
    visible = renderer.describe_state(_with_full_state(context, (1, 3, 8)))
    assert "对方骰子：冰火万（3个）" in visible
    assert "引擎视图未提供" not in visible
    # The opponent's status line no longer carries the dice (no duplicate, and the
    # dice types are not lost mid-sentence).
    status_line = next(line for line in visible.splitlines() if line.startswith("对方："))
    assert status_line.endswith("手牌2张，牌堆 0 张")
    assert "骰子" not in status_line


def test_pre_roll_phase_reports_dice_as_not_thrown_yet():
    renderer = RichStateRenderer(_assets())
    context = _context()
    context.player_view.round_number = 0
    context.player_view.phase = "init_hands"
    # Even if a (stale/placeholder) dice list is present, before the first roll the
    # board genuinely has no dice: "无（0个）" would read like a spent pool.
    _with_full_state(context, (1, 2, 3))
    text = renderer.describe_state(context)
    assert "我方骰子：尚未投掷" in text
    assert "对方骰子：尚未投掷" in text
    assert "引擎视图未提供" not in text

    # "init_hands" alone (round already 1) is still the mulligan phase.
    context.player_view.round_number = 1
    assert "对方骰子：尚未投掷" in renderer.describe_state(context)
    # The action phase keeps the real lists.
    context.player_view.phase = "action"
    context.player_view.round_number = 2
    text = renderer.describe_state(context)
    assert "我方骰子：冰水（2个）" in text
    assert "对方骰子：冰水火（3个）" in text


def test_full_state_never_reveals_opponent_hand_contents():
    renderer = RichStateRenderer(_assets())
    context = _context()
    context.player_view.players[1].dice = ()
    context.player_view.players[1].hand_cards = (
        SimpleNamespace(definition_id=0),
        SimpleNamespace(definition_id=0),
    )
    text = renderer.describe_state(
        _with_full_state(context, (1, 2), opp_hand_ids=(332004, 214031))
    )
    assert "手牌2张" in text
    assert "运筹帷幄" not in text
    assert "抵天雷罚" not in text


def test_state_marks_charge_and_plunge_availability():
    renderer = RichStateRenderer(_assets())
    context = _context()
    context.player_view.players[0].can_charged = True
    context.player_view.players[0].can_plunging = True
    text = renderer.describe_state(context)
    assert "★出战（可重击）（可下落攻击）" in text
    context.player_view.players[0].can_charged = False
    context.player_view.players[0].can_plunging = False
    assert "可重击" not in renderer.describe_state(context)


def test_skill_candidate_without_aura_announces_attachment():
    renderer = RichStateRenderer(_assets())
    line = renderer.describe_candidate(
        _context(opponent_aura=0),
        _spec(OptionKind.ACTION_USE_SKILL, 23012),
        0,
    )
    assert "命中后为目标附着火元素" in line
    assert "将附着火" not in line          # engine-log phrasing is gone
    assert "。；" not in line              # description's own period is dropped


def test_physical_skill_candidate_is_marked():
    renderer = RichStateRenderer(_assets())
    # 11011 is Ganyu's normal attack: 2 physical damage.
    line = renderer.describe_candidate(
        _context(opponent_aura=3),
        _spec(OptionKind.ACTION_USE_SKILL, 11011),
        0,
    )
    assert "普通攻击" in line
    # A target with an aura: say explicitly that no reaction happens.
    assert "物理伤害不触发元素反应" in line
    assert "将附着" not in line
    assert "不附着元素" not in line
    # No aura: physical damage never attaches, so the hint is simply omitted.
    bare = renderer.describe_candidate(
        _context(opponent_aura=0),
        _spec(OptionKind.ACTION_USE_SKILL, 11011),
        0,
    )
    assert bare.endswith("（造成2点物理伤害）")
    assert "；" not in bare


def test_card_candidate_adds_description():
    renderer = RichStateRenderer(_assets())
    line = renderer.describe_candidate(
        _context(), _spec(OptionKind.ACTION_PLAY_CARD, 100), 0
    )
    assert "打出卡牌" in line
    assert "角色免疫冻结" in line


def test_switch_candidate_is_labeled():
    renderer = RichStateRenderer(_assets())
    line = renderer.describe_candidate(
        _context(), _spec(OptionKind.ACTION_SWITCH_ACTIVE, 1101), 0
    )
    assert "切换出战角色" in line


def test_plain_renderer_is_unchanged_and_rich_system_uses_the_new_layout():
    assets = _assets()
    context = _context(opponent_aura=3)
    plain = StateRenderer(assets)
    rich = RichStateRenderer(assets)

    plain_state = plain.describe_state(context)
    assert "附着:" not in plain_state
    assert "（1/10）" not in plain_state
    assert "对方已宣布结束" not in plain_state

    plain_line = plain.describe_candidate(
        context, _spec(OptionKind.ACTION_USE_SKILL, 23012), 0
    )
    assert "使用技能" in plain_line
    assert "元素战技" not in plain_line

    task = TaskSpec(
        stage="wagner",
        display="瓦格纳",
        objectives=("赢得胜利",),
        deck="test",
    )
    # The compact renderer keeps its historical minimal prompt …
    plain_system = plain.system_message(task=task, skills=(), rules_note="规则摘要")["content"]
    assert plain_system.startswith(
        "你是《七圣召唤》的对局助手。"
    )
    assert "规则摘要" in plain_system
    # … while the default (rich) renderer uses the fixed six-part layout.
    rich_system = rich.system_message(
        task=task, skills=(), rules_note="规则摘要"
    )["content"]
    assert rich_system != plain_system
    assert "规则摘要" not in rich_system        # rules_note is ignored
    assert "【对局流程】" in rich_system        # fixed rules text instead
    assert "当前对局的获胜目标" in rich_system


def test_system_message_segment_order_is_fixed():
    from skillloop.prompt_v2 import (
        CARD_INFO_HEADER,
        GAME_RULES_V2,
        OBJECTIVE_HEADER,
        PREVIEW_PRECEDENCE_NOTE,
        SKILLS_HEADER,
        STAGE_RULES_HEADER,
        stage_special_rules_text,
    )
    from skillloop.render import OUTPUT_CONTRACT, SYSTEM_ROLE
    from skillloop.schema import Skill, SkillConditions

    renderer = RichStateRenderer(_assets())
    task = TaskSpec(
        stage="ellin",
        display="艾琳",
        objectives=("赢得胜利",),
        deck="keqing_jihua",
        objective_types=("win",),
        characters=(1101,),
        cards=(332004,),
    )
    skill = Skill(
        skill_id="strat_x",
        skill_name="测试",
        description="测试策略。",
        conditions=SkillConditions(),
        layer="strategy",
    )
    content = renderer.system_message(task=task, skills=(skill,))["content"]
    special = stage_special_rules_text("ellin")
    assert special
    parts = [
        SYSTEM_ROLE,
        GAME_RULES_V2,
        STAGE_RULES_HEADER,
        CARD_INFO_HEADER,
        OBJECTIVE_HEADER,
        PREVIEW_PRECEDENCE_NOTE,
        SKILLS_HEADER,
        OUTPUT_CONTRACT,
    ]
    positions = [content.index(part) for part in parts]
    assert positions == sorted(positions)
    # The forecast-precedence note is a standalone line right before the skills block.
    assert f"\n{PREVIEW_PRECEDENCE_NOTE}\n{SKILLS_HEADER}\n" in content
    assert content == "\n".join([
        SYSTEM_ROLE,
        GAME_RULES_V2,
        f"{STAGE_RULES_HEADER}\n{special}",
        renderer.knowledge_message(task),
        f"{OBJECTIVE_HEADER}\n{task.describe()}\n目标判定：\n- win：赢得胜利——击倒对方全部角色。",
        PREVIEW_PRECEDENCE_NOTE,
        f"{SKILLS_HEADER}\n{skill.prompt_block()}",
        OUTPUT_CONTRACT,
    ])


def test_cryo_dendro_co_aura_is_rendered_and_hinted():
    renderer = RichStateRenderer(_assets())
    context = _context(opponent_aura=113)
    text = renderer.describe_state(context)
    assert "附着:冰草" in text
    line = renderer.describe_candidate(
        _context(opponent_aura=113),
        _spec(OptionKind.ACTION_USE_SKILL, 23012),  # 火伤
        0,
    )
    assert "附着冰+草" in line


def test_play_card_action_type_uses_card_tags_not_description():
    renderer = RichStateRenderer(_assets())
    fast = renderer.describe_candidate(
        _context(), _spec(OptionKind.ACTION_PLAY_CARD, 332006), 0
    )
    assert "快速行动，不结束行动权" in fast
    slow = renderer.describe_candidate(
        _context(), _spec(OptionKind.ACTION_PLAY_CARD, 217011), 0
    )
    assert "战斗行动（会换对方行动）" in slow


# -- description truncation policy -----------------------------------------
# 214031 抵天雷罚 is 112 chars, 217031 心识蕴藏之种 is 206 chars: both were cut at 60
# characters before, so they pin the "full text in the system prefix" rule and the
# raised (120) cap in the per-decision user message.
def test_system_knowledge_prints_full_card_text_without_ellipsis():
    renderer = RichStateRenderer(_assets())
    task = TaskSpec(
        stage="ellin",
        display="艾琳",
        objectives=(),
        deck="keqing_jihua",
        cards=(214031, 217031),
    )
    text = renderer.knowledge_message(task)
    for card_id in (214031, 217031):
        description = " ".join(str(renderer._card_info(card_id).description).split())
        assert len(description) > 100
        assert description in text
    assert "…" not in text


def test_system_knowledge_prints_full_skill_text():
    renderer = RichStateRenderer(_assets())
    task = TaskSpec(
        stage="ellin",
        display="艾琳",
        objectives=(),
        deck="keqing_jihua",
        characters=(1101,),  # 甘雨：技能说明都短于旧的 90 字符上限，仍需完整出现
    )
    text = renderer.knowledge_message(task)
    for skill_id in renderer.assets.character(1101).skill_ids:
        skill = renderer._skill_info(int(skill_id))
        if skill is None:
            continue
        description = " ".join(str(skill.description).split())
        assert description in text


def test_user_message_card_description_is_capped_at_120():
    renderer = RichStateRenderer(_assets())
    context = _context()
    context.player_view.players[0].hand_cards = (
        SimpleNamespace(definition_id=217031),  # 206 字符说明
    )
    text = renderer.describe_state(context)
    line = next(line for line in text.splitlines() if "心识蕴藏之种" in line)
    assert line.endswith("…")
    body = line.split("：", 1)[1]
    assert len(body) <= 120
    assert len(body) > 100


def test_candidate_card_description_is_capped_at_120():
    renderer = RichStateRenderer(_assets())
    line = renderer.describe_candidate(
        _context(), _spec(OptionKind.ACTION_PLAY_CARD, 217031), 0
    )
    # The candidate keeps the description as a summary, capped for per-decision cost.
    assert "…" in line
    match = line.split("（", 1)[1].rsplit("；", 1)[0]
    assert len(match) <= 120


# -- candidate hint wording ------------------------------------------------
def test_same_element_hint_says_no_new_reaction():
    renderer = RichStateRenderer(_assets())
    line = renderer.describe_candidate(
        _context(opponent_aura=3),          # 目标已附着火
        _spec(OptionKind.ACTION_USE_SKILL, 23012),  # 火伤
        0,
    )
    assert "目标已附着火，不触发新反应" in line
    assert "将附着" not in line


def test_anemo_and_geo_hint_says_no_attachment():
    renderer = RichStateRenderer(_assets())
    anemo = renderer.describe_candidate(
        _context(opponent_aura=0), _spec(OptionKind.ACTION_USE_SKILL, 15012), 0
    )
    assert "风元素伤害不附着元素" in anemo
    geo = renderer.describe_candidate(
        _context(opponent_aura=0), _spec(OptionKind.ACTION_USE_SKILL, 16012), 0
    )
    assert "岩元素伤害不附着元素" in geo


def test_elemental_tuning_shows_source_and_target_element():
    renderer = RichStateRenderer(_assets())
    spec = _spec(OptionKind.ACTION_ELEMENTAL_TUNING, 214031)  # 抵天雷罚
    spec.used_dice = (2,)      # 被转换的骰子：水
    spec.target_dice = 3       # 转换后的元素：火
    line = renderer.describe_candidate(_context(), spec, 0)
    assert "把 1 个水骰转为火骰" in line
    assert "换 水 骰" not in line          # the old bug: source rendered as result
    assert "转为火" in line


def test_elemental_tuning_without_target_dice_reports_the_spent_die_only():
    renderer = RichStateRenderer(_assets())
    spec = _spec(OptionKind.ACTION_ELEMENTAL_TUNING, 214031)  # target_dice=0
    spec.used_dice = (2,)
    line = renderer.describe_candidate(_context(), spec, 0)
    assert "换 1 个水骰" in line
    assert "转为" not in line              # never guesses the resulting element


# -- B1: the default card pool must match the asset pack ---------------------
def test_default_card_pool_matches_asset_latest_max_health():
    """Guard for B1: no pinned pool, so the board equals the asset dump.

    Our deck knowledge prints ``HP上限11`` for 2301 straight from
    ``data/assets/expert_system_assets_chs_latest.json``. If ``run_match`` ever
    resolves to an older pinned pool (``v3.3.0`` had 2301=10) the live board would
    read ``10/10`` and contradict the prompt. This test locks the premise
    "default card pool == latest asset pack" itself, not just one number.
    """
    from envs.policy import build_policy
    from npc.spec import get_stage
    from skillloop.run import npc_policy_for_stage, resolve_deck, run_match

    assets = _assets()
    try:
        stage = get_stage("blanche")
        deck = resolve_deck("keqing_shuanghuo")
        npc_deck = type(deck)(
            name="npc_blanche",
            characters=stage.deck_characters,
            cards=deck.cards,
        )
        record = run_match(
            deck,
            npc_deck,
            build_policy("heuristic", seed=1, role="player"),
            npc_policy_for_stage(stage),
            seed=1,
            max_steps=60,
            record_decisions=True,
            record_views=True,
        )
    except Exception as exc:  # engine unavailable: skip rather than fail
        pytest.skip(f"engine unavailable: {type(exc).__name__}: {exc}")
    decision = next(
        d for d in record.decisions
        if d.request_type == "action" and d.player == 0 and d.player_view
    )
    character = next(
        c for c in decision.player_view["players"][0]["characters"]
        if c["definition_id"] == 2301
    )
    assert character["max_health"] == assets.character(2301).max_health
    assert assets.character(2301).max_health == 11  # latest (v7.1.0) card face


# -- candidate selection helpers -------------------------------------------
def _action_spec(
    kind,
    *,
    subject: int = 0,
    mask: int = 0,
    targets: tuple = (),
    used: tuple = (),
    metadata: dict | None = None,
    discarded_slot: int = -1,
    discarded_card: int = 0,
):
    return LowLevelActionSpec(
        action_code=0,
        request_type=DecisionType.ACTION,
        kind=kind,
        label="x",
        subject_definition_id=subject,
        target_slots=tuple(targets),
        used_dice=tuple(used),
        switch_hand_slot_mask=mask,
        discarded_hand_slot=discarded_slot,
        discarded_card_definition_id=discarded_card,
        metadata=dict(metadata or {}),
    )


def _candidate_context(hand_ids, specs, *, round_number: int = 3, phase: str = "action"):
    def character(definition_id, *, active=True):
        return SimpleNamespace(
            definition_id=definition_id,
            health=10,
            max_health=10,
            energy=0,
            max_energy=0,
            aura=0,
            is_active=active,
            defeated=False,
        )

    me = SimpleNamespace(
        characters=(character(1403),),
        hand_cards=tuple(SimpleNamespace(definition_id=c) for c in hand_ids),
        dice=(1, 2, 3),
        declared_end=False,
    )
    opp = SimpleNamespace(
        characters=(character(1101),),
        hand_cards=(),
        dice=(),
        declared_end=False,
    )
    state = SimpleNamespace(
        players=[me, opp],
        round_number=round_number,
        phase=phase,
        current_turn=0,
    )
    return SimpleNamespace(
        player_view=state,
        acting_player=0,
        legal_low_level_codes=tuple(300 + i for i in range(len(specs))),
        legal_low_level_specs=tuple(specs),
    )


def test_candidates_keep_declare_end_and_one_action_per_hand_card():
    """B2: the 16 cap must not drop 宣布结束 or a whole hand card."""
    hand = [332004, 214031, 332006]
    specs = [
        _action_spec(OptionKind.ACTION_USE_SKILL, subject=23011),
        _action_spec(OptionKind.ACTION_USE_SKILL, subject=23012),
        _action_spec(OptionKind.ACTION_USE_SKILL, subject=23013),
    ]
    for card_id in hand:
        specs.append(_action_spec(OptionKind.ACTION_PLAY_CARD, subject=card_id, used=(1,)))
        specs.append(_action_spec(OptionKind.ACTION_PLAY_CARD, subject=card_id, used=(2,)))
        specs.append(
            _action_spec(
                OptionKind.ACTION_ELEMENTAL_TUNING,
                used=(1,),
                discarded_card=card_id,
            )
        )
    for die in (1, 2, 3, 8):
        specs.append(
            _action_spec(OptionKind.ACTION_SWITCH_ACTIVE, subject=1101, used=(die,))
        )
    specs.append(_action_spec(OptionKind.ACTION_DECLARE_END))  # engine puts it last
    assert len(specs) > 16

    context = _candidate_context(hand, specs)
    selected = select_candidates(context)
    assert len(selected) == 16
    selected_specs = [spec for _code, spec in selected]
    assert any(spec.kind == OptionKind.ACTION_DECLARE_END for spec in selected_specs)

    covered: set[int] = set()
    for spec in selected_specs:
        if spec.kind == OptionKind.ACTION_PLAY_CARD:
            covered.add(int(spec.subject_definition_id))
        elif spec.kind == OptionKind.ACTION_ELEMENTAL_TUNING:
            covered.add(int(spec.discarded_card_definition_id))
    assert set(hand) <= covered

    text = RichStateRenderer(_assets()).user_message(context, selected)["content"]
    lines = [line for line in text.splitlines() if line.startswith("[")]
    indices = [int(line[1: line.index("]")]) for line in lines]
    assert indices == list(range(len(selected)))  # continuous from 0
    assert any("宣布结束" in line for line in lines)


def test_mulligan_candidates_keep_all_keep_and_multi_card_combos():
    """H3: mulligan is keyed by slot mask, not by the first swapped card.

    A 5-card hand has 32 keep/swap combinations; the standard 16 cap cut off every
    combination that touched the 5th card, so ``SWITCH_HANDS`` gets its own cap.
    """
    hand = [100, 214031, 332004, 332006, 213031]
    specs = [
        _action_spec(OptionKind.SWITCH_HANDS, mask=mask) for mask in range(32)
    ]
    context = _candidate_context(hand, specs, round_number=0, phase="init_hands")
    selected = select_candidates(context)
    assert len(selected) == 32
    masks = [spec.switch_hand_slot_mask for _code, spec in selected]
    assert masks == sorted(masks)
    assert masks[0] == 0  # 全部保留 survives
    assert any(bin(mask).count("1") >= 2 for mask in masks)
    # The 5th card (bit 4) is part of something, not silently dropped.
    assert any(mask & (1 << 4) for mask in masks)

    text = RichStateRenderer(_assets()).user_message(context, selected)["content"]
    assert "全部保留" in text
    assert "换掉" in text

    # Other request types keep the 16 cap.
    skills = [
        _action_spec(OptionKind.ACTION_USE_SKILL, subject=23000 + index)
        for index in range(20)
    ]
    action_context = _candidate_context(hand, skills, round_number=3, phase="action")
    assert len(select_candidates(action_context)) == 16


# -- B3: effective requirement vs payment ----------------------------------
def test_effective_requirement_uses_engine_metadata_not_asset_list_price():
    renderer = RichStateRenderer(_assets())
    # 23012 asset play_cost is 3 pyro, but the engine's effective cost here is
    # 1 pyro + 1 void (any) after a discount: 需求 must follow the engine.
    spec = _action_spec(
        OptionKind.ACTION_USE_SKILL,
        subject=23012,
        used=(3,),
        metadata={"required_cost": ((3, 1), (0, 1))},
    )
    line = renderer.describe_candidate(_context(), spec, 0)
    assert "需求［1火+1任意］（已计折扣）" in line
    assert "需求［3火］" not in line
    assert "支付［1火］" in line


def test_discounted_requirement_is_marked_and_matches_by_multiset():
    renderer = RichStateRenderer(_assets())
    # 14032 星斗归位 lists 3 electro; with an artifact discount the engine charges
    # 1 electro + 1 any. The annotation says why the number is below the card text.
    discounted = _action_spec(
        OptionKind.ACTION_USE_SKILL,
        subject=14032,
        used=(4,),
        metadata={"required_cost": ((4, 1), (0, 1))},
    )
    line = renderer.describe_candidate(_context(), discounted, 0)
    assert "需求［1雷+1任意］（已计折扣）" in line

    # Same multiset, different order / vocabulary (万 vs 任意): not a discount.
    same = _action_spec(
        OptionKind.ACTION_USE_SKILL,
        subject=14032,
        used=(4, 4, 4),
        metadata={"required_cost": ((4, 3),)},
    )
    unchanged = renderer.describe_candidate(_context(), same, 0)
    assert "需求［3雷］" in unchanged
    assert "已计折扣" not in unchanged

    # No asset list price (switch / tuning) -> nothing to compare, no annotation.
    switch = _action_spec(
        OptionKind.ACTION_SWITCH_ACTIVE,
        subject=1101,
        used=(2,),
        targets=(PublicSlotTarget(owner="self", zone="character", index=0),),
        metadata={"required_cost": ((0, 1),)},
    )
    switched = renderer.describe_candidate(_context(), switch, 0)
    assert "需求［1任意］" in switched
    assert "已计折扣" not in switched


def test_output_contract_asks_for_ids_not_skill_names():
    from skillloop.render import OUTPUT_CONTRACT

    assert "本决策引用的技能 id（技能块方括号里的 id）" in OUTPUT_CONTRACT
    assert "影响本决策的技能 id" not in OUTPUT_CONTRACT


def test_free_card_requirement_is_omitted_and_payment_kept():
    renderer = RichStateRenderer(_assets())
    spec = _action_spec(
        OptionKind.ACTION_PLAY_CARD,
        subject=332004,
        used=(2,),
        metadata={"required_cost": ()},
    )
    line = renderer.describe_candidate(_context(), spec, 0)
    assert "需求" not in line
    assert "支付［1水］" in line


def test_payment_omitted_when_equal_to_effective_requirement():
    renderer = RichStateRenderer(_assets())
    spec = _action_spec(
        OptionKind.ACTION_USE_SKILL,
        subject=23012,
        used=(3, 3, 3),
        metadata={"required_cost": ((3, 3),)},
    )
    line = renderer.describe_candidate(_context(), spec, 0)
    assert "需求［3火］" in line
    assert "支付" not in line


# -- H1/H2/M4: stage rules and progress header ------------------------------
def test_stage_special_rules_text_is_read_from_config():
    from skillloop.prompt_v2 import stage_special_rules_text

    assert "艾琳" in stage_special_rules_text("ellin")
    assert "初始能量+2" in stage_special_rules_text("blanche")
    assert stage_special_rules_text("swan") == ""


def test_progress_header_never_prints_6_over_5_and_prepares_mulligan():
    task = TaskSpec(
        stage="wagner",
        display="瓦格纳",
        objectives=("5回合内胜利",),
        deck="x",
        objective_types=("win_within_rounds",),
        max_rounds=5,
    )
    context = _context()
    context.player_view.round_number = 6
    text = progress_block(task, context, ProgressSignals())
    assert text.startswith("第6轮（三星要求≤5轮；对局上限15轮）")
    assert "6/5" not in text

    context.player_view.round_number = 0
    context.player_view.phase = "init_hands"
    text = progress_block(task, context, ProgressSignals())
    assert text.startswith("准备阶段")


# -- H5: counter units ------------------------------------------------------
def test_entity_counters_carry_units():
    renderer = RichStateRenderer(_assets())
    assert renderer._entity(
        SimpleNamespace(definition_id=322005, variables={"usage": 2})
    ) == "卯师傅(2次)"
    assert renderer._entity(
        SimpleNamespace(definition_id=322005, variables={"duration": 2})
    ) == "卯师傅(2回合)"
    # Numeric variables (stacks/layers) keep a bare count.
    assert renderer._entity(
        SimpleNamespace(definition_id=322005, variables={"layer": 3})
    ) == "卯师傅(3)"


# -- H7: talent card previews its triggered skill ---------------------------
def test_talent_card_previews_triggered_skill():
    renderer = RichStateRenderer(_assets())
    context = _candidate_context(hand_ids=[214031], specs=[])
    line = renderer.describe_candidate(
        context, _action_spec(OptionKind.ACTION_PLAY_CARD, subject=214031), 0
    )
    assert "随后：星斗归位" in line
    assert "造成3点雷元素伤害" in line
    assert "命中后为目标附着雷元素" in line


# -- H8: 原激化 uses 2 charges ---------------------------------------------
def test_primordial_reaction_has_two_uses():
    from skillloop.rich_render import _REACTION_EFFECTS

    assert "可用2" in _REACTION_EFFECTS["原激化"]
    assert "可用3" not in _REACTION_EFFECTS["原激化"]


# -- M5: opponent status line is player-relative ----------------------------
def test_opponent_line_uses_player_relative_label():
    renderer = RichStateRenderer(_assets(), opponent_name="NPC(布兰琪)")
    context = _context()
    context.player_view.players[1].dice = ()  # masked in our view
    text = renderer.describe_state(context)
    assert "对方·布兰琪：" in text
    assert "NPC(布兰琪)" not in text
    assert "对方支援：" in text and "对方召唤物：" in text and "对方状态：" in text
    # Dice live on their own line now, right below the opponent's status line.
    lines = text.splitlines()
    status = next(index for index, line in enumerate(lines) if line.startswith("对方·布兰琪："))
    assert lines[status + 1] == "对方骰子：引擎视图未提供"


# -- M12: one card, several targets → description printed once -------------
def test_multi_target_card_description_is_printed_once():
    renderer = RichStateRenderer(_assets())
    context = _context()
    context.player_view.players[0].hand_cards = (
        SimpleNamespace(definition_id=332004),
    )
    target = PublicSlotTarget(owner="self", zone="character", index=0)
    candidates = []
    for index, die in enumerate((1, 2, 3)):
        spec = _action_spec(
            OptionKind.ACTION_PLAY_CARD,
            subject=332004,
            targets=(target,),
            used=(die,),
        )
        candidates.append((500 + index, spec))
    text = renderer.user_message(context, candidates)["content"]
    body = text.split("合法动作：", 1)[1]
    assert body.count("抓2张牌") == 1
    assert body.count("→") >= 2


def test_system_parts_omit_stage_rules_when_stage_has_none():
    renderer = RichStateRenderer(_assets())
    task = TaskSpec(stage="swan", display="斯万", objectives=(), deck="x")
    segments = dict(renderer.system_parts(task=task, skills=()))
    assert "关卡特殊规则" not in segments
    assert "游戏规则（GAME_RULES_V2）" in segments


# -- H4: remaining deck size is shown for both sides -----------------------
def test_state_reports_remaining_pile_for_both_sides():
    renderer = RichStateRenderer(_assets())
    context = _context()
    context.player_view.players[0].pile_cards = tuple(
        SimpleNamespace(definition_id=0, id=index) for index in range(13)
    )
    # Opponent pile cards are masked (definition_id=0) but the count is public.
    context.player_view.players[1].pile_cards = tuple(
        SimpleNamespace(definition_id=0, id=index) for index in range(9)
    )
    text = renderer.describe_state(context)
    assert "我方牌堆：13 张" in text
    assert "牌堆 9 张" in text


# -- engine preview: the settled forecast in the candidate line -------------
# ``Action.preview`` is what the engine will actually settle (shields, buffs and the
# current aura included); the card text alone cannot know any of it.
def _id_character(
    instance_id: int,
    definition_id: int,
    *,
    active: bool = False,
    aura: int = 0,
    health: int = 10,
):
    return SimpleNamespace(
        id=instance_id,
        definition_id=definition_id,
        health=health,
        max_health=health,
        energy=0,
        max_energy=0,
        aura=aura,
        is_active=active,
        defeated=False,
    )


def _id_context(*, opp_aura: int = 0, our_active: int = 1101):
    our_ids = {1101: -500000, 1102: -500001, 1103: -500002}
    me = SimpleNamespace(
        characters=tuple(
            _id_character(our_ids[definition_id], definition_id, active=definition_id == our_active)
            for definition_id in (1101, 1102, 1103)
        ),
        hand_cards=(),
        dice=(1, 2, 3),
        declared_end=False,
    )
    opp = SimpleNamespace(
        characters=(_id_character(-500033, 3302, active=True, aura=opp_aura),),
        hand_cards=(),
        dice=(),
        declared_end=False,
    )
    state = SimpleNamespace(
        players=[me, opp], round_number=2, phase="action", current_turn=0
    )
    return SimpleNamespace(player_view=state, acting_player=0)


def _preview_spec(
    kind,
    definition_id: int = 0,
    *,
    preview=(),
    required_cost=(),
    used=(),
    targets=(),
):
    return _action_spec(
        kind,
        subject=definition_id,
        used=used,
        targets=targets,
        metadata={
            "required_cost": tuple(required_cost),
            **({"preview": list(preview)} if preview else {}),
        },
    )


def _damage_preview(
    *,
    damage_type: int = 3,
    value: int = 4,
    target_id: int = -500033,
    target_definition_id: int = 3302,
    reaction_type: int = 0,
    cause_defeated: bool = False,
    old_health: int = 10,
    new_health: int = 6,
    heal_kind: int = 0,
):
    return {
        "kind": "damage",
        "damage_type": damage_type,
        "value": value,
        "target_id": target_id,
        "target_definition_id": target_definition_id,
        "reaction_type": reaction_type,
        "cause_defeated": cause_defeated,
        "old_health": old_health,
        "new_health": new_health,
        "heal_kind": heal_kind,
    }


def test_preview_damage_is_used_instead_of_the_static_reaction_hint():
    renderer = RichStateRenderer(_assets())
    spec = _preview_spec(
        OptionKind.ACTION_USE_SKILL,
        23012,
        required_cost=((3, 1), (0, 1)),
        used=(3,),
        preview=(_damage_preview(value=4, reaction_type=101, new_health=6),),
    )
    line = renderer.describe_candidate(_id_context(opp_aura=2), spec, 0)
    # The settled number (4, melt) is stated once, with the target's name.
    assert "；预计：对火斧丘丘暴徒造成4点火元素伤害（融化）" in line
    # The static description is kept but labelled as the card face: its "造成1点"
    # must not read as the outcome.
    assert "（卡面：造成1点火元素伤害" in line
    assert "将对附着水的出战角色触发蒸发" not in line
    assert line.count("预计") == 1


def test_preview_without_reaction_keeps_the_hint_and_states_the_forecast():
    renderer = RichStateRenderer(_assets())
    spec = _preview_spec(
        OptionKind.ACTION_USE_SKILL,
        23012,
        preview=(_damage_preview(value=2, new_health=8),),
    )
    line = renderer.describe_candidate(_id_context(), spec, 0)
    assert "预计：对火斧丘丘暴徒造成2点火元素伤害" in line
    assert "命中后为目标附着火元素" not in line


def test_preview_shielded_zero_damage_and_health_correction():
    renderer = RichStateRenderer(_assets())
    # 寒冰大盾: the card face says 2, the settled value is 0 and health is unchanged.
    shielded = renderer.describe_candidate(
        _id_context(),
        _preview_spec(
            OptionKind.ACTION_USE_SKILL,
            11011,
            preview=(
                _damage_preview(
                    damage_type=0, value=0, old_health=10, new_health=10, reaction_type=0
                ),
            ),
        ),
        0,
    )
    assert "预计：对火斧丘丘暴徒造成0点物理伤害" in shielded
    assert "HP" not in shielded.split("预计：", 1)[1]
    # A damage reduction makes the health delta disagree with the nominal value.
    reduced = renderer.describe_candidate(
        _id_context(),
        _preview_spec(
            OptionKind.ACTION_USE_SKILL,
            11011,
            preview=(
                _damage_preview(
                    damage_type=0, value=2, old_health=10, new_health=9, reaction_type=0
                ),
            ),
        ),
        0,
    )
    assert "造成2点物理伤害（HP 10→9）" in reduced


def test_preview_defeat_is_marked():
    renderer = RichStateRenderer(_assets())
    line = renderer.describe_candidate(
        _id_context(),
        _preview_spec(
            OptionKind.ACTION_USE_SKILL,
            23013,
            preview=(
                _damage_preview(value=6, old_health=3, new_health=0, cause_defeated=True),
            ),
        ),
        0,
    )
    assert "预计：对火斧丘丘暴徒造成6点火元素伤害，击倒（HP 3→0）" in line


def test_preview_heal_is_rendered_and_zero_heal_is_dropped():
    renderer = RichStateRenderer(_assets())
    heal = renderer.describe_candidate(
        _id_context(),
        _preview_spec(
            OptionKind.ACTION_PLAY_CARD,
            332004,
            preview=(
                _damage_preview(
                    damage_type=9,
                    value=2,
                    target_id=-500000,
                    target_definition_id=1101,
                    old_health=3,
                    new_health=5,
                    heal_kind=1,
                ),
            ),
        ),
        0,
    )
    assert "预计：对甘雨治疗2" in heal
    # A play card's description is labelled the same way ("卡面：") because the heal
    # entry is a preview; the action-type note follows it inside the same parens.
    assert "（卡面：抓2张牌；" in heal
    # A heal entry on a full-HP character carries value=0: "治疗0" is noise.
    noop = renderer.describe_candidate(
        _id_context(),
        _preview_spec(
            OptionKind.ACTION_PLAY_CARD,
            332004,
            preview=(
                _damage_preview(
                    damage_type=9,
                    value=0,
                    target_id=-500000,
                    target_definition_id=1101,
                    old_health=10,
                    new_health=10,
                    heal_kind=1,
                ),
            ),
        ),
        0,
    )
    assert "预计" not in noop


def test_preview_multi_target_is_joined_and_capped():
    renderer = RichStateRenderer(_assets())
    entries = [
        _damage_preview(value=1, new_health=9 - index)
        for index in range(8)
    ]
    line = renderer.describe_candidate(
        _id_context(),
        _preview_spec(OptionKind.ACTION_USE_SKILL, 23012, preview=entries),
        0,
    )
    forecast = line.split("预计：", 1)[1]
    assert forecast.count("；") == 5          # six entries, then "等8项"
    assert forecast.endswith("等8项")


def test_preview_target_name_falls_back_to_definition_then_generic():
    renderer = RichStateRenderer(_assets())
    # Unknown instance id, but a known definition id -> the character's name.
    by_definition = renderer.describe_candidate(
        _id_context(),
        _preview_spec(
            OptionKind.ACTION_USE_SKILL,
            23012,
            preview=(
                _damage_preview(
                    value=1, target_id=0, target_definition_id=1101, new_health=9
                ),
            ),
        ),
        0,
    )
    assert "对甘雨造成1点火元素伤害" in by_definition
    # Neither id resolves -> generic wording, never a fabricated name.
    unknown = renderer.describe_candidate(
        _id_context(),
        _preview_spec(
            OptionKind.ACTION_USE_SKILL,
            23012,
            preview=(
                _damage_preview(
                    value=1, target_id=0, target_definition_id=0, new_health=9
                ),
            ),
        ),
        0,
    )
    assert "对目标造成1点火元素伤害" in unknown


def test_preview_absent_keeps_the_old_static_behaviour():
    renderer = RichStateRenderer(_assets())
    line = renderer.describe_candidate(
        _id_context(opp_aura=2), _action_spec(OptionKind.ACTION_USE_SKILL, subject=23012), 0
    )
    assert "预计" not in line
    assert "将对附着水的出战角色触发蒸发" in line
    # Without a forecast the description is not a "card face" caveat: no prefix.
    assert "（造成1点火元素伤害" in line
    assert "卡面" not in line


def test_preview_aura_only_entries_add_no_line():
    renderer = RichStateRenderer(_assets())
    line = renderer.describe_candidate(
        _id_context(),
        _preview_spec(
            OptionKind.ACTION_USE_SKILL,
            23012,
            preview=(
                {
                    "kind": "aura",
                    "element_type": 3,
                    "target_id": -500033,
                    "target_definition_id": 3302,
                    "reaction_type": 0,
                },
            ),
        ),
        0,
    )
    assert "预计" not in line
    # A non-damage preview (aura attachment / summon) is not a damage forecast, so the
    # card text keeps its plain parens.
    assert "（造成1点火元素伤害" in line
    assert "卡面" not in line


def test_preview_entries_are_extracted_from_the_engine_message():
    """The adapter stores a JSON-safe, capped projection of ``Action.preview``."""
    from gitcg.proto import rpc_pb2

    from reps.action_adapter import PREVIEW_MAX_ENTRIES, _action_preview

    action = rpc_pb2.Action()
    damage = action.preview.add().damage
    damage.damage_type = 3
    damage.value = 4
    damage.target_id = -500033
    damage.target_definition_id = 3302
    damage.reaction_type = 101
    damage.cause_defeated = True
    damage.old_health = 3
    damage.new_health = 0
    aura = action.preview.add().apply_aura
    aura.element_type = 3
    aura.target_id = -500033
    aura.target_definition_id = 3302
    # Other mutations are ignored.
    action.preview.add().modify_entity_var.variable_name = "energy"
    for _ in range(20):
        action.preview.add().damage.value = 1

    entries = _action_preview(action)
    assert len(entries) == PREVIEW_MAX_ENTRIES
    assert entries[0] == {
        "kind": "damage",
        "damage_type": 3,
        "value": 4,
        "target_id": -500033,
        "target_definition_id": 3302,
        "reaction_type": 101,
        "cause_defeated": True,
        "old_health": 3,
        "new_health": 0,
        "heal_kind": 0,
    }
    assert entries[1] == {
        "kind": "aura",
        "element_type": 3,
        "target_id": -500033,
        "target_definition_id": 3302,
        "reaction_type": 0,
    }
    import json

    json.dumps(entries)  # JSON-safe by construction


def test_energy_payment_says_the_charges_are_spent():
    renderer = RichStateRenderer(_assets())
    spec = _action_spec(
        OptionKind.ACTION_USE_SKILL,
        subject=23013,
        used=(3, 8, 8),
        metadata={"required_cost": ((3, 3), (9, 2))},
    )
    line = renderer.describe_candidate(_context(), spec, 0)
    assert "需求［3火+2能量］" in line
    assert "支付［1火+2万］（清空2能量）" in line
    # No separate payment line -> the requirement already names the energy.
    equal = _action_spec(
        OptionKind.ACTION_USE_SKILL,
        subject=23013,
        used=(3, 3, 3),
        metadata={"required_cost": ((3, 3),)},
    )
    assert "清空" not in renderer.describe_candidate(_context(), equal, 0)


def test_switch_candidate_names_the_current_active_not_the_target_twice():
    renderer = RichStateRenderer(_assets())
    spec = _action_spec(
        OptionKind.ACTION_SWITCH_ACTIVE,
        subject=1102,
        used=(1,),
        targets=(PublicSlotTarget(owner="self", zone="character", index=1),),
    )
    line = renderer.describe_candidate(_id_context(), spec, 3)
    assert line.startswith("[3] 切换出战：当前·甘雨 → 迪奥娜")
    assert "→ 我方·迪奥娜" not in line
    assert "支付［1冰］" in line
