#!/usr/bin/env python3
"""Run an experiment: build, pin to an idle core, execute jobs in randomized
order over several rounds, write raw CSV + metadata.

  python scripts/run.py E1            # full
  python scripts/run.py E1 --quick    # smoke test (few sizes, 1 round)
  python scripts/run.py --list

Output: results/raw/<EXP>/<run_id>__r<round>.csv and <run_id>.meta.json.
Every row carries build/compiler/config/round/job and the CCD neighbour
utilization measured over that job (to flag interference from other users).
"""
import argparse
import io
import json
import os
import platform
import random
import subprocess
import sys
import time
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import common as C  # noqa: E402
import pick_core  # noqa: E402

GCC4 = ["gcc-scalar", "gcc-default", "gcc-native256", "gcc-native512"]
CLANG4 = ["clang-scalar", "clang-default", "clang-native256", "clang-native512"]
ALL10 = [f"{c}-{f}" for c in ("gcc", "clang")
         for f in ("scalar", "default", "native256", "native512", "native512fm")]


def J(build, kernel, ns, dtype="f32", **kw):
    """One bench invocation (a list of n values)."""
    return dict(build=build, kernel=kernel, dtype=dtype, ns=list(ns), **kw)


def sweep_ns(kernel, dtype, quick, per_octave=4, lo=8 * C.KiB, hi=1 * C.GiB, **kw):
    fps = C.footprint_sweep(lo, hi, per_octave if not quick else 1)
    return sorted({C.n_for_footprint(kernel, dtype, f, **kw) for f in fps})


# ---------------------------------------------------------------- experiments
def e0(q):
    jobs = [dict(tool="calib_peak"), dict(tool="calib_align")]
    for k in ("bw_read", "bw_write", "bw_scale", "bw_triad"):
        jobs.append(J("gcc-native512", k, sweep_ns(k, "f32", q), counters="core"))
    return jobs


def e1(q):
    return [J(b, k, sweep_ns(k, "f32", q), counters="core")
            for b in GCC4 + CLANG4 for k in ("dot_simd", "mul", "stencil")]


def e2(q):
    """Locality: E1 provides the dot sweep; here add memory-traffic counters
    (estimated DRAM read lines) for dot and mul, scalar vs widest."""
    return [J(b, k, sweep_ns(k, "f32", q, per_octave=2), counters="mem")
            for b in ("gcc-scalar", "gcc-native512") for k in ("dot_simd", "mul")]


def e3(q):
    jobs = []
    offs = [0, 4, 8, 16, 32, 48, 60] if not q else [0, 4, 32]
    builds = ["gcc-scalar", "gcc-native256", "gcc-native512", "clang-native512"]
    for regime, fp in (("L1", 24 * C.KiB), ("L2", 512 * C.KiB)):
        for b in builds:
            for k in ("mul", "stencil", "dot_simd"):
                n = C.n_for_footprint(k, "f32", fp)
                for o in offs:
                    jobs.append(J(b, k, [n], offset=o, counters="align", tag=f"align-{regime}"))
                # relative misalignment: arrays on different offsets
                jobs.append(J(b, k, [n], offset_x=0, offset_y=4, offset_z=8, counters="align",
                              tag=f"align-{regime}-mixed"))
            jobs.append(J(b, "mul_aligned", [C.n_for_footprint("mul", "f32", fp)], offset=0,
                          counters="align", tag=f"align-{regime}"))
    tail_ns = list(range(1024, 1089)) if not q else list(range(1024, 1089, 7))
    small_ns = list(range(1, 129)) if not q else list(range(1, 129, 9))
    for b, k in (("gcc-scalar", "mul"), ("gcc-native256", "mul"), ("gcc-native512", "mul"),
                 ("clang-native512", "mul"), ("clang-native512", "mul_pred"),
                 ("clang-native256", "mul_pred")):
        jobs.append(J(b, k, tail_ns, counters="core", tag="tail"))
        jobs.append(J(b, k, small_ns, counters="core", tag="small"))
    return jobs


