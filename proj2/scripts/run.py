#!/usr/bin/env python3
"""Run an experiment: build, choose idle cores / an idle NUMA node, execute
membench jobs in shuffled order over several rounds, write raw CSV + metadata.

  python scripts/run.py latency            # full (old IDs like E1 also work)
  python scripts/run.py latency --quick    # smoke test (fewer points, 1 round)
  python scripts/run.py intensity --resume 20261020-160206   # finish an interrupted run
  python scripts/run.py --list

Output: results/raw/<EXP>/<run_id>__r<round>.csv and <run_id>.meta.json
(written last: marks a completed run).

Placement (membench pins its own threads):
  core      one thread on an idle core of the quietest CCD (single-thread probes)
  node      n threads on the quieter NUMA node, round-robin over its CCDs
            (bandwidth / loaded latency); element 0 = latency core
  unpinned  no pinning at all, full-machine affinity (the pitfall experiment)
Interference gate (shared machine): before each job the watched cpus (the
CCD of `core` jobs, the whole node for `node` jobs) must be quiet, and every
row records `others_util` = cores' worth of OTHER users' activity on the
watched cpus during the job (busy jiffies minus our own child CPU time). A job
that saw > LIMIT is re-measured (up to 3 attempts, `attempts` column).
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

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import common as C  # noqa: E402
import pick_core  # noqa: E402

K, M, G = C.KiB, C.MiB, C.GiB
LEVELS = {"L1": 16 * K, "L2": 512 * K, "L3": 16 * M, "DRAM": 1 * G}
NODE_THREADS = 31            # all cores of a node except its first (housekeeping)
# Cores' worth of OTHER activity tolerated on the watched cpus. Node limit is
# 2.0 (not 1.0): background services (VS Code server etc.) keep ~1.1 cores
# busy on every node of this shared machine; `others_util` records it per row.
LIMIT = {"core": 0.5, "node": 2.0, "unpinned": 3.0}  # unpinned watches all 64 cpus


def chase(size, tag, counters="fills", place="core", **a):
    return dict(mode="chase", place=place, tag=tag,
                args=dict(size=size, counters=counters, **a))


def bw(n, size, tag, place="node", counters="clock", **a):
    """n generator threads, `size` bytes each."""
    return dict(mode="bw", place=place if n > 1 else "core", n=n, tag=tag,
                args=dict(size=size, counters=counters, **a))


def loadlat(gens, tag, size=64 * M, counters="clock", **a):
    return dict(mode="loadlat", place="node", n=gens + 1, tag=tag,
                args=dict(size=size, counters=counters, **a))


def kern(kernel, pattern, n, tag, counters="cache", **a):
    return dict(mode="kern", place="core", tag=tag,
                args=dict(kernel=kernel, pattern=pattern, n=n, counters=counters, **a))


def kern_n(kernel, pattern, footprint, gstride=1):
    """n whose footprint (all arrays incl. idx) is closest to `footprint`."""
    arrays = 3 if kernel == "mul" else 2
    per = arrays * 8 * (gstride if pattern == "stride" else 1) + (4 if pattern == "gather" else 0)
    return max(64, int(footprint // per) // 64 * 64)


def sweep(lo, hi, per_octave, quick, align=C.LINE):
    return C.footprint_sweep(lo, hi, 1 if quick else per_octave, align)


# ---------------------------------------------------------------- experiments
def jobs_calib(q):
    """Calibration: clock (L1 chase cycles/ns) and a DRAM latency anchor."""
    return [chase(16 * K, "clock", counters="clock"), chase(1 * G, "dram", counters="fills")]


def jobs_latency(q):
    """Zero-queue latency per level (random 64 B chase), 2 MiB vs 4 KiB pages, local vs remote DRAM."""
    jobs = []
    for pages in ("2m", "4k"):
        for lv, size in LEVELS.items():
            jobs.append(chase(size, f"{lv}:{pages}", pages=pages))
            jobs.append(chase(size, f"{lv}:{pages}", pages=pages, counters="tlb"))
    jobs.append(chase(1 * G, "DRAM-remote:2m", remote=True))
    jobs.append(chase(1 * G, "DRAM-remote:2m", remote=True, counters="tlb"))
    return jobs


def jobs_workingset(q):
    """Working-set sweep: random-chase latency and read bandwidth (1 core, whole node)."""
    jobs = [chase(s, "lat") for s in sweep(4 * K, 2 * G, 4, q)]
    jobs += [bw(1, s, "bw1", ms=50, rw="1:0") for s in sweep(8 * K, 2 * G, 2, q)]
    for tot in sweep(256 * K, 4 * G, 2, q):
        per = max(4 * K, tot // NODE_THREADS // (4 * K) * (4 * K))
        jobs.append(bw(NODE_THREADS, per, "bwnode", ms=50, rw="1:0"))
    return jobs


def jobs_pattern(q):
    """Access pattern and granularity: dependent latency and independent bandwidth."""
    jobs = []
    for size in (16 * M, 1 * G):
        for pat in ("seq", "random"):
            for st in (64, 256, 1024):
                jobs.append(chase(size, f"lat:{pat}:{st}", pattern=pat, stride=st, counters="mem"))
    for n, size in ((1, 1 * G), (NODE_THREADS, 64 * M)):
        who = "core" if n == 1 else "node"
        cnt = "mem" if n == 1 else "clock"
        for order in ("seq", "random"):
            for st in (64, 256, 1024):
                jobs.append(bw(n, size, f"bw:{who}:stride", order=order, stride=st, gran=64,
                               rw="1:0", counters=cnt))
        for g in (8, 16, 32, 64):
            jobs.append(bw(n, size, f"bw:{who}:dense", stride=g, gran=g, rw="1:0", counters=cnt))
            if g < 64:
                jobs.append(bw(n, size, f"bw:{who}:partial", stride=64, gran=g, rw="1:0",
                               counters=cnt))
    return jobs


MIXES = [("1:0", False), ("0:1", False), ("0:1", True), ("7:3", False), ("7:3", True),
         ("1:1", False), ("1:1", True)]


def jobs_rwmix(q):
    """Read/write mix: bandwidth (1 core, whole node) and loaded latency at full load."""
    jobs = []
    for rw, nt in MIXES:
        kw = dict(rw=rw, nt=True) if nt else dict(rw=rw)
        tag = f"{rw}{':nt' if nt else ''}"
        jobs.append(bw(1, 1 * G, f"core:{tag}", counters="mem", **kw))
        jobs.append(bw(NODE_THREADS, 64 * M, f"node:{tag}", **kw))
        jobs.append(loadlat(NODE_THREADS - 1, f"loaded:{tag}", **kw))
    jobs.append(loadlat(0, "idle"))
    return jobs


def jobs_intensity(q):
    """Intensity: loaded-latency sweeps (generators x injection delay) and 1-core MLP sweep."""
    jobs = []
    gens = [0, 1, 2, 4, 6, 8, 12, 16, 20, 24, 30] if not q else [0, 4, 30]
    delays = ([0, 2, 5, 8, 12, 16, 20, 25, 30, 40, 50, 75, 100, 150, 200, 400, 800, 1600]
              if not q else [0, 400])
    for rw in ("1:0", "7:3", "1:1"):
        for g in gens:
            jobs.append(loadlat(g, f"gens:{rw}", rw=rw))
        for d in delays[1:]:
            jobs.append(loadlat(NODE_THREADS - 1, f"delay:{rw}", rw=rw, delay=d))
    ks = [1, 2, 3, 4, 5, 6, 8, 10, 12, 14, 16, 20, 24, 28, 32] if not q else [1, 4, 16]
    for size, lv in ((1 * G, "DRAM"), (16 * M, "L3")):
        for k in ks:
            jobs.append(chase(size, f"mlp:{lv}", chains=k, counters="clock"))
    return jobs


def jobs_cachemiss(q):
    """Cache-miss impact: mul/axpy x contig/stride-8/gather over footprints, 3 counter sets."""
    jobs = []
    for fp in sweep(16 * K, 1 * G, 2, q):
        for k in ("mul", "axpy"):
            for pat, gs in (("contig", 1), ("stride", 8), ("gather", 1)):
                n = kern_n(k, pat, fp, gs)
                for cnt in ("cache", "fills", "mem"):
                    jobs.append(kern(k, pat, n, f"{pat}", counters=cnt, gstride=gs))
    return jobs


def jobs_tlb(q):
    """TLB-miss impact: 4 KiB vs 2 MiB pages (page-stride chase, random chase, kernels)."""
    jobs = []
    for pages in ("4k", "2m"):
        for p in sweep(16, 256 * K, 2, q, align=1):  # p = number of 4 KiB pages touched
            jobs.append(chase(p * 4 * K, f"pagerand:{pages}", pattern="pagerand", pages=pages,
                              counters="tlb"))
        for s in sweep(256 * K, 4 * G, 2, q):
            jobs.append(chase(s, f"random:{pages}", pages=pages, counters="tlb"))
        for fp in sweep(1 * M, 1 * G, 2, q):
            jobs.append(kern("mul", "stride", kern_n("mul", "stride", fp, 512), f"page-stride:{pages}",
                             counters="tlb", gstride=512, pages=pages))
            jobs.append(kern("mul", "gather", kern_n("mul", "gather", fp), f"gather:{pages}",
                             counters="tlb", pages=pages))
    return jobs


def jobs_pitfall(q):
    """Pitfalls: cache reuse posing as DRAM, unpinned/remote placement, prefetchable 'random'."""
    jobs = []
    for s in (4 * M, 8 * M, 16 * M, 32 * M, 64 * M, 128 * M, 1 * G):
        jobs.append(chase(s, "reuse:lat"))
        jobs.append(bw(1, s, "reuse:bw", rw="1:0"))
    reps = 10 if not q else 2
    for i in range(reps):
        jobs.append(chase(1 * G, "pin:local", samples=5, rep=i))
        jobs.append(chase(1 * G, "pin:remote", samples=5, remote=True, rep=i))
        jobs.append(chase(1 * G, "pin:unpinned", samples=5, place="unpinned", rep=i))
        jobs.append(bw(16, 64 * M, "pin:bw-pinned", samples=5, rep=i))
        jobs.append(bw(16, 64 * M, "pin:bw-unpinned", place="unpinned", samples=5, rep=i))
    jobs.append(chase(1 * G, "prefetch:random", counters="mem"))
    jobs.append(chase(1 * G, "prefetch:seq", pattern="seq", counters="mem"))
    for p in (3, 17, 67, 257):
        jobs.append(chase(1 * G, f"prefetch:fakerand", pattern="fakerand", param=p, counters="mem"))
    return jobs


EXPERIMENTS = {"calib": jobs_calib, "latency": jobs_latency, "workingset": jobs_workingset,
               "pattern": jobs_pattern, "rwmix": jobs_rwmix, "intensity": jobs_intensity,
               "cachemiss": jobs_cachemiss, "tlb": jobs_tlb, "pitfall": jobs_pitfall}
DEFAULTS = {  # rounds, samples, min_ms (chase/kern), ms (bw/loadlat window)
    "calib": (3, 11, 20, 200), "latency": (3, 11, 20, 200), "workingset": (2, 7, 20, 50),
    "pattern": (3, 7, 20, 200), "rwmix": (3, 7, 20, 200), "intensity": (2, 5, 20, 200),
    "cachemiss": (2, 7, 20, 200), "tlb": (2, 7, 20, 200), "pitfall": (1, 7, 20, 200),
}


# --------------------------------------------------------------------- runner
class Placement:
    def __init__(self, core, node):
        self.core = core
        self.node = node
        self.node_cpus = pick_core.node_order(node)
        other = [n for n in pick_core.nodes() if n != pick_core.cpu_node_of(core)]
        self.remote_cpu = pick_core.node_order(other[0])[-1] if other else None
        self.ccd = next(g for g in pick_core.ccds() if core in g)


def job_cmd(job, pl, samples, min_ms, ms):
    a = dict(job["args"])
    a.setdefault("samples", job.get("samples", samples))
    if job["mode"] in ("chase", "kern"):
        a.setdefault("min_ms", min_ms)
    else:
        a.setdefault("ms", ms)
    place = job["place"]
    if job["mode"] in ("chase", "kern"):
        a["cpu"] = -1 if place == "unpinned" else pl.core
        if a.pop("remote", False):
            a["mem_cpu"] = pl.remote_cpu
    else:
        n = job["n"]
        if place == "unpinned":
            cpus = [-1] * n
        elif place == "core" or n == 1:
            cpus = [pl.core]
        else:
            cpus = pl.node_cpus[:n]
        a["cpus"] = ",".join(map(str, cpus))
    a.pop("remote", None)
    a.pop("rep", None)
    cmd = [str(C.MEMBENCH), job["mode"]]
    for k, v in a.items():
        if v is True:
            cmd.append("--" + k.replace("_", "-"))
        elif v is not False and v is not None:
            cmd += ["--" + k.replace("_", "-"), str(v)]
    cmd.append("--header")
    return cmd


def watched(job, pl):
    """(cpus whose activity we attribute to other users, subtract our own CPU time?)"""
    if job["place"] == "unpinned":
        return set(range(os.cpu_count())), True
    if job["place"] == "node" and job.get("n", 1) > 1:
        return set(pick_core.node_cpus(pl.node)), True
    return set(pl.ccd) - {pl.core}, False


def busy_seconds(cpus, before, after):
    hz = os.sysconf("SC_CLK_TCK")
    tot = 0
    for c in cpus:
        tot += (after[c][0] - before[c][0]) - (after[c][1] - before[c][1])
    return tot / hz


def child_cpu_seconds():
    r = resource.getrusage(resource.RUSAGE_CHILDREN)
    return r.ru_utime + r.ru_stime


def others_now(cpus, window=0.5):
    a = pick_core.cpu_times()
    time.sleep(window)
    return busy_seconds(cpus, a, pick_core.cpu_times()) / window


def wait_quiet(cpus, limit, max_wait_s=7 * 86400, why=""):
    t0 = time.time()
    while (u := others_now(cpus)) > limit:
        if time.time() - t0 > max_wait_s:
            sys.exit(f"cpus still busy (util {u:.2f}) after {max_wait_s}s; giving up")
        if time.time() - t0 < 1 or int(time.time() - t0) % 600 < 30:  # log every ~10 min
            print(f"  waiting for quiet cpus{why}: others' util {u:.2f}", flush=True)
        time.sleep(30)
    return time.time() - t0


def all_cpus_preexec():
    os.sched_setaffinity(0, set(range(os.cpu_count())))


def run_job(job, pl, samples, min_ms, ms):
    cmd = job_cmd(job, pl, samples, min_ms, ms)
    cpus, subtract = watched(job, pl)
    before, t0, c0 = pick_core.cpu_times(), time.time(), child_cpu_seconds()
    p = subprocess.run(cmd, capture_output=True, text=True,
                       preexec_fn=all_cpus_preexec if job["place"] == "unpinned" else None)
    if p.returncode:
        sys.exit(f"membench failed: {' '.join(cmd)}\n{p.stderr}")
    el = time.time() - t0
    busy = busy_seconds(cpus, before, pick_core.cpu_times())
    if subtract:
        busy -= child_cpu_seconds() - c0
    util = max(0.0, busy / el) if el >= 0.25 else float("nan")
    return pd.read_csv(io.StringIO(p.stdout)), util, cmd


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
    ap.add_argument("--core", type=int, help="override automatic idle-core choice")
    ap.add_argument("--node", type=int, help="override automatic idle-node choice")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--resume", metavar="RUN_ID",
                    help="continue an interrupted run: keep its finished rounds, run the rest")
    a = ap.parse_args()
    if a.list or not a.exp:
        for k, f in EXPERIMENTS.items():
            print(f"{k}: {len(f(False)):4d} jobs  {(f.__doc__ or '').strip().splitlines()[0]}")
        return
    exp = C.exp_id(a.exp)
    rounds, samples, min_ms, ms = DEFAULTS[exp]
    if a.quick:
        rounds, samples, min_ms, ms = 1, 3, 5, 50
    rounds = a.rounds or rounds
    samples = a.samples or samples
    jobs = EXPERIMENTS[exp](a.quick)
    multi = any(j["place"] in ("node", "unpinned") and j.get("n", 1) > 1 for j in jobs)

    subprocess.run(["make", "-s", "-C", str(C.ROOT), "all"], check=True)
    t_wait = time.time()
    while True:  # wait (up to an hour) for quiet hardware instead of measuring under load
        util = pick_core.utilization(1.0)
        nd = pick_core.pick_node(util=util, max_util=LIMIT["node"]) if a.node is None else \
            {"node": a.node, "ok": True, "util_sum": sum(util[c] for c in pick_core.node_cpus(a.node))}
        cp = pick_core.pick(nd["node"] if multi else None)
        ok = (cp["ok"] or a.core is not None) and (nd["ok"] or not multi)
        if ok or time.time() - t_wait > 7 * 86400:
            break
        print(f"waiting for quiet hardware: best CCD util {cp['ccd_util_sum']:.2f}, "
              f"node {nd['node']} util {nd['util_sum']:.2f}", flush=True)
        time.sleep(30)
    if not ok:
        sys.exit("no quiet CCD/node for a week; retry later or pass --core/--node")
    core = a.core if a.core is not None else cp["core"]
    pl = Placement(core, nd["node"])
    # keep this driver (pandas, subprocess plumbing) off the measured cpus
    busy_set = set(pl.ccd) | (set(pick_core.node_cpus(pl.node)) if multi else set())
    free = set(range(os.cpu_count())) - busy_set
    if free:
        os.sched_setaffinity(0, free)
    run_id = a.resume or datetime.now().strftime("%Y%m%d-%H%M%S") + ("-quick" if a.quick else "")
    out = C.RAW / exp
    out.mkdir(parents=True, exist_ok=True)
    meta = dict(exp=exp, run_id=run_id, quick=a.quick, rounds=rounds, samples=samples,
                min_ms=min_ms, ms=ms, core=core, ccd=pl.ccd, node=pl.node,
                node_cpus=pl.node_cpus, remote_cpu=pl.remote_cpu, host=platform.node(),
                git=git_hash(), started=datetime.now().isoformat(), n_jobs=len(jobs), jobs=jobs)
    print(f"{exp}: {len(jobs)} jobs x {rounds} rounds; core {core} (CCD {pl.ccd[0]}-{pl.ccd[-1]}), "
          f"node {pl.node}, remote cpu {pl.remote_cpu}", flush=True)
    t_start = time.time()
    flagged = retried = 0
    for r in range(rounds):
        if a.resume and (out / f"{run_id}__r{r}.csv").exists():
            print(f"  round {r + 1}/{rounds}: kept from the interrupted run", flush=True)
            meta.setdefault("rounds_resumed", []).append(r)
            continue
        order = list(range(len(jobs)))
        random.Random(1000 + r).shuffle(order)
        frames = []
        for done, ji in enumerate(order):
            job = jobs[ji]
            cpus, _ = watched(job, pl)
            lim = LIMIT[job["place"]]
            wait_quiet(cpus, lim)
            for attempt in range(1, 4):
                df, util, cmd = run_job(job, pl, samples, min_ms, ms)
                if not util > lim:  # NaN (too short to judge) counts as clean
                    break
                retried += 1
                wait_quiet(cpus, lim, why=f" (retrying job {ji}, attempt {attempt + 1})")
            flagged += util > lim
            df.insert(0, "exp", exp)
            df["tag"], df["place"] = job["tag"], job["place"]
            df["rep"] = job["args"].get("rep", 0)
            df["remote"] = bool(job["args"].get("remote", False))
            df["round"], df["job"], df["attempts"] = r, ji, attempt
            df["others_util"] = round(util, 3)
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
          f"({retried} re-measured because of other users, {flagged} still busy after 3 tries)")


if __name__ == "__main__":
    main()
