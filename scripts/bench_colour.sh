#!/usr/bin/env bash
# Measure the colour pass rate and size the run to a wall-clock budget.
#
# Usage: scripts/bench_colour.sh [n_probe] [budget_minutes]
#
# Starts nothing: llama-server must already be up (`make serve-model`).
set -euo pipefail

N=${1:-24}
BUDGET_MIN=${2:-55}
HOST=${LLAMA_HOST:-http://127.0.0.1:8080}
PY=${PY:-.venv/bin/python}

echo "[bench] probing $HOST with $N tracks"
start=$(date +%s)
COLOUR_CACHE=data/work/bench_colour.jsonl \
COLOUR_MAX_TRACKS=$N COLOUR_EVENT_COVERAGE=1.0 \
PYTHONPATH=. $PY -m pipeline.colour_model
end=$(date +%s)

elapsed=$((end - start))
rate=$(awk "BEGIN {printf \"%.3f\", $N / ($elapsed > 0 ? $elapsed : 1)}")
budget=$(awk "BEGIN {printf \"%d\", $rate * $BUDGET_MIN * 60}")
echo
echo "[bench] $N tracks in ${elapsed}s = ${rate} tracks/s"
echo "[bench] a ${BUDGET_MIN}-minute pass fits about ${budget} tracks"
echo "[bench] run it with: COLOUR_MAX_TRACKS=${budget} make colour"
