from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from envs.decks import deck_spec
from reps.action_adapter import BuiltDecisionContext
from reps.schema import DecisionContext, DecisionType, LowLevelActionSpec, OptionKind
from skillloop.agent import AgentConfig, SkillAgentPolicy, parse_choice, parse_note
from skillloop.cards import card_kind
from skillloop.library import SkillLibrary
from skillloop.render import ObjectiveDetail, StateRenderer, TaskSpec
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
    def __init__(self, reply: str | None | list[str | None] = '{"choice": 1}') -> None:
        self.reply = reply
        self.calls: list[list[dict[str, str]]] = []
        self.kwargs: list[dict[str, Any]] = []

    def chat(self, messages, **kwargs) -> str | None:
        self.calls.append(messages)
        self.kwargs.append(kwargs)
        if isinstance(self.reply, list):
            return self.reply[min(len(self.calls) - 1, len(self.reply) - 1)]
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


def test_agent_sends_no_thinking_and_small_json_budget():
    client = _FakeClient('{"choice": 1}')
    agent = _agent(client)
    agent.choose(_built(3))
    kwargs = client.kwargs[0]
    assert kwargs["reasoning_effort"] == "none"   # in-game decisions never think
    assert kwargs["max_tokens"] == 64             # pure JSON reply, no reasoning budget
    assert AgentConfig().max_tokens == 64


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


def test_long_skill_description_reaches_system_prompt_verbatim():
    """Detailed extracted prompts must not be truncated on the way into the model."""
    long_description = (
        "起手：先找武器牌，没有就换掉全部非角色牌。" * 20
        + "自查：如果第3回合还没打出第一件装备，立刻停止铺场，转去抢血。END_MARKER"
    )
    lib = SkillLibrary.from_skills([
        Skill(skill_id="long_plan", skill_name="长提示", description=long_description),
    ])
    client = _FakeClient('{"choice": 0}')
    agent = _agent(client, library=lib, top_k=1)
    agent.choose(_built(3))

    system = client.calls[0][0]["content"]
    assert len(long_description) > 400
    assert long_description in system             # render.py injects the full text


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


# -- pinned loadout ----------------------------------------------------------
def test_pinned_skills_replace_retrieval_and_keep_order():
    lib = SkillLibrary.from_skills([
        Skill(skill_id="alpha", skill_name="甲", description="第一"),
        Skill(skill_id="beta", skill_name="乙", description="第二"),
        Skill(skill_id="unpinned", skill_name="丙", description="不应出现"),
    ])
    client = _FakeClient('{"choice": 0}')
    agent = _agent(
        client, library=lib, top_k=3, pinned_skills=("beta", "alpha", "missing"),
    )
    agent.choose(_built(3))
    system = client.calls[0][0]["content"]
    assert "alpha" in system and "beta" in system
    assert "unpinned" not in system
    assert system.index("beta") < system.index("alpha")   # prompt order == pinned order


def test_pinned_rule_is_not_duplicated_and_remains_citable():
    """A loadout may pin rule-layer skills (e.g. ``rule_burst``).

    The pinned block keeps it for citation accounting, but the always-on rules note
    must not inject the same block a second time.
    """
    lib = SkillLibrary.from_skills([
        Skill(
            skill_id="rule_burst", skill_name="元素爆发", description="爆发需要能量",
            layer="rule",
        ),
        Skill(
            skill_id="strat_burst", skill_name="爆发时机", description="留骰子给爆发",
            layer="strategy",
        ),
    ])
    client = _FakeClient('{"choice": 0, "used": ["rule_burst", "strat_burst"]}')
    agent = _agent(
        client, library=lib, pinned_skills=("rule_burst", "strat_burst"),
    )
    agent.choose(_built(3))

    system = client.calls[0][0]["content"]
    assert system.count("- [rule_burst]") == 1
    assert system.count("- [strat_burst]") == 1
    assert agent.decisions[-1].used == ("rule_burst", "strat_burst")


