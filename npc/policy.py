"""``NpcPolicy`` — deterministic greedy + rotation opponent for PvE stages.

PLAN.md §4.4 (PvE-B2): the NPC enemy has no hand and no dice in the real game;
the engine only has *global* hand/dice counts, so we approximate it with a
policy that **never plays an action card** (nor elemental-tunes one away). Every
turn it spends its dice on the highest-damage legal skill; ties break by a fixed
``(character slot, skill order)`` order. Character switching is left entirely to
the engine (passives such as the Cryo Hilichurl Shooter's *Hide*) — the policy
never actively switches.

Determinism: ``choose`` is a pure function of the legal action set (plus, for
rotation mode, a small per-character pointer that advances only on skill use),
so the same seed reproduces the same game (``tests/test_npc_policy.py``).

Rotation configs add a *tempo* constraint on top of greedy: for the active
character, a fixed cycle of skill kinds (``"A"``/``"E"``/``"Q"``) is preferred
when that kind has a legal, damaging skill; otherwise the policy falls back to
greedy. Multiple named rotations are shipped in ``ROTATIONS``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from reps.action_adapter import BuiltDecisionContext
from reps.schema import DecisionType, LowLevelActionSpec, OptionKind

from .spec import NpcStage

# Named rotation cycles (skill kinds in engine order). ``None`` == pure greedy.
ROTATIONS: dict[str, tuple[str, ...] | None] = {
    "greedy": None,
    "cycle_a_e_q": ("A", "E", "Q"),
    "cycle_e_q_a": ("E", "Q", "A"),
    "cycle_q_e_a": ("Q", "E", "A"),
    "cycle_e_a": ("E", "A"),
}


def _spec_kind_order(spec: LowLevelActionSpec) -> tuple[int, int]:
    """Tie-break key fragment: ``(character slot hint, skill definition id)``."""
    return (0, int(spec.subject_definition_id))


@dataclass
class NpcPolicy:
    """Greedy (+ optional rotation) deterministic NPC policy.

    ``damage_by_skill_id`` maps a *skill definition id* to an estimated damage;
    ``skill_kind_by_skill_id`` maps it to ``"A"``/``"E"``/``"Q"`` (used by
    rotation mode). Both come from ``npc.spec`` via :func:`npc_policy_for_stage`.
    """

    name: str = "npc"
    damage_by_skill_id: dict[int, int] = field(default_factory=dict)
    skill_kind_by_skill_id: dict[int, str] = field(default_factory=dict)
    rotation: tuple[str, ...] | None = None
    rotation_name: str | None = None
    _rotation_pointer: dict[int, int] = field(default_factory=dict, repr=False)

    # -- Policy protocol --------------------------------------------------------
    def choose(self, built: BuiltDecisionContext) -> int:
        context = built.context
        pairs = list(
            zip(context.legal_low_level_codes, context.legal_low_level_specs, strict=False)
        )
        if not pairs:
            raise RuntimeError("no legal actions available")
        if context.request_type != DecisionType.ACTION:
            return self._choose_non_action(pairs)
        return self._choose_action(built, pairs)

    # -- non-action requests ----------------------------------------------------
    def _choose_non_action(self, pairs: list[tuple[int, LowLevelActionSpec]]) -> int:
        kinds = {spec.kind for _, spec in pairs}
        if OptionKind.CHOOSE_ACTIVE in kinds:
            return self._min_by(
                pairs,
                lambda spec: (spec.choose_active_slot if spec.choose_active_slot >= 0 else 1 << 30),
            )
        if OptionKind.REROLL_DICE in kinds:
            # Keep every die; the fastest deterministic choice (no card synergy
            # target since the NPC never plays cards).
            return self._min_by(
                pairs,
                lambda spec: int(spec.reroll_dice_mask).bit_count(),
            )
        if OptionKind.SWITCH_HANDS in kinds:
            return self._min_by(
                pairs,
                lambda spec: int(spec.switch_hand_slot_mask).bit_count(),
            )
        # select_card and anything unexpected: lowest action code (stable).
        return min(code for code, _ in pairs)

    # -- action requests --------------------------------------------------------
    def _choose_action(
        self,
        built: BuiltDecisionContext,
        pairs: list[tuple[int, LowLevelActionSpec]],
    ) -> int:
        skills = [(code, spec) for code, spec in pairs if spec.kind == OptionKind.ACTION_USE_SKILL]
        if not skills:
            # No affordable skill: the NPC never plays cards or switches, so it
            # yields the initiative.
            return self._declare_end(pairs)
        return self._select_skill(built, skills, pairs)

    def _select_skill(
        self,
        built: BuiltDecisionContext,
        skills: list[tuple[int, LowLevelActionSpec]],
        all_pairs: list[tuple[int, LowLevelActionSpec]],
    ) -> int:
        active_slot = self._active_slot(built)
        if self.rotation:
            preferred = self._preferred_rotation_kind(skills, active_slot)
            if preferred is not None:
                choices = [
                    (code, spec)
                    for code, spec in skills
                    if self.skill_kind_by_skill_id.get(int(spec.subject_definition_id)) == preferred
                ]
                damaging = [item for item in choices if self._damage(item[1]) > 0]
                if damaging:
                    return self._max_damage(damaging, active_slot)
        return self._max_damage(skills, active_slot)

    def _preferred_rotation_kind(
        self,
        skills: list[tuple[int, LowLevelActionSpec]],
        active_slot: int,
    ) -> str | None:
        assert self.rotation
        character_key = active_slot
        kinds = {self.skill_kind_by_skill_id.get(int(spec.subject_definition_id)) for _, spec in skills}
        kinds.discard(None)
        available = [kind for kind in self.rotation if kind in kinds]
        if not available:
            return None
        pointer = self._rotation_pointer.get(character_key, 0)
        preferred = self.rotation[pointer % len(self.rotation)]
        # Advance the pointer only over kinds this character can actually use.
        if preferred not in kinds:
            preferred = available[pointer % len(available)]
        self._rotation_pointer[character_key] = pointer + 1
        return preferred

    def _max_damage(
        self,
        skills: list[tuple[int, LowLevelActionSpec]],
        active_slot: int,
    ) -> int:
        def key(item: tuple[int, LowLevelActionSpec]) -> tuple[int, int, int]:
            code, spec = item
            _, skill_id = _spec_kind_order(spec)
            return (-self._damage(spec), active_slot, skill_id)

        return min(skills, key=key)[0]

    def _damage(self, spec: LowLevelActionSpec) -> int:
        return int(self.damage_by_skill_id.get(int(spec.subject_definition_id), 0))

    def _declare_end(self, pairs: list[tuple[int, LowLevelActionSpec]]) -> int:
        ends = [code for code, spec in pairs if spec.kind == OptionKind.ACTION_DECLARE_END]
        if ends:
            return min(ends)
        return min(code for code, _ in pairs)

    @staticmethod
    def _active_slot(built: BuiltDecisionContext) -> int:
        view = built.context.player_view
        if view is None:
            return 0
        player = view.players[int(built.context.acting_player)]
        for index, character in enumerate(player.characters):
            if bool(character.is_active):
                return int(index)
        return 0

    @staticmethod
    def _min_by(pairs: list[tuple[int, LowLevelActionSpec]], key) -> int:
        return min(pairs, key=lambda item: (key(item[1]), item[0]))[0]


def npc_policy_for_stage(
    stage: NpcStage,
    *,
    rotation: str | tuple[str, ...] | None = None,
) -> NpcPolicy:
    """Build the (greedy by default) NPC policy for one stage's enemy lineup.

    ``rotation`` accepts a name from :data:`ROTATIONS` or an explicit cycle of
    skill kinds. Any name is validated so a typo fails loudly instead of silently
    disabling the tempo constraint.
    """
    from .spec import stage_damage_table, stage_skill_kind_table

    rotation_cycle: tuple[str, ...] | None
    rotation_name: str | None
    if isinstance(rotation, str):
        if rotation not in ROTATIONS:
            raise KeyError(f"unknown rotation {rotation!r}; known: {sorted(ROTATIONS)}")
        rotation_cycle = ROTATIONS[rotation]
        rotation_name = rotation
    else:
        rotation_cycle = tuple(rotation) if rotation else None
        rotation_name = "custom" if rotation_cycle else None

    # Restrict both tables to the stage's enemy lineup so any skill id the NPC
    # can actually play resolves deterministically.
    damage = stage_damage_table(stage)
    kinds = stage_skill_kind_table(stage)
    return NpcPolicy(
        name=npc_name(stage, rotation_name=rotation_name),
        damage_by_skill_id=damage,
        skill_kind_by_skill_id=kinds,
        rotation=rotation_cycle,
        rotation_name=rotation_name,
    )


def npc_name(stage: NpcStage, *, rotation_name: str | None = None) -> str:
    suffix = f"#{rotation_name}" if rotation_name else ""
    return f"npc:{stage.name}{suffix}"
