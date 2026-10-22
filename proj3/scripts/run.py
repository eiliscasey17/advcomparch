#!/usr/bin/env python3
"""Run a storage experiment with fio: gate on a quiet /data, run jobs (shuffled
rounds, or an ordered sequence for state-dependent experiments), keep every
fio JSON, write one CSV row per job.

  python scripts/run.py F1            # full
  python scripts/run.py F1 --quick    # smoke test (short runs, 1 round)
  python scripts/run.py --list

Output: results/raw/<EXP>/<run_id>__r<round>.csv, results/raw/<EXP>/<run_id>/
j<job>_r<round>.json (+ iops logs), <run_id>.meta.json (written last).

Every job: fio --direct=1 --ioengine=libaio (unless the experiment is about
changing that), time_based with a ramp, random offsets aligned to bs
(fio default blockalign=bs), --norandommap (pure random, repeats allowed),
--buffer_compress_percentage=0 (incompressible data, see PATTERNS), jobs pinned one per cpu on
the NVMe controllers' NUMA node, round-robin over its CCDs.

Shared-machine gate: before each job /data (md124) must be quiet
(<= GATE_IOPS and <= GATE_MBPS of other traffic over 2 s). During the job the
md124 bytes moved are compared with fio's own bytes; a job that saw more
than OTHERS_MBPS of foreign traffic is re-measured (up to 3 attempts).
"""
import argparse
import io
import json
import os
import platform
import random
import resource
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import common as C  # noqa: E402
import pick_core  # noqa: E402

K, M, G = C.KiB, C.MiB, C.GiB
GATE_IOPS, GATE_MBPS, OTHERS_MBPS = 300, 30.0, 30.0
QD32 = dict(qd=8, jobs=4)  # the "fixed QD" used by sweeps: 4 jobs x iodepth 8


# Write-buffer content (the "data pattern" knob). Default for every experiment:
# incompressible (zlib ratio 1.00) at no CPU cost; fio reuses its buffers, so the
# data is dedupable, which neither md nor these drives exploit. Verified by
# tests/test_analysis.py::test_data_patterns.
PATTERNS = {
    "incompressible": ["--buffer_compress_percentage=0"],
    "compress50": ["--buffer_compress_percentage=50", "--buffer_compress_chunk=4k"],
    "zeros": ["--zero_buffers=1", "--scramble_buffers=0"],
    # unique + incompressible, but fio regenerates every buffer: ~3 us/KiB of CPU (F8 pitfall)
    "refill": ["--refill_buffers=1", "--buffer_compress_percentage=0"],
}


def J(tag, rw, bs, qd=1, jobs=1, region=C.MAIN_REGION, runtime=20, ramp=5, mix=None,
      file=None, direct=1, engine="libaio", hist=False, iolog=False, extra=(), check_io=True,
      pattern="incompressible", **kw):
    """One fio invocation. `region` = bytes of the file addressed (from offset 0)."""
    return dict(tag=tag, rw=rw, bs=bs, qd=qd, jobs=jobs, region=region, runtime=runtime,
                ramp=ramp, mix=mix, file=str(file or C.DATAFILE), direct=direct, engine=engine,
                hist=hist, iolog=iolog, extra=list(extra), check_io=check_io, pattern=pattern,
                **kw)


def size_of(s):
    if isinstance(s, (int, float)):
        return int(s)
    mult = {"k": K, "m": M, "g": G}[s[-1].lower()]
    return int(float(s[:-1]) * mult)


