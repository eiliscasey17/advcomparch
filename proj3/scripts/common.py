"""Shared definitions: paths, the storage stack under test, fio JSON parsing,
diskstats, Little's Law / knee / percentile helpers, statistics.

Every derived number in the plots is computed here and nowhere else, so
tests/test_analysis.py can pin the formulas down. knee / load_raw / bootstrap
are carried over from proj2.
"""
import json
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
RAW, SUMMARY, PLOTS, ENV = (RESULTS / d for d in ("raw", "summary", "plots", "env"))
# Raw results behind the committed figures (per-job CSV rows, every fio JSON,
# the iops logs), gzipped and tracked in git; the full local raw/ tree (with
# quick and interrupted runs) is gitignored.
RAW_COMMITTED = RESULTS / "raw_runs"
FIO_JOBS = RESULTS / "fio_jobs"          # job files reconstructed from those JSONs

# Experiment IDs, in the order of the assignment's required list. The old
# numeric IDs (F0..F8) are still accepted as aliases everywhere.
EXPERIMENT_IDS = ["calib", "qd1", "blocksize", "rwmix", "qdsweep", "tail", "lba", "state",
                  "pitfall"]
LEGACY_IDS = {f"F{i}": e for i, e in enumerate(EXPERIMENT_IDS)}


def exp_id(name):
    """Canonical experiment ID for a new-style name or a legacy F<n> alias."""
    name = name.strip()
    e = LEGACY_IDS.get(name.upper(), name.lower())
    if e not in EXPERIMENT_IDS:
        raise ValueError(f"unknown experiment {name!r}; known: {', '.join(EXPERIMENT_IDS)}")
    return e
FIO = Path(os.environ.get("FIO", Path.home() / "miniconda3/envs/advcomparch/bin/fio"))

KiB, MiB, GiB = 1 << 10, 1 << 20, 1 << 30

# ---------------------------------------------------------- system under test
# Test files live OUTSIDE the repo on /data (XFS on mdadm RAID5 over 4 NVMe).
SCRATCH = Path(os.environ.get("FIO_SCRATCH", "/data/home/caseye2/fio_proj3"))
DATAFILE = SCRATCH / "data.dat"          # prefilled, incompressible, fully written
DATA_SIZE = 224 * GiB                    # budget agreed with the user: <= 256 GiB in total
MAIN_REGION = 64 * GiB                   # region used unless an experiment sweeps it
MD = "md124"                             # /data


def md_members(md=MD):
    """Member drives of the array, read from sysfs: NVMe names are not stable
    across reboots (after the 2026-10-22 restart nvme1n1 became nvme2n1)."""
    p = Path(f"/sys/block/{md}/slaves")
    if not p.exists():
        return ["nvme2n1", "nvme3n1", "nvme4n1", "nvme5n1"]
    return sorted(re.sub(r"p\d+$", "", s.name) for s in p.iterdir())


MEMBERS = md_members()                   # 4 x Micron MTFDKCC6T4TGQ, 6.4 TB
RAID_CHUNK = 512 * KiB
RAID_DATA_DISKS = 3                      # RAID5 over 4: one chunk of parity per stripe
FULL_STRIPE = RAID_CHUNK * RAID_DATA_DISKS  # 1.5 MiB
NVME_NODE = 0                            # NUMA node the member NVMe controllers sit on

PCTS = [50, 90, 95, 99, 99.9, 99.99]
PCT_COLS = [f"p{p:g}_us" for p in PCTS]


def pct_col(p):
    return f"p{p:g}_us"


# ------------------------------------------------------------- fio parsing --
def _dir_row(d, prefix=""):
    """Metrics of one direction ('read'/'write') of a fio job, latencies in us."""
    clat, lat, slat = d["clat_ns"], d["lat_ns"], d["slat_ns"]
    row = {"iops": d["iops"], "bw_MBps": d["bw_bytes"] / 1e6, "io_bytes": d["io_bytes"],
           "total_ios": d["total_ios"], "lat_mean_us": lat["mean"] / 1e3,
           "clat_mean_us": clat["mean"] / 1e3, "slat_mean_us": slat["mean"] / 1e3,
           "lat_max_us": lat["max"] / 1e3, "lat_stddev_us": lat["stddev"] / 1e3}
    pc = clat.get("percentile", {})
    for p in PCTS:
        key = f"{p:.6f}"
        row[pct_col(p)] = pc[key] / 1e3 if key in pc else np.nan
    return {prefix + k: v for k, v in row.items()}