STRIDE_PATTERNS = [  # (label, kernel-suffix or gather pattern, gstride)
    ("s1", "", None), ("s2", "_s2", None), ("s4", "_s4", None), ("s8", "_s8", None),
    ("g-identity", "identity", 1), ("g-s2", "stride", 2), ("g-s4", "stride", 4),
    ("g-s8", "stride", 8), ("g-random", "random", 1),
]


def e4(q):
    jobs = []
    builds = ["gcc-scalar", "gcc-native256", "gcc-native512", "clang-native512"]
    for regime, fp in (("L2", 512 * C.KiB), ("DRAM", 512 * C.MiB)):
        for fam, base, gk in (("mul", "mul", "mul_gather"), ("dot", "dot_simd", "dot_gather")):
            for label, p, gs in STRIDE_PATTERNS:
                if gs is None:
                    k = base if p == "" else f"{fam}{p}"
                    n = C.n_for_footprint(k, "f32", fp)
                    extra = {}
                else:
                    k = gk
                    n = C.n_for_footprint(k, "f32", fp, gstride=gs, pattern=p)
                    extra = dict(pattern=p, gstride=gs)
                for b in builds:
                    jobs.append(J(b, k, [n], counters="core", tag=f"{regime}:{label}", **extra))
    return jobs


def e5(q):
    jobs = []
    builds = ["gcc-scalar", "gcc-native256", "gcc-native512", "clang-scalar", "clang-native512"]
    for regime, fp in (("L1", 16 * C.KiB), ("L2", 256 * C.KiB), ("DRAM", 512 * C.MiB)):
        for b in builds:
            for k in ("dot_simd", "mul", "stencil"):
                for d in ("f32", "f64", "i32"):
                    jobs.append(J(b, k, [C.n_for_footprint(k, d, fp)], dtype=d, counters="core",
                                  tag=regime))
    return jobs


E6_KERNELS = ["dot_simd", "dot_plain", "dot_acc4", "mul", "mul_pred", "stencil", "dot_s4",
              "mul_s8", "dot_gather", "mul_gather"]


def e6(q):
    """PMU width mix at an L1-resident size, every build."""
    jobs = []
    for b in ALL10:
        for k in E6_KERNELS:
            extra = dict(pattern="random") if "gather" in k else {}
            n = C.n_for_footprint(k, "f32", 16 * C.KiB, **extra)
            jobs.append(J(b, k, [n], counters="width", **extra))
            jobs.append(J(b, k, [n], counters="core", **extra))
    return jobs


def e8(q):
    builds = ["gcc-scalar", "gcc-native512", "gcc-native512fm", "clang-scalar",
              "clang-native512", "clang-native512fm"]
    return [J(b, k, sweep_ns(k, "f32", q, per_octave=2), counters="core")
            for b in builds for k in ("dot_plain", "dot_simd")]


def e9(q):
    jobs = []
    builds = ["gcc-scalar", "gcc-default", "gcc-native256", "gcc-native512", "clang-scalar",
              "clang-native256", "clang-native512"]
    ns = [512 * i for i in range(1, 8)]  # 4..28 KiB footprint: all L1
    for b in builds:
        for k in ("dot_simd", "dot_acc2", "dot_acc4", "dot_acc8"):
            jobs.append(J(b, k, ns, counters="core", serialize=True, tag="serial"))
            jobs.append(J(b, k, [3072], counters="core", tag="b2b"))
    return jobs


EXPERIMENTS = {"E0": e0, "E1": e1, "E2": e2, "E3": e3, "E4": e4, "E5": e5, "E6": e6,
               "E8": e8, "E9": e9}
DEFAULTS = {  # rounds, samples, min_ms
    "E0": (3, 11, 2.0), "E1": (3, 11, 2.0), "E2": (2, 7, 2.0), "E3": (3, 11, 2.0),
    "E4": (3, 11, 2.0), "E5": (3, 11, 2.0), "E6": (1, 5, 2.0), "E8": (2, 9, 2.0),
    "E9": (3, 11, 2.0),
}


