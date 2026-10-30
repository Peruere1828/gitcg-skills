"""A9 data-plane fixes: store de-dup key, deterministic caps, error retry.

Pins the review findings that were confirmed against the source:

* **H1** — the BC store de-dup key is the *full* game identity
  ``(deck, opponent, seed)``. A multi-deck corpus (A7) whose decks each restart
  ``--seed-start 0`` must not silently drop games whose ``(opponent, seed)``
  collides.
* **H2** — ``--max-train-samples`` / ``--max-val-samples`` are applied in a
  deterministic (identity-sorted) order, so the same replay dir loads to the
  same store regardless of the (completion-order) line layout.
* **M6** — a game whose record errored can be retried (``retry_errors`` /
  ``--retry-errors``) instead of being permanently treated as done.
* the flat reader skips a corrupt compact line instead of aborting the load.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch

from reps.observation_encoder import BeliefTarget, EncodedObservation
from train.collect import CollectSpec, collect
from train.data import Sample, iter_samples_jsonl, iter_task_records, sample_to_payload


# --------------------------------------------------------------------------- #
# fixtures: tiny synthetic replay dirs (no engine, no real 11 GB corpus)
# --------------------------------------------------------------------------- #
def _sample(step: int, *, teacher: int = 0) -> dict:
    return {
        "observation": {
            "token_features": [[float(step), 1.0]],
            "option_features": [[float(step)]],
            "belief_target": {},
        },
        "teacher_index": teacher,
        "value_target": 0.0,
        "step_index": step,
    }


def _record(deck: str, opponent: str, seed: int, n_samples: int, *, index: int = 0) -> dict:
    return {
        "index": index,
        "deck": deck,
        "opponent": opponent,
        "seed": seed,
        "error": None,
        "samples": [_sample(j) for j in range(n_samples)],
    }


def _write_dir(tmp_path: Path, name: str, records: list[dict]) -> str:
    d = tmp_path / name
    d.mkdir(parents=True)
    (d / "samples.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )
    return str(d)


def _assert_store_equal(a, b) -> None:
    assert len(a) == len(b)
    assert a.keys == b.keys
    assert a.teacher == b.teacher
    assert a.value == b.value
    assert a.b_hsize == b.b_hsize
    assert a.b_burst == b.b_burst
    assert a.b_mask == b.b_mask
    for i in range(len(a)):
        assert torch.equal(a.tokens[i], b.tokens[i])
        assert torch.equal(a.options[i], b.options[i])
        assert torch.equal(a.b_hand[i], b.b_hand[i])
        assert torch.equal(a.b_deck[i], b.b_deck[i])
        assert torch.equal(a.b_hist[i], b.b_hist[i])


# --------------------------------------------------------------------------- #
# H1: de-dup / sort key must include the deck
# --------------------------------------------------------------------------- #
def test_dedup_key_includes_deck(tmp_path: Path):
    from scripts.train_bc_scale import load_store

    a = _write_dir(tmp_path, "a", [_record("deckA", "opp", 0, 2)])
    b = _write_dir(tmp_path, "b", [_record("deckB", "opp", 0, 3)])
    train, val, meta = load_store(
        [a, b], seed_start=0, val_every=0,
        max_train_samples=None, max_val_samples=None,
    )
    assert len(train) == 5  # both games survive: same (opp, seed) but distinct decks
    assert len(val) == 0
    assert meta["duplicate_games"] == 0
    assert {k[0] for k in train.keys} == {"deckA", "deckB"}


def test_dedup_same_full_identity_is_still_dropped(tmp_path: Path):
    from scripts.train_bc_scale import load_store

    a = _write_dir(tmp_path, "a", [_record("deckA", "opp", 0, 2)])
    b = _write_dir(tmp_path, "b", [_record("deckA", "opp", 0, 5)])
    train, _val, meta = load_store(
        [a, b], seed_start=0, val_every=0,
        max_train_samples=None, max_val_samples=None,
    )
    assert len(train) == 2  # first occurrence wins
    assert meta["duplicate_games"] == 1


def test_datafirst_store_dedup_includes_deck(tmp_path: Path):
    from scripts.datafirst_bc_store import load_store_any

    a = _write_dir(tmp_path, "a", [_record("deckA", "opp", 0, 2)])
    b = _write_dir(tmp_path, "b", [_record("deckB", "opp", 0, 3)])
    train, val, meta = load_store_any([a, b], seed_start=0, val_every=0)
    assert len(train) == 5
    assert meta["duplicate_games"] == 0
    assert {k[0] for k in train.keys} == {"deckA", "deckB"}


# --------------------------------------------------------------------------- #
# H2: sample caps are deterministic and order-independent
# --------------------------------------------------------------------------- #
def test_sample_cap_is_deterministic_across_line_order(tmp_path: Path):
    from scripts.train_bc_scale import load_store

    records = [
        _record("d", "o", seed, 2, index=i) for i, seed in enumerate(range(1, 6))
    ]
    # Two dirs with the *same* games written in opposite (completion) order.
    a = _write_dir(tmp_path, "a", list(reversed(records)))
    b = _write_dir(tmp_path, "b", list(records))
    kw = dict(seed_start=0, val_every=0, max_train_samples=6, max_val_samples=None)
    ta, _va, _ma = load_store([a], **kw)
    tb, _vb, _mb = load_store([b], **kw)
    assert len(ta) == len(tb) == 6
    _assert_store_equal(ta, tb)
    # accepted games are the identity-sorted first ones, never file-order first
    assert sorted(k[2] for k in ta.keys) == [1, 1, 2, 2, 3, 3]


def test_cap_split_val_is_deterministic(tmp_path: Path):
    from scripts.train_bc_scale import load_store

    records = [_record("d", "o", seed, 1, index=seed) for seed in range(10)]
    a = _write_dir(tmp_path, "a", records)
    b = _write_dir(tmp_path, "b", list(reversed(records)))
    kw = dict(seed_start=0, val_every=5, max_train_samples=4, max_val_samples=2)
    ta, va, _ = load_store([a], **kw)
    tb, vb, _ = load_store([b], **kw)
    assert (len(ta), len(va)) == (4, 2)
    assert (len(tb), len(vb)) == (4, 2)
    _assert_store_equal(ta, tb)
    _assert_store_equal(va, vb)


def test_uncapped_store_is_order_independent(tmp_path: Path):
    from scripts.train_bc_scale import load_store

    records = [_record("d", "o", seed, 3, index=seed) for seed in range(4)]
    a = _write_dir(tmp_path, "a", records)
    b = _write_dir(tmp_path, "b", list(reversed(records)))
    ta, _va, _ = load_store([a], seed_start=0, val_every=0,
                            max_train_samples=None, max_val_samples=None)
    tb, _vb, _ = load_store([b], seed_start=0, val_every=0,
                            max_train_samples=None, max_val_samples=None)
    _assert_store_equal(ta, tb)


# --------------------------------------------------------------------------- #
# M6: errored games are retryable, not permanently "done"
# --------------------------------------------------------------------------- #
def _fake_stream(succeeded: set[int], attempts: list[int]):
    """A streaming stub: errors a task's first attempt, succeeds on retries."""

    def fake_stream(shard, *, worker_module, workers=None, on_result):
        for task in shard:
            attempts.append(task.index)
            errored = task.index not in succeeded
            succeeded.add(task.index)
            on_result(
                {
                    "index": task.index,
                    "deck": task.deck,
                    "opponent": task.opponent,
                    "seed": task.seed,
                    "error": "boom" if errored else None,
                    "samples": [],
                }
            )
        return len(shard)

    return fake_stream


