"""Offline proofs for the parked-game scheduler (``skillloop.parked``).

Everything here runs without network access: ``StubClient`` implements the tiny
``chat``/``usage`` surface the scheduler needs and returns a fixed answer. The main
test compares a small grid played through ``skillloop.run``'s synchronous policy path
with the same grid played by ``ParkedRunner``; the remaining tests pin the specific
bookkeeping fixes (per-game budget, fallback, usage metering, one-shot library load,
stable prompt prefix).
"""

from __future__ import annotations

import threading
import time

import pytest

import common.llm
import skillloop.run as run_module
from common.llm import LLMUsage
from envs.decks import deck_spec
from envs.match import run_match
from envs.policy import expert_policy
from npc.policy import npc_policy_for_stage
from npc.spec import get_stage
from skillloop.agent import AgentConfig
from skillloop.library import SkillLibrary
from skillloop.parked import ParkedGame, ParkedRunner
from skillloop.run import RunSpec
from skillloop.schema import Skill, SkillConditions

DECK = "superconduct_aggro"
STAGES = ("timmie", "blanche")
SEEDS = (0, 1)


@pytest.fixture(autouse=True)
def _isolate_llm_env(monkeypatch):
    """Never let the developer's shell set the spend cap for these tests."""
    monkeypatch.delenv("GITCG_LLM_BUDGET_USD", raising=False)


# -- offline client -----------------------------------------------------------
class StubClient:
    """Deterministic stand-in for ``common.llm.LLMClient``.

    ``reply`` may be a string or ``callable(index) -> str | None``. Token usage is
    reported through the real ``LLMUsage.record`` interface, so the scheduler's
    thread-local metering is exercised exactly like the real client.
    """

    def __init__(self, reply: str | None = '{"choice": 0}', *, prompt_tokens: int = 7,
                 completion_tokens: int = 3, cached_tokens: int = 2,
                 cost_usd: float = 0.0001, sleep: float = 0.0) -> None:
        self.reply = reply
        self.usage = LLMUsage()
        self.calls = 0
        self.prompts: list[list[dict[str, str]]] = []
        self.kwargs: list[dict] = []
        self._lock = threading.Lock()
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens
        self._cached_tokens = cached_tokens
        self._cost_usd = cost_usd
        self._sleep = sleep

    def chat(self, messages, **kwargs) -> str | None:
        with self._lock:
            index = self.calls
            self.calls += 1
            self.prompts.append(list(messages))
            self.kwargs.append(dict(kwargs))
        if self._sleep:
            time.sleep(self._sleep)
        reply = self.reply(index) if callable(self.reply) else self.reply
        if reply is not None:
            self.usage.record(
                prompt=self._prompt_tokens,
                completion=self._completion_tokens,
                seconds=self._sleep or 0.001,
                cached=self._cached_tokens,
                cost=self._cost_usd,
            )
        return reply


def _spec(*, group: str = "llm_empty", max_calls: int = 80,
          library_root: str | None = None) -> RunSpec:
    return RunSpec(
        group=group,
        stages=STAGES,
        seeds=SEEDS,
        deck=DECK,
        max_calls=max_calls,
        library_root=library_root,
        out="data/runs/unused-in-tests.jsonl",
    )


def _task(spec: RunSpec, stage_name: str = "timmie", seed: int = 0,
          task_index: int = 0) -> ParkedGame:
    return ParkedGame(stage=get_stage(stage_name), seed=seed, spec=spec, task_index=task_index)


def _run_task(spec: RunSpec, client: StubClient, *, stage_name: str = "timmie",
              seed: int = 0, concurrency: int = 2) -> tuple[ParkedGame, dict]:
    task = _task(spec, stage_name, seed)
    runner = ParkedRunner(spec, concurrency=concurrency, client=client)
    rows = runner.run([task], progress=False)
    assert len(rows) == 1
    return task, rows[0]


def _decision_key(decisions) -> list[tuple]:
    """Code- and index-independent fingerprint of one player's decision stream.

    Action codes depend on the process-global codebook, and the engine's
    ``ActionRequest.action`` order can differ after a resume (the scheduler stores
    low-level codes, not engine indices, so the *semantic* choice is preserved).
    Compare the semantic fields instead of ``chosen_label``'s action index.
    """
    return [
        (
            record.request_type,
            record.chosen_kind,
            record.subject_definition_id,
            record.card_kind,
            tuple(sorted(record.used_dice)),
        )
        for record in decisions
        if record.player == 0
    ]


