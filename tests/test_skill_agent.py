from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from reps.action_adapter import BuiltDecisionContext
from reps.schema import DecisionContext, DecisionType, LowLevelActionSpec, OptionKind
from skillloop.agent import AgentConfig, SkillAgentPolicy, parse_choice
from skillloop.library import SkillLibrary
from skillloop.render import StateRenderer, TaskSpec
from skillloop.schema import Skill, SkillConditions


# -- fakes -------------------------------------------------------------------
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
    def __init__(self, reply: str | None = '{"choice": 1}') -> None:
        self.reply = reply
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, messages, **_kwargs) -> str | None:
        self.calls.append(messages)
        return self.reply


class _Base:
    name = "base"

    def __init__(self) -> None:
        self.calls = 0

    def choose(self, built) -> int:
        self.calls += 1
        return int(built.context.legal_low_level_codes[0])


def _built(n: int = 3, request: DecisionType = DecisionType.ACTION) -> BuiltDecisionContext:
    specs = tuple(
        LowLevelActionSpec(
            action_code=100 + i,
            request_type=request,
            kind=OptionKind.ACTION_USE_SKILL,
            label=f"skill{i}",
            subject_definition_id=i,
        )
        for i in range(n)
    )
    context = DecisionContext(
        acting_player=0,
        request_type=request,
        step_index=7,
        full_state=None,      # renderer only touches player_view (None -> placeholder)
        legal_low_level_codes=tuple(spec.action_code for spec in specs),
        legal_low_level_specs=specs,
        legal_low_level_mask=tuple(True for _ in specs),
    )
    return BuiltDecisionContext(
        context=context,
        payload_by_low_level_code={},
        label_by_low_level_code={},
        low_to_high_code={},
    )


def _agent(client: _FakeClient, *, library: SkillLibrary | None = None, **config) -> SkillAgentPolicy:
    task = TaskSpec(stage="wagner", display="瓦格纳", objectives=("5回合内胜利",), deck="superconduct_aggro")
    return SkillAgentPolicy(
        _Base(),
        client,
        renderer=StateRenderer(_FakeAssets(), opponent_name="NPC"),
        task=task,
        library=library,
        config=AgentConfig(**config),
    )


# -- parse -------------------------------------------------------------------
def test_parse_choice_accepts_valid_json():
    index, used, error = parse_choice('{"choice": 2, "used": ["a", "b", "c"]}', 4)
    assert (index, used, error) == (2, ("a", "b"), None)  # capped at 2 ids


def test_parse_choice_rejects_out_of_range_and_garbage():
    assert parse_choice('{"choice": 9}', 3) == (None, (), "choice_out_of_range")
    assert parse_choice("not json", 3) == (None, (), "unparseable")
    assert parse_choice(None, 3) == (None, (), "empty_response")
    assert parse_choice('{"choice": "x"}', 3) == (None, (), "choice_not_int")


def test_parse_choice_accepts_single_string_used():
    index, used, error = parse_choice('{"choice": 0, "used": "only_one"}', 2)
    assert (index, used, error) == (0, ("only_one",), None)


# -- policy ------------------------------------------------------------------
def test_agent_maps_choice_index_to_action_code():
    client = _FakeClient('{"choice": 2}')
    agent = _agent(client)
    code = agent.choose(_built(3))
    assert code == 102                      # third candidate
    assert len(client.calls) == 1
    assert agent.decisions[0].n_candidates == 3


def test_agent_falls_back_on_unparseable_reply():
    client = _FakeClient("garbage")
    agent = _agent(client)
    code = agent.choose(_built(3))
    assert code == 100                      # base policy's first legal action
    assert agent.decisions[0].error == "unparseable"


def test_agent_skips_forced_and_disallowed_requests():
    client = _FakeClient()
    agent = _agent(client)
    assert agent.choose(_built(1)) == 100   # only one legal action -> no call
    assert client.calls == []
    mulligan = _built(3, request=DecisionType.SELECT_CARD)
    assert agent.choose(mulligan) == 100    # request type not in config
    assert client.calls == []


def test_agent_injects_skills_and_filters_citations():
    lib = SkillLibrary.from_skills([
        Skill(skill_id="speed_equip", skill_name="速攻少装备", description="速攻关只打一张装备牌"),
        Skill(skill_id="other", skill_name="其他", description="换人前先看对方爆发"),
    ])
    client = _FakeClient('{"choice": 0, "used": ["speed_equip", "invented"]}')
    agent = _agent(client, library=lib, top_k=2)
    agent.choose(_built(3))
    system = client.calls[0][0]["content"]
    assert "speed_equip" in system
    assert agent.decisions[0].used == ("speed_equip",)   # unknown id dropped


def test_system_prefix_is_built_once_per_game():
    client = _FakeClient('{"choice": 0}')
    agent = _agent(client)
    agent.choose(_built(3))
    first = agent.decisions[0].system_hash
    agent.choose(_built(3))
    assert agent.decisions[1].system_hash == first
    assert len(client.calls) == 2
    assert client.calls[0][0]["content"] == client.calls[1][0]["content"]


def test_call_budget_is_enforced():
    client = _FakeClient('{"choice": 0}')
    agent = _agent(client, max_calls_per_game=1)
    agent.choose(_built(3))
    agent.choose(_built(3))
    assert len(client.calls) == 1


def test_retrieval_matches_machine_objective_types_not_prompt_text():
    lib = SkillLibrary.from_skills([
        Skill(
            skill_id="speed",
            skill_name="速攻别拖",
            description="速攻关卡不要拖到后期",
            conditions=SkillConditions(objective_types=("win_within_rounds",)),
            layer="strategy",
        )
    ])
    client = _FakeClient('{"choice": 0}')

    def agent_for(types):
        task = TaskSpec(stage="wagner", display="瓦格纳", objectives=("5回合内胜利",),
                        deck="x", objective_types=types)
        return SkillAgentPolicy(_Base(), client, renderer=StateRenderer(_FakeAssets(), opponent_name="NPC"),
                                task=task, library=lib, config=AgentConfig(top_k=3))

    agent_for(("win_within_rounds",)).choose(_built(3))
    assert "speed" in client.calls[0][0]["content"]

    other = _FakeClient('{"choice": 0}')
    task = TaskSpec(stage="wagner", display="瓦格纳", objectives=("赢得胜利",), deck="x",
                    objective_types=("win",))
    SkillAgentPolicy(_Base(), other, renderer=StateRenderer(_FakeAssets(), opponent_name="NPC"),
                     task=task, library=lib, config=AgentConfig(top_k=3)).choose(_built(3))
    assert "speed" not in other.calls[0][0]["content"]