def fio_cmd(job, cpus, quick=False, logdir=None):
    region = job["region"]
    seq = job["rw"] in ("read", "write", "rw")
    n = job["jobs"]
    runtime = min(job["runtime"], 5) if quick else job["runtime"]
    ramp = min(job["ramp"], 1) if quick else job["ramp"]
    cmd = [str(C.FIO), f"--name={job['tag'].replace(':', '_')}", f"--filename={job['file']}",
           f"--rw={job['rw']}", f"--bs={job['bs']}", f"--iodepth={job['qd']}", f"--numjobs={n}",
           "--group_reporting=1", f"--direct={job['direct']}", f"--ioengine={job['engine']}",
           "--time_based=1", f"--runtime={runtime}", f"--ramp_time={ramp}",
           "--percentile_list=" + ":".join(f"{p:g}" for p in C.PCTS),
           "--output-format=" + ("json+" if job["hist"] else "json"),
           "--norandommap=1", "--randrepeat=0", "--random_generator=tausworthe64",
           "--allow_file_create=0", "--invalidate=1"] + PATTERNS[job["pattern"]]
    if seq and n > 1:  # disjoint sequential streams, one per job
        per = region // n // size_of(job["bs"]) * size_of(job["bs"])
        cmd += [f"--size={per}", f"--offset_increment={per}"]
    else:
        cmd += [f"--size={region}"]
    if job["mix"] is not None:
        cmd.append(f"--rwmixread={job['mix']}")
    if cpus:
        cmd += ["--cpus_allowed=" + ",".join(map(str, cpus)), "--cpus_allowed_policy=split"]
    if job["iolog"] and logdir:
        cmd += [f"--write_iops_log={logdir}", "--log_avg_msec=1000"]
    return cmd + list(job["extra"])


# ---------------------------------------------------------------- experiments
RW = {("rand", "read"): "randread", ("rand", "write"): "randwrite",
      ("seq", "read"): "read", ("seq", "write"): "write"}
BS_SWEEP = ["4k", "16k", "32k", "64k", "128k", "256k", "512k", "1m"]
STRIPE_BS = "1536k"  # one full RAID5 stripe (3 data chunks x 512 KiB): no read-modify-write


def f0(q):
    """Calibration: fio software floor (null engine) and a QD1 sanity read."""
    return [J("null:qd1", "randread", "4k", engine="null", direct=0, check_io=False),
            J("null:qd32", "randread", "4k", engine="null", direct=0, check_io=False, **QD32),
            J("sanity:randread4k:qd1", "randread", "4k")]


def f1(q):
    """QD=1 baselines: 4 KiB random and 128 KiB sequential, read and write (json+ tails)."""
    return [J(f"qd1:{p}:{d}", RW[(p, d)], bs, runtime=30, hist=True)
            for p, bs in (("rand", "4k"), ("seq", "128k")) for d in ("read", "write")]


def f2(q):
    """Block-size sweep 4 KiB-1 MiB, random and sequential, read and write, QD 32 (4x8)."""
    bss = BS_SWEEP if not q else ["4k", "128k", "1m"]
    jobs = [J(f"bs:{p}:{d}", RW[(p, d)], bs, **QD32)
            for p in ("rand", "seq") for d in ("read", "write") for bs in bss]
    # full-stripe sequential writes (and reads, for symmetry)
    return jobs + [J(f"bs:seq:{d}", RW[("seq", d)], STRIPE_BS, **QD32) for d in ("read", "write")]


def f3(q):
    """Read/write mix 100/70/50/0 % reads: 4 KiB random and 128 KiB sequential, QD 32."""
    jobs = []
    for p, bs, rw in (("rand", "4k", "randrw"), ("seq", "128k", "rw")):
        for mix in (100, 70, 50, 0):
            jobs.append(J(f"mix:{p}{bs}", rw, bs, mix=mix, **QD32))
    return jobs


