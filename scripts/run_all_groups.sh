#!/usr/bin/env bash
# Full experiment: every group on the same stages/seeds, then paired comparisons
# against the scripted baseline. Runs are resumable (same --out => skip done rows).
#
# Usage:
#   bash scripts/run_all_groups.sh 50 6                 # full scale, 6 workers (API concurrency = workers)
#   EVAL_GROUPS="scripted contestant" bash scripts/run_all_groups.sh 10 8
#
# NOTE: the selection variable is EVAL_GROUPS, not GROUPS: bash reserves GROUPS
# for the process group-id array.
#
# Groups (the only experimental variable is the skill library):
#   scripted    plain scripted expert (no objective awareness)      -- no LLM, no library
#   contestant  hand-written objective-aware policy (reference)     -- no LLM, no library
#   llm_empty   LLM + game rules only (data/skills/rules_v1, 17 rule;
#               no strategy/stage_specific, no loadout)             -- no --library
#   static      LLM + rules + hand-written static strategies
#               (data/skills/static_v1, 17+10) + reports/loadouts/v1
#   dynamic     LLM + rules + evolved strategies
#               (data/skills/dyn_v4, 17+23) + reports/loadouts/v4
#
# `contestant` is a reference arm, not one of the four groups.
# static/dynamic may override their library with STATIC_LIB / DYNAMIC_LIB and
# their frozen loadout with STATIC_LOADOUT / DYNAMIC_LOADOUT.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
PY="${PY:-/home/ubuntuaaa/miniconda3/envs/gitcg-skill/bin/python}"
SEEDS="${1:-50}"
WORKERS="${2:-6}"
EVAL_GROUPS="${EVAL_GROUPS:-scripted contestant llm_empty static dynamic}"
STATIC_LIB="${STATIC_LIB:-data/skills/static_v1}"
DYNAMIC_LIB="${DYNAMIC_LIB:-data/skills/dyn_v4}"
STATIC_LOADOUT="${STATIC_LOADOUT:-reports/loadouts/v1}"
DYNAMIC_LOADOUT="${DYNAMIC_LOADOUT:-reports/loadouts/v4}"
DECK="${DECK:-superconduct_aggro}"
# Optional in-game notebook for the LLM groups (default off). This is an A/B knob:
# the fingerprint records it, so do not point it at an existing baseline --out.
SCRATCHPAD="${SCRATCHPAD:-0}"
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1

# Same evaluation protocol for every group (era decks + stage special rules).
common=(
  --stages all --seeds "$SEEDS" --workers "$WORKERS" --deck "$DECK"
  --stage-decks configs/stage_decks.json
  --card-version v3.3.0
  --stage-rules
)

needs_llm=0
for group in $EVAL_GROUPS; do
  case "$group" in
    llm_empty|static|dynamic) needs_llm=1 ;;
  esac
done
if [ "$needs_llm" = 1 ] && [ -z "${GITCG_LLM_BUDGET_USD:-}" ]; then
  echo "=== WARNING: GITCG_LLM_BUDGET_USD is not set; the D11 hard budget guard is off for LLM groups" >&2
fi

started=$(date -Is)
echo "=== $started groups='$EVAL_GROUPS' seeds=$SEEDS workers=$WORKERS deck=$DECK"

for group in $EVAL_GROUPS; do
  extra=()
  case "$group" in
    static)
      extra=(--library "$STATIC_LIB" --loadout "$STATIC_LOADOUT")
      ;;
    dynamic)
      extra=(--library "$DYNAMIC_LIB" --loadout "$DYNAMIC_LOADOUT")
      ;;
    llm_empty)
      # Fixed to data/skills/rules_v1 by the runner; never pass --library/--loadout.
      ;;
  esac
  case "$group" in
    llm_empty|static|dynamic)
      [ "$SCRATCHPAD" = 1 ] && extra+=(--scratchpad)
      ;;
  esac
  echo "=== $(date -Is) group=$group ${extra[*]:-}"
  "$PY" -m skillloop.run --group "$group" "${common[@]}" "${extra[@]}" \
    --out "data/runs/${group}.jsonl"
done

echo "=== $(date -Is) paired comparisons (baseline = scripted)"
for group in $EVAL_GROUPS; do
  [ "$group" = "scripted" ] && continue
  "$PY" -m scripts.compare_groups --a data/runs/scripted.jsonl \
    --b "data/runs/${group}.jsonl" --label-a scripted --label-b "$group" \
    --out "reports/compare/scripted_vs_${group}.json"
done

echo "=== $(date -Is) done (started $started)"
