# Ported from Rebel_base_RL research/world_model (AGPL-3.0, github.com/.../Rebel_base_RL) by genshin-GITCG.
# Keep upstream semantics; adapt imports only. See NOTICE.md.
from __future__ import annotations

from reps.schema import DecisionContext

from .common import choose_elemental_focus_reroll, make_rule_config
from ..models import RuleChoice

RULE_CONFIG = make_rule_config(
    slug="dual_mualani_stacks",
    opener="玛拉妮",
    carry=("玛拉妮", "玛薇卡", "深渊使徒·激流"),
    bench=("玛薇卡", "深渊使徒·激流"),
    preferred_elements=("GCG_TAG_ELEMENT_HYDRO", "GCG_TAG_ELEMENT_PYRO"),
    style="stack_burst",
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
    mualani_nightsoul = agent._character_nightsoul(context, character_name="玛拉妮")
    mavuika_nightsoul = agent._character_nightsoul(context, character_name="玛薇卡")
    if aura > 0 and mualani_nightsoul > 0:
        preferred = ("玛拉妮", "深渊使徒·激流", "玛薇卡")
    elif aura > 0:
        preferred = ("玛拉妮", "玛薇卡", "深渊使徒·激流")
    elif mavuika_nightsoul > 0 and round_number <= 3:
        preferred = ("玛薇卡", "玛拉妮", "深渊使徒·激流")
    elif round_number <= 2:
        preferred = ("玛拉妮", "玛薇卡", "深渊使徒·激流")
    else:
        preferred = ("玛拉妮", "深渊使徒·激流", "玛薇卡")
    return agent._choose_active_by_names(context, preferred, require_safe_target=True)


def choose_reroll(agent, context: DecisionContext):
    active_name = agent._active_character_name(context)
    if active_name == "玛薇卡":
        return choose_elemental_focus_reroll(
            agent,
            context,
            primary=("GCG_TAG_ELEMENT_PYRO",),
            secondary=("GCG_TAG_ELEMENT_HYDRO",),
            rule_id="reroll.dual_mualani_curve",
        )
    return choose_elemental_focus_reroll(
        agent,
        context,
        primary=("GCG_TAG_ELEMENT_HYDRO",),
        secondary=("GCG_TAG_ELEMENT_PYRO",),
        rule_id="reroll.dual_mualani_curve",
    )


def choose_select_card(agent, context: DecisionContext):
    candidates: dict[str, int] = {}
    for action_code, spec in agent._pairs(context):
        card = agent.assets.cards.get(int(spec.subject_definition_id))
        if card is not None:
            candidates[str(card.name)] = int(action_code)
    if not candidates or set(candidates) - {"驰轮车·涉渡", "驰轮车·跃升", "驰轮车·疾驰"}:
        return None
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)
    summon_count = len(agent._player(context).summons)
    hand_size = agent._hand_size(context)
    vehicle_equipped = _mavuika_vehicle_equipped(agent, context)
    opponent_hp = agent._opponent_active_health(context)
    mavuika_nightsoul = agent._character_nightsoul(context, character_name="玛薇卡")
    priorities: list[str] = []
    if vehicle_equipped and mavuika_nightsoul > 0 and summon_count > 0 and round_number <= 4:
        priorities.append("驰轮车·涉渡")
    if aura > 0 and mavuika_nightsoul > 0 and opponent_hp <= 6:
        priorities.append("驰轮车·跃升")
    if mavuika_nightsoul > 0 and hand_size <= 2 and round_number >= 4:
        priorities.append("驰轮车·疾驰")
    priorities.extend(("驰轮车·涉渡", "驰轮车·跃升", "驰轮车·疾驰"))
    for name in priorities:
        action_code = candidates.get(name)
        if action_code is not None:
            return RuleChoice(action_code=action_code, rule_id="select_card.mavuika_vehicle")
    return None


