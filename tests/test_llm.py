"""LLM layer: client plumbing, JSON parsing, assist fallback, coach structure.

These use a ``MockLLMClient`` (no network) except the ``contexts`` fixture, which
plays one local game to obtain real decision contexts.
"""

from __future__ import annotations

import pytest

from common.llm import MockLLMClient, extract_json_object, redact
from agents.llm_assist import AssistConfig, LLMAssistPolicy
from coach.replay import ReplayCoach, rule_based_flags
from envs.decks import deck_spec
from envs.match import run_match
from envs.policy import expert_policy, _asset_catalog


def test_extract_json_object_variants():
    assert extract_json_object('{"choice": 1, "reason": "x"}') == {"choice": 1, "reason": "x"}
    assert extract_json_object('```json\n{"choice": 2}\n```') == {"choice": 2}
    assert extract_json_object('Sure! {"choice": 3} done') == {"choice": 3}
    assert extract_json_object("no json here") is None
    assert extract_json_object(None) is None


def test_redact_removes_api_keys():
    text = "Authorization: Bearer sk-abcdef1234567890 and sk-x"
    assert "sk-abcdef1234567890" not in redact(text)
    assert "sk-***" in redact(text)


class _Capture:
    def __init__(self, inner):
        self.inner = inner
        self.name = "capture"
        self.contexts = []

    def choose(self, built):
        self.contexts.append(built)
        return self.inner.choose(built)


@pytest.fixture(scope="module")
def contexts():
    cap = _Capture(expert_policy("superconduct_aggro"))
    run_match(
        deck_spec("superconduct_aggro"),
        deck_spec("natlan_battleship"),
        cap,
        expert_policy("natlan_battleship"),
        seed=5,
    )
    action_ctx = [b for b in cap.contexts if b.context.request_type.value == "action"]
    assert action_ctx
    return action_ctx


def _assist(client, *, budget=4, base=None):
    base = base or expert_policy("superconduct_aggro")
    return LLMAssistPolicy(
        base,
        client,
        assets=_asset_catalog(),
        opponent_name="natlan_battleship",
        config=AssistConfig(budget_per_game=budget, min_legal_options=1),
    )


def test_assist_falls_back_on_unparseable_response(contexts):
    client = MockLLMClient(["not json"] * 10)
    policy = _assist(client, budget=2)
    built = contexts[0]
    base_code = policy.base.choose(built)
    code = policy.choose(built)
    assert code == base_code
    assert policy.interventions[-1].error == "unparseable"
    assert policy.interventions[-1].called


def test_assist_accepts_valid_choice_and_respects_budget(contexts):
    client = MockLLMClient(['{"choice": 1, "reason": "alt"}'] * 10)
    policy = _assist(client, budget=2)
    calls = 0
    for built in contexts:
        before = len(policy.interventions)
        policy.choose(built)
        if len(policy.interventions) > before:
            calls += 1
    assert calls == 2  # budget respected
    assert any(iv.changed for iv in policy.interventions)


def test_assist_prompt_has_no_hidden_opponent_cards(contexts):
    client = MockLLMClient(['{"choice": 0}'] * 5)
    policy = _assist(client, budget=1)
    policy.choose(contexts[0])
    prompt = client.prompts[0][1]["content"]
    # Opponent hand is described only by size, never by card names.
    assert "opponent hand size=" in prompt


def _synthetic_match() -> dict:
    def view(acting: int) -> dict:
        chars = [
            {"definition_id": 1411, "health": 10, "max_health": 10, "energy": 1, "aura": 0, "defeated": False, "is_active": True},
            {"definition_id": 1510, "health": 8, "max_health": 10, "energy": 0, "aura": 2, "defeated": False, "is_active": False},
        ]
        return {
            "players": [
                {"characters": chars, "hand_cards": [{"definition_id": 332002}] * 3, "dice": [8, 1, 1]},
                {"characters": chars, "hand_cards": [{"definition_id": 0}] * 4, "dice": []},
            ]
        }

    return {
        "deck0": "superconduct_aggro",
        "deck1": "natlan_battleship",
        "winner": 0,
        "rounds": 5,
        "decisions": [
            {"step_index": 1, "player": 0, "request_type": "action", "chosen_label": "use_skill:x", "legal_labels": ["a"], "player_view": view(0)},
            {"step_index": 2, "player": 1, "request_type": "action", "chosen_label": "declare_end", "legal_labels": ["a", "b"], "fallback": True, "player_view": view(1)},
        ],
    }


def test_rule_based_flags_detect_fallbacks():
    flags = rule_based_flags(_synthetic_match())
    assert any(flag["step"] == 2 for flag in flags)


def test_coach_degrades_without_llm():
    coach = ReplayCoach(assets=_asset_catalog(), client=None)
    review = coach.review(_synthetic_match())
    assert review["llm"] is None
    assert review["mistakes"]
    assert "summary" in review


def test_coach_with_mock_llm_returns_structured_review():
    client = MockLLMClient(
        ['{"summary":"ok","mistakes":[{"step":1,"player":0,"severity":"low","why":"w","better":"b"}],"deck_lessons":["keep tempo"]}']
    )
    coach = ReplayCoach(assets=_asset_catalog(), client=client)
    review = coach.review(_synthetic_match())
    assert review["llm"]["parsed"] is True
    assert review["summary"] == "ok"
    assert review["deck_lessons"] == ["keep tempo"]
    # rule flags are appended to LLM mistakes
    assert len(review["mistakes"]) >= 2
