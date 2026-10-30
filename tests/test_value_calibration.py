"""Value-head calibration knobs (H3): activation, scoring rules, target smoothing.

The value head feeds two things that care about *scale*: the search frontier
(``train/resolver.py`` treats it as P(win)) and the AWR advantage. So the contract
enforced here is: a ``sigmoid`` head emits a probability, and the proper scoring
rules (``brier``/``bce``) refuse to run on a raw-logit (identity) head.
"""

from __future__ import annotations

import torch

from scripts.train_bc_scale import Config, Store, _value_loss, run_batch
from train.model import CVPN, TransformerCVPN, TransformerCVPNConfig, CVPNConfig, activate_value


def _tiny_model(activation: str = "identity") -> CVPN:
    torch.manual_seed(0)
    return CVPN(CVPNConfig(token_dim=4, option_dim=2, d_model=8, option_hidden=8,
                           value_hidden=8, value_activation=activation))


def _store(n: int = 8) -> Store:
    empty = torch.zeros(0)
    return Store(
        tokens=[torch.zeros(2, 4) for _ in range(n)],
        options=[torch.tensor([[0.0, 1.0], [1.0, 0.0]]) for _ in range(n)],
        teacher=[0] * n,
        value=[1.0] * (n // 2) + [0.0] * (n - n // 2),
        b_hand=[empty] * n, b_deck=[empty] * n, b_hist=[empty] * n,
        b_hsize=[0.0] * n, b_burst=[0.0] * n, b_mask=[False] * n,
        keys=[("d", "o", 0, i) for i in range(n)],
    )


def test_sigmoid_activation_bounds_the_value_head():
    model = _tiny_model("sigmoid")
    tokens = torch.randn(64, 3, 4)
    mask = torch.ones(64, 3, dtype=torch.bool)
    options = torch.randn(64, 2, 2)
    omask = torch.ones(64, 2, dtype=torch.bool)
    _, value = model(tokens, mask, options, omask)
    assert float(value.min().detach()) > 0.0 and float(value.max().detach()) < 1.0
    assert activate_value(torch.tensor([0.0]), model.config).item() == 0.5


def test_sigmoid_activation_survives_checkpoint_roundtrip(tmp_path):
    path = tmp_path / "sigmoid_value.pt"
    model = _tiny_model("sigmoid")
    model.save(str(path))
    reloaded = CVPN.load(str(path))
    assert reloaded.config.value_activation == "sigmoid"


def test_proper_scoring_rules_require_a_probability_head():
    model = _tiny_model("identity")
    value = torch.tensor([0.2, -0.4])
    targets = torch.tensor([1.0, 0.0])
    for kind in ("brier", "bce"):
        try:
            _value_loss(value, targets, Config(value_loss=kind), model)
        except ValueError as exc:
            assert "sigmoid" in str(exc)
        else:  # pragma: no cover - the guard is the point
            raise AssertionError(f"value_loss={kind} must refuse an identity head")


def test_brier_on_a_probability_head_is_a_proper_score():
    model = _tiny_model("sigmoid")
    value = torch.tensor([0.9, 0.1])
    targets = torch.tensor([1.0, 0.0])
    loss = _value_loss(value, targets, Config(value_loss="brier"), model)
    assert abs(float(loss) - (0.01 + 0.01) / 2) < 1e-6
    bce = _value_loss(value, targets, Config(value_loss="bce"), model)
    # BCE punishes the 10% miss ~10x harder than Brier does
    assert abs(float(bce) + float(torch.log(torch.tensor(0.9)))) < 1e-6
    assert float(bce) > float(loss)


def test_value_target_smoothing_pulls_targets_toward_a_half():
    model = _tiny_model("identity")
    value = torch.tensor([0.0, 1.0])
    targets = torch.tensor([0.0, 1.0])
    plain = _value_loss(value, targets, Config(value_loss="mse"), model)
    smoothed = _value_loss(value, targets, Config(value_loss="mse", value_smooth=0.1), model)
    assert plain < 1e-9 < smoothed  # 0.1^2 * 2 / 2
    assert abs(float(smoothed) - 0.01) < 1e-6


def test_bce_equals_negative_log_likelihood_at_the_target():
    model = _tiny_model("sigmoid")
    value = torch.tensor([0.8])
    targets = torch.tensor([1.0])
    loss = _value_loss(value, targets, Config(value_loss="bce"), model)
    assert abs(float(loss) + float(torch.log(torch.tensor(0.8)))) < 1e-6


def test_run_batch_default_path_is_unchanged_by_the_new_knobs():
    store = _store()
    index = list(range(len(store)))
    model = _tiny_model()
    loss, info = run_batch(model, store, index, "cpu", 0.5, 0.5, False, Config())
    assert "value" in info and float(loss) > 0
