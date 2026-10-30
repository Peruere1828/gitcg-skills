"""Challenge-objective judging on synthetic trajectories (PLAN.md §4.4, PvE-B3)."""

from __future__ import annotations

import npc.objectives as objectives
from envs.match import DecisionRecord, MatchEvent, MatchRecord
from npc.objectives import evaluate_objective, evaluate_stage
from npc.spec import NpcObjective, get_stage
from reps.schema import CharacterSnapshot, PlayerSnapshot, StateSnapshot


def _record(
    *,
    winner: int | None = 0,
    rounds: int = 4,
    decisions: list[DecisionRecord] | None = None,
    events: list[MatchEvent] | None = None,
    final_state_json: str | None = None,
) -> MatchRecord:
    return MatchRecord(
        deck0="contestant",
        deck1="npc_timaeus",
        seed=1,
        winner=winner,
        rounds=rounds,
        decisions0=0,
        decisions1=0,
        fallbacks0=0,
        fallbacks1=0,
        io_errors0=(),
        io_errors1=(),
        final_state_json=final_state_json,
        decisions=decisions or [],
        events=events or [],
    )


def _card_decision(card_kind: str, *, player: int = 0) -> DecisionRecord:
    return DecisionRecord(
        player=player,
        step_index=0,
        request_type="action",
        chosen_action_code=1,
        chosen_label="play_card",
        chosen_kind="action_play_card",
        legal_action_codes=(1,),
        legal_labels=("play_card",),
        subject_definition_id=123,
        card_kind=card_kind,
    )


def _skill_event(skill_type: str, *, player: int = 0) -> MatchEvent:
    return MatchEvent(kind="skill", player=player, skill_type=skill_type, skill_definition_id=999)


def _reaction_event(*, player: int = 0) -> MatchEvent:
    return MatchEvent(kind="reaction", player=player, reaction_type="vaporize")


def test_win_objective():
    assert evaluate_objective(NpcObjective("win"), _record(winner=0)).achieved
    assert not evaluate_objective(NpcObjective("win"), _record(winner=1)).achieved
    assert not evaluate_objective(NpcObjective("win"), _record(winner=None)).achieved


def test_win_within_rounds_objective():
    objective = NpcObjective("win_within_rounds", n=5)
    assert evaluate_objective(objective, _record(winner=0, rounds=5)).achieved
    assert not evaluate_objective(objective, _record(winner=0, rounds=6)).achieved
    assert not evaluate_objective(objective, _record(winner=1, rounds=3)).achieved
    assert not evaluate_objective(NpcObjective("win_within_rounds", n=None), _record()).achieved


def test_defeated_less_than_objective_uses_final_state(monkeypatch):
    def _snapshot(defeated: list[bool]) -> StateSnapshot:
        chars = tuple(
            CharacterSnapshot(
                id=100 + index,
                definition_id=200 + index,
                health=0 if flag else 5,
                max_health=5,
                energy=0,
                max_energy=2,
                defeated=flag,
                is_active=index == 0,
            )
            for index, flag in enumerate(defeated)
        )

        def player() -> PlayerSnapshot:
            return PlayerSnapshot(
                active_character_id=100,
                characters=chars,
                combat_statuses=(),
                summons=(),
                supports=(),
                hand_cards=(),
                pile_cards=(),
                dice=(),
                declared_end=True,
                legend_used=False,
            )

        return StateSnapshot(phase="game_end", round_number=1, current_turn=0, winner=0, players=(player(), player()))

    objective = NpcObjective("defeated_less_than", k=2)
    monkeypatch.setattr(objectives, "snapshot_state", lambda *_: _snapshot([True, False, False]))
    assert evaluate_objective(objective, _record(final_state_json="{}")).achieved
    monkeypatch.setattr(objectives, "snapshot_state", lambda *_: _snapshot([True, True, False]))
    assert not evaluate_objective(objective, _record(final_state_json="{}")).achieved
    # No recorded final state -> not judgeable.
    assert not evaluate_objective(objective, _record(final_state_json=None)).achieved


def test_use_elemental_burst_objective():
    objective = NpcObjective("use_elemental_burst", count=1)
    assert evaluate_objective(objective, _record(events=[_skill_event("burst")])).achieved
    assert not evaluate_objective(objective, _record(events=[_skill_event("normal")])).achieved
    # Only the contestant's bursts count.
    assert not evaluate_objective(objective, _record(events=[_skill_event("burst", player=1)])).achieved
    two = NpcObjective("use_elemental_burst", count=2)
    assert not evaluate_objective(two, _record(events=[_skill_event("burst")])).achieved
    assert evaluate_objective(two, _record(events=[_skill_event("burst"), _skill_event("burst")])).achieved


def test_trigger_elemental_reactions_objective():
    objective = NpcObjective("trigger_elemental_reactions", count=2)
    events = [_reaction_event(), _reaction_event(player=1), _reaction_event()]
    assert evaluate_objective(objective, _record(events=events)).achieved
    assert not evaluate_objective(objective, _record(events=[_reaction_event()])).achieved


def test_play_equipment_card_objective():
    objective = NpcObjective("play_equipment_card", kind="artifact", count=1)
    assert evaluate_objective(objective, _record(decisions=[_card_decision("artifact")])).achieved
    assert not evaluate_objective(objective, _record(decisions=[_card_decision("weapon")])).achieved
    weapon = NpcObjective("play_equipment_card", kind="weapon", count=1)
    assert evaluate_objective(weapon, _record(decisions=[_card_decision("weapon")])).achieved
    # Opponent card plays never count.
    assert not evaluate_objective(
        weapon, _record(decisions=[_card_decision("weapon", player=1)])
    ).achieved


def test_play_event_cards_objective():
    objective = NpcObjective("play_event_cards", count=2)
    decisions = [_card_decision("event"), _card_decision("event"), _card_decision("support")]
    assert evaluate_objective(objective, _record(decisions=decisions)).achieved
    assert not evaluate_objective(objective, _record(decisions=[_card_decision("event")])).achieved


def test_evaluate_stage_star_counting():
    stage = get_stage("timmie")  # win / win_within_rounds(5) / defeated_less_than(2)
    result = evaluate_stage(stage, _record(winner=1, rounds=9))
    assert result.stars == 0 and not result.three_star

    # A win that violates only the round limit -> 1 star (win) among 3.
    result = evaluate_stage(stage, _record(winner=0, rounds=9))
    assert result.stars == 1 and not result.three_star

    # A clean fast win with the final state recorded -> all three.
    result = evaluate_stage(stage, _record(winner=0, rounds=4))
    # The defeated objective needs a recorded final state; without it only two
    # of the three objectives hold.
    assert result.stars == 2 and not result.three_star
