"""Compact single-game traces (no engine, no network)."""

from __future__ import annotations

from envs.match import DecisionRecord, MatchEvent
from skillloop.replay import compact_trace


def _decision(
    player: int,
    step: int,
    request: str,
    kind: str | None,
    label: str | None,
    *,
    dice: tuple[int, ...] = (),
    round_number: int | None = None,
    subject: int = 0,
    card_kind: str | None = None,
) -> DecisionRecord:
    return DecisionRecord(
        player=player,
        step_index=step,
        request_type=request,
        chosen_action_code=1,
        chosen_label=label,
        chosen_kind=kind,
        legal_action_codes=(1,),
        legal_labels=(label or "",),
        used_dice=dice,
        player_view={"round_number": round_number} if round_number is not None else None,
        subject_definition_id=subject,
        card_kind=card_kind,
    )


def _reaction(round_number: int, reaction: str, player: int | None) -> MatchEvent:
    return MatchEvent(
        kind="reaction",
        player=player,
        round_number=round_number,
        reaction_type=reaction,
    )


def _row() -> dict:
    return {
        "winner": 0,
        "rounds": 2,
        "objectives": {
            "win": True,
            "win_within_rounds": False,
            "play_equipment_card": False,
        },
    }


def test_compact_trace_orders_actions_reactions_and_scoreboard():
    decisions = [
        _decision(0, 1, "reroll_dice", "reroll_dice", "reroll:0,2"),
        _decision(0, 2, "reroll_dice", "reroll_dice", "reroll:1"),          # first only
        _decision(0, 3, "action", "action_use_skill", "use_skill:0:1,2",
                  dice=(1, 2), round_number=1, subject=32031),
        _decision(1, 1, "action", "action_play_card", "play_card:1:3",
                  dice=(3,), round_number=1, subject=332001, card_kind="event"),
        _decision(0, 4, "choose_active", "choose_active", "choose_active:3201"),
        _decision(0, 5, "choose_active", "choose_active", "choose_active:3202"),  # first only
        _decision(0, 6, "select_card", "select_card", "select_card:332001"),      # ignored
        _decision(1, 2, "action", "action_declare_end", "declare_end:2:none",
                  round_number=2),
    ]
    events = [
        MatchEvent(kind="skill", player=0, round_number=1, skill_type="burst"),
        _reaction(1, "superconduct", 0),
        _reaction(2, "vaporize", 1),
        _reaction(2, "vaporize", 1),                                          # coalesced
    ]

    trace = compact_trace(_row(), decisions, events)

    assert len(trace) == 8
    assert trace[0] == "R1 P0 重掷 0,2"
    assert trace[1].startswith("R1 P0 技能 #32031 骰1,2")
    assert trace[2] == "R1 P1(NPC) 打牌 最好的伙伴！ (event) 骰3"
    assert trace[3] == "R1 P0 选人"                # second choose_active dropped
    assert trace[4] == "R2 P1(NPC) 结束"
    assert trace[5] == "R1 反应 superconduct P0"
    assert trace[6] == "R2 反应 vaporize P1(NPC) x2"
    assert trace[7].startswith("结局 winner=0（我方胜），2 回合；目标 ")
    assert "win✔" in trace[7] and "win_within_rounds✘" in trace[7]
    assert compact_trace(_row(), decisions, events) == trace   # deterministic


def test_compact_trace_hard_caps_lines_and_reports_truncation():
    decisions = [
        _decision(0, index, "action", "action_use_skill", f"use_skill:{index}:none",
                  round_number=1, subject=32031)
        for index in range(10)
    ]
    meta: dict = {}
    trace = compact_trace({"winner": 1, "rounds": 3, "objectives": {"win": False}},
                          decisions, [], max_actions=4, meta=meta)

    assert len(trace) == 4
    assert meta["truncated"] is True
    assert meta["n_action_lines"] == 10
    assert trace[-1].startswith("结局 winner=1（对方胜）")


def test_compact_trace_falls_back_to_label_when_kind_is_missing():
    decision = _decision(0, 1, "action", None, "play_card:2:1",
                         dice=(1,), subject=1, card_kind="event")
    trace = compact_trace(_row(), [decision], [])
    assert trace[0] == "R1 P0 打牌 #1 (event) 骰1"


def test_compact_trace_resolves_asset_names_and_keeps_unknown_ids():
    decisions = [
        _decision(0, 1, "action", "action_use_skill", "use_skill:0:none",
                  subject=14032),                                   # 刻晴·星斗归位
        _decision(0, 2, "action", "action_play_card", "play_card:1:none",
                  subject=332001, card_kind="event"),               # 最好的伙伴！
        _decision(0, 3, "action", "action_switch_active", "switch_active:2:none",
                  subject=1403),                                   # 刻晴
        _decision(0, 4, "action", "action_use_skill", "use_skill:3:none",
                  subject=999999),                                 # unknown id
    ]
    trace = compact_trace(_row(), decisions, [])
    assert trace[0] == "R1 P0 技能 刻晴·星斗归位"
    assert trace[1] == "R1 P0 打牌 最好的伙伴！ (event)"
    assert trace[2] == "R1 P0 切人 刻晴"
    assert trace[3] == "R1 P0 技能 #999999"


def test_compact_trace_handles_missing_fields_without_crashing():
    trace = compact_trace({}, [{"request_type": "action"}, {}], [{}])
    assert len(trace) == 4
    assert trace[0] == "R1 P0 ?"                  # missing player defaults to 0
    assert trace[1] == "R1 P0 ?"
    assert "反应" in trace[2]
    assert trace[3] == "结局 winner=None，? 回合；目标 无记录"


def test_compact_trace_always_keeps_scoreboard_line():
    trace = compact_trace(
        _row(),
        [_decision(0, 1, "action", "action_use_skill", "use_skill:0:none")],
        [],
        max_actions=1,
    )
    assert len(trace) == 1 and trace[0].startswith("结局 ")
