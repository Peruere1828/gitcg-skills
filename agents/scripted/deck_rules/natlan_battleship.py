# Ported from Rebel_base_RL research/world_model (AGPL-3.0, github.com/.../Rebel_base_RL) by genshin-GITCG.
# Keep upstream semantics; adapt imports only. See NOTICE.md.
from __future__ import annotations

from reps.schema import DecisionContext, OptionKind

from .common import make_rule_config
from ..models import RuleChoice

RULE_CONFIG = make_rule_config(
    slug="natlan_battleship",
    opener="玛薇卡",
    carry=("玛薇卡", "茜特菈莉", "希诺宁"),
    bench=("茜特菈莉", "希诺宁"),
    preferred_elements=("GCG_TAG_ELEMENT_PYRO", "GCG_TAG_ELEMENT_CRYO", "GCG_TAG_ELEMENT_GEO"),
    style="tempo_setup",
)

VEHICLE_CARD_IDS = {113154, 113155, 113156}
DISCOVER_VEHICLE_NAMES = (
    "异色猎刀鳐",
    "匿叶龙",
    "鳍游龙",
    "嵴锋龙",
    "暝视龙",
    "绒翼龙",
    "浪船",
    "突角龙",
    "呀！呀！",
)
IZPAPA_STATUS_ID = 111143
WHITE_SHIELD_STATUS_ID = 111142
NIGHTSOUL_PAYOFF_SKILL_IDS = {1131541, 1131561, 1161121}
STARCALLER_WATCH_ID = 311112
HOLY_CROWN_ID = 312033
KATHERYNE_SUPPORT_ID = 322002
VANARANA_SUPPORT_ID = 321011
STADIUM_SUPPORT_ID = 321022


def _entity_ids(entities) -> set[int]:
    return {
        int(getattr(entity, "definition_id", 0))
        for entity in entities
        if int(getattr(entity, "definition_id", 0)) > 0
    }


def _character_entity_ids(agent, context: DecisionContext, name: str) -> set[int]:
    for character in agent._player(context).characters:
        if agent._character_name(character.definition_id) == name:
            return _entity_ids(getattr(character, "entities", ()))
    return set()


def _combat_status_ids(agent, context: DecisionContext) -> set[int]:
    return _entity_ids(agent._player(context).combat_statuses)


def _support_ids(agent, context: DecisionContext) -> set[int]:
    return _entity_ids(agent._player(context).supports)


def _legal_skill_ids(agent, context: DecisionContext) -> set[int]:
    return {
        int(spec.subject_definition_id)
        for _, spec in agent._pairs(context, kind=OptionKind.ACTION_USE_SKILL)
        if int(spec.subject_definition_id) > 0
    }


def _alive_count(agent, context: DecisionContext) -> int:
    return sum(1 for character in agent._player(context).characters if not character.defeated)


def _opponent_active_health(agent, context: DecisionContext) -> int:
    state = context.player_view or context.full_state
    opponent = state.players[1 - context.acting_player]
    for character in opponent.characters:
        if character.is_active:
            return int(character.health)
    return 0


def _opponent_alive_count(agent, context: DecisionContext) -> int:
    state = context.player_view or context.full_state
    opponent = state.players[1 - context.acting_player]
    return sum(1 for character in opponent.characters if not character.defeated)


def _total_alive_health(agent, context: DecisionContext, *, opponent: bool = False) -> int:
    state = context.player_view or context.full_state
    player_index = 1 - context.acting_player if opponent else context.acting_player
    player = state.players[player_index]
    return sum(int(character.health) for character in player.characters if not character.defeated)


def _materially_behind(
    *,
    hp_margin: int,
    self_alive_count: int,
    opponent_alive_count: int,
    active_health: int,
    opponent_active_health: int,
    low_hp_threshold: int,
) -> bool:
    if self_alive_count < opponent_alive_count:
        return True
    if hp_margin <= -6:
        return True
    return (
        hp_margin <= -4
        and active_health <= low_hp_threshold
        and opponent_active_health >= active_health + 2
    )


def _actor_slug(name: str) -> str:
    return {
        "玛薇卡": "mavuika",
        "茜特菈莉": "citlali",
        "希诺宁": "xilonen",
    }.get(name, "natlan")


def _rule_id(active_name: str, suffix: str) -> str:
    return f"action.natlan_{_actor_slug(active_name)}_{suffix}"


def _declare_end(agent, context: DecisionContext, rule_id: str) -> RuleChoice | None:
    for action_code, _ in agent._pairs(context, kind=OptionKind.ACTION_DECLARE_END):
        return RuleChoice(action_code=int(action_code), rule_id=rule_id)
    return None


def _declare_actor_end(agent, context: DecisionContext, active_name: str) -> RuleChoice | None:
    return _declare_end(agent, context, _rule_id(active_name, "hold_end"))


def _play_named_for_actor(
    agent,
    context: DecisionContext,
    *,
    active_name: str,
    suffix: str,
    card_names: tuple[str, ...],
    min_tactical_score: int | None = None,
) -> RuleChoice | None:
    return agent._play_named_card(
        context,
        card_names=card_names,
        rule_id=_rule_id(active_name, suffix),
        min_tactical_score=min_tactical_score,
    )


def _mavuika_vehicle_equipped(agent, context: DecisionContext) -> bool:
    return bool(_character_entity_ids(agent, context, "玛薇卡") & VEHICLE_CARD_IDS)


def _mavuika_vehicle_name(agent, context: DecisionContext) -> str | None:
    entity_ids = _character_entity_ids(agent, context, "玛薇卡")
    for definition_id, name in (
        (113154, "驰轮车·跃升"),
        (113155, "驰轮车·涉渡"),
        (113156, "驰轮车·疾驰"),
    ):
        if definition_id in entity_ids:
            return name
    return None


