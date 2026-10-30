# Ported from Rebel_base_RL research/world_model (AGPL-3.0, github.com/.../Rebel_base_RL) by genshin-GITCG.
# Keep upstream semantics; adapt imports only. See NOTICE.md.
from __future__ import annotations

from reps.schema import DecisionContext, OptionKind

from .common import choose_elemental_focus_reroll, make_rule_config
from ..models import RuleChoice

RULE_CONFIG = make_rule_config(
    slug="trial_skirk_ayaka_navia",
    opener="爱可菲",
    carry=("丝柯克", "娜维娅", "爱可菲"),
    bench=("娜维娅", "爱可菲"),
    preferred_elements=("GCG_TAG_ELEMENT_CRYO", "GCG_TAG_ELEMENT_GEO"),
    style="trial_pressure",
)


def _declare_end(agent, context: DecisionContext, *, rule_id: str) -> RuleChoice | None:
    for action_code, _ in agent._pairs(context, kind=OptionKind.ACTION_DECLARE_END):
        return RuleChoice(action_code=int(action_code), rule_id=rule_id)
    return None


def _play_fast_switch(agent, context: DecisionContext, *, rule_id: str) -> RuleChoice | None:
    return agent._play_named_card(
        context,
        card_names=("交给我吧！",),
        rule_id=rule_id,
    )


def _play_legend_restock(agent, context: DecisionContext, *, rule_id: str) -> RuleChoice | None:
    return agent._play_named_card(
        context,
        card_names=("万家灶火",),
        rule_id=rule_id,
    )


def choose_active(agent, context: DecisionContext):
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)
    if aura > 0:
        preferred = ("娜维娅", "丝柯克", "爱可菲")
    elif round_number <= 2:
        preferred = ("爱可菲", "丝柯克", "娜维娅")
    else:
        preferred = ("丝柯克", "娜维娅", "爱可菲")
    return agent._choose_active_by_names(context, preferred, require_safe_target=True)


def choose_reroll(agent, context: DecisionContext):
    active_name = agent._active_character_name(context)
    if active_name == "娜维娅":
        return choose_elemental_focus_reroll(
            agent,
            context,
            primary=("GCG_TAG_ELEMENT_GEO",),
            secondary=("GCG_TAG_ELEMENT_CRYO",),
            rule_id="reroll.trial_skirk_navia_curve",
        )
    return choose_elemental_focus_reroll(
        agent,
        context,
        primary=("GCG_TAG_ELEMENT_CRYO",),
        secondary=("GCG_TAG_ELEMENT_GEO",),
        rule_id="reroll.trial_skirk_navia_curve",
    )


