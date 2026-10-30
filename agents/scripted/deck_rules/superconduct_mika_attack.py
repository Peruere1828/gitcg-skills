# Ported from Rebel_base_RL research/world_model (AGPL-3.0, github.com/.../Rebel_base_RL) by genshin-GITCG.
# Keep upstream semantics; adapt imports only. See NOTICE.md.
from __future__ import annotations

from reps.schema import DecisionContext

from .common import choose_elemental_focus_reroll, make_rule_config

RULE_CONFIG = make_rule_config(
    slug="superconduct_mika_attack",
    opener="雷音权现",
    carry=("爱可菲", "雷音权现", "米卡"),
    bench=("雷音权现", "米卡"),
    preferred_elements=("GCG_TAG_ELEMENT_CRYO", "GCG_TAG_ELEMENT_ELECTRO"),
    style="superconduct",
)


def choose_active(agent, context: DecisionContext):
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)
    if aura > 0:
        preferred = ("米卡", "爱可菲", "雷音权现")
    elif round_number <= 2:
        preferred = ("雷音权现", "爱可菲", "米卡")
    else:
        preferred = ("爱可菲", "米卡", "雷音权现")
    return agent._choose_active_by_names(context, preferred, require_safe_target=True)


def choose_reroll(agent, context: DecisionContext):
    active_name = agent._active_character_name(context)
    round_number = agent._round_number(context)
    if round_number <= 2 or active_name == "雷音权现":
        primary = ("GCG_TAG_ELEMENT_ELECTRO",)
        secondary = ("GCG_TAG_ELEMENT_CRYO",)
    else:
        primary = ("GCG_TAG_ELEMENT_CRYO",)
        secondary = ("GCG_TAG_ELEMENT_ELECTRO",)
    return choose_elemental_focus_reroll(
        agent,
        context,
        primary=primary,
        secondary=secondary,
        rule_id="reroll.superconduct_mika_curve",
    )


def choose_action(agent, context: DecisionContext):
    active_name = agent._active_character_name(context)
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)

    if round_number <= 2:
        choice = agent._play_named_card(
            context,
            card_names=("元素幻变：超导祝佑", "元素共鸣：交织之冰", "元素共鸣：粉碎之冰", "交给我吧！", "旧时庭园"),
            rule_id="action.superconduct_mika_build_engine",
            min_tactical_score=1,
        )
        if choice is not None:
            return choice

    if active_name == "雷音权现":
        choice = agent._play_named_card(
            context,
            card_names=("灵光明烁之心", "黄金剧团的奖赏"),
            rule_id="action.superconduct_mika_arm_manifestation",
        )
        if choice is not None:
            return choice
        if aura <= 0:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_E", "GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A"),
                rule_id="action.superconduct_mika_apply_electro",
            )
            if choice is not None:
                return choice
        if aura > 0 and round_number <= 4:
            choice = agent._switch_to_named_targets(
                context,
                target_names=("米卡", "爱可菲"),
                rule_id="action.superconduct_mika_rotate_to_cryo",
                require_safe_target=True,
            )
            if choice is not None:
                return choice
        if round_number >= 4:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A", "GCG_SKILL_TAG_E"),
                rule_id="action.superconduct_mika_manifestation_pressure",
            )
            if choice is not None:
                return choice
        return None

    if active_name == "爱可菲":
        choice = agent._play_named_card(
            context,
            card_names=("浮溯之珏", "黄金剧团的奖赏"),
            rule_id="action.superconduct_mika_arm_escoffier",
        )
        if choice is not None:
            return choice
        if aura <= 0:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_E", "GCG_SKILL_TAG_A", "GCG_SKILL_TAG_Q"),
                rule_id="action.superconduct_mika_apply_cryo",
            )
            if choice is not None:
                return choice
        if aura > 0 and round_number <= 4:
            choice = agent._switch_to_named_targets(
                context,
                target_names=("米卡", "雷音权现"),
                rule_id="action.superconduct_mika_handoff_finish",
                require_safe_target=True,
            )
            if choice is not None:
                return choice
        if round_number >= 5:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A", "GCG_SKILL_TAG_E"),
                rule_id="action.superconduct_mika_cryo_pressure",
            )
            if choice is not None:
                return choice
        return None

    if active_name == "米卡":
        choice = agent._play_named_card(
            context,
            card_names=("依随的策援", "贯虹之槊", "和璞鸢", "贯月矢", "角斗士的凯旋", "赌徒的耳环"),
            rule_id="action.superconduct_mika_arm_mika",
        )
        if choice is not None:
            return choice
        if aura > 0:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A", "GCG_SKILL_TAG_E"),
                rule_id="action.superconduct_mika_finish_window",
            )
            if choice is not None:
                return choice
        if aura <= 0 and round_number <= 3:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_E", "GCG_SKILL_TAG_A"),
                rule_id="action.superconduct_mika_setup_cryo",
            )
            if choice is not None:
                return choice
        return agent._switch_to_named_targets(
            context,
            target_names=("雷音权现", "爱可菲"),
            rule_id="action.superconduct_mika_reset_chain",
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
    if name == "依随的策援":
        return 5 if active_name == "米卡" else -3
    if name in {"贯虹之槊", "和璞鸢", "贯月矢", "角斗士的凯旋"}:
        return 4 if active_name == "米卡" else 0
    if name == "赌徒的耳环":
        return 2 if active_name == "米卡" and round_number >= 3 else 0
    if name == "灵光明烁之心":
        return 4 if active_name == "雷音权现" else 0
    if name == "浮溯之珏":
        return 4 if active_name == "爱可菲" else 0
    if name == "黄金剧团的奖赏":
        return 3 if active_name in {"雷音权现", "爱可菲"} else 0
    if name == "元素幻变：超导祝佑":
        return 5 if round_number <= 2 else 2
    if name == "元素共鸣：交织之冰":
        return 3 if active_name in {"米卡", "爱可菲"} and dice_count <= 4 else 0
    if name == "元素共鸣：粉碎之冰":
        return 4 if aura > 0 and active_name in {"米卡", "爱可菲"} else -2
    if name == "交给我吧！":
        return 3 if round_number <= 3 and active_name in {"雷音权现", "爱可菲"} else -1
    if name == "旧时庭园":
        return 4 if round_number <= 2 else 0
    if name == "北地烟熏鸡":
        return 3 if active_name == "米卡" and aura > 0 else -1
    if name == "野猪公主":
        return -2 if round_number <= 3 else 0
    return 0
