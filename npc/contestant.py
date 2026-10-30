"""Contestant policy for the PvE-v1 gate (PLAN.md §4.4, D17/D19).

The gate plays a contestant deck against each stage's NPC lineup. The contestant
side needs to be *deterministic*, *pure-policy* (no LLM) and — unlike the
scripted aggro rules — able to satisfy the challenge objectives that require
playing specific cards (``play_equipment_card`` / ``play_event_cards``) or
bursts (``use_elemental_burst``). The B2/B3 smoke measured
``play_equipment_card`` = 0 for a scripted expert, which is a *policy* property,
not an engine limitation.

:class:`ContestantPolicy` therefore wraps a base deterministic policy (by
default the scripted ``superconduct_aggro`` expert rule, which plays the
cryo/electro superconduct rotation well) with a thin **objective injection**:

* play the stage's required equipment card (by kind: ``weapon``/``artifact``)
  when it is legal and not yet met this match;
* play the required event cards;
* use an elemental burst when the stage requires it and a burst is legal.

The injection is per-kind (an artifact play must not satisfy a weapon
objective) and purely a function of the legal action set plus a few per-match
counters, so the same seed reproduces the same game
(``tests/test_npc_contestant.py``).

``build_contestant_profile`` builds a *synthetic* deck profile with no
deck-specific rules; passing ``base_slug=None`` uses it (a generic expert chain)
instead of a scripted rule.

The policy is exposed through ``npc.eval`` as the ``policy0`` spec
``"contestant"`` (and ``"contestant_plain"`` for the same base without the
injection), which builds it for the stage being played.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from reps.action_adapter import BuiltDecisionContext
from reps.schema import DeckSpec, OptionKind

from .spec import NpcObjective, NpcStage

CONTESTANT_POLICY_SPEC = "contestant"
CONTESTANT_PLAIN_POLICY_SPEC = "contestant_plain"
DEFAULT_BASE_SLUG = "superconduct_aggro"


def _asset_catalog():
    from agents.scripted.assets import load_assets

    return load_assets()


def _card_kind(card_id: int) -> str:
    """Classify an action card id via the asset tags (see decklab.builder)."""
    from decklab.builder import card_kind

    return card_kind(card_id)


def _skill_is_burst(skill_id: int) -> bool:
    skill = _asset_catalog().skills.get(int(skill_id))
    return skill is not None and skill.skill_type == "GCG_SKILL_TAG_Q"


def _style_for_elements(elements: tuple[str, ...]) -> str:
    """Pick a reaction style for the generic rule chain from the deck elements.

    ``ExpertRuleAgent._reaction_switch_targets`` keys off ``rule_config.style``;
    a generic style disables reaction setup switching entirely.
    """
    present = set(elements)
    prefix = "GCG_TAG_ELEMENT_"
    if {f"{prefix}CRYO", f"{prefix}ELECTRO"} <= present:
        return "superconduct_aggro"
    if {f"{prefix}HYDRO", f"{prefix}CRYO"} <= present:
        return "freeze"
    if {f"{prefix}PYRO", f"{prefix}HYDRO"} <= present:
        return "vaporize"
    if {f"{prefix}HYDRO", f"{prefix}ELECTRO"} <= present:
        return "electro_charged"
    if f"{prefix}GEO" in present:
        return "crystallize"
    return "generic"


def build_contestant_profile(deck: DeckSpec):
    """A synthetic ``DeckProfile`` for ``deck`` with no deck-specific rules.

    ``agents.scripted.deck_rules`` is keyed by slug; the synthetic slug below is
    deliberately absent so ``ExpertRuleAgent._choose_deck_specific_action``
    returns ``None`` and the generic rule chain runs.
    """
    from agents.scripted.deck_rules.common import make_rule_config
    from agents.scripted.models import DeckProfile

    catalog = _asset_catalog()
    names = tuple(catalog.character(int(c)).name for c in deck.characters)
    elements = tuple(catalog.element_for_character(int(c)) for c in deck.characters)
    config = make_rule_config(
        slug="decklab_contestant",
        opener=names[0],
        carry=names,
        bench=names[1:],
        preferred_elements=elements,
        style=_style_for_elements(elements),
    )
    return DeckProfile(
        slug="decklab_contestant",
        name=deck.name,
        author="decklab",
        tags=(),
        share_code="",
        characters=tuple(int(c) for c in deck.characters),
        cards=tuple(int(c) for c in deck.cards),
        character_names=names,
        character_elements=elements,
        rule_config=config,
        talent_cards=(),
        support_cards=(),
        equipment_cards=(),
        event_cards=(),
    )


def _base_policy(deck: DeckSpec, base_slug: str | None, *, seed: int = 0):
    """Build the wrapped base policy: a scripted expert or a generic expert.

    ``seed`` is forwarded to the scripted agent's RNG (as ``envs.policy`` does
    for every other policy). Today the ported rules never consult their RNG, but
    passing an explicit deterministic seed keeps the policy reproducible if that
    ever changes (``random.Random(None)`` would seed from the OS).
    """
    if base_slug:
        from envs.policy import expert_policy

        return expert_policy(base_slug, seed=int(seed))
    from agents.scripted.agent import ExpertRuleAgent
    from envs.policy import ScriptedPolicy

    agent = ExpertRuleAgent(
        assets=_asset_catalog(), profile=build_contestant_profile(deck), seed=int(seed)
    )
    return ScriptedPolicy(agent, name="generic_contestant")


@dataclass
class ContestantPolicy:
    """Base deterministic player + challenge-objective injection (deterministic)."""

    base: object
    objectives: tuple[NpcObjective, ...] = ()
    inject_objectives: bool = True
    name: str = "contestant"
    # Per-kind equipment play counts: an artifact play must not satisfy a weapon
    # objective (and vice versa).
    _equipment_by_kind: dict[str, int] = field(default_factory=dict, repr=False)
    _events_played: int = field(default=0, repr=False)
    _bursts_used: int = field(default=0, repr=False)

    # -- objective helpers ------------------------------------------------------
    def _objective(self, objective_type: str) -> NpcObjective | None:
        for objective in self.objectives:
            if objective.type == objective_type:
                return objective
        return None

    @staticmethod
    def _cheapest(candidates: list[tuple[int, object]]) -> int:
        return min(candidates, key=lambda item: (len(item[1].used_dice), item[0]))[0]

    def _injected_action(self, built: BuiltDecisionContext) -> int | None:
        pairs = list(
            zip(built.context.legal_low_level_codes, built.context.legal_low_level_specs)
        )
        play = [(code, spec) for code, spec in pairs if spec.kind == OptionKind.ACTION_PLAY_CARD]
        skills = [(code, spec) for code, spec in pairs if spec.kind == OptionKind.ACTION_USE_SKILL]

        burst = self._objective("use_elemental_burst")
        if burst is not None and self._bursts_used < int(burst.count or 1):
            candidates = [
                (code, spec)
                for code, spec in skills
                if _skill_is_burst(int(spec.subject_definition_id))
            ]
            if candidates:
                return self._cheapest(candidates)

        equipment = self._objective("play_equipment_card")
        if equipment is not None and play:
            kind = equipment.kind or "equipment"
            if self._equipment_by_kind.get(kind, 0) < int(equipment.count or 1):
                candidates = [
                    (code, spec)
                    for code, spec in play
                    if _card_kind(int(spec.subject_definition_id)) == kind
                ]
                if candidates:
                    return self._cheapest(candidates)

        events = self._objective("play_event_cards")
        if events is not None and self._events_played < int(events.count or 1) and play:
            candidates = [
                (code, spec)
                for code, spec in play
                if _card_kind(int(spec.subject_definition_id)) == "event"
            ]
            if candidates:
                return self._cheapest(candidates)
        return None

    def _note_choice(self, built: BuiltDecisionContext, code: int) -> None:
        for candidate, spec in zip(
            built.context.legal_low_level_codes, built.context.legal_low_level_specs
        ):
            if int(candidate) != int(code):
                continue
            if spec.kind == OptionKind.ACTION_PLAY_CARD:
                kind = _card_kind(int(spec.subject_definition_id))
                if kind in ("weapon", "artifact", "talent"):
                    self._equipment_by_kind[kind] = self._equipment_by_kind.get(kind, 0) + 1
                elif kind == "event":
                    self._events_played += 1
            elif spec.kind == OptionKind.ACTION_USE_SKILL:
                if _skill_is_burst(int(spec.subject_definition_id)):
                    self._bursts_used += 1
            return

    # -- Policy protocol --------------------------------------------------------
    def choose(self, built: BuiltDecisionContext) -> int:
        context = built.context
        if context.request_type.value == "action" and self.inject_objectives:
            injected = self._injected_action(built)
            if injected is not None:
                self._note_choice(built, injected)
                return int(injected)
        code = int(self.base.choose(built))
        if context.request_type.value == "action":
            self._note_choice(built, code)
        return code


def build_contestant_policy(
    deck: DeckSpec,
    stage: NpcStage | None = None,
    *,
    inject_objectives: bool = True,
    base_slug: str | None = DEFAULT_BASE_SLUG,
    seed: int = 0,
) -> ContestantPolicy:
    """Build the gate contestant policy for ``deck`` (optionally stage-aware)."""
    return ContestantPolicy(
        base=_base_policy(deck, base_slug, seed=seed),
        objectives=tuple(stage.objectives) if stage is not None else (),
        inject_objectives=inject_objectives,
        name="contestant" if inject_objectives else "contestant_plain",
    )


__all__ = [
    "CONTESTANT_PLAIN_POLICY_SPEC",
    "CONTESTANT_POLICY_SPEC",
    "DEFAULT_BASE_SLUG",
    "ContestantPolicy",
    "build_contestant_policy",
    "build_contestant_profile",
]
