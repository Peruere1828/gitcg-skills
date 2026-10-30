# Ported from Rebel_base_RL research/world_model (AGPL-3.0, github.com/.../Rebel_base_RL) by genshin-GITCG.
# Keep upstream semantics; adapt imports only. See NOTICE.md.
from __future__ import annotations

from reps.schema import DecisionContext, OptionKind

from .common import make_rule_config
from ..models import RuleChoice

RULE_CONFIG = make_rule_config(
    slug="superconduct_aggro",
    opener="雷电将军",
    carry=("雷电将军", "甘雨", "刻晴"),
    bench=("甘雨", "刻晴"),
    preferred_elements=("GCG_TAG_ELEMENT_ELECTRO", "GCG_TAG_ELEMENT_CRYO"),
    style="superconduct_aggro",
)


def choose_active(agent, context: DecisionContext):
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)
    if aura > 0:
        preferred = ("刻晴", "雷电将军", "甘雨")
    elif round_number <= 3:
        preferred = ("雷电将军", "甘雨", "刻晴")
    else:
        preferred = ("刻晴", "甘雨", "雷电将军")
    return agent._choose_active_by_names(context, preferred, require_safe_target=True)


def _declare_end(agent, context: DecisionContext, *, rule_id: str):
    for action_code, _ in agent._pairs(context, kind=OptionKind.ACTION_DECLARE_END):
        return RuleChoice(action_code=int(action_code), rule_id=rule_id)
    return None


def choose_reroll(agent, context: DecisionContext):
    visible_dice = tuple(int(value) for value in context.request_payload.get("visible_dice", ()))
    if not visible_dice:
        return None
    active_name = agent._active_character_name(context)
    round_number = agent._round_number(context)
    if round_number <= 2:
        primary = {"GCG_TAG_ELEMENT_ELECTRO"}
        secondary = {"GCG_TAG_ELEMENT_CRYO"}
    elif active_name in {"雷电将军", "刻晴"}:
        primary = {"GCG_TAG_ELEMENT_ELECTRO"}
        secondary = {"GCG_TAG_ELEMENT_CRYO"}
    else:
        primary = {"GCG_TAG_ELEMENT_CRYO"}
        secondary = {"GCG_TAG_ELEMENT_ELECTRO"}
    desired = primary | secondary
    best_code = None
    best_score = None
    for action_code, spec in agent._pairs(context):
        rerolled = [
            die
            for index, die in enumerate(visible_dice)
            if spec.reroll_dice_mask & (1 << index)
        ]
        kept = [
            die
            for index, die in enumerate(visible_dice)
            if not (spec.reroll_dice_mask & (1 << index))
        ]
        keep_omni = sum(1 for die in kept if die == 8)
        keep_primary = sum(1 for die in kept if agent._die_to_tag(die) in primary)
        keep_secondary = sum(1 for die in kept if agent._die_to_tag(die) in secondary)
        reroll_omni = sum(1 for die in rerolled if die == 8)
        reroll_primary = sum(1 for die in rerolled if agent._die_to_tag(die) in primary)
        reroll_secondary = sum(1 for die in rerolled if agent._die_to_tag(die) in secondary)
        keep_off = sum(
            1
            for die in kept
            if die != 8 and agent._die_to_tag(die) not in desired
        )
        reroll_off = sum(
            1
            for die in rerolled
            if die != 8 and agent._die_to_tag(die) not in desired
        )
        score = (
            -reroll_omni,
            -reroll_primary,
            keep_omni + 2 * keep_primary,
            keep_primary,
            keep_secondary,
            -reroll_secondary,
            reroll_off,
            -keep_off,
            -len(rerolled),
        )
        if best_score is None or score > best_score:
            best_code = int(action_code)
            best_score = score
    if best_code is None:
        return None
    return RuleChoice(action_code=best_code, rule_id="reroll.superconduct_curve")


def choose_select_card(agent, context: DecisionContext):
    candidates: dict[str, int] = {}
    for action_code, spec in agent._pairs(context):
        card = agent.assets.cards.get(int(spec.subject_definition_id))
        if card is not None:
            candidates[str(card.name)] = int(action_code)
    if not candidates or set(candidates) - {"超导祝佑·极寒", "超导祝佑·电冲"}:
        return None
    for name in ("超导祝佑·电冲", "超导祝佑·极寒"):
        action_code = candidates.get(name)
        if action_code is not None:
            return RuleChoice(action_code=action_code, rule_id="select_card.superconduct_blessing")
    return None