def f4(q):
    """Queue-depth / parallelism sweep: iodepth x numjobs for 4 KiB random and 128 KiB sequential."""
    jobs = []
    d1 = [1, 2, 4, 8, 16, 32, 64, 128, 256] if not q else [1, 16, 128]
    for qd in d1:
        jobs.append(J("qd:randread4k:j1", "randread", "4k", qd=qd))
    for n in ([2, 4, 8, 16] if not q else [4]):
        jobs.append(J("qd:randread4k:jobs@32", "randread", "4k", qd=32, jobs=n))
    for qd in ([1, 2, 4, 8, 16, 32, 64, 128] if not q else [4, 64]):
        jobs.append(J("qd:randread4k:j8", "randread", "4k", qd=qd, jobs=8))
    for qd in ([1, 2, 4, 8, 16, 32, 64, 128] if not q else [1, 32]):
        jobs.append(J("qd:randwrite4k:j4", "randwrite", "4k", qd=qd, jobs=4))
    for qd in ([1, 4, 16, 64] if not q else [16]):
        jobs.append(J("qd:randwrite4k:j1", "randwrite", "4k", qd=qd))
    for qd in ([1, 2, 4, 8, 16, 32, 64] if not q else [1, 16]):
        jobs.append(J("qd:seqread128k:j1", "read", "128k", qd=qd))
        jobs.append(J("qd:seqwrite128k:j1", "write", "128k", qd=qd))
    for qd in ([4, 8, 16, 32] if not q else [16]):
        jobs.append(J("qd:seqread128k:j4", "read", "128k", qd=qd, jobs=4))
        jobs.append(J("qd:seqwrite128k:j4", "write", "128k", qd=qd, jobs=4))
    return jobs


def f4_knee_qd(default=32):
    """Total QD at the throughput knee of the 4 KiB random-read j8 curve (from F4)."""
    try:
        s = pd.read_csv(C.SUMMARY / "F4.csv")
        d = s[s.tag == "qd:randread4k:j8"]
        i = C.knee_by_gain(d.qd_total, d.iops)
        return int(d.qd_total.iloc[i])
    except (FileNotFoundError, KeyError, ValueError, AttributeError):
        return default


