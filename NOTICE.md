# NOTICE

Copyright (C) 2026 Peruere1828. Licensed under AGPL-3.0-only — see [`LICENSE`](./LICENSE)
for the full license text.

## Vendored / ported code

The following directories contain code ported (copied and adapted, not patched
in place) from upstream AGPL-3.0 projects. Each ported file carries a header
comment pointing at its origin.

- `reps/` — ported from **Rebel_base_RL** `research/world_model/src/gitcg_world_model`
  (action representation: schema, snapshot, action hierarchy/taxonomy,
  action adapter, public-state masking, semantic priors, baseline agents, replay).
- `agents/scripted/` — ported from **Rebel_base_RL**
  `research/world_model/src/gitcg_expert_system` (the 20/22 scripted deck rules
  used as fixed evaluation opponents, plus their asset/profile/deck-share-code
  plumbing).

Upstream: https://github.com/piovium/genius-invokation (engine, `gitcg` binding)
and the Rebel_base_RL reference clone under `refs/` (read-only, not committed).

No mihoyo card art / assets are redistributed. Card metadata is fetched on demand
into `data/` (gitignored) via the public GI-TCG assets API.

## Engine reproducibility note

Two engine behaviours break naive "same seed -> same game"; both are worked
around so `run_match` is fully deterministic:

1. `gitcg`'s deck-pile shuffle uses JavaScript `Math.random()` and is **not**
   controlled by the engine's seeded random generator. We pre-shuffle each pile
   in Python from a derived seed and pass `NO_SHUFFLE=1` (`envs/match.py`). Dice
   rolls and draws use the engine's seeded RNG and are sequential given the pile.

2. The ported `reps/action_hierarchy` keeps a **process-global** low-level action
   codebook whose codes are assigned incrementally as new action specs appear. A
   match's codes therefore depended on which matches ran earlier in the process.
   `run_match` resets the codebook at the start of every match, making a match
   independent of process history.

Consequence: low-level `action_code`s are only stable *within* a match. Training
pipelines must key labels/embeddings on the semantic action key
(`reps.action_hierarchy.low_level_semantic_key_for_code`), not the raw code.
