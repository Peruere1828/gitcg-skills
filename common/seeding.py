"""Deterministic seeding helpers.

Paired-seed evaluation (PLAN.md §4.1 L1) requires that the same logical match
(deck pairing + index) maps to the same engine seed on every run/machine. We use
BLAKE2b over a canonicalized tuple, so seeds are stable across processes and
Python hash randomization.
"""

from __future__ import annotations

import hashlib
from typing import Any

_MASK = (1 << 31) - 1


def stable_hash(*parts: Any) -> int:
    """Return a stable non-negative 31-bit integer hash of the given parts."""
    digest = hashlib.blake2b(digest_size=8)
    for part in parts:
        digest.update(repr(part).encode("utf-8"))
        digest.update(b"\x1f")
    return int.from_bytes(digest.digest(), "big") & _MASK


def derive_seed(base: int, *parts: Any) -> int:
    """Derive a deterministic 31-bit seed from a base seed and extra parts."""
    return stable_hash(int(base), *parts)