def test_collect_errors_are_done_by_default_and_retryable(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GITCG_DATA_DIR", str(tmp_path))
    succeeded: set[int] = set()
    attempts: list[int] = []
    monkeypatch.setattr("train.collect.stream_task_shard", _fake_stream(succeeded, attempts))

    spec = CollectSpec(deck="a", opponents=("b",), seeds=(0, 1))
    first = collect(spec, tag="t", workers=1, progress=False)
    assert first["ran_this_call"] == 2
    assert first["errors"] == 2

    attempts.clear()
    second = collect(spec, tag="t", workers=1, progress=False)
    assert second["ran_this_call"] == 0  # default: errors treated as done
    assert attempts == []

    third = collect(spec, tag="t", workers=1, progress=False, retry_errors=True)
    assert third["ran_this_call"] == 2  # retried
    assert attempts == [0, 1]

    path = tmp_path / "replays" / "t" / "samples.jsonl"
    records = list(iter_task_records(path))
    assert {r["index"] for r in records if not r.get("error")} == {0, 1}


def test_retry_errors_respects_max_tasks(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GITCG_DATA_DIR", str(tmp_path))
    succeeded: set[int] = set()
    attempts: list[int] = []
    monkeypatch.setattr("train.collect.stream_task_shard", _fake_stream(succeeded, attempts))

    spec = CollectSpec(deck="a", opponents=("b",), seeds=(0, 1, 2))
    collect(spec, tag="t", workers=1, progress=False)  # all three error

    for _ in range(3):
        attempts.clear()
        r = collect(
            spec, tag="t", workers=1, progress=False, retry_errors=True, max_tasks=1
        )
        assert r["ran_this_call"] == 1
    r = collect(spec, tag="t", workers=1, progress=False, retry_errors=True, max_tasks=1)
    assert r["ran_this_call"] == 0  # nothing left to retry


# --------------------------------------------------------------------------- #
# minor: a corrupt compact line is skipped, not fatal
# --------------------------------------------------------------------------- #
def _obs() -> EncodedObservation:
    return EncodedObservation(
        token_features=((1.0, 2.0),),
        token_mask=(True,),
        opponent_token_mask=(True,),
        option_features=((0.5,),),
        option_mask=(True,),
        low_level_action_codes=(0,),
        low_to_high_codes=(0,),
        high_level_mask=(True,),
        privileged_state=(0.0,),
        belief_target=BeliefTarget(
            hand_histogram=(0.1,),
            hand_size=1.0,
            remaining_deck_histogram=(0.2,),
            burst_ready_count=0.0,
            group_hand_presence=(1.0,),
            group_remaining_deck_presence=(0.0,),
        ),
        opponent_tag_id=0,
        opponent_entity_id=0,
        opponent_deck_id=0,
    )


def test_iter_samples_jsonl_skips_corrupt_compact_line(tmp_path: Path):
    sample = Sample(
        observation=_obs(), teacher_index=0, value_target=0.0,
        request_type="action", step_index=0, deck="a", opponent="b",
    )
    good = sample_to_payload(sample, format="compact")
    corrupt = sample_to_payload(sample, format="compact")
    # A bad shape makes ``_unpack_dense``'s ``reshape`` raise ``ValueError``.
    corrupt["observation"]["token_features"]["shape"] = [99, 99]
    path = tmp_path / "samples.jsonl"
    path.write_text(
        json.dumps(good) + "\n" + json.dumps(corrupt) + "\n", encoding="utf-8"
    )
    loaded = list(iter_samples_jsonl(path))
    assert len(loaded) == 1  # corrupt line skipped, good line kept
