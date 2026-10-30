# Ported from Rebel_base_RL research/world_model (AGPL-3.0, github.com/.../Rebel_base_RL) by genshin-GITCG.
# Keep upstream semantics; adapt imports only. See NOTICE.md.
from __future__ import annotations

from reps.schema import DecisionContext

from .common import make_rule_config
from ..models import RuleChoice

RULE_CONFIG = make_rule_config(
    slug="dvalin_bonk",
    opener="特瓦林",
    carry=("特瓦林", "早柚", "伊安珊"),
    bench=("早柚", "伊安珊"),
    preferred_elements=("GCG_TAG_ELEMENT_ANEMO", "GCG_TAG_ELEMENT_ELECTRO"),
    style="swirl_otk",
)


def choose_active(agent, context: DecisionContext):
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)
    if aura > 0:
        preferred = ("早柚", "特瓦林", "伊安珊") if round_number <= 3 else ("特瓦林", "早柚", "伊安珊")
    else:
        preferred = ("伊安珊", "早柚", "特瓦林")
    return agent._choose_active_by_names(context, preferred, require_safe_target=True)


def choose_reroll(agent, context: DecisionContext):
    visible_dice = tuple(int(value) for value in context.request_payload.get("visible_dice", ()))
    if not visible_dice:
        return None
    active_name = agent._active_character_name(context)
    round_number = agent._round_number(context)
    if active_name == "伊安珊":
        primary = {"GCG_TAG_ELEMENT_ELECTRO"}
        secondary = {"GCG_TAG_ELEMENT_ANEMO"}
    else:
        primary = {"GCG_TAG_ELEMENT_ANEMO"}
        secondary = {"GCG_TAG_ELEMENT_ELECTRO"} if round_number <= 3 else set()
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
            keep_omni + 2 * keep_primary + keep_secondary,
            keep_omni,
            keep_primary,
            keep_secondary,
            -keep_off,
            reroll_off,
            -reroll_omni,
            -reroll_primary,
            -reroll_secondary,
            -len(rerolled),
        )
        if best_score is None or score > best_score:
            best_code = int(action_code)
            best_score = score
    if best_code is None:
        return None
    return RuleChoice(action_code=best_code, rule_id="reroll.dvalin_swirl_focus")


