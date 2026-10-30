"""M3-CPU S-C (G7): belief head shapes, proper-scoring loss, checkpoint round-trip.

Engine-free: the head, its loss and the label batching are pure torch over
synthetic ``EncodedObservation`` belief targets.
"""

from __future__ import annotations

import pytest
import torch

from reps.observation_encoder import BeliefTarget, EncodedObservation
from train.belief_head import (
    BeliefHead,
    BeliefTargetBatch,
    belief_loss,
    target_batch_from_observations,
)
from train.model import CVPN, CVPNConfig, TransformerCVPN, TransformerCVPNConfig, load_cvpn_model


def _belief_target(*, group_dim: int = 4, card_dim: int = 5, signal: float = 1.0) -> BeliefTarget:
    groups = tuple(1.0 if i % 2 == 0 else 0.0 for i in range(group_dim))
    hist = tuple((1.0 if i == 0 else 0.0) for i in range(card_dim))
    return BeliefTarget(
        hand_histogram=tuple(signal * v for v in hist),
        hand_size=signal * 0.5,
        remaining_deck_histogram=tuple(signal * v for v in hist),
        burst_ready_count=signal * 0.25,
        group_hand_presence=tuple(signal * v for v in groups),
        group_remaining_deck_presence=tuple(signal * v for v in groups),
    )


def _observation(*, group_dim: int = 4, card_dim: int = 5, signal: float = 1.0) -> EncodedObservation:
    return EncodedObservation(
        token_features=((0.1, 0.2, 0.3), (0.4, 0.5, 0.6)),
        token_mask=(True, True),
        opponent_token_mask=(False, False),
        option_features=((0.0, 0.0), (1.0, 1.0)),
        option_mask=(True, True),
        low_level_action_codes=(0, 1),
        low_to_high_codes=(-1, -1),
        high_level_mask=(True,),
        privileged_state=(0.0,),
        belief_target=_belief_target(group_dim=group_dim, card_dim=card_dim, signal=signal),
        opponent_tag_id=0,
        opponent_entity_id=0,
        opponent_deck_id=0,
    )


def test_belief_head_output_shapes():
    head = BeliefHead(16, group_dim=4, card_dim=5)
    pooled = torch.randn(3, 16)
    out = head(pooled)
    assert out.group_hand_logits.shape == (3, 4)
    assert out.group_deck_logits.shape == (3, 4)
    assert out.hand_card_logits.shape == (3, 5)
    assert out.hand_size.shape == (3,)
    assert out.burst_ready.shape == (3,)


def test_belief_head_requires_a_target():
    with pytest.raises(ValueError):
        BeliefHead(8)


def test_target_batch_masks_degenerate_rows():
    live = _observation(signal=1.0)
    dead = _observation(signal=0.0)
    batch = target_batch_from_observations([live, dead])
    assert batch.mask.tolist() == [True, False]
    assert batch.group_hand_presence.shape == (2, 4)
    assert batch.hand_histogram.shape == (2, 5)


def test_belief_loss_decreases_under_optimization():
    torch.manual_seed(0)
    head = BeliefHead(8, group_dim=4, card_dim=5)
    optimizer = torch.optim.Adam(head.parameters(), lr=5e-3)
    observations = [_observation() for _ in range(4)]
    targets = target_batch_from_observations(observations, device="cpu")
    pooled = torch.randn(len(observations), 8)

    first = None
    for _ in range(120):
        optimizer.zero_grad(set_to_none=True)
        loss = belief_loss(head(pooled), targets)
        loss.backward()
        optimizer.step()
        if first is None:
            first = float(loss.item())
    final = float(loss.item())
    assert final < first, (first, final)
    assert final < 0.1


def test_belief_loss_skips_fully_degenerate_batch():
    head = BeliefHead(8, group_dim=4, card_dim=5)
    targets = target_batch_from_observations([_observation(signal=0.0) for _ in range(2)])
    loss = belief_loss(head(torch.randn(2, 8)), targets)
    assert float(loss.item()) == pytest.approx(0.0)


