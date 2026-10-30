"""Synchronous single-match runner + the Player adapter that drives policies.

This is the WS0 core (PLAN.md): wrap ``gitcg`` so a full game can be played by
two arbitrary policies, with only ``notification.state``-derived observations
fed to the agents (no god view). It also records per-decision traces for the
coach/arena layers.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable

from gitcg import (
    ActionRequest,
    ActionResponse,
    ChooseActiveRequest,
    ChooseActiveResponse,
    CreateParam,
    Game,
    GameStatus,
    Player,
    RerollDiceRequest,
    RerollDiceResponse,
    SelectCardRequest,
    SelectCardResponse,
    SwitchHandsRequest,
    SwitchHandsResponse,
    low_level,
)

from reps.action_adapter import BuiltDecisionContext, build_decision_context
from reps.schema import DecisionType, DeckSpec, OptionKind, StateSnapshot
from reps.snapshot import snapshot_notification, snapshot_state

from .policy import Policy


@dataclass
class DecisionRecord:
    """One decision made during a match (for arena stats and coach consumption)."""

    player: int
    step_index: int
    request_type: str
    chosen_action_code: int
    chosen_label: str | None
    chosen_kind: str | None
    legal_action_codes: tuple[int, ...]
    legal_labels: tuple[str, ...]
    fallback: bool = False
    policy_error: str | None = None
    used_dice: tuple[int, ...] = ()
    player_view: dict[str, Any] | None = None
    # Additive (PvE-B3): the semantic subject of the chosen action. For
    # ``action_use_skill`` this is the skill definition id; for
    # ``action_play_card`` it is the card definition id. ``card_kind`` is only
    # resolved for played cards ("weapon"/"artifact"/"event"/"support"/...).
    subject_definition_id: int = 0
    card_kind: str | None = None


@dataclass
class MatchEvent:
    """One public engine event captured during a match (PvE-B3 objective judging).

    The engine's ``Notification.mutation`` stream carries the facts that the
    public state snapshots do not retain (burst usage, elemental reactions). We
    record a thin, JSON-safe projection of those mutations here. Attribution to a
    player is best-effort: ``skill_used`` carries ``who`` directly; reactions are
    attributed through the damage source instance id resolved against the latest
    public state.
    """

    kind: str  # "skill" | "reaction"
    player: int | None = None
    round_number: int = 0
    skill_definition_id: int = 0
    skill_type: str | None = None  # "normal"/"elemental"/"burst"/"passive"/...
    reaction_type: str | None = None  # e.g. "superconduct"/"vaporize"/...
    source_definition_id: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "player": self.player,
            "round_number": self.round_number,
            "skill_definition_id": self.skill_definition_id,
            "skill_type": self.skill_type,
            "reaction_type": self.reaction_type,
            "source_definition_id": self.source_definition_id,
        }


def _enum_name_map(enum_type: Any) -> dict[int, str]:
    return _enum_name_map_cached(str(enum_type.DESCRIPTOR.full_name), enum_type)


@lru_cache(maxsize=16)
def _enum_name_map_cached(_full_name: str, enum_type: Any) -> dict[int, str]:
    return {int(value.number): str(value.name) for value in enum_type.DESCRIPTOR.values}


_SKILL_TYPE_NAMES = {
    "SKILL_TYPE_TRIGGERED": "triggered",
    "SKILL_TYPE_CHARACTER_PASSIVE": "passive",
    "SKILL_TYPE_NORMAL": "normal",
    "SKILL_TYPE_ELEMENTAL": "elemental",
    "SKILL_TYPE_BURST": "burst",
    "SKILL_TYPE_TECHNIQUE": "technique",
    "SKILL_TYPE_TRIGGERED_FROM_ITS_ATTACHMENT": "triggered_attachment",
}


def _reaction_name(value: int) -> str | None:
    from gitcg.proto import enums_pb2

    if int(value) == 0:
        return None
    name = _enum_name_map(enums_pb2.ReactionType).get(int(value), str(value))
    return name.removeprefix("REACTION_TYPE_").lower()


def _skill_type_name(value: int) -> str | None:
    from gitcg.proto import mutation_pb2

    name = _enum_name_map(mutation_pb2.SkillType).get(int(value), str(value))
    return _SKILL_TYPE_NAMES.get(name, name.removeprefix("SKILL_TYPE_").lower())


@lru_cache(maxsize=1)
def _talent_card_ids() -> frozenset[int]:
    """Definition ids of talent cards (the engine type cannot distinguish them).

    The engine reports a talent card in hand as ``ENTITY_TYPE_EVENT_CARD`` (it is
    played like an event) and there is no talent ``EquipmentType``, so the
    entity type/equipment pair alone cannot tell a talent from a plain event. We
    fall back to the scripted-expert asset catalog's ``GCG_TAG_TALENT`` tag —
    the same source ``decklab.builder.card_kind`` uses — so the *recorded* card
    kind matches the contestant's selection vocabulary
    (``weapon|artifact|talent|support|event``). Without this, a
    ``play_equipment_card`` objective with ``kind="talent"`` could never pass.
    """
    from agents.scripted.assets import load_assets

    return frozenset(
        int(card_id)
        for card_id, card in load_assets().cards.items()
        if "GCG_TAG_TALENT" in set(card.tags)
    )


def _card_kind_from_entity(
    entity_type: int, equipment: int, *, definition_id: int = 0
) -> str | None:
    """Map an engine ``EntityState`` type/equipment pair to a card-kind label."""
    from gitcg.proto import state_pb2

    if definition_id and int(definition_id) in _talent_card_ids():
        return "talent"
    if int(entity_type) == int(state_pb2.EntityType.ENTITY_TYPE_EVENT_CARD):
        return "event"
    if int(entity_type) == int(state_pb2.EntityType.ENTITY_TYPE_SUPPORT):
        return "support"
    if int(entity_type) == int(state_pb2.EntityType.ENTITY_TYPE_EQUIPMENT):
        equipment_name = state_pb2.EquipmentType.Name(int(equipment))
        return {
            "EQUIPMENT_TYPE_WEAPON": "weapon",
            "EQUIPMENT_TYPE_ARTIFACT": "artifact",
            "EQUIPMENT_TYPE_TECHNIQUE": "technique",
        }.get(equipment_name, "equipment")
    return None


@dataclass
class MatchRecord:
    deck0: str
    deck1: str
    seed: int
    winner: int | None
    rounds: int
    decisions0: int
    decisions1: int
    fallbacks0: int
    fallbacks1: int
    io_errors0: tuple[str, ...]
    io_errors1: tuple[str, ...]
    error: str | None = None
    truncated: bool = False
    wall_seconds: float = 0.0
    final_state_json: str | None = None
    decisions: list[DecisionRecord] = field(default_factory=list)
    events: list[MatchEvent] = field(default_factory=list)

    @property
    def matchup(self) -> str:
        return f"{self.deck0}__vs__{self.deck1}"

    def as_dict(
        self, *, include_decisions: bool = False, include_events: bool = False
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "deck0": self.deck0,
            "deck1": self.deck1,
            "matchup": self.matchup,
            "seed": self.seed,
            "winner": self.winner,
            "rounds": self.rounds,
            "decisions0": self.decisions0,
            "decisions1": self.decisions1,
            "fallbacks0": self.fallbacks0,
            "fallbacks1": self.fallbacks1,
            "io_errors0": list(self.io_errors0),
            "io_errors1": list(self.io_errors1),
            "error": self.error,
            "truncated": self.truncated,
            "wall_seconds": self.wall_seconds,
        }
        if include_decisions:
            payload["decisions"] = [asdict_decision(d) for d in self.decisions]
        if include_events:
            payload["events"] = [event.as_dict() for event in self.events]
        return payload


def asdict_decision(record: DecisionRecord) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(record)


def _fallback_code(built: BuiltDecisionContext) -> int:
    codes = list(built.context.legal_low_level_codes)
    if not codes:
        return -1
    for code, spec in zip(codes, built.context.legal_low_level_specs):
        if spec.kind == OptionKind.ACTION_DECLARE_END:
            return int(code)
    return int(codes[0])


def _is_policy_quit(exc: BaseException) -> bool:
    """``agents.human.HumanQuit``？（延迟导入：envs 不在导入期依赖 agents）"""
    if type(exc).__name__ != "HumanQuit":
        # 名字预筛：普通策略异常不该为它去 import 一次
        return False
    from agents.human import HumanQuit

    return isinstance(exc, HumanQuit)


class PolicyPlayer(Player):
    """A ``gitcg.Player`` that delegates every request to a ``Policy``."""

    def __init__(
        self,
        who: int,
        policy: Policy,
        *,
        recorder: list[DecisionRecord] | None = None,
        record_views: bool = False,
        full_state_provider: Callable[[], str | None] | None = None,
        metadata: dict[str, Any] | None = None,
        event_sink: list[MatchEvent] | None = None,
    ) -> None:
        self.who = int(who)
        self.policy = policy
        self.recorder = recorder
        self.record_views = bool(record_views)
        self.full_state_provider = full_state_provider
        self.metadata = metadata or {}
        self.event_sink = event_sink
        self._owner_by_entity_id: dict[int, int] = {}
        self.notification: Any = None
        self.io_errors: list[str] = []
        self.decisions = 0
        self.fallbacks = 0
        self.quit_requested = False
        self._step_index = 0

    # -- gitcg.Player interface -------------------------------------------------
    def on_notify(self, notification: Any) -> None:
        self.notification = notification
        if self.event_sink is not None:
            self._collect_events(notification)

    def on_io_error(self, error_msg: str) -> None:
        self.io_errors.append(str(error_msg))

    def _register_entity_owners(self, state: Any) -> None:
        """Record which player owns each entity id (best-effort attribution).

        Beyond the player-level ``character``/``summon``/``support``/
        ``combat_status`` entities, characters carry *attached* entities
        (statuses/equipment) under ``CharacterState.entity``; those ids can be a
        damage ``source_id`` too, so register them (and their attachments) as
        well. Registration is additive across notifications, so an entity that
        has already left the state still resolves to its owner.
        """
        for who in (0, 1):
            player = state.player[who]
            for field in ("character", "summon", "support", "combat_status"):
                for entity in getattr(player, field):
                    self._owner_by_entity_id[int(entity.id)] = who
                    for attachment in getattr(entity, "attachment", ()):
                        self._owner_by_entity_id[int(attachment.id)] = who
            for character in player.character:
                for entity in getattr(character, "entity", ()):
                    self._owner_by_entity_id[int(entity.id)] = who
                    for attachment in getattr(entity, "attachment", ()):
                        self._owner_by_entity_id[int(attachment.id)] = who

    def _collect_events(self, notification: Any) -> None:
        """Project the engine's public mutation stream into ``MatchEvent``s.

        Only one player needs a sink; the engine delivers public mutations to
        both players, so ``run_match`` attaches it to a single ``PolicyPlayer``
        to avoid double counting.
        """
        sink = self.event_sink
        if sink is None:
            return
        state = getattr(notification, "state", None)
        if state is not None:
            self._register_entity_owners(state)
        round_number = int(getattr(state, "round_number", 0)) if state is not None else 0
        mutations = list(getattr(notification, "mutation", ()))
        # The engine also counts reactions triggered by a *pure elemental
        # application* (``applyAura`` with ``!opt.fromDamage``; mutator.ts
        # ``recordReaction``) — not just by damage. Those mutations carry no
        # source id, but the engine emits them after the ``skill_used`` that
        # applied the element, so attribute each to the **nearest preceding**
        # ``skill_used`` in the batch (``None`` if none precedes it). Using the
        # batch's *last* skill would misattribute a cross-player batch (end-phase
        # summon ticks interleave sides).
        last_skill_who: int | None = None
        for mutation in mutations:
            which = mutation.WhichOneof("mutation")
            if which == "skill_used":
                skill = mutation.skill_used
                last_skill_who = int(skill.who)
                sink.append(
                    MatchEvent(
                        kind="skill",
                        player=int(skill.who),
                        round_number=round_number,
                        skill_definition_id=int(skill.skill_definition_id),
                        skill_type=_skill_type_name(int(skill.skill_type)),
                        source_definition_id=int(skill.caller_definition_id),
                    )
                )
            elif which == "damage":
                damage = mutation.damage
                if int(damage.reaction_type) == 0:
                    continue
                sink.append(
                    MatchEvent(
                        kind="reaction",
                        player=self._owner_by_entity_id.get(int(damage.source_id)),
                        round_number=round_number,
                        reaction_type=_reaction_name(int(damage.reaction_type)),
                        source_definition_id=int(damage.source_definition_id),
                    )
                )
            elif which == "apply_aura":
                aura = mutation.apply_aura
                if int(aura.reaction_type) == 0:
                    continue
                sink.append(
                    MatchEvent(
                        kind="reaction",
                        player=last_skill_who,
                        round_number=round_number,
                        reaction_type=_reaction_name(int(aura.reaction_type)),
                        # ``applyAura`` carries no source entity; the target is
                        # not a "source", so leave the source definition empty.
                        source_definition_id=0,
                    )
                )

    def on_action(self, request: ActionRequest) -> ActionResponse:
        built, code = self._decide(DecisionType.ACTION, request)
        payload = self._payload(built, code)
        return ActionResponse(
            chosen_action_index=int(payload.get("chosen_action_index", 0)),
            used_dice=[int(d) for d in payload.get("used_dice", ())],
        )

    def on_choose_active(self, request: ChooseActiveRequest) -> ChooseActiveResponse:
        built, code = self._decide(DecisionType.CHOOSE_ACTIVE, request)
        payload = self._payload(built, code)
        return ChooseActiveResponse(
            active_character_id=int(
                payload.get("active_character_id", request.candidate_ids[0])
            )
        )

    def on_reroll_dice(self, request: RerollDiceRequest) -> RerollDiceResponse:
        built, code = self._decide(DecisionType.REROLL_DICE, request)
        payload = self._payload(built, code)
        return RerollDiceResponse(
            dice_to_reroll=[int(d) for d in payload.get("dice_to_reroll", ())]
        )

    def on_select_card(self, request: SelectCardRequest) -> SelectCardResponse:
        built, code = self._decide(DecisionType.SELECT_CARD, request)
        payload = self._payload(built, code)
        return SelectCardResponse(
            selected_definition_id=int(
                payload.get("selected_definition_id", request.candidate_definition_ids[0])
            )
        )

    def on_switch_hands(self, request: SwitchHandsRequest) -> SwitchHandsResponse:
        built, code = self._decide(DecisionType.SWITCH_HANDS, request)
        payload = self._payload(built, code)
        return SwitchHandsResponse(
            removed_hand_ids=[int(c) for c in payload.get("removed_hand_ids", ())]
        )

    # -- internals --------------------------------------------------------------
    def _payload(self, built: BuiltDecisionContext, code: int) -> dict[str, Any]:
        payload = built.payload_by_low_level_code.get(int(code))
        if payload is None:
            payload = built.payload_by_low_level_code.get(_fallback_code(built), {})
        return payload

    def _build(self, request_type: DecisionType, request: Any) -> BuiltDecisionContext:
        if self.notification is None:
            raise RuntimeError("decision requested before any notification")
        view = snapshot_notification(self.notification)
        full_state = view
        full_state_json = None
        if self.full_state_provider is not None:
            try:
                full_state_json = self.full_state_provider()
                if full_state_json:
                    # God view (training targets / privileged state). Agent inputs
                    # still use ``player_view`` below, so this does not leak.
                    full_state = snapshot_state(None, full_state_json)
            except Exception:
                full_state_json = None
                full_state = view
        return build_decision_context(
            acting_player=self.who,
            request_type=request_type,
            request=request,
            full_state=full_state,
            player_view=view,
            full_state_json=full_state_json,
            step_index=self._step_index,
        )

    def _decide(self, request_type: DecisionType, request: Any) -> tuple[BuiltDecisionContext, int]:
        self._step_index += 1
        self.decisions += 1
        built = self._build(request_type, request)
        policy_error: str | None = None
        code = -1
        try:
            code = int(self.policy.choose(built))
        except Exception as exc:  # callback exceptions are swallowed by cffi; capture here
            policy_error = f"{type(exc).__name__}: {exc}"
            if _is_policy_quit(exc):
                # 人机对局里人按 q/Ctrl-C：不是错误，是「退出本局」的请求。
                # run_match 在这一步结束后跳出循环，该局不进胜率/评分。
                self.quit_requested = True
        legal = set(int(c) for c in built.context.legal_low_level_codes)
        fallback = False
        if code not in legal:
            fallback = True
            self.fallbacks += 1
            code = _fallback_code(built)
        if self.recorder is not None:
            labels = [spec.label for spec in built.context.legal_low_level_specs]
            spec = self._spec_for(built, code)
            payload = built.payload_by_low_level_code.get(int(code), {})
            subject_definition_id = int(getattr(spec, "subject_definition_id", 0))
            card_kind = None
            if getattr(spec, "kind", None) == OptionKind.ACTION_PLAY_CARD:
                card_kind = self._resolve_card_kind(subject_definition_id)
            self.recorder.append(
                DecisionRecord(
                    player=self.who,
                    step_index=self._step_index,
                    request_type=request_type.value,
                    chosen_action_code=int(code),
                    chosen_label=getattr(spec, "label", None),
                    chosen_kind=getattr(getattr(spec, "kind", None), "value", None),
                    legal_action_codes=tuple(int(c) for c in built.context.legal_low_level_codes),
                    legal_labels=tuple(str(label) for label in labels),
                    fallback=fallback,
                    policy_error=policy_error,
                    used_dice=tuple(int(d) for d in payload.get("used_dice", ())),
                    player_view=_snapshot_to_dict(snapshot_notification(self.notification))
                    if self.record_views
                    else None,
                    subject_definition_id=subject_definition_id,
                    card_kind=card_kind,
                )
            )
        return built, code

    def _resolve_card_kind(self, definition_id: int) -> str | None:
        """Card kind of a played card, read from the acting player's hand."""
        notification = self.notification
        if notification is None or int(definition_id) <= 0:
            return None
        try:
            hand = notification.state.player[self.who].hand_card
        except Exception:
            return None
        for card in hand:
            if int(card.definition_id) == int(definition_id):
                return _card_kind_from_entity(
                    int(card.type), int(card.equipment), definition_id=int(definition_id)
                )
        return None

    @staticmethod
    def _spec_for(built: BuiltDecisionContext, code: int):
        for candidate, spec in zip(
            built.context.legal_low_level_codes, built.context.legal_low_level_specs
        ):
            if int(candidate) == int(code):
                return spec
        return None


