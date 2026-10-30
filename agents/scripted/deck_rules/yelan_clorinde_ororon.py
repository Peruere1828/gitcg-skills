# Ported from Rebel_base_RL research/world_model (AGPL-3.0, github.com/.../Rebel_base_RL) by genshin-GITCG.
# Keep upstream semantics; adapt imports only. See NOTICE.md.
from __future__ import annotations

from reps.schema import DecisionContext

from .common import choose_elemental_focus_reroll, make_rule_config

RULE_CONFIG = make_rule_config(
    slug="yelan_clorinde_ororon",
    opener="夜兰",
    carry=("克洛琳德", "夜兰", "欧洛伦"),
    bench=("夜兰", "欧洛伦"),
    preferred_elements=("GCG_TAG_ELEMENT_HYDRO", "GCG_TAG_ELEMENT_ELECTRO"),
    style="electro_charged",
)


def choose_active(agent, context: DecisionContext):
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)
    summon_count = len(agent._player(context).summons)
    if aura > 0 or summon_count > 0:
        preferred = ("克洛琳德", "欧洛伦", "夜兰")
    elif round_number <= 2:
        preferred = ("夜兰", "欧洛伦", "克洛琳德")
    else:
        preferred = ("欧洛伦", "克洛琳德", "夜兰")
    return agent._choose_active_by_names(context, preferred, require_safe_target=True)


def choose_reroll(agent, context: DecisionContext):
    active_name = agent._active_character_name(context)
    if active_name in {"克洛琳德", "欧洛伦"}:
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
        rule_id="reroll.yelan_clorinde_curve",
    )


def choose_action(agent, context: DecisionContext):
    active_name = agent._active_character_name(context)
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)
    summon_count = len(agent._player(context).summons)
    dice_count = len(agent._player(context).dice)
    opponent_hp = agent._opponent_active_health(context)

    if round_number <= 2 and not (active_name == "克洛琳德" and (aura > 0 or summon_count > 0)):
        choice = agent._play_named_card(
            context,
            card_names=("立本", "常九爷", "寻宝仙灵", "运筹帷幄", "琴音之诗", "元素共鸣：交织之雷", "元素共鸣：强能之雷"),
            rule_id="action.yelan_clorinde_build_engine",
            min_tactical_score=1,
        )
        if choice is not None:
            return choice

    if active_name == "夜兰":
        choice = agent._play_named_card(
            context,
            card_names=("宗室面具", "黄金剧团的奖赏"),
            rule_id="action.yelan_clorinde_arm_yelan",
        )
        if choice is not None:
            return choice
        if aura <= 0:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_E", "GCG_SKILL_TAG_A"),
                rule_id="action.yelan_clorinde_setup_hydro",
            )
            if choice is not None:
                return choice
        if aura > 0 and round_number <= 4 and (round_number >= 2 or summon_count > 0 or opponent_hp <= 6):
            choice = agent._switch_to_named_targets(
                context,
                target_names=("克洛琳德",),
                rule_id="action.yelan_clorinde_handoff_finisher",
                require_safe_target=True,
            )
            if choice is not None:
                return choice
        if summon_count > 0 and round_number >= 3 and dice_count >= 1:
            choice = agent._switch_to_named_targets(
                context,
                target_names=("欧洛伦", "克洛琳德") if round_number <= 3 else ("克洛琳德", "欧洛伦"),
                rule_id="action.yelan_clorinde_handoff_finisher",
                require_safe_target=True,
            )
            if choice is not None:
                return choice
        if round_number >= 5:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A", "GCG_SKILL_TAG_E"),
                rule_id="action.yelan_clorinde_hydro_pressure",
            )
            if choice is not None:
                return choice
        return None

    if active_name == "欧洛伦":
        choice = agent._play_named_card(
            context,
            card_names=("黄金剧团的奖赏",),
            rule_id="action.yelan_clorinde_arm_ororon",
        )
        if choice is not None:
            return choice
        if aura <= 0:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_E", "GCG_SKILL_TAG_A"),
                rule_id="action.yelan_clorinde_setup_electro",
            )
            if choice is not None:
                return choice
        if (aura > 0 or (summon_count > 0 and round_number >= 3) or opponent_hp <= 8) and round_number <= 4:
            choice = agent._switch_to_named_targets(
                context,
                target_names=("克洛琳德",),
                rule_id="action.yelan_clorinde_rotate_to_clorinde",
                require_safe_target=True,
            )
            if choice is not None:
                return choice
        if round_number >= 5:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A", "GCG_SKILL_TAG_E"),
                rule_id="action.yelan_clorinde_ororon_pressure",
            )
            if choice is not None:
                return choice
        return None

    if active_name == "克洛琳德":
        choice = agent._play_named_card(
            context,
            card_names=("浮溯之珏", "角斗士的凯旋", "兽肉薄荷卷", "北地烟熏鸡"),
            rule_id="action.yelan_clorinde_arm_clorinde",
        )
        if choice is not None:
            return choice
        if aura > 0 or summon_count > 0:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_E", "GCG_SKILL_TAG_A"),
                rule_id="action.yelan_clorinde_finish_window",
            )
            if choice is not None:
                return choice
        if aura <= 0 and round_number >= 2:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_E", "GCG_SKILL_TAG_A"),
                rule_id="action.yelan_clorinde_self_setup",
            )
            if choice is not None:
                return choice
        if round_number == 1 and aura <= 0 and summon_count <= 0:
            return agent._switch_to_named_targets(
                context,
                target_names=("夜兰",),
                rule_id="action.yelan_clorinde_reset_reaction_setup",
                require_safe_target=True,
            )
        if round_number == 3 and aura <= 0 and summon_count <= 0 and dice_count >= 1:
            return agent._switch_to_named_targets(
                context,
                target_names=("欧洛伦", "夜兰"),
                rule_id="action.yelan_clorinde_reset_reaction_setup",
                require_safe_target=True,
            )
        return None
    return None