def _event_key(events) -> list[tuple]:
    return [
        (event.kind, event.player, event.round_number, event.skill_definition_id,
         event.skill_type, event.reaction_type, event.source_definition_id)
        for event in events
    ]


# -- parity with the synchronous runner --------------------------------------
def test_parked_rows_match_synchronous_runner(monkeypatch):
    """2 stages x 2 seeds: same stub client, same rows, same decision/event stream.

    The parked scheduler can only park ``action`` requests, so the synchronous agent
    is restricted to the same request types; otherwise it would also ask the model
    during the mulligan / active-character choice and the two runs would differ by
    design rather than by a scheduler bug.
    """
    real_config = AgentConfig
    real_match = run_module.run_match

    def synchronous_config(**kwargs):
        kwargs.setdefault("request_types", ("action",))
        return real_config(**kwargs)

    captured: dict[tuple[str, int], object] = {}
    current: dict[str, object] = {}

    def spy_match(*args, **kwargs):
        # Record decisions as well: play_one itself does not need them, but the
        # comparison below does. Recording is side-effect free.
        kwargs["record_decisions"] = True
        record = real_match(*args, **kwargs)
        captured[(str(current["stage"]), int(current["seed"]))] = record
        return record

    monkeypatch.setattr(run_module, "AgentConfig", synchronous_config)
    monkeypatch.setattr(run_module, "run_match", spy_match)
    monkeypatch.setattr(common.llm, "build_llm_client", lambda: StubClient())

    spec = _spec()
    sync_rows = []
    pairs = [(stage, seed) for stage in STAGES for seed in SEEDS]
    for index, (stage_name, seed) in enumerate(pairs):
        current.update(stage=stage_name, seed=seed)
        sync_rows.append(run_module.play_one(
            {"spec": spec.__dict__, "stage": stage_name, "seed": seed, "task_index": index}
        ))

    tasks = [
        _task(spec, stage_name, seed, task_index=index)
        for index, (stage_name, seed) in enumerate(pairs)
    ]
    runner = ParkedRunner(spec, concurrency=4, client=StubClient())
    parked_rows = runner.run(tasks, progress=False)

    sync_by_key = {(row["stage"], row["seed"]): row for row in sync_rows}
    parked_by_key = {(row["stage"], row["seed"]): row for row in parked_rows}
    task_by_key = {(task.stage.name, task.seed): task for task in tasks}

    assert set(sync_by_key) == set(parked_by_key) == set(task_by_key)
    for key, sync_row in sync_by_key.items():
        parked_row = parked_by_key[key]
        # compare_groups.py only needs a stable shape; keep it identical.
        assert set(sync_row) == set(parked_row)
        for field in ("task_index", "winner", "rounds", "three_star", "stars",
                      "n_objectives", "objectives", "error", "truncated",
                      "llm_calls", "llm_errors"):
            assert sync_row[field] == parked_row[field], f"{key} {field}"
        assert not parked_row["truncated"]

        sync_record = captured[key]
        parked_task = task_by_key[key]
        assert _decision_key(sync_record.decisions) == _decision_key(parked_task.decisions)
        assert _event_key(sync_record.events) == _event_key(parked_task.events)


# -- per-game budget ----------------------------------------------------------
def test_call_cap_is_per_game_across_resumes():
    """cap=1 means exactly one model call for the whole game, not one per segment."""
    spec = _spec(max_calls=1)
    client = StubClient('{"choice": 0}')
    task, row = _run_task(spec, client)

    assert row["llm_calls"] == 1
    assert client.calls == 1
    assert row["error"] is None
    assert task.final_state is not None          # the game still finished
    assert len(task.decisions) > 1               # resumes happened after the answer


def test_call_cap_survives_many_resumes():
    """A cap of 3 stops parking after three calls even though there are more actions."""
    spec = _spec(max_calls=3)
    client = StubClient('{"choice": 0}')
    task, row = _run_task(spec, client)

    assert row["llm_calls"] == client.calls == 3
    assert row["error"] is None


