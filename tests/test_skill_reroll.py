from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from envs.decks import deck_spec
from envs.match import run_match
from envs.policy import expert_policy
from reps.action_adapter import build_decision_context
from reps.schema import (
    CharacterSnapshot,
    DecisionType,
    PlayerSnapshot,
    StateSnapshot,
)
from skillloop.agent import AgentConfig, SkillAgentPolicy
from skillloop.cards import character_element
from skillloop.render import StateRenderer, TaskSpec
from skillloop.reroll import keep_dice_values, preferred_reroll_code

# 刻晴 (Electro), 柯莱 (Dendro), 菲谢尔 (Electro)
KEQING = 1403
COLLEI = 1701
FISCHL = 1401


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


class _FakeClient:
    """No LLM is ever called in these tests; a reply is kept for safety."""

    def __init__(self, reply: str | None = None) -> None:
        self.reply = reply
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return self.reply


def _character(character_id: int, definition_id: int, *, active: bool = False) -> CharacterSnapshot:
    return CharacterSnapshot(
        id=character_id,
        definition_id=definition_id,
        health=10,
        max_health=10,
        energy=0,
        max_energy=3,
        defeated=False,
        is_active=active,
    )


def _player(*definition_ids: int, dice: tuple[int, ...] = ()) -> PlayerSnapshot:
    characters = tuple(
        _character(index + 1, definition_id, active=index == 0)
        for index, definition_id in enumerate(definition_ids)
    )
    return PlayerSnapshot(
        active_character_id=1,
        characters=characters,
        combat_statuses=(),
        summons=(),
        supports=(),
        hand_cards=(),
        pile_cards=(),
        dice=dice,
        declared_end=False,
        legend_used=False,
    )


def _view(
    dice: tuple[int, ...],
    *definition_ids: int,
    opponent_definition_ids: tuple[int, ...] = (1407,),
    opponent_dice: tuple[int, ...] = (),
) -> StateSnapshot:
    return StateSnapshot(
        phase="reroll",
        round_number=1,
        current_turn=0,
        winner=None,
        players=(
            _player(*definition_ids, dice=dice),
            _player(*opponent_definition_ids, dice=opponent_dice),
        ),
    )


def _built(view: StateSnapshot, acting_player: int = 0):
    return build_decision_context(
        acting_player=acting_player,
        request_type=DecisionType.REROLL_DICE,
        request=None,  # engine request is unused by the reroll enumerator
        full_state=view,
        player_view=view,
        full_state_json=None,
        step_index=1,
    )


def test_keep_dice_values_keeps_omni_and_team_elements():
    view = _view((1, 1, 4, 4, 7, 8, 8, 2), KEQING, COLLEI, FISCHL)
    assert keep_dice_values(view) == frozenset({4, 7, 8})
    assert character_element(KEQING) == "雷"
    assert character_element(COLLEI) == "草"


def test_keep_dice_values_follows_the_acting_player():
    """Seat 1 has a different team, so its keep set must not be seat 0's."""
    view = _view(
        (1, 1, 4, 7, 8, 2, 3),
        KEQING,
        COLLEI,
        FISCHL,
        opponent_definition_ids=(1101, 1407),  # 甘雨/冰 + 雷电将军/雷
        opponent_dice=(1, 1, 4, 7, 8, 2, 3),
    )
    assert keep_dice_values(view, 0) == frozenset({4, 7, 8})
    assert keep_dice_values(view, 1) == frozenset({1, 4, 8})

    p0_built = _built(view, acting_player=0)
    p1_built = _built(view, acting_player=1)
    p0_code = preferred_reroll_code(p0_built)
    p1_code = preferred_reroll_code(p1_built)
    assert p0_code is not None and p1_code is not None
    assert sorted(p0_built.payload_by_low_level_code[p0_code]["dice_to_reroll"]) == [1, 1, 2, 3]
    assert sorted(p1_built.payload_by_low_level_code[p1_code]["dice_to_reroll"]) == [2, 3, 7]


