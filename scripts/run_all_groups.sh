#!/usr/bin/env bash
# Full experiment: every group on the same stages/seeds, then paired comparisons
# against the scripted baseline. Runs are resumable (same --out => skip done rows).
#
# Usage:
#   bash scripts/run_all_groups.sh 50 8                 # full scale, 8 workers
#   GROUPS="scripted contestant" bash scripts/run_all_groups.sh 10 8
#
# Groups:
#   scripted    plain scripted expert (no objective awareness)      -- no LLM
#   contestant  hand-written objective-aware policy (reference)     -- no LLM
#   llm_empty   the same LLM agent with an empty skill library
#   static      LLM agent + rules-only library (data/skills/rules_v1)
#   dynamic     LLM agent + evolved library (pass --library via LIB env)
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
PY="${PY:-/home/ubuntuaaa/miniconda3/envs/gitcg-skill/bin/python}"
SEEDS="${1:-50}"
WORKERS="${2:-8}"
GROUPS="${GROUPS:-scripted contestant llm_empty static dynamic}"
STATIC_LIB="${STATIC_LIB:-data/skills/rules_v1}"
DYNAMIC_LIB="${DYNAMIC_LIB:-data/skills/dynamic_v1}"
DECK="${DECK:-superconduct_aggro}"
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1

started=$(date -Is)
echo "=== $started groups='$GROUPS' seeds=$SEEDS workers=$WORKERS deck=$DECK"

for group in $GROUPS; do
  lib=""
  case "$group" in
    static)  lib="--library $STATIC_LIB" ;;
    dynamic) lib="--library $DYNAMIC_LIB" ;;
  esac
  echo "=== $(date -Is) group=$group ${lib:+($lib)}"
  "$PY" -m skillloop.run --group "$group" --stages all --seeds "$SEEDS" \
    --workers "$WORKERS" --deck "$DECK" $lib \
    --out "data/runs/${group}.jsonl"
done

echo "=== $(date -Is) paired comparisons (baseline = scripted)"
for group in $GROUPS; do
  [ "$group" = "scripted" ] && continue
  "$PY" -m scripts.compare_groups --a data/runs/scripted.jsonl \
    --b "data/runs/${group}.jsonl" --label-a scripted --label-b "$group" \
    --out "reports/compare/scripted_vs_${group}.json"
done

echo "=== $(date -Is) done (started $started)"
