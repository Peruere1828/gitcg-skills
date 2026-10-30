"""P0: frontier value is a probability and is player-0 relative."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import torch
from torch import nn

from envs import cfr_probe
from train.resolver import PriorResolver


class _ValueModel(nn.Module):
    def __init__(self, raw: float, activation: str = "identity") -> None:
        super().__init__()
        self.raw = float(raw)
        self.config = SimpleNamespace(value_activation=activation)
        self.bias = nn.Parameter(torch.zeros(1))

    def forward(self, tokens, token_mask, options, option_mask):
        batch, n_options = options.shape[:2]
        logits = torch.zeros(batch, n_options)
        return logits, torch.full((batch,), self.raw)


class _Encoder:
    def encode_context(self, context, *, history=(), include_training_targets=False):
        from reps.observation_encoder import BeliefTarget, EncodedObservation

        return EncodedObservation(
            token_features=((1.0,),),
            token_mask=(True,),
            opponent_token_mask=(False,),
            option_features=((0.0,),),
            option_mask=(True,),
            low_level_action_codes=(0,),
            low_to_high_codes=(0,),
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


def _resolver(raw: float, activation: str = "identity") -> PriorResolver:
    return PriorResolver(model=_ValueModel(raw, activation), encoder=_Encoder())


def _built(acting: int):
    return SimpleNamespace(
        context=SimpleNamespace(acting_player=acting, legal_low_level_codes=[0])
    )


def test_identity_value_is_mapped_from_centred_score_to_probability():
    assert _resolver(-1.0).value_probability(_built(0)) == 0.0
    assert _resolver(0.0).value_probability(_built(0)) == 0.5
    assert _resolver(1.0).value_probability(_built(0)) == 1.0


def test_sigmoid_value_is_already_a_probability():
    assert _resolver(0.8, activation="sigmoid").value_probability(_built(0)) == pytest.approx(0.8)


def test_frontier_flips_acting_player_to_player_zero(monkeypatch):
    class _Fake:
        def value_probability(self, built):
            return 0.8

    monkeypatch.setitem(cfr_probe._VALUE_CACHE, "fake", _Fake())
    assert cfr_probe._frontier_probability(_built(0), value_spec="fake") == pytest.approx(0.8)
    assert cfr_probe._frontier_probability(_built(1), value_spec="fake") == pytest.approx(0.2)