# --------------------------------------------------------------------- runner
def bench_cmd(job, core, samples, min_ms):
    cmd = ["taskset", "-c", str(core), str(C.BUILD / job["build"] / "bench"),
           "--kernel", job["kernel"], "--dtype", job["dtype"],
           "--n", ",".join(map(str, job["ns"])), "--samples", str(samples),
           "--min-ms", str(min_ms), "--counters", job.get("counters", "core"), "--header"]
    for key, flag in (("offset", "--offset"), ("offset_x", "--offset-x"),
                      ("offset_y", "--offset-y"), ("offset_z", "--offset-z"),
                      ("pattern", "--pattern"), ("gstride", "--gstride")):
        if key in job:
            cmd += [flag, str(job[key])]
    if job.get("serialize"):
        cmd.append("--serialize")
    return cmd


def ccd_neighbor_util(core, before, after, elapsed):
    """Summed utilization of the other cores on our CCD during a job. NaN for
    jobs < 0.25 s: /proc/stat has 10 ms jiffies, too coarse to say."""
    if elapsed < 0.25:
        return float("nan")
    ccd = next(g for g in pick_core.ccds() if core in g)
    tot = 0.0
    for c in ccd:
        if c == core:
            continue
        dt = after[c][0] - before[c][0]
        di = after[c][1] - before[c][1]
        tot += 0.0 if dt <= 0 else 1.0 - di / dt
    return tot


def ccd_busy(core, window=1.0):
    """Summed utilization of the other cores on `core`'s CCD right now."""
    a = pick_core.cpu_times()
    time.sleep(window)
    return ccd_neighbor_util(core, a, pick_core.cpu_times(), window)


def wait_quiet(core, limit=0.5, max_wait_s=3600, why="", window=1.0):
    """Block until our CCD is quiet (shared L3 / memory path), polling every
    30 s. Returns seconds waited."""
    t0 = time.time()
    while (u := ccd_busy(core, window)) > limit:
        if time.time() - t0 > max_wait_s:
            sys.exit(f"CCD of core {core} still busy (util {u:.2f}) after {max_wait_s}s; giving up")
        print(f"  waiting for quiet CCD{why}: neighbour util {u:.2f}", flush=True)
        time.sleep(30)
    return time.time() - t0