def _mavuika_vehicle_priorities(
    *,
    round_number: int,
    aura: int,
    hand_size: int,
    has_izpapa: bool,
    opponent_active_health: int,
    hand_names: set[str],
    switch_engine_ready: bool,
    active_is_hurt: bool,
) -> tuple[str, ...]:
    priorities: list[str] = []
    pressure_window = aura > 0 or opponent_active_health <= 6
    can_cycle_switch_vehicle = has_izpapa and (
        round_number >= 4 or switch_engine_ready or active_is_hurt or opponent_active_health <= 4
    )
    reload_vehicle_window = (
        round_number in {3, 4}
        and hand_size == 0
        and aura == 0
        and opponent_active_health > 5
        and not switch_engine_ready
        and not has_izpapa
        and not active_is_hurt
    )
    if round_number <= 2:
        if pressure_window:
            priorities.append("驰轮车·跃升")
        if can_cycle_switch_vehicle or switch_engine_ready or has_izpapa:
            priorities.append("驰轮车·涉渡")
        if not pressure_window and hand_size == 0 and not switch_engine_ready and not has_izpapa:
            priorities.append("驰轮车·疾驰")
    elif round_number in {3, 4}:
        if pressure_window:
            priorities.append("驰轮车·跃升")
        if can_cycle_switch_vehicle:
            priorities.append("驰轮车·涉渡")
        if reload_vehicle_window:
            priorities.append("驰轮车·疾驰")
    if aura > 0 or opponent_active_health <= 6:
        priorities.append("驰轮车·跃升")
    if can_cycle_switch_vehicle or ("龙伙伴的聚餐" in hand_names and has_izpapa):
        priorities.append("驰轮车·涉渡")
    if reload_vehicle_window:
        priorities.append("驰轮车·疾驰")
    if hand_size <= 2 and round_number >= 5:
        priorities.append("驰轮车·疾驰")
    if round_number >= 5 and pressure_window:
        priorities.append("驰轮车·跃升")
    if has_izpapa:
        if can_cycle_switch_vehicle:
            priorities.extend(("驰轮车·涉渡", "驰轮车·跃升", "驰轮车·疾驰"))
        else:
            priorities.extend(("驰轮车·跃升", "驰轮车·疾驰", "驰轮车·涉渡"))
    elif round_number <= 2:
        priorities.extend(("驰轮车·疾驰", "驰轮车·跃升", "驰轮车·涉渡"))
    elif round_number == 3 and aura == 0 and opponent_active_health > 6 and hand_size > 2:
        priorities.extend(("驰轮车·跃升", "驰轮车·涉渡", "驰轮车·疾驰"))
    else:
        priorities.extend(("驰轮车·跃升", "驰轮车·疾驰", "驰轮车·涉渡"))
    return tuple(dict.fromkeys(priorities))


def _play_vehicle_card(
    agent,
    context: DecisionContext,
    *,
    round_number: int,
    aura: int,
    hand_size: int,
    has_izpapa: bool,
    opponent_active_health: int,
    hand_names: set[str],
    switch_engine_ready: bool,
    active_is_hurt: bool,
) -> RuleChoice | None:
    return agent._play_named_card(
        context,
        card_names=_mavuika_vehicle_priorities(
            round_number=round_number,
            aura=aura,
            hand_size=hand_size,
            has_izpapa=has_izpapa,
            opponent_active_health=opponent_active_health,
            hand_names=hand_names,
            switch_engine_ready=switch_engine_ready,
            active_is_hurt=active_is_hurt,
        ),
        rule_id="action.natlan_mavuika_equip_vehicle",
    )


def _discover_vehicle_priorities(
    *,
    round_number: int,
    active_name: str,
    aura: int,
    has_izpapa: bool,
    hand_size: int,
    alive_count: int,
    active_is_hurt: bool,
) -> tuple[str, ...]:
    priorities: list[str] = []
    if has_izpapa:
        priorities.append("鳍游龙")
    if active_is_hurt or alive_count <= 2:
        priorities.extend(("浪船", "暝视龙"))
    else:
        priorities.extend(("浪船", "绒翼龙"))
    if active_name == "玛薇卡" and aura > 0:
        priorities.append("异色猎刀鳐")
    if hand_size <= 3:
        priorities.append("嵴锋龙")
    if round_number >= 4:
        priorities.extend(("异色猎刀鳐", "匿叶龙"))
    if round_number >= 5 and alive_count <= 2:
        priorities.append("呀！呀！")
    priorities.extend(
        (
            "浪船",
            "绒翼龙",
            "鳍游龙",
            "嵴锋龙",
            "暝视龙",
            "异色猎刀鳐",
            "匿叶龙",
            "呀！呀！",
            "突角龙",
        )
    )
    return tuple(dict.fromkeys(priorities))


def _play_discovered_vehicle(
    agent,
    context: DecisionContext,
    *,
    active_name: str,
    round_number: int,
    aura: int,
    has_izpapa: bool,
    hand_size: int,
    alive_count: int,
    active_is_hurt: bool,
) -> RuleChoice | None:
    return _play_named_for_actor(
        agent,
        context,
        active_name=active_name,
        suffix="play_aux_vehicle",
        card_names=_discover_vehicle_priorities(
            round_number=round_number,
            active_name=active_name,
            aura=aura,
            has_izpapa=has_izpapa,
            hand_size=hand_size,
            alive_count=alive_count,
            active_is_hurt=active_is_hurt,
        ),
    )


def choose_active(agent, context: DecisionContext):
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)
    hand_names = set(agent._hand_card_names(context))
    if aura > 0:
        preferred = ("茜特菈莉", "玛薇卡", "希诺宁")
    elif "丛山锻火驰行" in hand_names and "凯瑟琳" in hand_names:
        preferred = ("玛薇卡", "茜特菈莉", "希诺宁")
    elif "「人之名」解放" in hand_names or any(name.startswith("驰轮车·") for name in hand_names):
        preferred = ("玛薇卡", "茜特菈莉", "希诺宁")
    elif "困困冥想术" in hand_names or "祭星者之望" in hand_names:
        preferred = ("茜特菈莉", "玛薇卡", "希诺宁")
    elif round_number <= 2 and "万家灶火" in hand_names and "「人之名」解放" not in hand_names:
        preferred = ("茜特菈莉", "玛薇卡", "希诺宁")
    elif round_number <= 3:
        preferred = ("玛薇卡", "茜特菈莉", "希诺宁")
    else:
        preferred = ("玛薇卡", "茜特菈莉", "希诺宁")
    return agent._choose_active_by_names(context, preferred, require_safe_target=True)


def choose_reroll(agent, context: DecisionContext):
    visible_dice = tuple(int(value) for value in context.request_payload.get("visible_dice", ()))
    if not visible_dice:
        return None
    active_name = agent._active_character_name(context)
    round_number = agent._round_number(context)
    has_izpapa = IZPAPA_STATUS_ID in _combat_status_ids(agent, context)
    vehicle_equipped = _mavuika_vehicle_equipped(agent, context)
    if active_name == "玛薇卡":
        primary = {"GCG_TAG_ELEMENT_PYRO"}
        secondary = {"GCG_TAG_ELEMENT_CRYO"} if round_number <= 2 else set()
        tertiary = set()
        rule_id = "reroll.natlan_mavuika_curve"
    elif active_name == "茜特菈莉":
        primary = {"GCG_TAG_ELEMENT_CRYO"}
        secondary = {"GCG_TAG_ELEMENT_PYRO"}
        tertiary = set() if has_izpapa else {"GCG_TAG_ELEMENT_GEO"} if round_number <= 2 else set()
        rule_id = "reroll.natlan_citlali_engine_curve" if has_izpapa else "reroll.natlan_citlali_setup_curve"
    else:
        primary = {"GCG_TAG_ELEMENT_GEO"}
        secondary = {"GCG_TAG_ELEMENT_PYRO"} if vehicle_equipped or round_number >= 4 else {"GCG_TAG_ELEMENT_CRYO"}
        tertiary = {"GCG_TAG_ELEMENT_CRYO"} if secondary != {"GCG_TAG_ELEMENT_CRYO"} and round_number <= 4 else set()
        rule_id = "reroll.natlan_xilonen_curve"
    desired = primary | secondary | tertiary
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
        keep_tertiary = sum(1 for die in kept if agent._die_to_tag(die) in tertiary)
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
            5 * keep_omni + 4 * keep_primary + 2 * keep_secondary + keep_tertiary,
            keep_omni,
            keep_primary,
            keep_secondary,
            keep_tertiary,
            -keep_off,
            reroll_off,
            -len(rerolled),
        )
        if best_score is None or score > best_score:
            best_code = int(action_code)
            best_score = score
    if best_code is None:
        return None
    return RuleChoice(action_code=best_code, rule_id=rule_id)


