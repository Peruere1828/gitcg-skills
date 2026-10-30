# Ported from Rebel_base_RL research/world_model (AGPL-3.0, github.com/.../Rebel_base_RL) by genshin-GITCG.
# Keep upstream semantics; adapt imports only. See NOTICE.md.
from __future__ import annotations

from reps.schema import DecisionContext, OptionKind

from .common import choose_elemental_focus_reroll, make_rule_config
from ..models import RuleChoice

RULE_CONFIG = make_rule_config(
    slug="ice_water_battleship",
    opener="芙宁娜",
    carry=("恰斯卡", "丝柯克", "芙宁娜"),
    bench=("芙宁娜", "恰斯卡"),
    preferred_elements=("GCG_TAG_ELEMENT_CRYO", "GCG_TAG_ELEMENT_HYDRO", "GCG_TAG_ELEMENT_ANEMO"),
    style="freeze_swirl",
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


def _active_has_entity(agent, context: DecisionContext, *, definition_ids: tuple[int, ...]) -> bool:
    active = agent._active_character(context)
    if active is None:
        return False
    attached = {int(entity.definition_id) for entity in active.entities}
    return any(int(definition_id) in attached for definition_id in definition_ids)


def _count_hand_cards_with_total_cost(agent, context: DecisionContext, *, total_cost: int) -> int:
    count = 0
    for card_state in agent._player(context).hand_cards:
        card = agent.assets.cards.get(int(card_state.definition_id))
        if card is None:
            continue
        if agent._total_dice_cost(card.play_cost) == int(total_cost):
            count += 1
    return count


def _count_ready_dice_for_element(agent, context: DecisionContext, *, element_tag: str) -> int:
    return sum(
        1
        for die in agent._player(context).dice
        if int(die) == 8 or agent._die_to_tag(die) == element_tag
    )


def _character_definition_id(agent, context: DecisionContext, *, character_name: str) -> int:
    character = agent._character_by_name(context, character_name)
    return int(character.definition_id) if character is not None else 0


def _character_has_entity(
    agent,
    context: DecisionContext,
    *,
    character_name: str,
    definition_ids: tuple[int, ...],
) -> bool:
    character = agent._character_by_name(context, character_name)
    if character is None or getattr(character, "defeated", False):
        return False
    attached = {int(entity.definition_id) for entity in character.entities}
    return any(int(definition_id) in attached for definition_id in definition_ids)


def _character_is_alive(agent, context: DecisionContext, *, character_name: str) -> bool:
    character = agent._character_by_name(context, character_name)
    if character is None or getattr(character, "defeated", False):
        return False
    return int(character.health) > 0


def _summon_definition_ids(agent, context: DecisionContext) -> tuple[int, ...]:
    return tuple(int(summon.definition_id) for summon in agent._player(context).summons)


def _team_health_snapshot(agent, context: DecisionContext) -> tuple[int, int, int, int]:
    total_missing_hp = 0
    wounded_count = 0
    low_hp_count = 0
    critical_hp_count = 0
    for character in agent._player(context).characters:
        if getattr(character, "defeated", False):
            continue
        health = int(character.health)
        max_health = int(character.max_health)
        missing_hp = max(0, max_health - health)
        total_missing_hp += missing_hp
        if missing_hp > 0:
            wounded_count += 1
        if health <= 5:
            low_hp_count += 1
        if health <= 3:
            critical_hp_count += 1
    return total_missing_hp, wounded_count, low_hp_count, critical_hp_count


def _play_named_card_on_targets(
    agent,
    context: DecisionContext,
    *,
    card_names: tuple[str, ...],
    target_names: tuple[str, ...],
    rule_id: str,
    min_tactical_score: int | None = None,
) -> RuleChoice | None:
    if not card_names or not target_names:
        return None
    rank_by_name = {name: len(card_names) - index for index, name in enumerate(card_names)}
    target_rank = {name: len(target_names) - index for index, name in enumerate(target_names)}
    best: tuple[int, tuple[int, ...]] | None = None
    for action_code, spec in agent._pairs(context, kind=OptionKind.ACTION_PLAY_CARD):
        card = agent.assets.cards.get(int(spec.subject_definition_id))
        if card is None or str(card.name) not in rank_by_name:
            continue
        target_name = agent._switch_target_name(context, spec)
        if target_name not in target_rank:
            continue
        tactical_score = agent._description_tactical_score(card, context=context, mode="named")
        if min_tactical_score is not None and tactical_score < int(min_tactical_score):
            continue
        total_cost = agent._total_dice_cost(card.play_cost)
        score = (
            rank_by_name[str(card.name)],
            target_rank[target_name],
            tactical_score + agent._card_context_bonus(card, context=context, mode="named"),
            *agent._spec_resource_score(context, spec, card=card),
            -total_cost,
            -int(spec.subject_definition_id),
            -int(action_code),
        )
        if best is None or score > best[1]:
            best = (int(action_code), score)
    if best is None:
        return None
    return RuleChoice(action_code=best[0], rule_id=rule_id)


def choose_active(agent, context: DecisionContext):
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)
    summon_definition_ids = set(_summon_definition_ids(agent, context))
    summon_count = len(summon_definition_ids)
    hand_names = tuple(agent._hand_card_names(context))
    hand_name_set = set(hand_names)
    bullet_count = sum(1 for name in hand_names if name == "追影弹" or name.startswith("焕光追影弹"))
    chasca_nightsoul = agent._character_nightsoul(context, character_name="恰斯卡")
    skirk_subtlety = agent._character_special_energy(
        context,
        character_name="丝柯克",
        fallback_variable_name="serpentsSubtlety",
    )
    furina_definition_id = _character_definition_id(agent, context, character_name="芙宁娜")
    furina_heal_mode = furina_definition_id == 1212
    has_pressure_summon = 112111 in summon_definition_ids
    has_heal_summon = 112112 in summon_definition_ids
    total_missing_hp, _, low_hp_count, _ = _team_health_snapshot(agent, context)
    chasca_opening_tools = {
        "子弹的戏法",
        "绒翼龙",
        "飞行队出击！",
        "中央实验室遗址",
        "很棒，哥们。",
        "指挥的礼帽",
        "幻戏倒计时：3",
    }
    if aura > 0 and chasca_nightsoul > 0:
        preferred = ("恰斯卡", "丝柯克", "芙宁娜")
    elif aura > 0 and skirk_subtlety >= 5 and bullet_count <= 0:
        preferred = ("丝柯克", "恰斯卡", "芙宁娜")
    elif round_number <= 2 and summon_count <= 0:
        preferred = ("芙宁娜", "恰斯卡", "丝柯克")
    elif furina_heal_mode and has_heal_summon and low_hp_count >= 1:
        preferred = ("芙宁娜", "恰斯卡", "丝柯克")
    elif furina_heal_mode and total_missing_hp <= 2 and aura <= 0 and not has_pressure_summon:
        preferred = ("芙宁娜", "恰斯卡", "丝柯克")
    elif round_number <= 2 and has_pressure_summon and (bullet_count > 0 or hand_name_set & chasca_opening_tools):
        preferred = ("恰斯卡", "芙宁娜", "丝柯克")
    elif round_number >= 3 and aura <= 0 and summon_count <= 0 and chasca_nightsoul <= 0 and bullet_count <= 0:
        preferred = ("芙宁娜", "恰斯卡", "丝柯克")
    else:
        preferred = ("恰斯卡", "丝柯克", "芙宁娜")
    return agent._choose_active_by_names(context, preferred, require_safe_target=True)


