# Ported from Rebel_base_RL research/world_model (AGPL-3.0, github.com/.../Rebel_base_RL) by genshin-GITCG.
# Keep upstream semantics; adapt imports only. See NOTICE.md.
from __future__ import annotations

from reps.schema import DecisionContext, OptionKind

from .common import make_rule_config
from ..models import RuleChoice

RULE_CONFIG = make_rule_config(
    slug="mualani_double_pyro",
    opener="玛薇卡",
    carry=("玛拉妮", "玛薇卡", "烟绯"),
    bench=("玛薇卡", "烟绯"),
    preferred_elements=("GCG_TAG_ELEMENT_HYDRO", "GCG_TAG_ELEMENT_PYRO"),
    style="vaporize",
)

VEHICLE_CARD_IDS = {113154, 113155, 113156}


def _mavuika_vehicle_equipped(agent, context: DecisionContext) -> bool:
    for character in agent._player(context).characters:
        if agent._character_name(character.definition_id) != "玛薇卡":
            continue
        return any(
            int(getattr(entity, "definition_id", 0)) in VEHICLE_CARD_IDS
            for entity in getattr(character, "entities", ())
        )
    return False


def choose_active(agent, context: DecisionContext):
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)
    if aura > 0:
        preferred = ("玛拉妮", "烟绯", "玛薇卡")
    elif round_number >= 5:
        preferred = ("玛拉妮", "烟绯", "玛薇卡")
    elif round_number <= 2:
        preferred = ("玛拉妮", "烟绯", "玛薇卡")
    else:
        preferred = ("烟绯", "玛拉妮", "玛薇卡")
    return agent._choose_active_by_names(context, preferred, require_safe_target=True)


def choose_reroll(agent, context: DecisionContext):
    visible_dice = tuple(int(value) for value in context.request_payload.get("visible_dice", ()))
    if not visible_dice:
        return None
    active_name = agent._active_character_name(context)
    if active_name == "玛拉妮":
        primary = {"GCG_TAG_ELEMENT_HYDRO"}
        secondary = {"GCG_TAG_ELEMENT_PYRO"}
    else:
        primary = {"GCG_TAG_ELEMENT_PYRO"}
        secondary = {"GCG_TAG_ELEMENT_HYDRO"}
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
    return RuleChoice(action_code=best_code, rule_id="reroll.mualani_pyro_curve")


def choose_select_card(agent, context: DecisionContext):
    candidates: dict[str, int] = {}
    for action_code, spec in agent._pairs(context):
        card = agent.assets.cards.get(int(spec.subject_definition_id))
        if card is not None:
            candidates[str(card.name)] = int(action_code)
    if not candidates or set(candidates) - {"驰轮车·涉渡", "驰轮车·跃升", "驰轮车·疾驰"}:
        return None
    round_number = agent._round_number(context)
    hand_size = agent._hand_size(context)
    aura = agent._opponent_active_aura(context)
    opponent_hp = agent._opponent_active_health(context)
    priorities: list[str] = []
    if round_number <= 4:
        priorities.append("驰轮车·涉渡")
    if hand_size <= 2 or round_number >= 4:
        priorities.append("驰轮车·疾驰")
    if aura > 0 and opponent_hp <= 6:
        priorities.append("驰轮车·跃升")
    priorities.extend(("驰轮车·涉渡", "驰轮车·疾驰", "驰轮车·跃升"))
    for name in priorities:
        action_code = candidates.get(name)
        if action_code is not None:
            return RuleChoice(action_code=action_code, rule_id="select_card.mavuika_vehicle")
    return None


