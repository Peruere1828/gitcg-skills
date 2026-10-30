# Ported from Rebel_base_RL research/world_model (AGPL-3.0, github.com/.../Rebel_base_RL) by genshin-GITCG.
# Keep upstream semantics; adapt imports only. See NOTICE.md.
from __future__ import annotations

from reps.schema import DecisionContext

from .common import choose_elemental_focus_reroll, make_rule_config

RULE_CONFIG = make_rule_config(
    slug="shenhe_double_swirl",
    opener="申鹤",
    carry=("特瓦林", "申鹤", "早柚"),
    bench=("申鹤", "早柚"),
    preferred_elements=("GCG_TAG_ELEMENT_ANEMO", "GCG_TAG_ELEMENT_CRYO"),
    style="swirl_cryo",
)


def choose_active(agent, context: DecisionContext):
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)
    if aura > 0:
        preferred = ("早柚", "特瓦林", "申鹤")
    elif round_number <= 2:
        preferred = ("申鹤", "早柚", "特瓦林")
    else:
        preferred = ("特瓦林", "早柚", "申鹤")
    return agent._choose_active_by_names(context, preferred, require_safe_target=True)


def choose_reroll(agent, context: DecisionContext):
    active_name = agent._active_character_name(context)
    if active_name == "申鹤":
        primary = ("GCG_TAG_ELEMENT_CRYO",)
        secondary = ("GCG_TAG_ELEMENT_ANEMO",)
    else:
        primary = ("GCG_TAG_ELEMENT_ANEMO",)
        secondary = ("GCG_TAG_ELEMENT_CRYO",)
    return choose_elemental_focus_reroll(
        agent,
        context,
        primary=primary,
        secondary=secondary,
        rule_id="reroll.shenhe_double_swirl_curve",
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
            card_names=("万家灶火", "风龙废墟", "「沃陆之邦」", "化种匣", "元素共鸣：迅捷之风", "元素共鸣：交织之风", "最好的伙伴！", "交给我吧！"),
            rule_id="action.shenhe_double_swirl_build_engine",
            min_tactical_score=1,
        )
        if choice is not None:
            return choice

    if injured_exists:
        choice = agent._play_named_card(
            context,
            card_names=("赦免宣告", "快快缝补术", "丰稔之赐"),
            rule_id="action.shenhe_double_swirl_patch_team",
        )
        if choice is not None and round_number >= 3:
            return choice

    if active_name == "申鹤":
        if aura <= 0:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_E", "GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A"),
                rule_id="action.shenhe_double_swirl_setup_cryo",
            )
            if choice is not None:
                return choice
        if aura > 0 and round_number <= 3:
            return agent._switch_to_named_targets(
                context,
                target_names=("早柚", "特瓦林"),
                rule_id="action.shenhe_double_swirl_handoff_finish",
                require_safe_target=True,
            )
        if round_number >= 5:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A", "GCG_SKILL_TAG_E"),
                rule_id="action.shenhe_double_swirl_cryo_pressure",
            )
            if choice is not None:
                return choice
        return None

    if active_name == "早柚":
        choice = agent._play_named_card(
            context,
            card_names=("偷懒的新方法", "黄金剧团"),
            rule_id="action.shenhe_double_swirl_arm_sayu",
        )
        if choice is not None:
            return choice
        if aura > 0:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_E", "GCG_SKILL_TAG_A"),
                rule_id="action.shenhe_double_swirl_swirl_finish",
            )
            if choice is not None:
                return choice
        if aura <= 0 and round_number <= 3:
            return agent._switch_to_named_targets(
                context,
                target_names=("申鹤",),
                rule_id="action.shenhe_double_swirl_reset_cryo",
                require_safe_target=True,
            )
        if round_number >= 4:
            return agent._switch_to_named_targets(
                context,
                target_names=("特瓦林",),
                rule_id="action.shenhe_double_swirl_rotate_to_dvalin",
                require_safe_target=True,
            )
        return None

    if active_name == "特瓦林":
        choice = agent._play_named_card(
            context,
            card_names=("毁裂风涡", "黄金剧团"),
            rule_id="action.shenhe_double_swirl_arm_dvalin",
        )
        if choice is not None:
            return choice
        if aura > 0 or round_number >= 3:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_E", "GCG_SKILL_TAG_A"),
                rule_id="action.shenhe_double_swirl_dvalin_finish",
            )
            if choice is not None:
                return choice
        return agent._switch_to_named_targets(
            context,
            target_names=("申鹤", "早柚"),
            rule_id="action.shenhe_double_swirl_reset_chain",
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
    injured_exists = any(
        int(character.health) < int(character.max_health)
        for character in agent._player(context).characters
        if not character.defeated
    )
    if name == "偷懒的新方法":
        return 5 if active_name == "早柚" else -3
    if name == "毁裂风涡":
        return 5 if active_name == "特瓦林" else -3
    if name in {"万家灶火", "风龙废墟", "「沃陆之邦」", "化种匣"}:
        return 4 if round_number <= 3 else 1
    if name == "元素共鸣：迅捷之风":
        return 4 if round_number <= 3 or aura > 0 else 1
    if name == "元素共鸣：交织之风":
        return 3 if active_name in {"早柚", "特瓦林"} else 0
    if name == "交给我吧！":
        return 3 if round_number <= 3 and active_name in {"申鹤", "早柚"} else -1
    if name == "白垩之术":
        return 3 if active_name == "申鹤" else 0
    if name in {"赦免宣告", "快快缝补术", "丰稔之赐"}:
        return 3 if injured_exists else -1
    if name == "黄金剧团":
        return 3 if active_name in {"早柚", "特瓦林"} else 0
    if name == "最好的伙伴！":
        return 3 if round_number <= 2 else 0
    if name == "奇瑰之汤":
        return -2 if round_number <= 2 else 1
    return 0
