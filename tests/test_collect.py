"""Cluster data plane (L5/M3): sample serialization + resumable collection.

Fast tests cover the on-disk format (round-trip, torn-line tolerance, fingerprint
stability); the slow test drives the real parallel collector and its resume path
on a tiny grid.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from reps.observation_encoder import BeliefTarget, EncodedObservation
from train.collect import CollectSpec, CollectTask, SearchSpec, collect
from train.data import (
    Sample,
    iter_task_records,
    load_replay_samples,
    observation_from_payload,
    observation_to_payload,
    sample_from_payload,
    sample_to_payload,
)


def _synthetic_observation() -> EncodedObservation:
    return EncodedObservation(
        token_features=((1.0, 2.0), (3.0, 4.0)),
        token_mask=(True, False),
        opponent_token_mask=(True, True),
        option_features=((0.5, 0.6), (0.7, 0.8)),
        option_mask=(True, False),
        low_level_action_codes=(1, 2),
        low_to_high_codes=(2, 3),
        high_level_mask=(True, False),
        privileged_state=(0.0, 1.0),
        belief_target=BeliefTarget(
            hand_histogram=(0.1, 0.2),
            hand_size=2.0,
            remaining_deck_histogram=(0.3,),
            burst_ready_count=1.0,
            group_hand_presence=(1.0,),
            group_remaining_deck_presence=(0.5,),
        ),
        opponent_tag_id=3,
        opponent_entity_id=4,
        opponent_deck_id=5,
    )


def test_observation_roundtrip_is_lossless():
    obs = _synthetic_observation()
    payload = observation_to_payload(obs)
    assert json.loads(json.dumps(payload))  # JSON-serializable
    assert observation_from_payload(json.loads(json.dumps(payload))) == obs


def test_sample_roundtrip_is_lossless():
    sample = Sample(
        observation=_synthetic_observation(),
        teacher_index=1,
        value_target=1.0,
        request_type="action",
        step_index=7,
        deck="a",
        opponent="b",
    )
    payload = json.loads(json.dumps(sample_to_payload(sample)))
    assert sample_from_payload(payload) == sample


def test_collect_spec_tasks_and_fingerprint_are_stable():
    spec = CollectSpec(deck="a", opponents=("b", "c"), seeds=(0, 1, 2))
    tasks = spec.tasks()
    assert [t.index for t in tasks] == list(range(6))
    assert [(t.opponent, t.seed) for t in tasks[:4]] == [
        ("b", 0), ("b", 1), ("b", 2), ("c", 0)
    ]
    assert spec.fingerprint() == spec.fingerprint()


def test_tolerant_reader_skips_torn_line(tmp_path: Path):
    path = tmp_path / "samples.jsonl"
    path.write_text(
        json.dumps({"index": 0, "samples": []}) + "\n"
        + '{"index": 1, "samples": [',  # torn
        encoding="utf-8",
    )
    records = list(iter_task_records(path))
    assert [r["index"] for r in records] == [0]


def test_load_replay_samples_flattens(tmp_path: Path):
    sample = Sample(
        observation=_synthetic_observation(),
        teacher_index=0,
        value_target=0.0,
        request_type="action",
        step_index=0,
        deck="a",
        opponent="b",
    )
    path = tmp_path / "samples.jsonl"
    path.write_text(
        json.dumps({"index": 0, "error": None, "samples": [sample_to_payload(sample)]}) + "\n",
        encoding="utf-8",
    )
    loaded = load_replay_samples(tmp_path)
    assert loaded == [sample]


@pytest.mark.slow
def test_collect_is_resumable(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GITCG_DATA_DIR", str(tmp_path))
    spec = CollectSpec(deck="superconduct_aggro", opponents=("natlan_battleship",), seeds=(0, 1))
    first = collect(spec, tag="t", workers=2, progress=False)
    assert first["games"] == 2
    assert first["samples"] > 0
    assert first["ran_this_call"] == 2

    second = collect(spec, tag="t", workers=2, progress=False)
    assert second["ran_this_call"] == 0  # nothing left to do
    assert second["games"] == first["games"]

    from train.data import load_samples
    samples = load_samples(tmp_path / "replays" / "t")
    assert len(samples) == first["samples"]


# --------------------------------------------------------------------------- #
# S-C search teacher (G6): spec wiring + engine smoke
# --------------------------------------------------------------------------- #
def test_search_spec_roundtrips_and_enters_fingerprint():
    search = SearchSpec(iterations=3, depth=2, candidate_limit=4, max_searches=5, belief_samples=1)
    assert SearchSpec.from_dict(search.as_dict()) == search
    base = CollectSpec(deck="a", opponents=("b",), seeds=(0,))
    with_search = CollectSpec(deck="a", opponents=("b",), seeds=(0,), search=search)
    assert base.fingerprint() != with_search.fingerprint()  # search block is pinned
    assert with_search.fingerprint() == with_search.fingerprint()
    task = with_search.tasks()[0]
    assert CollectTask.from_dict(task.as_dict()) == task
    assert task.search == search


# --------------------------------------------------------------------------- #
# A8: bounded-memory streaming collection (parent must not accumulate a shard)
# --------------------------------------------------------------------------- #
# A fake worker emits one result line per task with a large payload, so the
# parent-side memory behaviour is observable without running the engine. Follows
# tests/test_rollout_subprocess.py (write the module to tmp_path, put it on
# PYTHONPATH).
FAKE_STREAM_WORKER = """\
import json
import sys

