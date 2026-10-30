# Ported from Rebel_base_RL research/world_model (AGPL-3.0, github.com/.../Rebel_base_RL) by genshin-GITCG.
# Keep upstream semantics; adapt imports only. See NOTICE.md.
from __future__ import annotations

from reps.schema import DecisionContext, OptionKind

from .common import choose_elemental_focus_reroll, make_rule_config
from ..models import RuleChoice

RULE_CONFIG = make_rule_config(
    slug="nahida_kokomi_raiden",
    opener="纳西妲",
    carry=("雷电将军", "纳西妲", "珊瑚宫心海"),
    bench=("纳西妲", "珊瑚宫心海"),
    preferred_elements=("GCG_TAG_ELEMENT_DENDRO", "GCG_TAG_ELEMENT_HYDRO", "GCG_TAG_ELEMENT_ELECTRO"),
    style="hyperbloom",
)


def _declare_end(agent, context: DecisionContext, *, rule_id: str) -> RuleChoice | None:
    for action_code, _ in agent._pairs(context, kind=OptionKind.ACTION_DECLARE_END):
        return RuleChoice(action_code=int(action_code), rule_id=rule_id)
    return None


def _has_meaningful_free_card(agent, context: DecisionContext) -> bool:
    for _, spec in agent._pairs(context, kind=OptionKind.ACTION_PLAY_CARD):
        if str(spec.label).endswith(":none") and agent._remaining_card_action_value(context, spec) > 0:
            return True
    return False


def choose_active(agent, context: DecisionContext):
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)
    if aura > 0:
        preferred = ("雷电将军", "珊瑚宫心海", "纳西妲")
    elif round_number <= 2:
        preferred = ("纳西妲", "珊瑚宫心海", "雷电将军")
    else:
        preferred = ("珊瑚宫心海", "雷电将军", "纳西妲")
    return agent._choose_active_by_names(context, preferred, require_safe_target=True)


def choose_reroll(agent, context: DecisionContext):
    active_name = agent._active_character_name(context)
    if active_name == "纳西妲":
        primary = ("GCG_TAG_ELEMENT_DENDRO",)
        secondary = ("GCG_TAG_ELEMENT_HYDRO", "GCG_TAG_ELEMENT_ELECTRO")
    elif active_name == "珊瑚宫心海":
        primary = ("GCG_TAG_ELEMENT_HYDRO",)
        secondary = ("GCG_TAG_ELEMENT_DENDRO", "GCG_TAG_ELEMENT_ELECTRO")
    else:
        primary = ("GCG_TAG_ELEMENT_ELECTRO",)
        secondary = ("GCG_TAG_ELEMENT_DENDRO", "GCG_TAG_ELEMENT_HYDRO")
    return choose_elemental_focus_reroll(
        agent,
        context,
        primary=primary,
        secondary=secondary,
        rule_id="reroll.nahida_hyperbloom_curve",
    )