def f5(q):
    """Tail latency (json+ histograms, 60 s) at mid-QD, near-knee and past-knee QD."""
    kq = f4_knee_qd()
    jobs = []
    for label, tot in (("mid", max(4, kq // 4)), ("knee", kq), ("past", min(1024, kq * 4))):
        n = 4 if tot >= 4 else 1
        for tag, rw, mix in (("randread4k", "randread", None), ("randwrite4k", "randwrite", None),
                             ("randrw70-4k", "randrw", 70)):
            jobs.append(J(f"tail:{tag}:{label}", rw, "4k", qd=tot // n, jobs=n, mix=mix,
                          runtime=60, hist=True))
    return jobs


def f6(q):
    """Working set / LBA range 16 MiB-224 GiB: 4 KiB random read (QD1, QD32) and write (QD32)."""
    regions = [16 * M, 64 * M, 256 * M, 1 * G, 4 * G, 16 * G, 64 * G, C.DATA_SIZE]
    if q:
        regions = [16 * M, 1 * G, C.DATA_SIZE]
    jobs = []
    for r in regions:
        jobs.append(J("lba:randread4k:qd1", "randread", "4k", region=r))
        jobs.append(J("lba:randread4k:qd32", "randread", "4k", region=r, **QD32))
        jobs.append(J("lba:randwrite4k:qd32", "randwrite", "4k", region=r, **QD32))
    return jobs


PROBES = lambda ph: [  # noqa: E731
    J(f"state:{ph}:randread4k:qd1", "randread", "4k", runtime=30, hist=True, phase=ph),
    J(f"state:{ph}:randwrite4k:qd1", "randwrite", "4k", runtime=30, hist=True, phase=ph),
    J(f"state:{ph}:randread4k:qd32", "randread", "4k", phase=ph, **QD32)]


def f7(q):
    """Device state: QD1 probes, ~40 min sustained 4 KiB random write over 224 GiB, probes after and after 10 min idle."""
    seg = 40 if not q else 2
    steps = PROBES("before")
    for i in range(seg):  # back-to-back 60 s segments -> per-minute percentiles
        steps.append(J("state:sustain:randwrite4k", "randwrite", "4k", region=C.DATA_SIZE,
                       runtime=60 if not q else 10, ramp=0, iolog=True, segment=i, **QD32))
    steps += PROBES("after")
    steps.append({"sleep": 600 if not q else 10})
    steps += PROBES("idle10m")
    return steps


PC_FILE = C.SCRATCH / "pagecache.dat"   # 8 GiB, fully written
PAT_FILE = C.SCRATCH / "pattern.dat"    # 8 GiB, F8 data-pattern runs only (zeros never touch data.dat)
FA_FILE = C.SCRATCH / "fallocated.dat"  # 16 GiB, fallocated, never written


def f8(q):
    """Pitfalls: page cache, wrong effective QD, unwritten extents, data pattern, CPU-bound generator."""
    pc = dict(file=PC_FILE, region=8 * G, check_io=False, ramp=0)
    steps = [
        # (a) page cache: direct vs cold buffered vs warm buffered (file read into cache first)
        J("pc:randread4k:direct", "randread", "4k", **pc),
        J("pc:randread4k:buffered-cold", "randread", "4k", direct=0, engine="psync", **pc),
        J("pc:warmup", "read", "1m", direct=0, engine="psync", runtime=60,
          extra=["--time_based=0", "--loops=1"], **pc),
        J("pc:randread4k:buffered-warm", "randread", "4k", direct=0, engine="psync",
          extra=["--invalidate=0"], **pc),
        J("pc:seqread128k:direct", "read", "128k", **pc),
        J("pc:seqread128k:buffered-warm", "read", "128k", direct=0, engine="psync",
          extra=["--invalidate=0"], **pc),
        J("pc:randwrite4k:direct", "randwrite", "4k", **pc),
        J("pc:randwrite4k:buffered", "randwrite", "4k", direct=0, engine="psync",
          extra=["--invalidate=0"], **pc),
        # (b) effective queue depth: asked for 32, what is really in flight?
        J("qd:libaio-direct-qd32", "randread", "4k", qd=32),
        J("qd:libaio-buffered-qd32", "randread", "4k", qd=32, direct=0, check_io=False),
        J("qd:psync-qd32", "randread", "4k", qd=32, engine="psync"),
        J("qd:psync-32jobs", "randread", "4k", jobs=32, engine="psync"),
        # (c) never-written (fallocated) extents vs written data
        {"fallocate": str(FA_FILE), "size": 16 * G},
        J("fa:randread4k:qd32:unwritten", "randread", "4k", file=FA_FILE, region=16 * G,
          check_io=False, ramp=0, **QD32),
        J("fa:randread4k:qd32:written", "randread", "4k", region=16 * G, ramp=0, **QD32),
        J("fa:randwrite4k:qd1:first-write", "randwrite", "4k", file=FA_FILE, region=16 * G,
          runtime=30, ramp=0, check_io=False),
        J("fa:randwrite4k:qd1:overwrite", "randwrite", "4k", region=16 * G, runtime=30, ramp=0),
        {"remove": str(FA_FILE)},
    ]
    # (d) data pattern: the drives do not compress; verify it makes no difference.
    #     On a scratch file (fully written first), so zeros never land in data.dat.
    pf = dict(file=PAT_FILE, region=8 * G)
    steps.append({"prefill": str(PAT_FILE), "size": 8 * G})
    for pat in ("incompressible", "compress50", "zeros"):
        steps.append(J(f"pattern:seqwrite128k:{pat}", "write", "128k", pattern=pat, **pf, **QD32))
        steps.append(J(f"pattern:randwrite4k:{pat}", "randwrite", "4k", pattern=pat, **pf, **QD32))
    # (e) the generator as the bottleneck: refill_buffers makes fio itself the limit
    for pat in ("incompressible", "refill"):
        steps.append(J(f"gen:seqwrite128k:qd1:{pat}", "write", "128k", pattern=pat, **pf))
        steps.append(J(f"gen:seqwrite128k:qd32:{pat}", "write", "128k", pattern=pat, **pf, **QD32))
    steps.append({"remove": str(PAT_FILE)})
    return steps


EXPERIMENTS = {"F0": f0, "F1": f1, "F2": f2, "F3": f3, "F4": f4, "F5": f5, "F6": f6,
               "F7": f7, "F8": f8}
ORDERED = {"F7", "F8"}  # device/page-cache state matters: never shuffle
DEFAULTS = {"F0": 3, "F1": 3, "F2": 2, "F3": 3, "F4": 2, "F5": 2, "F6": 2, "F7": 1, "F8": 2}


# --------------------------------------------------------------------- runner
def md_rate(window=2.0):
    a = C.diskstats([C.MD])[C.MD]
    time.sleep(window)
    b = C.diskstats([C.MD])[C.MD]
    iops = ((b[0] - a[0]) + (b[2] - a[2])) / window
    mbps = ((b[1] - a[1]) + (b[3] - a[3])) * 512 / window / 1e6
    return iops, mbps


def wait_quiet(why=""):
    t0 = time.time()
    while True:
        iops, mbps = md_rate()
        if iops <= GATE_IOPS and mbps <= GATE_MBPS:
            return time.time() - t0
        if int(time.time() - t0) % 600 < 32:
            print(f"  waiting for quiet /data{why}: {iops:.0f} IOPS, {mbps:.1f} MB/s", flush=True)
        time.sleep(30)


def child_cpu():
    r = resource.getrusage(resource.RUSAGE_CHILDREN)
    return r.ru_utime + r.ru_stime


def child_block_bytes():
    """Block-layer bytes read+written by our reaped children (fio). Linux fills
    ru_inblock/ru_oublock from per-task I/O accounting (512-byte units), which
    charges O_DIRECT bios to the submitting process -> exactly fio's own I/O,
    ramp and all (verified: equals md124's delta on an idle array)."""
    r = resource.getrusage(resource.RUSAGE_CHILDREN)
    return (r.ru_inblock + r.ru_oublock) * 512


def run_job(job, cpus, quick, logdir):
    cmd = fio_cmd(job, cpus, quick, logdir)
    names = [C.MD] + C.MEMBERS
    d0, t0, b0 = C.diskstats(names), time.time(), child_block_bytes()
    p = subprocess.run(cmd, capture_output=True, text=True)
    wall = time.time() - t0
    d1, ours = C.diskstats(names), child_block_bytes() - b0
    if p.returncode:
        sys.exit(f"fio failed: {' '.join(cmd)}\n{p.stderr}")
    txt = p.stdout[p.stdout.index("{"):]  # fio may print notes before the JSON
    doc = json.loads(txt)
    row = C.parse_fio(doc)
    dd = C.diskstats_delta(d0, d1)
    md = dd[C.MD]
    row["md_bytes_wall"] = (md[1] + md[3]) * 512
    row["wall_s"] = wall
    for k, nm in ((0, "reads"), (2, "writes")):
        row[f"mem_{nm}_wall"] = sum(dd[m][k] for m in C.MEMBERS)
    # device-side Little's Law: mean requests outstanding at the 4 drives over
    # the job (what the SSDs actually see, whatever fio believes it submitted)
    row["dev_qd"] = sum(dd[m][5] for m in C.MEMBERS) / (wall * 1e3)
    row["dev_busy"] = sum(dd[m][4] for m in C.MEMBERS) / (wall * 1e3) / len(C.MEMBERS)
    # foreign traffic on /data during the job: md bytes beyond fio's own block I/O
    row["fio_block_bytes"] = ours
    row["others_MBps"] = max(0.0, (row["md_bytes_wall"] - ours) / wall / 1e6)
    row["runtime_eff"] = min(job["runtime"], 5) if quick else job["runtime"]
    row["ramp_eff"] = min(job["ramp"], 1) if quick else job["ramp"]
    return row, doc, cmd


def git_hash():
    try:
        return subprocess.run(["git", "-C", str(C.ROOT), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


def do_step(step):
    """Non-fio steps of ordered experiments."""
    if "sleep" in step:
        print(f"  idle {step['sleep']} s", flush=True)
        time.sleep(step["sleep"])
    elif "fallocate" in step:
        fd = os.open(step["fallocate"], os.O_CREAT | os.O_WRONLY, 0o644)
        os.posix_fallocate(fd, 0, step["size"])
        os.close(fd)
    elif "remove" in step:
        os.unlink(step["remove"])
    elif "prefill" in step:
        import prep
        prep.create(Path(step["prefill"]), step["size"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("exp", nargs="?")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--rounds", type=int)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list or not a.exp:
        for k, f in EXPERIMENTS.items():
            n = sum("tag" in j for j in f(False))
            print(f"{k}: {n:3d} jobs  {(f.__doc__ or '').strip().splitlines()[0]}")
        return
    exp = a.exp.upper()
    rounds = 1 if a.quick else (a.rounds or DEFAULTS[exp])
    steps = EXPERIMENTS[exp](a.quick)
    if not C.DATAFILE.exists() or C.DATAFILE.stat().st_size < C.DATA_SIZE:
        sys.exit(f"{C.DATAFILE} missing or short: run scripts/prep.py first")
    if exp == "F8" and (not PC_FILE.exists() or PC_FILE.stat().st_size < 8 * G):
        sys.exit(f"{PC_FILE} missing: run scripts/prep.py first")
    node_cpus = pick_core.node_order(C.NVME_NODE)
    # keep this driver off the cpus fio will use
    os.sched_setaffinity(0, set(range(os.cpu_count())) - set(node_cpus[:32]) or {0})
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + ("-quick" if a.quick else "")
    out = C.RAW / exp
    (out / run_id).mkdir(parents=True, exist_ok=True)
    meta = dict(exp=exp, run_id=run_id, quick=a.quick, rounds=rounds, host=platform.node(),
                git=git_hash(), fio=subprocess.run([str(C.FIO), "--version"], capture_output=True,
                                                   text=True).stdout.strip(),
                started=datetime.now().isoformat(), cpus=node_cpus, steps=steps)
    print(f"{exp}: {sum('tag' in s for s in steps)} jobs x {rounds} rounds "
          f"({'ordered' if exp in ORDERED else 'shuffled'}), cpus from node {C.NVME_NODE}",
          flush=True)
    t_start = time.time()
    retried = flagged = 0
    for r in range(rounds):
        order = list(range(len(steps)))
        if exp not in ORDERED:
            random.Random(1000 + r).shuffle(order)
        rows = []
        for done, ji in enumerate(order):
            job = steps[ji]
            if "tag" not in job:
                do_step(job)
                continue
            cpus = node_cpus[:job["jobs"]]
            for attempt in range(1, 4):
                if job["check_io"]:
                    wait_quiet()
                logdir = str(out / run_id / f"j{ji:03d}_r{r}") if job["iolog"] else None
                c0 = child_cpu()
                row, doc, cmd = run_job(job, cpus, a.quick, logdir)
                row["fio_cpu_s"] = child_cpu() - c0
                if not job["check_io"] or row["others_MBps"] <= OTHERS_MBPS:
                    break
                retried += 1
                print(f"  job {ji} saw {row['others_MBps']:.0f} MB/s of other traffic; "
                      f"re-measuring", flush=True)
            flagged += job["check_io"] and row["others_MBps"] > OTHERS_MBPS
            (out / run_id / f"j{ji:03d}_r{r}.json").write_text(json.dumps(doc))
            meta_cols = {k: v for k, v in job.items() if k not in ("extra",)}
            meta_cols.update(extra=" ".join(job["extra"]), qd_total=job["qd"] * job["jobs"],
                             bs_bytes=size_of(job["bs"]), round=r, job=ji, attempts=attempt,
                             cmd=" ".join(cmd[1:]))
            rows.append({**meta_cols, **row})
            if (done + 1) % max(1, len(steps) // 10) == 0:
                print(f"  round {r + 1}/{rounds}: {done + 1}/{len(steps)} steps "
                      f"({time.time() - t_start:.0f}s)", flush=True)
        pd.DataFrame(rows).to_csv(out / f"{run_id}__r{r}.csv", index=False)
    meta.update(finished=datetime.now().isoformat(), elapsed_s=round(time.time() - t_start, 1),
                jobs_retried=int(retried), jobs_still_disturbed=int(flagged))
    (out / f"{run_id}.meta.json").write_text(json.dumps(meta, indent=1, default=str))
    print(f"{exp} done in {meta['elapsed_s']}s ({retried} re-measured, {flagged} still disturbed)")


if __name__ == "__main__":
    main()