def choose_action(agent, context: DecisionContext):
    active_name = agent._active_character_name(context)
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)
    player = agent._player(context)
    hand_names = tuple(agent._hand_card_names(context))
    hand_name_set = set(hand_names)
    hand_size = len(hand_names)
    shrapnel_count = sum(1 for name in hand_names if name == "裂晶弹片")
    remaining_dice = len(player.dice)
    legal_skill_ids = {
        int(spec.subject_definition_id)
        for _, spec in agent._pairs(context, kind=OptionKind.ACTION_USE_SKILL)
    }
    zero_cost_card_count = sum(
        1
        for card_state in player.hand_cards
        if (
            (card := agent.assets.cards.get(int(card_state.definition_id))) is not None
            and agent._total_dice_cost(card.play_cost) == 0
        )
    )
    skirk_subtlety = agent._character_special_energy(
        context,
        character_name="丝柯克",
        fallback_variable_name="serpentsSubtlety",
    )
    injured_exists = any(
        int(character.health) < int(character.max_health)
        for character in player.characters
        if not character.defeated
    )

    if "万家灶火" in hand_name_set and not bool(player.legend_used):
        if (round_number == 1 and ("湮远" not in hand_name_set or "虹彩缤纷的甜点茶话" not in hand_name_set)) or (
            round_number >= 2 and hand_size <= 2
        ):
            choice = _play_legend_restock(agent, context, rule_id="action.trial_legend_restock")
            if choice is not None:
                return choice

    if round_number <= 2:
        choice = agent._play_named_card(
            context,
            card_names=("元素共鸣：交织之冰", "交给我吧！", "风龙废墟", "中央实验室遗址"),
            rule_id="action.trial_build_engine",
            min_tactical_score=1,
        )
        if choice is not None:
            return choice

    if active_name == "爱可菲":
        if injured_exists and round_number >= 4:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q",),
                rule_id="action.trial_heal_reset",
            )
            if choice is not None:
                return choice
        if aura <= 0:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_E", "GCG_SKILL_TAG_A"),
                rule_id="action.trial_setup_cryo",
            )
            if choice is not None:
                return choice
        if aura > 0 and round_number <= 3:
            choice = _play_fast_switch(agent, context, rule_id="action.trial_fast_switch")
            if choice is not None:
                return choice
            return agent._switch_to_named_targets(
                context,
                target_names=("娜维娅",),
                rule_id="action.trial_rotate_escoffier_to_navia",
                require_safe_target=True,
            )
        if round_number == 1:
            choice = _play_fast_switch(agent, context, rule_id="action.trial_fast_switch")
            if choice is not None:
                return choice
            return agent._switch_to_named_targets(
                context,
                target_names=("丝柯克",),
                rule_id="action.trial_rotate_escoffier_to_skirk",
                require_safe_target=True,
            )
        return None

    if active_name == "丝柯克":
        choice = agent._play_named_card(
            context,
            card_names=("湮远",),
            rule_id="action.trial_arm_skirk",
        )
        if choice is not None and (aura > 0 or round_number >= 3):
            return choice
        choice = agent._play_named_card(
            context,
            card_names=("诸武相授",),
            rule_id="action.trial_arm_skirk",
        )
        if choice is not None and (round_number <= 4 or aura > 0):
            return choice
        if 11165 in legal_skill_ids and zero_cost_card_count > 0 and (round_number >= 2 or skirk_subtlety <= 4):
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(11165,),
                rule_id="action.trial_skirk_restock",
            )
            if choice is not None:
                return choice
        if round_number <= 2:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(11162,),
                rule_id="action.trial_skirk_setup_subtlety",
            )
            if choice is not None:
                return choice
        if 11163 in legal_skill_ids and skirk_subtlety <= 2:
            choice = _play_fast_switch(agent, context, rule_id="action.trial_fast_switch")
            if choice is not None:
                return choice
            choice = agent._switch_to_named_targets(
                context,
                target_names=("娜维娅", "爱可菲"),
                rule_id="action.trial_rotate_skirk_to_navia",
                require_safe_target=True,
            )
            if choice is not None:
                return choice
            hold = _declare_end(agent, context, rule_id="action.trial_hold_skirk_window")
            if hold is not None:
                return hold
        if aura > 0 and round_number >= 3:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A"),
                rule_id="action.trial_skirk_finish_window",
            )
            if choice is not None:
                return choice
        if round_number >= 3 and agent._has_ready_burst(context):
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q",),
                rule_id="action.trial_skirk_finish_window",
            )
            if choice is not None:
                return choice
        if aura > 0 and round_number >= 2:
            choice = _play_fast_switch(agent, context, rule_id="action.trial_fast_switch")
            if choice is not None:
                return choice
            return agent._switch_to_named_targets(
                context,
                target_names=("娜维娅",),
                rule_id="action.trial_rotate_skirk_to_navia",
                require_safe_target=True,
            )
        return None

    if active_name == "娜维娅":
        choice = agent._play_named_card(
            context,
            card_names=("裂晶弹片",),
            rule_id="action.trial_navia_shrapnel_pressure",
        )
        if choice is not None and (aura > 0 or shrapnel_count >= 2 or round_number >= 4):
            return choice
        if shrapnel_count > 0 and 16082 in legal_skill_ids:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(16082,),
                rule_id="action.trial_press_navia",
            )
            if choice is not None:
                return choice
        if aura > 0:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_E", "GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A"),
                rule_id="action.trial_press_navia",
            )
            if choice is not None:
                return choice
        if round_number >= 4:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A"),
                rule_id="action.trial_press_navia",
            )
            if choice is not None:
                return choice
        if round_number >= 4 and remaining_dice <= 1:
            hold = _declare_end(agent, context, rule_id="action.trial_hold_navia_window")
            if hold is not None:
                return hold
        return None
    return None


def card_context_bonus(agent, card, context: DecisionContext, mode: str) -> int:
    if card is None:
        return 0
    name = str(card.name)
    round_number = agent._round_number(context)
    active_name = agent._active_character_name(context)
    aura = agent._opponent_active_aura(context)
    hand_names = tuple(agent._hand_card_names(context))
    hand_name_set = set(hand_names)
    hand_size = len(hand_names)
    switch_count = len(agent._pairs(context, kind=OptionKind.ACTION_SWITCH_ACTIVE))
    if name == "元素共鸣：交织之冰":
        return 2 if active_name in {"爱可菲", "丝柯克"} and round_number <= 3 else 0
    if name == "诸武相授":
        if active_name == "丝柯克" and (round_number <= 4 or aura > 0):
            return 4
        return -4
    if name == "万家灶火":
        if round_number == 1 and ("湮远" not in hand_name_set or "虹彩缤纷的甜点茶话" not in hand_name_set):
            return 5
        return 4 if round_number >= 2 and hand_size <= 2 else -2
    if name == "湮远":
        if active_name == "丝柯克" and (aura > 0 or round_number >= 3):
            return 4
        return -5
    if name == "元素共鸣：粉碎之冰":
        return 3 if aura > 0 and active_name in {"爱可菲", "丝柯克"} else -2
    if name == "交给我吧！":
        if switch_count <= 0:
            return -3
        if active_name in {"爱可菲", "丝柯克"} and (aura > 0 or round_number <= 2):
            return 3
        if round_number <= 3 and active_name in {"爱可菲", "娜维娅"}:
            return 2
        return -1 if active_name == "丝柯克" and round_number <= 2 else 0
    if name == "风龙废墟":
        return 3 if round_number <= 2 else 0
    if name == "中央实验室遗址":
        return 2 if round_number <= 3 else 0
    if name == "裂晶弹片":
        if active_name == "娜维娅" and (aura > 0 or round_number >= 3):
            return 4
        return 1 if aura > 0 else -2
    if name == "典仪式晶火":
        return 3 if active_name == "娜维娅" and aura > 0 else -2
    if name == "奇瑰之汤":
        return -1 if round_number <= 2 else 1
    return 0
