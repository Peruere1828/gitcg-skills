# Ported from Rebel_base_RL research/world_model (AGPL-3.0, github.com/.../Rebel_base_RL) by genshin-GITCG.
# Keep upstream semantics; adapt imports only. See NOTICE.md.
from __future__ import annotations

from reps.schema import DecisionContext

from .common import choose_elemental_focus_reroll, make_rule_config
from ..models import RuleChoice

RULE_CONFIG = make_rule_config(
    slug="klee_mavuika_mualani",
    opener="玛拉妮",
    carry=("玛拉妮", "玛薇卡", "可莉"),
    bench=("玛薇卡", "可莉"),
    preferred_elements=("GCG_TAG_ELEMENT_HYDRO", "GCG_TAG_ELEMENT_PYRO"),
    style="vaporize",
)


def choose_active(agent, context: DecisionContext):
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)
    if aura > 0:
        preferred = ("玛拉妮", "玛薇卡", "可莉")
    elif round_number <= 2:
        preferred = ("玛拉妮", "玛薇卡", "可莉")
    else:
        preferred = ("玛薇卡", "玛拉妮", "可莉")
    return agent._choose_active_by_names(context, preferred, require_safe_target=True)


def choose_reroll(agent, context: DecisionContext):
    active_name = agent._active_character_name(context)
    if active_name == "玛拉妮":
        return choose_elemental_focus_reroll(
            agent,
            context,
            primary=("GCG_TAG_ELEMENT_HYDRO",),
            secondary=("GCG_TAG_ELEMENT_PYRO",),
            rule_id="reroll.klee_mavuika_curve",
        )
    return choose_elemental_focus_reroll(
        agent,
        context,
        primary=("GCG_TAG_ELEMENT_PYRO",),
        secondary=("GCG_TAG_ELEMENT_HYDRO",),
        rule_id="reroll.klee_mavuika_curve",
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
    hand_size = agent._hand_size(context)
    priorities: list[str] = []
    if round_number <= 3:
        priorities.append("驰轮车·涉渡")
    if hand_size <= 3 or round_number >= 4:
        priorities.append("驰轮车·疾驰")
    if aura > 0 and round_number <= 4:
        priorities.append("驰轮车·跃升")
    priorities.extend(("驰轮车·涉渡", "驰轮车·疾驰", "驰轮车·跃升"))
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
    hand_names = set(agent._hand_card_names(context))
    hand_size = agent._hand_size(context)
    has_vehicle_in_hand = any(name.startswith("驰轮车·") for name in hand_names)

    if round_number <= 2:
        choice = agent._play_named_card(
            context,
            card_names=("元素幻变：蒸发祝佑", "火与战争", "阿伽娅"),
            rule_id="action.klee_mavuika_mualani_build_engine",
            min_tactical_score=1,
        )
        if choice is not None:
            return choice
    if active_name == "玛拉妮" and summon_count > 0:
        choice = agent._play_named_card(
            context,
            card_names=("夜域赐礼·波涛顶底",),
            rule_id="action.klee_mavuika_mualani_play_mualani_talent",
        )
        if choice is not None:
            return choice
    if active_name == "玛薇卡":
        choice = agent._play_named_card(
            context,
            card_names=("「人之名」解放",),
            rule_id="action.klee_mavuika_mualani_play_mavuika_talent",
        )
        if choice is not None:
            return choice

    if active_name == "玛薇卡":
        if aura <= 0:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(1131551,),
                rule_id="action.klee_mavuika_mualani_vehicle_rotate",
            )
            if choice is not None and round_number <= 4:
                return choice
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(1131541,),
                rule_id="action.klee_mavuika_mualani_apply_pyro",
            )
            if choice is not None and round_number >= 4:
                return choice
            if not has_vehicle_in_hand and (round_number <= 3 or hand_size <= 3):
                choice = agent._use_active_skill_definition_ids(
                    context,
                    skill_definition_ids=(13152,),
                    rule_id="action.klee_mavuika_mualani_prepare_vehicle",
                )
                if choice is not None:
                    return choice
            if round_number >= 4:
                choice = agent._use_active_skill_types(
                    context,
                    skill_types=("GCG_SKILL_TAG_A",),
                    rule_id="action.klee_mavuika_mualani_build_spirit",
                )
                if choice is not None:
                    return choice
            if round_number <= 3:
                return agent._switch_to_named_targets(
                    context,
                    target_names=("可莉",),
                    rule_id="action.klee_mavuika_mualani_reset_klee",
                    require_safe_target=True,
                )
            return agent._switch_to_named_targets(
                context,
                target_names=("可莉", "玛拉妮"),
                rule_id="action.klee_mavuika_mualani_reset_klee",
                require_safe_target=True,
            )
        if not has_vehicle_in_hand and (round_number <= 4 or hand_size <= 3):
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(13152,),
                rule_id="action.klee_mavuika_mualani_prepare_vehicle",
            )
            if choice is not None:
                return choice
        if round_number >= 4:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_A",),
                rule_id="action.klee_mavuika_mualani_build_spirit",
            )
            if choice is not None:
                return choice
            return None
        return agent._switch_to_named_targets(
            context,
            target_names=("玛拉妮",),
            rule_id="action.klee_mavuika_mualani_switch_finisher_setup",
            require_safe_target=True,
        )

    if active_name == "可莉":
        if aura > 0 and (round_number >= 4 or hand_size <= 2):
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_E", "GCG_SKILL_TAG_A"),
                rule_id="action.klee_mavuika_mualani_klee_pressure",
            )
            if choice is not None:
                return choice
        if aura <= 0:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_E", "GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A"),
                rule_id="action.klee_mavuika_mualani_apply_pyro",
            )
            if choice is not None:
                return choice
        if round_number <= 3:
            return agent._switch_to_named_targets(
                context,
                target_names=("玛拉妮",),
                rule_id="action.klee_mavuika_mualani_switch_finisher_setup",
                require_safe_target=True,
            )
        return None

    if active_name == "玛拉妮":
        if aura > 0:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A", "GCG_SKILL_TAG_E"),
                rule_id="action.klee_mavuika_mualani_finish_window",
            )
            if choice is not None:
                return choice
        choice = agent._use_active_skill_definition_ids(
            context,
            skill_definition_ids=(1121422,),
            rule_id="action.klee_mavuika_mualani_stack_rotate",
        )
        if choice is not None and round_number <= 3:
            return choice
        if round_number <= 2:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_E",),
                rule_id="action.klee_mavuika_mualani_setup_hydro",
            )
            if choice is not None:
                return choice
        if round_number >= 4 or hand_size <= 2:
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_A", "GCG_SKILL_TAG_E"),
                rule_id="action.klee_mavuika_mualani_rebuild_hydro",
            )
            if choice is not None:
                return choice
            return None
        return agent._switch_to_named_targets(
            context,
            target_names=("玛薇卡",),
            rule_id="action.klee_mavuika_mualani_reset_mavuika",
            require_safe_target=True,
        )
    return None


