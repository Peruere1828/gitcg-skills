"""Toy CVPN pipeline: data collection, model shapes, overfit, and play-through."""

from __future__ import annotations

import pytest

from agents.neural import NeuralPolicy
from envs.decks import deck_spec
from envs.match import run_match
from envs.observation import default_encoder
from envs.policy import expert_policy
from train.data import collect_samples, sample_stats
from train.model import CVPN, collate, config_for_encoder
from train.train_loop import TrainConfig, train_bc


@pytest.fixture(scope="module")
def collected():
    encoder = default_encoder()
    samples = collect_samples(
        "superconduct_aggro", "natlan_battleship", (0,), encoder=encoder
    )
    assert samples
    return encoder, samples


def test_collection_produces_well_formed_samples(collected):
    encoder, samples = collected
    stats = sample_stats(samples)
    assert stats["n"] == len(samples)
    assert stats["max_options"] >= 2
    for sample in samples:
        assert sample.observation.option_features
        assert 0 <= sample.teacher_index < sample.n_options
        assert sample.value_target in (0.0, 0.5, 1.0)


def test_collate_pads_to_batch(collected):
    _encoder, samples = collected
    import torch

    tokens, token_mask, options, option_mask = collate(
        [s.observation for s in samples[:5]]
    )
    assert tokens.shape[0] == 5
    assert token_mask.shape == tokens.shape[:2]
    assert options.shape[0] == 5
    assert option_mask.shape == options.shape[:2]
    assert bool(token_mask.any()) and bool(option_mask.any())
    assert tokens.dtype == torch.float32


@pytest.mark.slow
def test_overfit_small_dataset(collected):
    encoder, samples = collected
    model = CVPN(config_for_encoder(encoder))
    metrics = train_bc(
        samples,
        model,
        TrainConfig(epochs=120, lr=8e-3, batch_size=16, val_fraction=0.0, value_coef=0.0),
    )
    # Majority-class baseline is ~0.3 here; beating 0.6 shows the network learns.
    assert metrics.train_accuracy > 0.6, metrics.as_dict()


@pytest.mark.slow
def test_trained_network_plays_a_legal_game(collected):
    encoder, samples = collected
    model = CVPN(config_for_encoder(encoder))
    train_bc(samples, model, TrainConfig(epochs=30, lr=5e-3, batch_size=16, value_coef=0.0))
    policy = NeuralPolicy(model=model, encoder=encoder)
    record = run_match(
        deck_spec("superconduct_aggro"),
        deck_spec("natlan_battleship"),
        policy,
        expert_policy("natlan_battleship", seed=1),
        seed=42,
    )
    assert record.error is None
    assert record.io_errors0 == ()
    assert record.fallbacks0 == 0


@pytest.mark.slow
def test_neural_spec_runs_through_rollout_worker(collected, tmp_path):
    """A ``neural:<ckpt>`` spec is usable by the multiprocess rollout (cluster eval)."""
    from envs.rollout import MatchTask, run_tasks

    encoder, _samples = collected
    model = CVPN(config_for_encoder(encoder))
    path = tmp_path / "ckpt.pt"
    model.save(str(path), encoder_config=encoder.to_dict())

    result = run_tasks(
        [
            MatchTask(
                index=0,
                deck0="superconduct_aggro",
                deck1="natlan_battleship",
                seed=7,
                policy0=f"neural:{path}",
                policy1="expert",
            )
        ],
        workers=1,
    )[0]
    assert result["error"] is None
    assert result["fallbacks0"] == 0 and result["fallbacks1"] == 0
    assert result["io_errors0"] == []


@pytest.mark.slow
def test_neural_policy_spec_loads_checkpoint(collected, tmp_path):
    """A checkpoint with a saved encoder config can be rebuilt from a spec string."""
    from envs.policy import build_policy
    from train.model import checkpoint_encoder_config

    encoder, _samples = collected
    model = CVPN(config_for_encoder(encoder))
    path = tmp_path / "ckpt.pt"
    model.save(str(path), encoder_config=encoder.to_dict())
    assert checkpoint_encoder_config(str(path)) == encoder.to_dict()

    policy = build_policy(f"neural:{path}", seed=0, role="p0")
    record = run_match(
        deck_spec("superconduct_aggro"),
        deck_spec("natlan_battleship"),
        policy,
        expert_policy("natlan_battleship", seed=1),
        seed=7,
    )
    assert record.error is None
    assert record.io_errors0 == ()
    assert record.fallbacks0 == 0


def test_precollate_padding_does_not_change_loss(collected):
    """Global padding must be loss-neutral: masked tokens/options are inert."""
    import torch

    from train.train_loop import _device_tensors, _loss_on_index

    encoder, samples = collected
    torch.manual_seed(0)
    model = CVPN(config_for_encoder(encoder, d_model=64)).eval()
    n = min(16, len(samples))
    subset = list(samples[:n])
    padded = subset + list(samples[:n])  # bigger T/K -> different global pad widths
    small = _device_tensors(subset, "cpu")
    big = _device_tensors(padded, "cpu")
    idx = torch.arange(n)
    with torch.no_grad():
        loss_small, _, _ = _loss_on_index(model, small, idx, 0.5)
        loss_big, _, _ = _loss_on_index(model, big, idx, 0.5)
    assert torch.allclose(loss_small, loss_big, atol=1e-4)


def test_precollate_fast_path_runs_and_learns(collected):
    import torch

    encoder, samples = collected
    torch.manual_seed(0)
    model = CVPN(config_for_encoder(encoder, d_model=64))
    metrics = train_bc(
        samples,
        model,
        TrainConfig(epochs=8, batch_size=16, val_fraction=0.2, device="cpu", precollate=True),
    )
    assert metrics.n_train > 0
    assert metrics.train_loss == metrics.train_loss  # finite
    assert 0.0 <= metrics.val_accuracy <= 1.0
    assert metrics.train_accuracy >= metrics.val_accuracy - 0.25


def test_precollate_matches_default_path(collected):
    import torch

    encoder, samples = collected
    torch.manual_seed(0)
    base = CVPN(config_for_encoder(encoder, d_model=64))
    m_base = train_bc(
        samples, base,
        TrainConfig(epochs=8, batch_size=16, val_fraction=0.2, seed=0, device="cpu"),
    )
    torch.manual_seed(0)
    fast = CVPN(config_for_encoder(encoder, d_model=64))
    m_fast = train_bc(
        samples, fast,
        TrainConfig(epochs=8, batch_size=16, val_fraction=0.2, seed=0, device="cpu", precollate=True),
    )
    assert abs(m_base.train_accuracy - m_fast.train_accuracy) < 0.05
    assert abs(m_base.train_loss - m_fast.train_loss) < 0.1
