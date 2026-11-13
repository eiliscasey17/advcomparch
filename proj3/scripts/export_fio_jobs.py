#!/usr/bin/env python3
"""Write one fio job file per job of every committed run, reconstructed from
the "job options" fio itself recorded in that job's JSON output, i.e. exactly
what ran (run.py builds the command line programmatically; these files are
its readable, re-runnable equivalent).

  python scripts/export_fio_jobs.py   -> results/fio_jobs/<exp>/jNNN_<name>_bs<bs>_qd<iodepth>x<numjobs>[_mix<r>].fio
  fio --output-format=json results/fio_jobs/qd1/j000_qd1_rand_read.fio

Command-line-only output settings (--output-format=json/json+, --output) are
not job options, so they are not in the files; run.py passes them.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402


def job_file(doc, source):
    j = doc["jobs"][0]
    opts = dict(j["job options"])
    name = opts.pop("name", j["jobname"])
    lines = [f"; reconstructed from {source} ({doc['fio version']} 'job options')",
             f"; run as: fio --output-format=json <this file>", f"[{name}]"]
    lines += [f"{k}={v}" for k, v in opts.items()]
    return "\n".join(lines) + "\n"


def main():
    pins = json.loads((C.RESULTS / "runs.json").read_text())
    n = 0
    for exp in C.EXPERIMENT_IDS:
        run = pins.get(exp)
        if not run:
            continue
        out = C.FIO_JOBS / exp
        out.mkdir(parents=True, exist_ok=True)
        for old in out.glob("*.fio"):
            old.unlink()
        raw, _ = C.load_raw(exp, run)
        for job in sorted(raw.job.unique()):
            rnd = int(raw[raw.job == job]["round"].min())
            p = C.raw_file(exp, run, f"j{int(job):03d}_r{rnd}.json")
            doc = json.loads(C.read_text(p))
            o = doc["jobs"][0]["job options"]
            label = (f"{doc['jobs'][0]['jobname']}_bs{o.get('bs', '-')}"
                     f"_qd{o.get('iodepth', '1')}x{o.get('numjobs', '1')}"
                     + (f"_mix{o['rwmixread']}" if "rwmixread" in o else ""))
            # cite the committed (gzipped) copy, which is what a clone has
            src = f"results/raw_runs/{exp}/{run}/j{int(job):03d}_r{rnd}.json.gz"
            (out / f"j{int(job):03d}_{label}.fio").write_text(job_file(doc, src))
            n += 1
    print(f"wrote {n} job files under {C.FIO_JOBS.relative_to(C.ROOT)}/")


if __name__ == "__main__":
    main()