def effective_qd_mode(levels):
    """fio's iodepth_level histogram ({'1':..,'2':..,'4':..,..,'>=64':..}
    in % of time) -> the depth bucket where the job spent most of its time."""
    k = max(levels, key=lambda b: levels[b])
    return 64 if k == ">=64" else int(k)


def parse_fio(doc):
    """One fio JSON document (group_reporting=1 -> one job entry) -> dict with
    per-direction metrics (r_*, w_*) plus combined totals. Combined latency
    is the IO-weighted mean of the two directions."""
    j = doc["jobs"][0]
    out = {"elapsed_s": j["elapsed"], "runtime_ms": j["job_runtime"],
           "usr_cpu": j["usr_cpu"], "sys_cpu": j["sys_cpu"],
           "qd_mode": effective_qd_mode(j["iodepth_level"]),
           "qd_at_mode_pct": max(j["iodepth_level"].values())}
    r, w = j["read"], j["write"]
    if r["total_ios"]:
        out.update(_dir_row(r, "r_"))
    if w["total_ios"]:
        out.update(_dir_row(w, "w_"))
    ios = r["total_ios"] + w["total_ios"]
    out["iops"] = r["iops"] + w["iops"]
    out["bw_MBps"] = (r["bw_bytes"] + w["bw_bytes"]) / 1e6
    out["io_bytes"] = r["io_bytes"] + w["io_bytes"]
    out["lat_mean_us"] = ((r["lat_ns"]["mean"] * r["total_ios"] + w["lat_ns"]["mean"] *
                           w["total_ios"]) / ios / 1e3) if ios else np.nan
    # device-level view fio itself recorded (md + member drives), same window
    mem_util = []
    for du in doc.get("disk_util", []):
        name = du["name"]
        if name != MD:
            # md's members, as fio itself found them: in_queue is diskstats'
            # weighted ms, so sum / wall ms = requests in flight at the drives
            out["mem_in_queue_ms"] = out.get("mem_in_queue_ms", 0) + du["in_queue"]
            mem_util.append(du["util"])
        else:
            out["md_read_ios"], out["md_write_ios"] = du["read_ios"], du["write_ios"]
            out["md_read_sectors"], out["md_write_sectors"] = du["read_sectors"], du["write_sectors"]
            out["md_util"] = du["util"]
            out["mem_read_ios"] = du.get("aggr_read_ios", np.nan) * len(MEMBERS)
            out["mem_write_ios"] = du.get("aggr_write_ios", np.nan) * len(MEMBERS)
            out["mem_read_sectors"] = du.get("aggr_read_sectors", np.nan) * len(MEMBERS)
            out["mem_write_sectors"] = du.get("aggr_write_sectors", np.nan) * len(MEMBERS)
            out["mem_util"] = du.get("aggr_util", np.nan)
    if mem_util:
        out["mem_util_mean"] = float(np.mean(mem_util))
    return out


def hist_bins(doc, direction):
    """json+ completion-latency histogram -> (sorted latency_us, counts)."""
    bins = doc["jobs"][0][direction]["clat_ns"].get("bins", {})
    if not bins:
        return np.array([]), np.array([])
    lat = np.array([int(k) for k in bins], float) / 1e3
    cnt = np.array(list(bins.values()), float)
    o = np.argsort(lat)
    return lat[o], cnt[o]


def ccdf(lat_us, counts):
    """P(latency > x) at each histogram value x."""
    c = np.cumsum(counts)
    return lat_us, 1 - c / c[-1]


def percentile_from_hist(lat_us, counts, p):
    c = np.cumsum(counts) / counts.sum()
    return float(lat_us[np.searchsorted(c, p / 100.0)])


# --------------------------------------------------------- Little / knee ----
def little_qd(iops, lat_us):
    """Little's Law L = lambda * W: requests in flight = IOPS * latency."""
    return np.asarray(iops, float) * np.asarray(lat_us, float) / 1e6


def little_iops(qd, lat_us):
    return np.asarray(qd, float) / (np.asarray(lat_us, float) / 1e6)


