"""Replay enrichment: element dice and bounded public-state deltas (no engine)."""

from __future__ import annotations

from typing import Any

from envs.match import DecisionRecord, MatchEvent
from skillloop.replay import (
    _cap_effect,
    _effect_suffix,
    _short_description,
    compact_trace,
)


# --------------------------------------------------------------------------- #
# fixtures: dicts shaped exactly like ``dataclasses.asdict(StateSnapshot)``
# --------------------------------------------------------------------------- #
def _char(
    definition_id: int,
    health: int = 10,
    *,
    energy: int = 0,
    aura: int = 0,
    defeated: bool = False,
    entities: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    return {
        "id": 0,
        "definition_id": definition_id,
        "health": health,
        "max_health": health,
        "energy": energy,
        "max_energy": 3,
        "defeated": defeated,
        "is_active": False,
        "aura": aura,
        "entities": list(entities),
    }


def _entity(definition_id: int, **variables: int) -> dict[str, Any]:
    return {
        "id": 0,
        "definition_id": definition_id,
        "variables": dict(variables),
        "attachments": [],
    }


def _player(
    characters: tuple[dict[str, Any], ...],
    *,
    supports: tuple[dict[str, Any], ...] = (),
    summons: tuple[dict[str, Any], ...] = (),
    statuses: tuple[dict[str, Any], ...] = (),
    hand: tuple[dict[str, Any], ...] = (),
    dice: tuple[int, ...] = (),
    declared_end: bool = False,
) -> dict[str, Any]:
    return {
        "active_character_id": 0,
        "characters": list(characters),
        "combat_statuses": list(statuses),
        "summons": list(summons),
        "supports": list(supports),
        "hand_cards": list(hand),
        "pile_cards": [],
        "dice": list(dice),
        "declared_end": declared_end,
        "legend_used": False,
    }


def _view(
    p0: dict[str, Any],
    p1: dict[str, Any],
    *,
    round_number: int = 1,
) -> dict[str, Any]:
    return {
        "phase": "action",
        "round_number": round_number,
        "current_turn": 0,
        "winner": None,
        "players": [p0, p1],
    }


def _decision(
    player: int,
    step: int,
    *,
    label: str,
    kind: str,
    dice: tuple[int, ...] = (),
    view: dict[str, Any] | None = None,
    subject: int = 0,
    card_kind: str | None = None,
    preview: tuple[dict[str, Any], ...] = (),
) -> DecisionRecord:
    return DecisionRecord(
        player=player,
        step_index=step,
        request_type="action",
        chosen_action_code=1,
        chosen_label=label,
        chosen_kind=kind,
        legal_action_codes=(1,),
        legal_labels=(label,),
        used_dice=dice,
        player_view=view,
        subject_definition_id=subject,
        card_kind=card_kind,
        preview=preview,
    )


def _row() -> dict[str, Any]:
    return {"winner": 0, "rounds": 2, "objectives": {"win": True}}


# --------------------------------------------------------------------------- #
# (a) dice mapping
# --------------------------------------------------------------------------- #
def test_dice_text_maps_elements_and_keeps_unknown_values():
    decision = _decision(
        0, 1, label="use_skill:0:4,4,8", kind="action_use_skill",
        dice=(4, 4, 8), subject=32031,
    )
    line = compact_trace(_row(), [decision], [])[0]
    assert line.endswith("自动支付[雷,雷,万]")

    unknown = _decision(
        0, 1, label="play_card:1:0,9", kind="action_play_card",
        dice=(0, 9), subject=1, 
    )
    line = compact_trace(_row(), [unknown], [])[0]
    assert line.endswith("自动支付[?,9]")

    huge = _decision(
        0, 1, label="use_skill:0:none", kind="action_use_skill",
        dice=tuple(range(1, 12)), subject=32031,
    )
    symbols = compact_trace(_row(), [huge], [])[0].split("自动支付[")[1].rstrip("]")
    assert len(symbols.split(",")) == 8


def _mechanical(label: str, request: str = "reroll_dice") -> DecisionRecord:
    return DecisionRecord(
        player=0,
        step_index=1,
        request_type=request,
        chosen_action_code=1,
        chosen_label=label,
        chosen_kind=request,
        legal_action_codes=(1,),
        legal_labels=(label,),
    )


def test_reroll_line_uses_element_symbols():
    trace = compact_trace(_row(), [_mechanical("reroll:2,2,4,5,5,6,6,7")], [])
    assert trace[0] == "R1 P0 重掷 [水,水,雷,风,风,岩,岩,草]（脚本策略，非 LLM 决策）"
    assert compact_trace(_row(), [_mechanical("reroll:none")], [])[0] == (
        "R1 P0 重掷（脚本策略，非 LLM 决策）"
    )


def test_npc_skill_name_and_effect_are_resolved():
    decision = _decision(1, 1, label="use_skill:0:none", kind="action_use_skill",
                         subject=37031)
    line = compact_trace(_row(), [decision], [])[0]
    assert line.startswith("R1 P1(NPC) 技能 草丘丘萨满·杖击（造成1点物理伤害）")


def test_character_skill_description_is_appended():
    decision = _decision(0, 1, label="use_skill:0:none", kind="action_use_skill",
                         subject=14032)
    line = compact_trace(_row(), [decision], [])[0]
    assert line.startswith("R1 P0 技能 刻晴·星斗归位（造成3点雷元素伤害")
    assert line.endswith("）")


def test_unknown_skill_has_no_description():
    decision = _decision(0, 1, label="use_skill:0:none", kind="action_use_skill",
                         subject=999999)
    line = compact_trace(_row(), [decision], [])[0]
    assert line == "R1 P0 技能 #999999"


def test_short_description_is_flat_period_free_and_capped():
    text = "造成1点伤害。\n并\n" + "很长" * 30
    out = _short_description(text)
    assert "\n" not in out and len(out) <= 40 and out.endswith("…")
    assert _short_description("造成3点物理伤害。") == "造成3点物理伤害"
    assert _short_description("") == ""


# --------------------------------------------------------------------------- #
# (b) no views -> byte-identical to the pre-enrichment trace
# --------------------------------------------------------------------------- #
def test_no_views_keeps_legacy_lines_and_never_adds_a_suffix():
    decisions = [
        _decision(0, 1, label="use_skill:0:none", kind="action_use_skill", subject=999999),
        _decision(1, 1, label="play_card:1:none", kind="action_play_card", subject=332001),
        _decision(0, 2, label="declare_end:2:none", kind="action_declare_end"),
    ]
    events = [MatchEvent(kind="reaction", player=0, round_number=1,
                         reaction_type="superconduct")]

    trace = compact_trace(_row(), decisions, events)
    assert trace[:3] == [
        "R1 P0 技能 #999999",
        "R1 P1(NPC) 打牌 最好的伙伴！",
        "R1 P0 结束",
    ]
    assert trace[3] == "R1 反应 superconduct P0"
    assert trace[4].startswith("结局 winner=0（我方胜），2 回合；目标 ")
    assert all("此后场上" not in line for line in trace)
    assert all("→" not in line for line in trace)


# --------------------------------------------------------------------------- #
# (c) public-state delta when views are recorded
# --------------------------------------------------------------------------- #
def test_effect_suffix_reports_hp_aura_status_dice_and_end():
    before = _view(
        _player(
            (_char(1401, 10), _char(1701, 11), _char(1403, 10)),
            supports=(_entity(322002),),
            dice=(1, 2, 3, 4, 5, 6, 7, 8),
        ),
        _player(
            (_char(3301, 10), _char(3402, 6), _char(3703, 6)),
            hand=(_entity(332001),),
        ),
    )
    after = _view(
        _player(
            (_char(1401, 10), _char(1701, 11), _char(1403, 10)),
            supports=(_entity(322002), _entity(133011, usage=2)),
            dice=(1, 2, 3, 4, 5),
            declared_end=True,
        ),
        _player(
            (_char(3301, 7, aura=3), _char(3402, 6), _char(3703, 6)),
            hand=(_entity(332001), _entity(332004)),
        ),
    )
    decisions = [
        _decision(0, 1, label="use_skill:0:none", kind="action_use_skill",
                  view=before, subject=133011),
        _decision(0, 2, label="declare_end:0:none", kind="action_declare_end",
                  view=after),
    ]

    line = compact_trace(_row(), decisions, [])[0]
    assert "此后场上" in line
    assert "对方·火深渊法师 10→7" in line
    assert "附着 无→火" in line
    assert "我方 获得「火元素护罩」(2次)" in line
    assert "我方骰 8→5" in line
    assert "对方手牌" not in line
    assert "我方 宣布结束" in line
    # The next decision is the *after* view, so its own line has no successor.
    assert "此后场上" not in compact_trace(_row(), decisions, [])[1]


def test_effect_suffix_marks_defeated_and_energy():
    before = _view(
        _player((_char(1401, 3, energy=1),)),
        _player((_char(3301, 4),)),
    )
    after = _view(
        _player((_char(1401, 0, energy=2, defeated=True),)),
        _player((_char(3301, 4),)),
    )
    suffix = _effect_suffix(before, after)
    assert "我方·菲谢尔 3→0 击倒" in suffix
    # Defeat cleanup (energy -> 0) is not repeated.
    assert "能量" not in suffix


def test_effect_suffix_reports_energy_without_defeat():
    before = _view(
        _player((_char(1401, 3, energy=1),)),
        _player((_char(3301, 4),)),
    )
    after = _view(
        _player((_char(1401, 3, energy=2),)),
        _player((_char(3301, 4),)),
    )
    assert "我方·菲谢尔 能量 1→2" in _effect_suffix(before, after)


def test_status_value_change_reads_as_transition():
    before = _view(
        _player((_char(1401),), summons=(_entity(114011, usage=2),)),
        _player((_char(3301),)),
    )
    after = _view(
        _player((_char(1401),), summons=(_entity(114011, usage=1),)),
        _player((_char(3301),)),
    )
    suffix = _effect_suffix(before, after)
    assert "「奥兹」2次→1次" in suffix
    assert "获得" not in suffix and "消失" not in suffix


def test_status_names_without_a_known_definition_never_leak_ids():
    unknown = {
        "id": 193011,
        "definition_id": 999999,
        "variables": {"usage": 2},
        "attachments": [],
    }
    unknown_after = dict(unknown, variables={"usage": 1})
    before = _view(
        _player((_char(1401),)),
        _player((_char(3301),), statuses=(unknown,)),
    )
    after = _view(
        _player((_char(1401),)),
        _player((_char(3301),), statuses=(unknown_after,)),
    )
    suffix = _effect_suffix(before, after)
    assert "「未知状态」2次→1次" in suffix
    assert "#" not in suffix and "193011" not in suffix and "999999" not in suffix


def test_entity_name_prefers_card_definition_id_over_instance_id():
    entity = {
        "id": -500066,
        "definition_id": 999999,      # not in the asset catalog
        "card_definition_id": 322002,  # 凯瑟琳
        "variables": {},
        "attachments": [],
    }
    before = _view(_player((_char(1401),)), _player((_char(3301),)))
    after = _view(
        _player((_char(1401),)),
        _player((_char(3301),), supports=(entity,)),
    )
    suffix = _effect_suffix(before, after)
    assert "获得「凯瑟琳」" in suffix
    assert "#" not in suffix


def test_npc_variant_definition_ids_resolve_via_the_70000_offset():
    # Real engine ids observed on NPC-only statuses: official id + 70000.
    before = _view(
        _player((_char(1401),)),
        _player(
            (_char(9301),),
            statuses=(
                dict(_entity(193011, usage=2), id=-500066),  # 潜行
                dict(_entity(192021, duration=2), id=-500069),  # 水光破镜
            ),
        ),
    )
    after = _view(
        _player((_char(1401),)),
        _player(
            (_char(9301),),
            statuses=(
                dict(_entity(193011, usage=1), id=-500066),
                dict(_entity(192021, duration=1), id=-500069),
            ),
        ),
    )
    suffix = _effect_suffix(before, after)
    assert "「潜行」2次→1次" in suffix
    assert "「水光破镜」2回合→1回合" in suffix
    assert "未知状态" not in suffix
    assert "#" not in suffix and "193011" not in suffix and "192021" not in suffix


def test_status_phase_markers_are_stripped_and_merged():
    before = _view(_player((_char(1401),)), _player((_char(3301),)))
    after = _view(
        _player(
            (_char(1401),),
            supports=(
                _entity(117012),  # 新叶
                _entity(117013),  # 新叶（已创建）
                _entity(303206),  # 交给我吧！（生效中）
            ),
        ),
        _player((_char(3301),)),
    )
    suffix = _effect_suffix(before, after)
    assert "获得「新叶」×2" in suffix
    assert suffix.count("新叶") == 1
    assert "已创建" not in suffix
    assert "获得「交给我吧！」" in suffix
    assert "生效中" not in suffix


def test_card_kind_is_rendered_in_chinese():
    expected = {
        "weapon": "武器",
        "artifact": "圣遗物",
        "talent": "天赋",
        "event": "事件",
        "support": "支援",
        "other": "other",
    }
    for kind, text in expected.items():
        decision = _decision(
            0, 1, label="play_card:0:none", kind="action_play_card",
            subject=332001, card_kind=kind,
        )
        line = compact_trace(_row(), [decision], [])[0]
        assert f" ({text})" in line


def test_dice_diff_is_skipped_across_seats():
    # P0's view hides P1's dice (0) and P1's view hides P0's dice, so a change of
    # actor between the two views must not be read as dice gain/loss.
    before = _view(
        _player((_char(1401),), dice=(1, 2, 3, 4, 5, 6, 7, 8)),
        _player((_char(3301),), dice=()),
    )
    after = _view(
        _player((_char(1401),), dice=()),
        _player((_char(3301),), dice=(1, 2, 3, 4, 5, 6, 7, 8)),
    )
    suffix = _effect_suffix(before, after, before_actor=0, after_actor=1)
    assert "骰" not in suffix

    same_seat = _effect_suffix(before, after, before_actor=0, after_actor=0)
    assert "我方骰 8→0" in same_seat


def test_declared_end_reset_at_round_boundary_is_not_reported():
    before = _view(
        _player((_char(1401),), declared_end=True, dice=(1, 2)),
        _player((_char(3301),)),
        round_number=1,
    )
    after = _view(
        _player((_char(1401),), declared_end=False, dice=(1, 2)),
        _player((_char(3301),)),
        round_number=2,
    )
    suffix = _effect_suffix(before, after, before_actor=0, after_actor=0)
    assert "撤回结束" not in suffix
    assert "回合 1→2" in suffix


# --------------------------------------------------------------------------- #
# (c2) H9: final-state board + last-action delta + truncation note
# --------------------------------------------------------------------------- #
def test_final_state_adds_board_and_completes_the_last_action_delta():
    before = _view(
        _player((_char(1401, 10),)),
        _player((_char(3301, 10),)),
    )
    final = _view(
        _player((dict(_char(1401, 0, defeated=True), max_health=10),)),
        _player((_char(3301, 10),)),
    )
    decision = _decision(
        0, 1, label="use_skill:0:none", kind="action_use_skill",
        view=before, subject=14031,
    )
    trace = compact_trace(_row(), [decision], [], final_state=final)

    # The last decision has no successor view, so the delta is filled from the
    # final snapshot (HP/击倒 in the example) ...
    assert "此后场上" in trace[0] and "我方·菲谢尔 10→0 击倒" in trace[0]
    # ... and one board line precedes the scoreboard.
    assert trace[1] == "最终：我方 菲谢尔 0/10；对方 火深渊法师 10/10"
    assert trace[2].startswith("结局 ")


def test_final_state_line_falls_back_to_the_last_recorded_view():
    view = _view(_player((_char(1401, 4),)), _player((_char(3301, 7),)))
    decision = _decision(0, 1, label="use_skill:0:none", kind="action_use_skill",
                         view=view, subject=14031)
    trace = compact_trace(_row(), [decision], [])
    assert trace[-2] == "最终：我方 菲谢尔 4/4；对方 火深渊法师 7/7"


def test_no_views_and_no_final_state_has_no_final_line():
    trace = compact_trace(
        _row(), [_decision(0, 1, label="use_skill:0:none", kind="action_use_skill",
                           subject=14031)], []
    )
    assert not any(line.startswith("最终：") for line in trace)
    assert trace[-1].startswith("结局 ")


def test_final_state_accepts_a_dataclass_snapshot():
    from dataclasses import dataclass

    @dataclass
    class _Snap:
        players: list

    snapshot = _Snap(players=[
        _player((_char(1401, 6),)),
        _player((_char(3301, 3),)),
    ])
    decision = _decision(0, 1, label="use_skill:0:none", kind="action_use_skill",
                         subject=14031)
    trace = compact_trace(_row(), [decision], [], final_state=snapshot)
    assert trace[-2] == "最终：我方 菲谢尔 6/6；对方 火深渊法师 3/3"


def test_final_state_does_not_rewrite_an_action_that_already_has_a_delta():
    before = _view(_player((_char(1401, 10),)), _player((_char(3301, 10),)))
    after = _view(_player((_char(1401, 8),)), _player((_char(3301, 10),)))
    final = _view(_player((_char(1401, 1),)), _player((_char(3301, 10),)))
    decisions = [
        _decision(0, 1, label="use_skill:0:none", kind="action_use_skill",
                  view=before, subject=14031),
        # A later non-action decision supplies the "after" view for the action.
        DecisionRecord(
            player=0, step_index=2, request_type="reroll_dice",
            chosen_action_code=1, chosen_label="reroll:none",
            chosen_kind="reroll_dice", legal_action_codes=(1,),
            legal_labels=("reroll:none",), player_view=after,
        ),
    ]
    trace = compact_trace(_row(), decisions, [], final_state=final)
    assert trace[0].count("此后场上") == 1
    assert "10→8" in trace[0]
    assert "8→1" not in trace[0]          # the final delta was not appended twice


def test_truncated_trace_marks_the_cut_after_the_first_line():
    decisions = [
        _decision(0, index, label=f"use_skill:{index}:none", kind="action_use_skill",
                  subject=14031)
        for index in range(6)
    ]
    trace = compact_trace(_row(), decisions, [], max_actions=4)
    assert trace[1] == "（回放已截断：共 6 个决策，仅显示前 3 行）"


# --------------------------------------------------------------------------- #
# (d) hand identities are never compared
# --------------------------------------------------------------------------- #
def test_effect_diff_ignores_private_hand_content():
    before = _view(
        _player((_char(1401),), hand=(_entity(332001), _entity(332004))),
        _player((_char(3301),), hand=(_entity(312006),)),
    )
    after = _view(
        _player((_char(1401),), hand=(_entity(332007), _entity(333001))),
        _player((_char(3301),), hand=(_entity(312007),)),
    )
    suffix = _effect_suffix(before, after)
    assert suffix == ""  # same counts; content must not leak into the trace
    for secret in ("最好的伙伴", "332001", "332004", "332007", "333001", "312006"):
        assert secret not in suffix


def test_hand_count_change_is_reported_for_the_same_viewer():
    before = _view(
        _player((_char(1401),), hand=(_entity(332001),)),
        _player((_char(3301),)),
    )
    after = _view(
        _player((_char(1401),), hand=(_entity(332001), _entity(332004), _entity(332007))),
        _player((_char(3301),)),
    )
    suffix = _effect_suffix(before, after, before_actor=0, after_actor=0)
    assert "我方手牌 1→3张" in suffix


def test_hand_count_is_not_reported_across_seats():
    # The next view after a combat action belongs to the opponent. Their view
    # zeroes hand-card definition ids and the opponent's dice, so neither the
    # hand count nor the dice count may be inferred across seats.
    before = _view(
        _player((_char(1401),), hand=(_entity(332001),), dice=(1, 2, 3)),
        _player((_char(3301),)),
    )
    after = _view(
        _player((_char(1401),), hand=(), dice=()),
        _player((_char(3301),), hand=(_entity(312006),), dice=(4, 5)),
    )
    suffix = _effect_suffix(before, after, before_actor=0, after_actor=1)
    assert "手牌" not in suffix
    assert "骰" not in suffix


def test_same_view_hand_count_only_reports_our_side():
    before = _view(
        _player((_char(1401),), hand=(_entity(332001),)),
        _player((_char(3301),), hand=(_entity(312006),)),
    )
    after = _view(
        _player((_char(1401),), hand=(_entity(332001), _entity(332004))),
        _player((_char(3301),), hand=(_entity(312006), _entity(312007))),
    )
    suffix = _effect_suffix(before, after, before_actor=0, after_actor=0)
    assert "我方手牌 1→2张" in suffix
    assert "对方手牌" not in suffix


def test_npc_private_fields_are_not_shown_even_for_same_seat_views():
    # The trace is contestant-facing: the NPC's own hand/dice may be known to the
    # engine, but they are not surfaced even when both views belong to the NPC.
    before = _view(
        _player((_char(1401),)),
        _player((_char(3301),), hand=(_entity(312006),), dice=(1, 2)),
    )
    after = _view(
        _player((_char(1401),)),
        _player((_char(3301),), hand=(_entity(312006), _entity(312007)), dice=(1, 2, 3)),
    )
    suffix = _effect_suffix(before, after, before_actor=1, after_actor=1)
    assert "手牌" not in suffix
    assert "骰" not in suffix


# --------------------------------------------------------------------------- #
# view pairing, item cap, length cap
# --------------------------------------------------------------------------- #
def test_effect_uses_last_previous_view_and_skips_without_after_view():
    first = _view(_player((_char(1401, 10),)), _player((_char(3301, 10),)))
    second = _view(_player((_char(1401, 8),)), _player((_char(3301, 10),)))
    decisions = [
        _decision(0, 1, label="use_skill:0:none", kind="action_use_skill",
                  view=first, subject=32031),
        _decision(0, 2, label="use_skill:1:none", kind="action_use_skill",
                  subject=32031),  # no view: reuse the previous one
        _decision(0, 3, label="use_skill:2:none", kind="action_use_skill",
                  view=second, subject=32031),  # last view: no successor
    ]
    trace = compact_trace(_row(), decisions, [])
    assert "我方·菲谢尔 10→8" in trace[0]      # first view -> next available view
    assert "我方·菲谢尔 10→8" in trace[1]      # no own view: reuse the previous one
    assert "此后场上" not in trace[2]


def test_effect_never_pairs_without_a_before_view():
    only = _view(_player((_char(1401, 8),)), _player((_char(3301, 10),)))
    decisions = [
        _decision(0, 1, label="use_skill:0:none", kind="action_use_skill",
                  subject=32031),  # no view at all yet
        _decision(0, 2, label="use_skill:1:none", kind="action_use_skill",
                  view=only, subject=32031),
    ]
    trace = compact_trace(_row(), decisions, [])
    assert all("此后场上" not in line for line in trace)


def test_effect_item_cap_and_length_cap():
    before = _view(
        _player(
            (_char(1401, 10, energy=0), _char(1701, 11, energy=0), _char(1403, 10, energy=0)),
            supports=(_entity(322002),),
        ),
        _player((_char(3301, 10),)),
    )
    after = _view(
        _player(
            (_char(1401, 9, energy=1), _char(1701, 10, energy=1), _char(1403, 9, energy=1)),
            supports=(_entity(322002), _entity(322009)),
        ),
        _player((_char(3301, 10),)),
    )
    suffix = _effect_suffix(before, after)
    assert "等1项" in suffix                  # 6 HP/energy + 1 added support
    assert suffix.count("；") <= 6

    assert len(_cap_effect("x" * 500)) == 120
    assert _cap_effect("x" * 500).endswith("…")
    assert _cap_effect("短") == "短"


def test_effect_suffix_is_capped_with_long_names(monkeypatch):
    import skillloop.replay as replay

    monkeypatch.setattr(
        replay, "_effect_character_name", lambda _definition_id: "很长的角色名字" * 3
    )
    before = _view(
        _player((_char(1401, 10, energy=0), _char(1701, 11, energy=0), _char(1403, 10, energy=0))),
        _player((_char(3301, 10),)),
    )
    after = _view(
        _player((_char(1401, 1, energy=1), _char(1701, 2, energy=1), _char(1403, 3, energy=1))),
        _player((_char(3301, 10),)),
    )
    suffix = _effect_suffix(before, after).strip()
    assert len(suffix) <= 120
    assert suffix.endswith("…")


def test_entity_counters_carry_the_variable_unit():
    """A bare ``(2)`` next to HP/damage numbers reads like more damage."""
    before = _view(_player((_char(1401),)), _player((_char(3301),)))
    after = _view(
        _player((_char(1401),)),
        _player(
            (_char(3301),),
            statuses=(
                dict(_entity(133021, usage=2), id=-500070),     # 流焰磨刃: 可用次数
                dict(_entity(192021, duration=2), id=-500069),  # 水光破镜: 持续回合
                dict(_entity(121011, layer=3), id=-500071),     # 层数
                dict(_entity(126011, shield=1), id=-500072),    # 护盾值: 无单位
            ),
        ),
    )
    suffix = _effect_suffix(before, after)
    assert "获得「流焰磨刃」(2次)" in suffix
    assert "获得「水光破镜」(2回合)" in suffix
    assert "获得「冰萤」(3层)" in suffix
    assert "获得「岩盔」(1)" in suffix
    assert "获得「流焰磨刃」(2)" not in suffix


def test_entity_counter_units_follow_the_variable_key():
    from skillloop.replay import _entity_key

    entity = {"definition_id": 133021, "variables": {}}
    assert _entity_key(dict(entity, variables={"usages": 2}))[1] == "2次"
    assert _entity_key(dict(entity, variables={"level": 2}))[1] == "2层"
    assert _entity_key(dict(entity, variables={"stack": 2}))[1] == "2层"
    assert _entity_key(dict(entity, variables={"count": 2}))[1] == "2"
    assert _entity_key(dict(entity, variables={"shield": 2}))[1] == "2"
    # Priority follows the documented key order: usage wins over duration.
    both = {"usage": 2, "duration": 5}
    assert _entity_key(dict(entity, variables=both))[1] == "2次"
    assert _entity_key(dict(entity, variables={}))[1] is None


# --------------------------------------------------------------------------- #
# (e) preview hand/attach effects (星斗归位 弃雷楔 -> 雷元素附魔)
# --------------------------------------------------------------------------- #
def test_preview_hand_remove_and_attach_are_stated():
    decision = _decision(
        0, 1, label="use_skill:0:4,4", kind="action_use_skill", subject=14032,
        preview=(
            {"kind": "hand_remove", "definition_id": 114031, "reason": 3},  # 雷楔
            {"kind": "attach", "definition_id": 114032},                    # 雷元素附魔
        ),
    )
    line = compact_trace(_row(), [decision], [])[0]
    assert "手牌：弃置「雷楔」" in line
    assert "附属「雷元素附魔」" in line
    # Distinct from the ``→ 此后场上`` state delta: one leading ``；``, then ``、``.
    assert "；手牌：弃置「雷楔」、附属「雷元素附魔」" in line


def test_preview_hand_add_is_stated():
    decision = _decision(
        0, 1, label="use_skill:0:none", kind="action_use_skill", subject=14032,
        preview=({"kind": "hand_add", "definition_id": 114031},),  # 雷楔
    )
    line = compact_trace(_row(), [decision], [])[0]
    assert "；手牌：获得「雷楔」" in line


def test_preview_absent_leaves_the_line_unchanged():
    decision = _decision(
        0, 1, label="use_skill:0:none", kind="action_use_skill", subject=14032
    )
    line = compact_trace(_row(), [decision], [])[0]
    assert "手牌：" not in line and "附属" not in line


def test_preview_omits_unresolvable_names_and_never_emits_ids():
    decision = _decision(
        0, 1, label="use_skill:0:none", kind="action_use_skill", subject=14032,
        preview=({"kind": "hand_add", "definition_id": 999999},),
    )
    line = compact_trace(_row(), [decision], [])[0]
    assert "#" not in line and "手牌：" not in line


def test_preview_lists_at_most_four_entries():
    decision = _decision(
        0, 1, label="use_skill:0:none", kind="action_use_skill", subject=14032,
        preview=tuple(
            {"kind": "hand_add", "definition_id": 114031} for _ in range(6)
        ),
    )
    line = compact_trace(_row(), [decision], [])[0]
    assert line.count("手牌：获得「雷楔」") == 4
