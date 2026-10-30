"""Transformer CVPN (M3 model exploration): shapes, masking, save/load."""

from __future__ import annotations

import pytest
import torch

from train.model import TransformerCVPN, TransformerCVPNConfig


def _cfg(**over):
    base = dict(token_dim=8, option_dim=5, d_model=16, n_layers=1, n_heads=4, dim_feedforward=32)
    base.update(over)
    return TransformerCVPNConfig(**base)


def test_transformer_forward_shapes_and_option_masking():
    model = TransformerCVPN(_cfg()).eval()
    b, t, k = 2, 4, 3
    tokens = torch.randn(b, t, 8)
    token_mask = torch.ones(b, t, dtype=torch.bool)
    token_mask[0, 2:] = False  # padded tokens ignored
    options = torch.randn(b, k, 5)
    option_mask = torch.ones(b, k, dtype=torch.bool)
    option_mask[0, 2] = False  # padded option -> -inf
    with torch.no_grad():
        logits, value = model(tokens, token_mask, options, option_mask)
    assert logits.shape == (b, k)
    assert value.shape == (b,)
    assert torch.isinf(logits[0, 2]) and logits[0, 2] < 0
    assert torch.isfinite(logits[option_mask]).all()


def test_transformer_requires_divisible_heads():
    with pytest.raises(ValueError):
        TransformerCVPN(_cfg(d_model=15, n_heads=4))


def test_transformer_save_load_roundtrip(tmp_path):
    model = TransformerCVPN(_cfg()).eval()
    path = tmp_path / "tr.pt"
    model.save(str(path), encoder_config={"k": 1})
    loaded = TransformerCVPN.load(str(path))
    tokens = torch.randn(1, 3, 8)
    mask = torch.ones(1, 3, dtype=torch.bool)
    options = torch.randn(1, 2, 5)
    omask = torch.ones(1, 2, dtype=torch.bool)
    with torch.no_grad():
        a, _ = model(tokens, mask, options, omask)
        b, _ = loaded(tokens, mask, options, omask)
    assert torch.allclose(a, b)


def test_load_cvpn_model_dispatches_on_arch(tmp_path):
    from dataclasses import asdict

    from train.model import CVPN, CVPNConfig, load_cvpn_model

    cvpn = CVPN(CVPNConfig(token_dim=8, option_dim=5, d_model=16)).eval()
    transformer = TransformerCVPN(_cfg()).eval()
    for model, expected in ((cvpn, CVPN), (transformer, TransformerCVPN)):
        path = tmp_path / f"{expected.__name__}.pt"
        model.save(str(path))
        assert isinstance(load_cvpn_model(str(path)), expected)
    # Legacy checkpoints with no "arch" field default to the mean-pool CVPN.
    legacy = tmp_path / "legacy.pt"
    torch.save({"config": asdict(cvpn.config), "state_dict": cvpn.state_dict()}, legacy)
    assert isinstance(load_cvpn_model(str(legacy)), CVPN)
