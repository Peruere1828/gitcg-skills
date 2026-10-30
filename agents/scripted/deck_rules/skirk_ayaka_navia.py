# Ported from Rebel_base_RL research/world_model (AGPL-3.0, github.com/.../Rebel_base_RL) by genshin-GITCG.
# Keep upstream semantics; adapt imports only. See NOTICE.md.
from __future__ import annotations

from reps.schema import DecisionContext, OptionKind

from .common import choose_elemental_focus_reroll, make_rule_config
from ..models import RuleChoice

RULE_CONFIG = make_rule_config(
    slug="skirk_ayaka_navia",
    opener="爱可菲",
    carry=("丝柯克", "娜维娅", "爱可菲"),
    bench=("娜维娅", "爱可菲"),
    preferred_elements=("GCG_TAG_ELEMENT_CRYO", "GCG_TAG_ELEMENT_GEO"),
    style="talent_pressure",
)


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


def _crystal_shrapnel_count(hand_names: tuple[str, ...]) -> int:
    return sum(1 for name in hand_names if name == "裂晶弹片")


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
    hand_names = tuple(agent._hand_card_names(context))
    shrapnel_count = _crystal_shrapnel_count(hand_names)
    skirk_subtlety = agent._character_special_energy(
        context,
        character_name="丝柯克",
        fallback_variable_name="serpentsSubtlety",
    )
    if aura > 0 and skirk_subtlety >= 5:
        preferred = ("丝柯克", "娜维娅", "爱可菲")
    elif aura > 0 and shrapnel_count >= 2:
        preferred = ("娜维娅", "丝柯克", "爱可菲")
    elif round_number <= 2:
        preferred = ("爱可菲", "丝柯克", "娜维娅")
    elif shrapnel_count >= 2:
        preferred = ("娜维娅", "丝柯克", "爱可菲")
    else:
        preferred = ("丝柯克", "爱可菲", "娜维娅")
    return agent._choose_active_by_names(context, preferred, require_safe_target=True)


def choose_reroll(agent, context: DecisionContext):
    active_name = agent._active_character_name(context)
    if active_name == "娜维娅":
        return choose_elemental_focus_reroll(
            agent,
            context,
            primary=("GCG_TAG_ELEMENT_GEO",),
            secondary=("GCG_TAG_ELEMENT_CRYO",),
            rule_id="reroll.skirk_ayaka_navia_curve",
        )
    return choose_elemental_focus_reroll(
        agent,
        context,
        primary=("GCG_TAG_ELEMENT_CRYO",),
        secondary=("GCG_TAG_ELEMENT_GEO",),
        rule_id="reroll.skirk_ayaka_navia_curve",
    )


