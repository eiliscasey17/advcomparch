#!/usr/bin/env bash
# Full suite: env capture, every experiment, summaries, plots (~1-2 h of
# measurement; the idle-hardware gate waits, without limit, for quiet cpus).
# Re-running resumes: experiments with a completed run are skipped.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-$HOME/miniconda3/envs/advcomparch/bin/python}
./scripts/capture_env.sh
# single-core experiments (cachemiss, tlb) before whole-node ones (intensity, pitfall): they only
# need one quiet CCD, so they can run while the nodes are busy
for e in ${@:-calib latency workingset pattern rwmix cachemiss tlb intensity pitfall}; do
  # resume: skip experiments that already have a completed full run
  if ls results/raw/"$e"/*[0-9].meta.json >/dev/null 2>&1; then
    echo "$e: completed run exists, skipping (delete its meta.json to re-run)"
  else
    # resume an interrupted run (round CSVs but no meta.json) instead of starting over
    part=$( (ls results/raw/"$e"/*[0-9]__r0.csv 2>/dev/null || true) | tail -1 | xargs -r basename | sed 's/__r0.csv//')
    $PY scripts/run.py "$e" ${part:+--resume "$part"}
  fi
  $PY scripts/summarize.py "$e"
done
$PY scripts/mlc.py || true
$PY scripts/plot.py all
echo ALL DONE