def choose_action(agent, context: DecisionContext):
    active_name = agent._active_character_name(context)
    round_number = agent._round_number(context)
    aura = agent._opponent_active_aura(context)
    remaining_dice = len(agent._player(context).dice)

    if round_number <= 2:
        choice = agent._play_named_card(
            context,
            card_names=("万家灶火", "风龙废墟", "「沃陆之邦」", "元素共鸣：迅捷之风", "最好的伙伴！", "元素共鸣：交织之风", "化种匣"),
            rule_id="action.dvalin_bonk_build_engine",
            min_tactical_score=1,
        )
        if choice is not None:
            return choice

    if active_name == "伊安珊":
        choice = agent._play_named_card(
            context,
            card_names=("「沃陆之邦」的训教",),
            rule_id="action.dvalin_bonk_play_iansan_talent",
        )
        if choice is not None:
            return choice
        if aura <= 0:
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(14143, 14142),
                rule_id="action.dvalin_bonk_apply_electro_window",
            )
            if choice is not None:
                return choice
        if aura > 0:
            return agent._switch_to_named_targets(
                context,
                target_names=("早柚", "特瓦林") if round_number <= 3 else ("特瓦林", "早柚"),
                rule_id="action.dvalin_bonk_rotate_finish_window",
                require_safe_target=True,
            )
        if round_number <= 2 and remaining_dice >= 2:
            return agent._switch_to_named_targets(
                context,
                target_names=("特瓦林", "早柚"),
                rule_id="action.dvalin_bonk_rotate_raw_pressure",
                require_safe_target=True,
            )
        return None

    if active_name == "早柚":
        choice = agent._play_named_card(
            context,
            card_names=("偷懒的新方法",),
            rule_id="action.dvalin_bonk_play_sayu_talent",
        )
        if choice is not None:
            return choice
        if aura > 0:
            if agent._has_ready_burst(context):
                choice = agent._use_active_skill_types(
                    context,
                    skill_types=("GCG_SKILL_TAG_Q",),
                    rule_id="action.dvalin_bonk_swirl_finish",
                )
                if choice is not None:
                    return choice
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(15072, 15073),
                rule_id="action.dvalin_bonk_swirl_finish",
            )
            if choice is not None:
                return choice
        if aura <= 0:
            if agent._has_ready_burst(context):
                choice = agent._use_active_skill_types(
                    context,
                    skill_types=("GCG_SKILL_TAG_Q",),
                    rule_id="action.dvalin_bonk_sayu_setup_window",
                )
                if choice is not None:
                    return choice
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=(15072,),
                rule_id="action.dvalin_bonk_sayu_setup_window",
            )
            if choice is not None:
                return choice
        if aura > 0 or round_number >= 4:
            return agent._switch_to_named_targets(
                context,
                target_names=("特瓦林",),
                rule_id="action.dvalin_bonk_sayu_rotate_to_finisher",
                require_safe_target=True,
            )
        return None

    if active_name == "特瓦林":
        if agent._has_ready_burst(context):
            choice = agent._use_active_skill_types(
                context,
                skill_types=("GCG_SKILL_TAG_Q",),
                rule_id="action.dvalin_bonk_dvalin_finish",
            )
            if choice is not None:
                return choice
        if aura > 0 or round_number >= 3:
            skill_ids = (25023, 25022) if round_number <= 3 else (25022, 25023)
            choice = agent._use_active_skill_definition_ids(
                context,
                skill_definition_ids=skill_ids,
                rule_id="action.dvalin_bonk_dvalin_finish",
            )
            if choice is not None:
                return choice
        choice = agent._use_active_skill_definition_ids(
            context,
            skill_definition_ids=(25022, 25023),
            rule_id="action.dvalin_bonk_dvalin_raw_pressure",
        )
        if choice is not None:
            return choice
        if aura > 0 and round_number <= 3:
            return agent._switch_to_named_targets(
                context,
                target_names=("早柚",),
                rule_id="action.dvalin_bonk_reset_to_sayu_window",
                require_safe_target=True,
            )
        if aura <= 0 and round_number <= 2 and remaining_dice >= 2:
            return agent._switch_to_named_targets(
                context,
                target_names=("伊安珊",),
                rule_id="action.dvalin_bonk_reset_to_iansan_window",
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
    dice_count = len(agent._player(context).dice)
    injured_exists = any(
        int(character.health) < int(character.max_health)
        for character in agent._player(context).characters
        if not character.defeated
    )
    if name == "万家灶火":
        return 5 if round_number <= 2 else 2
    if name == "风龙废墟":
        return 4 if round_number <= 3 else 1
    if name == "「沃陆之邦」":
        return 4 if round_number <= 3 else 1
    if name == "元素共鸣：迅捷之风":
        return 4 if round_number <= 3 or aura > 0 else 1
    if name == "元素共鸣：交织之风":
        return 3 if active_name in {"早柚", "特瓦林"} else 1
    if name == "最好的伙伴！":
        return 4 if dice_count <= 2 else 1
    if name == "化种匣":
        return 3 if round_number <= 3 else 0
    if name == "偷懒的新方法":
        return 4 if active_name == "早柚" and (aura > 0 or round_number <= 3) else -2
    if name == "「沃陆之邦」的训教":
        return 4 if active_name == "伊安珊" and round_number <= 4 else -2
    if name == "黄金剧团":
        return 3 if active_name in {"特瓦林", "早柚"} and round_number <= 4 else 0
    if name == "健身的成果":
        return 3 if active_name == "伊安珊" else 0
    if name == "赦免宣告":
        return 3 if injured_exists else -2
    if name == "奇瑰之汤":
        return -2 if round_number <= 2 else 0
    if name == "丰稔之赐":
        return 2 if injured_exists else 0
    return 0
