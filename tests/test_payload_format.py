"""A6 compact payload format: round-trip, backward compat, fingerprint, resume.

The compact format packs dense float arrays into base64 fp16/fp32 buffers inside
the *same* one-line-per-game envelope. These tests pin the contract that matters:

* ``jsonl`` stays exactly the historical lossless list format;
* ``compact`` round-trips within fp16 tolerance (fp32 essentially exact);
* readers auto-detect either format, so old replay dirs keep loading;
* ``format`` (and dtype / privileged flag) are part of the spec fingerprint;
* torn trailing lines are still tolerated on resume.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from reps.observation_encoder import BeliefTarget, EncodedObservation
from train.collect import CollectSpec, CollectTask
from train.data import (
    DEFAULT_COMPACT_DTYPE,
    Sample,
    is_dense_payload,
    iter_task_records,
    load_samples,
    observation_from_payload,
    observation_to_payload,
    sample_from_payload,
    sample_to_payload,
)


def _synthetic_observation() -> EncodedObservation:
    # Values chosen to be fp16-representable-ish but not exact, so the fp16 test
    # actually exercises the tolerance rather than a lucky round-trip.
    return EncodedObservation(
        token_features=((1.0, 0.0, 0.1234), (0.3333333, 2.5, 0.0)),
        token_mask=(True, False),
        opponent_token_mask=(True, True),
        option_features=((0.5, 0.6, 0.7), (0.0, 1.0, 0.25)),
        option_mask=(True, False),
        low_level_action_codes=(1, 2),
        low_to_high_codes=(2, 3),
        high_level_mask=(True, False),
        privileged_state=(0.0, 1.0, 0.1234, 3.0),
        belief_target=BeliefTarget(
            hand_histogram=(0.1, 0.2, 0.0),
            hand_size=2.0,
            remaining_deck_histogram=(0.3,),
            burst_ready_count=1.0,
            group_hand_presence=(1.0, 0.0),
            group_remaining_deck_presence=(0.5, 0.25),
        ),
        opponent_tag_id=3,
        opponent_entity_id=4,
        opponent_deck_id=5,
    )


def _synthetic_sample() -> Sample:
    return Sample(
        observation=_synthetic_observation(),
        teacher_index=1,
        value_target=1.0,
        request_type="action",
        step_index=7,
        deck="a",
        opponent="b",
    )


def _approx_obs(a: EncodedObservation, b: EncodedObservation, rtol=2e-3, atol=1e-3) -> None:
    def close(x, y):
        assert np.allclose(
            np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64), rtol=rtol, atol=atol
        )

    close(a.token_features, b.token_features)
    close(a.option_features, b.option_features)
    close(a.privileged_state, b.privileged_state)
    close(a.belief_target.hand_histogram, b.belief_target.hand_histogram)
    close(a.belief_target.remaining_deck_histogram, b.belief_target.remaining_deck_histogram)
    close(a.belief_target.group_hand_presence, b.belief_target.group_hand_presence)
    close(
        a.belief_target.group_remaining_deck_presence,
        b.belief_target.group_remaining_deck_presence,
    )
    assert a.token_mask == b.token_mask
    assert a.option_mask == b.option_mask
    assert a.low_level_action_codes == b.low_level_action_codes
    assert a.high_level_mask == b.high_level_mask
    assert a.opponent_tag_id == b.opponent_tag_id
    assert a.belief_target.hand_size == b.belief_target.hand_size


def test_jsonl_format_is_lossless_and_byte_compatible():
    obs = _synthetic_observation()
    payload = observation_to_payload(obs)
    assert not is_dense_payload(payload["token_features"])
    assert json.loads(json.dumps(payload))  # JSON-serializable
    assert observation_from_payload(json.loads(json.dumps(payload))) == obs

    sample = _synthetic_sample()
    restored = sample_from_payload(json.loads(json.dumps(sample_to_payload(sample))))
    assert restored == sample


def test_compact_uses_dense_envelopes():
    payload = observation_to_payload(_synthetic_observation(), format="compact")
    for key in ("token_features", "option_features", "privileged_state"):
        assert is_dense_payload(payload[key])
        assert payload[key]["__dense__"] == DEFAULT_COMPACT_DTYPE
        assert "shape" in payload[key] and payload[key]["data"]
    for key in ("hand_histogram", "remaining_deck_histogram", "group_hand_presence"):
        assert is_dense_payload(payload["belief_target"][key])


def test_compact_fp16_roundtrip_within_tolerance():
    sample = _synthetic_sample()
    payload = json.loads(json.dumps(sample_to_payload(sample, format="compact")))
    restored = sample_from_payload(payload)
    assert restored.teacher_index == sample.teacher_index
    assert restored.value_target == sample.value_target
    assert restored.request_type == sample.request_type
    _approx_obs(sample.observation, restored.observation, rtol=2e-3, atol=1e-3)


def test_compact_fp32_roundtrip_is_tight():
    sample = _synthetic_sample()
    payload = json.loads(
        json.dumps(sample_to_payload(sample, format="compact", compact_dtype="float32"))
    )
    restored = sample_from_payload(payload)
    # fp32 base64 is essentially exact for these magnitudes.
    _approx_obs(sample.observation, restored.observation, rtol=1e-6, atol=1e-6)


def test_compact_no_privileged_state_drops_vector():
    sample = _synthetic_sample()
    payload = observation_to_payload(
        sample.observation, format="compact", include_privileged_state=False
    )
    assert "privileged_state" not in payload
    restored = observation_from_payload(json.loads(json.dumps(payload)))
    assert restored.privileged_state == ()


def test_readers_autodetect_format():
    obs = _synthetic_observation()
    as_json = observation_from_payload(observation_to_payload(obs, format="jsonl"))
    as_compact = observation_from_payload(
        observation_to_payload(obs, format="compact", compact_dtype="float32")
    )
    _approx_obs(obs, as_json, rtol=0, atol=0)
    _approx_obs(obs, as_compact, rtol=1e-6, atol=1e-6)


def test_compact_falls_back_to_lists_on_ragged_matrix():
    ragged = EncodedObservation(
        token_features=((1.0, 2.0), (3.0,)),  # uneven -> cannot pack
        token_mask=(True, True),
        opponent_token_mask=(True, True),
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
    payload = observation_to_payload(ragged, format="compact")
    assert not is_dense_payload(payload["token_features"])  # graceful fallback
    restored = observation_from_payload(json.loads(json.dumps(payload)))
    assert restored.token_features == ragged.token_features


def test_fingerprint_includes_format():
    base = CollectSpec(deck="a", opponents=("b",), seeds=(0,))
    compact = CollectSpec(deck="a", opponents=("b",), seeds=(0,), format="compact")
    compact_np = CollectSpec(
        deck="a", opponents=("b",), seeds=(0,), format="compact", include_privileged_state=False
    )
    compact32 = CollectSpec(
        deck="a", opponents=("b",), seeds=(0,), format="compact", compact_dtype="float32"
    )
    fps = {
        base.fingerprint(),
        compact.fingerprint(),
        compact_np.fingerprint(),
        compact32.fingerprint(),
    }
    assert len(fps) == 4  # each payload choice is a distinct dataset
    assert base.fingerprint() == base.fingerprint()
    # The privileged-state flag is inert for jsonl, so it must not shift the fp.
    jsonl_np = CollectSpec(
        deck="a", opponents=("b",), seeds=(0,), include_privileged_state=False
    )
    assert jsonl_np.fingerprint() == base.fingerprint()


def test_collect_task_roundtrips_format_fields():
    task = CollectTask(
        index=0,
        deck="a",
        opponent="b",
        seed=1,
        payload_format="compact",
        compact_dtype="float32",
        include_privileged_state=False,
    )
    back = CollectTask.from_dict(task.as_dict())
    assert back == task
    # legacy task dicts (no format keys) default to jsonl.
    legacy = {"index": 2, "deck": "a", "opponent": "b", "seed": 3}
    assert CollectTask.from_dict(legacy).payload_format == "jsonl"


def test_load_samples_reads_compact_replay_dir(tmp_path: Path):
    sample = _synthetic_sample()
    record = {
        "index": 0,
        "error": None,
        "deck": "a",
        "opponent": "b",
        "seed": 0,
        "samples": [sample_to_payload(sample, format="compact")],
    }
    replay = tmp_path / "replays" / "c"
    replay.mkdir(parents=True)
    (replay / "samples.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

    loaded = load_samples(replay)
    assert len(loaded) == 1
    _approx_obs(sample.observation, loaded[0].observation)
    assert loaded[0].teacher_index == sample.teacher_index


def test_compact_torn_line_is_tolerated(tmp_path: Path):
    good = {
        "index": 0,
        "samples": [sample_to_payload(_synthetic_sample(), format="compact")],
    }
    path = tmp_path / "samples.jsonl"
    path.write_text(
        json.dumps(good) + "\n" + '{"index": 1, "samples": [{"observ',
        encoding="utf-8",
    )
    records = list(iter_task_records(path))
    assert [r["index"] for r in records] == [0]


def test_unsupported_format_and_dtype_raise():
    with pytest.raises(ValueError):
        observation_to_payload(_synthetic_observation(), format="parquet")
    with pytest.raises(ValueError):
        observation_to_payload(
            _synthetic_observation(), format="compact", compact_dtype="bfloat16"
        )
