"""P0: temperature sampling in ``NeuralPolicy`` is seeded and repeatable."""

from __future__ import annotations

from types import SimpleNamespace

import torch
from torch import nn

from agents.neural import NeuralPolicy
from reps.observation_encoder import BeliefTarget, EncodedObservation


class _ToyModel(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.bias = nn.Parameter(torch.zeros(1))

    def forward(self, tokens, token_mask, options, option_mask):
        batch, n_options = options.shape[:2]
        logits = torch.zeros(batch, n_options)
        if n_options >= 2:
            logits[:, 0] = 0.1
            logits[:, 1] = 0.9
        return logits, torch.zeros(batch)


class _ToyEncoder:
    def encode_context(self, context, *, history=(), include_training_targets=False):
        return EncodedObservation(
            token_features=((1.0, 0.0),),
            token_mask=(True,),
            opponent_token_mask=(False,),
            option_features=((0.0,), (0.0,)),
            option_mask=(True, True),
            low_level_action_codes=(0, 1),
            low_to_high_codes=(0, 1),
            high_level_mask=(True, True),
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


def _built():
    return SimpleNamespace(context=SimpleNamespace(legal_low_level_codes=[0, 1]))


def _sequence(seed: int, n: int = 30) -> list[int]:
    policy = NeuralPolicy(
        model=_ToyModel(), encoder=_ToyEncoder(), temperature=1.0, seed=seed
    )
    return [policy.choose(_built()) for _ in range(n)]


def test_same_seed_repeats_sampling():
    assert _sequence(123) == _sequence(123)


def test_different_seeds_produce_different_samples():
    assert _sequence(1) != _sequence(2)