def choose_select_card(agent, context: DecisionContext):
    candidates: dict[str, int] = {}
    for action_code, spec in agent._pairs(context):
        card = agent.assets.cards.get(int(spec.subject_definition_id))
        if card is not None:
            candidates[str(card.name)] = int(action_code)
    if not candidates or set(candidates) - {"驰轮车·涉渡", "驰轮车·跃升", "驰轮车·疾驰"}:
        if set(candidates) and set(candidates) <= set(DISCOVER_VEHICLE_NAMES):
            round_number = agent._round_number(context)
            aura = agent._opponent_active_aura(context)
            active_name = agent._active_character_name(context)
            hand_size = agent._hand_size(context)
            alive_count = _alive_count(agent, context)
            active_character = agent._active_character(context)
            active_health = int(getattr(active_character, "health", 0))
            active_max_health = max(1, int(getattr(active_character, "max_health", 0) or 1))
            priorities = _discover_vehicle_priorities(
                round_number=round_number,
                active_name=active_name,
                aura=aura,
                has_izpapa=IZPAPA_STATUS_ID in _combat_status_ids(agent, context),
                hand_size=hand_size,
                alive_count=alive_count,
                active_is_hurt=active_health < active_max_health,
            )
            choice = agent._select_candidate_by_names(
                context,
                candidate_names=priorities,
                rule_id="select_card.discover_vehicle",
            )
            if choice is not None:
                return choice
        return None
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)
    hand_names = set(agent._hand_card_names(context))
    hand_size = agent._hand_size(context)
    support_ids = _support_ids(agent, context)
    active_character = agent._active_character(context)
    active_health = int(getattr(active_character, "health", 0))
    active_max_health = max(1, int(getattr(active_character, "max_health", 0) or 1))
    priorities = _mavuika_vehicle_priorities(
        round_number=round_number,
        aura=aura,
        hand_size=hand_size,
        has_izpapa=IZPAPA_STATUS_ID in _combat_status_ids(agent, context),
        opponent_active_health=_opponent_active_health(agent, context),
        hand_names=hand_names,
        switch_engine_ready=bool({"凯瑟琳", "丛山锻火驰行"} & hand_names) or KATHERYNE_SUPPORT_ID in support_ids,
        active_is_hurt=active_health < active_max_health,
    )
    for name in priorities:
        action_code = candidates.get(name)
        if action_code is not None:
            return RuleChoice(action_code=action_code, rule_id="select_card.mavuika_vehicle")
    return None