def test_pinned_skills_do_not_change_across_decisions():
    lib = SkillLibrary.from_skills([
        Skill(skill_id="alpha", skill_name="甲", description="第一"),
        Skill(skill_id="beta", skill_name="乙", description="第二"),
    ])
    client = _FakeClient('{"choice": 0}')
    agent = _agent(client, library=lib, pinned_skills=("alpha",))
    agent.choose(_built(3))
    first_system = client.calls[0][0]["content"]
    first_hash = agent.decisions[0].system_hash
    agent.choose(_built(3))
    assert client.calls[1][0]["content"] == first_system
    assert agent.decisions[1].system_hash == first_hash


# -- per-decision progress block --------------------------------------------
def _built_cards(card_ids: tuple[int, ...]) -> BuiltDecisionContext:
    specs = tuple(
        LowLevelActionSpec(
            action_code=200 + index,
            request_type=DecisionType.ACTION,
            kind=OptionKind.ACTION_PLAY_CARD,
            label=f"card{card_id}",
            subject_definition_id=int(card_id),
        )
        for index, card_id in enumerate(card_ids)
    )
    context = DecisionContext(
        acting_player=0,
        request_type=DecisionType.ACTION,
        step_index=8,
        full_state=None,
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


def test_progress_block_is_in_user_message_and_counts_our_card_plays():
    weapons = list(dict.fromkeys(
        card_id for card_id in deck_spec("superconduct_mika_attack").cards
        if card_kind(int(card_id)) == "weapon"
    ))[:2]
    assert len(weapons) == 2

    task = TaskSpec(
        stage="wagner",
        display="瓦格纳",
        objectives=("赢得胜利", "打出2张武器牌", "5回合内胜利"),
        deck="superconduct_mika_attack",
        objective_types=("win", "play_equipment_card", "win_within_rounds"),
        max_rounds=5,
        objective_details=(
            ObjectiveDetail("win", "赢得胜利"),
            ObjectiveDetail("play_equipment_card", "打出2张武器牌", count=2, kind="weapon"),
            ObjectiveDetail("win_within_rounds", "5回合内胜利", n=5),
        ),
    )
    client = _FakeClient('{"choice": 0}')
    agent = SkillAgentPolicy(
        _Base(), client, renderer=StateRenderer(_FakeAssets(), opponent_name="NPC"),
        task=task, config=AgentConfig(),
    )
    agent.choose(_built_cards(tuple(weapons)))
    first = client.calls[0][1]["content"]
    assert first.startswith("回合 ")
    assert "未完成目标：" in first
    assert "打出2张武器牌（我方已打0/2）" in first
    assert "赢得胜利（未知）" in first

    agent.choose(_built_cards(tuple(weapons)))
    second = client.calls[1][1]["content"]
    assert "打出2张武器牌（我方已打1/2）" in second
    # system prefix is still byte-identical; only the user message moved
    assert client.calls[0][0]["content"] == client.calls[1][0]["content"]


# -- optional in-game scratchpad --------------------------------------------
def test_parse_note_flattens_and_ignores_bad_values():
    assert parse_note('{"choice": 0, "note": "先打武器\\n  再抢血"}') == "先打武器 再抢血"
    assert parse_note('{"choice": 0}') == ""
    assert parse_note('{"choice": 0, "note": 7}') == ""
    assert parse_note("garbage") == ""
    assert parse_note(None) == ""


def test_scratchpad_off_keeps_prompt_and_system_hash_unchanged():
    """Default arm must be byte-identical to a run without the flag at all."""
    task = TaskSpec(stage="wagner", display="瓦格纳", objectives=("5回合内胜利",), deck="x")
    client = _FakeClient('{"choice": 0}')

    def build(**config):
        return SkillAgentPolicy(
            _Base(), client,
            renderer=StateRenderer(_FakeAssets(), opponent_name="NPC"),
            task=task, config=AgentConfig(**config),
        )

    implicit = build()
    explicit = build(scratchpad=False)
    expected_system = StateRenderer(_FakeAssets(), opponent_name="NPC").system_message(
        task=task, skills=(), rules_note=""
    )["content"]
    for agent in (implicit, explicit):
        agent.choose(_built(3))
        assert client.calls[-1][0]["content"] == expected_system
        assert "记录板" not in client.calls[-1][0]["content"]
        assert "记录板" not in client.calls[-1][1]["content"]
    assert implicit.decisions[0].system_hash == explicit.decisions[0].system_hash


def test_scratchpad_note_is_replayed_and_overwrites_previous_note():
    client = _FakeClient([
        '{"choice": 0, "note": "第一版：先铺场\\n再看爆发"}',
        '{"choice": 1, "note": "第二版：转抢血"}',
        '{"choice": 2}',                       # no note -> keep the latest one
    ])
    agent = _agent(client, scratchpad=True)

    agent.choose(_built(3))
    first_user = client.calls[0][1]["content"]
    assert first_user.startswith("本局记录板（上一决策）：\n（空；请先写本局路线与当前轮计划）\n\n")
    assert "记录板" in client.calls[0][0]["content"]       # widened output contract
    assert agent.decisions[0].note == "第一版：先铺场 再看爆发"
    assert agent.decisions[0].scratchpad_chars == len("第一版：先铺场 再看爆发")

    agent.choose(_built(3))
    second_user = client.calls[1][1]["content"]
    assert second_user.startswith("本局记录板（上一决策）：\n第一版：先铺场 再看爆发\n\n")
    assert agent.decisions[1].note == "第二版：转抢血"

    agent.choose(_built(3))
    third_user = client.calls[2][1]["content"]
    assert third_user.startswith("本局记录板（上一决策）：\n第二版：转抢血\n\n")
    assert "第一版" not in third_user
    assert agent.decisions[2].note == ""                    # no note in this reply
    assert agent._scratchpad == "第二版：转抢血"            # previous note retained
    # The system prefix is still stable across decisions when the scratchpad moves.
    assert client.calls[0][0]["content"] == client.calls[2][0]["content"]


def test_scratchpad_note_is_truncated_and_does_not_change_choice():
    long_note = "计" * 40
    client = _FakeClient(f'{{"choice": 2, "note": "{long_note}"}}')
    agent = _agent(client, scratchpad=True, note_max_chars=10, scratchpad_max_chars=10)
    code = agent.choose(_built(3))
    assert code == 102                                      # choice parsing unaffected
    assert agent.decisions[0].note == "计" * 10
    assert agent._scratchpad == "计" * 10
    assert agent.decisions[0].scratchpad_chars == 10


def test_scratchpad_bad_note_does_not_affect_choice():
    client = _FakeClient('{"choice": 1, "note": 42}')       # non-string note
    agent = _agent(client, scratchpad=True)
    assert agent.choose(_built(3)) == 101
    assert agent.decisions[0].choice == 1
    assert agent.decisions[0].note == ""
    assert agent._scratchpad == ""


def test_decision_as_dict_includes_note_and_scratchpad_chars():
    client = _FakeClient('{"choice": 0, "note": "计划"}')
    agent = _agent(client, scratchpad=True)
    agent.choose(_built(3))
    payload = agent.decisions[0].as_dict()
    assert payload["note"] == "计划"
    assert payload["scratchpad_chars"] == len("计划")


def test_scratchpad_is_per_policy_instance():
    client = _FakeClient(['{"choice": 0, "note": "我这一局的路线"}', '{"choice": 0}'])
    first = _agent(client, scratchpad=True)
    first.choose(_built(3))
    second = _agent(client, scratchpad=True)
    second.choose(_built(3))
    assert second._scratchpad == ""
    assert client.calls[1][1]["content"].startswith(
        "本局记录板（上一决策）：\n（空；请先写本局路线与当前轮计划）"
    )
