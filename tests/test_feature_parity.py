"""P0 feature-parity tests: lossless definition ids + store↔engine encoding.

The A2 post-mortem found that compact float16 payloads silently destroyed
``definition_id / 400000`` (152 cards collapsed to 28 ids; 4/142 buckets
survived). These tests pin the fix at three levels:

* the compact payload now carries exact integer definition ids;
* ``observation_from_payload`` restores the definition columns losslessly;
* ``compare_observations`` reports a definition-id mismatch as a first-class
  parity issue rather than hiding it inside a float tolerance;
* encoding with ``include_training_targets=True`` vs ``False`` does not change
  policy inputs (the flag only controls labels).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest

from envs.parity import (
    ParityRecorder,
    compare_observations,
    player_view_digest,
)
from reps.observation_encoder import (
    DEFINITION_SCALE,
    BeliefTarget,
    EncodedObservation,
)
from train.data import (
    Sample,
    observation_from_payload,
    observation_to_payload,
    repair_observation_payload,
    sample_to_payload,
)


def _observation(token_id: int = 333029, option_id: int = 123456) -> EncodedObservation:
    token = [0.0] * 65
    token[42] = float(token_id) / DEFINITION_SCALE
    option = [0.0] * 68
    option[30] = float(option_id) / DEFINITION_SCALE
    return EncodedObservation(
        token_features=(tuple(token),),
        token_mask=(True,),
        opponent_token_mask=(False,),
        option_features=(tuple(option),),
        option_mask=(True,),
        low_level_action_codes=(7,),
        low_to_high_codes=(1,),
        high_level_mask=(True,),
        privileged_state=(),
        belief_target=BeliefTarget(
            hand_histogram=(),
            hand_size=0.0,
            remaining_deck_histogram=(),
            burst_ready_count=0.0,
            group_hand_presence=(),
            group_remaining_deck_presence=(),
        ),
        opponent_tag_id=0,
        opponent_entity_id=0,
        opponent_deck_id=0,
    )


def _recover(value: float) -> int:
    return int(round(float(value) * DEFINITION_SCALE))


def test_fp16_definition_id_is_not_reversible():
    # This is the bug, pinned as a regression: six-digit ids do not survive the
    # old compact default.
    stored = float(np.float16(333029 / DEFINITION_SCALE))
    assert _recover(stored) != 333029


def test_compact_payload_preserves_definition_ids_exactly():
    original = _observation()
    payload = json.loads(
        json.dumps(observation_to_payload(original, format="compact", compact_dtype="float16"))
    )
    assert payload["token_definition_ids"] == [333029]
    assert payload["option_definition_ids"] == [123456]
    assert payload["definition_indices"] == [42, 30]

    restored = observation_from_payload(payload)
    assert _recover(restored.token_features[0][42]) == 333029
    assert _recover(restored.option_features[0][30]) == 123456


def test_repair_observation_payload_fixes_store_columns():
    payload = json.loads(
        json.dumps(observation_to_payload(_observation(), format="compact", compact_dtype="float16"))
    )
    repaired = repair_observation_payload(payload)
    assert _recover(repaired["token_features"][0][42]) == 333029
    assert _recover(repaired["option_features"][0][30]) == 123456


def test_compare_observations_flags_definition_id_mismatch():
    left = _observation(token_id=333029)
    right = _observation(token_id=333008)  # what fp16 would round to
    report = compare_observations(left, right, atol=1.0)
    assert not report.ok
    assert "TOKEN_DEFINITION_ID" in report.counts()
    issue = next(i for i in report.issues if i.kind == "TOKEN_DEFINITION_ID")
    assert issue.row == 0 and issue.col == 42
    assert issue.actual == 333029 and issue.expected == 333008


def test_compare_observations_ignores_fp16_noise_outside_definition_column():
    left = _observation()
    right = _observation()
    left_token = list(left.token_features[0])
    right_token = list(right.token_features[0])
    left_token[10] = 0.1234
    right_token[10] = 0.1234001
    left = EncodedObservation(
        **{**left.__dict__, "token_features": (tuple(left_token),)}
    )
    right = EncodedObservation(
        **{**right.__dict__, "token_features": (tuple(right_token),)}
    )
    assert compare_observations(left, right, atol=1e-3).ok


def test_store_builder_records_exact_definition_ids(tmp_path):
    from scripts.datafirst_bc_store import load_store_any

    sample = Sample(
        observation=_observation(),
        teacher_index=0,
        value_target=1.0,
        request_type="action",
        step_index=1,
        deck="dvalin_bonk",
        opponent="natlan_battleship",
    )
    record = {
        "deck": "dvalin_bonk",
        "opponent": "natlan_battleship",
        "seed": 1,
        "winner": 0,
        "samples": [sample_to_payload(sample, format="compact")],
    }
    (tmp_path / "samples.jsonl").write_text(
        json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    train, val, meta = load_store_any(
        [str(tmp_path)], seed_start=0, val_every=0, device="cpu", progress_every=0
    )
    assert meta["events"] == 1
    assert meta["definition_ids_present"] == 1
    assert meta["definition_indices"] == [42, 30]
    assert len(val) == 0 and len(train) == 1
    assert _recover(float(train.tokens[0][0, 42])) == 333029
    assert _recover(float(train.options[0][0, 30])) == 123456


def test_parity_recorder_and_view_digest_are_deterministic():
    class _Policy:
        name = "fixed"

        def choose(self, built):
            return 3

    context = SimpleNamespace(
        step_index=4,
        request_type=SimpleNamespace(value="action"),
        acting_player=0,
        player_view={"players": [{"hp": 5}]},
    )
    built = SimpleNamespace(context=context)
    first = ParityRecorder(_Policy())
    second = ParityRecorder(_Policy())
    assert first.choose(built) == 3
    assert second.choose(built) == 3
    assert first.items[0].view_digest == second.items[0].view_digest
    assert player_view_digest(built) == first.items[0].view_digest


@pytest.mark.parametrize("deck,opponent", [("superconduct_aggro", "natlan_battleship")])
def test_encode_training_targets_flag_does_not_change_policy_inputs(deck, opponent):
    """Engine-backed P1 parity: the collector flag only fills labels."""
    from envs.decks import deck_spec
    from envs.match import run_match
    from envs.observation import default_encoder
    from envs.policy import build_policy

    recorder = ParityRecorder(build_policy("heuristic", seed=1, role="p0"))
    opp = build_policy("heuristic", seed=2, role="p1")
    record = run_match(deck_spec(deck), deck_spec(opponent), recorder, opp, seed=0)
    if record.error:
        pytest.skip(f"engine match failed: {record.error}")
    assert recorder.items
    encoder = default_encoder()
    for item in recorder.items[:5]:
        with_targets = encoder.encode_context(
            item.built.context, history=(), include_training_targets=True
        )
        without_targets = encoder.encode_context(
            item.built.context, history=(), include_training_targets=False
        )
        report = compare_observations(with_targets, without_targets, atol=0.0)
        assert report.ok, report.as_dict()
