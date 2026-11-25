"""Own-seat-only match history for the decision prompt (``skillloop.history``).

The history must read like a caster's notes ("R1 对方 · 使用 破镜") while never
showing anything the acting seat cannot see: the opponent's hand keeps its
engine-masked ids (0 / negative placeholders) out of the prompt, and the
opponent's hand is a count only.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from reps.schema import (
    CharacterSnapshot,
    DecisionContext,
    DecisionType,
    EntitySnapshot,
    LowLevelActionSpec,
    OptionKind,
    PlayerSnapshot,
    StateSnapshot,
)
from skillloop.history import MatchHistory, _name
from skillloop.render import StateRenderer

# Real ids from the cached asset dump (``skillloop.cards``).
SKILL_SHIYUAN = 14074  # 诸愿百眼之轮
SKILL_EYYAO = 14072  # 神变·恶曜开眼
SKILL_KEQING_E = 14032  # 刻晴·星斗归位
SKILL_BENNETT_A = 13031  # 班尼特·好运剑
SKILL_BENNETT_E = 13032  # 班尼特·热情过载
CARD_CAIDING = 330006  # 裁定之时
CARD_JIAOZHUO = 312301  # 焦灼的魔女帽
CARD_GAMBLER = 312004  # 赌徒的耳环


@dataclass
class _Named:
    name: str


class _FakeAssets:
    def card(self, definition_id: int) -> _Named:
        return _Named(f"card{definition_id}")

    def character(self, definition_id: int) -> _Named:
        return _Named(f"char{definition_id}")

    def skill(self, definition_id: int) -> _Named:
        return _Named(f"skill{definition_id}")


def _character(
    definition_id: int,
    *,
    health: int = 10,
    energy: int = 0,
    aura: int = 0,
    entities=(),
) -> CharacterSnapshot:
    return CharacterSnapshot(
        id=definition_id + 1000,
        definition_id=definition_id,
        health=health,
        max_health=10,
        energy=energy,
        max_energy=3,
        defeated=health <= 0,
        is_active=True,
        aura=aura,
        entities=tuple(entities),
    )


def _player(
    characters=(),
    hand=(),
    *,
    dice=(),
    statuses=(),
    summons=(),
    supports=(),
) -> PlayerSnapshot:
    return PlayerSnapshot(
        active_character_id=None,
        characters=tuple(characters),
        combat_statuses=tuple(statuses),
        summons=tuple(summons),
        supports=tuple(supports),
        hand_cards=tuple(hand),
        pile_cards=(),
        dice=tuple(dice),
        declared_end=False,
        legend_used=False,
    )


def _view(me: PlayerSnapshot, opp: PlayerSnapshot, *, round_number: int = 2) -> StateSnapshot:
    return StateSnapshot(
        phase="action",
        round_number=round_number,
        current_turn=0,
        winner=None,
        players=(me, opp),
    )


def _context(
    *,
    view: StateSnapshot | None = None,
    events: tuple[dict, ...] = (),
    round_number: int = 2,
) -> DecisionContext:
    return DecisionContext(
        acting_player=0,
        request_type=DecisionType.ACTION,
        step_index=1,
        full_state=view,
        player_view=view,
        metadata={"recent_events": list(events)},
    )


def _empty_context(round_number: int = 2) -> DecisionContext:
    me = _player((_character(1407),), (_card(330006),))
    opp = _player((_character(3203),))
    return _context(view=_view(me, opp, round_number=round_number))


def _card(definition_id: int, *, entity_id: int | None = None) -> EntitySnapshot:
    return EntitySnapshot(
        id=int(entity_id if entity_id is not None else definition_id + 5000),
        definition_id=int(definition_id),
    )


# -- event lines -------------------------------------------------------------
def test_event_lines_name_skills_and_reactions_in_chinese():
    from skillloop.replay import _skill_name

    history = MatchHistory()
    context = _context(
        view=_view(_player((_character(1407),)), _player((_character(3203),))),
        events=(
            {"kind": "skill", "player": 1, "definition_id": SKILL_SHIYUAN, "round_number": 2},
            {"kind": "skill", "player": 0, "definition_id": SKILL_EYYAO, "round_number": 2},
            {"kind": "reaction", "player": 1, "reaction_type": "vaporize", "round_number": 2},
        ),
    )
    lines = history.consume(context)
    # Skill names come from the shared resolver (``skillloop.replay``), never
    # from a local table, so an id it knows is never printed as ``#id``.
    assert lines == [
        f"R2 对方 · 使用 {_skill_name(SKILL_SHIYUAN)}",
        f"R2 我方 · 使用 {_skill_name(SKILL_EYYAO)}",
        "R2 对方 · 触发 蒸发",
    ]
    assert not any("#" in line for line in lines)


def test_opponent_draw_is_a_count_and_our_draw_names_the_cards():
    history = MatchHistory()
    context = _context(
        view=_view(_player((_character(1407),)), _player((_character(3203),))),
        events=(
            {"kind": "card_move", "player": 1, "to": 5, "reason": "draw", "round_number": 1},
            {"kind": "card_move", "player": 1, "to": 5, "reason": "draw", "round_number": 1},
            {"kind": "card_move", "player": 0, "to": 5, "reason": "draw",
             "definition_id": CARD_CAIDING, "from": 6, "round_number": 1},
        ),
    )
    lines = history.consume(context)
    assert "R1 对方 · 抽牌 2 张" in lines
    assert "R1 我方 · 抽到「裁定之时」" in lines
    # The opponent's drawn cards are never named, even if an event carries an id.
    assert all("裁定之时" not in line for line in lines if "对方" in line)


def test_opponent_card_move_never_carries_the_card_identity():
    history = MatchHistory()
    # A hostile (or buggy) projection that does include the opponent's card id:
    # the renderer must still not print it.
    context = _context(
        view=_view(_player((_character(1407),)), _player((_character(3203),))),
        events=(
            {"kind": "card_move", "player": 1, "to": 0, "reason": "played",
             "definition_id": CARD_CAIDING, "from": 5, "round_number": 1},
        ),
    )
    lines = history.consume(context)
    assert lines == []


def test_opponent_hand_names_and_negative_ids_never_reach_the_prompt():
    history = MatchHistory()
    opp_hand = (
        _card(0, entity_id=-500036),
        _card(0, entity_id=-500037),
    )
    view = _view(_player((_character(1407),), (_card(CARD_CAIDING),), dice=(2, 2)), _player((_character(3203),), opp_hand))
    context = _context(
        view=view,
        events=(
            {"kind": "card_move", "player": 1, "to": 5, "reason": "draw", "round_number": 1},
        ),
    )
    history.consume(context)
    rendered = history.render()
    assert "对方" in rendered
    assert "-500036" not in rendered and "-500037" not in rendered
    # The opponent's hand count is public and may be named as a count.
    assert "裁定之时" not in rendered


# -- state-diff fallback -----------------------------------------------------
def test_diff_fallback_reports_public_changes_only():
    history = MatchHistory()

    me_before = _player((_character(1407, health=10, energy=1),), (_card(330006),), dice=(2, 2))
    opp_before = _player((_character(3203, health=10),), (_card(0, entity_id=-500036),))
    history.consume(_context(view=_view(me_before, opp_before, round_number=1)))

    me_after = _player(
        (_character(1407, health=10, energy=2),),
        (_card(330006), _card(312007)),
        dice=(3, 3, 3),
    )
    opp_after = _player(
        (_character(3203, health=7),),
        (_card(0, entity_id=-500036), _card(0, entity_id=-500037)),
    )
    lines = history.consume(_context(view=_view(me_after, opp_after, round_number=2)))

    # Every line carries the round prefix, consequences included.
    assert "R2 我方·雷电将军 能量 1→2" in lines
    assert "R2 我方骰子 2→3：火火火" in lines
    assert "R2 我方抽到「华饰之兜」" in lines
    assert "R2 对方·愚人众先遣队·水铳重卫士 HP 10→7" in lines
    assert "R2 对方手牌 1→2" in lines
    assert all(line.startswith("R2 ") for line in lines)
    assert not any("-500" in line for line in lines)


def test_diff_and_events_do_not_double_report_hand_traffic():
    history = MatchHistory()
    me_before = _player((_character(1407),), (_card(330006),))
    opp_before = _player((_character(3203),), (_card(0, entity_id=-500036),))
    history.consume(_context(view=_view(me_before, opp_before)))

    me_after = _player((_character(1407),), (_card(330006), _card(312007)))
    opp_after = _player((_character(3203),), (_card(0, entity_id=-500036), _card(0, entity_id=-500037)))
    history.consume(
        _context(
            view=_view(me_after, opp_after),
            events=(
                {"kind": "card_move", "player": 0, "to": 5, "reason": "draw",
                 "definition_id": 312007, "from": 6, "round_number": 2},
                {"kind": "card_move", "player": 1, "to": 5, "reason": "draw", "round_number": 2},
            ),
        )
    )
    rendered = history.render()
    assert "抽到「华饰之兜」" in rendered
    assert "对方 · 抽牌 1 张" in rendered
    assert "我方手牌" not in rendered
    assert "对方手牌" not in rendered


def test_diff_lines_carry_the_round_they_belong_to():
    history = MatchHistory()
    me_before = _player((_character(1407, energy=0),), (_card(330006),))
    opp_before = _player((_character(3203, health=10),), (_card(0, entity_id=-500036),))
    history.consume(_context(view=_view(me_before, opp_before, round_number=4)))

    me_after = _player((_character(1407, energy=2),), (_card(330006),))
    opp_after = _player((_character(3203, health=6),), (_card(0, entity_id=-500036),))
    lines = history.consume(_context(view=_view(me_after, opp_after, round_number=5)))
    assert lines
    assert all(line.startswith("R5 ") for line in lines)


def test_missing_round_in_the_view_is_written_as_a_question_mark():
    history = MatchHistory()
    before = _player((_character(1407, energy=0),), (_card(330006),))
    opp = _player((_character(3203, health=10),), (_card(0, entity_id=-500036),))
    history.consume(_context(view=_view(before, opp, round_number=None)))

    after = _player((_character(1407, energy=1),), (_card(330006),))
    lines = history.consume(_context(view=_view(after, opp, round_number=None)))
    assert lines
    assert all(line.startswith("R? ") for line in lines)

    # No view at all: an event without its own round cannot say more than "?".
    events = MatchHistory().consume(
        _context(
            view=None,
            events=({"kind": "skill", "player": 1, "definition_id": SKILL_SHIYUAN},),
        )
    )
    assert events and events[0].startswith("R? ")


def test_diff_lines_the_events_already_state_are_not_repeated():
    history = MatchHistory()
    me_before = _player((_character(1407),), (_card(CARD_CAIDING),))
    opp = _player((_character(3203),), (_card(0, entity_id=-500036),))
    history.consume(_context(view=_view(me_before, opp, round_number=1)))

    me_after = _player((_character(1407),), (), supports=(_card(CARD_CAIDING),))
    lines = history.consume(
        _context(
            view=_view(me_after, opp, round_number=1),
            events=(
                {"kind": "card_move", "player": 0, "to": 1, "reason": "played",
                 "definition_id": CARD_CAIDING, "from": 5, "round_number": 1},
            ),
        )
    )
    # The event names the card; the support-zone diff must not say it again.
    assert sum("裁定之时" in line for line in lines) == 1
    assert lines == ["R1 我方 · 打出「裁定之时」"]


def test_adjacent_identical_history_lines_are_kept_once():
    history = MatchHistory()
    context = _empty_context(round_number=2)
    history.record_own(context, "使用技能：刻晴·星斗归位")
    history.record_own(context, "使用技能：刻晴·星斗归位")
    assert history.render().count("使用技能：刻晴·星斗归位") == 1


# -- overlay / NPC names -----------------------------------------------------
def test_each_shooter_passive_resolves_to_its_own_id_and_owner():
    """``Hide`` is one GTS alias in three files; the ids must not overwrite."""
    from skillloop.passive_skills import passive_by_id, passives_for

    if passive_by_id(31023) is None:
        pytest.skip("引擎 GTS 角色数据缺失（build/engine/.../characters）：跳过被动别名用例")

    expected = {
        3102: (31023, "冰箭丘丘人·躲起来"),
        3002: (30023, "射手丘丘人·躲起来"),
        3402: (34023, "雷箭丘丘人·躲起来"),
    }
    ids = set()
    for character_id, (skill_id, full_name) in expected.items():
        # The alias ``Hide`` must resolve inside the owner's own id namespace.
        assert [item["id"] for item in passives_for(character_id)] == [skill_id]
        assert passive_by_id(skill_id)["id"] == skill_id
        # And the history names it ``owner·被动`` instead of ``#id``.
        assert _name(skill_id) == full_name
        ids.add(skill_id)
    assert len(ids) == 3


def test_npc_overlay_ids_use_the_shared_skill_resolver():
    """``93014`` / ``193011`` must read as names, not as bare engine ids."""
    from skillloop.cards import character_name
    from skillloop.replay import _npc_skill_table, _skill_name

    if not _npc_skill_table():
        pytest.skip("NPC 技能表不可用（configs/npc 未加载）：无法校验 NPC 名字解析")
    if character_name(123011).startswith("#"):
        pytest.skip("资产快照缺少 overlay 变体名（123011 潜行）：无法校验状态名")

    # The resolver the replay traces use: an id it knows is never shown as ``#id``.
    resolved = _skill_name(93014)

    history = MatchHistory()
    me = _player((_character(1407),), (_card(330006),))
    opp_before = _player((_character(9301, health=13),), (_card(0, entity_id=-500036),))
    history.consume(_context(view=_view(me, opp_before, round_number=1)))

    opp_after = _player(
        (_character(9301, health=13, entities=(_card(193011),)),),
        (_card(0, entity_id=-500036),),
    )
    lines = history.consume(
        _context(
            view=_view(me, opp_after, round_number=1),
            events=(
                {"kind": "skill", "player": 1, "definition_id": 93014, "round_number": 1},
            ),
        )
    )
    body = "\n".join(lines)
    assert "#93014" not in body
    assert "#193011" not in body
    if not resolved.startswith("#"):
        assert resolved in body
    else:
        # 93014 is NPC 9301's *passive* slot: the NPC skill table only carries
        # A/E/Q plus prepared extras, so the history falls back to the engine
        # passive sheets (``skillloop.passive_skills``) for ``owner·passive``.
        assert "潜行大师" in body
    # The status the passive granted keeps its own name as well.
    assert "潜行" in body


# -- own actions -------------------------------------------------------------
def test_record_own_line_uses_the_round_and_is_clipped():
    history = MatchHistory()
    context = _empty_context(round_number=3)
    history.record_own(context, "使用技能：刻晴·星斗归位")
    assert history.render().endswith("R3 我方 · 使用技能：刻晴·星斗归位")

    history.record_own(context, "长" * 300)
    assert all(len(line) <= 120 for line in history.render().splitlines())


def test_recorded_own_action_replaces_its_engine_event():
    """The engine event of our own action must not repeat the recorded label."""
    history = MatchHistory()
    context = _empty_context()
    history.record_own(context, "使用技能：神变·恶曜开眼")
    lines = history.consume(
        _context(
            view=context.player_view,
            events=(
                {"kind": "skill", "player": 0, "definition_id": SKILL_EYYAO, "round_number": 2},
            ),
        )
    )
    assert lines == []
    rendered = history.render()
    assert rendered.count("神变·恶曜开眼") == 1


def test_rich_renderer_action_label_suppresses_its_own_skill_event():
    """``普通攻击：好运剑`` + ``使用 班尼特·好运剑`` must not both be printed."""
    history = MatchHistory()
    context = _empty_context(round_number=3)
    history.record_own(context, "普通攻击：好运剑")
    lines = history.consume(
        _context(
            view=context.player_view,
            events=(
                {"kind": "skill", "player": 0, "definition_id": SKILL_BENNETT_A, "round_number": 3},
            ),
        )
    )
    assert lines == []
    rendered = history.render()
    assert rendered.count("好运剑") == 1
    assert "使用 班尼特·好运剑" not in rendered


def test_a_different_own_event_is_never_swallowed_by_a_recorded_action():
    history = MatchHistory()
    context = _empty_context(round_number=4)
    history.record_own(context, "元素战技：热情过载")
    lines = history.consume(
        _context(
            view=context.player_view,
            events=(
                {"kind": "skill", "player": 0, "definition_id": SKILL_BENNETT_A, "round_number": 4},
            ),
        )
    )
    assert lines == ["R4 我方 · 使用 班尼特·好运剑"]


def test_only_the_first_of_two_identical_own_events_is_swallowed():
    history = MatchHistory()
    context = _empty_context(round_number=3)
    history.record_own(context, "使用技能：好运剑")
    lines = history.consume(
        _context(
            view=context.player_view,
            events=(
                {"kind": "skill", "player": 0, "definition_id": SKILL_BENNETT_A, "round_number": 3},
                {"kind": "skill", "player": 0, "definition_id": SKILL_BENNETT_A, "round_number": 3},
            ),
        )
    )
    assert lines == ["R3 我方 · 使用 班尼特·好运剑"]
    # The recorded label plus the once-only event line.
    assert history.render().count("好运剑") == 2


def test_a_non_action_label_cannot_swallow_a_later_own_skill():
    """``选择出战角色：刻晴`` must not eat ``使用 刻晴·星斗归位``.

    The old implementation consumed a bare *count*, so any own event in the batch
    took the slot; matching by action name is not enough either, because "刻晴"
    is a substring of the skill name -- only skill/card labels arm the matcher.
    """
    history = MatchHistory()
    context = _empty_context(round_number=2)
    history.record_own(context, "选择出战角色：刻晴")
    lines = history.consume(
        _context(
            view=context.player_view,
            events=(
                {"kind": "skill", "player": 0, "definition_id": SKILL_KEQING_E, "round_number": 2},
            ),
        )
    )
    assert lines == ["R2 我方 · 使用 刻晴·星斗归位"]


def test_recorded_card_play_suppresses_its_card_move_event():
    history = MatchHistory()
    context = _empty_context(round_number=3)
    history.record_own(context, "打出卡牌：焦灼的魔女帽 → 我方·班尼特")
    lines = history.consume(
        _context(
            view=context.player_view,
            events=(
                {"kind": "card_move", "player": 0, "to": 1, "reason": "played",
                 "definition_id": CARD_JIAOZHUO, "from": 5, "round_number": 3},
            ),
        )
    )
    assert lines == []
    assert "焦灼的魔女帽" in history.render()


def test_recorded_card_play_suppresses_the_skill_event_it_fires():
    """A card play also emits a ``skill`` event carrying the card id."""
    history = MatchHistory()
    context = _empty_context(round_number=3)
    history.record_own(context, "打出卡牌：赌徒的耳环 → 我方·刻晴")
    lines = history.consume(
        _context(
            view=context.player_view,
            events=(
                {"kind": "card_move", "player": 0, "to": 1, "reason": "unspecified",
                 "definition_id": CARD_GAMBLER, "from": 5, "round_number": 3},
                {"kind": "skill", "player": 0, "definition_id": CARD_GAMBLER, "round_number": 3},
            ),
        )
    )
    assert lines == []


def test_card_effect_event_one_decision_later_in_the_same_round_is_suppressed():
    history = MatchHistory()
    context = _empty_context(round_number=4)
    history.record_own(context, "打出卡牌：赌徒的耳环 → 我方·刻晴")
    first = history.consume(
        _context(
            view=context.player_view,
            events=(
                {"kind": "card_move", "player": 0, "to": 1, "reason": "played",
                 "definition_id": CARD_GAMBLER, "from": 5, "round_number": 4},
            ),
        )
    )
    assert first == []
    second = history.consume(
        _context(
            view=context.player_view,
            events=(
                {"kind": "skill", "player": 0, "definition_id": CARD_GAMBLER, "round_number": 4},
            ),
        )
    )
    assert second == []
    assert history.render().count("赌徒的耳环") == 1


# -- render ------------------------------------------------------------------
def test_render_keeps_the_newest_lines_and_returns_empty_without_history():
    history = MatchHistory()
    assert history.render() == ""
    context = _empty_context()
    for index in range(20):
        history.record_own(context, f"动作{index}")
    text = history.render(max_lines=5)
    body = text.splitlines()
    # The header states the order the lines are in: oldest first, newest last.
    assert body[0] == "对局历史（仅你方可见信息，按时间顺序，最后一条最新）："
    assert len(body) == 6
    assert body[-1].endswith("动作19")
    assert "动作15" in body[1]
    assert all("动作14" not in line for line in body)


def test_user_message_inserts_history_between_state_and_legal_actions():
    spec = LowLevelActionSpec(
        action_code=100,
        request_type=DecisionType.ACTION,
        kind=OptionKind.ACTION_USE_SKILL,
        label="use_skill",
        subject_definition_id=0,
    )
    candidates = [(100, spec)]
    renderer = StateRenderer(_FakeAssets(), opponent_name="NPC")
    context = _context(view=None)

    plain = renderer.user_message(context, candidates)["content"]
    assert "对局历史" not in plain

    with_history = renderer.user_message(context, candidates, history="对局历史（x）：\nR1 我方 · 使用 技能")["content"]
    assert with_history.index("（无状态）") < with_history.index("对局历史") < with_history.index("合法动作：")
    assert with_history.replace("对局历史（x）：\nR1 我方 · 使用 技能\n", "") == plain


# -- real match --------------------------------------------------------------
def test_real_match_history_names_both_sides_without_hidden_ids():
    from envs.decks import deck_spec
    from envs.match import run_match
    from envs.policy import expert_policy
    from skillloop.agent import AgentConfig, SkillAgentPolicy
    from skillloop.render import TaskSpec

    class _Client:
        def __init__(self) -> None:
            self.calls: list[list[dict[str, str]]] = []
            self.last_reasoning = ""

        def chat(self, messages, **kwargs):
            self.calls.append(messages)
            return '{"choice": 0, "used": []}'

    deck0, deck1 = "superconduct_aggro", "natlan_battleship"
    client = _Client()
    policy = SkillAgentPolicy(
        expert_policy(deck0, seed=1),
        client,
        renderer=StateRenderer(_FakeAssets(), opponent_name="NPC"),
        task=TaskSpec(stage="wagner", display="瓦格纳", objectives=("赢得胜利",), deck=deck0),
        config=AgentConfig(),
    )
    run_match(
        deck_spec(deck0),
        deck_spec(deck1),
        policy,
        expert_policy(deck1),
        seed=1,
        max_steps=400,
        record_decisions=True,
        record_events=True,
        record_views=True,
    )

    users = [call[1]["content"] for call in client.calls]
    assert len(users) > 2
    assert "对局历史" not in users[0]
    with_history = [user for user in users if "对局历史" in user]
    assert with_history
    assert any("我方 ·" in user for user in with_history)
    assert any("对方 ·" in user for user in with_history)
    # Masked opponent hand entities carry negative ids; none may reach the prompt.
    assert not any("-500" in user for user in users)
    # The history sits between the board and the legal-action list.
    sample = with_history[-1]
    assert sample.index("对局历史") < sample.index("合法动作：")


@pytest.mark.parametrize("bad", [{"kind": "truncated"}, {}, {"kind": "unknown"}])
def test_unknown_or_bookkeeping_events_are_ignored(bad):
    history = MatchHistory()
    context = _context(
        view=_view(_player((_character(1407),)), _player((_character(3203),))),
        events=(bad,),
    )
    assert history.consume(context) == []