def knee(x, y):
    """Kneedle-style knee of a rising convex curve y(x) (latency vs
    throughput: flat, then climbing): in coordinates normalized to [0, 1],
    the point lying farthest below the chord from the first to the last
    point. Returns the index into the original (unsorted) arrays."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    o = np.argsort(x)
    xs, ys = x[o], y[o]
    if len(xs) < 3:
        return int(o[-1])
    xn = (xs - xs[0]) / (xs[-1] - xs[0] or 1)
    yn = (ys - ys[0]) / (ys[-1] - ys[0] or 1)
    return int(o[int(np.argmax(xn - yn))])


def knee_by_gain(qd, thr, frac=0.9):
    """Alternative knee for a QD sweep: smallest QD reaching `frac` of the
    maximum throughput (beyond it, more depth mostly adds latency)."""
    qd, thr = np.asarray(qd, float), np.asarray(thr, float)
    o = np.argsort(qd)
    ok = thr[o] >= frac * thr.max()
    return int(o[int(np.argmax(ok))])


# ------------------------------------------------------------- diskstats ----
def diskstats(names=None):
    """{dev: (reads, read_sectors, writes, write_sectors, io_ticks_ms,
    weighted_ms)}; weighted_ms = sum over requests of time in queue+service,
    so delta(weighted_ms) / delta(wall ms) = mean requests in flight there."""
    out = {}
    for line in Path("/proc/diskstats").read_text().splitlines():
        f = line.split()
        if names is None or f[2] in names:
            out[f[2]] = tuple(int(x) for x in (f[3], f[5], f[7], f[9], f[12], f[13]))
    return out


def diskstats_delta(a, b):
    return {k: tuple(y - x for x, y in zip(a[k], b[k])) for k in a if k in b}


# ------------------------------------------------------------- raw data ----
def _run_dirs(exp):
    """Local raw dir first (full data, gitignored), then the committed copy."""
    return [RAW / exp, RAW_COMMITTED / exp]


def raw_file(exp, run, name):
    """Path of one raw file of a run (e.g. 'j003_r1.json'): the local copy if
    present, else the gzipped committed one. Raises if neither exists."""
    exp = exp_id(exp)
    for d in _run_dirs(exp):
        for p in (d / run / name, d / run / f"{name}.gz"):
            if p.exists():
                return p
    raise FileNotFoundError(f"{exp}/{run}/{name}")


def raw_glob(exp, run, pattern):
    """Raw files of a run matching a glob (local copy preferred), sorted by
    their uncompressed name."""
    exp = exp_id(exp)
    for d in _run_dirs(exp):
        hits = sorted((d / run).glob(pattern)) or sorted((d / run).glob(pattern + ".gz"))
        if hits:
            return hits
    return []


def read_text(p):
    import gzip
    p = Path(p)
    return gzip.open(p, "rt").read() if p.suffix == ".gz" else p.read_text()


def load_raw(exp, run=None):
    """Concatenate the per-job CSV rows of one experiment run.

    Run choice: `run` if given, else the ID pinned in results/runs.json (the
    run behind the committed figures), else the latest completed full run.
    Files come from the local raw/ tree if present, otherwise from the
    gzipped copies committed under results/raw_runs/. Only completed runs
    (those with a .meta.json, written last) are ever picked up."""
    exp = exp_id(exp)
    found = {}  # run id -> {round: file}
    for d, pattern in ((RAW_COMMITTED / exp, "*__r*.csv.gz"), (RAW / exp, "*__r*.csv")):
        for f in sorted(d.glob(pattern)):
            r = f.name.split("__")[0]
            if (d / f"{r}.meta.json").exists():
                found.setdefault(r, {})[f.name.split("__")[1].split(".")[0]] = f  # local wins
    if not found:
        raise FileNotFoundError(f"no completed raw run for {exp} in {RAW / exp} or {RAW_COMMITTED / exp}")
    pins = RESULTS / "runs.json"
    if run is None and pins.exists():
        run = json.loads(pins.read_text()).get(exp)
    if run is None:
        full = sorted(r for r in found if not r.endswith("-quick"))
        run = (full or sorted(found))[-1]
    if run not in found:
        raise FileNotFoundError(f"run {run} of {exp} not found")
    df = pd.concat([pd.read_csv(found[run][k]) for k in sorted(found[run])], ignore_index=True)
    df["exp"] = exp  # rows recorded before the rename carry the old F<n> label
    return df, run


def load_json(exp, run, job, rnd):
    return json.loads(read_text(raw_file(exp, run, f"j{job:03d}_r{rnd}.json")))


def bootstrap_median_ci(x, n_boot=2000, alpha=0.05, rng=None):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if len(x) == 0:
        return np.nan, np.nan, np.nan
    rng = rng or np.random.default_rng(0)
    meds = np.median(rng.choice(x, (n_boot, len(x)), replace=True), axis=1)
    return float(np.median(x)), float(np.quantile(meds, alpha / 2)), float(np.quantile(meds, 1 - alpha / 2))