def git_hash():
    try:
        return subprocess.run(["git", "-C", str(C.ROOT), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("exp", nargs="?")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--rounds", type=int)
    ap.add_argument("--samples", type=int)
    ap.add_argument("--min-ms", type=float)
    ap.add_argument("--core", type=int, help="override automatic idle-core choice")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    if a.list or not a.exp:
        for k, f in EXPERIMENTS.items():
            print(f"{k}: {len(f(False))} jobs  {(f.__doc__ or '').strip().splitlines()[0] if f.__doc__ else ''}")
        return
    exp = a.exp.upper()
    rounds, samples, min_ms = DEFAULTS[exp]
    if a.quick:
        rounds, samples, min_ms = 1, 3, 1.0
    rounds = a.rounds or rounds
    samples = a.samples or samples
    min_ms = a.min_ms or min_ms
    jobs = EXPERIMENTS[exp](a.quick)

    subprocess.run(["make", "-s", "-j16", "-C", str(C.ROOT), "all"], check=True)
    t_wait = time.time()
    while True:  # wait (up to an hour) for a quiet CCD instead of measuring under load
        pick = pick_core.pick()
        if pick["ok"] or a.core is not None or time.time() - t_wait > 3600:
            break
        print(f"no quiet CCD on node 0 (best {pick['ccd']} util {pick['ccd_util_sum']:.2f}); "
              "waiting 30 s", flush=True)
        time.sleep(30)
    if not pick["ok"] and a.core is None:
        sys.exit("no quiet CCD on node 0 for an hour; retry later or pass --core")
    core = a.core if a.core is not None else pick["core"]
    # keep this driver (pandas, subprocess plumbing) off the measured CCD
    ccd = next(g for g in pick_core.ccds() if core in g)
    os.sched_setaffinity(0, set(range(os.cpu_count())) - set(ccd))
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + ("-quick" if a.quick else "")
    out = C.RAW / exp
    out.mkdir(parents=True, exist_ok=True)
    meta = dict(exp=exp, run_id=run_id, quick=a.quick, rounds=rounds, samples=samples,
                min_ms=min_ms, core=core, ccd=pick["ccd"], ccd_util_at_start=pick["ccd_util_sum"],
                host=platform.node(), git=git_hash(), started=datetime.now().isoformat(),
                n_jobs=len(jobs), jobs=jobs)
    print(f"{exp}: {len(jobs)} jobs x {rounds} rounds on core {core} (CCD {pick['ccd']})",
          flush=True)
    t_start = time.time()
    flagged = retried = 0
    last_check = 0.0
    for r in range(rounds):
        order = list(range(len(jobs)))
        random.Random(1000 + r).shuffle(order)
        frames = []
        for done, ji in enumerate(order):
            job = jobs[ji]
            if time.time() - last_check > 2.0:  # covers jobs too short to self-check
                wait_quiet(core, window=0.3)
                last_check = time.time()
            for attempt in range(1, 4):  # re-measure jobs disturbed by CCD neighbours
                before, t_job = pick_core.cpu_times(), time.time()
                if job.get("tool"):  # standalone calibration binary in build/common
                    p = subprocess.run(["taskset", "-c", str(core),
                                        str(C.BUILD / "common" / job["tool"])],
                                       capture_output=True, text=True, check=True)
                    df = pd.read_csv(io.StringIO(p.stdout))
                    df["build"] = "calib" if job["tool"] == "calib_peak" else job["tool"]
                else:
                    cmd = bench_cmd(job, core, samples, min_ms)
                    p = subprocess.run(cmd, capture_output=True, text=True)
                    if p.returncode:
                        sys.exit(f"bench failed: {' '.join(cmd)}\n{p.stderr}")
                    df = pd.read_csv(io.StringIO(p.stdout))
                    df["build"] = job["build"]
                    df["serialize"] = bool(job.get("serialize"))
                util = ccd_neighbor_util(core, before, pick_core.cpu_times(), time.time() - t_job)
                if not util > 0.5:  # NaN (too short to judge) counts as clean
                    break
                retried += 1
                wait_quiet(core, why=f" (retrying job {ji}, attempt {attempt + 1})")
            flagged += util > 0.5
            df["attempts"] = attempt
            df.insert(0, "exp", exp)
            df["compiler"] = df.build.str.split("-").str[0]
            df["config"] = df.build.str.split("-", n=1).str[1]
            df["round"], df["job"], df["tag"] = r, ji, job.get("tag", "")
            df["neighbor_util"] = round(util, 3)
            frames.append(df)
            if (done + 1) % max(1, len(jobs) // 10) == 0:
                print(f"  round {r + 1}/{rounds}: {done + 1}/{len(jobs)} jobs "
                      f"({time.time() - t_start:.0f}s)", flush=True)
        pd.concat(frames, ignore_index=True).to_csv(out / f"{run_id}__r{r}.csv", index=False)
    meta["finished"] = datetime.now().isoformat()
    meta["elapsed_s"] = round(time.time() - t_start, 1)
    meta["jobs_with_busy_neighbors"] = int(flagged)
    meta["jobs_retried"] = int(retried)
    (out / f"{run_id}.meta.json").write_text(json.dumps(meta, indent=1))
    print(f"{exp} done in {meta['elapsed_s']}s -> {out}/{run_id}__r*.csv "
          f"({retried} re-measured because of busy neighbours, {flagged} still busy after 3 tries)")


if __name__ == "__main__":
    main()
