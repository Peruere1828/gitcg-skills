# Ported from Rebel_base_RL research/world_model (AGPL-3.0, github.com/.../Rebel_base_RL) by genshin-GITCG.
# Keep upstream semantics; adapt imports only. See NOTICE.md.
from __future__ import annotations

from reps.schema import DecisionContext

from .common import choose_elemental_focus_reroll, make_rule_config

RULE_CONFIG = make_rule_config(
    slug="electrocharged_moon",
    opener="伊涅芙",
    carry=("伊涅芙", "水形幻人", "多莉"),
    bench=("水形幻人", "多莉"),
    preferred_elements=("GCG_TAG_ELEMENT_HYDRO", "GCG_TAG_ELEMENT_ELECTRO"),
    style="electro_charged",
)


def choose_active(agent, context: DecisionContext):
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)
    if aura > 0:
        preferred = ("伊涅芙", "多莉", "水形幻人")
    elif round_number <= 2:
        preferred = ("伊涅芙", "水形幻人", "多莉")
    else:
        preferred = ("水形幻人", "伊涅芙", "多莉")
    return agent._choose_active_by_names(context, preferred, require_safe_target=True)


def choose_reroll(agent, context: DecisionContext):
    active_name = agent._active_character_name(context)
    if active_name in {"伊涅芙", "多莉"}:
        primary = ("GCG_TAG_ELEMENT_ELECTRO",)
        secondary = ("GCG_TAG_ELEMENT_HYDRO",)
    else:
        primary = ("GCG_TAG_ELEMENT_HYDRO",)
        secondary = ("GCG_TAG_ELEMENT_ELECTRO",)
    return choose_elemental_focus_reroll(
        agent,
        context,
        primary=primary,
        secondary=secondary,
        rule_id="reroll.electrocharged_moon_curve",
    )


def choose_action(agent, context: DecisionContext):
    active_name = agent._active_character_name(context)
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)
    hand_names = set(agent._hand_card_names(context))

    if round_number <= 2:
        choice = agent._play_named_card(
            context,
            card_names=(
                "循环整流引擎",
                "骑士团图书馆",
                "群玉阁",
                "汐印石",
                "常九爷",
                "寻宝仙灵",
                "最好的伙伴！",
                "交给我吧！",
                "元素共鸣：交织之雷",
                "元素共鸣：强能之雷",
            ),
            rule_id="action.electrocharged_moon_build_engine",
            min_tactical_score=1,
        )
        if choice is not None:
            return choice

    if active_name == "伊涅芙":
        choice = agent._play_named_card(
            context,
            card_names=("循环整流引擎", "灵光明烁之心"),
            rule_id="action.electrocharged_moon_arm_ineffa",
        )
        if choice is not None:
            return choice
        if aura <= 0:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_E", "GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A"),
                rule_id="action.electrocharged_moon_setup_electro",
            )
            if choice is not None:
                return choice
        if aura > 0 and round_number >= 3:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A", "GCG_SKILL_TAG_E"),
                rule_id="action.electrocharged_moon_finish_window",
            )
            if choice is not None:
                return choice
        return agent._switch_to_named_targets(
            context,
            target_names=("水形幻人", "多莉"),
            rule_id="action.electrocharged_moon_reset_chain",
            require_safe_target=True,
        )

    if active_name == "水形幻人":
        if aura <= 0:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_E", "GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A"),
                rule_id="action.electrocharged_moon_setup_hydro",
            )
            if choice is not None:
                return choice
        if aura > 0 and round_number <= 4:
            choice = agent._switch_to_named_targets(
                context,
                target_names=("伊涅芙", "多莉"),
                rule_id="action.electrocharged_moon_rotate_finish",
                require_safe_target=True,
            )
            if choice is not None:
                return choice
        if round_number >= 5:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A", "GCG_SKILL_TAG_E"),
                rule_id="action.electrocharged_moon_hydro_pressure",
            )
            if choice is not None:
                return choice
        return None

    if active_name == "多莉":
        choice = agent._play_named_card(
            context,
            card_names=("公义的酬报", "少女易逝的芳颜"),
            rule_id="action.electrocharged_moon_arm_dori",
        )
        if choice is not None:
            return choice
        if round_number >= 4 or aura > 0:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_E", "GCG_SKILL_TAG_A"),
                rule_id="action.electrocharged_moon_dori_pressure",
            )
            if choice is not None:
                return choice
        if "「邪龙」的苏醒" in hand_names or "「魔女M的祝福」" in hand_names:
            choice = agent._play_named_card(
                context,
                card_names=("「邪龙」的苏醒", "「魔女M的祝福」"),
                rule_id="action.electrocharged_moon_payoff_event",
            )
            if choice is not None:
                return choice
        return agent._switch_to_named_targets(
            context,
            target_names=("水形幻人", "伊涅芙"),
            rule_id="action.electrocharged_moon_reset_chain",
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
    if name == "循环整流引擎":
        return 5 if active_name == "伊涅芙" else -3
    if name in {"骑士团图书馆", "群玉阁", "汐印石", "常九爷", "寻宝仙灵"}:
        return 4 if round_number <= 3 else 1
    if name == "元素共鸣：交织之雷":
        return 3 if active_name in {"伊涅芙", "多莉"} and dice_count <= 4 else 0
    if name == "元素共鸣：强能之雷":
        return 4 if active_name in {"伊涅芙", "多莉"} else 1
    if name == "灵光明烁之心":
        return 4 if active_name == "伊涅芙" else 0
    if name == "公义的酬报":
        return 4 if active_name == "多莉" else 0
    if name == "少女易逝的芳颜":
        return 3 if active_name == "多莉" else 0
    if name == "裁定之时":
        return 3 if aura > 0 and active_name in {"伊涅芙", "多莉"} else 0
    if name in {"「邪龙」的苏醒", "「魔女M的祝福」"}:
        return 3 if round_number >= 3 else 1
    if name == "最好的伙伴！":
        return 3 if round_number <= 2 and dice_count <= 4 else 0
    if name == "交给我吧！":
        return 3 if round_number <= 3 and active_name in {"伊涅芙", "水形幻人"} else -1
    if name == "奇瑰之汤":
        return -2 if round_number <= 2 else 1
    return 0