def choose_action(agent, context: DecisionContext):
    active_name = agent._active_character_name(context)
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)
    opponent_active_health = _opponent_active_health(agent, context)
    player = agent._player(context)
    hand_names = tuple(agent._hand_card_names(context))
    hand_name_set = set(hand_names)
    hand_size = len(hand_names)
    dice_count = len(player.dice)
    support_ids = _support_ids(agent, context)
    combat_status_ids = _combat_status_ids(agent, context)
    legal_skill_ids = _legal_skill_ids(agent, context)
    injured_exists = any(
        int(character.health) < int(character.max_health)
        for character in player.characters
        if not character.defeated
    )
    active_character = agent._active_character(context)
    active_health = int(getattr(active_character, "health", 0))
    active_max_health = max(1, int(getattr(active_character, "max_health", 0) or 1))
    active_is_hurt = active_health < active_max_health
    alive_count = _alive_count(agent, context)
    has_talent_in_hand = bool({"「人之名」解放", "丛山锻火驰行"} & hand_name_set)
    has_vehicle_in_hand = any(name.startswith("驰轮车·") for name in hand_name_set)
    has_izpapa = IZPAPA_STATUS_ID in combat_status_ids
    vehicle_equipped = _mavuika_vehicle_equipped(agent, context)
    vehicle_name = _mavuika_vehicle_name(agent, context)
    discovered_vehicle_in_hand = bool(set(DISCOVER_VEHICLE_NAMES) & hand_name_set)
    switch_engine_ready = bool({"凯瑟琳", "丛山锻火驰行"} & hand_name_set) or KATHERYNE_SUPPORT_ID in support_ids
    off_deck_window = "困困冥想术" in hand_name_set or discovered_vehicle_in_hand
    pressure_window = aura > 0 or opponent_active_health <= 6
    refill_window = hand_size <= 2 or (round_number >= 5 and hand_size <= 4)
    can_restock_with_legend = "万家灶火" in hand_name_set and not player.legend_used and round_number >= 2
    mavuika_jump_ready = 1131541 in legal_skill_ids
    mavuika_switch_ready = 1131551 in legal_skill_ids
    mavuika_dash_ready = 1131561 in legal_skill_ids
    xilonen_jump_ready = 1161121 in legal_skill_ids
    mavuika_has_crown = HOLY_CROWN_ID in _character_entity_ids(agent, context, "玛薇卡")
    citlali_has_watch = STARCALLER_WATCH_ID in _character_entity_ids(agent, context, "茜特菈莉")
    citlali_has_crown = HOLY_CROWN_ID in _character_entity_ids(agent, context, "茜特菈莉")
    xilonen_has_crown = HOLY_CROWN_ID in _character_entity_ids(agent, context, "希诺宁")
    low_hp_threshold = int(getattr(agent.profile.rule_config, "switch_hp_threshold", 4))
    opponent_alive_count = _opponent_alive_count(agent, context)
    total_health = _total_alive_health(agent, context)
    opponent_total_health = _total_alive_health(agent, context, opponent=True)
    hp_margin = total_health - opponent_total_health
    materially_behind = _materially_behind(
        hp_margin=hp_margin,
        self_alive_count=alive_count,
        opponent_alive_count=opponent_alive_count,
        active_health=active_health,
        opponent_active_health=opponent_active_health,
        low_hp_threshold=low_hp_threshold,
    )
    safe_setup_window = (
        aura == 0
        and round_number <= 3
        and alive_count == 3
        and active_health > low_hp_threshold
    )
    stabilize_window = round_number >= 4 and (
        active_health <= low_hp_threshold or alive_count <= 2
    )
    can_arm_offdeck_now = discovered_vehicle_in_hand or (
        "困困冥想术" in hand_name_set
        and round_number <= 2
        and not has_vehicle_in_hand
        and not vehicle_equipped
        and not discovered_vehicle_in_hand
        and dice_count >= 4
        and safe_setup_window
    )
    mavuika_reload_window = (
        vehicle_name == "驰轮车·疾驰"
        and round_number in {3, 4}
        and hand_size == 0
        and not pressure_window
        and opponent_active_health > 5
        and not switch_engine_ready
        and not has_izpapa
        and not active_is_hurt
    )
    xilonen_support_window = (
        injured_exists
        or hand_size <= 2
        or opponent_active_health <= 2
        or (aura > 0 and round_number <= 4)
    )
    xilonen_fast_switch_ready = (
        KATHERYNE_SUPPORT_ID in support_ids
        or "凯瑟琳" in hand_name_set
        or "丛山锻火驰行" in hand_name_set
        or vehicle_equipped
        or has_izpapa
    )
    xilonen_setup_window = (
        not pressure_window
        and (
            (round_number <= 2 and safe_setup_window and xilonen_fast_switch_ready)
            or (
                "燃素充盈" in hand_name_set
                and hand_size <= 1
                and round_number == 3
                and dice_count >= 3
                and (xilonen_fast_switch_ready or xilonen_jump_ready)
            )
        )
    )
    xilonen_reload_window = (
        hand_size == 0
        and not pressure_window
        and opponent_active_health > 2
        and round_number <= 3
    )
    xilonen_chip_finish_window = (
        opponent_active_health <= 2
        or (
            opponent_active_health <= 4
            and hand_size == 0
            and legal_skill_ids <= {16111}
            and not materially_behind
        )
    )
    xilonen_chip_pressure_window = (
        opponent_active_health <= 4
        and (
            round_number <= 4
            or legal_skill_ids <= {16111}
            or aura > 0
        )
    )
    if legal_skill_ids <= {16111} and round_number >= 5 and opponent_active_health <= 6:
        xilonen_chip_pressure_window = True

    if (
        "万家灶火" in hand_name_set
        and not bool(player.legend_used)
        and round_number == 1
        and not materially_behind
        and safe_setup_window
        and not has_talent_in_hand
        and active_name in {"玛薇卡", "茜特菈莉"}
    ):
        choice = _play_named_for_actor(
            agent,
            context,
            active_name=active_name,
            suffix="legend_open",
            card_names=("万家灶火",),
        )
        if choice is not None:
            return choice

    if active_name == "玛薇卡":
        if (
            round_number == 1
            and not has_vehicle_in_hand
            and not vehicle_equipped
            and aura == 0
            and hand_size >= 4
            and "万家灶火" not in hand_name_set
        ):
            choice = _play_named_for_actor(
                agent,
                context,
                active_name=active_name,
                suffix="play_talent",
                card_names=("「人之名」解放",),
            )
            if choice is not None:
                return choice
        if has_vehicle_in_hand:
            choice = _play_vehicle_card(
                agent,
                context,
                round_number=round_number,
                aura=aura,
                hand_size=hand_size,
                has_izpapa=has_izpapa,
                opponent_active_health=opponent_active_health,
                hand_names=hand_name_set,
                switch_engine_ready=switch_engine_ready,
                active_is_hurt=active_is_hurt,
            )
            if choice is not None and (
                not vehicle_equipped
                or not (mavuika_jump_ready or mavuika_switch_ready or mavuika_dash_ready)
            ):
                return choice
        if (
            mavuika_jump_ready
            and not mavuika_has_crown
            and "诸圣的礼冠" in hand_name_set
            and (pressure_window or round_number >= 4)
        ):
            choice = _play_named_for_actor(
                agent,
                context,
                active_name=active_name,
                suffix="arm_nightsoul_damage",
                card_names=("诸圣的礼冠",),
            )
            if choice is not None:
                return choice
        if aura > 0 and dice_count <= 2:
            choice = _play_named_for_actor(
                agent,
                context,
                active_name=active_name,
                suffix="fix_dice",
                card_names=("最好的伙伴！",),
            )
            if choice is not None:
                return choice
        if pressure_window and mavuika_jump_ready:
            choice = _play_named_for_actor(
                agent,
                context,
                active_name=active_name,
                suffix="pyro_finish_buffer",
                card_names=("龙龙饼干",),
            )
            if choice is not None:
                return choice
        if pressure_window:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(1131541, 13153),
                rule_id=_rule_id(active_name, "pyro_finish_window"),
            )
            if choice is not None:
                return choice
        if 13151 in legal_skill_ids and (
            (pressure_window and not (mavuika_jump_ready or 13153 in legal_skill_ids))
            or (round_number >= 3 and opponent_active_health <= 4 and active_health > low_hp_threshold)
        ):
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(13151,),
                rule_id=_rule_id(active_name, "pyro_chip_window"),
            )
            if choice is not None:
                return choice
        if vehicle_equipped and mavuika_switch_ready:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(1131551,),
                rule_id=_rule_id(active_name, "vehicle_rotate_setup"),
            )
            if choice is not None and (
                dice_count >= 2
                and (
                    has_izpapa
                    or round_number >= 3
                    or switch_engine_ready
                    or active_is_hurt
                    or opponent_active_health <= 4
                )
            ):
                return choice
        if vehicle_equipped and mavuika_dash_ready:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(1131561,),
                rule_id=_rule_id(active_name, "vehicle_reload_refill"),
            )
            if choice is not None and mavuika_reload_window:
                return choice
        if vehicle_equipped:
            choice = _play_named_for_actor(
                agent,
                context,
                active_name=active_name,
                suffix="extend_vehicle",
                card_names=("龙伙伴的聚餐",),
            )
            if choice is not None:
                if (
                    vehicle_name in {"驰轮车·跃升", "驰轮车·涉渡"}
                    and (round_number >= 2 or has_izpapa or pressure_window)
                ):
                    return choice
                if (
                    vehicle_name == "驰轮车·疾驰"
                    and round_number <= 2
                    and hand_size <= 1
                    and not pressure_window
                ):
                    return choice
        if not vehicle_equipped and not has_vehicle_in_hand:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(13152,),
                rule_id=_rule_id(active_name, "prepare_vehicle"),
            )
            if choice is not None and (
                round_number == 1
                and has_talent_in_hand
                and safe_setup_window
                and not switch_engine_ready
                and dice_count >= 3
            ):
                return choice
        if round_number >= 3 and legal_skill_ids <= {13152} and not pressure_window:
            return _declare_end(agent, context, _rule_id(active_name, "hold_setup_window"))
        if (
            can_restock_with_legend
            and not stabilize_window
            and (hand_size <= 3 or (round_number >= 3 and not has_talent_in_hand))
        ):
            choice = _play_named_for_actor(
                agent,
                context,
                active_name=active_name,
                suffix="legend_restock",
                card_names=("万家灶火",),
            )
            if choice is not None:
                return choice
        if (
            "困困冥想术" in hand_name_set
            and round_number <= 2
            and not has_vehicle_in_hand
            and not vehicle_equipped
            and not discovered_vehicle_in_hand
            and safe_setup_window
        ):
            choice = _play_named_for_actor(
                agent,
                context,
                active_name=active_name,
                suffix="discover_vehicle",
                card_names=("困困冥想术",),
            )
            if choice is not None:
                return choice
        if discovered_vehicle_in_hand and (has_izpapa or active_is_hurt or round_number >= 3):
            choice = _play_discovered_vehicle(
                agent,
                context,
                active_name=active_name,
                round_number=round_number,
                aura=aura,
                has_izpapa=has_izpapa,
                hand_size=hand_size,
                alive_count=alive_count,
                active_is_hurt=active_is_hurt,
            )
            if choice is not None:
                return choice
        if "运筹帷幄" in hand_name_set and dice_count >= 1 and refill_window:
            choice = _play_named_for_actor(
                agent,
                context,
                active_name=active_name,
                suffix="draw_reload",
                card_names=("运筹帷幄",),
            )
            if choice is not None:
                return choice
        if "桓那兰那" in hand_name_set and VANARANA_SUPPORT_ID not in support_ids and round_number <= 3 and dice_count >= 5 and hand_size <= 2 and not pressure_window:
            choice = _play_named_for_actor(
                agent,
                context,
                active_name=active_name,
                suffix="bank_dice",
                card_names=("桓那兰那",),
            )
            if choice is not None:
                return choice
        if (
            "圣火竞技场" in hand_name_set
            and STADIUM_SUPPORT_ID not in support_ids
            and 4 <= round_number <= 6
            and not stabilize_window
            and not pressure_window
            and (vehicle_equipped or active_is_hurt)
        ):
            choice = _play_named_for_actor(
                agent,
                context,
                active_name=active_name,
                suffix="arena_setup",
                card_names=("圣火竞技场",),
            )
            if choice is not None:
                return choice
        if (
            "凯瑟琳" in hand_name_set
            and KATHERYNE_SUPPORT_ID not in support_ids
            and safe_setup_window
            and (mavuika_switch_ready or "丛山锻火驰行" in hand_name_set)
        ):
            choice = _play_named_for_actor(
                agent,
                context,
                active_name=active_name,
                suffix="fast_switch_support",
                card_names=("凯瑟琳",),
            )
            if choice is not None:
                return choice
        if "最好的伙伴！" in hand_name_set and dice_count <= 2 and (opponent_active_health <= 4 or hand_size <= 4):
            choice = _play_named_for_actor(
                agent,
                context,
                active_name=active_name,
                suffix="fix_dice",
                card_names=("最好的伙伴！",),
            )
            if choice is not None:
                return choice
        choice = agent._switch_to_named_targets(
            context,
            target_names=("茜特菈莉", "希诺宁"),
            rule_id=_rule_id(active_name, "rotate_setup_front"),
            require_safe_target=True,
        )
        if choice is not None and round_number <= 2 and aura == 0 and not has_izpapa and not vehicle_equipped and dice_count >= 3:
            return choice
        if dice_count <= 1:
            return _declare_end(agent, context, _rule_id(active_name, "hold_end_low_dice"))
        return None

    if active_name == "茜特菈莉":
        if aura > 0:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_E"),
                rule_id=_rule_id(active_name, "melt_window"),
            )
            if choice is not None:
                return choice
        choice = agent._use_active_skill_types(
            context,
            skill_types=("GCG_SKILL_TAG_E",),
            rule_id=_rule_id(active_name, "setup_window"),
        )
        if choice is not None and (
            (
                round_number <= 2
                and (
                    "困困冥想术" in hand_name_set
                    or discovered_vehicle_in_hand
                    or "祭星者之望" in hand_name_set
                )
                and not ("「人之名」解放" in hand_name_set or has_vehicle_in_hand)
            )
            or (not has_izpapa and 3 <= round_number <= 5)
            or (round_number <= 3 and aura == 0 and discovered_vehicle_in_hand)
            or (not has_izpapa and round_number <= 2 and not has_vehicle_in_hand)
            or (not has_izpapa and round_number >= 5)
        ):
            return choice
        if (
            "祭星者之望" in hand_name_set
            and not citlali_has_watch
            and can_arm_offdeck_now
        ):
            choice = _play_named_for_actor(
                agent,
                context,
                active_name=active_name,
                suffix="arm_discovered_damage",
                card_names=("祭星者之望",),
            )
            if choice is not None:
                return choice
        if (
            "困困冥想术" in hand_name_set
            and round_number <= 2
            and not has_vehicle_in_hand
            and not vehicle_equipped
            and not discovered_vehicle_in_hand
            and safe_setup_window
        ):
            choice = _play_named_for_actor(
                agent,
                context,
                active_name=active_name,
                suffix="discover_vehicle",
                card_names=("困困冥想术",),
            )
            if choice is not None:
                return choice
        if discovered_vehicle_in_hand and (
            has_izpapa or active_is_hurt or round_number >= 3
        ):
            choice = _play_named_for_actor(
                agent,
                context,
                active_name=active_name,
                suffix="play_aux_vehicle",
                card_names=_discover_vehicle_priorities(
                    round_number=round_number,
                    active_name=active_name,
                    aura=aura,
                    has_izpapa=has_izpapa,
                    hand_size=hand_size,
                    alive_count=alive_count,
                    active_is_hurt=active_is_hurt,
                ),
            )
            if choice is not None:
                return choice
        if (
            "诸圣的礼冠" in hand_name_set
            and not citlali_has_crown
            and has_izpapa
            and 3 <= round_number <= 5
            and dice_count >= 2
            and (aura > 0 or discovered_vehicle_in_hand)
        ):
            choice = _play_named_for_actor(
                agent,
                context,
                active_name=active_name,
                suffix="arm_nightsoul_damage",
                card_names=("诸圣的礼冠",),
            )
            if choice is not None:
                return choice
        if can_restock_with_legend and hand_size <= 2 and not stabilize_window and not pressure_window:
            choice = _play_named_for_actor(
                agent,
                context,
                active_name=active_name,
                suffix="legend_restock",
                card_names=("万家灶火",),
            )
            if choice is not None:
                return choice
        if "运筹帷幄" in hand_name_set and dice_count >= 1 and refill_window and (not stabilize_window or hand_size <= 1):
            choice = _play_named_for_actor(
                agent,
                context,
                active_name=active_name,
                suffix="draw_reload",
                card_names=("运筹帷幄",),
            )
            if choice is not None:
                return choice
        if "桓那兰那" in hand_name_set and VANARANA_SUPPORT_ID not in support_ids and 2 <= round_number <= 3 and dice_count >= 5 and hand_size <= 2 and aura == 0 and not has_izpapa:
            choice = _play_named_for_actor(
                agent,
                context,
                active_name=active_name,
                suffix="bank_dice",
                card_names=("桓那兰那",),
            )
            if choice is not None:
                return choice
        if (
            "凯瑟琳" in hand_name_set
            and KATHERYNE_SUPPORT_ID not in support_ids
            and safe_setup_window
            and (vehicle_equipped or has_izpapa or "丛山锻火驰行" in hand_name_set)
        ):
            choice = _play_named_for_actor(
                agent,
                context,
                active_name=active_name,
                suffix="fast_switch_support",
                card_names=("凯瑟琳",),
            )
            if choice is not None:
                return choice
        if (
            "圣火竞技场" in hand_name_set
            and STADIUM_SUPPORT_ID not in support_ids
            and 4 <= round_number <= 6
            and not stabilize_window
            and not pressure_window
            and has_izpapa
        ):
            choice = _play_named_for_actor(
                agent,
                context,
                active_name=active_name,
                suffix="arena_setup",
                card_names=("圣火竞技场",),
            )
            if choice is not None:
                return choice
        if injured_exists and agent._has_ready_burst(context):
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q",),
                rule_id=_rule_id(active_name, "reset_burst"),
            )
            if choice is not None:
                return choice
        choice = agent._switch_to_named_targets(
            context,
            target_names=("玛薇卡", "希诺宁"),
            rule_id=_rule_id(active_name, "rotate_reaction_setup"),
            require_safe_target=True,
        )
        if choice is not None and (
            aura > 0 or (vehicle_equipped and has_izpapa and (pressure_window or round_number <= 4) and dice_count >= 2)
        ):
            return choice
        choice = agent._switch_to_named_targets(
            context,
            target_names=("希诺宁", "玛薇卡"),
            rule_id=_rule_id(active_name, "rotate_engine_setup"),
            require_safe_target=True,
        )
        if choice is not None and has_izpapa and round_number <= 2 and dice_count >= 3 and switch_engine_ready and not pressure_window:
            return choice
        choice = agent._switch_to_named_targets(
            context,
            target_names=("玛薇卡", "希诺宁"),
            rule_id=_rule_id(active_name, "late_rotate_out"),
            require_safe_target=True,
        )
        if choice is not None and round_number >= 5 and legal_skill_ids <= {11141} and not can_arm_offdeck_now:
            return choice
        choice = agent._use_active_skill_definition_ids(
            context,
            skill_definition_ids=(11141,),
            rule_id=_rule_id(active_name, "late_chip"),
        )
        if choice is not None and round_number >= 5 and legal_skill_ids <= {11141} and (
            opponent_active_health <= 1
            or (materially_behind and aura > 0 and dice_count >= 3)
        ):
            return choice
        if "最好的伙伴！" in hand_name_set and dice_count <= 2 and hand_size <= 4 and (has_izpapa or aura > 0 or vehicle_equipped):
            choice = _play_named_for_actor(
                agent,
                context,
                active_name=active_name,
                suffix="fix_dice",
                card_names=("最好的伙伴！",),
            )
            if choice is not None:
                return choice
        if round_number >= 5 and legal_skill_ids <= {11141} and not can_arm_offdeck_now and dice_count == 0:
            return _declare_end(agent, context, _rule_id(active_name, "late_hold"))
        if dice_count <= 1:
            return _declare_end(agent, context, _rule_id(active_name, "hold_end_low_dice"))
        return None

    if active_name == "希诺宁":
        choice = agent._switch_to_named_targets(
            context,
            target_names=("玛薇卡", "茜特菈莉"),
            rule_id=_rule_id(active_name, "rotate_mainline"),
            require_safe_target=True,
        )
        if choice is not None and round_number >= 3 and not aura and not injured_exists and not materially_behind and (
            vehicle_equipped or has_izpapa
        ):
            return choice
        choice = agent._switch_to_named_targets(
            context,
            target_names=("玛薇卡", "茜特菈莉"),
            rule_id=_rule_id(active_name, "rotate_setup_front"),
            require_safe_target=True,
        )
        if choice is not None and round_number <= 2 and aura == 0 and not xilonen_fast_switch_ready and not xilonen_jump_ready:
            return choice
        if (
            round_number <= 2
            and dice_count >= 4
            and (KATHERYNE_SUPPORT_ID in support_ids or "凯瑟琳" in hand_name_set)
            and (aura > 0 or alive_count <= 2)
        ):
            choice = _play_named_for_actor(
                agent,
                context,
                active_name=active_name,
                suffix="play_talent",
                card_names=("丛山锻火驰行",),
            )
            if choice is not None:
                return choice
        if injured_exists or aura > 0:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(16113,),
                rule_id=_rule_id(active_name, "support_window"),
            )
            if choice is not None and xilonen_support_window:
                return choice
        choice = agent._use_active_skill_definition_ids(
            context,
            skill_definition_ids=(16112,),
            rule_id=_rule_id(active_name, "setup_window"),
        )
        if choice is not None and xilonen_setup_window:
            return choice
        if (
            "圣火竞技场" in hand_name_set
            and STADIUM_SUPPORT_ID not in support_ids
            and 3 <= round_number <= 5
            and not pressure_window
            and not stabilize_window
            and not materially_behind
            and (xilonen_jump_ready or "丛山锻火驰行" in hand_name_set or vehicle_equipped)
        ):
            choice = _play_named_for_actor(
                agent,
                context,
                active_name=active_name,
                suffix="arena_setup",
                card_names=("圣火竞技场",),
            )
            if choice is not None:
                return choice
        if xilonen_jump_ready and "燃素充盈" in hand_name_set and hand_size <= 2 and round_number >= 3:
            choice = _play_named_for_actor(
                agent,
                context,
                active_name=active_name,
                suffix="fuel_refill",
                card_names=("燃素充盈",),
            )
            if choice is not None:
                return choice
        if xilonen_jump_ready and "诸圣的礼冠" in hand_name_set and not xilonen_has_crown and round_number >= 4 and (pressure_window or alive_count <= 2):
            choice = _play_named_for_actor(
                agent,
                context,
                active_name=active_name,
                suffix="arm_nightsoul_damage",
                card_names=("诸圣的礼冠",),
            )
            if choice is not None:
                return choice
        if xilonen_jump_ready and "龙龙饼干" in hand_name_set and hand_size <= 2 and round_number >= 4:
            choice = _play_named_for_actor(
                agent,
                context,
                active_name=active_name,
                suffix="discount_special",
                card_names=("龙龙饼干",),
            )
            if choice is not None:
                return choice
        choice = agent._use_active_skill_definition_ids(
            context,
            skill_definition_ids=(1161121,),
            rule_id=_rule_id(active_name, "reload_refill"),
        )
        if choice is not None and xilonen_reload_window:
            return choice
        if can_restock_with_legend and hand_size <= 2 and not stabilize_window and not materially_behind:
            choice = _play_named_for_actor(
                agent,
                context,
                active_name=active_name,
                suffix="legend_restock",
                card_names=("万家灶火",),
            )
            if choice is not None:
                return choice
        if "运筹帷幄" in hand_name_set and dice_count >= 1 and hand_size <= 2:
            choice = _play_named_for_actor(
                agent,
                context,
                active_name=active_name,
                suffix="draw_reload",
                card_names=("运筹帷幄",),
            )
            if choice is not None and not materially_behind:
                return choice
        if discovered_vehicle_in_hand and (active_is_hurt or round_number >= 4 or alive_count <= 2):
            choice = _play_discovered_vehicle(
                agent,
                context,
                active_name=active_name,
                round_number=round_number,
                aura=aura,
                has_izpapa=has_izpapa,
                hand_size=hand_size,
                alive_count=alive_count,
                active_is_hurt=active_is_hurt,
            )
            if choice is not None:
                return choice
        if (
            "桓那兰那" in hand_name_set
            and VANARANA_SUPPORT_ID not in support_ids
            and round_number <= 3
            and dice_count >= 5
            and hand_size <= 2
            and aura == 0
            and not pressure_window
            and not xilonen_jump_ready
            and not materially_behind
        ):
            choice = _play_named_for_actor(
                agent,
                context,
                active_name=active_name,
                suffix="bank_dice",
                card_names=("桓那兰那",),
            )
            if choice is not None:
                return choice
        if (
            "凯瑟琳" in hand_name_set
            and KATHERYNE_SUPPORT_ID not in support_ids
            and safe_setup_window
            and not materially_behind
            and (vehicle_equipped or has_izpapa or "丛山锻火驰行" in hand_name_set)
        ):
            choice = _play_named_for_actor(
                agent,
                context,
                active_name=active_name,
                suffix="fast_switch_support",
                card_names=("凯瑟琳",),
            )
            if choice is not None:
                return choice
        choice = agent._switch_to_named_targets(
            context,
            target_names=("玛薇卡",),
            rule_id=_rule_id(active_name, "rotate_setup"),
            require_safe_target=True,
        )
        if choice is not None and vehicle_equipped and (pressure_window or round_number >= 5) and dice_count >= 2 and hand_size <= 4:
            return choice
        choice = agent._use_active_skill_definition_ids(
            context,
            skill_definition_ids=(16111,),
            rule_id=_rule_id(active_name, "chip_finish"),
        )
        if choice is not None and xilonen_chip_finish_window:
            return choice
        choice = agent._use_active_skill_definition_ids(
            context,
            skill_definition_ids=(16111,),
            rule_id=_rule_id(active_name, "chip_pressure"),
        )
        if choice is not None and xilonen_chip_pressure_window:
            return choice
        choice = agent._use_active_skill_definition_ids(
            context,
            skill_definition_ids=(16111,),
            rule_id=_rule_id(active_name, "late_chip"),
        )
        if choice is not None and legal_skill_ids <= {16111} and (
            dice_count >= 3 or materially_behind or opponent_active_health <= 6 or aura > 0
        ):
            return choice
        if (
            round_number <= 5
            and legal_skill_ids <= {16111}
            and opponent_active_health > 4
            and not vehicle_equipped
            and dice_count == 0
        ):
            return _declare_end(agent, context, _rule_id(active_name, "hold_chip_window"))
        if (
            round_number >= 3
            and 16112 in legal_skill_ids
            and legal_skill_ids <= {16111, 16112, 1161121}
            and opponent_active_health > 4
            and not pressure_window
            and dice_count == 0
        ):
            return _declare_end(agent, context, _rule_id(active_name, "hold_chip_window"))
        if "最好的伙伴！" in hand_name_set and dice_count <= 2 and hand_size <= 4 and (xilonen_jump_ready or opponent_active_health <= 4):
            choice = _play_named_for_actor(
                agent,
                context,
                active_name=active_name,
                suffix="fix_dice",
                card_names=("最好的伙伴！",),
            )
            if choice is not None:
                return choice
        if dice_count <= 1:
            return _declare_end(agent, context, _rule_id(active_name, "hold_end_low_dice"))
        return None

    return None


