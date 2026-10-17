#!/usr/bin/env bash
# Full experiment suite, sequentially on one pinned core (~45-60 min).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-$HOME/miniconda3/envs/advcomparch/bin/python}
./scripts/capture_env.sh
for e in ${@:-E0 E1 E2 E3 E4 E5 E6 E8 E9}; do
  $PY scripts/run.py "$e"
done
echo ALL DONE
