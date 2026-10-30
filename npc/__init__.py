"""NPC PvE debug system (PLAN.md §4.4, D17).

Public surface:

* ``npc.spec`` — typed, validated ``NpcSpec`` (stage) + enemy definitions, the
  name -> engine character id mapping, and the skill damage table.
* ``npc.policy`` — :class:`npc.policy.NpcPolicy` (deterministic greedy + rotation).
* ``npc.objectives`` — challenge-objective judging + three-star decision.
* ``npc.contestant`` — deterministic gate contestant (scripted expert + objective injection).
* ``npc.eval`` — stage x seed evaluation entry (win rate + three-star rate).
"""

from __future__ import annotations

from .contestant import ContestantPolicy, build_contestant_policy, build_contestant_profile
from .objectives import ObjectiveResult, StarResult, evaluate_stage
from .policy import ROTATIONS, NpcPolicy, npc_policy_for_stage
from .spec import (
    ENEMY_ENGINE_IDS,
    NpcEnemy,
    NpcObjective,
    NpcSkill,
    NpcStage,
    engine_id_for_name,
    enemy_name_index,
    get_stage,
    load_enemies,
    load_npc_specs,
    load_stages,
    stage_names,
)

__all__ = [
    "ENEMY_ENGINE_IDS",
    "ContestantPolicy",
    "NpcEnemy",
    "NpcObjective",
    "NpcPolicy",
    "NpcSkill",
    "NpcStage",
    "ObjectiveResult",
    "ROTATIONS",
    "StarResult",
    "build_contestant_policy",
    "build_contestant_profile",
    "engine_id_for_name",
    "enemy_name_index",
    "evaluate_stage",
    "get_stage",
    "load_enemies",
    "load_npc_specs",
    "load_stages",
    "npc_policy_for_stage",
    "stage_names",
]