def choose_action(agent, context: DecisionContext):
    def _declare_end(rule_id: str):
        for action_code, _ in agent._pairs(context, kind=OptionKind.ACTION_DECLARE_END):
            return RuleChoice(action_code=int(action_code), rule_id=rule_id)
        return None

    active_name = agent._active_character_name(context)
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)
    summon_count = len(agent._player(context).summons)
    hand_names = set(agent._hand_card_names(context))
    hand_size = agent._hand_size(context)
    dice_count = len(agent._player(context).dice)
    opponent_hp = agent._opponent_active_health(context)
    has_vehicle_in_hand = any(name.startswith("驰轮车·") for name in hand_names)
    vehicle_equipped = _mavuika_vehicle_equipped(agent, context)

    if round_number <= 2:
        choice = agent._play_named_card(
            context,
            card_names=("元素幻变：蒸发祝佑", "元素共鸣：交织之火", "温妮莎传奇", "交给我吧！", "阿伽娅"),
            rule_id="action.mualani_double_pyro_build_engine",
            min_tactical_score=1,
        )
        if choice is not None:
            return choice
        if hand_size <= 2:
            choice = agent._play_named_card(
                context,
                card_names=("化城郭",),
                rule_id="action.mualani_double_pyro_build_engine",
                min_tactical_score=1,
            )
            if choice is not None:
                return choice
    if active_name == "玛拉妮" and summon_count > 0:
        choice = agent._play_named_card(
            context,
            card_names=("夜域赐礼·波涛顶底",),
            rule_id="action.mualani_double_pyro_play_mualani_talent",
        )
        if choice is not None:
            return choice
    if active_name == "玛薇卡":
        choice = agent._play_named_card(
            context,
            card_names=("「人之名」解放",),
            rule_id="action.mualani_double_pyro_play_mavuika_talent",
        )
        if choice is not None:
            return choice

    if active_name == "玛薇卡":
        if aura <= 0:
            if not vehicle_equipped and not has_vehicle_in_hand and round_number <= 3:
                choice = agent._use_active_skill_definition_ids(
                    context,
                    skill_definition_ids=(13152,),
                    rule_id="action.mualani_double_pyro_prepare_vehicle",
                )
                if choice is not None:
                    return choice
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(1131551, 1131561),
                rule_id="action.mualani_double_pyro_vehicle_rotate",
            )
            if choice is not None and vehicle_equipped and round_number <= 2 and hand_size <= 2:
                return choice
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(1131541,),
                rule_id="action.mualani_double_pyro_apply_pyro",
            )
            if choice is not None and round_number >= 3 and (
                opponent_hp <= 6 or summon_count > 0 or hand_size <= 1
            ):
                return choice
            return agent._switch_to_named_targets(
                context,
                target_names=("烟绯",) if round_number <= 2 else ("烟绯", "玛拉妮"),
                rule_id="action.mualani_double_pyro_reset_pyro",
                require_safe_target=True,
            )
        if round_number <= 4 or summon_count > 0 or opponent_hp <= 7:
            return agent._switch_to_named_targets(
                context,
                target_names=("玛拉妮",),
                rule_id="action.mualani_double_pyro_switch_finisher",
                require_safe_target=True,
            )
        choice = agent._use_active_skill_types(
            context,
            skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A"),
            rule_id="action.mualani_double_pyro_hold_pyro_pressure",
        )
        if choice is not None:
            return choice
        return agent._switch_to_named_targets(
            context,
            target_names=("玛拉妮",),
            rule_id="action.mualani_double_pyro_switch_finisher",
            require_safe_target=True,
        )
    if active_name == "烟绯":
        if aura <= 0:
            skill_types = ("GCG_SKILL_TAG_E", "GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A")
            if round_number >= 4:
                skill_types = ("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_E", "GCG_SKILL_TAG_A")
            choice = agent._use_active_skill_types(
                context,
                skill_types=skill_types,
                rule_id="action.mualani_double_pyro_apply_pyro",
            )
            if choice is not None:
                return choice
            if round_number <= 2 and dice_count <= 1:
                return _declare_end("action.mualani_double_pyro_hold_pyro_window")
            return None
        if round_number <= 4 or summon_count > 0 or opponent_hp <= 7:
            return agent._switch_to_named_targets(
                context,
                target_names=("玛拉妮",),
                rule_id="action.mualani_double_pyro_switch_finisher",
                require_safe_target=True,
            )
        return agent._use_active_skill_types(
            context,
            skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A"),
            rule_id="action.mualani_double_pyro_pyro_pressure",
        )
    if active_name == "玛拉妮":
        if aura > 0:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_E"),
                rule_id="action.mualani_double_pyro_finish_window",
            )
            if choice is not None:
                return choice
            if opponent_hp <= 4:
                choice = agent._use_active_skill_types(
                    context,
                    skill_types=("GCG_SKILL_TAG_A",),
                    rule_id="action.mualani_double_pyro_finish_window",
                )
                if choice is not None:
                    return choice
        choice = agent._use_active_skill_definition_ids(
            context,
            skill_definition_ids=(1121422,),
            rule_id="action.mualani_double_pyro_vehicle_rotate",
        )
        if choice is not None and aura <= 0 and round_number <= 3:
            return choice
        if round_number <= 2:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_E",),
                rule_id="action.mualani_double_pyro_hydro_setup",
            )
            if choice is not None:
                return choice
        return agent._switch_to_named_targets(
            context,
            target_names=("烟绯",) if round_number <= 4 else ("玛薇卡", "烟绯"),
            rule_id="action.mualani_double_pyro_reset_pyro",
            require_safe_target=True,
        )
    return None


