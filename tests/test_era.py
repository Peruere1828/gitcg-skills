from __future__ import annotations

import json

import pytest

from skillloop.era import (
    era_policy,
    is_era,
    load_era_decks,
    resolve_base_policy,
    resolve_deck,
)


def test_load_era_decks_reads_the_configured_pool():
    decks = load_era_decks()
    assert "keqing_jihua" in decks
    assert len(decks) >= 5
    # Cached: repeated calls return the same mapping object.
    assert load_era_decks() is decks


def test_resolve_deck_reads_era_and_expert_pools():
    era = resolve_deck("keqing_jihua")
    assert era.name == "keqing_jihua"
    assert len(era.characters) == 3
    assert len(era.cards) == 30

    expert = resolve_deck("superconduct_aggro")
    assert expert.name == "superconduct_aggro"
    assert len(expert.characters) == 3
    assert len(expert.cards) == 30

    assert is_era("keqing_jihua")
    assert not is_era("superconduct_aggro")


def test_resolve_base_policy_works_for_era_and_expert_decks():
    era = resolve_base_policy("keqing_jihua")
    assert era.name == "expert:keqing_jihua"
    assert era.agent.profile.slug == "keqing_jihua"
    assert era.agent.profile.rule_config.style == "era"
    assert len(era.agent.profile.cards) == 30

    expert = resolve_base_policy("superconduct_aggro")
    assert expert.name == "expert:superconduct_aggro"
    assert expert.agent.profile.slug == "superconduct_aggro"


def test_era_policy_rule_overrides_apply_without_touching_defaults():
    default = era_policy(load_era_decks()["keqing_jihua"])
    assert default.agent.profile.rule_config.setup_rounds == 2

    tuned = era_policy(
        load_era_decks()["keqing_jihua"],
        rule_overrides={"setup_rounds": 0, "switch_hp_threshold": 6},
    )
    assert tuned.agent.profile.rule_config.setup_rounds == 0
    assert tuned.agent.profile.rule_config.switch_hp_threshold == 6
    # Untouched fields keep their defaults.
    assert tuned.agent.profile.rule_config.keep_tags == default.agent.profile.rule_config.keep_tags


class _StubClient:
    """Always pick candidate 0; enough to exercise the parked scheduler offline."""

    def chat(self, messages, **kwargs):
        return '{"choice": 0}'


@pytest.mark.slow
def test_parked_runner_with_era_stage_deck_plays(tmp_path):
    from npc.spec import get_stage
    from skillloop.parked import ParkedGame, ParkedRunner
    from skillloop.run import RunSpec

    stage_decks = tmp_path / "stage_decks.json"
    stage_decks.write_text(
        json.dumps({"stages": {"timmie": {"deck": "keqing_jihua", "notes": "test"}}}),
        encoding="utf-8",
    )
    spec = RunSpec(
        group="llm_empty",
        stages=("timmie",),
        seeds=(1,),
        deck="superconduct_aggro",
        max_calls=2,
        stage_decks_root=str(stage_decks),
        stage_rules=True,
        out=str(tmp_path / "parked.jsonl"),
    )
    task = ParkedGame(
        stage=get_stage("timmie"), seed=1, spec=spec, task_index=0
    )
    rows = ParkedRunner(spec, concurrency=2, client=_StubClient()).run(
        [task], progress=False
    )

    assert len(rows) == 1
    assert rows[0]["deck"] == "keqing_jihua"
    assert rows[0]["stage_rules"] is True
    assert rows[0]["error"] is None


@pytest.mark.slow
def test_scripted_game_with_era_stage_deck_plays(tmp_path):
    """End-to-end: stage_decks.json era entry reaches the engine and the row."""
    import skillloop.run as run_module
    from skillloop.run import RunSpec

    stage_decks = tmp_path / "stage_decks.json"
    stage_decks.write_text(
        json.dumps({"stages": {"timmie": {"deck": "keqing_jihua", "notes": "test"}}}),
        encoding="utf-8",
    )
    out = tmp_path / "run.jsonl"
    spec = RunSpec(
        group="scripted",
        stages=("timmie",),
        seeds=(1,),
        deck="superconduct_aggro",
        stage_decks_root=str(stage_decks),
        stage_rules=True,
        out=str(out),
    )
    summary = run_module.run(spec, progress=False)
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]

    assert summary["valid"] == 1
    assert len(rows) == 1
    row = rows[0]
    assert row["deck"] == "keqing_jihua"
    assert row["stage_rules"] is True
    assert row["error"] is None
