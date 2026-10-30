# Ported from Rebel_base_RL research/world_model (AGPL-3.0, github.com/.../Rebel_base_RL) by genshin-GITCG.
# Keep upstream semantics; adapt imports only. See NOTICE.md.
from __future__ import annotations

import random
from collections import Counter
from types import SimpleNamespace
from typing import Iterable

from reps.action_adapter import (
    BuiltDecisionContext,
    REQ_ALIGNED,
    REQ_ENERGY,
    REQ_LEGEND,
    REQ_VOID,
    _enumerate_action_dice_payments,
)
from reps.schema import DecisionContext, DecisionType, LowLevelActionSpec, OptionKind

from .assets import AssetCatalog
from .deck_rules import ACTION_RULES, ACTIVE_RULES, CARD_BONUS_RULES, REROLL_RULES, SELECT_RULES
from .models import DeckProfile, RuleChoice

OMNI_DICE = 8
_SPECIALIZED_MECHANIC_KEYWORDS = (
    "夜魂",
    "驰轮车",
    "追影弹",
    "虚境裂隙",
    "裂晶弹片",
    "水之新生",
    "蛇之狡谋",
    "准备技能",
)
_PRECISE_TIMING_CARD_NAMES = {
    "交给我吧！",
    "很棒，哥们。",
    "飞行队出击！",
    "万家灶火",
    "元素共鸣：交织之冰",
    "元素共鸣：粉碎之冰",
    "咚咚嘭嘭",
    "裂晶弹片",
    "水与正义",
    "纵声欢唱",
    "旁白的注脚",
    "鎏金殿堂",
    "一捧绿野",
    "噔噔！",
    "困困冥想术",
}
_COST_TYPE_TO_REQ = {
    "GCG_COST_DICE_CRYO": 1,
    "GCG_COST_DICE_HYDRO": 2,
    "GCG_COST_DICE_PYRO": 3,
    "GCG_COST_DICE_ELECTRO": 4,
    "GCG_COST_DICE_ANEMO": 5,
    "GCG_COST_DICE_GEO": 6,
    "GCG_COST_DICE_DENDRO": 7,
    "GCG_COST_DICE_VOID": REQ_VOID,
    "GCG_COST_DICE_SAME": REQ_ALIGNED,
    "GCG_COST_ENERGY": REQ_ENERGY,
    "GCG_COST_LEGEND": REQ_LEGEND,
}
_UNSUPPORTED_COST_TYPES: set[str] = set()
_SPECIAL_ENERGY_VARIABLE_BY_COST_TYPE = {
    "GCG_COST_SKIRK_SPECIAL_ENERGY": "serpentsSubtlety",
    "GCG_COST_MAVUIKA_SPECIAL_ENERGY": "fightingSpirit",
}


def _is_special_energy_cost_type(cost_type: str) -> bool:
    return "SPECIAL_ENERGY" in str(cost_type)


