# Ported from Rebel_base_RL research/world_model (AGPL-3.0, github.com/.../Rebel_base_RL) by genshin-GITCG.
# Keep upstream semantics; adapt imports only. See NOTICE.md.
from __future__ import annotations

from reps.schema import DecisionContext

from .common import choose_elemental_focus_reroll, make_rule_config

RULE_CONFIG = make_rule_config(
    slug="unyielding_geo",
    opener="钟离",
    carry=("黄金王兽", "嘉明", "钟离"),
    bench=("嘉明", "钟离"),
    preferred_elements=("GCG_TAG_ELEMENT_GEO", "GCG_TAG_ELEMENT_PYRO"),
    style="crystallize_pressure",
)


def choose_active(agent, context: DecisionContext):
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)
    if aura > 0:
        preferred = ("黄金王兽", "钟离", "嘉明")
    elif round_number <= 2:
        preferred = ("钟离", "嘉明", "黄金王兽")
    else:
        preferred = ("嘉明", "黄金王兽", "钟离")
    return agent._choose_active_by_names(context, preferred, require_safe_target=True)


def choose_reroll(agent, context: DecisionContext):
    active_name = agent._active_character_name(context)
    if active_name == "嘉明":
        primary = ("GCG_TAG_ELEMENT_PYRO",)
        secondary = ("GCG_TAG_ELEMENT_GEO",)
    else:
        primary = ("GCG_TAG_ELEMENT_GEO",)
        secondary = ("GCG_TAG_ELEMENT_PYRO",)
    return choose_elemental_focus_reroll(
        agent,
        context,
        primary=primary,
        secondary=secondary,
        rule_id="reroll.unyielding_geo_curve",
    )


def choose_action(agent, context: DecisionContext):
    active_name = agent._active_character_name(context)
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)
    injured_exists = any(
        int(character.health) < int(character.max_health)
        for character in agent._player(context).characters
        if not character.defeated
    )

    if round_number <= 2:
        choice = agent._play_named_card(
            context,
            card_names=("桓那兰那", "风龙废墟", "欧庇克莱歌剧院", "派蒙", "凯瑟琳", "卯师傅", "立本", "阿伽娅", "岩与契约", "最好的伙伴！"),
            rule_id="action.unyielding_geo_build_engine",
            min_tactical_score=1,
        )
        if choice is not None:
            return choice

    if injured_exists:
        choice = agent._play_named_card(
            context,
            card_names=("缤纷马卡龙", "沉玉茶露"),
            rule_id="action.unyielding_geo_patch_team",
        )
        if choice is not None and round_number >= 3:
            return choice

    if active_name == "钟离":
        choice = agent._play_named_card(
            context,
            card_names=("炊金馔玉", "贯虹之槊", "千岩牢固"),
            rule_id="action.unyielding_geo_arm_zhongli",
        )
        if choice is not None:
            return choice
        if round_number <= 2:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_E", "GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A"),
                rule_id="action.unyielding_geo_shield_setup",
            )
            if choice is not None:
                return choice
        if aura > 0 and round_number >= 3:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_E", "GCG_SKILL_TAG_A"),
                rule_id="action.unyielding_geo_guard_pressure",
            )
            if choice is not None:
                return choice
        return agent._switch_to_named_targets(
            context,
            target_names=("嘉明", "黄金王兽"),
            rule_id="action.unyielding_geo_reset_chain",
            require_safe_target=True,
        )

    if active_name == "嘉明":
        if aura <= 0:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_E", "GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A"),
                rule_id="action.unyielding_geo_apply_pyro",
            )
            if choice is not None:
                return choice
        if aura > 0 and round_number >= 4:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A", "GCG_SKILL_TAG_E"),
                rule_id="action.unyielding_geo_gaming_pressure",
            )
            if choice is not None:
                return choice
        return agent._switch_to_named_targets(
            context,
            target_names=("黄金王兽", "钟离"),
            rule_id="action.unyielding_geo_handoff_geo",
            require_safe_target=True,
        )

    if active_name == "黄金王兽":
        choice = agent._play_named_card(
            context,
            card_names=("异兽侵蚀",),
            rule_id="action.unyielding_geo_arm_wolflord",
        )
        if choice is not None:
            return choice
        if aura > 0:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_E", "GCG_SKILL_TAG_A"),
                rule_id="action.unyielding_geo_finish_window",
            )
            if choice is not None:
                return choice
        if round_number >= 5:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A", "GCG_SKILL_TAG_E"),
                rule_id="action.unyielding_geo_geo_pressure",
            )
            if choice is not None:
                return choice
        return agent._switch_to_named_targets(
            context,
            target_names=("嘉明", "钟离"),
            rule_id="action.unyielding_geo_reset_chain",
            require_safe_target=True,
        )
    return None


def card_context_bonus(agent, card, context: DecisionContext, mode: str) -> int:
    if card is None:
        return 0
    name = str(card.name)
    round_number = agent._round_number(context)
    active_name = agent._active_character_name(context)
    aura = agent._opponent_active_aura(context)
    dice_count = len(agent._player(context).dice)
    injured_exists = any(
        int(character.health) < int(character.max_health)
        for character in agent._player(context).characters
        if not character.defeated
    )
    if name == "炊金馔玉":
        return 5 if active_name == "钟离" else -3
    if name == "异兽侵蚀":
        return 5 if active_name == "黄金王兽" else -3
    if name in {"桓那兰那", "风龙废墟", "欧庇克莱歌剧院", "派蒙", "凯瑟琳", "卯师傅", "立本", "阿伽娅"}:
        return 4 if round_number <= 3 else 1
    if name == "元素共鸣：交织之岩":
        return 3 if active_name in {"钟离", "黄金王兽"} and dice_count <= 4 else 0
    if name == "元素共鸣：坚定之岩":
        return 4 if aura > 0 and active_name in {"钟离", "黄金王兽"} else -2
    if name == "岩与契约":
        return 4 if dice_count <= 2 else 1
    if name == "贯虹之槊":
        return 4 if active_name == "钟离" else 0
    if name == "千岩牢固":
        return 4 if active_name == "钟离" else 0
    if name == "仙跳墙":
        return 4 if agent._has_ready_burst(context) else -1
    if name in {"缤纷马卡龙", "沉玉茶露"}:
        return 3 if injured_exists else 0
    if name == "突角龙":
        return 2 if aura > 0 or round_number >= 4 else 0
    if name == "最好的伙伴！":
        return 3 if round_number <= 2 else 0
    if name == "奇瑰之汤":
        return -2 if round_number <= 2 else 1
    return 0