def card_context_bonus(agent, card, context: DecisionContext, mode: str) -> int:
    if card is None:
        return 0
    name = str(card.name)
    round_number = agent._round_number(context)
    active_name = agent._active_character_name(context)
    aura = agent._opponent_active_aura(context)
    dice_count = len(agent._player(context).dice)
    hand_size = agent._hand_size(context)
    if name in {"立本", "常九爷", "寻宝仙灵"}:
        return 4 if round_number <= 2 and active_name != "克洛琳德" else 1 if round_number <= 3 else 0
    if name in {"运筹帷幄", "琴音之诗"}:
        if active_name == "克洛琳德" and (aura > 0 or round_number >= 4):
            return -2
        return 3 if round_number <= 2 and hand_size <= 3 else 1 if round_number <= 3 else 0
    if name == "元素共鸣：交织之雷":
        return 3 if active_name in {"克洛琳德", "欧洛伦"} and dice_count <= 4 else 0
    if name == "元素共鸣：强能之雷":
        return 4 if active_name in {"克洛琳德", "欧洛伦"} else 1
    if name == "浮溯之珏":
        return 4 if active_name == "克洛琳德" else 0
    if name == "黄金剧团的奖赏":
        return 4 if active_name in {"夜兰", "欧洛伦"} and round_number <= 4 else 1
    if name == "宗室面具":
        return 4 if active_name == "夜兰" and round_number <= 4 else 0
    if name in {"角斗士的凯旋", "赌徒的耳环"}:
        return 4 if active_name == "克洛琳德" else 0
    if name == "磐岩盟契":
        return 3 if dice_count <= 2 else -2
    if name == "兽肉薄荷卷":
        return 4 if active_name == "克洛琳德" else 0
    if name == "北地烟熏鸡":
        return 3 if active_name == "克洛琳德" else 0
    if name == "野猪公主":
        return -2 if round_number <= 3 else 0
    return 0