class ExpertRuleAgent:
    def __init__(self, *, assets: AssetCatalog, profile: DeckProfile, seed: int | None = None) -> None:
        self.assets = assets
        self.profile = profile
        self.random = random.Random(seed)

    def choose(self, built: BuiltDecisionContext) -> RuleChoice:
        context = built.context
        if context.request_type == DecisionType.CHOOSE_ACTIVE:
            return self._choose_active(context)
        if context.request_type == DecisionType.REROLL_DICE:
            return self._choose_reroll(context)
        if context.request_type == DecisionType.SWITCH_HANDS:
            return self._choose_switch_hands(context)
        if context.request_type == DecisionType.SELECT_CARD:
            return self._choose_select_card(context)
        if context.request_type == DecisionType.ACTION:
            return self._choose_action(context)
        return self._fallback_choice(context, rule_id="fallback.unknown_request")

    def _choose_active(self, context: DecisionContext) -> RuleChoice:
        deck_choose_active = ACTIVE_RULES.get(self.profile.slug)
        if deck_choose_active is not None:
            choice = deck_choose_active(self, context)
            if choice is not None:
                return choice
        pairs = self._pairs(context)
        ranked = sorted(
            pairs,
            key=lambda pair: (
                self._character_rank(self._character_name(pair[1].subject_definition_id)),
                self._character_hp_by_slot(context, pair[1].choose_active_slot) * -1,
                pair[1].label,
            ),
        )
        return RuleChoice(
            action_code=int(ranked[0][0]),
            rule_id=f"choose_active.preferred.{self.profile.slug}",
        )

    def _choose_active_by_names(
        self,
        context: DecisionContext,
        preferred_names: tuple[str, ...],
        *,
        require_safe_target: bool = False,
        rule_id: str | None = None,
    ) -> RuleChoice | None:
        if not preferred_names:
            return None
        name_rank = {name: len(preferred_names) - index for index, name in enumerate(preferred_names)}
        resolved_rule_id = rule_id or f"choose_active.deck_preferred.{self.profile.slug}"

        def _pick(*, safe_only: bool) -> tuple[int, tuple[int, int, str]] | None:
            best_choice: tuple[int, tuple[int, int, str]] | None = None
            for action_code, spec in self._pairs(context):
                name = self._character_name(spec.subject_definition_id)
                if name not in name_rank:
                    continue
                hp = self._character_hp_by_slot(context, spec.choose_active_slot)
                if safe_only and hp <= self.profile.rule_config.switch_hp_threshold:
                    continue
                score = (
                    name_rank[name],
                    hp,
                    str(spec.label),
                )
                if best_choice is None or score > best_choice[1]:
                    best_choice = (int(action_code), score)
            return best_choice

        best = _pick(safe_only=require_safe_target)
        if best is None and require_safe_target:
            best = _pick(safe_only=False)
        if best is None:
            return None
        return RuleChoice(action_code=best[0], rule_id=resolved_rule_id)

    def _choose_reroll(self, context: DecisionContext) -> RuleChoice:
        deck_choose_reroll = REROLL_RULES.get(self.profile.slug)
        if deck_choose_reroll is not None:
            choice = deck_choose_reroll(self, context)
            if choice is not None:
                return choice
        visible_dice = tuple(int(value) for value in context.request_payload.get("visible_dice", ()))
        desired = set(self.profile.rule_config.preferred_elements)
        best_code = None
        best_score = None
        for action_code, spec in self._pairs(context):
            rerolled = [die for index, die in enumerate(visible_dice) if spec.reroll_dice_mask & (1 << index)]
            kept = [die for index, die in enumerate(visible_dice) if not (spec.reroll_dice_mask & (1 << index))]
            keep_good = sum(1 for die in kept if die == OMNI_DICE or self._die_to_tag(die) in desired)
            keep_bad = sum(1 for die in kept if die != OMNI_DICE and self._die_to_tag(die) not in desired)
            reroll_bad = sum(1 for die in rerolled if self._die_to_tag(die) not in desired)
            score = (keep_good, reroll_bad, -keep_bad, -len(rerolled))
            if best_score is None or score > best_score:
                best_code = int(action_code)
                best_score = score
        return RuleChoice(action_code=int(best_code), rule_id="reroll.keep_preferred")

    def _choose_switch_hands(self, context: DecisionContext) -> RuleChoice:
        player = self._player(context)
        hand_cards = tuple(player.hand_cards)
        best_code = None
        best_score = None
        for action_code, spec in self._pairs(context):
            removed_indexes = [
                index for index in range(len(hand_cards))
                if spec.switch_hand_slot_mask & (1 << index)
            ]
            kept_indexes = [index for index in range(len(hand_cards)) if index not in removed_indexes]
            keep_score = sum(
                self._contextual_hand_card_keep_score(context, hand_cards[index].definition_id)
                for index in kept_indexes
            )
            remove_penalty = sum(
                self._contextual_hand_card_keep_score(context, hand_cards[index].definition_id)
                for index in removed_indexes
            )
            score = (keep_score - remove_penalty, -len(removed_indexes))
            if best_score is None or score > best_score:
                best_code = int(action_code)
                best_score = score
        return RuleChoice(action_code=int(best_code), rule_id="switch_hands.keep_core")

    def _choose_select_card(self, context: DecisionContext) -> RuleChoice:
        deck_choose_select = SELECT_RULES.get(self.profile.slug)
        if deck_choose_select is not None:
            choice = deck_choose_select(self, context)
            if choice is not None:
                return choice
        candidate_ids = tuple(int(value) for value in context.request_payload.get("candidate_definition_ids", ()))
        candidate_set = set(candidate_ids)
        rule_id = "select_card.preferred"
        if candidate_set and candidate_set <= {113154, 113155, 113156}:
            rule_id = "select_card.mavuika_vehicle"
        elif candidate_set and candidate_set <= {303041, 303042}:
            preferred = ("超导祝佑·极寒", "超导祝佑·电冲")
            if self._opponent_active_aura(context) > 0:
                preferred = ("超导祝佑·电冲", "超导祝佑·极寒")
            choice = self._select_candidate_by_names(
                context,
                candidate_names=preferred,
                rule_id="select_card.superconduct_blessing",
            )
            if choice is not None:
                return choice
        elif candidate_set and candidate_set <= {303051, 303052}:
            player = self._player(context)
            low_hp_exists = any(
                int(character.health) <= max(3, int(character.max_health) // 2)
                for character in player.characters
                if not character.defeated
            )
            preferred = ("蒸发祝佑·炽燃", "蒸发祝佑·狂浪")
            if self._active_character_name(context) == "玛拉妮" or low_hp_exists:
                preferred = ("蒸发祝佑·狂浪", "蒸发祝佑·炽燃")
            choice = self._select_candidate_by_names(
                context,
                candidate_names=preferred,
                rule_id="select_card.vaporize_blessing",
            )
            if choice is not None:
                return choice
        elif candidate_ids:
            cards = [self.assets.cards.get(int(card_id)) for card_id in candidate_ids]
            if cards and all(card is not None and "GCG_TAG_VEHICLE" in set(card.tags) for card in cards):
                rule_id = "select_card.discover_vehicle"
            elif cards and all(card is not None and "GCG_TAG_FOOD" in set(card.tags) for card in cards):
                rule_id = "select_card.discover_food"
        best_code = None
        best_score = None
        for action_code, spec in self._pairs(context):
            card_id = int(spec.subject_definition_id)
            card = self.assets.cards.get(card_id)
            related_name = self._character_name(card.related_character_id) if card and card.related_character_id else ""
            is_carry = related_name in self.profile.rule_config.carry
            total_cost = sum(count for _, count in (card.play_cost if card else ()))
            source_bonus = 0
            if rule_id == "select_card.discover_food" and card is not None:
                desc = str(card.description or "")
                active = self._active_character(context)
                active_hp = int(active.health) if active is not None else 0
                active_max_hp = int(active.max_health) if active is not None else 0
                if ("治疗" in desc or "回复" in desc) and active_hp <= max(3, active_max_hp // 2):
                    source_bonus += 2
                if "造成的伤害+1" in desc or "少花费1个元素骰" in desc:
                    source_bonus += 1
            score = (
                1 if is_carry else 0,
                self._description_tactical_score(card, context=context, mode="select")
                + self._card_context_bonus(card, context=context, mode="select")
                + source_bonus,
                -total_cost,
                -card_id,
            )
            if best_score is None or score > best_score:
                best_code = int(action_code)
                best_score = score
        return RuleChoice(action_code=int(best_code), rule_id=rule_id)

    def _choose_action(self, context: DecisionContext) -> RuleChoice:
        deck_specific = self._choose_deck_specific_action(context)
        if deck_specific is not None:
            return deck_specific
        for chooser in (
            self._rule_use_burst,
            self._rule_tune_for_priority_action,
            self._rule_play_active_talent,
            self._rule_reaction_setup_switch,
            self._rule_play_setup_support,
            self._rule_play_equipment,
            self._rule_use_elemental_skill,
            self._rule_use_normal_attack,
            self._rule_play_low_cost_event,
            self._rule_use_any_skill,
            self._rule_play_any_card,
            self._rule_switch_low_hp,
            self._rule_switch_to_preferred,
            self._rule_elemental_tuning,
            self._rule_declare_end,
        ):
            choice = chooser(context)
            if choice is not None:
                return choice
        return self._fallback_choice(context, rule_id="fallback.scored_action")

    def _choose_deck_specific_action(self, context: DecisionContext) -> RuleChoice | None:
        deck_choose_action = ACTION_RULES.get(self.profile.slug)
        if deck_choose_action is None:
            return None
        return deck_choose_action(self, context)

    def _rule_switch_low_hp(self, context: DecisionContext) -> RuleChoice | None:
        active = self._active_character(context)
        if active is None or active.health > self.profile.rule_config.switch_hp_threshold:
            return None
        if self._has_ready_burst(context):
            return None
        target = self._best_switch_target(context, require_better_hp=True, min_hp_gain=2)
        if target is None:
            return None
        return RuleChoice(action_code=target[0], rule_id="action.switch_low_hp")

    def _rule_play_active_talent(self, context: DecisionContext) -> RuleChoice | None:
        active_name = self._active_character_name(context)
        best: tuple[int, tuple[int, ...], object] | None = None
        for action_code, spec in self._pairs(context, kind=OptionKind.ACTION_PLAY_CARD):
            card = self.assets.cards.get(int(spec.subject_definition_id))
            if card is None or "GCG_TAG_TALENT" not in set(card.tags):
                continue
            if self._character_name(card.related_character_id) != active_name:
                continue
            if self._requires_deck_specific_card_handling(card):
                continue
            tactical_score = (
                self._description_tactical_score(card, context=context, mode="talent")
                + self._card_context_bonus(card, context=context, mode="talent")
            )
            total_cost = self._total_dice_cost(card.play_cost)
            score = (
                tactical_score,
                *self._spec_resource_score(context, spec, card=card),
                -total_cost,
                -int(spec.subject_definition_id),
                -int(action_code),
            )
            if best is None or score > best[1]:
                best = (int(action_code), score, card)
        if best is None or best[1][0] <= 0:
            return None
        return RuleChoice(
            action_code=best[0],
            rule_id=self._rule_id_for_card("action.play_active_talent", best[2]),
        )

    def _rule_use_burst(self, context: DecisionContext) -> RuleChoice | None:
        active_name = self._active_character_name(context)
        best: tuple[int, tuple[int, ...]] | None = None
        for action_code, spec in self._pairs(context, kind=OptionKind.ACTION_USE_SKILL):
            skill = self.assets.skills.get(int(spec.subject_definition_id))
            if skill is None or skill.skill_type != "GCG_SKILL_TAG_Q":
                continue
            if skill.character_name != active_name:
                continue
            score = (
                self._skill_tactical_score(skill, context=context),
                *self._spec_resource_score(context, spec, skill=skill),
                -self._total_dice_cost(skill.play_cost),
                -int(spec.subject_definition_id),
                -int(action_code),
            )
            if best is None or score > best[1]:
                best = (int(action_code), score)
        if best is None:
            return None
        return RuleChoice(action_code=best[0], rule_id="action.use_burst")

    def _rule_tune_for_priority_action(self, context: DecisionContext) -> RuleChoice | None:
        if self._rule_switch_low_hp(context) is not None:
            return None
        current_best = self._best_immediate_priority_score(context)
        legal_skill_ids = {
            int(spec.subject_definition_id)
            for _, spec in self._pairs(context, kind=OptionKind.ACTION_USE_SKILL)
        }
        legal_card_ids = {
            int(spec.subject_definition_id)
            for _, spec in self._pairs(context, kind=OptionKind.ACTION_PLAY_CARD)
        }
        best: tuple[int, tuple[int, ...]] | None = None
        for action_code, spec in self._pairs(context, kind=OptionKind.ACTION_ELEMENTAL_TUNING):
            intrinsic = self._tuning_intrinsic_score(context, spec)
            if intrinsic is None:
                continue
            outcomes = self._tuned_dice_outcomes(context, spec)
            if not outcomes:
                continue
            projected_scores = [
                projected
                for projected in (
                    self._best_projected_priority_score(
                        context,
                        dice=outcome,
                        legal_skill_ids=legal_skill_ids,
                        legal_card_ids=legal_card_ids,
                    )
                    for outcome in outcomes
                )
                if projected is not None
            ]
            if not projected_scores:
                continue
            conservative_projected = min(projected_scores)
            if current_best is not None and conservative_projected <= current_best:
                continue
            score = (*conservative_projected, *intrinsic, -int(action_code))
            if best is None or score > best[1]:
                best = (int(action_code), score)
        if best is None:
            return None
        return RuleChoice(action_code=best[0], rule_id="action.tune_for_priority")

    def _rule_reaction_setup_switch(self, context: DecisionContext) -> RuleChoice | None:
        active = self._active_character(context)
        if active is None:
            return None
        if self._round_number(context) >= 5 and self._has_legal_skill_action(context):
            return None
        aura = self._opponent_active_aura(context)
        active_element = self._character_element(active.definition_id)
        desired_setup, desired_finish = self._reaction_switch_targets()
        if not desired_setup and not desired_finish:
            return None
        if aura <= 0 and active_element in desired_finish:
            target = self._best_switch_by_elements(context, desired_setup)
            if target is not None:
                return RuleChoice(action_code=target[0], rule_id="action.switch_for_setup")
        if aura > 0 and active_element in desired_setup:
            target = self._best_switch_by_elements(context, desired_finish)
            if target is not None:
                return RuleChoice(action_code=target[0], rule_id="action.switch_for_reaction")
        return None

    def _rule_play_setup_support(self, context: DecisionContext) -> RuleChoice | None:
        if int(context.player_view.round_number if context.player_view else context.full_state.round_number) > self.profile.rule_config.setup_rounds:
            return None
        best: tuple[int, tuple[int, ...], object] | None = None
        for action_code, spec in self._pairs(context, kind=OptionKind.ACTION_PLAY_CARD):
            card = self.assets.cards.get(int(spec.subject_definition_id))
            if card is None:
                continue
            tag_set = set(card.tags)
            if not ({"GCG_TAG_ALLY", "GCG_TAG_PLACE", "GCG_TAG_ITEM"} & tag_set):
                continue
            total_cost = self._total_dice_cost(card.play_cost)
            score = (
                self._description_tactical_score(card, context=context, mode="setup")
                + self._card_context_bonus(card, context=context, mode="setup"),
                *self._spec_resource_score(context, spec, card=card),
                -total_cost,
                -int(action_code),
                -int(spec.subject_definition_id),
            )
            if best is None or score > best[1]:
                best = (int(action_code), score, card)
        if best is None or best[1][0] <= 0:
            return None
        return RuleChoice(
            action_code=best[0],
            rule_id=self._rule_id_for_card("action.play_setup_support", best[2]),
        )

    def _rule_play_equipment(self, context: DecisionContext) -> RuleChoice | None:
        active_name = self._active_character_name(context)
        best: tuple[int, tuple[int, ...], object] | None = None
        for action_code, spec in self._pairs(context, kind=OptionKind.ACTION_PLAY_CARD):
            card = self.assets.cards.get(int(spec.subject_definition_id))
            if card is None:
                continue
            tag_set = set(card.tags)
            if not ({"GCG_TAG_WEAPON", "GCG_TAG_ARTIFACT", "GCG_TAG_VEHICLE"} & tag_set):
                continue
            if self._requires_deck_specific_card_handling(card):
                continue
            related_name = self._character_name(card.related_character_id) if card.related_character_id else ""
            is_active_match = 1 if related_name == active_name else 0
            is_carry_match = 1 if related_name in self.profile.rule_config.carry else 0
            tactical_score = (
                self._description_tactical_score(card, context=context, mode="equip")
                + self._card_context_bonus(card, context=context, mode="equip")
            )
            total_cost = self._total_dice_cost(card.play_cost)
            score = (
                is_active_match,
                is_carry_match,
                tactical_score,
                *self._spec_resource_score(context, spec, card=card),
                -total_cost,
                -int(action_code),
            )
            if best is None or score > best[1]:
                best = (int(action_code), score, card)
        if best is None or (best[1][0] == 0 and best[1][1] == 0) or best[1][2] <= 0:
            return None
        return RuleChoice(
            action_code=best[0],
            rule_id=self._rule_id_for_card("action.play_equipment", best[2]),
        )

    def _rule_use_elemental_skill(self, context: DecisionContext) -> RuleChoice | None:
        active_name = self._active_character_name(context)
        best: tuple[int, tuple[int, ...], object] | None = None
        for action_code, spec in self._pairs(context, kind=OptionKind.ACTION_USE_SKILL):
            skill = self.assets.skills.get(int(spec.subject_definition_id))
            if skill is None or skill.skill_type != "GCG_SKILL_TAG_E":
                continue
            if skill.character_name != active_name:
                continue
            score = (
                self._skill_tactical_score(skill, context=context),
                *self._spec_resource_score(context, spec, skill=skill),
                -self._total_dice_cost(skill.play_cost),
                -int(spec.subject_definition_id),
                -int(action_code),
            )
            if best is None or score > best[1]:
                best = (int(action_code), score, skill)
        if best is None:
            return None
        return RuleChoice(
            action_code=best[0],
            rule_id=self._rule_id_for_skill("action.use_elemental_skill", best[2]),
        )

    def _rule_switch_to_preferred(self, context: DecisionContext) -> RuleChoice | None:
        active_name = self._active_character_name(context)
        if active_name == self.profile.rule_config.opener or active_name in self.profile.rule_config.carry[:2]:
            return None
        non_switch_non_end = [
            pair
            for pair in self._pairs(context)
            if pair[1].kind not in {
                OptionKind.ACTION_DECLARE_END,
                OptionKind.ACTION_SWITCH_ACTIVE,
                OptionKind.ACTION_ELEMENTAL_TUNING,
            }
        ]
        if not non_switch_non_end:
            return None
        if not any(pair[1].kind == OptionKind.ACTION_USE_SKILL for pair in non_switch_non_end):
            meaningful_cards = [
                pair
                for pair in non_switch_non_end
                if pair[1].kind == OptionKind.ACTION_PLAY_CARD
                and self._remaining_card_action_value(context, pair[1]) > 0
            ]
            if not meaningful_cards:
                return None
        target = self._best_switch_target(
            context,
            require_better_hp=False,
            preferred_names=(self.profile.rule_config.opener, *self.profile.rule_config.carry[:2]),
            require_safe_target=True,
        )
        if target is None:
            return None
        return RuleChoice(action_code=target[0], rule_id="action.switch_to_preferred")

    def _rule_use_normal_attack(self, context: DecisionContext) -> RuleChoice | None:
        active_name = self._active_character_name(context)
        best: tuple[int, tuple[int, ...], object] | None = None
        for action_code, spec in self._pairs(context, kind=OptionKind.ACTION_USE_SKILL):
            skill = self.assets.skills.get(int(spec.subject_definition_id))
            if skill is None or skill.skill_type != "GCG_SKILL_TAG_A":
                continue
            if skill.character_name != active_name:
                continue
            score = (
                self._skill_tactical_score(skill, context=context),
                *self._spec_resource_score(context, spec, skill=skill),
                -self._total_dice_cost(skill.play_cost),
                -int(spec.subject_definition_id),
                -int(action_code),
            )
            if best is None or score > best[1]:
                best = (int(action_code), score, skill)
        if best is None:
            return None
        return RuleChoice(
            action_code=best[0],
            rule_id=self._rule_id_for_skill("action.use_normal_attack", best[2]),
        )

    def _rule_play_low_cost_event(self, context: DecisionContext) -> RuleChoice | None:
        best: tuple[int, tuple[int, ...], object] | None = None
        for action_code, spec in self._pairs(context, kind=OptionKind.ACTION_PLAY_CARD):
            card = self.assets.cards.get(int(spec.subject_definition_id))
            if card is None:
                continue
            if self._requires_deck_specific_card_handling(card):
                continue
            total_cost = self._total_dice_cost(card.play_cost)
            tag_set = set(card.tags)
            is_event = 0 if {"GCG_TAG_ALLY", "GCG_TAG_PLACE", "GCG_TAG_ITEM", "GCG_TAG_WEAPON", "GCG_TAG_ARTIFACT", "GCG_TAG_TALENT", "GCG_TAG_VEHICLE"} & tag_set else 1
            score = (
                is_event,
                self._description_tactical_score(card, context=context, mode="event")
                + self._card_context_bonus(card, context=context, mode="event"),
                *self._spec_resource_score(context, spec, card=card),
                -total_cost,
                -int(spec.subject_definition_id),
                -int(action_code),
            )
            if best is None or score > best[1]:
                best = (int(action_code), score, card)
        if best is None or best[1][0] == 0 or best[1][1] < 3:
            return None
        return RuleChoice(
            action_code=best[0],
            rule_id=self._rule_id_for_card("action.play_low_cost_event", best[2]),
        )

    def _rule_use_any_skill(self, context: DecisionContext) -> RuleChoice | None:
        best: tuple[int, tuple[int, ...], object] | None = None
        kind_rank = {
            "GCG_SKILL_TAG_E": 3,
            "GCG_SKILL_TAG_A": 2,
            "GCG_SKILL_TAG_VEHICLE": 2,
            "GCG_SKILL_TAG_Q": 1,
        }
        for action_code, spec in self._pairs(context, kind=OptionKind.ACTION_USE_SKILL):
            skill = self.assets.skills.get(int(spec.subject_definition_id))
            if skill is None or self._requires_deck_specific_skill_handling(skill):
                continue
            tactical_score = self._skill_tactical_score(skill, context=context)
            score = (
                kind_rank.get(skill.skill_type, 0),
                tactical_score,
                *self._spec_resource_score(context, spec, skill=skill),
                -self._total_dice_cost(skill.play_cost),
                -int(spec.subject_definition_id),
                -int(action_code),
            )
            if best is None or score > best[1]:
                best = (int(action_code), score, skill)
        if best is None or best[1][1] <= 0:
            return None
        return RuleChoice(
            action_code=best[0],
            rule_id=self._rule_id_for_skill("action.use_any_skill", best[2]),
        )

    def _rule_play_any_card(self, context: DecisionContext) -> RuleChoice | None:
        best: tuple[int, tuple[int, ...], object] | None = None
        for action_code, spec in self._pairs(context, kind=OptionKind.ACTION_PLAY_CARD):
            card = self.assets.cards.get(int(spec.subject_definition_id))
            if card is None:
                continue
            tag_set = set(card.tags)
            if {
                "GCG_TAG_TALENT",
                "GCG_TAG_WEAPON",
                "GCG_TAG_ARTIFACT",
                "GCG_TAG_VEHICLE",
                "GCG_TAG_ALLY",
                "GCG_TAG_PLACE",
                "GCG_TAG_ITEM",
            } & tag_set:
                continue
            if self._requires_deck_specific_card_handling(card):
                continue
            related_name = self._character_name(card.related_character_id) if card.related_character_id else ""
            is_carry = 1 if related_name in self.profile.rule_config.carry else 0
            is_support = 1 if related_name in self.profile.rule_config.bench else 0
            total_cost = self._total_dice_cost(card.play_cost)
            score = (
                is_carry,
                is_support,
                self._description_tactical_score(card, context=context, mode="any")
                + self._card_context_bonus(card, context=context, mode="any"),
                *self._spec_resource_score(context, spec, card=card),
                -total_cost,
                -int(spec.subject_definition_id),
                -int(action_code),
            )
            if best is None or score > best[1]:
                best = (int(action_code), score, card)
        if best is None or (best[1][0] == 0 and best[1][1] == 0 and best[1][2] <= 0):
            return None
        return RuleChoice(
            action_code=best[0],
            rule_id=self._rule_id_for_card("action.play_any_card", best[2]),
        )

    def _rule_declare_end(self, context: DecisionContext) -> RuleChoice | None:
        declare_pairs = list(self._pairs(context, kind=OptionKind.ACTION_DECLARE_END))
        if not declare_pairs:
            return None
        non_tuning_non_end = [
            pair
            for pair in self._pairs(context)
            if pair[1].kind not in {
                OptionKind.ACTION_DECLARE_END,
                OptionKind.ACTION_ELEMENTAL_TUNING,
                OptionKind.ACTION_SWITCH_ACTIVE,
            }
        ]
        if not non_tuning_non_end:
            return RuleChoice(action_code=int(declare_pairs[0][0]), rule_id="action.declare_end")
        skill_actions = [
            pair
            for pair in non_tuning_non_end
            if pair[1].kind == OptionKind.ACTION_USE_SKILL
        ]
        if skill_actions:
            return None
        meaningful_card_actions = [
            pair
            for pair in non_tuning_non_end
            if pair[1].kind == OptionKind.ACTION_PLAY_CARD
            and self._remaining_card_action_value(context, pair[1]) > 0
        ]
        if meaningful_card_actions:
            return None
        return RuleChoice(action_code=int(declare_pairs[0][0]), rule_id="action.declare_end")

    def _remaining_card_action_value(self, context: DecisionContext, spec: LowLevelActionSpec) -> int:
        if spec.kind != OptionKind.ACTION_PLAY_CARD:
            return 0
        card = self.assets.cards.get(int(spec.subject_definition_id))
        if card is None:
            return 0
        return (
            self._description_tactical_score(card, context=context, mode="fallback")
            + self._card_context_bonus(card, context=context, mode="fallback")
        )

    def _rule_id_for_card(self, base: str, card) -> str:
        category = "card"
        tag_set = set(getattr(card, "tags", ()))
        if "GCG_TAG_TALENT" in tag_set:
            category = "talent"
        elif "GCG_TAG_WEAPON" in tag_set:
            category = "weapon"
        elif "GCG_TAG_ARTIFACT" in tag_set:
            category = "artifact"
        elif "GCG_TAG_VEHICLE" in tag_set:
            category = "vehicle"
        elif "GCG_TAG_ALLY" in tag_set:
            category = "ally"
        elif "GCG_TAG_PLACE" in tag_set:
            category = "place"
        elif "GCG_TAG_ITEM" in tag_set:
            category = "item"
        elif "GCG_TAG_EVENT" in tag_set:
            category = "event"
        return f"{base}.{self.profile.slug}.{category}_{int(card.id)}"

    def _rule_id_for_skill(self, base: str, skill) -> str:
        skill_type = str(getattr(skill, "skill_type", "") or "")
        family = {
            "GCG_SKILL_TAG_A": "na",
            "GCG_SKILL_TAG_E": "skill",
            "GCG_SKILL_TAG_Q": "burst",
            "GCG_SKILL_TAG_VEHICLE": "vehicle",
        }.get(skill_type, "skill")
        return f"{base}.{self.profile.slug}.{family}_{int(skill.id)}"

    def _requires_deck_specific_card_handling(self, card) -> bool:
        text = f"{getattr(card, 'name', '')} {getattr(card, 'description', '')}"
        if str(getattr(card, "name", "") or "") in _PRECISE_TIMING_CARD_NAMES:
            return True
        return any(keyword in text for keyword in _SPECIALIZED_MECHANIC_KEYWORDS)

    def _requires_deck_specific_skill_handling(self, skill) -> bool:
        text = f"{getattr(skill, 'name', '')} {getattr(skill, 'description', '')}"
        if any(keyword in text for keyword in _SPECIALIZED_MECHANIC_KEYWORDS):
            return True
        if str(getattr(skill, "character_name", "") or "") == "":
            return True
        return False

    def _rule_elemental_tuning(self, context: DecisionContext) -> RuleChoice | None:
        tuning_pairs = list(self._pairs(context, kind=OptionKind.ACTION_ELEMENTAL_TUNING))
        if not tuning_pairs:
            return None
        player = self._player(context)
        if len(player.dice) <= 1 or not player.hand_cards:
            return None
        if any(
            spec.kind in {OptionKind.ACTION_USE_SKILL, OptionKind.ACTION_PLAY_CARD}
            for _, spec in self._pairs(context)
        ):
            return None
        best: tuple[int, tuple[int, ...]] | None = None
        for action_code, spec in tuning_pairs:
            intrinsic = self._tuning_intrinsic_score(context, spec)
            if intrinsic is None:
                continue
            score = (*intrinsic, -int(action_code))
            if best is None or score > best[1]:
                best = (int(action_code), score)
        if best is None:
            return None
        return RuleChoice(action_code=best[0], rule_id="action.elemental_tuning")

    def _fallback_action_score(
        self,
        context: DecisionContext,
        *,
        action_code: int,
        spec: LowLevelActionSpec,
    ) -> tuple[int, ...]:
        if spec.kind == OptionKind.ACTION_DECLARE_END:
            return (0, 1, 0, 0, -int(action_code))
        if spec.kind == OptionKind.ACTION_USE_SKILL:
            skill = self.assets.skills.get(int(spec.subject_definition_id))
            tactical = self._skill_tactical_score(skill, context=context)
            kind_bonus = {
                "GCG_SKILL_TAG_Q": 2,
                "GCG_SKILL_TAG_E": 1,
                "GCG_SKILL_TAG_VEHICLE": 0,
                "GCG_SKILL_TAG_A": -1,
            }.get(str(getattr(skill, "skill_type", "") or ""), 0)
            return (
                tactical,
                kind_bonus,
                *self._spec_resource_score(context, spec, skill=skill),
                -int(spec.subject_definition_id),
                -int(action_code),
            )
        if spec.kind == OptionKind.ACTION_PLAY_CARD:
            card = self.assets.cards.get(int(spec.subject_definition_id))
            tactical = self._remaining_card_action_value(context, spec)
            related_name = self._character_name(card.related_character_id) if card and card.related_character_id else ""
            active_name = self._active_character_name(context)
            is_active_match = 1 if related_name and related_name == active_name else 0
            is_carry_match = 1 if related_name in self.profile.rule_config.carry else 0
            total_cost = self._total_dice_cost(card.play_cost) if card is not None else len(spec.used_dice)
            return (
                tactical,
                is_active_match + is_carry_match,
                *self._spec_resource_score(context, spec, card=card),
                -total_cost,
                -int(spec.subject_definition_id),
                -int(action_code),
            )
        if spec.kind == OptionKind.ACTION_SWITCH_ACTIVE:
            active = self._active_character(context)
            active_hp = int(active.health) if active is not None else 0
            target_name = self._switch_target_name(context, spec)
            target_hp = self._switch_target_hp(context, spec)
            preferred_bonus = 1 if target_name in (self.profile.rule_config.opener, *self.profile.rule_config.carry[:2]) else 0
            return (
                -1 + preferred_bonus,
                1 if target_hp > active_hp else 0,
                target_hp,
                -self._character_rank(target_name),
                -int(action_code),
            )
        if spec.kind == OptionKind.ACTION_ELEMENTAL_TUNING:
            intrinsic = self._tuning_intrinsic_score(context, spec)
            if intrinsic is None:
                return (-2, 0, 0, -int(action_code))
            return (1, *intrinsic, -int(action_code))
        if spec.kind == OptionKind.CHOOSE_ACTIVE:
            name = self._character_name(spec.subject_definition_id)
            hp = self._character_hp_by_slot(context, spec.choose_active_slot)
            preferred_bonus = 1 if name in (self.profile.rule_config.opener, *self.profile.rule_config.carry[:2]) else 0
            return (preferred_bonus, hp, -self._character_rank(name), -int(action_code))
        return (-3, -int(action_code))

    def _fallback_choice(self, context: DecisionContext, *, rule_id: str) -> RuleChoice:
        pairs = self._pairs(context)
        if not pairs:
            return RuleChoice(action_code=0, rule_id=rule_id, fallback=True)
        best: tuple[int, tuple[int, ...], LowLevelActionSpec] | None = None
        for action_code, spec in pairs:
            score = self._fallback_action_score(context, action_code=int(action_code), spec=spec)
            if best is None or score > best[1]:
                best = (int(action_code), score, spec)
        if best is None:
            return RuleChoice(action_code=int(pairs[0][0]), rule_id=rule_id, fallback=True)
        resolved_rule_id = "fallback.scored_action"
        if best[2].kind == OptionKind.ACTION_DECLARE_END:
            resolved_rule_id = "fallback.declare_end"
        return RuleChoice(
            action_code=best[0],
            rule_id=resolved_rule_id,
            fallback=True,
        )

    def _has_legal_skill_action(self, context: DecisionContext) -> bool:
        return any(True for _ in self._pairs(context, kind=OptionKind.ACTION_USE_SKILL))

    def _best_switch_target(
        self,
        context: DecisionContext,
        *,
        require_better_hp: bool,
        min_hp_gain: int = 1,
        preferred_names: tuple[str, ...] = (),
        require_safe_target: bool = False,
    ) -> tuple[int, str] | None:
        active = self._active_character(context)
        active_hp = active.health if active is not None else 0
        best: tuple[int, str, tuple[int, int, int]] | None = None
        for action_code, spec in self._pairs(context, kind=OptionKind.ACTION_SWITCH_ACTIVE):
            name = self._switch_target_name(context, spec)
            hp = self._switch_target_hp(context, spec)
            if require_better_hp and hp < active_hp + int(min_hp_gain):
                continue
            if require_safe_target and hp <= self.profile.rule_config.switch_hp_threshold:
                continue
            preferred_bonus = 1 if not preferred_names or name in preferred_names else 0
            score = (
                preferred_bonus,
                -self._character_rank(name),
                hp,
                -int(action_code),
            )
            if best is None or score > best[2]:
                best = (int(action_code), name, score)
        if best is None:
            return None
        return best[0], best[1]

    def _best_switch_by_elements(
        self,
        context: DecisionContext,
        desired_elements: tuple[str, ...],
    ) -> tuple[int, str] | None:
        if not desired_elements:
            return None
        active = self._active_character(context)
        active_hp = active.health if active is not None else 0
        best: tuple[int, str, tuple[int, int, int, int]] | None = None
        for action_code, spec in self._pairs(context, kind=OptionKind.ACTION_SWITCH_ACTIVE):
            name = self._switch_target_name(context, spec)
            hp = self._switch_target_hp(context, spec)
            element = self._character_element_by_name(name)
            if element not in desired_elements:
                continue
            if hp <= self.profile.rule_config.switch_hp_threshold and hp <= active_hp:
                continue
            score = (
                1 if hp > active_hp else 0,
                -self._character_rank(name),
                hp,
                -int(action_code),
            )
            if best is None or score > best[2]:
                best = (int(action_code), name, score)
        if best is None:
            return None
        return best[0], best[1]

    def _play_named_card(
        self,
        context: DecisionContext,
        *,
        card_names: tuple[str, ...],
        rule_id: str,
        min_tactical_score: int | None = None,
    ) -> RuleChoice | None:
        if not card_names:
            return None
        rank_by_name = {name: len(card_names) - index for index, name in enumerate(card_names)}
        best: tuple[int, tuple[int, ...]] | None = None
        for action_code, spec in self._pairs(context, kind=OptionKind.ACTION_PLAY_CARD):
            card = self.assets.cards.get(int(spec.subject_definition_id))
            if card is None or card.name not in rank_by_name:
                continue
            tactical_score = self._description_tactical_score(card, context=context, mode="named")
            if min_tactical_score is not None and tactical_score < int(min_tactical_score):
                continue
            total_cost = self._total_dice_cost(card.play_cost)
            score = (
                rank_by_name[card.name],
                tactical_score + self._card_context_bonus(card, context=context, mode="named"),
                *self._spec_resource_score(context, spec, card=card),
                -total_cost,
                -int(spec.subject_definition_id),
                -int(action_code),
            )
            if best is None or score > best[1]:
                best = (int(action_code), score)
        if best is None:
            return None
        return RuleChoice(action_code=best[0], rule_id=rule_id)

    def _select_candidate_by_names(
        self,
        context: DecisionContext,
        *,
        candidate_names: tuple[str, ...],
        rule_id: str,
    ) -> RuleChoice | None:
        if not candidate_names:
            return None
        rank_by_name = {name: len(candidate_names) - index for index, name in enumerate(candidate_names)}
        best: tuple[int, tuple[int, int, int]] | None = None
        for action_code, spec in self._pairs(context):
            card = self.assets.cards.get(int(spec.subject_definition_id))
            if card is None or str(card.name) not in rank_by_name:
                continue
            total_cost = sum(count for _, count in card.play_cost)
            score = (
                rank_by_name[str(card.name)],
                self._description_tactical_score(card, context=context, mode="select")
                + self._card_context_bonus(card, context=context, mode="select"),
                -total_cost,
            )
            if best is None or score > best[1]:
                best = (int(action_code), score)
        if best is None:
            return None
        return RuleChoice(action_code=best[0], rule_id=rule_id)

    def _use_active_skill_types(
        self,
        context: DecisionContext,
        *,
        skill_types: tuple[str, ...],
        rule_id: str,
    ) -> RuleChoice | None:
        if not skill_types:
            return None
        type_rank = {skill_type: len(skill_types) - index for index, skill_type in enumerate(skill_types)}
        active_name = self._active_character_name(context)
        best: tuple[int, tuple[int, ...]] | None = None
        for action_code, spec in self._pairs(context, kind=OptionKind.ACTION_USE_SKILL):
            skill = self.assets.skills.get(int(spec.subject_definition_id))
            if skill is None or skill.skill_type not in type_rank:
                continue
            if skill.character_name and skill.character_name != active_name:
                continue
            score = (
                type_rank[skill.skill_type],
                self._skill_tactical_score(skill, context=context),
                *self._spec_resource_score(context, spec, skill=skill),
                -self._total_dice_cost(skill.play_cost),
                -int(spec.subject_definition_id),
                -int(action_code),
            )
            if best is None or score > best[1]:
                best = (int(action_code), score)
        if best is None:
            return None
        return RuleChoice(action_code=best[0], rule_id=rule_id)

    def _use_active_skill_definition_ids(
        self,
        context: DecisionContext,
        *,
        skill_definition_ids: tuple[int, ...],
        rule_id: str,
    ) -> RuleChoice | None:
        if not skill_definition_ids:
            return None
        id_rank = {int(skill_id): len(skill_definition_ids) - index for index, skill_id in enumerate(skill_definition_ids)}
        best: tuple[int, tuple[int, ...]] | None = None
        for action_code, spec in self._pairs(context, kind=OptionKind.ACTION_USE_SKILL):
            skill_id = int(spec.subject_definition_id)
            if skill_id not in id_rank:
                continue
            skill = self.assets.skills.get(skill_id)
            score = (
                id_rank[skill_id],
                self._skill_tactical_score(skill, context=context),
                *self._spec_resource_score(context, spec, skill=skill),
                -(self._total_dice_cost(skill.play_cost) if skill is not None else len(spec.used_dice)),
                -int(action_code),
            )
            if best is None or score > best[1]:
                best = (int(action_code), score)
        if best is None:
            return None
        return RuleChoice(action_code=best[0], rule_id=rule_id)

    def _switch_to_named_targets(
        self,
        context: DecisionContext,
        *,
        target_names: tuple[str, ...],
        rule_id: str,
        require_safe_target: bool = False,
    ) -> RuleChoice | None:
        if not target_names:
            return None
        active = self._active_character(context)
        active_hp = active.health if active is not None else 0
        name_rank = {name: len(target_names) - index for index, name in enumerate(target_names)}
        best: tuple[int, tuple[int, int, int, int]] | None = None
        for action_code, spec in self._pairs(context, kind=OptionKind.ACTION_SWITCH_ACTIVE):
            name = self._switch_target_name(context, spec)
            if name not in name_rank:
                continue
            hp = self._switch_target_hp(context, spec)
            if require_safe_target and hp <= self.profile.rule_config.switch_hp_threshold and hp <= active_hp:
                continue
            score = (
                name_rank[name],
                1 if hp > active_hp else 0,
                hp,
                -int(action_code),
            )
            if best is None or score > best[1]:
                best = (int(action_code), score)
        if best is None:
            return None
        return RuleChoice(action_code=best[0], rule_id=rule_id)

    def _best_immediate_priority_score(self, context: DecisionContext) -> tuple[int, ...] | None:
        best: tuple[int, ...] | None = None
        for _, spec in self._pairs(context):
            score = self._immediate_priority_score(context, spec)
            if score is None:
                continue
            if best is None or score > best:
                best = score
        return best

    def _immediate_priority_score(
        self,
        context: DecisionContext,
        spec: LowLevelActionSpec,
    ) -> tuple[int, ...] | None:
        if spec.kind == OptionKind.ACTION_USE_SKILL:
            skill = self.assets.skills.get(int(spec.subject_definition_id))
            if skill is None:
                return None
            return (
                *self._skill_priority_components(context, skill),
                *self._spec_resource_score(context, spec, skill=skill),
                -int(spec.subject_definition_id),
                -int(spec.action_code),
            )
        if spec.kind == OptionKind.ACTION_PLAY_CARD:
            card = self.assets.cards.get(int(spec.subject_definition_id))
            if card is None:
                return None
            return (
                *self._card_priority_components(context, card),
                *self._spec_resource_score(context, spec, card=card),
                -int(spec.subject_definition_id),
                -int(spec.action_code),
            )
        return None

    def _best_projected_priority_score(
        self,
        context: DecisionContext,
        *,
        dice: tuple[int, ...],
        legal_skill_ids: set[int],
        legal_card_ids: set[int],
    ) -> tuple[int, ...] | None:
        best: tuple[int, ...] | None = None
        active = self._active_character(context)
        if active is not None:
            character = self.assets.characters.get(int(active.definition_id))
            if character is not None:
                for skill_id in character.skill_ids:
                    if int(skill_id) in legal_skill_ids:
                        continue
                    skill = self.assets.skills.get(int(skill_id))
                    if skill is None:
                        continue
                    if str(skill.skill_type) not in {"GCG_SKILL_TAG_Q", "GCG_SKILL_TAG_E", "GCG_SKILL_TAG_VEHICLE"}:
                        continue
                    score = self._projected_skill_priority_score(
                        context,
                        skill=skill,
                        dice=dice,
                        actor=active,
                    )
                    if score is not None and (best is None or score > best):
                        best = score
        for card_state in self._player(context).hand_cards:
            card = self.assets.cards.get(int(card_state.definition_id))
            if card is None or int(card.id) in legal_card_ids:
                continue
            if not self._is_tune_target_card(context, card):
                continue
            score = self._projected_card_priority_score(context, card=card, dice=dice)
            if score is not None and (best is None or score > best):
                best = score
        return best

    def _projected_skill_priority_score(
        self,
        context: DecisionContext,
        *,
        skill,
        dice: tuple[int, ...],
        actor,
    ) -> tuple[int, ...] | None:
        best_payment = self._best_payment_resource_score(
            context,
            dice=dice,
            play_cost=skill.play_cost,
            skill=skill,
            actor=actor,
        )
        if best_payment is None:
            return None
        return (
            *self._skill_priority_components(context, skill),
            *best_payment,
            -int(skill.id),
        )

    def _projected_card_priority_score(
        self,
        context: DecisionContext,
        *,
        card,
        dice: tuple[int, ...],
    ) -> tuple[int, ...] | None:
        best_payment = self._best_payment_resource_score(
            context,
            dice=dice,
            play_cost=card.play_cost,
            card=card,
            actor=self._active_character(context),
        )
        if best_payment is None:
            return None
        return (
            *self._card_priority_components(context, card),
            *best_payment,
            -int(card.id),
        )

    def _card_priority_components(self, context: DecisionContext, card) -> tuple[int, ...]:
        active_name = self._active_character_name(context)
        related_name = self._character_name(card.related_character_id) if card.related_character_id else ""
        tag_set = set(card.tags)
        tactical = self._description_tactical_score(card, context=context, mode="priority")
        tactical += self._card_context_bonus(card, context=context, mode="priority")
        is_active_match = 1 if related_name == active_name and related_name else 0
        is_carry = 1 if related_name in self.profile.rule_config.carry else 0
        is_bench = 1 if related_name in self.profile.rule_config.bench else 0
        is_setup = 1 if {"GCG_TAG_ALLY", "GCG_TAG_PLACE", "GCG_TAG_ITEM"} & tag_set else 0
        is_equipment = 1 if {"GCG_TAG_WEAPON", "GCG_TAG_ARTIFACT", "GCG_TAG_TALENT", "GCG_TAG_VEHICLE"} & tag_set else 0
        is_event = 1 if not (is_setup or is_equipment) else 0
        round_number = self._round_number(context)
        if "GCG_TAG_TALENT" in tag_set and is_active_match:
            tier = 7
        elif "GCG_TAG_TALENT" in tag_set and is_carry:
            tier = 6
        elif is_setup and round_number <= self.profile.rule_config.setup_rounds and tactical > 0:
            tier = 5
        elif is_equipment and (is_active_match or is_carry):
            tier = 5
        elif is_event and tactical >= 2:
            tier = 4
        elif is_carry or is_bench:
            tier = 3
        else:
            tier = 1
        return (
            tier,
            tactical,
            is_active_match + is_carry,
            is_event,
            -self._total_dice_cost(card.play_cost),
        )

    def _is_tune_target_card(self, context: DecisionContext, card) -> bool:
        if card is None:
            return False
        active_name = self._active_character_name(context)
        related_name = self._character_name(card.related_character_id) if card.related_character_id else ""
        if related_name != active_name:
            return False
        tag_set = set(card.tags)
        if "GCG_TAG_TALENT" in tag_set:
            return True
        tactical = self._description_tactical_score(card, context=context, mode="tune")
        tactical += self._card_context_bonus(card, context=context, mode="tune")
        return bool({"GCG_TAG_WEAPON", "GCG_TAG_ARTIFACT"} & tag_set) and tactical >= 4

    def _skill_priority_components(self, context: DecisionContext, skill) -> tuple[int, ...]:
        active_name = self._active_character_name(context)
        kind_rank = {
            "GCG_SKILL_TAG_Q": 8,
            "GCG_SKILL_TAG_E": 6,
            "GCG_SKILL_TAG_VEHICLE": 5,
            "GCG_SKILL_TAG_A": 4,
        }
        return (
            kind_rank.get(str(skill.skill_type), 2),
            self._skill_tactical_score(skill, context=context),
            1 if not skill.character_name or skill.character_name == active_name else 0,
            -self._total_dice_cost(skill.play_cost),
        )

    def _tuned_dice_outcomes(self, context: DecisionContext, spec: LowLevelActionSpec) -> tuple[tuple[int, ...], ...]:
        visible_dice = tuple(int(die) for die in self._player(context).dice)
        target_dice = int(spec.target_dice)
        outcomes: set[tuple[int, ...]] = set()
        for index, die in enumerate(visible_dice):
            if die in (target_dice, OMNI_DICE):
                continue
            updated = list(visible_dice)
            updated[index] = target_dice
            outcomes.add(tuple(sorted(updated)))
        return tuple(sorted(outcomes))

    def _tuning_intrinsic_score(self, context: DecisionContext, spec: LowLevelActionSpec) -> tuple[int, ...] | None:
        active = self._active_character(context)
        active_element = self._character_element(active.definition_id) if active is not None else ""
        desired_tags = {tag for tag in (active_element, *self.profile.rule_config.preferred_elements) if tag}
        if not desired_tags:
            return None
        target_dice = int(spec.target_dice)
        target_tag = self._die_to_tag(target_dice)
        if target_tag not in desired_tags:
            return None
        improves_alignment = 1 if any(die not in (target_dice, OMNI_DICE) for die in self._player(context).dice) else 0
        if not improves_alignment:
            return None
        discarded_definition_id = int(spec.discarded_card_definition_id or spec.subject_definition_id)
        discard_penalty = self._contextual_hand_card_keep_score(context, discarded_definition_id)
        target_priority = 2 if target_tag == active_element and active_element else 1
        return (
            target_priority,
            improves_alignment,
            -discard_penalty,
        )

    def _best_payment_resource_score(
        self,
        context: DecisionContext,
        *,
        dice: tuple[int, ...],
        play_cost: tuple[tuple[str, int], ...],
        card=None,
        skill=None,
        actor=None,
    ) -> tuple[int, ...] | None:
        if not self._non_dice_costs_met(context, play_cost=play_cost, actor=actor):
            return None
        requirements = self._build_required_cost(play_cost)
        if requirements is None:
            return None
        payments = _enumerate_action_dice_payments(tuple(int(value) for value in dice), requirements)
        if not payments:
            return None
        best: tuple[int, ...] | None = None
        for payment in payments:
            score = self._resource_score_for_payment(
                context,
                available_dice=dice,
                spent_dice=payment,
                card=card,
                skill=skill,
            )
            if best is None or score > best:
                best = score
        return best

    def _spec_resource_score(
        self,
        context: DecisionContext,
        spec: LowLevelActionSpec,
        *,
        card=None,
        skill=None,
    ) -> tuple[int, ...]:
        return self._resource_score_for_payment(
            context,
            available_dice=tuple(int(value) for value in self._player(context).dice),
            spent_dice=tuple(int(value) for value in spec.used_dice),
            card=card,
            skill=skill,
        )

    def _resource_score_for_payment(
        self,
        context: DecisionContext,
        *,
        available_dice: Iterable[int],
        spent_dice: Iterable[int],
        card=None,
        skill=None,
    ) -> tuple[int, ...]:
        focus_elements = self._ordered_focus_elements(context, card=card, skill=skill)
        available_counter = Counter(int(value) for value in available_dice)
        spent_counter = Counter(int(value) for value in spent_dice)
        remaining_counter = available_counter.copy()
        remaining_counter.subtract(spent_counter)
        primary = focus_elements[0] if focus_elements else ""
        focus_set = set(focus_elements)
        remaining_by_tag = Counter(
            self._die_to_tag(die)
            for die, count in remaining_counter.items()
            for _ in range(max(0, int(count)))
            if die != OMNI_DICE and self._die_to_tag(die)
        )
        spent_by_tag = Counter(
            self._die_to_tag(die)
            for die, count in spent_counter.items()
            for _ in range(max(0, int(count)))
            if die != OMNI_DICE and self._die_to_tag(die)
        )
        omni_remaining = max(0, int(remaining_counter.get(OMNI_DICE, 0)))
        omni_spent = max(0, int(spent_counter.get(OMNI_DICE, 0)))
        primary_remaining = int(remaining_by_tag.get(primary, 0)) if primary else 0
        primary_spent = int(spent_by_tag.get(primary, 0)) if primary else 0
        preferred_remaining = sum(
            int(remaining_by_tag.get(tag, 0))
            for tag in focus_elements[1:]
        )
        off_remaining = sum(
            int(count)
            for tag, count in remaining_by_tag.items()
            if tag and tag not in focus_set
        )
        off_spent = sum(
            int(count)
            for tag, count in spent_by_tag.items()
            if tag and tag not in focus_set
        )
        concentration = 4 * primary_remaining * primary_remaining
        concentration += 2 * sum(int(remaining_by_tag.get(tag, 0)) ** 2 for tag in focus_elements[1:])
        concentration += 5 * omni_remaining * omni_remaining
        weighted_alignment = 4 * primary_remaining + 2 * preferred_remaining + 5 * omni_remaining - 3 * off_remaining
        return (
            weighted_alignment,
            concentration,
            omni_remaining,
            primary_remaining,
            preferred_remaining,
            off_spent,
            -off_remaining,
            -omni_spent,
            -primary_spent,
        )

    def _ordered_focus_elements(
        self,
        context: DecisionContext,
        *,
        card=None,
        skill=None,
    ) -> tuple[str, ...]:
        ordered: list[str] = []
        seen: set[str] = set()

        def add(tag: str) -> None:
            if not tag or tag in seen:
                return
            ordered.append(tag)
            seen.add(tag)

        if skill is not None and getattr(skill, "character_id", 0):
            add(self._character_element(int(skill.character_id)))
        if card is not None and getattr(card, "related_character_id", None):
            add(self._character_element(int(card.related_character_id)))
        active = self._active_character(context)
        if active is not None:
            add(self._character_element(int(active.definition_id)))
        for tag in self.profile.rule_config.preferred_elements:
            add(str(tag))
        return tuple(ordered)

    def _build_required_cost(self, play_cost: tuple[tuple[str, int], ...]) -> tuple[SimpleNamespace, ...] | None:
        requirements: list[SimpleNamespace] = []
        for cost_type, count in play_cost:
            if int(count) <= 0:
                continue
            if _is_special_energy_cost_type(str(cost_type)):
                continue
            req_type = _COST_TYPE_TO_REQ.get(str(cost_type))
            if req_type is None:
                return None
            requirements.append(SimpleNamespace(type=int(req_type), count=int(count)))
        return tuple(requirements)

    def _non_dice_costs_met(
        self,
        context: DecisionContext,
        *,
        play_cost: tuple[tuple[str, int], ...],
        actor=None,
    ) -> bool:
        player = self._player(context)
        for cost_type, count in play_cost:
            if int(count) <= 0:
                continue
            if _is_special_energy_cost_type(str(cost_type)):
                if self._character_special_energy(
                    context,
                    actor=actor,
                    fallback_variable_name=_SPECIAL_ENERGY_VARIABLE_BY_COST_TYPE.get(str(cost_type), ""),
                ) < int(count):
                    return False
                continue
            if cost_type == "GCG_COST_ENERGY":
                if actor is None or int(getattr(actor, "energy", 0)) < int(count):
                    return False
            if cost_type == "GCG_COST_LEGEND" and bool(player.legend_used):
                return False
        return True

    def _total_dice_cost(self, play_cost: tuple[tuple[str, int], ...]) -> int:
        return sum(
            int(count)
            for cost_type, count in play_cost
            if str(cost_type).startswith("GCG_COST_DICE_")
        )

    def _pairs(
        self,
        context: DecisionContext,
        *,
        kind: OptionKind | None = None,
    ) -> list[tuple[int, LowLevelActionSpec]]:
        pairs = [
            (int(action_code), spec)
            for action_code, spec in zip(
                context.legal_low_level_codes,
                context.legal_low_level_specs,
                strict=False,
            )
        ]
        if kind is None:
            return pairs
        return [pair for pair in pairs if pair[1].kind == kind]

    def _round_number(self, context: DecisionContext) -> int:
        state = context.player_view or context.full_state
        return int(state.round_number)

    def _player(self, context: DecisionContext):
        state = context.player_view or context.full_state
        return state.players[context.acting_player]

    def _opponent(self, context: DecisionContext):
        state = context.player_view or context.full_state
        return state.players[1 - context.acting_player]

    def _active_character(self, context: DecisionContext):
        player = self._player(context)
        for character in player.characters:
            if character.is_active:
                return character
        return None

    def _active_character_name(self, context: DecisionContext) -> str:
        active = self._active_character(context)
        if active is None:
            return ""
        return self._character_name(active.definition_id)

    def _character_by_name(self, context: DecisionContext, name: str):
        if not name:
            return None
        for character in self._player(context).characters:
            if self._character_name(character.definition_id) == name:
                return character
        return None

    def _entity_variable_total(self, character, *, variable_name: str) -> int:
        if character is None or not variable_name:
            return 0
        return sum(int(getattr(entity, "variables", {}).get(variable_name, 0) or 0) for entity in character.entities)

    def _character_entity_variable_total(
        self,
        context: DecisionContext,
        *,
        variable_name: str,
        character_name: str | None = None,
        active_only: bool = False,
    ) -> int:
        if active_only:
            character = self._active_character(context)
        elif character_name is not None:
            character = self._character_by_name(context, character_name)
        else:
            character = None
        return self._entity_variable_total(character, variable_name=variable_name)

    def _character_nightsoul(self, context: DecisionContext, *, character_name: str) -> int:
        return self._character_entity_variable_total(
            context,
            variable_name="nightsoul",
            character_name=character_name,
        )

    def _active_nightsoul(self, context: DecisionContext) -> int:
        return self._character_entity_variable_total(
            context,
            variable_name="nightsoul",
            active_only=True,
        )

    def _character_special_energy_name(
        self,
        context: DecisionContext,
        *,
        character_name: str | None = None,
        active_only: bool = False,
        actor=None,
        fallback_variable_name: str = "",
    ) -> str:
        if actor is None:
            if active_only:
                actor = self._active_character(context)
            elif character_name is not None:
                actor = self._character_by_name(context, character_name)
        variable_name = str(getattr(actor, "special_energy_name", "") or "")
        if variable_name:
            return variable_name
        return str(fallback_variable_name or "")

    def _character_special_energy(
        self,
        context: DecisionContext,
        *,
        character_name: str | None = None,
        active_only: bool = False,
        actor=None,
        fallback_variable_name: str = "",
    ) -> int:
        variable_name = self._character_special_energy_name(
            context,
            character_name=character_name,
            active_only=active_only,
            actor=actor,
            fallback_variable_name=fallback_variable_name,
        )
        if actor is None:
            if active_only:
                actor = self._active_character(context)
            elif character_name is not None:
                actor = self._character_by_name(context, character_name)
        return self._entity_variable_total(actor, variable_name=variable_name)

    def _active_special_energy(self, context: DecisionContext) -> int:
        return self._character_special_energy(context, active_only=True)

    def _hand_card_names(self, context: DecisionContext) -> tuple[str, ...]:
        names: list[str] = []
        for card_state in self._player(context).hand_cards:
            card = self.assets.cards.get(int(card_state.definition_id))
            if card is not None:
                names.append(str(card.name))
        return tuple(names)

    def _hand_size(self, context: DecisionContext) -> int:
        return len(self._player(context).hand_cards)

    def _switch_target_name(self, context: DecisionContext, spec: LowLevelActionSpec) -> str:
        player = self._player(context)
        for target in spec.target_slots:
            if target.owner == "self" and target.zone == "character":
                if 0 <= int(target.index) < len(player.characters):
                    return self._character_name(player.characters[int(target.index)].definition_id)
        if 0 <= int(spec.choose_active_slot) < len(player.characters):
            return self._character_name(player.characters[int(spec.choose_active_slot)].definition_id)
        return ""

    def _switch_target_hp(self, context: DecisionContext, spec: LowLevelActionSpec) -> int:
        player = self._player(context)
        for target in spec.target_slots:
            if target.owner == "self" and target.zone == "character":
                if 0 <= int(target.index) < len(player.characters):
                    return int(player.characters[int(target.index)].health)
        if 0 <= int(spec.choose_active_slot) < len(player.characters):
            return int(player.characters[int(spec.choose_active_slot)].health)
        return 0

    def _character_hp_by_slot(self, context: DecisionContext, slot: int) -> int:
        player = self._player(context)
        if 0 <= int(slot) < len(player.characters):
            return int(player.characters[int(slot)].health)
        return 0

    def _character_rank(self, name: str) -> int:
        order = (self.profile.rule_config.opener, *self.profile.rule_config.carry, *self.profile.rule_config.bench)
        try:
            return order.index(name)
        except ValueError:
            return len(order) + 1

    def _character_name(self, definition_id: int | None) -> str:
        if definition_id is None:
            return ""
        character = self.assets.characters.get(int(definition_id))
        return character.name if character is not None else ""

    def _character_element(self, definition_id: int | None) -> str:
        if definition_id is None:
            return ""
        return self.assets.element_for_character(int(definition_id))

    def _character_element_by_name(self, name: str) -> str:
        if not name:
            return ""
        for definition_id in self.profile.characters:
            if self._character_name(definition_id) == name:
                return self._character_element(definition_id)
        return ""

    def _has_ready_burst(self, context: DecisionContext) -> bool:
        active_name = self._active_character_name(context)
        for _, spec in self._pairs(context, kind=OptionKind.ACTION_USE_SKILL):
            skill = self.assets.skills.get(int(spec.subject_definition_id))
            if skill is None:
                continue
            if skill.character_name == active_name and skill.skill_type == "GCG_SKILL_TAG_Q":
                return True
        return False

    def _opponent_active_character(self, context: DecisionContext):
        opponent = self._opponent(context)
        for character in opponent.characters:
            if character.is_active:
                return character
        return None

    def _opponent_active_health(self, context: DecisionContext) -> int:
        active = self._opponent_active_character(context)
        return int(active.health) if active is not None else 0

    def _opponent_active_aura(self, context: DecisionContext) -> int:
        active = self._opponent_active_character(context)
        return int(active.aura) if active is not None else 0

    def _opponent_support_cards(self, context: DecisionContext) -> tuple:
        cards = []
        for support in self._opponent(context).supports:
            definition_id = int(getattr(support, "definition_id", 0) or 0)
            if definition_id <= 0:
                continue
            card = self.assets.cards.get(definition_id)
            if card is not None:
                cards.append(card)
        return tuple(cards)

    def _opponent_support_names(self, context: DecisionContext) -> tuple[str, ...]:
        return tuple(str(card.name) for card in self._opponent_support_cards(context))

    def _opponent_summon_count(self, context: DecisionContext) -> int:
        return len(self._opponent(context).summons)

    def _opponent_combat_status_count(self, context: DecisionContext) -> int:
        return len(self._opponent(context).combat_statuses)

    def _opponent_public_threat(self, context: DecisionContext):
        support_pressure = 0
        for card in self._opponent_support_cards(context):
            name = str(card.name or "")
            desc = str(card.description or "")
            if name in {"凯瑟琳", "桓那兰那", "圣火竞技场"}:
                support_pressure += 2
                continue
            if "快速行动" in desc or "继续行动" in desc:
                support_pressure += 2
                continue
            if any(
                keyword in desc
                for keyword in (
                    "结束阶段",
                    "行动阶段开始时",
                    "每回合1次",
                    "生成",
                    "少花费1",
                    "抓1张",
                    "抓2张",
                    "抽1张",
                    "抽2张",
                )
            ):
                support_pressure += 1
        summon_count = self._opponent_summon_count(context)
        combat_status_count = self._opponent_combat_status_count(context)
        aura = self._opponent_active_aura(context)
        score = (
            min(4, summon_count * 2)
            + min(2, combat_status_count)
            + min(3, support_pressure)
            + (1 if aura > 0 else 0)
        )
        return SimpleNamespace(
            score=min(9, score),
            summon_count=summon_count,
            combat_status_count=combat_status_count,
            support_pressure=min(3, support_pressure),
            aura=int(aura),
            active_health=self._opponent_active_health(context),
            support_names=self._opponent_support_names(context),
        )

    def _reaction_switch_targets(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        style = str(self.profile.rule_config.style)
        if style in {"vaporize", "stack_burst"}:
            return (("GCG_TAG_ELEMENT_PYRO",), ("GCG_TAG_ELEMENT_HYDRO",))
        if style in {"freeze", "freeze_swirl"}:
            return (("GCG_TAG_ELEMENT_HYDRO",), ("GCG_TAG_ELEMENT_CRYO",))
        if style in {"superconduct", "superconduct_aggro", "superconduct_otk", "superconduct_burst"}:
            return (("GCG_TAG_ELEMENT_CRYO",), ("GCG_TAG_ELEMENT_ELECTRO",))
        if style == "electro_charged":
            return (("GCG_TAG_ELEMENT_HYDRO",), ("GCG_TAG_ELEMENT_ELECTRO",))
        if style == "hyperbloom":
            return (("GCG_TAG_ELEMENT_HYDRO", "GCG_TAG_ELEMENT_DENDRO"), ("GCG_TAG_ELEMENT_ELECTRO",))
        if style in {"crystallize", "crystallize_pressure"}:
            return (("GCG_TAG_ELEMENT_PYRO", "GCG_TAG_ELEMENT_HYDRO", "GCG_TAG_ELEMENT_CRYO", "GCG_TAG_ELEMENT_ELECTRO"), ("GCG_TAG_ELEMENT_GEO",))
        return ((), ())

    def _hand_card_keep_score(self, card_id: int) -> int:
        card = self.assets.cards.get(int(card_id))
        if card is None:
            return 0
        score = 0
        if "GCG_TAG_TALENT" in set(card.tags):
            score += 4
        if set(card.tags) & set(self.profile.rule_config.keep_tags):
            score += 2
        related_name = self._character_name(card.related_character_id) if card.related_character_id else ""
        if related_name in self.profile.rule_config.carry:
            score += 3
        elif related_name in self.profile.rule_config.bench:
            score += 1
        total_cost = sum(count for _, count in card.play_cost)
        if total_cost <= 2:
            score += 1
        elif total_cost >= 4:
            score -= 2
        score += self._description_static_keep_score(card)
        return score

    def _contextual_hand_card_keep_score(self, context: DecisionContext, card_id: int) -> int:
        score = self._hand_card_keep_score(card_id)
        card = self.assets.cards.get(int(card_id))
        if card is None:
            return score
        score += max(0, self._description_tactical_score(card, context=context, mode="priority") // 2)
        score += max(0, self._card_context_bonus(card, context=context, mode="priority"))
        if "GCG_TAG_VEHICLE" in set(card.tags):
            score += 2
        return score

    def _description_static_keep_score(self, card) -> int:
        desc = str(card.description or "")
        score = 0
        for keyword in (
            "立刻使用一次",
            "投掷阶段",
            "总是投出万能元素",
            "抓1张",
            "抓2张",
            "抓3张",
            "抽1张",
            "抽2张",
            "少花费1",
            "快速行动",
            "生成2个万能元素",
            "生成4个不同类型的基础元素骰",
            "可用次数+1",
        ):
            if keyword in desc:
                score += 2
        for keyword in ("造成的伤害+1", "穿透伤害+1", "治疗", "回复", "护盾", "结束阶段"):
            if keyword in desc:
                score += 1
        for keyword in ("双方各抓", "向双方牌组中放入", "随机效果"):
            if keyword in desc:
                score -= 2
        return score

    def _description_tactical_score(self, card, *, context: DecisionContext, mode: str) -> int:
        if card is None:
            return 0
        desc = str(card.description or "")
        round_number = int(context.player_view.round_number if context.player_view else context.full_state.round_number)
        score = 0
        if "立刻使用一次" in desc:
            score += 4
        if "快速行动" in desc:
            score += 3
        if any(keyword in desc for keyword in ("抓1张", "抓2张", "抓3张", "抽1张", "抽2张", "抽3张")):
            score += 3
        if "生成2个万能元素" in desc:
            score += 4
        if "生成4个不同类型的基础元素骰" in desc:
            score += 4
        if "生成1个万能元素" in desc:
            score += 3
        if "生成1个随机基础元素骰" in desc or "生成1个水元素骰" in desc or "生成1个火元素骰" in desc or "生成1个冰元素骰" in desc:
            score += 2
        if "下次使用技能时少花费1个元素骰" in desc or "下次使用「特技」少花费1个元素骰" in desc:
            score += 3
        if "下一次「元素战技」造成的伤害+2" in desc or "下一次引发火元素相关反应时" in desc:
            score += 3
        if "下一次「普通攻击」少花费" in desc or "下一次「普通攻击」造成的伤害+1" in desc:
            score += 2
        if "可用次数+1" in desc:
            score += 2
        if "投掷阶段" in desc or "总是投出万能元素" in desc:
            score += 4 if round_number <= 2 else 2
        if "造成的伤害+1" in desc or "穿透伤害+1" in desc:
            score += 3
        if "治疗" in desc or "回复" in desc or "护盾" in desc:
            score += 2
        if "结束阶段" in desc:
            score += 2
        if "切换为「出战角色」" in desc or "切换角色" in desc:
            score += 2
        if "随机效果" in desc:
            score -= 2
        if "双方各抓" in desc:
            score -= 4
        if "向双方牌组中放入" in desc:
            score -= 3
        if mode == "setup":
            if any(keyword in desc for keyword in ("投掷阶段", "总是投出万能元素", "结束阶段", "抓", "抽")):
                score += 3
        if mode == "equip":
            if "立刻使用一次" in desc:
                score += 3
            if "每回合1次" in desc:
                score += 2
        if mode == "event":
            if any(keyword in desc for keyword in ("造成1点", "造成2点", "治疗", "回复", "抓1张", "抓2张")):
                score += 2
        if self.profile.rule_config.style in {"vaporize", "stack_burst"}:
            if "结束阶段" in desc or "切换为「出战角色」" in desc:
                score += 2
        if self.profile.rule_config.style in {"superconduct", "superconduct_aggro", "superconduct_otk", "superconduct_burst"}:
            if "普通攻击" in desc or "雷元素相关反应" in desc or "物理伤害" in desc:
                score += 2
        if self.profile.rule_config.style in {"freeze", "freeze_swirl"}:
            if "冻结" in desc or "召唤物" in desc or "结束阶段" in desc:
                score += 2
        if self.profile.rule_config.style == "electro_charged":
            if "感电" in desc or "穿透伤害" in desc:
                score += 3
        if self.profile.rule_config.style in {"crystallize", "crystallize_pressure"}:
            if "裂晶弹片" in desc or "岩元素伤害" in desc or "护盾" in desc:
                score += 3
        return score

    def _public_response_bonus(
        self,
        *,
        desc: str,
        context: DecisionContext,
        mode: str,
        is_skill: bool = False,
    ) -> int:
        if not desc:
            return 0
        threat = self._opponent_public_threat(context)
        active = self._active_character(context)
        active_health = int(active.health) if active is not None else 0
        active_max_health = int(active.max_health) if active is not None else 0
        active_is_low = active is not None and active_health <= max(4, active_max_health // 2)
        bonus = 0
        if threat.score >= 3:
            if any(keyword in desc for keyword in ("治疗", "回复", "护盾")):
                bonus += 3 if active_is_low else 1
            if any(keyword in desc for keyword in ("快速行动", "继续行动", "切换角色", "切换为「出战角色」")):
                bonus += 1
            if any(
                keyword in desc
                for keyword in (
                    "生成2个万能元素",
                    "生成1个万能元素",
                    "生成4个不同类型的基础元素骰",
                    "下次使用技能时少花费1个元素骰",
                    "下次使用「特技」少花费1个元素骰",
                    "转换2个元素骰",
                )
            ):
                bonus += 1
            if (
                mode == "setup"
                and threat.score >= 5
                and any(keyword in desc for keyword in ("结束阶段", "行动阶段开始时", "投掷阶段"))
                and not any(
                    keyword in desc
                    for keyword in (
                        "治疗",
                        "回复",
                        "护盾",
                        "快速行动",
                        "继续行动",
                        "生成1个万能元素",
                        "生成2个万能元素",
                        "生成4个不同类型的基础元素骰",
                        "少花费1个元素骰",
                    )
                )
            ):
                bonus -= 1
            if is_skill and "准备" in desc and threat.score >= 6 and not any(
                keyword in desc for keyword in ("治疗", "回复", "护盾")
            ):
                bonus -= 1
        if threat.summon_count > 0 and "召唤物" in desc:
            bonus += min(2, threat.summon_count)
        if threat.active_health > 0 and threat.active_health <= 4:
            if (
                any(keyword in desc for keyword in ("造成的伤害+1", "穿透伤害+1", "立刻使用一次"))
                or ("造成" in desc and "伤害" in desc)
            ):
                bonus += 2
        if threat.support_pressure > 0 and any(
            name in threat.support_names for name in ("凯瑟琳", "桓那兰那", "圣火竞技场")
        ):
            if any(keyword in desc for keyword in ("快速行动", "继续行动", "切换角色", "治疗", "回复", "护盾")):
                bonus += 1
        return bonus

    def _skill_tactical_score(self, skill, *, context: DecisionContext) -> int:
        if skill is None:
            return 0
        desc = str(skill.description or "")
        score = 0
        if "造成" in desc and "伤害" in desc:
            score += 2
        if "切换" in desc:
            score += 2
        if "继续行动" in desc or "快速行动" in desc:
            score += 3
        if "转换2个元素骰" in desc and "万能元素" in desc:
            score += 4
        if "转换2个非万能元素骰" in desc and "造成" not in desc:
            score -= 3
        if any(keyword in desc for keyword in ("抓1张", "抓2张", "抓3张", "抽1张", "抽2张", "抽3张")):
            score += 3
        if "治疗" in desc or "回复" in desc or "护盾" in desc:
            score += 2
        if "准备" in desc:
            score += 1
        if "结束阶段" in desc:
            score += 1
        if skill.skill_type == "GCG_SKILL_TAG_Q":
            score += 2
        if self.profile.rule_config.style in {"vaporize", "stack_burst"} and "切换" in desc:
            score += 1
        if self.profile.rule_config.style in {"freeze", "freeze_swirl"} and "冻结" in desc:
            score += 2
        if self.profile.rule_config.style in {"freeze", "freeze_swirl"} and "物理伤害" in desc and "冻结" not in desc:
            score -= 2
        if self.profile.rule_config.style in {"superconduct", "superconduct_aggro", "superconduct_otk", "superconduct_burst"}:
            if "物理伤害" in desc or "雷元素" in desc:
                score += 1
        score += self._public_response_bonus(desc=desc, context=context, mode="skill", is_skill=True)
        return score

    def _card_context_bonus(self, card, *, context: DecisionContext, mode: str) -> int:
        if card is None:
            return 0
        bonus = 0
        deck_card_bonus = CARD_BONUS_RULES.get(self.profile.slug)
        if deck_card_bonus is not None:
            bonus += int(deck_card_bonus(self, card, context, mode))
        round_number = self._round_number(context)
        active_name = self._active_character_name(context)
        aura = self._opponent_active_aura(context)
        summon_count = len(self._player(context).summons)
        switch_count = len(self._pairs(context, kind=OptionKind.ACTION_SWITCH_ACTIVE))
        hand_size = self._hand_size(context)
        name = str(card.name)
        if name == "驰轮车·涉渡":
            if active_name == "玛薇卡":
                bonus += 4 if round_number <= 4 else 1
            elif round_number <= 3:
                bonus += 1
            if summon_count > 0:
                bonus += 3
            elif round_number >= 5:
                bonus -= 2
        if name == "驰轮车·跃升":
            bonus += 4 if aura > 0 or round_number >= 4 else -3
        if name == "驰轮车·疾驰":
            if round_number <= 2:
                bonus += 1
            elif hand_size <= 2 or round_number >= 5:
                bonus += 3
            else:
                bonus -= 2
        if name == "龙伙伴的聚餐":
            bonus += 2 if summon_count > 0 else -3
        if name == "元素共鸣：热诚之火":
            bonus += 4 if aura > 0 and active_name in {"玛薇卡", "烟绯", "可莉"} else -5
        if name == "最好的伙伴！":
            bonus += 2 if round_number <= 2 else -1
        if name == "交给我吧！":
            bonus += 2 if switch_count > 0 and round_number <= 3 else -2
        if name == "诸武相授":
            if active_name == "丝柯克" and round_number == 1:
                bonus += 1
            else:
                bonus -= 4
        if name == "裂晶弹片":
            if self.profile.rule_config.style in {"crystallize", "crystallize_pressure"}:
                bonus += 2
            else:
                bonus += 1 if round_number <= 2 else -2
        bonus += self._public_response_bonus(
            desc=str(card.description or ""),
            context=context,
            mode=mode,
        )
        return bonus

    def _die_to_tag(self, die: int) -> str:
        mapping = {
            1: "GCG_TAG_ELEMENT_CRYO",
            2: "GCG_TAG_ELEMENT_HYDRO",
            3: "GCG_TAG_ELEMENT_PYRO",
            4: "GCG_TAG_ELEMENT_ELECTRO",
            5: "GCG_TAG_ELEMENT_ANEMO",
            6: "GCG_TAG_ELEMENT_GEO",
            7: "GCG_TAG_ELEMENT_DENDRO",
        }
        return mapping.get(int(die), "")