def choose_reroll(agent, context: DecisionContext):
    active_name = agent._active_character_name(context)
    if active_name == "芙宁娜":
        return choose_elemental_focus_reroll(
            agent,
            context,
            primary=("GCG_TAG_ELEMENT_HYDRO",),
            secondary=("GCG_TAG_ELEMENT_ANEMO",),
            rule_id="reroll.ice_water_curve",
        )
    if active_name == "丝柯克":
        return choose_elemental_focus_reroll(
            agent,
            context,
            primary=("GCG_TAG_ELEMENT_CRYO",),
            secondary=("GCG_TAG_ELEMENT_HYDRO",),
            rule_id="reroll.ice_water_curve",
        )
    return choose_elemental_focus_reroll(
        agent,
        context,
        primary=("GCG_TAG_ELEMENT_ANEMO",),
        secondary=("GCG_TAG_ELEMENT_HYDRO",),
        rule_id="reroll.ice_water_curve",
    )


def choose_action(agent, context: DecisionContext):
    active_name = agent._active_character_name(context)
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)
    summon_definition_ids = set(_summon_definition_ids(agent, context))
    summon_count = len(summon_definition_ids)
    hand_names = tuple(agent._hand_card_names(context))
    hand_name_set = set(hand_names)
    hand_size = agent._hand_size(context)
    bullet_names = tuple(name for name in hand_names if name == "追影弹" or name.startswith("焕光追影弹"))
    bullet_count = len(bullet_names)
    remaining_dice = len(agent._player(context).dice)
    opponent_hp = agent._opponent_active_health(context)
    chasca_nightsoul = agent._character_nightsoul(context, character_name="恰斯卡")
    chasca_has_vehicle = _character_has_entity(agent, context, character_name="恰斯卡", definition_ids=(313006,))
    chasca_has_staff = _character_has_entity(agent, context, character_name="恰斯卡", definition_ids=(115112,))
    skirk_subtlety = agent._character_special_energy(
        context,
        character_name="丝柯克",
        fallback_variable_name="serpentsSubtlety",
    )
    furina_alive = _character_is_alive(agent, context, character_name="芙宁娜")
    skirk_alive = _character_is_alive(agent, context, character_name="丝柯克")
    legal_skill_ids = _legal_skill_ids(agent, context)
    has_skill_action = agent._has_legal_skill_action(context)
    has_free_card = _has_meaningful_free_card(agent, context)
    skirk_flash_ready = _active_has_entity(agent, context, definition_ids=(111162,))
    has_vehicle_in_hand = "绒翼龙" in hand_name_set
    zero_cost_card_count = _count_hand_cards_with_total_cost(agent, context, total_cost=0)
    void_rift_count = sum(1 for name in hand_names if name == "虚境裂隙")
    anemo_ready_dice = _count_ready_dice_for_element(
        agent,
        context,
        element_tag="GCG_TAG_ELEMENT_ANEMO",
    )
    cryo_ready_dice = _count_ready_dice_for_element(
        agent,
        context,
        element_tag="GCG_TAG_ELEMENT_CRYO",
    )
    furina_definition_id = _character_definition_id(agent, context, character_name="芙宁娜")
    furina_pressure_mode = furina_definition_id == 1211
    furina_heal_mode = furina_definition_id == 1212
    has_pressure_summon = 112111 in summon_definition_ids
    has_heal_summon = 112112 in summon_definition_ids
    total_missing_hp, _, low_hp_count, critical_hp_count = _team_health_snapshot(agent, context)
    needs_team_heal = critical_hp_count > 0 or low_hp_count >= 2 or total_missing_hp >= 7
    urgent_team_heal = critical_hp_count > 0 or total_missing_hp >= 8 or (low_hp_count >= 2 and round_number >= 4)
    team_stable = low_hp_count <= 0 and total_missing_hp <= 3
    immediate_finish_window = (
        (active_name == "恰斯卡" and chasca_nightsoul > 0 and aura > 0 and (bullet_count > 0 or hand_size >= 5))
        or (active_name == "丝柯克" and aura > 0 and (skirk_subtlety >= 5 or skirk_flash_ready or 11161 in legal_skill_ids))
    )
    chasca_engine_ready = bullet_count > 0 or any(
        name in hand_name_set
        for name in ("子弹的戏法", "绒翼龙", "飞行队出击！")
    ) or chasca_nightsoul > 0 or chasca_has_vehicle or chasca_has_staff

    if "圣俗杂座" in hand_name_set:
        if (
            furina_pressure_mode
            and (has_pressure_summon or summon_count <= 0)
            and round_number >= 4
            and urgent_team_heal
            and not immediate_finish_window
        ):
            choice = agent._play_named_card(
                context,
                card_names=("圣俗杂座",),
                rule_id="action.ice_water_shift_furina_to_heal",
            )
            if choice is not None:
                return choice
        if (
            furina_heal_mode
            and not urgent_team_heal
            and (aura <= 0 or not has_pressure_summon)
            and (
                round_number >= 2
                or bullet_count > 0
                or chasca_nightsoul > 0
                or skirk_subtlety >= 4
                or has_heal_summon
            )
        ):
            choice = agent._play_named_card(
                context,
                card_names=("圣俗杂座",),
                rule_id="action.ice_water_shift_furina_to_pressure",
            )
            if choice is not None:
                return choice

    if active_name == "芙宁娜":
        if (
            "黄金剧团的奖赏" in hand_name_set
            and round_number <= 2
            and (has_pressure_summon or summon_count <= 0)
        ):
            choice = _play_named_card_on_targets(
                agent,
                context,
                card_names=("黄金剧团的奖赏",),
                target_names=("芙宁娜",),
                rule_id="action.ice_water_arm_furina",
                min_tactical_score=1,
            )
            if choice is not None:
                return choice
        if "很棒，哥们。" in hand_name_set and not has_vehicle_in_hand and (has_pressure_summon or round_number >= 2):
            choice = agent._play_named_card(
                context,
                card_names=("很棒，哥们。",),
                rule_id="action.ice_water_prime_technique",
            )
            if choice is not None:
                return choice
        if round_number <= 2 and summon_count <= 0:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_E",),
                rule_id="action.ice_water_setup_furina",
            )
            if choice is not None:
                return choice
        if round_number >= 3 and summon_count <= 0 and furina_pressure_mode and aura <= 0:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_E",),
                rule_id="action.ice_water_keep_hydro",
            )
            if choice is not None:
                return choice
        if round_number >= 5 and summon_count <= 0 and furina_heal_mode and urgent_team_heal:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_E",),
                rule_id="action.ice_water_keep_heal",
            )
            if choice is not None:
                return choice
        if 3130063 in legal_skill_ids and aura > 0 and skirk_subtlety >= 5 and bullet_count <= 0 and not urgent_team_heal:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(3130063,),
                rule_id="action.ice_water_vehicle_to_skirk",
            )
            if choice is not None:
                return choice
        if round_number >= 4 and (aura > 0 or summon_count > 0):
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q",),
                rule_id="action.ice_water_furina_pressure",
            )
            if choice is not None:
                return choice
            if opponent_hp <= 2:
                choice = agent._use_active_skill_types(
                    context,
                    skill_types=("GCG_SKILL_TAG_A",),
                    rule_id="action.ice_water_furina_pressure",
                )
            if choice is not None:
                return choice
        if has_vehicle_in_hand and (aura > 0 or summon_count > 0 or round_number >= 3):
            choice = _play_named_card_on_targets(
                agent,
                context,
                card_names=("绒翼龙",),
                target_names=("恰斯卡",),
                rule_id="action.ice_water_prime_chasca_vehicle",
                min_tactical_score=1,
            )
            if choice is not None:
                return choice
        if (
            round_number <= 3
            and (has_pressure_summon or aura > 0)
            and chasca_nightsoul <= 0
            and bullet_count <= 0
            and remaining_dice >= 1
            and (
                chasca_has_vehicle
                or chasca_has_staff
                or anemo_ready_dice >= 3
                or "子弹的戏法" in hand_name_set
                or has_vehicle_in_hand
            )
        ):
            choice = agent._switch_to_named_targets(
                context,
                target_names=("恰斯卡",),
                rule_id="action.ice_water_switch_finisher_setup",
                require_safe_target=True,
            )
            if choice is not None:
                return choice
        if (
            round_number <= 2
            and has_pressure_summon
            and chasca_nightsoul <= 0
            and bullet_count <= 0
            and chasca_engine_ready
            and remaining_dice <= 3
            and not has_free_card
        ):
            hold = _declare_end(agent, context, rule_id="action.ice_water_hold_furina_window")
            if hold is not None:
                return hold
        if (
            round_number <= 3
            and aura <= 0
            and summon_count > 0
            and bullet_count <= 0
            and chasca_nightsoul <= 0
            and remaining_dice <= 2
            and not has_free_card
        ):
            hold = _declare_end(agent, context, rule_id="action.ice_water_hold_furina_window")
            if hold is not None:
                return hold
        if aura > 0 and skirk_subtlety >= 5 and bullet_count <= 0 and chasca_nightsoul <= 0 and remaining_dice >= 1:
            choice = agent._switch_to_named_targets(
                context,
                target_names=("丝柯克",),
                rule_id="action.ice_water_switch_skirk_finish",
                require_safe_target=True,
            )
            if choice is not None:
                return choice
        if (
            aura > 0
            or (bullet_count > 0 and round_number >= 4)
            or (chasca_nightsoul > 0 and round_number >= 4)
            or (has_pressure_summon and round_number >= 4)
        ) and remaining_dice >= 1:
            choice = agent._switch_to_named_targets(
                context,
                target_names=("恰斯卡",),
                rule_id="action.ice_water_switch_finisher_setup",
                require_safe_target=True,
            )
            if choice is not None:
                return choice
        if (
            round_number >= 4
            and remaining_dice <= 3
            and not has_free_card
            and (furina_heal_mode or has_pressure_summon or has_heal_summon)
        ):
            hold = _declare_end(agent, context, rule_id="action.ice_water_hold_furina_window")
            if hold is not None:
                return hold
        if round_number <= 2 and summon_count <= 0 and aura <= 0 and remaining_dice <= 2 and not has_free_card:
            hold = _declare_end(agent, context, rule_id="action.ice_water_hold_furina_window")
            if hold is not None:
                return hold
        if remaining_dice <= 0 and not has_skill_action and not has_free_card:
            return _declare_end(agent, context, rule_id="action.ice_water_hold_furina_window")
        return None

    if active_name == "丝柯克":
        if (
            11163 in legal_skill_ids
            and (
                skirk_subtlety >= 5
                or (skirk_subtlety >= 4 and round_number >= 4)
                or (skirk_subtlety >= 3 and opponent_hp <= skirk_subtlety + 2)
            )
        ):
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(11163,),
                rule_id="action.ice_water_skirk_pressure",
            )
            if choice is not None:
                return choice
        if void_rift_count > 0 and (round_number >= 4 or aura > 0):
            choice = agent._play_named_card(
                context,
                card_names=("湮远",),
                rule_id="action.ice_water_play_skirk_talent",
            )
            if choice is not None:
                return choice
        if (
            not skirk_flash_ready
            and 11161 in legal_skill_ids
            and cryo_ready_dice >= 1
            and remaining_dice >= 1
            and (aura > 0 or round_number >= 4 or opponent_hp <= 4)
            and (
                11163 not in legal_skill_ids
                or cryo_ready_dice <= 2
                or skirk_subtlety <= 4
            )
        ):
            choice = agent._play_named_card(
                context,
                card_names=("诸武相授",),
                rule_id="action.ice_water_play_skirk_talent",
            )
            if choice is not None:
                return choice
        if has_vehicle_in_hand and (aura > 0 or round_number >= 3):
            choice = _play_named_card_on_targets(
                agent,
                context,
                card_names=("绒翼龙",),
                target_names=("恰斯卡",),
                rule_id="action.ice_water_prime_chasca_vehicle",
                min_tactical_score=1,
            )
            if choice is not None:
                return choice
        if "运筹帷幄" in hand_name_set and round_number >= 4 and hand_size <= 2:
            choice = agent._play_named_card(
                context,
                card_names=("运筹帷幄",),
                rule_id="action.ice_water_skirk_refill_hand",
            )
            if choice is not None:
                return choice
        if "很棒，哥们。" in hand_name_set and round_number >= 4 and not has_vehicle_in_hand:
            choice = agent._play_named_card(
                context,
                card_names=("很棒，哥们。",),
                rule_id="action.ice_water_skirk_prime_technique",
            )
            if choice is not None:
                return choice
        if "子弹的戏法" in hand_name_set and bullet_count <= 0 and round_number >= 2:
            choice = _play_named_card_on_targets(
                agent,
                context,
                card_names=("子弹的戏法",),
                target_names=("恰斯卡",),
                rule_id="action.ice_water_load_cryo_bullet",
            )
            if choice is not None:
                return choice
        if 11165 in legal_skill_ids and (
            (zero_cost_card_count >= 2 and (round_number >= 4 or skirk_subtlety <= 4))
            or (round_number >= 5 and zero_cost_card_count > 0 and skirk_subtlety <= 2)
        ):
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(11165,),
                rule_id="action.ice_water_skirk_restock",
            )
            if choice is not None:
                return choice
        if 11162 in legal_skill_ids and (
            (aura > 0 and skirk_subtlety <= 4 and 11163 not in legal_skill_ids)
            or (has_pressure_summon and round_number >= 4 and skirk_subtlety <= 3)
            or (round_number >= 6 and skirk_subtlety <= 2)
        ):
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(11162,),
                rule_id="action.ice_water_skirk_setup",
            )
            if choice is not None:
                return choice
        if 11163 in legal_skill_ids and skirk_subtlety <= 2 and opponent_hp > skirk_subtlety + 2:
            if has_pressure_summon or chasca_engine_ready or bullet_count > 0 or chasca_nightsoul > 0:
                choice = agent._switch_to_named_targets(
                    context,
                    target_names=("恰斯卡", "芙宁娜"),
                    rule_id="action.ice_water_reset_hydro_setup",
                    require_safe_target=True,
                )
                if choice is not None:
                    return choice
            if furina_alive:
                choice = agent._switch_to_named_targets(
                    context,
                    target_names=("芙宁娜", "恰斯卡"),
                    rule_id="action.ice_water_reset_hydro_setup",
                    require_safe_target=True,
                )
                if choice is not None:
                    return choice
            if remaining_dice <= 1 and not has_free_card:
                return _declare_end(agent, context, rule_id="action.ice_water_hold_skirk_window")
        if aura <= 0:
            if skirk_flash_ready and opponent_hp <= 2 and 11161 in legal_skill_ids:
                choice = agent._use_active_skill_definition_ids(
                    context,
                    skill_definition_ids=(11161,),
                    rule_id="action.ice_water_skirk_cryo_close",
                )
                if choice is not None:
                    return choice
            if skirk_flash_ready and 11161 in legal_skill_ids and round_number >= 4:
                choice = agent._use_active_skill_definition_ids(
                    context,
                    skill_definition_ids=(11161,),
                    rule_id="action.ice_water_skirk_cryo_setup",
                )
                if choice is not None:
                    return choice
            if has_pressure_summon or chasca_engine_ready or chasca_nightsoul > 0:
                choice = agent._switch_to_named_targets(
                    context,
                    target_names=("恰斯卡",),
                    rule_id="action.ice_water_reset_hydro_setup",
                    require_safe_target=True,
                )
                if choice is not None:
                    return choice
            if round_number <= 2 and summon_count <= 0 and not chasca_engine_ready:
                choice = agent._switch_to_named_targets(
                    context,
                    target_names=("芙宁娜",),
                    rule_id="action.ice_water_reset_hydro_setup",
                    require_safe_target=True,
                )
                if choice is not None:
                    return choice
            if 11165 in legal_skill_ids and 11161 not in legal_skill_ids and zero_cost_card_count <= 1 and not has_free_card:
                return _declare_end(agent, context, rule_id="action.ice_water_hold_skirk_window")
            if remaining_dice <= 0 and not has_skill_action and not has_free_card:
                return _declare_end(agent, context, rule_id="action.ice_water_hold_skirk_window")
            return None
        if skirk_flash_ready and aura > 0 and 11161 in legal_skill_ids:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(11161,),
                rule_id="action.ice_water_skirk_cryo_pressure",
            )
            if choice is not None:
                return choice
        if skirk_flash_ready and opponent_hp <= 2 and 11161 in legal_skill_ids:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(11161,),
                rule_id="action.ice_water_skirk_cryo_close",
            )
            if choice is not None:
                return choice
        if 11161 in legal_skill_ids and (skirk_flash_ready or (aura <= 0 and round_number >= 5)):
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(11161,),
                rule_id="action.ice_water_skirk_last_pressure",
            )
            if choice is not None:
                return choice
        if 3130063 in legal_skill_ids and (aura > 0 or round_number >= 4):
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(3130063,),
                rule_id="action.ice_water_skirk_vehicle_reposition",
            )
            if choice is not None:
                return choice
        if has_pressure_summon or bullet_count > 0 or chasca_nightsoul > 0 or "子弹的戏法" in hand_name_set:
            choice = agent._switch_to_named_targets(
                context,
                target_names=("恰斯卡",),
                rule_id="action.ice_water_freeze_finish",
                require_safe_target=True,
            )
            if choice is not None:
                return choice
        if 11165 in legal_skill_ids and 11161 not in legal_skill_ids and zero_cost_card_count <= 1 and not has_free_card:
            return _declare_end(agent, context, rule_id="action.ice_water_hold_skirk_window")
        if remaining_dice <= 0 and not has_skill_action and not has_free_card:
            return _declare_end(agent, context, rule_id="action.ice_water_hold_skirk_window")
        choice = agent._switch_to_named_targets(
            context,
            target_names=("恰斯卡",),
            rule_id="action.ice_water_freeze_finish",
            require_safe_target=True,
        )
        if choice is not None:
            return choice
        return None

    if active_name == "恰斯卡":
        if (
            3130063 in legal_skill_ids
            and not furina_alive
            and skirk_alive
            and bullet_count <= 0
            and chasca_nightsoul <= 0
            and (aura > 0 or skirk_subtlety >= 4 or round_number >= 4)
        ):
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(3130063,),
                rule_id="action.ice_water_vehicle_to_skirk",
            )
            if choice is not None:
                return choice
        if 3130063 in legal_skill_ids and aura <= 0 and bullet_count <= 0 and not chasca_engine_ready:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(3130063,),
                rule_id="action.ice_water_vehicle_reset_hydro",
            )
            if choice is not None:
                return choice
        if (
            round_number <= 2
            and chasca_nightsoul <= 0
            and 15112 not in legal_skill_ids
            and bullet_count <= 0
            and "子弹的戏法" not in hand_name_set
            and summon_count <= 0
            and remaining_dice >= 1
        ):
            choice = agent._switch_to_named_targets(
                context,
                target_names=("芙宁娜",),
                rule_id="action.ice_water_reset_hydro_setup",
                require_safe_target=True,
            )
            if choice is not None:
                return choice
        if (
            round_number <= 2
            and chasca_nightsoul <= 0
            and 15112 not in legal_skill_ids
            and bullet_count <= 0
            and "子弹的戏法" not in hand_name_set
            and summon_count <= 0
            and remaining_dice <= 2
            and not has_free_card
        ):
            hold = _declare_end(agent, context, rule_id="action.ice_water_hold_chasca_window")
            if hold is not None:
                return hold
        if aura > 0 and bullet_count <= 0 and chasca_nightsoul <= 0 and skirk_subtlety >= 5:
            choice = agent._switch_to_named_targets(
                context,
                target_names=("丝柯克",),
                rule_id="action.ice_water_freeze_finish",
                require_safe_target=True,
            )
            if choice is not None:
                return choice
        if round_number <= 2 and chasca_nightsoul <= 0 and 15112 in legal_skill_ids:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(15112,),
                rule_id="action.ice_water_setup_chasca",
            )
            if choice is not None:
                return choice
        if (
            round_number <= 2
            and chasca_nightsoul > 0
            and bullet_count <= 0
            and "子弹的戏法" not in hand_name_set
            and not has_vehicle_in_hand
            and summon_count <= 0
        ):
            choice = agent._switch_to_named_targets(
                context,
                target_names=("芙宁娜",),
                rule_id="action.ice_water_reset_hydro_setup",
                require_safe_target=True,
            )
            if choice is not None:
                return choice
        if (
            "子弹的戏法" in hand_name_set
            and (
                aura > 0
                or (round_number >= 4 and (summon_count > 0 or chasca_nightsoul > 0 or hand_size <= 2))
                or opponent_hp <= 3
            )
        ):
            choice = agent._play_named_card(
                context,
                card_names=("子弹的戏法",),
                rule_id="action.ice_water_build_engine",
            )
            if choice is not None:
                return choice
        if (
            "飞行队出击！" in hand_name_set
            and (
                bullet_count > 0
                or round_number >= 4
                or (aura > 0 and chasca_nightsoul > 0 and hand_size <= 2)
            )
        ):
            choice = agent._play_named_card(
                context,
                card_names=("飞行队出击！",),
                rule_id="action.ice_water_refill_hand",
                min_tactical_score=1,
            )
            if choice is not None:
                return choice
        if (
            "很棒，哥们。" in hand_name_set
            and not has_vehicle_in_hand
            and bullet_count <= 0
            and (round_number <= 2 or chasca_nightsoul <= 0)
        ):
            choice = agent._play_named_card(
                context,
                card_names=("很棒，哥们。",),
                rule_id="action.ice_water_build_engine",
            )
            if choice is not None:
                return choice
        if has_vehicle_in_hand and (aura > 0 or bullet_count > 0 or (round_number >= 4 and chasca_nightsoul > 0)):
            choice = agent._play_named_card(
                context,
                card_names=("绒翼龙",),
                rule_id="action.ice_water_build_engine",
            )
            if choice is not None:
                return choice
        if "中央实验室遗址" in hand_name_set and (bullet_count > 0 or "飞行队出击！" in hand_name_set or hand_size >= 4):
            choice = agent._play_named_card(
                context,
                card_names=("中央实验室遗址",),
                rule_id="action.ice_water_build_engine",
                min_tactical_score=1,
            )
            if choice is not None:
                return choice
        if bullet_count > 0 and (aura > 0 or chasca_nightsoul > 0 or opponent_hp <= 3 or round_number >= 4):
            choice = agent._play_named_card(
                context,
                card_names=bullet_names,
                rule_id="action.ice_water_bullet_pressure",
                min_tactical_score=0,
            )
            if choice is not None:
                return choice
        if aura > 0:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(15113,),
                rule_id="action.ice_water_swirl_finish",
            )
            if choice is not None:
                return choice
        if (
            chasca_nightsoul > 0
            and 1151121 in legal_skill_ids
            and (
                bullet_count > 0
                or round_number >= 5
                or "幻戏倒计时：3" in hand_name_set
                or hand_size >= 6
                or opponent_hp <= 3
                or (aura > 0 and has_vehicle_in_hand and hand_size >= 5)
            )
        ):
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(1151121,),
                rule_id="action.ice_water_swirl_finish",
            )
            if choice is not None:
                return choice
        if (
            round_number <= 3
            and chasca_nightsoul > 0
            and bullet_count <= 0
            and not has_vehicle_in_hand
            and 1151121 not in legal_skill_ids
            and not has_free_card
        ):
            hold = _declare_end(agent, context, rule_id="action.ice_water_hold_chasca_window")
            if hold is not None:
                return hold
        if (
            aura > 0
            and chasca_nightsoul <= 0
            and round_number >= 5
            and remaining_dice >= 3
            and (summon_count > 0 or "飞行队出击！" in hand_name_set)
            and 15112 in legal_skill_ids
        ):
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(15112,),
                rule_id="action.ice_water_prime_nightsoul_finish",
            )
            if choice is not None:
                return choice
        if (
            aura <= 0
            and bullet_count <= 0
            and remaining_dice >= 1
            and (chasca_nightsoul > 0 or round_number >= 3)
            and summon_count <= 0
            and not chasca_engine_ready
        ):
            return agent._switch_to_named_targets(
                context,
                target_names=("芙宁娜",),
                rule_id="action.ice_water_reset_hydro_setup",
                require_safe_target=True,
            )
        if remaining_dice <= 0 and not has_skill_action and not has_free_card:
            return _declare_end(agent, context, rule_id="action.ice_water_hold_chasca_window")
        return None
    return None


