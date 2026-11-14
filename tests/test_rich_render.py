from __future__ import annotations

from types import SimpleNamespace

import pytest

from reps.schema import OptionKind
from skillloop.render import StateRenderer, TaskSpec
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


def _spec(kind, definition_id: int = 0):
    return SimpleNamespace(
        kind=kind,
        subject_definition_id=definition_id,
        target_slots=(),
        used_dice=(),
        select_card_definition_id=None,
    )


def test_state_renders_aura_names_and_hand_capacity():
    renderer = RichStateRenderer(_assets())
    text = renderer.describe_state(_context(opponent_aura=3))
    assert "附着:火" in text
    assert "无附着" in text
    assert "（1/10）" in text
    assert "对方已宣布结束=False" in text


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
    assert "规则要点（3.3）" in text
    assert "超载：本次伤害+2" in text
    assert "对局资料" in text
    assert "甘雨[冰·弓] HP上限12" in text
    assert "普通攻击" in text and "流天射术" in text
    assert "我方牌组（2张）" in text
    assert "运筹帷幄［1同色］×2" in text
    assert "抓2张牌" in text


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
    assert "我方支援：卯师傅(2)" in text
    assert "我方召唤物：燃烧烈焰" in text
    assert "我方状态：抵抗之躯" in text
    assert "装备:魔导绪论" in text
    assert "状态:冻结" in text
    assert "对方已宣布结束=False" in text


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


def test_candidate_dice_cost_uses_grouped_notation():
    renderer = RichStateRenderer(_assets())
    spec = _spec(OptionKind.ACTION_USE_SKILL, 23012)
    spec.used_dice = (1, 1, 2)
    line = renderer.describe_candidate(_context(), spec, 0)
    assert "花费［2冰+1水］" in line
    assert "花费冰冰水" not in line


def test_skill_candidate_without_aura_announces_attachment():
    renderer = RichStateRenderer(_assets())
    line = renderer.describe_candidate(
        _context(opponent_aura=0),
        _spec(OptionKind.ACTION_USE_SKILL, 23012),
        0,
    )
    assert "将附着火" in line


def test_physical_skill_candidate_is_marked():
    renderer = RichStateRenderer(_assets())
    # 11011 is Ganyu's normal attack: 2 physical damage.
    line = renderer.describe_candidate(
        _context(opponent_aura=3),
        _spec(OptionKind.ACTION_USE_SKILL, 11011),
        0,
    )
    assert "普通攻击" in line
    assert "物理伤害（不附着元素）" in line
    assert "将附着" not in line


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


def test_plain_renderer_is_unchanged_and_system_message_is_inherited():
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
    assert rich.system_message(task=task, skills=()) == plain.system_message(
        task=task, skills=()
    )


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