def choose_action(agent, context: DecisionContext):
    active_name = agent._active_character_name(context)
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)
    has_skill_action = agent._has_legal_skill_action(context)
    remaining_dice = len(agent._player(context).dice)
    injured_exists = any(
        int(character.health) < int(character.max_health)
        for character in agent._player(context).characters
        if not character.defeated
    )

    if round_number <= 2:
        choice = agent._play_named_card(
            context,
            card_names=("群玉阁", "派蒙", "凯瑟琳", "立本", "常九爷", "参量质变仪", "红羽团扇", "璃月港口", "晨曦酒庄", "望舒客栈", "西风大教堂"),
            rule_id="action.nahida_hyperbloom_build_engine",
            min_tactical_score=1,
        )
        if choice is not None:
            return choice

    if active_name == "纳西妲":
        choice = agent._play_named_card(
            context,
            card_names=("心识蕴藏之种", "天空之卷", "魔导绪论"),
            rule_id="action.nahida_hyperbloom_arm_nahida",
        )
        if choice is not None:
            return choice
        if aura <= 0:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_E", "GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A"),
                rule_id="action.nahida_hyperbloom_setup_dendro",
            )
            if choice is not None:
                return choice
        if aura > 0 and round_number >= 4 and has_skill_action:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A", "GCG_SKILL_TAG_E"),
                rule_id="action.nahida_hyperbloom_dendro_pressure",
            )
            if choice is not None:
                return choice
        if remaining_dice <= 0 and not has_skill_action and not _has_meaningful_free_card(agent, context):
            return _declare_end(agent, context, rule_id="action.nahida_hyperbloom_hold_window")
        if aura <= 0 and remaining_dice < 2:
            return _declare_end(agent, context, rule_id="action.nahida_hyperbloom_hold_window")
        handoff_targets = ("雷电将军", "珊瑚宫心海")
        if aura <= 0 and round_number <= 3:
            handoff_targets = ("珊瑚宫心海", "雷电将军")
        return agent._switch_to_named_targets(
            context,
            target_names=handoff_targets,
            rule_id="action.nahida_hyperbloom_handoff_reaction",
            require_safe_target=True,
        )

    if active_name == "珊瑚宫心海":
        choice = agent._play_named_card(
            context,
            card_names=("酒渍船帽",),
            rule_id="action.nahida_hyperbloom_arm_kokomi",
        )
        if choice is not None:
            return choice
        if injured_exists and round_number >= 4:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q",),
                rule_id="action.nahida_hyperbloom_heal_reset",
            )
            if choice is not None:
                return choice
        if aura <= 0:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_E", "GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A"),
                rule_id="action.nahida_hyperbloom_setup_hydro",
            )
            if choice is not None:
                return choice
        if aura > 0 and (injured_exists or round_number >= 4) and has_skill_action:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A", "GCG_SKILL_TAG_E"),
                rule_id="action.nahida_hyperbloom_hydro_pressure",
            )
            if choice is not None:
                return choice
        if remaining_dice <= 0 and not has_skill_action and not _has_meaningful_free_card(agent, context):
            return _declare_end(agent, context, rule_id="action.nahida_hyperbloom_hold_window")
        if aura > 0 and round_number <= 4 and remaining_dice >= 2:
            return agent._switch_to_named_targets(
                context,
                target_names=("雷电将军",),
                rule_id="action.nahida_hyperbloom_rotate_to_raiden",
                require_safe_target=True,
            )
        return None

    if active_name == "雷电将军":
        choice = agent._play_named_card(
            context,
            card_names=("万千的愿望", "薙草之稻光", "白缨枪", "唤雷的头冠"),
            rule_id="action.nahida_hyperbloom_arm_raiden",
        )
        if choice is not None:
            return choice
        if aura > 0:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_E", "GCG_SKILL_TAG_A"),
                rule_id="action.nahida_hyperbloom_finish_window",
            )
            if choice is not None:
                return choice
            if round_number >= 3 and has_skill_action:
                choice = agent._use_active_skill_types(
                    context,
                    skill_types=("GCG_SKILL_TAG_E", "GCG_SKILL_TAG_A"),
                    rule_id="action.nahida_hyperbloom_electro_pressure",
                )
                if choice is not None:
                    return choice
            return None
        choice = agent._use_active_skill_types(
            context,
            skill_types=("GCG_SKILL_TAG_E", "GCG_SKILL_TAG_A"),
            rule_id="action.nahida_hyperbloom_setup_electro",
        )
        if choice is not None and (round_number <= 2 or remaining_dice >= 3):
            return choice
        if remaining_dice <= 0 and not has_skill_action and not _has_meaningful_free_card(agent, context):
            return _declare_end(agent, context, rule_id="action.nahida_hyperbloom_hold_window")
        if round_number <= 2 and remaining_dice >= 2:
            return agent._switch_to_named_targets(
                context,
                target_names=("纳西妲", "珊瑚宫心海"),
                rule_id="action.nahida_hyperbloom_reset_chain",
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
    switch_count = len(agent._pairs(context, kind=None))
    injured_exists = any(
        int(character.health) < int(character.max_health)
        for character in agent._player(context).characters
        if not character.defeated
    )
    if name == "心识蕴藏之种":
        return 5 if active_name == "纳西妲" else -3
    if name == "万千的愿望":
        return 5 if active_name == "雷电将军" else -3
    if name in {"群玉阁", "派蒙", "凯瑟琳", "立本", "常九爷", "参量质变仪", "红羽团扇", "璃月港口", "晨曦酒庄", "望舒客栈", "西风大教堂"}:
        return 4 if round_number <= 3 else 1
    if name in {"天空之卷", "魔导绪论"}:
        return 4 if active_name == "纳西妲" else 0
    if name in {"薙草之稻光", "白缨枪", "唤雷的头冠"}:
        return 4 if active_name == "雷电将军" else 0
    if name == "酒渍船帽":
        return 3 if active_name == "珊瑚宫心海" else 0
    if name == "最好的伙伴！":
        return 3 if round_number <= 2 else 0
    if name == "一掷乾坤":
        return 2 if round_number <= 2 else 0
    if name in {"交给我吧！", "鹤归之时"}:
        return 3 if round_number <= 3 and switch_count > 0 else 0
    if name == "快快缝补术":
        return 3 if injured_exists else 0
    if name == "送你一程":
        return 4 if aura > 0 and round_number >= 3 else -1
    return 0