# -- failed model answer ------------------------------------------------------
def test_unparsable_answer_falls_back_to_expert_without_double_counting():
    spec = _spec()
    client = StubClient("this is not json")
    task, row = _run_task(spec, client)

    assert row["error"] is None
    assert row["llm_calls"] > 0
    assert row["llm_errors"] == row["llm_calls"]
    assert row["cited_skills"] == []

    # The same game with a pure-expert policy: the fallback must reproduce it exactly,
    # with one DecisionRecord per real decision (no duplicated parked step).
    stage = get_stage("timmie")
    contestant = deck_spec(DECK)
    npc_deck = type(contestant)(
        name=f"npc_{stage.name}", characters=stage.deck_characters, cards=contestant.cards
    )

    record = run_match(
        contestant,
        npc_deck,
        expert_policy(DECK, seed=0),
        npc_policy_for_stage(stage),
        seed=0,
        record_decisions=True,
        record_events=True,
        record_final_state=True,
    )
    assert _decision_key(record.decisions) == _decision_key(task.decisions)
    assert _event_key(record.events) == _event_key(task.events)
    assert (record.winner, record.rounds) == (task.winner, task.rounds)


# -- usage and wall time ------------------------------------------------------
def test_client_is_built_once_per_run(monkeypatch):
    made: list[StubClient] = []

    def factory():
        client = StubClient('{"choice": 0}')
        made.append(client)
        return client

    monkeypatch.setattr(common.llm, "build_llm_client", factory)
    spec = _spec(max_calls=3)
    tasks = [_task(spec, "timmie", 0), _task(spec, "timmie", 1)]
    rows = ParkedRunner(spec, concurrency=2).run(tasks, progress=False)

    assert len(made) == 1                     # one client for the whole run
    assert len(rows) == 2
    assert sum(row["llm_calls"] for row in rows) == 6


def test_usage_and_llm_seconds_are_measured():
    spec = _spec(max_calls=2)
    client = StubClient('{"choice": 0}', sleep=0.01)
    task, row = _run_task(spec, client)

    assert row["llm_calls"] == 2
    assert row["prompt_tokens"] == 2 * client._prompt_tokens
    assert row["completion_tokens"] == 2 * client._completion_tokens
    assert row["cached_tokens"] == 2 * client._cached_tokens
    assert row["cost_usd"] == round(2 * client._cost_usd, 6)
    assert task.llm_seconds > 0.0
    assert row["llm_seconds"] >= 0.02


def test_concurrent_calls_do_not_mix_usage_deltas():
    """One shared client, two threads: each game's tokens stay its own."""
    spec = _spec(max_calls=2)
    client = StubClient('{"choice": 0}')
    tasks = [_task(spec, "timmie", seed) for seed in (0, 1)]
    runner = ParkedRunner(spec, concurrency=2, client=client)
    rows = runner.run(tasks, progress=False)

    assert len(rows) == 2
    assert client.calls == 4
    for row in rows:
        assert row["llm_calls"] == 2
        assert row["prompt_tokens"] == 2 * client._prompt_tokens
        assert row["completion_tokens"] == 2 * client._completion_tokens


# -- D10 cheap in-game settings ----------------------------------------------
def test_model_calls_inherit_agent_config_defaults(monkeypatch):
    """Parked calls must use the cheap in-game settings, not their own numbers.

    ``GITCG_LLM_REASONING`` is set to a paid value on purpose: ``LLMClient.chat``
    falls back to it when ``reasoning_effort`` is None, so the parked call site has
    to pass the config value explicitly.
    """
    monkeypatch.setenv("GITCG_LLM_REASONING", "low")
    spec = _spec(max_calls=2)
    client = StubClient('{"choice": 0}')
    _task_obj, row = _run_task(spec, client)

    assert row["llm_calls"] == len(client.kwargs) == 2
    assert {kwargs["reasoning_effort"] for kwargs in client.kwargs} == {"none"}
    assert {kwargs["max_tokens"] for kwargs in client.kwargs} == {64}

    # The runner must inherit the dataclass defaults, not restate D10's numbers.
    runner = ParkedRunner(spec, client=client)
    assert runner.config.max_tokens == AgentConfig().max_tokens == 64
    assert runner.config.reasoning_effort == AgentConfig().reasoning_effort == "none"


# -- spend guard --------------------------------------------------------------
def test_budget_stops_launching_new_games(monkeypatch):
    """$0.05 budget at $0.02/game: three games run, the fourth never starts."""
    monkeypatch.setenv("GITCG_LLM_BUDGET_USD", "0.05")
    spec = _spec(max_calls=2)                 # 2 calls x $0.01 = $0.02/game
    client = StubClient('{"choice": 0}', cost_usd=0.01)
    tasks = [_task(spec, "timmie", seed) for seed in (0, 1, 2, 3)]
    runner = ParkedRunner(spec, concurrency=1, client=client)
    rows, summary = runner.run_with_summary(tasks, progress=False)
    assert summary is runner.last_summary

    assert len(rows) == 3
    assert client.calls == 6
    assert all(row["llm_calls"] == 2 and row["error"] is None for row in rows)
    assert tasks[3].calls == 0 and tasks[3].final_state is None   # never launched
    assert summary["aborted"] == "budget"
    assert summary["budget_usd"] == 0.05
    assert summary["spent_usd"] == 0.06
    assert summary["games"] == 3
    assert summary["out"] == spec.out