def card_context_bonus(agent, card, context: DecisionContext, mode: str) -> int:
    if card is None:
        return 0
    name = str(card.name)
    round_number = agent._round_number(context)
    active_name = agent._active_character_name(context)
    summon_count = len(agent._player(context).summons)
    aura = agent._opponent_active_aura(context)
    hand_size = agent._hand_size(context)
    if name == "祭星者之望":
        if active_name == "可莉" and round_number <= 3:
            return 3
        return -6
    if name == "很棒，哥们。":
        return 2 if active_name == "玛薇卡" and round_number <= 2 else -5
    if name == "元素共鸣：交织之火":
        return 2 if active_name in {"玛薇卡", "可莉"} and round_number <= 2 else -3
    if name == "夜域赐礼·波涛顶底":
        return 4 if active_name == "玛拉妮" and summon_count > 0 else -4
    if name == "阿伽娅":
        return 4 if round_number <= 3 else 1
    if name == "火与战争":
        return 3 if round_number <= 2 and active_name in {"玛薇卡", "可莉"} else 1 if aura > 0 else -1
    if name == "驰轮车·涉渡":
        return 5 if round_number <= 3 else 1
    if name == "驰轮车·跃升":
        return 5 if aura > 0 or round_number >= 4 else -4
    if name == "驰轮车·疾驰":
        return 3 if hand_size <= 2 and round_number >= 5 else -5
    return 0