def choose_action(agent, context: DecisionContext):
    active_name = agent._active_character_name(context)
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)
    summon_count = len(agent._player(context).summons)

    if round_number <= 2:
        choice = agent._play_named_card(
            context,
            card_names=("元素幻变：超导祝佑", "元素共鸣：交织之雷", "元素共鸣：强能之雷", "桓那兰那", "凯瑟琳"),
            rule_id="action.superconduct_build_engine",
            min_tactical_score=1,
        )
        if choice is not None:
            return choice

    if active_name == "雷电将军":
        if agent._has_ready_burst(context):
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q",),
                rule_id="action.superconduct_raiden_burst",
            )
            if choice is not None:
                return choice
        if round_number <= 3 and summon_count <= 0:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(14072,),
                rule_id="action.superconduct_setup_eye",
            )
            if choice is not None:
                return choice
        choice = agent._use_active_skill_types(
            context,
            skill_types=("GCG_SKILL_TAG_A",),
            rule_id="action.superconduct_raiden_pressure",
        )
        if choice is not None and round_number >= 5:
            return choice
        target_names = ("甘雨", "刻晴") if aura <= 0 else ("刻晴", "甘雨")
        return agent._switch_to_named_targets(
            context,
            target_names=target_names,
            rule_id="action.superconduct_rotate_window_setup",
            require_safe_target=True,
        )

    if active_name == "甘雨":
        if aura <= 0:
            if agent._has_ready_burst(context) and round_number >= 3:
                choice = agent._use_active_skill_types(
                    context,
                    skill_types=("GCG_SKILL_TAG_Q",),
                    rule_id="action.superconduct_ganyu_burst",
                )
                if choice is not None:
                    return choice
            skill_ids = (11013, 11012, 11011) if round_number <= 3 else (11013, 11012, 11014, 11011)
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=skill_ids,
                rule_id="action.superconduct_apply_cryo",
            )
            if choice is not None:
                return choice
        if round_number >= 5:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(11013, 11011),
                rule_id="action.superconduct_ganyu_pressure",
            )
            if choice is not None:
                return choice
            return None
        return agent._switch_to_named_targets(
            context,
            target_names=("刻晴", "雷电将军"),
            rule_id="action.superconduct_rotate_electro_setup",
            require_safe_target=True,
        )

    if active_name == "刻晴":
        if aura > 0:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(14033, 14032),
                rule_id="action.superconduct_keqing_finish_core",
            )
            if choice is not None:
                return choice
        if round_number >= 5:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(14033, 14032, 14031),
                rule_id="action.superconduct_keqing_pressure",
            )
            if choice is not None:
                return choice
            return None
        if aura <= 0:
            choice = agent._switch_to_named_targets(
                context,
                target_names=("甘雨", "雷电将军"),
                rule_id="action.superconduct_rotate_cryo_setup",
                require_safe_target=True,
            )
            if choice is not None:
                return choice
        if aura > 0:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(14031,),
                rule_id="action.superconduct_keqing_finish_fallback",
            )
            if choice is not None and round_number >= 4:
                return choice
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
    if name == "元素幻变：超导祝佑":
        return 5 if round_number <= 2 else 1
    if name == "元素共鸣：交织之雷":
        if active_name in {"雷电将军", "刻晴"} and dice_count <= 4:
            return 3
        return 1 if round_number <= 2 else -1
    if name == "元素共鸣：强能之雷":
        return 4 if active_name in {"雷电将军", "刻晴"} else 1
    if name == "交给我吧！":
        if active_name == "甘雨" and aura > 0 and round_number <= 4:
            return 3
        if active_name == "雷电将军" and aura <= 0 and round_number <= 4:
            return 2
        return -1 if round_number >= 5 else 0
    if name == "仙跳墙":
        return 4 if agent._has_ready_burst(context) else -2
    if name == "白垩之术":
        return 3 if active_name in {"雷电将军", "刻晴"} else 0
    if name == "桓那兰那":
        return 3 if round_number <= 3 else 0
    if name == "凯瑟琳":
        return 3 if round_number <= 3 else 0
    if name == "雷楔":
        return 4 if aura > 0 or round_number <= 4 else 1
    if name == "昔日宗室之仪":
        return 3 if round_number <= 3 else 0
    if name == "奇瑰之汤":
        return -2 if round_number <= 2 else 0
    return 0