def test_budget_is_per_invocation_not_history(monkeypatch):
    """A second ``run`` starts from spent=0, like a fresh ``skillloop.run`` process."""
    monkeypatch.setenv("GITCG_LLM_BUDGET_USD", "0.01")
    spec = _spec(max_calls=1)                 # $0.02/game, already over $0.01
    client = StubClient('{"choice": 0}', cost_usd=0.02)
    runner = ParkedRunner(spec, concurrency=1, client=client)

    first = runner.run([_task(spec, "timmie", 0)], progress=False)
    second = runner.run([_task(spec, "timmie", 1)], progress=False)

    assert len(first) == len(second) == 1
    assert runner.last_summary is not None
    assert runner.last_summary["spent_usd"] == 0.02       # not 0.04
    assert "aborted" not in runner.last_summary


def test_summary_without_budget_omits_budget_fields():
    spec = _spec(max_calls=1)
    client = StubClient('{"choice": 0}')
    runner = ParkedRunner(spec, concurrency=1, client=client)
    rows = runner.run([_task(spec)], progress=False)

    assert len(rows) == 1
    summary = runner.last_summary
    assert summary is not None
    assert summary["games"] == 1
    assert summary["out"] == spec.out
    assert "budget_usd" not in summary
    assert "aborted" not in summary


# -- library, citations, cached prefix ---------------------------------------
def _library(tmp_path, skills, version=7) -> SkillLibrary:
    library = SkillLibrary.from_skills(skills, version=version)
    library.save(tmp_path)
    return library


def test_library_is_loaded_once_and_citations_are_filtered(tmp_path, monkeypatch):
    library = _library(tmp_path, [
        Skill(skill_id="s1", skill_name="speed", description="speed run"),
    ])
    loads: list[object] = []
    original_load = SkillLibrary.load

    def counting_load(root):
        loads.append(root)
        return original_load(root)

    monkeypatch.setattr(SkillLibrary, "load", staticmethod(counting_load))

    spec = _spec(group="static", max_calls=2, library_root=str(tmp_path))
    client = StubClient('{"choice": 0, "used": ["s1", "invented"]}')
    task, row = _run_task(spec, client)

    assert len(loads) == 1
    assert row["library_version"] == library.version
    assert row["cited_skills"] == ["s1"]
    assert task.error is None


def test_system_prefix_is_built_once_and_stable_across_resumes(tmp_path):
    """Retrieval may depend on the round, so rebuilding per segment would change the
    system prompt mid-game and break the provider's cached prefix."""
    _library(tmp_path, [
        Skill(skill_id="early", skill_name="early", description="early game"),
        Skill(
            skill_id="late",
            skill_name="late",
            description="late game",
            conditions=SkillConditions(round_min=2),
        ),
    ])
    spec = _spec(group="static", max_calls=6, library_root=str(tmp_path))
    client = StubClient('{"choice": 0}')
    _task_obj, row = _run_task(spec, client, concurrency=1)

    assert row["llm_calls"] >= 2                 # multiple segments were resumed
    systems = {prompt[0]["content"] for prompt in client.prompts}
    assert len(systems) == 1
    assert "late" not in next(iter(systems))     # built in round 1, never rebuilt


# -- no client -----------------------------------------------------------------
def test_truncated_flag_is_reported(monkeypatch):
    import skillloop.parked as parked_module

    monkeypatch.setattr(parked_module, "MAX_STEPS", 1)
    spec = _spec(max_calls=2)
    task, row = _run_task(spec, StubClient())

    assert task.truncated is True
    assert row["truncated"] is True
    assert row["llm_calls"] == 0
    # Like run_match, a truncated game still reports its partial state.
    assert row["error"] is None


def test_missing_client_is_reported_not_crashed():
    spec = _spec()
    task = _task(spec)
    rows = ParkedRunner(spec, client=None).run([task], progress=False)

    assert rows[0]["error"] == "no_llm_config"
    assert rows[0]["llm_calls"] == 0