def test_cvpn_forward_all_shapes_and_masking():
    model = CVPN(
        CVPNConfig(token_dim=3, option_dim=2, d_model=16, belief_group_dim=4, belief_card_dim=5)
    ).eval()
    b, t, k = 2, 2, 2
    tokens = torch.randn(b, t, 3)
    token_mask = torch.ones(b, t, dtype=torch.bool)
    options = torch.randn(b, k, 2)
    option_mask = torch.ones(b, k, dtype=torch.bool)
    with torch.no_grad():
        logits, value, belief = model.forward_all(tokens, token_mask, options, option_mask)
    assert logits.shape == (b, k)
    assert value.shape == (b,)
    assert belief is not None and belief.group_hand_logits.shape == (b, 4)
    assert belief.hand_card_logits.shape == (b, 5)
    # ``forward`` stays the 2-tuple interface the policy/search code relies on.
    with torch.no_grad():
        policy_logits, policy_value = model(tokens, token_mask, options, option_mask)
    assert torch.allclose(policy_logits, logits) and torch.allclose(policy_value, value)


def test_cvpn_without_belief_head_has_forward_all_none():
    model = CVPN(CVPNConfig(token_dim=3, option_dim=2, d_model=8)).eval()
    assert not model.has_belief
    with torch.no_grad():
        _, _, belief = model.forward_all(
            torch.randn(1, 2, 3), torch.ones(1, 2, dtype=torch.bool),
            torch.randn(1, 2, 2), torch.ones(1, 2, dtype=torch.bool),
        )
    assert belief is None


def test_belief_head_survives_checkpoint_roundtrip(tmp_path):
    model = CVPN(
        CVPNConfig(token_dim=3, option_dim=2, d_model=16, belief_group_dim=4, belief_card_dim=5)
    ).eval()
    path = tmp_path / "belief.pt"
    model.save(str(path), encoder_config={"k": 1})
    loaded = load_cvpn_model(str(path))
    assert isinstance(loaded, CVPN) and loaded.has_belief
    tokens = torch.randn(2, 2, 3)
    mask = torch.ones(2, 2, dtype=torch.bool)
    options = torch.randn(2, 3, 2)
    omask = torch.ones(2, 3, dtype=torch.bool)
    with torch.no_grad():
        _, _, before = model.forward_all(tokens, mask, options, omask)
        _, _, after = loaded.forward_all(tokens, mask, options, omask)
    assert torch.allclose(before.group_hand_logits, after.group_hand_logits)
    assert torch.allclose(before.hand_size, after.hand_size)


def test_legacy_config_without_belief_fields_loads():
    from dataclasses import asdict

    model = CVPN(CVPNConfig(token_dim=3, option_dim=2, d_model=8))
    payload = asdict(model.config)
    payload.pop("belief_group_dim")
    payload.pop("belief_card_dim")
    payload.pop("belief_hidden")
    reloaded = CVPN(CVPNConfig(**payload))
    assert not reloaded.has_belief


def test_transformer_cvpn_belief_shapes():
    config = TransformerCVPNConfig(
        token_dim=8, option_dim=5, d_model=16, n_layers=1, n_heads=4,
        dim_feedforward=32, belief_group_dim=4, belief_card_dim=5,
    )
    model = TransformerCVPN(config).eval()
    with torch.no_grad():
        _, _, belief = model.forward_all(
            torch.randn(2, 4, 8), torch.ones(2, 4, dtype=torch.bool),
            torch.randn(2, 3, 5), torch.ones(2, 3, dtype=torch.bool),
        )
    assert belief.group_hand_logits.shape == (2, 4)
    assert belief.hand_card_logits.shape == (2, 5)


def test_belief_target_batch_is_self_consistent():
    observations = [_observation() for _ in range(3)]
    batch = target_batch_from_observations(observations)
    assert isinstance(batch, BeliefTargetBatch)
    assert batch.mask.all()
    assert float(batch.hand_size.mean()) == pytest.approx(0.5)
