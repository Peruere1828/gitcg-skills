"""Challenge-objective judging for NPC PvE stages (PLAN.md §4.4, PvE-B3).

Given one finished match (``envs.match.MatchRecord``) and the stage's
``NpcSpec``, decide whether each challenge objective was met and how many stars
that is (all objectives met == three stars).

Objective kinds and where the evidence comes from:

======================  =====================================================
objective               evidence
======================  =====================================================
``win``                 ``MatchRecord.winner`` (contestant is player 0)
``win_within_rounds``   ``winner`` + ``MatchRecord.rounds`` <= ``n``
``defeated_less_than``  ``winner`` + defeated contestant characters read from
                        ``MatchRecord.final_state_json`` (needs
                        ``record_final_state=True``)
``use_elemental_burst`` ``MatchEvent(kind="skill", skill_type="burst")`` for
                        player 0 (needs ``record_events=True``)
``trigger_elemental_reactions``
                        ``MatchEvent(kind="reaction", player=0)`` (needs
                        ``record_events=True``)
``play_equipment_card`` ``DecisionRecord(chosen_kind="action_play_card",
                        card_kind=<kind>)`` for player 0 (needs
                        ``record_decisions=True``)
``play_event_cards``    ``DecisionRecord(..., card_kind="event")`` for player 0
======================  =====================================================

So a full objective judgement needs a match run with
``record_decisions=True, record_events=True, record_final_state=True`` — exactly
what :func:`npc.eval.run_npc_eval` sets up.
"""

from __future__ import annotations

from dataclasses import dataclass

from envs.match import DecisionRecord, MatchEvent, MatchRecord
from reps.snapshot import snapshot_state

from .spec import NpcObjective, NpcStage

CONTESTANT = 0  # the deck under evaluation is always player 0 (NPC = player 1)


@dataclass(frozen=True)
class ObjectiveResult:
    type: str
    achieved: bool
    detail: str = ""
    text: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "type": self.type,
            "achieved": bool(self.achieved),
            "detail": self.detail,
            "text": self.text,
        }


@dataclass(frozen=True)
class StarResult:
    objectives: tuple[ObjectiveResult, ...]
    stars: int
    three_star: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "objectives": [objective.as_dict() for objective in self.objectives],
            "stars": int(self.stars),
            "three_star": bool(self.three_star),
        }


def defeated_count(final, player: int) -> int:
    """Number of defeated characters on ``player``'s side in a final snapshot."""
    if len(final.players) <= int(player):
        return 0
    return sum(1 for character in final.players[int(player)].characters if character.defeated)


def _defeated_count(record: MatchRecord, player: int) -> int | None:
    if not record.final_state_json:
        return None
    try:
        final = snapshot_state(None, record.final_state_json)
    except Exception:
        return None
    return defeated_count(final, player)


def _count_decisions(record: MatchRecord, predicate) -> int:
    return sum(1 for decision in record.decisions if decision.player == CONTESTANT and predicate(decision))


def _count_events(record: MatchRecord, predicate) -> int:
    return sum(1 for event in record.events if predicate(event))


def evaluate_objective(
    objective: NpcObjective,
    record: MatchRecord,
    *,
    stage: NpcStage | None = None,
) -> ObjectiveResult:
    """Judge one objective against a finished match."""
    del stage  # reserved for future stage-aware objectives
    objective_type = objective.type
    won = record.winner == CONTESTANT

    if objective_type == "win":
        return ObjectiveResult(
            objective_type, won, detail=f"winner={record.winner}", text=objective.text
        )

    if objective_type == "win_within_rounds":
        n = objective.n
        if n is None:
            return ObjectiveResult(objective_type, False, detail="missing n", text=objective.text)
        achieved = won and int(record.rounds) <= int(n)
        return ObjectiveResult(
            objective_type,
            achieved,
            detail=f"winner={record.winner}, rounds={record.rounds}, n={n}",
            text=objective.text,
        )

    if objective_type == "defeated_less_than":
        k = objective.k
        if k is None:
            return ObjectiveResult(objective_type, False, detail="missing k", text=objective.text)
        defeated = _defeated_count(record, CONTESTANT)
        if defeated is None:
            return ObjectiveResult(
                objective_type,
                False,
                detail="final state not recorded (record_final_state=True required)",
                text=objective.text,
            )
        achieved = won and int(defeated) < int(k)
        return ObjectiveResult(
            objective_type,
            achieved,
            detail=f"winner={record.winner}, defeated={defeated}, k={k}",
            text=objective.text,
        )

    if objective_type == "use_elemental_burst":
        count = objective.count if objective.count is not None else 1
        bursts = _count_events(
            record,
            lambda event: event.kind == "skill"
            and event.player == CONTESTANT
            and event.skill_type == "burst",
        )
        return ObjectiveResult(
            objective_type,
            bursts >= int(count),
            detail=f"bursts={bursts}, count={count}",
            text=objective.text,
        )

    if objective_type == "trigger_elemental_reactions":
        count = objective.count if objective.count is not None else 1
        reactions = _count_events(
            record,
            lambda event: event.kind == "reaction" and event.player == CONTESTANT,
        )
        return ObjectiveResult(
            objective_type,
            reactions >= int(count),
            detail=f"reactions={reactions}, count={count}",
            text=objective.text,
        )

    if objective_type == "play_equipment_card":
        count = objective.count if objective.count is not None else 1
        kind = objective.kind or "equipment"
        played = _count_decisions(
            record,
            lambda decision: decision.chosen_kind == "action_play_card"
            and decision.card_kind == kind,
        )
        return ObjectiveResult(
            objective_type,
            played >= int(count),
            detail=f"played={played}, count={count}, kind={kind}",
            text=objective.text,
        )

    if objective_type == "play_event_cards":
        count = objective.count if objective.count is not None else 1
        played = _count_decisions(
            record,
            lambda decision: decision.chosen_kind == "action_play_card"
            and decision.card_kind == "event",
        )
        return ObjectiveResult(
            objective_type,
            played >= int(count),
            detail=f"played={played}, count={count}",
            text=objective.text,
        )

    return ObjectiveResult(objective_type, False, detail=f"unsupported type {objective_type!r}", text=objective.text)


def evaluate_stage(spec: NpcStage, record: MatchRecord) -> StarResult:
    """Judge every objective of ``spec`` against one finished match."""
    results = tuple(evaluate_objective(objective, record, stage=spec) for objective in spec.objectives)
    stars = sum(1 for result in results if result.achieved)
    return StarResult(
        objectives=results,
        stars=stars,
        three_star=bool(results) and all(result.achieved for result in results),
    )


def summarize_events(events: list[MatchEvent]) -> dict[str, int]:
    """Small helper for reports: count burst/reaction events per player."""
    summary: dict[str, int] = {}
    for event in events:
        if event.kind == "skill" and event.skill_type == "burst":
            summary[f"burst_p{event.player}"] = summary.get(f"burst_p{event.player}", 0) + 1
        elif event.kind == "reaction":
            summary[f"reaction_p{event.player}"] = summary.get(f"reaction_p{event.player}", 0) + 1
    return summary


def play_decision_summary(decisions: list[DecisionRecord], player: int = CONTESTANT) -> dict[str, int]:
    """Count played cards by kind for one player (reporting helper)."""
    summary: dict[str, int] = {}
    for decision in decisions:
        if decision.player != int(player) or decision.chosen_kind != "action_play_card":
            continue
        key = f"play_{decision.card_kind or 'unknown'}"
        summary[key] = summary.get(key, 0) + 1
    return summary
