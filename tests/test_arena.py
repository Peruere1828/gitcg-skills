"""Arena end-to-end, including the subprocess rollout path."""

from __future__ import annotations

import json

import pytest

from eval.arena import ArenaSpec, run_arena
from eval.opponents import opponent_by_name, scripted_opponents


def _tiny_spec(workers: int) -> ArenaSpec:
    pool = (opponent_by_name("superconduct_aggro"), opponent_by_name("natlan_battleship"))
    return ArenaSpec(contestants=pool, pool=pool, seeds=(0, 1), workers=workers, tag="test")


def test_scripted_registry_is_populated():
    opponents = scripted_opponents()
    assert len(opponents) >= 20
    assert all(opp.policy.startswith("expert:") for opp in opponents)


def test_arena_policy_override_reaches_tasks():
    """A checkpoint can be the contestant: --policy0 neural:<ckpt> (cluster eval)."""
    spec = ArenaSpec(
        contestants=(opponent_by_name("superconduct_aggro"),),
        pool=(opponent_by_name("natlan_battleship"),),
        seeds=(0,),
        policy0="neural:/tmp/ckpt.pt",
    )
    task = spec.tasks()[0]
    assert task.policy_spec(0) == "neural:/tmp/ckpt.pt"
    assert task.policy_spec(1) == "expert:natlan_battleship"


@pytest.mark.slow
def test_arena_sequential_summary():
    result = run_arena(_tiny_spec(workers=1))
    assert len(result.matches) == 8  # 2 contestants x 2 pool x 2 seeds
    assert all(m["error"] is None for m in result.matches)
    summary = result.summary()
    assert summary["n_matches"] == 8
    assert summary["contestants"]
    assert set(summary["elo"]) >= {"superconduct_aggro", "natlan_battleship"}


@pytest.mark.slow
def test_arena_multiprocess_matches_sequential():
    sequential = run_arena(_tiny_spec(workers=1))
    parallel = run_arena(_tiny_spec(workers=2))
    assert len(parallel.matches) == len(sequential.matches)
    # Deterministic tasks: same (deck, seed) yields the same winner regardless of
    # which process ran it.
    key = lambda m: (m["deck0"], m["deck1"], m["seed"])  # noqa: E731
    seq = {key(m): m["winner"] for m in sequential.matches}
    par = {key(m): m["winner"] for m in parallel.matches}
    assert seq == par


@pytest.mark.slow
def test_streamed_arena_resumes_after_interruption(tmp_path):
    spec = _tiny_spec(workers=2)
    raw = tmp_path / "resume_test.jsonl"
    first = run_arena(spec, raw_path=raw)
    assert len(first.matches) == 8
    lines = raw.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 8

    # Simulate a hard kill: only the first 5 results made it to disk.
    raw.write_text("\n".join(lines[:5]) + "\n", encoding="utf-8")
    second = run_arena(spec, raw_path=raw)

    assert second.metadata["resumed_matches"] == 5
    assert len(second.matches) == 8
    assert len(raw.read_text(encoding="utf-8").strip().splitlines()) == 8
    key = lambda m: (m["deck0"], m["deck1"], m["seed"])  # noqa: E731
    assert {key(m): m["winner"] for m in second.matches} == {
        key(m): m["winner"] for m in first.matches
    }


def test_resume_refuses_fingerprint_mismatch(tmp_path):
    spec = _tiny_spec(workers=1)
    raw = tmp_path / "mismatch.jsonl"
    raw.write_text(json.dumps({"task_index": 0}) + "\n", encoding="utf-8")
    (tmp_path / "mismatch.meta.json").write_text(
        json.dumps({"fingerprint": "not-the-real-one"}), encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="fingerprint"):
        run_arena(spec, raw_path=raw)
