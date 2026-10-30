"""NPC PvE evaluation entry (PLAN.md §4.4, PvE-B3).

Fast tests cover aggregation + resume guards on synthetic rows; one slow test
plays a real stage x seed grid end to end.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from reps.schema import DeckSpec
from npc.eval import (
    NpcEvalResult,
    NpcEvalSpec,
    _spec_fingerprint,
    _stream_state,
    run_npc_eval,
    write_artifacts,
)
from npc.spec import get_stage


def _spec(**overrides) -> NpcEvalSpec:
    deck = DeckSpec(name="d", characters=(1, 2, 3), cards=tuple(range(30)))
    base = dict(stage_names=("timaeus",), contestant=deck, seeds=(1, 2), policy0="heuristic")
    base.update(overrides)
    return NpcEvalSpec(**base)


def _row(
    index: int,
    *,
    stage: str = "timaeus",
    winner: int | None = 0,
    three_star: bool = True,
    stars: int = 3,
    objectives: dict[str, bool] | None = None,
    error: str | None = None,
) -> dict:
    return {
        "task_index": index,
        "stage": stage,
        "seed": index,
        "winner": winner,
        "rounds": 4,
        "stars": stars,
        "three_star": three_star,
        "objectives": objectives or {"win": True},
        "error": error,
    }


def test_summary_aggregates_overall_stages_objectives():
    rows = [
        _row(0, winner=0, three_star=True, stars=3, objectives={"win": True, "win_within_rounds": True}),
        _row(1, winner=1, three_star=False, stars=0, objectives={"win": False, "win_within_rounds": False}),
        _row(2, stage="timmie", winner=0, three_star=False, stars=2, objectives={"win": True}),
    ]
    summary = NpcEvalResult(spec=_spec(), rows=rows).summary()
    overall = summary["overall"]
    assert overall["games"] == 3
    assert overall["wins"] == 2
    assert overall["win_rate"] == pytest.approx(2 / 3)
    assert overall["three_star_rate"] == pytest.approx(1 / 3)
    assert overall["mean_stars"] == pytest.approx(5 / 3)
    assert set(summary["stages"]) == {"timaeus", "timmie"}
    assert summary["stages"]["timaeus"]["games"] == 2
    assert summary["objectives"]["win"]["achieved_rate"] == pytest.approx(2 / 3)


def test_fingerprint_is_stable_and_sensitive():
    a = _spec()
    b = _spec()
    assert _spec_fingerprint(a) == _spec_fingerprint(b)
    assert _spec_fingerprint(a) != _spec_fingerprint(_spec(seeds=(1, 2, 3)))
    assert _spec_fingerprint(a) != _spec_fingerprint(_spec(policy0="legal_random"))
    assert _spec_fingerprint(a) != _spec_fingerprint(_spec(rotation="cycle_a_e_q"))


def test_stream_state_resume_guard(tmp_path: Path):
    spec = _spec()
    raw = tmp_path / "run.jsonl"
    # First call creates the meta sidecar and the (missing) raw file.
    done = _stream_state(spec, raw)
    assert done == []
    raw.write_text(json.dumps(_row(0)) + "\n", encoding="utf-8")
    done = _stream_state(spec, raw)
    assert [row["task_index"] for row in done] == [0]

    # A different spec must refuse to resume against the pinned fingerprint.
    meta = raw.with_name("run.meta.json")
    meta.write_text(json.dumps({"fingerprint": "deadbeef", "tag": "x"}), encoding="utf-8")
    with pytest.raises(RuntimeError):
        _stream_state(spec, raw)


def test_torn_trailing_line_is_tolerated(tmp_path: Path):
    spec = _spec()
    raw = tmp_path / "run.jsonl"
    _stream_state(spec, raw)  # create meta
    with raw.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(_row(0)) + "\n")
        handle.write('{"task_index": 1, "stage": "tim')  # torn
    done = _stream_state(spec, raw)
    assert [row["task_index"] for row in done] == [0]


def test_task_grid_shape():
    spec = _spec(stage_names=("a", "b"), seeds=(1, 2, 3))
    grid = list(enumerate(spec.task_grid()))
    assert [index for index, _ in grid] == [0, 1, 2, 3, 4, 5]
    assert grid[4][1] == ("b", 2)


def test_task_grid_folds_base_seed_into_seed():
    # ``base_seed`` must change the games (not only the fingerprint), so a spec
    # resumed with a different base_seed is not silently accepted as identical.
    spec = _spec(stage_names=("timaeus",), seeds=(1, 2), base_seed=100)
    assert list(spec.task_grid()) == [("timaeus", 101), ("timaeus", 102)]
    assert _spec_fingerprint(_spec(base_seed=0)) != _spec_fingerprint(spec)


def test_objective_keys_disambiguate_same_type():
    from npc.eval import _objective_keys
    from npc.spec import NpcObjective

    keys = _objective_keys(
        [
            NpcObjective("win"),
            NpcObjective("play_event_cards", count=2),
            NpcObjective("play_event_cards", count=3),
            NpcObjective("win_within_rounds", n=5),
        ]
    )
    assert keys == ["win", "play_event_cards", "play_event_cards#1", "win_within_rounds"]
    assert len(keys) == len(set(keys))


@pytest.mark.slow
def test_run_npc_eval_end_to_end_and_resume(tmp_path: Path):
    from envs.decks import deck_spec

    spec = NpcEvalSpec(
        stage_names=("timaeus", "timmie"),
        contestant=deck_spec("superconduct_aggro"),
        seeds=(1, 2),
        policy0="legal_random",
        tag="smoke",
    )
    raw = tmp_path / "smoke.jsonl"
    result = run_npc_eval(spec, raw_path=raw)
    assert len(result.rows) == 4
    for row in result.rows:
        assert row["error"] is None
        assert not row["truncated"]
        assert isinstance(row["three_star"], bool)
        expected = set(objective.type for objective in get_stage(row["stage"]).objectives)
        assert set(row["objectives"]) == expected
    assert result.summary()["overall"]["games"] == 4

    # Re-running with the same raw path resumes (no new matches, no duplicates).
    resumed = run_npc_eval(spec, raw_path=raw)
    assert len(resumed.rows) == 4
    assert resumed.metadata["resumed_matches"] == 4

    paths = write_artifacts(resumed, run_id="npc_smoke_test")
    assert paths["summary"].exists()
    assert paths["stages_csv"].exists()
    paths["summary"].unlink()
    paths["stages_csv"].unlink()
