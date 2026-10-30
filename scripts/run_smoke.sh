#!/usr/bin/env bash
# 10-seed smoke: the two engine-only groups + the paired comparison.
#
# Purpose: get a staged result before spending any LLM tokens. Both groups use the
# same 16 stages and the same 10 seeds, so `compare_groups.py` pairs them exactly.
#
# Usage: bash scripts/run_smoke.sh [seeds] [workers]
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
PY="${PY:-/home/ubuntuaaa/miniconda3/envs/gitcg-skill/bin/python}"
SEEDS="${1:-10}"
WORKERS="${2:-8}"
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1

echo "=== $(date -Is) scripted group ($SEEDS seeds x 16 stages)"
"$PY" -m skillloop.run --group scripted --stages all --seeds "$SEEDS" \
  --workers "$WORKERS" --out data/runs/smoke_scripted.jsonl

echo "=== $(date -Is) contestant group (reference ceiling)"
"$PY" -m skillloop.run --group contestant --stages all --seeds "$SEEDS" \
  --workers "$WORKERS" --out data/runs/smoke_contestant.jsonl

echo "=== $(date -Is) paired comparison (b = contestant)"
"$PY" -m scripts.compare_groups --a data/runs/smoke_scripted.jsonl \
  --b data/runs/smoke_contestant.jsonl --label-a scripted --label-b contestant \
  --out reports/calibration/smoke_scripted_vs_contestant.json

echo "=== $(date -Is) done"
