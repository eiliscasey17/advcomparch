#!/usr/bin/env bash
# Full experiment suite, sequentially on one pinned core (~45-60 min).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-$HOME/miniconda3/envs/advcomparch/bin/python}
./scripts/capture_env.sh
for e in ${@:-calib simd locality align stride dtype verify negative multiacc}; do
  $PY scripts/run.py "$e"
done
echo ALL DONE
