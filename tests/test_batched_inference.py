"""Batched CVPN inference: chunking, masking, policy, and the JSON-lines service."""

from __future__ import annotations

import pytest

from reps.observation_encoder import BeliefTarget, EncodedObservation
from train.batched_inference import BatchedInferenceService, BatchScorer, ScoredPolicy
from train.model import CVPN, CVPNConfig


def _obs(n_tokens: int, n_options: int, *, token_dim: int = 2, option_dim: int = 3) -> EncodedObservation:
    return EncodedObservation(
        token_features=tuple(
            tuple(float(i + j) for j in range(token_dim)) for i in range(n_tokens)
        ),
        token_mask=tuple(True for _ in range(n_tokens)),
        opponent_token_mask=tuple(True for _ in range(n_tokens)),
        option_features=tuple(
            tuple(float(i) for _ in range(option_dim)) for i in range(n_options)
        ),
        option_mask=tuple(True for _ in range(n_options)),
        low_level_action_codes=tuple(range(n_options)),
        low_to_high_codes=tuple(range(n_options)),
        high_level_mask=tuple(True for _ in range(n_options)),
        privileged_state=(0.0,),
        belief_target=BeliefTarget(
            hand_histogram=(0.0,),
            hand_size=0.0,
            remaining_deck_histogram=(0.0,),
            burst_ready_count=0.0,
            group_hand_presence=(0.0,),
            group_remaining_deck_presence=(0.0,),
        ),
        opponent_tag_id=0,
        opponent_entity_id=0,
        opponent_deck_id=0,
    )


def _model() -> CVPN:
    return CVPN(CVPNConfig(token_dim=2, option_dim=3, d_model=8, option_hidden=8, value_hidden=8))


def _valid(rows):
    return [[v for v in row if v is not None] for row in rows]


def _rows_close(rows_a, rows_b, tol: float = 1e-4) -> bool:
    va, vb = _valid(rows_a), _valid(rows_b)
    if len(va) != len(vb):
        return False
    for x, y in zip(va, vb):
        if len(x) != len(y) or any(abs(p - q) > tol for p, q in zip(x, y)):
            return False
    return True


def test_batch_scorer_chunking_matches_single_batch():
    observations = [_obs(2 + i, 1 + i) for i in range(5)]
    model = _model()
    chunked = BatchScorer(model=model, max_batch_size=2).score(observations)
    whole = BatchScorer(model=model, max_batch_size=64).score(observations)
    assert _rows_close(chunked[0], whole[0])  # real options agree; padding width differs
    assert len(chunked[1]) == 5
    assert all(abs(a - b) <= 1e-4 for a, b in zip(chunked[1], whole[1]))


def test_batch_scorer_masks_padding_to_none():
    observations = [_obs(2, 2), _obs(2, 4)]  # second has more options -> first padded
    logits, _values = BatchScorer(model=_model(), max_batch_size=8).score(observations)
    assert len(logits[0]) == 4
    assert logits[0][0] is not None and logits[0][1] is not None
    assert logits[0][2] is None and logits[0][3] is None  # padding masked
    assert all(v is not None for v in logits[1])


def test_scored_policy_picks_greedy_legal_option():
    observation = _obs(3, 3)
    scorer = BatchScorer(model=_model(), max_batch_size=8)
    policy = ScoredPolicy(scorer=scorer, encoder=None, temperature=0.0)

    logits, _ = scorer.score([observation])
    expected = max(range(len(logits[0])), key=lambda i: logits[0][i])

    assert policy.choose_index(observation) == expected
    assert policy.calls == 1
    assert isinstance(policy.last_value, float)


@pytest.mark.slow
def test_service_roundtrip_matches_in_process(tmp_path):
    model = _model()
    path = tmp_path / "ckpt.pt"
    model.save(str(path))
    observations = [_obs(2 + i, 1 + i) for i in range(5)]

    in_process = BatchScorer(model=CVPN.load(str(path)), max_batch_size=64).score(observations)
    with BatchedInferenceService(str(path), device="cpu", bf16=False, max_batch_size=2) as service:
        served = service.score(observations)
    assert _rows_close(served[0], in_process[0])  # padding width differs by batch
    assert all(abs(a - b) <= 1e-4 for a, b in zip(served[1], in_process[1]))
