# Ported from Rebel_base_RL research/world_model (AGPL-3.0, github.com/.../Rebel_base_RL) by genshin-GITCG.
# Keep upstream semantics; adapt imports only. See NOTICE.md.
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Iterable

from reps.schema import DecisionContext

from ..models import CardInfo, DeckRuleConfig, RuleChoice

if TYPE_CHECKING:
    from ..agent import ExpertRuleAgent

DeckActionRule = Callable[["ExpertRuleAgent", DecisionContext], RuleChoice | None]
DeckChooseActiveRule = Callable[["ExpertRuleAgent", DecisionContext], RuleChoice | None]
DeckRerollRule = Callable[["ExpertRuleAgent", DecisionContext], RuleChoice | None]
DeckSelectCardRule = Callable[["ExpertRuleAgent", DecisionContext], RuleChoice | None]
DeckCardBonusRule = Callable[["ExpertRuleAgent", CardInfo | None, DecisionContext, str], int]


def make_rule_config(
    *,
    slug: str,
    opener: str,
    carry: tuple[str, ...],
    bench: tuple[str, ...],
    preferred_elements: tuple[str, ...],
    style: str,
    preferred_cards: tuple[str, ...] = (),
    keep_tags: tuple[str, ...] = ("GCG_TAG_TALENT", "GCG_TAG_ALLY", "GCG_TAG_PLACE"),
    switch_hp_threshold: int = 4,
    setup_rounds: int = 2,
) -> DeckRuleConfig:
    return DeckRuleConfig(
        slug=slug,
        opener=opener,
        carry=carry,
        bench=bench,
        preferred_elements=preferred_elements,
        style=style,
        preferred_cards=preferred_cards,
        keep_tags=keep_tags,
        switch_hp_threshold=switch_hp_threshold,
        setup_rounds=setup_rounds,
    )


def choose_elemental_focus_reroll(
    agent: "ExpertRuleAgent",
    context: DecisionContext,
    *,
    primary: Iterable[str],
    secondary: Iterable[str] = (),
    rule_id: str,
) -> RuleChoice | None:
    visible_dice = tuple(int(value) for value in context.request_payload.get("visible_dice", ()))
    if not visible_dice:
        return None
    primary_set = set(primary)
    secondary_set = set(secondary)
    desired = primary_set | secondary_set
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
        keep_primary = sum(1 for die in kept if agent._die_to_tag(die) in primary_set)
        keep_secondary = sum(1 for die in kept if agent._die_to_tag(die) in secondary_set)
        reroll_omni = sum(1 for die in rerolled if die == 8)
        reroll_primary = sum(1 for die in rerolled if agent._die_to_tag(die) in primary_set)
        reroll_secondary = sum(1 for die in rerolled if agent._die_to_tag(die) in secondary_set)
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
            keep_omni + 2 * keep_primary + keep_secondary,
            keep_primary,
            keep_secondary,
            reroll_off,
            -keep_off,
            -reroll_primary,
            -reroll_secondary,
            -len(rerolled),
        )
        if best_score is None or score > best_score:
            best_code = int(action_code)
            best_score = score
    if best_code is None:
        return None
    return RuleChoice(action_code=best_code, rule_id=rule_id)
