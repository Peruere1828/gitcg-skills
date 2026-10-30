# Ported from Rebel_base_RL research/world_model (AGPL-3.0, github.com/.../Rebel_base_RL) by genshin-GITCG.
# Keep upstream semantics; adapt imports only. See NOTICE.md.
from __future__ import annotations

from reps.schema import DecisionContext

from .common import choose_elemental_focus_reroll, make_rule_config

RULE_CONFIG = make_rule_config(
    slug="aywolfskirk",
    opener="爱可菲",
    carry=("丝柯克", "爱可菲", "黄金王兽"),
    bench=("爱可菲", "黄金王兽"),
    preferred_elements=("GCG_TAG_ELEMENT_CRYO", "GCG_TAG_ELEMENT_GEO"),
    style="pressure",
)


def choose_active(agent, context: DecisionContext):
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)
    if aura > 0:
        preferred = ("丝柯克", "黄金王兽", "爱可菲")
    elif round_number <= 2:
        preferred = ("爱可菲", "黄金王兽", "丝柯克")
    else:
        preferred = ("黄金王兽", "丝柯克", "爱可菲")
    return agent._choose_active_by_names(context, preferred, require_safe_target=True)


def choose_reroll(agent, context: DecisionContext):
    active_name = agent._active_character_name(context)
    if active_name in {"丝柯克", "爱可菲"}:
        primary = ("GCG_TAG_ELEMENT_CRYO",)
        secondary = ("GCG_TAG_ELEMENT_GEO",)
    else:
        primary = ("GCG_TAG_ELEMENT_GEO",)
        secondary = ("GCG_TAG_ELEMENT_CRYO",)
    return choose_elemental_focus_reroll(
        agent,
        context,
        primary=primary,
        secondary=secondary,
        rule_id="reroll.aywolfskirk_curve",
    )


def choose_action(agent, context: DecisionContext):
    active_name = agent._active_character_name(context)
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)

    if round_number <= 2:
        choice = agent._play_named_card(
            context,
            card_names=("万家灶火", "晨曦酒庄", "鸣神大社", "风龙废墟", "「花羽会」", "「沃陆之邦」", "派蒙", "温妮莎传奇", "最好的伙伴！"),
            rule_id="action.aywolfskirk_build_engine",
            min_tactical_score=1,
        )
        if choice is not None:
            return choice

    if active_name == "爱可菲":
        if aura <= 0:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_E", "GCG_SKILL_TAG_A", "GCG_SKILL_TAG_Q"),
                rule_id="action.aywolfskirk_setup_cryo",
            )
            if choice is not None:
                return choice
        if aura > 0 and round_number <= 3:
            choice = agent._switch_to_named_targets(
                context,
                target_names=("丝柯克", "黄金王兽"),
                rule_id="action.aywolfskirk_handoff_finish",
                require_safe_target=True,
            )
            if choice is not None:
                return choice
        if round_number >= 5:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A", "GCG_SKILL_TAG_E"),
                rule_id="action.aywolfskirk_cryo_pressure",
            )
            if choice is not None:
                return choice
        return None

    if active_name == "黄金王兽":
        choice = agent._play_named_card(
            context,
            card_names=("异兽侵蚀", "沙王的投影"),
            rule_id="action.aywolfskirk_arm_wolflord",
        )
        if choice is not None:
            return choice
        if aura > 0:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_E", "GCG_SKILL_TAG_A"),
                rule_id="action.aywolfskirk_geo_pressure",
            )
            if choice is not None:
                return choice
        if round_number >= 4:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A", "GCG_SKILL_TAG_E"),
                rule_id="action.aywolfskirk_geo_pressure",
            )
            if choice is not None:
                return choice
        return agent._switch_to_named_targets(
            context,
            target_names=("爱可菲", "丝柯克"),
            rule_id="action.aywolfskirk_reset_chain",
            require_safe_target=True,
        )

    if active_name == "丝柯克":
        choice = agent._play_named_card(
            context,
            card_names=("湮远",),
            rule_id="action.aywolfskirk_arm_skirk",
        )
        if choice is not None:
            return choice
        if aura > 0:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A", "GCG_SKILL_TAG_E"),
                rule_id="action.aywolfskirk_skirk_finish",
            )
            if choice is not None:
                return choice
        if round_number >= 4:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A", "GCG_SKILL_TAG_E"),
                rule_id="action.aywolfskirk_skirk_pressure",
            )
            if choice is not None:
                return choice
        return agent._switch_to_named_targets(
            context,
            target_names=("爱可菲", "黄金王兽"),
            rule_id="action.aywolfskirk_reset_chain",
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
    if name == "湮远":
        return 5 if active_name == "丝柯克" else -3
    if name == "异兽侵蚀":
        return 5 if active_name == "黄金王兽" else -3
    if name in {"万家灶火", "晨曦酒庄", "鸣神大社", "风龙废墟", "「花羽会」", "「沃陆之邦」", "派蒙"}:
        return 4 if round_number <= 3 else 1
    if name == "元素共鸣：交织之冰":
        return 3 if active_name in {"丝柯克", "爱可菲"} else 0
    if name == "元素共鸣：粉碎之冰":
        return 4 if aura > 0 and active_name in {"丝柯克", "爱可菲"} else -2
    if name == "温妮莎传奇":
        return 4 if round_number <= 2 else 1
    if name == "幻戏倒计时：3":
        return 3 if round_number >= 3 else 1
    if name == "沙王的投影":
        return 4 if active_name == "黄金王兽" else 0
    if name == "贯月矢":
        return 2 if active_name == "爱可菲" else 0
    if name == "最好的伙伴！":
        return 3 if round_number <= 2 else 0
    if name == "奇瑰之汤":
        return -2 if round_number <= 2 else 1
    return 0
