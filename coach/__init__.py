"""WS4 coach layer: replay attribution, curriculum suggestions, BC labels."""

from .replay import GameTimeline, ReplayCoach, build_timeline, load_matches, rule_based_flags

__all__ = [
    "GameTimeline",
    "ReplayCoach",
    "build_timeline",
    "load_matches",
    "rule_based_flags",
]