blob = "x" * {size}
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    task = json.loads(line)
    result = {{"index": task["index"], "samples": [{{"obs": blob}}], "error": None}}
    sys.stdout.write(json.dumps(result) + "\\n")
    sys.stdout.flush()
"""


def _write_fake_worker(tmp_path: Path, *, size: int) -> None:
    (tmp_path / "fake_stream_worker.py").write_text(
        FAKE_STREAM_WORKER.format(size=size), encoding="utf-8"
    )


def test_stream_task_shard_does_not_accumulate_results(tmp_path, monkeypatch):
    """Parent memory stays bounded: 400 x 128 KiB streamed, not retained."""
    import tracemalloc

    from envs.rollout import stream_task_shard

    size = 128 * 1024
    _write_fake_worker(tmp_path, size=size)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))

    n = 400
    tasks = CollectSpec(deck="a", opponents=("b",), seeds=tuple(range(n))).tasks()
    seen: list[int] = []

    def on_result(result: dict) -> None:
        seen.append(int(result["index"]))

    tracemalloc.start()
    tracemalloc.reset_peak()
    count = stream_task_shard(
        tasks, worker_module="fake_stream_worker", workers=4, on_result=on_result
    )
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert count == n
    assert sorted(seen) == list(range(n))  # every game streamed exactly once
    streamed = n * size
    # Streaming keeps only in-flight lines resident; accumulation would pin
    # ~streamed bytes. A 4x headroom keeps the assertion robust to allocator
    # noise while still failing loudly if results are retained.
    assert peak < streamed // 4, f"parent peak {peak} vs streamed {streamed}"


def test_run_task_shard_retain_results_is_backward_compatible(tmp_path, monkeypatch):
    from envs.rollout import run_task_shard, stream_task_shard

    size = 8 * 1024
    _write_fake_worker(tmp_path, size=size)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))

    tasks = CollectSpec(deck="a", opponents=("b",), seeds=tuple(range(12))).tasks()
    seen: list[int] = []

    def on_result(result: dict) -> None:
        seen.append(int(result["index"]))

    # Default (retain) returns every result, preserving rollout/arena behaviour.
    retained = run_task_shard(
        tasks, worker_module="fake_stream_worker", workers=3, on_result=on_result
    )
    assert sorted(r["index"] for r in retained) == list(range(12))
    assert sorted(seen) == list(range(12))

    # retain_results=False streams but returns an empty list (no accumulation).
    seen.clear()
    empty = run_task_shard(
        tasks, worker_module="fake_stream_worker", workers=3,
        on_result=on_result, retain_results=False,
    )
    assert empty == []
    assert sorted(seen) == list(range(12))

    # stream_task_shard is the dedicated no-retain entrypoint: returns the count.
    seen.clear()
    count = stream_task_shard(
        tasks, worker_module="fake_stream_worker", workers=3, on_result=on_result
    )
    assert count == 12
    assert sorted(seen) == list(range(12))


def test_collect_streams_and_resumes_exactly_once(tmp_path, monkeypatch):
    """collect() drives the streaming entrypoint and never duplicates on resume."""
    monkeypatch.setenv("GITCG_DATA_DIR", str(tmp_path))
    calls: list[int] = []

    def fake_stream(shard, *, worker_module, workers=None, on_result):
        for task in shard:
            calls.append(task.index)
            on_result(
                {
                    "index": task.index,
                    "deck": task.deck,
                    "opponent": task.opponent,
                    "seed": task.seed,
                    "error": None,
                    "samples": [{"observation": {}, "teacher_index": 0}],
                }
            )
        return len(shard)

    monkeypatch.setattr("train.collect.stream_task_shard", fake_stream)

    spec = CollectSpec(deck="a", opponents=("b",), seeds=(0, 1, 2))
    first = collect(spec, tag="t", workers=2, progress=False)
    assert first["ran_this_call"] == 3
    assert first["games"] == 3
    assert calls == [0, 1, 2]

    calls.clear()
    second = collect(spec, tag="t", workers=2, progress=False)
    assert second["ran_this_call"] == 0
    assert calls == []  # nothing pending -> streaming path not entered

    records = list(iter_task_records(tmp_path / "replays" / "t" / "samples.jsonl"))
    assert [r["index"] for r in records] == [0, 1, 2]  # exactly once, no dupes


def test_collect_redoes_torn_trailing_game(tmp_path, monkeypatch):
    monkeypatch.setenv("GITCG_DATA_DIR", str(tmp_path))
    calls: list[int] = []

    def fake_stream(shard, *, worker_module, workers=None, on_result):
        for task in shard:
            calls.append(task.index)
            on_result(
                {
                    "index": task.index,
                    "deck": task.deck,
                    "opponent": task.opponent,
                    "seed": task.seed,
                    "error": None,
                    "samples": [],
                }
            )
        return len(shard)

    monkeypatch.setattr("train.collect.stream_task_shard", fake_stream)

    spec = CollectSpec(deck="a", opponents=("b",), seeds=(0, 1))
    collect(spec, tag="t", workers=1, progress=False)
    path = tmp_path / "replays" / "t" / "samples.jsonl"
    good = list(iter_task_records(path))
    assert [r["index"] for r in good] == [0, 1]

    # Simulate a hard kill mid-write: rewrite with game 1 as a torn line.
    path.write_text(
        json.dumps(good[0]) + "\n" + '{"index": 1, "samples": [',
        encoding="utf-8",
    )
    calls.clear()
    resumed = collect(spec, tag="t", workers=1, progress=False)
    assert resumed["ran_this_call"] == 1
    assert calls == [1]  # only the torn game is redone
    assert [r["index"] for r in iter_task_records(path)] == [0, 1]


def test_collect_fingerprint_guard_refuses_drift(tmp_path, monkeypatch):
    monkeypatch.setenv("GITCG_DATA_DIR", str(tmp_path))

    def fake_stream(shard, *, worker_module, workers=None, on_result):
        for task in shard:
            on_result(
                {
                    "index": task.index,
                    "deck": task.deck,
                    "opponent": task.opponent,
                    "seed": task.seed,
                    "error": None,
                    "samples": [],
                }
            )
        return len(shard)

    monkeypatch.setattr("train.collect.stream_task_shard", fake_stream)

    spec = CollectSpec(deck="a", opponents=("b",), seeds=(0,))
    collect(spec, tag="t", workers=1, progress=False)
    drifted = CollectSpec(deck="a", opponents=("b",), seeds=(0, 1))
    assert spec.fingerprint() != drifted.fingerprint()
    with pytest.raises(SystemExit):
        collect(drifted, tag="t", workers=1, progress=False)


@pytest.mark.slow
def test_collect_search_samples_carries_teacher_targets():
    from envs.fork_bridge import ForkBridge
    from train.data import collect_search_samples
    from envs.observation import default_encoder

    with ForkBridge(workers=2) as bridge:
        samples = collect_search_samples(
            "superconduct_aggro",
            "natlan_battleship",
            (5,),
            encoder=default_encoder(),
            bridge=bridge,
            iterations=2,
            depth=1,
            candidate_limit=2,
            max_searches=1,
            belief_samples=1,
            request_types=("action",),
        )
    assert samples, "expected at least one encoded decision sample"
    searched = [s for s in samples if s.policy_target is not None]
    assert searched, "expected at least one solver-annotated decision"
    for sample in searched:
        assert len(sample.policy_target) == sample.n_options
        assert sum(sample.policy_target) == pytest.approx(1.0)
        assert sample.search_value is not None and -1.0 <= sample.search_value <= 1.0
        # belief labels are captured (god view) for the belief head.
        assert any(v > 0.0 for v in sample.observation.belief_target.group_hand_presence) or (
            sample.observation.belief_target.hand_size > 0.0
        )
