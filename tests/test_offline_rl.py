"""Offline-RL policy targets on the compact BC store (H2 of the 2-day strength plan).

Covers the three knobs added to ``scripts.train_bc_scale``:

* ``loss="awr"`` with ``beta=0`` must be *exactly* behaviour cloning (same loss,
  same gradients) — the regression guard that keeps the historical BC path intact;
* ``loss="awr"`` with ``beta>0`` must put more weight on the actions played in
  winning games than plain BC does;
* ``loss="q"`` (DouZero-style action value) must learn to *prefer the action that
  won* even in the states where the teacher played the losing action — that is the
  whole point of the arm: an improvement operator that can exceed the teacher;
* the discounted MC return is ``gamma ** steps_remaining`` per game.
"""

from __future__ import annotations

import torch

from scripts.train_bc_scale import Config, Store, evaluate, q_scale_for_store, run_batch, train
from train.model import CVPN, CVPNConfig


def _store(
    teachers: list[int],
    values: list[float],
    *,
    steps: list[int] | None = None,
    game_values: list[float] | None = None,
) -> Store:
    """Tiny two-option store: option 0 = [0, 1], option 1 = [1, 0]."""
    n = len(teachers)
    steps = steps if steps is not None else list(range(n))
    game_values = game_values if game_values is not None else values
    empty = torch.zeros(0)
    return Store(
        tokens=[torch.zeros(2, 4) for _ in range(n)],
        options=[torch.tensor([[0.0, 1.0], [1.0, 0.0]]) for _ in range(n)],
        teacher=list(teachers),
        value=list(values),
        b_hand=[empty.clone() for _ in range(n)],
        b_deck=[empty.clone() for _ in range(n)],
        b_hist=[empty.clone() for _ in range(n)],
        b_hsize=[0.0] * n,
        b_burst=[0.0] * n,
        b_mask=[False] * n,
        keys=[("deck", "opp", 0, int(s)) for s in steps],
    )


def _model(seed: int = 0) -> CVPN:
    torch.manual_seed(seed)
    return CVPN(CVPNConfig(token_dim=4, option_dim=2, d_model=8, option_hidden=8, value_hidden=8))


def test_awr_beta_zero_is_behaviour_cloning():
    store = _store([0, 1] * 8, [1.0, 0.0] * 8)
    index = list(range(len(store)))

    base = _model()
    config = Config(loss="ce")
    loss_ce, info_ce = run_batch(base, store, index, "cpu", 0.5, 0.5, False, config)
    loss_ce.backward()
    grad_ce = base.option_scorer[-1].weight.grad.clone()

    model = _model()
    config = Config(loss="awr", awr_beta=0.0)
    loss_awr, info_awr = run_batch(model, store, index, "cpu", 0.5, 0.5, False, config)
    loss_awr.backward()
    grad_awr = model.option_scorer[-1].weight.grad.clone()

    assert abs(info_ce["ce"] - info_awr["ce"]) < 1e-6
    assert abs(float(loss_ce.detach()) - float(loss_awr.detach())) < 1e-6
    assert torch.allclose(grad_ce, grad_awr, atol=1e-6)


def test_awr_upweights_the_winning_action():
    """Winning samples played option 0, losing samples played option 1."""
    teachers = [0] * 32 + [1] * 32
    values = [1.0] * 32 + [0.0] * 32
    store = _store(teachers, values)

    def prob_option0(config: Config) -> float:
        model = _model(seed=1)
        train(store, None, model, config, has_belief=False)
        index = list(range(len(store)))
        logits, _ = model(
            torch.zeros(len(store), 2, 4), torch.ones(len(store), 2, dtype=torch.bool),
            store.options[0].expand(len(store), -1, -1), torch.ones(len(store), 2, dtype=torch.bool),
        )
        probs = torch.softmax(logits, dim=-1)
        return float(probs[32:, 0].mean().detach())  # option-0 mass on the *losing* states

    bc = prob_option0(Config(epochs=30, lr=1e-2, loss="ce", seed=3))
    awr = prob_option0(Config(epochs=30, lr=1e-2, loss="awr", awr_beta=2.0, seed=3))
    assert awr > bc, f"AWR should keep more option-0 mass on losing states: {awr} vs {bc}"


def test_q_scale_is_discounted_by_remaining_steps():
    store = _store([0, 0, 0, 0], [1.0] * 4, steps=[0, 1, 2, 0], game_values=[1.0] * 4)
    # keys are all the same game ("deck","opp",0); last step index is 2
    scale = q_scale_for_store(store, target="discount", gamma=0.5)
    assert scale == [0.25, 0.5, 1.0, 0.25]
    assert q_scale_for_store(store, target="episode", gamma=0.5) is None


def test_q_head_prefers_the_winning_action_over_the_teacher():
    """Q must rank the action that won above the one the teacher actually played."""
    teachers = [0] * 24 + [1] * 24
    values = [1.0] * 24 + [0.0] * 24
    store = _store(teachers, values)

    model = _model(seed=2)
    train(store, None, model, Config(epochs=80, lr=1e-2, loss="q", seed=2), has_belief=False)
    logits, _ = model(
        torch.zeros(len(store), 2, 4), torch.ones(len(store), 2, dtype=torch.bool),
        store.options[0].expand(len(store), -1, -1), torch.ones(len(store), 2, dtype=torch.bool),
    )
    losing = slice(24, None)  # states where the teacher played option 1 (and lost)
    assert float(logits[losing, 0].mean()) > float(logits[losing, 1].mean())
    info = evaluate(model, store, Config(loss="q", batch_size=16), "cpu", False)
    assert "q_mse" in info