def _snapshot_to_dict(snapshot: StateSnapshot) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(snapshot)


def _build_create_param(
    deck0: DeckSpec,
    deck1: DeckSpec,
    *,
    seed: int,
    version: str | None = None,
    deterministic_shuffle: bool = True,
) -> CreateParam:
    create_param = CreateParam(version=version)
    create_param.set_characters(0, list(deck0.characters))
    create_param.set_characters(1, list(deck1.characters))
    # The engine's pile shuffle uses JS Math.random() and is *not* driven by the
    # state random seed (packages/core/src/utils.ts `shuffle`). To keep "same
    # seed -> same game" (M0 acceptance) we pre-shuffle the pile in Python with a
    # seeded RNG and tell the engine not to shuffle it again.
    if deterministic_shuffle:
        create_param.set_cards(0, _seeded_pile(deck0, seed, who=0))
        create_param.set_cards(1, _seeded_pile(deck1, seed, who=1))
        create_param.set_attr(low_level.ATTR_CREATEPARAM_NO_SHUFFLE_0, 1)
        create_param.set_attr(low_level.ATTR_CREATEPARAM_NO_SHUFFLE_1, 1)
    else:
        create_param.set_cards(0, list(deck0.cards))
        create_param.set_cards(1, list(deck1.cards))
    create_param.set_attr(low_level.ATTR_STATE_CONFIG_RANDOM_SEED, int(seed))
    return create_param


