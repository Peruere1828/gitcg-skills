# Ported / adapted representation layer (PLAN.md WS1).
#
# Modules ported from Rebel_base_RL `research/world_model/src/gitcg_world_model`
# (AGPL-3.0) and adapted here. See NOTICE.md for attribution and scope.

from .schema import (
    ActionChoice,
    DecisionContext,
    DecisionType,
    DeckSpec,
    EnvConfig,
    EpisodeRecord,
    LowLevelActionSpec,
    Matchup,
    OptionKind,
    StateSnapshot,
    TrajectoryStep,
)

__all__ = [
    "ActionChoice",
    "DecisionContext",
    "DecisionType",
    "DeckSpec",
    "EnvConfig",
    "EpisodeRecord",
    "LowLevelActionSpec",
    "Matchup",
    "OptionKind",
    "StateSnapshot",
    "TrajectoryStep",
]