def card_context_bonus(agent, card, context: DecisionContext, mode: str) -> int:
    if card is None:
        return 0
    player = agent._player(context)
    name = str(card.name)
    round_number = agent._round_number(context)
    active_name = agent._active_character_name(context)
    aura = agent._opponent_active_aura(context)
    hand_names = set(agent._hand_card_names(context))
    hand_size = agent._hand_size(context)
    dice_count = len(player.dice)
    injured_exists = any(
        int(character.health) < int(character.max_health)
        for character in player.characters
        if not character.defeated
    )
    opponent_active_health = _opponent_active_health(agent, context)
    support_ids = _support_ids(agent, context)
    combat_status_ids = _combat_status_ids(agent, context)
    legal_skill_ids = _legal_skill_ids(agent, context)
    active_character = agent._active_character(context)
    active_health = int(getattr(active_character, "health", 0))
    active_max_health = max(1, int(getattr(active_character, "max_health", 0) or 1))
    alive_count = _alive_count(agent, context)
    opponent_alive_count = _opponent_alive_count(agent, context)
    total_health = _total_alive_health(agent, context)
    opponent_total_health = _total_alive_health(agent, context, opponent=True)
    hp_margin = total_health - opponent_total_health
    low_hp_threshold = int(getattr(agent.profile.rule_config, "switch_hp_threshold", 4))
    has_talent_in_hand = bool({"「人之名」解放", "丛山锻火驰行"} & hand_names)
    has_vehicle_in_hand = any(item.startswith("驰轮车·") for item in hand_names)
    has_izpapa = IZPAPA_STATUS_ID in combat_status_ids
    vehicle_equipped = _mavuika_vehicle_equipped(agent, context)
    vehicle_name = _mavuika_vehicle_name(agent, context)
    nightsoul_payoff_ready = bool(legal_skill_ids & NIGHTSOUL_PAYOFF_SKILL_IDS)
    discovered_vehicle_in_hand = bool(set(DISCOVER_VEHICLE_NAMES) & hand_names)
    materially_behind = _materially_behind(
        hp_margin=hp_margin,
        self_alive_count=alive_count,
        opponent_alive_count=opponent_alive_count,
        active_health=active_health,
        opponent_active_health=opponent_active_health,
        low_hp_threshold=low_hp_threshold,
    )
    if name == "万家灶火":
        if bool(player.legend_used):
            return -8
        if materially_behind and round_number >= 3:
            return -6
        if round_number >= 4 and (aura > 0 or active_health <= low_hp_threshold or alive_count <= 2):
            return -4
        if round_number == 2 and hand_size <= 1 and not materially_behind:
            return 3
        if active_name == "玛薇卡" and not has_vehicle_in_hand and "「人之名」解放" not in hand_names:
            return 1
        return -4
    if name == "困困冥想术":
        if active_name == "希诺宁" and round_number >= 2:
            return -2
        if round_number >= 4 or active_health <= low_hp_threshold or alive_count <= 2:
            return -5
        if round_number <= 4 and not has_vehicle_in_hand and not discovered_vehicle_in_hand:
            return 5
        return 1 if round_number <= 3 else -2
    if name == "凯瑟琳":
        if KATHERYNE_SUPPORT_ID in support_ids:
            return -6
        if materially_behind or round_number >= 4:
            return -6
        return 5 if round_number <= 2 and (vehicle_equipped or has_izpapa or has_talent_in_hand) else 1
    if name == "最好的伙伴！":
        if active_name == "希诺宁" and round_number <= 2 and aura == 0:
            return -1
        return 4 if dice_count <= 2 or (round_number <= 2 and active_name != "希诺宁") else -3
    if name == "运筹帷幄":
        if materially_behind and round_number >= 4:
            return -4
        return 5 if hand_size <= 2 or round_number >= 5 else -1
    if name == "桓那兰那":
        return 3 if not materially_behind and VANARANA_SUPPORT_ID not in support_ids and round_number <= 2 and dice_count >= 5 and hand_size == 0 else -5
    if name == "圣火竞技场":
        if STADIUM_SUPPORT_ID in support_ids:
            return -6
        if materially_behind:
            return -7
        if round_number >= 5 and (aura > 0 or active_health <= low_hp_threshold or alive_count <= 2):
            return -6
        if 3 <= round_number <= 5 and dice_count >= 2 and injured_exists and active_name == "希诺宁" and (
            vehicle_equipped or has_izpapa or legal_skill_ids & NIGHTSOUL_PAYOFF_SKILL_IDS
        ):
            return 4
        return -5
    if name == "绒翼龙":
        return 6 if round_number <= 4 else 2
    if name == "浪船":
        return 6 if round_number <= 4 or active_name in {"茜特菈莉", "希诺宁"} else 3
    if name == "鳍游龙":
        return 6 if has_izpapa else 1
    if name == "嵴锋龙":
        return 5 if hand_size <= 3 or set(DISCOVER_VEHICLE_NAMES) & hand_names else 2
    if name == "暝视龙":
        return 5 if active_name == "希诺宁" or round_number >= 4 else 2
    if name == "匿叶龙":
        return 4 if round_number >= 4 else 1
    if name == "异色猎刀鳐":
        return 4 if round_number >= 4 or aura > 0 else 2
    if name == "突角龙":
        return 2 if active_name == "希诺宁" and round_number >= 5 else 0
    if name == "呀！呀！":
        return 5 if round_number >= 5 and hand_size <= 4 else -2
    if name == "诸圣的礼冠":
        if active_name == "茜特菈莉":
            if has_izpapa and 3 <= round_number <= 5 and (aura > 0 or discovered_vehicle_in_hand):
                return 4
            return -3
        if materially_behind and opponent_active_health > 4 and not nightsoul_payoff_ready:
            return -3
        if nightsoul_payoff_ready:
            return 5
        return 3 if active_name in {"玛薇卡", "希诺宁"} and round_number <= 4 else 0
    if name == "祭星者之望":
        if active_name == "茜特菈莉" and discovered_vehicle_in_hand:
            return 6
        if active_name == "茜特菈莉" and "困困冥想术" in hand_names and round_number <= 2:
            return 3
        return -6
    if name == "燃素充盈":
        if active_name == "希诺宁" and 1161121 in legal_skill_ids and hand_size <= 1 and not materially_behind:
            return 4
        if active_name == "玛薇卡" and (1131541 in legal_skill_ids or 1131561 in legal_skill_ids) and not materially_behind:
            return 5
        return -3
    if name == "龙龙饼干":
        if materially_behind and opponent_active_health > 4:
            return -2
        if legal_skill_ids & {1131541, 1131561, 1161121}:
            return 4
        return -1
    if name == "龙伙伴的聚餐":
        if not vehicle_equipped:
            return -6
        if vehicle_name == "驰轮车·疾驰" and materially_behind:
            return -5
        return 5
    if name == "小嵴锋龙！发现宝藏！":
        return -8
    if name == "驰轮车·涉渡":
        if has_izpapa and (round_number >= 4 or "凯瑟琳" in hand_names or active_health <= low_hp_threshold or opponent_active_health <= 4):
            return 5
        return -4 if round_number <= 2 else -2
    if name == "驰轮车·跃升":
        return 6 if aura > 0 or round_number >= 4 or opponent_active_health <= 6 else -2
    if name == "驰轮车·疾驰":
        if materially_behind and round_number >= 3:
            return -7
        if round_number <= 2 and not has_izpapa:
            return 5
        return 4 if hand_size <= 1 or (round_number >= 5 and hand_size <= 3 and not materially_behind) else -3
    if name == "丛山锻火驰行":
        if active_name == "希诺宁" and not materially_behind and round_number <= 2 and (
            (KATHERYNE_SUPPORT_ID in support_ids or "凯瑟琳" in hand_names)
            and (vehicle_equipped or has_izpapa)
        ):
            return 5
        return -7 if round_number >= 3 or active_health <= low_hp_threshold or materially_behind else -2
    if name == "「人之名」解放":
        if active_name == "玛薇卡" and not materially_behind and round_number <= 2 and not has_vehicle_in_hand and "万家灶火" not in hand_names:
            return 5
        if active_name != "玛薇卡":
            return -10 if round_number >= 3 else -6
        if discovered_vehicle_in_hand or has_vehicle_in_hand or vehicle_equipped:
            return -10
        return -8 if materially_behind or round_number >= 3 else -4
    if name == "白曜护盾" and WHITE_SHIELD_STATUS_ID in combat_status_ids:
        return 0
    if active_name == "茜特菈莉" and has_izpapa and name in {"桓那兰那", "圣火竞技场"}:
        return 1
    return 0