def test_preferred_reroll_code_discards_exactly_non_keep_dice():
    view = _view((1, 1, 4, 4, 7, 8, 8, 2), KEQING, COLLEI, FISCHL)
    built = _built(view)
    code = preferred_reroll_code(built)
    assert code is not None
    payload = built.payload_by_low_level_code[code]
    assert sorted(payload["dice_to_reroll"]) == [1, 1, 2]
    # The chosen candidate is legal and carries the corresponding mask.
    assert code in built.context.legal_low_level_codes
    spec = next(
        spec
        for candidate, spec in zip(built.context.legal_low_level_codes, built.context.legal_low_level_specs)
        if int(candidate) == int(code)
    )
    assert spec.reroll_dice_mask != 0


def test_preferred_reroll_code_returns_keep_all_when_nothing_discardable():
    view = _view((4, 7, 8, 8), KEQING, COLLEI, FISCHL)
    built = _built(view)
    code = preferred_reroll_code(built)
    assert code is not None
    assert built.payload_by_low_level_code[code]["dice_to_reroll"] == []


def test_preferred_reroll_code_falls_back_without_view():
    built = SimpleNamespace(context=SimpleNamespace(player_view=None))
    assert preferred_reroll_code(built) is None


def test_agent_choose_uses_fixed_reroll_rule_without_llm_call():
    view = _view((1, 1, 4, 4, 7, 8, 8, 2), KEQING, COLLEI, FISCHL)
    built = _built(view)
    client = _FakeClient()
    agent = SkillAgentPolicy(
        expert_policy("superconduct_aggro"),
        client,
        renderer=StateRenderer(_FakeAssets(), opponent_name="NPC"),
        task=TaskSpec(stage="smoke", display="smoke", objectives=("win",), deck="superconduct_aggro"),
        config=AgentConfig(override_stages=("__never__",)),
    )
    code = agent.choose(built)
    assert code == preferred_reroll_code(built)
    assert built.payload_by_low_level_code[code]["dice_to_reroll"] == [1, 1, 2]
    assert client.calls == []
    assert agent.last_decider == "script"


def _capture_first_p0_reroll(policy, who: int = 0):
    """Wrap a policy and record (visible dice, reroll payload) on its first reroll."""
    captured: dict[str, tuple[tuple[int, ...], tuple[int, ...]]] = {}

    class _Recorder:
        name = policy.name

        def choose(self, built):
            code = policy.choose(built)
            if (
                who == 0
                and "first" not in captured
                and built.context.request_type == DecisionType.REROLL_DICE
            ):
                dice = tuple(int(d) for d in built.context.player_view.players[0].dice)
                payload = built.payload_by_low_level_code.get(int(code), {})
                captured["first"] = (dice, tuple(int(d) for d in payload.get("dice_to_reroll", ())))
            return code

    return _Recorder(), captured


def test_run_match_p0_first_reroll_discards_every_non_keep_die():
    deck = deck_spec("superconduct_aggro")
    p0 = SkillAgentPolicy(
        expert_policy("superconduct_aggro"),
        _FakeClient(),
        renderer=StateRenderer(_FakeAssets(), opponent_name="NPC"),
        task=TaskSpec(stage="smoke", display="smoke", objectives=("win",), deck="superconduct_aggro"),
        config=AgentConfig(override_stages=("__never__",)),
    )
    wrapped, captured = _capture_first_p0_reroll(p0)
    run_match(
        deck,
        deck,
        wrapped,
        expert_policy("superconduct_aggro"),
        seed=1,
        player1_always_omni=False,
    )
    assert "first" in captured, "P0 never reached a reroll decision"
    dice, rerolled = captured["first"]
    keep = keep_dice_values(_view(dice, *(int(c) for c in deck.characters)))
    expected = tuple(sorted(die for die in dice if die not in keep))
    assert tuple(sorted(rerolled)) == expected
    assert all(die not in keep for die in rerolled)
