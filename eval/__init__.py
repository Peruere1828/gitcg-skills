"""WS2 evaluation layer: opponents, arena, ladder, stats.

Heavy submodules (``arena``) are intentionally *not* imported here so that
``python -m eval.arena`` does not double-import the module as ``__main__``.
Import them directly: ``from eval.arena import run_arena``.
"""

from .ladder import compute_elo
from .opponents import (
    Opponent,
    all_opponents,
    baseline_opponents,
    opponent_by_name,
    scripted_opponents,
)
from .stats import RateEstimate, estimate_rate, wilson_interval

__all__ = [
    "compute_elo",
    "Opponent",
    "all_opponents",
    "baseline_opponents",
    "opponent_by_name",
    "scripted_opponents",
    "RateEstimate",
    "estimate_rate",
    "wilson_interval",
]
