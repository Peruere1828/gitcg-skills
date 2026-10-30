"""Learned card/entity identity from the observation's definition scalar.

The encoder writes every entity definition id into one feature as ``id / 400000``
(:func:`reps.observation_encoder.definition_feature_indices` gives the column). That
is invertible, which means a model can turn the scalar back into a *category* and use
an embedding table -- the difference between "a monotone number that happens to be
Klee" and a representation that can share structure across cards with similar effects.

Nothing here changes the on-disk corpus format: every replay and store already carries
the scalar, so identity embeddings work on all existing data.
"""

from __future__ import annotations

import torch

from reps.observation_encoder import DEFINITION_SCALE


def definition_ids(column: torch.Tensor, *, scale: float = DEFINITION_SCALE) -> torch.Tensor:
    """Recover integer definition ids from the normalized feature column."""
    ids = torch.round(column.double() * scale).long().clamp(min=0)
    return torch.where(ids > int(scale), torch.zeros_like(ids), ids)


def bucket_ids(ids: torch.Tensor, buckets: int) -> torch.Tensor:
    """Map ids onto ``[1, buckets)``; 0 means "no definition" and doubles as padding.

    Bucketing keeps the table small (a few thousand slots) at the cost of rare
    collisions, which is the right trade here: the card vocabulary is long-tailed and
    an embedding collision between two rarely played cards is harmless.
    """
    if buckets <= 1:
        raise ValueError("id_buckets must be >= 2")
    hashed = ids % (buckets - 1) + 1
    return torch.where(ids > 0, hashed, torch.zeros_like(ids))


def token_definition_ids(
    tokens: torch.Tensor, index: int, buckets: int, *, scale: float = DEFINITION_SCALE
) -> torch.Tensor:
    """Id bucket per token, shape ``(B, T)``."""
    return bucket_ids(definition_ids(tokens[:, :, index], scale=scale), buckets)


def option_definition_ids(
    options: torch.Tensor, index: int, buckets: int, *, scale: float = DEFINITION_SCALE
) -> torch.Tensor:
    """Id bucket per legal option, shape ``(B, K)``."""
    return bucket_ids(definition_ids(options[:, :, index], scale=scale), buckets)
