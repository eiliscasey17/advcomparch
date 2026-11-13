#!/usr/bin/env bash
# Full suite: env capture, data files, every experiment, summaries, plots.
# Resumable: experiments with a completed run (meta.json) are skipped.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-$HOME/miniconda3/envs/advcomparch/bin/python}
./scripts/capture_env.sh
$PY scripts/prep.py
# tail picks its QDs from qdsweep's knee, so qdsweep must come first
for e in ${@:-calib qd1 blocksize rwmix qdsweep tail lba pitfall state}; do
  if ls results/raw/"$e"/*[0-9].meta.json >/dev/null 2>&1; then
    echo "$e: completed run exists, skipping (delete its meta.json to re-run)"
  else
    $PY scripts/run.py "$e"
  fi
  $PY scripts/summarize.py "$e"
done
$PY scripts/plot.py all
echo ALL DONE