def _seeded_pile(spec: DeckSpec, seed: int, *, who: int) -> list[int]:
    from common.seeding import derive_seed

    cards = list(int(c) for c in spec.cards)
    rng = random.Random(derive_seed(seed, "pile", who, spec.name))
    rng.shuffle(cards)
    return cards


def run_match(
    deck0: DeckSpec,
    deck1: DeckSpec,
    policy0: Policy,
    policy1: Policy,
    *,
    seed: int,
    version: str | None = None,
    max_steps: int = 5000,
    record_decisions: bool = False,
    record_views: bool = False,
    record_final_state: bool = False,
    record_events: bool = False,
    deterministic_shuffle: bool = True,
    full_state_for_agents: bool = False,
) -> MatchRecord:
    """Play one full game synchronously in the current thread.

    Both policies are driven from ``notification.state`` observations only. A
    ``max_steps`` guard marks abnormally long games as ``truncated`` instead of
    hanging the caller.

    With ``deterministic_shuffle`` (default) the pile is pre-shuffled in Python
    from ``seed`` so identical inputs reproduce an identical game; see
    ``_build_create_param``. The process-global action codebook is reset so codes
    do not depend on earlier matches in the same process (see
    ``reps.action_hierarchy.reset_default_hierarchical_action_codebook``).
    """
    from reps.action_hierarchy import reset_default_hierarchical_action_codebook

    reset_default_hierarchical_action_codebook()
    create_param = _build_create_param(
        deck0,
        deck1,
        seed=seed,
        version=version,
        deterministic_shuffle=deterministic_shuffle,
    )
    game = Game(create_param=create_param)
    decisions: list[DecisionRecord] = [] if record_decisions else []
    events: list[MatchEvent] = [] if record_events else []
    error: str | None = None
    truncated = False
    players: list[PolicyPlayer] = []
    full_state_provider = (lambda: game.state().json()) if full_state_for_agents else None
    for who, policy in ((0, policy0), (1, policy1)):
        recorder = decisions if record_decisions else None
        # Attach the event sink to a single player: the engine delivers public
        # mutations to both, so collecting from one avoids double counting.
        event_sink = events if (record_events and who == 0) else None
        players.append(
            PolicyPlayer(
                who,
                policy,
                recorder=recorder,
                record_views=record_views,
                full_state_provider=full_state_provider,
                event_sink=event_sink,
            )
        )
    game.set_player(0, players[0])
    game.set_player(1, players[1])

    start = time.perf_counter()
    steps = 0
    quit_by: int | None = None
    try:
        game.start()
        while game.is_running():
            if steps >= max_steps:
                truncated = True
                break
            game.step()
            steps += 1
            # 策略请求退出（人机对局里人输入 q / Ctrl-C）：立刻跳出，不往下走。
            quit_by = next((p.who for p in players if p.quit_requested), None)
            if quit_by is not None:
                break
    except Exception as exc:  # engine/IO failures are otherwise swallowed by cffi
        error = f"{type(exc).__name__}: {exc}"

    wall = time.perf_counter() - start
    winner: int | None = None
    rounds = 0
    final_json = None
    try:
        winner = game.winner()
        rounds = game.round_number()
        if record_final_state:
            final_json = game.state().json()
    except Exception:
        pass
    if game.status() == GameStatus.ABORTED and error is None:
        try:
            error = f"aborted: {game.error()}"
        except Exception:
            error = "aborted"
    if quit_by is not None:
        # 中途退出没有胜负：单独记 error（评分口径过滤 error/truncated），
        # 否则引擎留下的半个状态会被当成「对方胜」写进胜率。
        error = f"human quit: P{quit_by}"
        winner = None

    return MatchRecord(
        deck0=deck0.name,
        deck1=deck1.name,
        seed=int(seed),
        winner=winner,
        rounds=int(rounds),
        decisions0=players[0].decisions,
        decisions1=players[1].decisions,
        fallbacks0=players[0].fallbacks,
        fallbacks1=players[1].fallbacks,
        io_errors0=tuple(players[0].io_errors),
        io_errors1=tuple(players[1].io_errors),
        error=error,
        truncated=truncated,
        wall_seconds=wall,
        final_state_json=final_json,
        decisions=decisions,
        events=events,
    )