def card_context_bonus(agent, card, context: DecisionContext, mode: str) -> int:
    if card is None:
        return 0
    name = str(card.name)
    round_number = agent._round_number(context)
    active_name = agent._active_character_name(context)
    hand_names = tuple(agent._hand_card_names(context))
    hand_name_set = set(hand_names)
    hand_size = agent._hand_size(context)
    summon_definition_ids = set(_summon_definition_ids(agent, context))
    summon_count = len(summon_definition_ids)
    aura = agent._opponent_active_aura(context)
    bullet_count = sum(1 for item in hand_names if item == "追影弹" or item.startswith("焕光追影弹"))
    has_vehicle_in_hand = "绒翼龙" in hand_name_set
    chasca_nightsoul = agent._character_nightsoul(context, character_name="恰斯卡")
    skirk_subtlety = agent._character_special_energy(
        context,
        character_name="丝柯克",
        fallback_variable_name="serpentsSubtlety",
    )
    skirk_flash_ready = _active_has_entity(agent, context, definition_ids=(111162,))
    void_rift_count = sum(1 for item in agent._hand_card_names(context) if item == "虚境裂隙")
    furina_definition_id = _character_definition_id(agent, context, character_name="芙宁娜")
    furina_pressure_mode = furina_definition_id == 1211
    furina_heal_mode = furina_definition_id == 1212
    has_pressure_summon = 112111 in summon_definition_ids
    total_missing_hp, _, low_hp_count, critical_hp_count = _team_health_snapshot(agent, context)
    urgent_team_heal = critical_hp_count > 0 or total_missing_hp >= 8 or (low_hp_count >= 2 and round_number >= 4)
    if name == "运筹帷幄":
        if active_name == "芙宁娜" and round_number <= 2 and summon_count <= 0:
            return -8
        if active_name == "恰斯卡" and round_number <= 2:
            return -10
        if active_name == "丝柯克":
            return 4 if round_number >= 4 and hand_size <= 2 else -6
        return 4 if active_name == "恰斯卡" and round_number >= 3 and bullet_count > 0 else -4
    if name == "圣俗杂座":
        if furina_pressure_mode and round_number >= 4 and urgent_team_heal and has_pressure_summon:
            return 10
        if furina_heal_mode and not urgent_team_heal and round_number >= 2 and (aura <= 0 or not has_pressure_summon):
            return 8
        return -8
    if name == "清洁工作":
        return -10
    if name == "交给我吧！":
        if active_name == "丝柯克" and round_number in {3, 4}:
            return -8
        if active_name == "芙宁娜" and round_number <= 2 and (has_pressure_summon or aura > 0):
            return 4
        if active_name == "恰斯卡" and round_number <= 2 and chasca_nightsoul > 0:
            return 2
        return 2 if round_number <= 2 else -2
    if name == "飞行队出击！":
        if active_name == "芙宁娜" and summon_count <= 0:
            return -4
        if active_name == "恰斯卡" and (bullet_count > 0 or round_number >= 4 or (aura > 0 and chasca_nightsoul > 0 and hand_size <= 2)):
            return 4
        return -8 if active_name == "恰斯卡" and round_number <= 2 else -4
    if name == "黄金剧团的奖赏":
        if active_name == "芙宁娜" and round_number <= 3:
            return 7 if furina_pressure_mode or has_pressure_summon else 5
        return -10 if active_name == "丝柯克" else -6
    if name == "琴音之诗":
        if active_name == "芙宁娜" and round_number <= 2 and summon_count <= 0:
            return -6
        if active_name in {"芙宁娜", "恰斯卡"} and round_number >= 3:
            return 3
        return -4 if active_name == "丝柯克" else -2
    if name == "野猪公主":
        if round_number >= 3 and active_name in {"芙宁娜", "恰斯卡"}:
            return 3
        return -4 if round_number <= 2 else -1
    if name == "子弹的戏法":
        if active_name == "丝柯克":
            return 5 if aura > 0 or round_number >= 2 else 1
        if active_name == "芙宁娜":
            return 2 if round_number >= 3 and summon_count > 0 and bullet_count <= 0 else -2
        if active_name == "恰斯卡" and (summon_count > 0 or chasca_nightsoul > 0):
            return 5
        if active_name == "恰斯卡" and aura <= 0 and round_number <= 3:
            return -6
        return 1 if active_name == "恰斯卡" and round_number >= 4 else -3
    if name == "追影弹" or name.startswith("焕光追影弹"):
        if active_name != "恰斯卡" and round_number >= 4:
            return -4
        return 4 if active_name == "恰斯卡" and (chasca_nightsoul > 0 or aura > 0) else 1
    if name == "绒翼龙":
        if active_name == "丝柯克":
            return 4 if aura > 0 or round_number >= 3 else 1
        if active_name == "芙宁娜":
            return 2 if round_number >= 3 else -2
        if active_name == "恰斯卡" and (chasca_nightsoul > 0 or bullet_count > 0 or aura > 0):
            return 4
        return -4 if round_number <= 2 else -2
    if name == "中央实验室遗址":
        if active_name == "恰斯卡" and round_number >= 3 and (bullet_count > 0 or hand_size >= 4):
            return 5
        return -9 if round_number <= 2 else -3
    if name == "风龙废墟":
        if active_name == "丝柯克" and round_number >= 4:
            return 1
        return -10 if round_number <= 4 else -4
    if name in {"西尔弗和迈勒斯", "白手套和渔夫"} and active_name == "丝柯克":
        return -10
    if name == "指挥的礼帽":
        if active_name == "芙宁娜" and round_number <= 4:
            return -5
        return 3 if active_name == "恰斯卡" else 1 if active_name == "丝柯克" and aura > 0 else 0
    if name == "西尔弗和迈勒斯":
        if round_number >= 4 and active_name == "恰斯卡" and (aura > 0 or bullet_count > 0):
            return 2
        return -10 if round_number <= 3 else -3
    if name == "很棒，哥们。":
        if active_name == "丝柯克":
            return 4 if round_number >= 4 and not has_vehicle_in_hand else -3
        if active_name == "恰斯卡" and not has_vehicle_in_hand and bullet_count <= 0 and round_number <= 2:
            return 5
        if active_name == "恰斯卡" and round_number >= 3 and not has_vehicle_in_hand and bullet_count <= 0 and chasca_nightsoul <= 0:
            return 3
        if active_name == "恰斯卡" and (bullet_count > 0 or chasca_nightsoul > 0):
            return -6
        if active_name == "芙宁娜" and not has_vehicle_in_hand and (has_pressure_summon or round_number >= 2):
            return 3
        return -2
    if name == "白手套和渔夫":
        if active_name == "丝柯克":
            return -12
        return -10 if round_number <= 3 else -3
    if name == "沙王的投影":
        if active_name == "丝柯克":
            return 3 if aura > 0 and round_number >= 4 else -3
        return 2 if active_name == "恰斯卡" and (bullet_count > 0 or (aura > 0 and chasca_nightsoul > 0)) else -4
    if name == "清籁岛":
        return -6
    if name == "湮远":
        if active_name == "丝柯克" and void_rift_count > 0 and (aura > 0 or round_number >= 4):
            return 6
        return -6
    if name == "诸武相授":
        if active_name == "丝柯克" and not skirk_flash_ready and skirk_subtlety <= 4 and (round_number <= 4 or aura > 0):
            return 5
        return -6
    return 0
