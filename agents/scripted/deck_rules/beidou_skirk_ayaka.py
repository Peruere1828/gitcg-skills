# Ported from Rebel_base_RL research/world_model (AGPL-3.0, github.com/.../Rebel_base_RL) by genshin-GITCG.
# Keep upstream semantics; adapt imports only. See NOTICE.md.
from __future__ import annotations

from reps.schema import DecisionContext, OptionKind

from .common import make_rule_config
from ..models import RuleChoice

RULE_CONFIG = make_rule_config(
    slug="beidou_skirk_ayaka",
    opener="北斗",
    carry=("丝柯克", "神里绫华", "北斗"),
    bench=("神里绫华", "北斗"),
    preferred_elements=("GCG_TAG_ELEMENT_CRYO", "GCG_TAG_ELEMENT_ELECTRO"),
    style="superconduct_otk",
)


def choose_active(agent, context: DecisionContext):
    aura = agent._opponent_active_aura(context)
    round_number = agent._round_number(context)
    if aura > 0:
        preferred = ("北斗", "神里绫华", "丝柯克")
    elif round_number <= 2:
        preferred = ("北斗", "神里绫华", "丝柯克")
    elif round_number == 3:
        # 这套牌真正稳定挂冰的是神里绫华，不是丝柯克的元素战技。
        preferred = ("神里绫华", "北斗", "丝柯克")
    else:
        preferred = ("神里绫华", "丝柯克", "北斗")
    return agent._choose_active_by_names(context, preferred, require_safe_target=True)


def choose_reroll(agent, context: DecisionContext):
    visible_dice = tuple(int(value) for value in context.request_payload.get("visible_dice", ()))
    if not visible_dice:
        return None
    active_name = agent._active_character_name(context)
    if active_name == "北斗":
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
            keep_omni + keep_primary,
            keep_primary,
            keep_secondary,
            reroll_off,
            -keep_off,
            -len(rerolled),
        )
        if best_score is None or score > best_score:
            best_code = int(action_code)
            best_score = score
    if best_code is None:
        return None
    return RuleChoice(action_code=best_code, rule_id="reroll.beidou_skirk_curve")


def choose_select_card(agent, context: DecisionContext):
    candidates: dict[str, int] = {}
    for action_code, spec in agent._pairs(context):
        card = agent.assets.cards.get(int(spec.subject_definition_id))
        if card is not None:
            candidates[str(card.name)] = int(action_code)
    if not candidates or set(candidates) - {"超导祝佑·极寒", "超导祝佑·电冲"}:
        return None
    priorities = ("超导祝佑·电冲", "超导祝佑·极寒")
    for name in priorities:
        action_code = candidates.get(name)
        if action_code is not None:
            return RuleChoice(action_code=action_code, rule_id="select_card.superconduct_blessing")
    return None


def _declare_end(agent, context: DecisionContext, *, rule_id: str) -> RuleChoice | None:
    for action_code, _ in agent._pairs(context, kind=OptionKind.ACTION_DECLARE_END):
        return RuleChoice(action_code=int(action_code), rule_id=rule_id)
    return None


def _legal_skill_ids(agent, context: DecisionContext) -> set[int]:
    return {
        int(spec.subject_definition_id)
        for _, spec in agent._pairs(context, kind=OptionKind.ACTION_USE_SKILL)
    }


def _has_meaningful_free_card(agent, context: DecisionContext) -> bool:
    return any(
        str(spec.label).endswith(":none") and agent._remaining_card_action_value(context, spec) > 0
        for _, spec in agent._pairs(context, kind=OptionKind.ACTION_PLAY_CARD)
    )