def choose_action(agent, context: DecisionContext):
    active_name = agent._active_character_name(context)
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)
    summon_count = len(agent._player(context).summons)
    remaining_dice = len(agent._player(context).dice)
    hand_names = set(agent._hand_card_names(context))
    hand_size = agent._hand_size(context)
    has_vehicle_in_hand = any(name.startswith("驰轮车·") for name in hand_names)
    vehicle_equipped = _mavuika_vehicle_equipped(agent, context)
    opponent_hp = agent._opponent_active_health(context)
    mualani_nightsoul = agent._character_nightsoul(context, character_name="玛拉妮")
    mavuika_nightsoul = agent._character_nightsoul(context, character_name="玛薇卡")

    if round_number <= 2:
        choice = agent._play_named_card(
            context,
            card_names=("元素幻变：蒸发祝佑", "阿伽娅", "交给我吧！", "湖中垂柳", "「悬木人」"),
            rule_id="action.dual_mualani_build_engine",
            min_tactical_score=1,
        )
        if choice is not None:
            return choice
    if active_name in {"玛拉妮", "深渊使徒·激流"}:
        choice = agent._play_named_card(
            context,
            card_names=("元素共鸣：交织之水", "交给我吧！"),
            rule_id="action.dual_mualani_resource_setup",
            min_tactical_score=1,
        )
        if choice is not None and round_number <= 4:
            return choice
    if summon_count > 0 or vehicle_equipped:
        choice = agent._play_named_card(
            context,
            card_names=("龙伙伴的聚餐",),
            rule_id="action.dual_mualani_extend_technique",
            min_tactical_score=1,
        )
        if choice is not None:
            return choice
    if active_name == "玛拉妮" and summon_count > 0:
        choice = agent._play_named_card(
            context,
            card_names=("夜域赐礼·波涛顶底",),
            rule_id="action.dual_mualani_play_mualani_talent",
        )
        if choice is not None:
            return choice
    if active_name == "深渊使徒·激流":
        choice = agent._play_named_card(
            context,
            card_names=("暗流涌动",),
            rule_id="action.dual_mualani_play_abyss_talent",
        )
        if choice is not None:
            return choice

    if active_name == "玛薇卡":
        if not vehicle_equipped and not has_vehicle_in_hand and mavuika_nightsoul <= 0 and round_number <= 2:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(13152,),
                rule_id="action.dual_mualani_prepare_vehicle",
            )
            if choice is not None:
                return choice
        if mavuika_nightsoul > 0:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(1131551,),
                rule_id="action.dual_mualani_vehicle_rotate",
            )
            if choice is not None and (summon_count > 0 or aura > 0 or round_number <= 3):
                return choice
        if aura > 0:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(1131541,),
                rule_id="action.dual_mualani_apply_pyro",
            )
            if choice is not None and mavuika_nightsoul > 0 and (opponent_hp <= 6 or round_number >= 4):
                return choice
            if remaining_dice >= 3 or opponent_hp <= 4:
                return agent._switch_to_named_targets(
                    context,
                    target_names=("玛拉妮",),
                    rule_id="action.dual_mualani_switch_finish_window",
                    require_safe_target=True,
                )
            return None
        choice = agent._use_active_skill_definition_ids(
            context,
            skill_definition_ids=(1131561,),
            rule_id="action.dual_mualani_vehicle_draw",
        )
        if choice is not None and mavuika_nightsoul > 0 and vehicle_equipped and hand_size <= 2 and round_number >= 5:
            return choice
        if remaining_dice <= 1:
            return None
        return agent._switch_to_named_targets(
            context,
            target_names=("玛拉妮", "深渊使徒·激流") if summon_count <= 0 else ("玛拉妮", "深渊使徒·激流"),
            rule_id="action.dual_mualani_reset_chain",
            require_safe_target=True,
        )
    if active_name == "玛拉妮":
        if aura > 0:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q",),
                rule_id="action.dual_mualani_mualani_finish_window",
            )
            if choice is not None:
                return choice
            if opponent_hp <= 4:
                choice = agent._use_active_skill_types(
                    context,
                    skill_types=("GCG_SKILL_TAG_A",),
                    rule_id="action.dual_mualani_mualani_finish_window",
                )
                if choice is not None:
                    return choice
        if mualani_nightsoul > 0:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(1121422,),
                rule_id="action.dual_mualani_stack_rotate",
            )
            if choice is not None and remaining_dice >= 1 and (
                aura <= 0
                or (round_number <= 2 and summon_count <= 0 and opponent_hp > 4)
            ):
                return choice
        if summon_count <= 0 and round_number >= 3:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(12143,),
                rule_id="action.dual_mualani_arm_summon",
            )
            if choice is not None:
                return choice
        if mualani_nightsoul <= 0 and round_number <= 2:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_E",),
                rule_id="action.dual_mualani_setup_hydro_window",
            )
            if choice is not None:
                return choice
        if mualani_nightsoul <= 0 and round_number >= 3 and summon_count <= 0:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(12142,),
                rule_id="action.dual_mualani_setup_hydro_window",
            )
            if choice is not None:
                return choice
        if aura > 0 and (summon_count > 0 or mualani_nightsoul > 0):
            return None
        if remaining_dice <= 1 and summon_count > 0:
            return None
        return agent._switch_to_named_targets(
            context,
            target_names=("玛薇卡", "深渊使徒·激流") if summon_count <= 0 else ("深渊使徒·激流", "玛薇卡"),
            rule_id="action.dual_mualani_reset_chain",
            require_safe_target=True,
        )
    if active_name == "深渊使徒·激流":
        if aura <= 0 and (round_number <= 3 or summon_count <= 0):
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_E", "GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A"),
                rule_id="action.dual_mualani_hydro_setup",
            )
            if choice is not None:
                return choice
        if aura > 0 or summon_count > 0 or opponent_hp <= 6 or mualani_nightsoul > 0:
            if remaining_dice >= 3 or opponent_hp <= 4:
                return agent._switch_to_named_targets(
                    context,
                    target_names=("玛拉妮",),
                    rule_id="action.dual_mualani_handoff_to_finisher",
                    require_safe_target=True,
                )
            return None
        if remaining_dice <= 1:
            return None
        return agent._switch_to_named_targets(
            context,
            target_names=("玛薇卡", "玛拉妮"),
            rule_id="action.dual_mualani_reset_chain",
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
    summon_count = len(agent._player(context).summons)
    hand_size = agent._hand_size(context)
    opponent_hp = agent._opponent_active_health(context)
    mavuika_nightsoul = agent._character_nightsoul(context, character_name="玛薇卡")
    low_hp_exists = any(
        int(character.health) <= max(3, int(character.max_health) // 2)
        for character in agent._player(context).characters
        if not character.defeated
    )
    if name == "元素幻变：蒸发祝佑":
        return 4 if round_number <= 2 else 1
    if name == "阿伽娅":
        return 4 if round_number <= 3 else 1
    if name == "湖中垂柳":
        if round_number <= 2 and hand_size <= 3:
            return 4
        return 2 if hand_size <= 2 else -2
    if name == "「悬木人」":
        return 3 if round_number <= 2 else 0
    if name == "运筹帷幄":
        return -5 if round_number <= 4 else -2
    if name == "燃素充盈":
        return 4 if round_number <= 4 and active_name in {"玛薇卡", "玛拉妮"} else -2
    if name == "野猪公主":
        return 3 if round_number <= 4 and active_name in {"玛薇卡", "玛拉妮"} else -1
    if name == "元素共鸣：交织之水":
        return 2 if round_number <= 2 and active_name in {"玛拉妮", "深渊使徒·激流"} else -4
    if name == "元素共鸣：愈疗之水":
        return 3 if low_hp_exists and round_number >= 4 else -8
    if name == "交给我吧！":
        return 2 if round_number <= 2 and active_name == "深渊使徒·激流" else -5 if active_name == "玛薇卡" else -3
    if name == "驰轮车·涉渡":
        return 4 if mavuika_nightsoul > 0 and summon_count > 0 and round_number <= 4 else -6
    if name == "驰轮车·跃升":
        return 5 if mavuika_nightsoul > 0 and aura > 0 and opponent_hp <= 6 else -7
    if name == "驰轮车·疾驰":
        if mavuika_nightsoul > 0 and hand_size <= 2 and round_number >= 4:
            return 3
        return -6
    if name == "夜域赐礼·波涛顶底":
        return 4 if active_name == "玛拉妮" and summon_count > 0 else -4
    if name == "运筹帷幄":
        return -7
    if name in {"困困冥想术", "奇瑰之汤", "莲花酥", "龙龙饼干"}:
        return -5 if active_name == "玛薇卡" else -2
    return 0