def card_context_bonus(agent, card, context: DecisionContext, mode: str) -> int:
    if card is None:
        return 0
    name = str(card.name)
    active_name = agent._active_character_name(context)
    summon_count = len(agent._player(context).summons)
    round_number = agent._round_number(context)
    hand_size = agent._hand_size(context)
    dice_count = len(agent._player(context).dice)
    aura = agent._opponent_active_aura(context)
    opponent_hp = agent._opponent_active_health(context)
    low_hp_exists = any(
        int(character.health) <= max(3, int(character.max_health) // 2)
        for character in agent._player(context).characters
        if not character.defeated
    )
    if name == "夜域赐礼·波涛顶底":
        return 4 if active_name == "玛拉妮" and summon_count > 0 else -4
    if name == "驰轮车·涉渡":
        if active_name != "玛薇卡" and mode in {"any", "event", "fallback"}:
            return -6
        return 5 if round_number <= 4 else 1
    if name == "驰轮车·疾驰":
        return 4 if hand_size <= 2 or round_number >= 4 else -1
    if name == "驰轮车·跃升":
        return 4 if aura > 0 and opponent_hp <= 6 else -5 if round_number >= 4 else -2
    if name == "「悬木人」":
        return -5 if active_name == "烟绯" and round_number >= 3 else 1 if round_number <= 2 else -1
    if name == "元素共鸣：交织之火":
        if mode == "priority" and active_name in {"玛薇卡", "烟绯"} and round_number >= 3:
            return 4
        return 3 if active_name in {"玛薇卡", "烟绯"} and dice_count <= 3 else -3
    if name == "元素共鸣：热诚之火":
        return 4 if agent._opponent_active_aura(context) > 0 and active_name == "烟绯" else -4
    if name == "角斗士的凯旋":
        if mode == "priority" and active_name == "玛薇卡" and round_number >= 3:
            return 5
        return 1 if active_name in {"玛薇卡", "烟绯"} and hand_size <= 2 else -1
    if name == "燃素充盈":
        if mode == "priority" and active_name == "玛薇卡" and 3 <= round_number <= 5:
            return 5
        return 3 if active_name == "玛薇卡" and hand_size <= 2 else -2
    if name == "化城郭":
        if (round_number <= 2 and hand_size <= 3) or dice_count <= 1:
            return 3
        return -4
    if name == "交给我吧！":
        return 3 if round_number <= 4 and active_name in {"玛拉妮", "玛薇卡"} else -1
    if name == "北地烟熏鸡":
        return 3 if active_name == "玛拉妮" and agent._opponent_active_aura(context) > 0 else -3
    if name == "绝云锅巴":
        return 3 if active_name == "玛拉妮" and agent._opponent_active_aura(context) > 0 else -2
    if name == "炸鱼薯条":
        return 3 if dice_count <= 2 else -2
    if name == "温妮莎传奇":
        return 4 if round_number <= 2 and dice_count <= 4 else 0
    if name == "火与战争":
        return 3 if low_hp_exists or round_number >= 4 else -2
    if name == "磐岩盟契":
        return 3 if dice_count <= 1 else -4
    if name == "野猪公主":
        return 2 if round_number <= 4 else 1 if active_name in {"烟绯", "玛薇卡"} and hand_size <= 2 else -1
    if name == "龙龙饼干":
        return 3 if active_name in {"玛薇卡", "玛拉妮"} and round_number <= 4 else 1 if hand_size <= 2 else -1
    return 0
