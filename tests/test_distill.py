"""M3-CPU S-C (G6): teacher-target schema, distillation loss, and training.

Engine-free: the teacher-policy alignment helpers are pure functions, and the
distillation loop runs on synthetic ``Sample`` objects (an ``EncodedObservation``
plus solver targets) with a tiny CVPN.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch

from reps.observation_encoder import BeliefTarget, EncodedObservation
from train.data import (
    Sample,
    align_teacher_action_values,
    align_teacher_policy,
    sample_from_payload,
    sample_to_payload,
)
from train.distill import DistillConfig, distill_batch_loss, train_distill
from train.model import CVPN, CVPNConfig, collate


GROUP_DIM = 4
CARD_DIM = 5
TOKEN_DIM = 4
OPTION_DIM = 2


def _belief(*, signal: float = 1.0) -> BeliefTarget:
    groups = tuple(1.0 if i % 2 == 0 else 0.0 for i in range(GROUP_DIM))
    hist = tuple(1.0 if i == 0 else 0.0 for i in range(CARD_DIM))
    return BeliefTarget(
        hand_histogram=tuple(signal * v for v in hist),
        hand_size=signal * 0.5,
        remaining_deck_histogram=tuple(signal * v for v in hist),
        burst_ready_count=signal * 0.25,
        group_hand_presence=tuple(signal * v for v in groups),
        group_remaining_deck_presence=tuple(signal * v for v in groups),
    )


def _observation(seed: int, n_options: int) -> EncodedObservation:
    tokens = tuple(
        tuple(1.0 if (seed + offset) % 3 == 0 else 0.0 for offset in range(TOKEN_DIM))
        for _ in range(2)
    )
    options = tuple(
        (1.0 if (seed + i) % n_options == 0 else 0.0, float(i) / n_options)
        for i in range(n_options)
    )
    return EncodedObservation(
        token_features=tokens,
        token_mask=(True, True),
        opponent_token_mask=(False, False),
        option_features=options,
        option_mask=tuple(True for _ in range(n_options)),
        low_level_action_codes=tuple(range(n_options)),
        low_to_high_codes=tuple(-1 for _ in range(n_options)),
        high_level_mask=(True,),
        privileged_state=(0.0,),
        belief_target=_belief(),
        opponent_tag_id=0,
        opponent_entity_id=0,
        opponent_deck_id=0,
    )


def _sample(
    seed: int,
    n_options: int = 3,
    *,
    teacher_index: int | None = None,
    policy_target=None,
    search_value: float | None = None,
) -> Sample:
    if teacher_index is None:
        teacher_index = seed % n_options
    return Sample(
        observation=_observation(seed, n_options),
        teacher_index=int(teacher_index),
        value_target=1.0 if seed % 2 == 0 else 0.0,
        request_type="action",
        step_index=seed,
        deck="a",
        opponent="b",
        policy_target=policy_target,
        search_value=search_value,
    )


# --------------------------------------------------------------------------- #
# teacher-target alignment + schema
# --------------------------------------------------------------------------- #
def test_align_teacher_policy_normalizes_and_reindexes():
    keys = [("action", 10), ("action", 11), ("action", 12)]
    strategy = {keys[0]: 3.0, keys[1]: 1.0, ("action", 99): 5.0}
    aligned = align_teacher_policy(strategy, keys, 3)
    assert aligned is not None
    assert aligned[0] == pytest.approx(0.75)
    assert aligned[1] == pytest.approx(0.25)
    assert aligned[2] == pytest.approx(0.0)  # never seen -> 0 mass, still in support
    assert sum(aligned) == pytest.approx(1.0)


def test_align_teacher_policy_returns_none_when_no_mass():
    keys = [("action", 1)]
    assert align_teacher_policy({("action", 2): 1.0}, keys, 1) is None
    assert align_teacher_policy({}, keys, 1) is None
    assert align_teacher_policy({keys[0]: 1.0}, keys, 0) is None


def test_align_teacher_action_values_keep_sign():
    keys = [("k", 0), ("k", 1), ("k", 2)]
    values = align_teacher_action_values({keys[0]: -0.5, keys[2]: 0.25}, keys, 3)
    assert values == (-0.5, 0.0, 0.25)
    assert align_teacher_action_values({}, keys, 3) is None


def test_sample_payload_roundtrip_with_search_targets():
    sample = _sample(
        1,
        policy_target=(0.2, 0.5, 0.3),
        search_value=-0.4,
    )
    sample.action_values = (0.1, -0.2, 0.3)
    sample.search_meta = {"iterations": 8, "depth": 1, "particles": 2, "root_player": 0}
    payload = json.loads(json.dumps(sample_to_payload(sample)))
    assert sample_from_payload(payload) == sample


def test_legacy_payload_without_search_fields_still_loads():
    sample = _sample(0)
    payload = sample_to_payload(sample)
    # Emulate a pre-S-C replay line: drop every optional search field.
    for key in ("policy_target", "action_values", "search_value", "search_meta"):
        payload.pop(key, None)
    restored = sample_from_payload(payload)
    assert restored.policy_target is None
    assert restored.action_values is None
    assert restored.search_value is None
    assert restored.search_meta is None
    assert not restored.has_search_target


# --------------------------------------------------------------------------- #
# distillation loss numeric sanity
# --------------------------------------------------------------------------- #
def _model() -> CVPN:
    torch.manual_seed(0)
    return CVPN(
        CVPNConfig(
            token_dim=TOKEN_DIM,
            option_dim=OPTION_DIM,
            d_model=16,
            belief_group_dim=GROUP_DIM,
            belief_card_dim=CARD_DIM,
        )
    ).eval()


def test_soft_one_hot_policy_matches_hard_ce():
    model = _model()
    batch = [
        _sample(i, teacher_index=i % 3, policy_target=tuple(1.0 if j == i % 3 else 0.0 for j in range(3)))
        for i in range(6)
    ]
    _, soft = distill_batch_loss(model, batch, DistillConfig(use_policy_target=True))
    _, hard = distill_batch_loss(model, batch, DistillConfig(use_policy_target=False))
    assert soft["policy"] == pytest.approx(hard["policy"], abs=1e-5)
    assert soft["acc"] == pytest.approx(hard["acc"], abs=1e-6)


def test_value_target_selection_soft_vs_hard():
    model = _model()
    batch = [_sample(i, search_value=0.75) for i in range(4)]
    tokens, token_mask, options, option_mask = collate([s.observation for s in batch])
    with torch.no_grad():
        _, value, _ = model.forward_all(tokens, token_mask, options, option_mask)
    expected_soft = float(torch.nn.functional.mse_loss(value, torch.full_like(value, 0.75)).item())
    _, info_soft = distill_batch_loss(model, batch, DistillConfig(use_search_value=True))
    assert info_soft["value"] == pytest.approx(expected_soft, abs=1e-6)

    targets_hard = torch.tensor([s.value_target for s in batch])
    expected_hard = float(torch.nn.functional.mse_loss(value, targets_hard).item())
    _, info_hard = distill_batch_loss(model, batch, DistillConfig(use_search_value=False))
    assert info_hard["value"] == pytest.approx(expected_hard, abs=1e-6)


def test_total_loss_is_weighted_sum_of_components():
    model = _model()
    batch = [_sample(i, policy_target=(0.1, 0.2, 0.7), search_value=0.2) for i in range(5)]
    config = DistillConfig(policy_coef=2.0, value_coef=0.5, belief_coef=0.25)
    total, info = distill_batch_loss(model, batch, config)
    expected = 2.0 * info["policy"] + 0.5 * info["value"] + 0.25 * info["belief"]
    assert float(total.item()) == pytest.approx(expected, abs=1e-5)


# --------------------------------------------------------------------------- #
# distillation training
# --------------------------------------------------------------------------- #
def test_train_distill_reduces_loss_and_learns_policy_and_belief():
    samples = [
        _sample(i, policy_target=tuple(1.0 if j == i % 3 else 0.0 for j in range(3)), search_value=0.5)
        for i in range(12)
    ]
    model = _model()
    metrics = train_distill(
        samples,
        model,
        DistillConfig(epochs=80, lr=8e-3, batch_size=4, val_fraction=0.25, seed=0),
    )
    assert metrics.n_train > 0 and metrics.n_val > 0
    assert metrics.train_loss < metrics.history[0]["train_loss"]
    assert metrics.train_belief_loss < metrics.history[0]["train_belief_loss"]
    assert metrics.train_policy_acc > 0.7, metrics.as_dict()


def test_cold_start_config_is_hard_bc():
    samples = [_sample(i) for i in range(8)]
    model = _model()
    metrics = train_distill(
        samples,
        model,
        DistillConfig(
            epochs=5, batch_size=4, val_fraction=0.25, use_policy_target=False, use_search_value=False
        ),
    )
    assert metrics.train_belief_loss == metrics.train_belief_loss  # finite
    assert 0.0 <= metrics.train_policy_acc <= 1.0


def test_train_distill_without_belief_head_has_zero_belief_loss():
    samples = [_sample(i) for i in range(6)]
    torch.manual_seed(0)
    model = CVPN(CVPNConfig(token_dim=TOKEN_DIM, option_dim=OPTION_DIM, d_model=16))
    metrics = train_distill(samples, model, DistillConfig(epochs=3, batch_size=3, val_fraction=0.0))
    assert not model.has_belief
    assert metrics.train_belief_loss == pytest.approx(0.0)


# --------------------------------------------------------------------------- #
# pipeline wiring (slow: real engine eval)
# --------------------------------------------------------------------------- #
@pytest.mark.slow
def test_run_distill_round_smoke(tmp_path, monkeypatch):
    monkeypatch.setenv("GITCG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GITCG_REPORTS_DIR", str(tmp_path / "reports"))
    from envs.observation import default_encoder
    from train.data import collect_samples
    from train.pipeline import run_distill_round

    encoder = default_encoder()
    samples = collect_samples(
        "superconduct_aggro", "natlan_battleship", (0, 1), encoder=encoder
    )
    assert samples
    report = run_distill_round(
        samples=samples,
        deck="superconduct_aggro",
        eval_opponents=("natlan_battleship",),
        eval_seeds=(100,),
        d_model=32,
        distill_config=DistillConfig(epochs=3, batch_size=16),
    )
    assert report["data_source"] == "samples"
    assert report["belief_head"]["group_dim"] > 0
    assert report["train_metrics"]["n_train"] > 0
    assert "passed" in report["gate"]
    assert Path(report["checkpoint"]).exists()
    assert Path(report["checkpoint"]).suffix == ".pt"