def choose_action(agent, context: DecisionContext):
    active_name = agent._active_character_name(context)
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)
    remaining_dice = len(agent._player(context).dice)
    opponent_hp = agent._opponent_active_health(context)
    has_skill_action = agent._has_legal_skill_action(context)
    legal_skill_ids = _legal_skill_ids(agent, context)
    skirk_subtlety = agent._character_special_energy(context, character_name="丝柯克", fallback_variable_name="serpentsSubtlety")

    if round_number <= 2:
        choice = agent._play_named_card(
            context,
            card_names=("元素幻变：超导祝佑", "元素共鸣：交织之冰", "温妮莎传奇", "寻宝仙灵", "常九爷"),
            rule_id="action.beidou_skirk_build_engine",
            min_tactical_score=1,
        )
        if choice is not None:
            return choice

    if active_name == "神里绫华":
        if aura <= 0:
            choice = agent._play_named_card(
                context,
                card_names=("元素共鸣：交织之冰", "交给我吧！"),
                rule_id="action.beidou_skirk_build_engine",
                min_tactical_score=1,
            )
            if choice is not None:
                return choice
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(11052, 11051),
                rule_id="action.beidou_skirk_setup_cryo",
            )
            if choice is not None:
                return choice
        if aura > 0:
            choice = agent._switch_to_named_targets(
                context,
                target_names=("北斗",) if skirk_subtlety < 4 or round_number <= 3 else ("丝柯克", "北斗"),
                rule_id="action.beidou_skirk_rotate_beidou",
                require_safe_target=True,
            )
            if choice is not None:
                return choice
        if aura > 0 and round_number >= 4:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_E", "GCG_SKILL_TAG_A"),
                rule_id="action.beidou_skirk_ayaka_pressure",
            )
            if choice is not None:
                return choice
        if remaining_dice <= 0 and not has_skill_action and not _has_meaningful_free_card(agent, context):
            return _declare_end(agent, context, rule_id="action.beidou_skirk_hold_ayaka_window")
        return None

    if active_name == "北斗":
        if aura > 0:
            choice = agent._play_named_card(
                context,
                card_names=("霹雳连霄",),
                rule_id="action.beidou_skirk_play_beidou_talent",
            )
            if choice is not None:
                return choice
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q",),
                rule_id="action.beidou_skirk_finish_electro",
            )
            if choice is not None:
                return choice
            if round_number <= 4:
                choice = agent._use_active_skill_definition_ids(
                    context,
                    skill_definition_ids=(14052,),
                    rule_id="action.beidou_skirk_prepare_tide",
                )
                if choice is not None:
                    return choice
            if skirk_subtlety >= 5 and round_number >= 4 and remaining_dice >= 1:
                choice = agent._switch_to_named_targets(
                    context,
                    target_names=("丝柯克",),
                    rule_id="action.beidou_skirk_handoff_skirk",
                    require_safe_target=True,
                )
                if choice is not None:
                    return choice
            hold = _declare_end(agent, context, rule_id="action.beidou_skirk_hold_electro_window")
            if hold is not None:
                return hold
            return None
        if round_number >= 4:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_E", "GCG_SKILL_TAG_A"),
                rule_id="action.beidou_skirk_reapply_electro",
            )
            if choice is not None:
                return choice
        if round_number <= 3 and remaining_dice >= 1:
            return agent._switch_to_named_targets(
                context,
                target_names=("神里绫华",),
                rule_id="action.beidou_skirk_rotate_cryo",
                require_safe_target=True,
            )
        if remaining_dice <= 0 and not has_skill_action and not _has_meaningful_free_card(agent, context):
            return _declare_end(agent, context, rule_id="action.beidou_skirk_hold_setup_window")
        hold = _declare_end(agent, context, rule_id="action.beidou_skirk_hold_setup_window")
        if hold is not None:
            return hold
        return None

    if active_name == "丝柯克":
        choice = agent._play_named_card(
            context,
            card_names=("湮远", "诸武相授"),
            rule_id="action.beidou_skirk_play_skirk_talent",
        )
        if choice is not None and (round_number <= 4 or aura > 0):
            return choice
        if aura <= 0:
            if skirk_subtlety <= 4 and 11162 in legal_skill_ids:
                choice = agent._use_active_skill_definition_ids(
                    context,
                    skill_definition_ids=(11162,),
                    rule_id="action.beidou_skirk_charge_subtlety",
                )
                if choice is not None:
                    return choice
            if remaining_dice <= 0 and not has_skill_action and not _has_meaningful_free_card(agent, context):
                return _declare_end(agent, context, rule_id="action.beidou_skirk_hold_skirk_window")
        if aura > 0 and skirk_subtlety >= 5 and 11163 in legal_skill_ids:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(11163,),
                rule_id="action.beidou_skirk_finish_ruin",
            )
            if choice is not None:
                return choice
        if aura > 0 and opponent_hp <= 2 and 11161 in legal_skill_ids:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(11161,),
                rule_id="action.beidou_skirk_close_skirk",
            )
            if choice is not None:
                return choice
        if aura > 0 and skirk_subtlety < 4 and round_number <= 3:
            choice = agent._switch_to_named_targets(
                context,
                target_names=("北斗",),
                rule_id="action.beidou_skirk_rotate_beidou",
                require_safe_target=True,
            )
            if choice is not None:
                return choice
        if aura <= 0:
            choice = agent._switch_to_named_targets(
                context,
                target_names=("神里绫华",),
                rule_id="action.beidou_skirk_rotate_cryo",
                require_safe_target=True,
            )
            if choice is not None:
                return choice
        hold = _declare_end(agent, context, rule_id="action.beidou_skirk_hold_skirk_window")
        if hold is not None:
            return hold
        return agent._switch_to_named_targets(
            context,
            target_names=("神里绫华",),
            rule_id="action.beidou_skirk_rotate_beidou",
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
    hand_size = agent._hand_size(context)
    if name == "元素幻变：超导祝佑":
        return 5 if round_number <= 2 else 1
    if name == "霹雳连霄":
        return 4 if active_name == "北斗" and (aura > 0 or round_number >= 3) else -2
    if name == "元素共鸣：交织之冰":
        if active_name in {"神里绫华", "丝柯克"} and round_number <= 3:
            return 3
        return -2 if active_name == "北斗" and round_number <= 2 else 0
    if name == "交给我吧！":
        if active_name == "神里绫华" and aura <= 0:
            return 3
        if active_name == "北斗" and aura > 0:
            return 2
        return -1
    if name == "温妮莎传奇":
        return 4 if round_number <= 2 and dice_count <= 4 else -1
    if name == "寻宝仙灵":
        return 3 if round_number <= 3 else 0
    if name == "常九爷":
        return 2 if round_number <= 3 else 0
    if name == "磐岩盟契":
        return 4 if dice_count == 0 else -4
    if name == "拳力斗技！":
        return 3 if dice_count >= 8 and round_number <= 2 else -4
    if name == "浮溯之珏":
        return 2 if active_name in {"神里绫华", "北斗"} else 0
    if name == "角斗士的凯旋":
        return 2 if active_name in {"神里绫华", "北斗"} and hand_size <= 2 else -1
    if name == "宗室面具":
        return 2 if active_name == "北斗" and agent._has_ready_burst(context) else -1
    if name == "黄金剧团的奖赏":
        return 2 if active_name == "北斗" and round_number >= 3 else 0
    if name == "诸武相授":
        if active_name == "丝柯克" and (round_number <= 4 or aura > 0):
            return 4
        return -4
    return 0