def choose_action(agent, context: DecisionContext):
    active_name = agent._active_character_name(context)
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)
    hand_names = tuple(agent._hand_card_names(context))
    hand_name_set = set(hand_names)
    hand_size = len(hand_names)
    shrapnel_count = _crystal_shrapnel_count(hand_names)
    remaining_dice = len(agent._player(context).dice)
    legal_skill_ids = _legal_skill_ids(agent, context)
    has_skill_action = agent._has_legal_skill_action(context)
    has_free_card = _has_meaningful_free_card(agent, context)
    zero_cost_card_count = sum(
        1
        for card_state in agent._player(context).hand_cards
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
        for character in agent._player(context).characters
        if not character.defeated
    )

    if "万家灶火" in hand_name_set and not bool(agent._player(context).legend_used):
        if (round_number == 1 and ("湮远" not in hand_name_set or "虹彩缤纷的甜点茶话" not in hand_name_set)) or (
            round_number >= 2 and hand_size <= 2
        ):
            choice = _play_legend_restock(
                agent,
                context,
                rule_id="action.skirk_ayaka_navia_legend_restock",
            )
            if choice is not None:
                return choice

    if round_number <= 2:
        choice = agent._play_named_card(
            context,
            card_names=("元素共鸣：交织之冰",),
            rule_id="action.skirk_ayaka_navia_build_engine",
            min_tactical_score=1,
        )
        if choice is not None:
            return choice

    if active_name == "爱可菲":
        choice = agent._play_named_card(
            context,
            card_names=("虹彩缤纷的甜点茶话",),
            rule_id="action.skirk_ayaka_navia_arm_escoffier",
        )
        if choice is not None and (round_number <= 3 or aura > 0):
            return choice
        if injured_exists and round_number >= 5:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(11153,),
                rule_id="action.skirk_ayaka_navia_heal_reset",
            )
            if choice is not None:
                return choice
        if aura <= 0 or round_number <= 2:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(11152,),
                rule_id="action.skirk_ayaka_navia_setup_cryo",
            )
            if choice is not None:
                return choice
        if round_number <= 2 and aura <= 0:
            hold = _declare_end(agent, context, rule_id="action.skirk_ayaka_navia_hold_escoffier_window")
            if hold is not None:
                return hold
        if aura > 0 and shrapnel_count >= 2 and remaining_dice >= 1:
            choice = _play_fast_switch(
                agent,
                context,
                rule_id="action.skirk_ayaka_navia_fast_switch",
            )
            if choice is not None:
                return choice
            choice = agent._switch_to_named_targets(
                context,
                target_names=("娜维娅",),
                rule_id="action.skirk_ayaka_navia_rotate_to_navia",
                require_safe_target=True,
            )
            if choice is not None:
                return choice
        if remaining_dice <= 0 and not has_skill_action and not has_free_card:
            return _declare_end(agent, context, rule_id="action.skirk_ayaka_navia_hold_escoffier_window")
        choice = _play_fast_switch(
            agent,
            context,
            rule_id="action.skirk_ayaka_navia_fast_switch",
        )
        if choice is not None and round_number >= 3:
            return choice
        return agent._switch_to_named_targets(
            context,
            target_names=("丝柯克", "娜维娅"),
            rule_id="action.skirk_ayaka_navia_rotate_carry",
            require_safe_target=True,
        )

    if active_name == "丝柯克":
        choice = agent._play_named_card(
            context,
            card_names=("湮远",),
            rule_id="action.skirk_ayaka_navia_arm_skirk",
        )
        if choice is not None and (round_number >= 3 or aura > 0):
            return choice
        choice = agent._play_named_card(
            context,
            card_names=("诸武相授",),
            rule_id="action.skirk_ayaka_navia_arm_skirk",
        )
        if choice is not None and (round_number <= 4 or aura > 0):
            return choice
        if 11165 in legal_skill_ids and zero_cost_card_count > 0 and (round_number >= 4 or skirk_subtlety <= 4):
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(11165,),
                rule_id="action.skirk_ayaka_navia_restock_skirk",
            )
            if choice is not None:
                return choice
        if aura <= 0:
            if skirk_subtlety <= 4 and 11162 in legal_skill_ids:
                choice = agent._use_active_skill_definition_ids(
                    context,
                    skill_definition_ids=(11162,),
                    rule_id="action.skirk_ayaka_navia_build_subtlety",
                )
                if choice is not None:
                    return choice
            if round_number <= 3:
                hold = _declare_end(agent, context, rule_id="action.skirk_ayaka_navia_hold_skirk_window")
                if hold is not None:
                    return hold
            if round_number >= 4 and shrapnel_count >= 2 and remaining_dice >= 1:
                choice = _play_fast_switch(
                    agent,
                    context,
                    rule_id="action.skirk_ayaka_navia_fast_switch",
                )
                if choice is not None:
                    return choice
                choice = agent._switch_to_named_targets(
                    context,
                    target_names=("娜维娅",),
                    rule_id="action.skirk_ayaka_navia_rotate_to_navia",
                    require_safe_target=True,
                )
                if choice is not None:
                    return choice
            if remaining_dice <= 0 and not has_skill_action and not has_free_card:
                return _declare_end(agent, context, rule_id="action.skirk_ayaka_navia_hold_skirk_window")
            choice = _play_fast_switch(
                agent,
                context,
                rule_id="action.skirk_ayaka_navia_fast_switch",
            )
            if choice is not None and round_number >= 3:
                return choice
            return agent._switch_to_named_targets(
                context,
                target_names=("爱可菲",),
                rule_id="action.skirk_ayaka_navia_rotate_cryo_setup",
                require_safe_target=True,
            )
        if skirk_subtlety >= 5 and 11163 in legal_skill_ids:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(11163,),
                rule_id="action.skirk_ayaka_navia_press_skirk",
            )
            if choice is not None:
                return choice
        if shrapnel_count >= 2 and remaining_dice >= 1:
            choice = _play_fast_switch(
                agent,
                context,
                rule_id="action.skirk_ayaka_navia_fast_switch",
            )
            if choice is not None:
                return choice
            choice = agent._switch_to_named_targets(
                context,
                target_names=("娜维娅",),
                rule_id="action.skirk_ayaka_navia_rotate_to_navia",
                require_safe_target=True,
            )
            if choice is not None:
                return choice
        if round_number <= 3 and skirk_subtlety < 5 and remaining_dice >= 1:
            choice = _play_fast_switch(
                agent,
                context,
                rule_id="action.skirk_ayaka_navia_fast_switch",
            )
            if choice is not None:
                return choice
            choice = agent._switch_to_named_targets(
                context,
                target_names=("爱可菲",),
                rule_id="action.skirk_ayaka_navia_rotate_cryo_setup",
                require_safe_target=True,
            )
            if choice is not None:
                return choice
        if 11163 in legal_skill_ids and skirk_subtlety <= 2:
            choice = _play_fast_switch(
                agent,
                context,
                rule_id="action.skirk_ayaka_navia_fast_switch",
            )
            if choice is not None:
                return choice
            choice = agent._switch_to_named_targets(
                context,
                target_names=("娜维娅", "爱可菲"),
                rule_id="action.skirk_ayaka_navia_rotate_to_navia",
                require_safe_target=True,
            )
            if choice is not None:
                return choice
            hold = _declare_end(agent, context, rule_id="action.skirk_ayaka_navia_hold_skirk_window")
            if hold is not None:
                return hold
        if remaining_dice <= 0 and not has_skill_action and not has_free_card:
            return _declare_end(agent, context, rule_id="action.skirk_ayaka_navia_hold_skirk_window")
        return None

    if active_name == "娜维娅":
        choice = agent._play_named_card(
            context,
            card_names=("裂晶弹片",),
            rule_id="action.skirk_ayaka_navia_press_navia",
        )
        if choice is not None and (aura > 0 or shrapnel_count >= 2 or round_number >= 4):
            return choice
        choice = agent._play_named_card(
            context,
            card_names=("不明流通渠道",),
            rule_id="action.skirk_ayaka_navia_arm_navia",
        )
        if choice is not None and (aura > 0 or shrapnel_count >= 2 or round_number >= 4):
            return choice
        if aura > 0 and 16083 in legal_skill_ids:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(16083,),
                rule_id="action.skirk_ayaka_navia_press_navia",
            )
            if choice is not None:
                return choice
        if (shrapnel_count > 0 or (aura > 0 and round_number >= 4) or round_number >= 5) and 16082 in legal_skill_ids:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(16082,),
                rule_id="action.skirk_ayaka_navia_press_navia",
            )
            if choice is not None:
                return choice
        if round_number <= 3 or aura <= 0:
            choice = _play_fast_switch(
                agent,
                context,
                rule_id="action.skirk_ayaka_navia_fast_switch",
            )
            if choice is not None:
                return choice
            return agent._switch_to_named_targets(
                context,
                target_names=("爱可菲", "丝柯克"),
                rule_id="action.skirk_ayaka_navia_rotate_carry",
                require_safe_target=True,
            )
        if remaining_dice <= 0 and not has_skill_action and not has_free_card:
            return _declare_end(agent, context, rule_id="action.skirk_ayaka_navia_hold_navia_window")
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
    hand_size = agent._hand_size(context)
    shrapnel_count = _crystal_shrapnel_count(hand_names)
    switch_count = len(agent._pairs(context, kind=OptionKind.ACTION_SWITCH_ACTIVE))
    chef_mao_live = any(
        agent.assets.cards.get(int(getattr(support, "definition_id", 0) or 0)).name == "卯师傅"
        for support in agent._player(context).supports
        if agent.assets.cards.get(int(getattr(support, "definition_id", 0) or 0)) is not None
    )
    has_food_in_hand = any(name in hand_name_set for name in {"咚咚嘭嘭", "噔噔！"})
    if name == "诸武相授":
        if active_name == "丝柯克" and (round_number <= 4 or aura > 0):
            return 5
        return -5
    if name == "万家灶火":
        if round_number == 1 and ("湮远" not in hand_name_set or "虹彩缤纷的甜点茶话" not in hand_name_set):
            return 5
        return 4 if round_number >= 2 and hand_size <= 2 else -2
    if name == "湮远":
        if active_name == "丝柯克" and (aura > 0 or round_number >= 3):
            return 4
        return -5
    if name == "虹彩缤纷的甜点茶话":
        if active_name == "爱可菲" and (round_number <= 3 or aura <= 0):
            return 5
        return -4
    if name == "元素共鸣：交织之冰":
        if active_name == "爱可菲" and round_number <= 2:
            return 3
        return -1 if active_name == "娜维娅" and round_number <= 2 else 0
    if name == "交给我吧！":
        if round_number <= 2:
            return 2 if active_name == "爱可菲" and switch_count > 0 else -4
        if active_name in {"爱可菲", "娜维娅"} and switch_count > 0 and aura > 0:
            return 3
        if active_name == "丝柯克" and switch_count > 0 and round_number >= 3:
            return 3
        return -1
    if name == "不明流通渠道":
        if active_name == "娜维娅" and (aura > 0 or shrapnel_count >= 2 or round_number >= 4):
            return 5
        return -4
    if name == "典仪式晶火":
        if active_name == "娜维娅" and (aura > 0 or shrapnel_count >= 2):
            return 4
        return -3
    if name == "卯师傅":
        if has_food_in_hand or chef_mao_live:
            return 2
        return -3
    if name == "风龙废墟":
        if round_number >= 3 and active_name in {"爱可菲", "丝柯克", "娜维娅"}:
            return 2
        return -3
    if name == "咚咚嘭嘭":
        return 3 if active_name == "爱可菲" or hand_size <= 2 else 1
    if name == "噔噔！":
        if active_name == "爱可菲" and round_number >= 3:
            return 2
        return 1 if hand_size <= 2 else 0
    return 0
