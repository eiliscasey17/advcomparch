"""Shared definitions: paths, machine constants, derived metrics, statistics,
Little's Law / AMAT / knee helpers.

Every derived number in the plots (ns/load, GB/s, outstanding misses, AMAT,
knee) is computed here and nowhere else, so tests can pin the formulas down
(tests/test_analysis.py). Statistics helpers are copied from proj1.
"""
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
MEMBENCH = BUILD / "membench"
RESULTS = ROOT / "results"
RAW, SUMMARY, PLOTS, ENV = (RESULTS / d for d in ("raw", "summary", "plots", "env"))
MLC = ROOT / "tools" / "mlc" / "Linux" / "mlc"

KiB, MiB, GiB = 1 << 10, 1 << 20, 1 << 30
LINE = 64
PAGE_4K, PAGE_2M = 4 * KiB, 2 * MiB

# AMD EPYC 9334 (Zen 4). Verified against sysfs and `membench info` (cpuid)
# by tests/test_analysis.py.
CACHE = {"L1": 32 * KiB, "L2": 1 * MiB, "L3": 32 * MiB}  # per core, per core, per CCD
DTLB = {"L1": 72, "L2": 3072}                             # entries, any page size
TLB_REACH = {("L1", "4k"): 72 * PAGE_4K, ("L2", "4k"): 3072 * PAGE_4K,
             ("L1", "2m"): 72 * PAGE_2M, ("L2", "2m"): 3072 * PAGE_2M}
# 12 channels x DDR5-4800 x 8 B per socket. DIMM speed is not readable
# without root; 4800 MT/s is this SKU's maximum, so this is an upper bound.
DRAM_PEAK_GBS = 12 * 4800e6 * 8 / 1e9  # 460.8 GB/s
TSC_GHZ = 2.7                           # nominal; not core cycles under boost


def footprint_sweep(lo=4 * KiB, hi=2 * GiB, per_octave=4, align=LINE):
    """Log-spaced sizes, rounded down to multiples of `align` (one line)."""
    k = int(round(np.log2(hi / lo) * per_octave))
    out = [int(lo * 2 ** (i / per_octave)) for i in range(k + 1)]
    return sorted({max(align, s - s % align) for s in out})


def regime(ws):
    if ws <= CACHE["L1"]:
        return "L1"
    if ws <= CACHE["L2"]:
        return "L2"
    if ws <= CACHE["L3"]:
        return "L3"
    return "DRAM"


# ------------------------------------------------------------- metrics --
EVENTS = ["cycles", "instructions", "l1d_miss", "l2_miss_dc", "fill_l2", "fill_ccx",
          "fill_other_ccx", "fill_dram", "l2pf_hit_l3", "l2pf_miss_l3", "dtlb_miss",
          "dtlb_l2_miss"]


def line_bytes_per_access(stride):
    """Bytes of cache lines touched per access: one whole line when accesses
    are >= a line apart, else the stride's share of a line."""
    return np.where(np.asarray(stride) >= LINE, LINE, np.asarray(stride))


def add_metrics(df):
    """Per-sample derived metrics on a raw membench DataFrame (returns a copy).
    Rows of different modes coexist; each metric is filled only where it
    applies."""
    df = df.copy()
    has = lambda c: c in df and df[c].notna().any()  # noqa: E731
    if has("cycles"):
        df["ghz"] = df.cycles / df.ns
    m = df["mode"]
    ch = m == "chase"
    if ch.any():
        df.loc[ch, "ns_per_load"] = df.ns / df.steps
        df.loc[ch, "loads_per_ns"] = df.steps / df.ns
        df.loc[ch, "gbs"] = df.steps * LINE / df.ns          # line traffic of the chase
        df.loc[ch, "ns_per_step"] = df.ns / (df.steps / df.chains)  # latency seen by each chain
        if has("cycles"):
            df.loc[ch, "cyc_per_load"] = df.cycles / df.steps
            df.loc[ch, "cyc_per_step"] = df.cycles / (df.steps / df.chains)
        for e in EVENTS[2:]:
            if has(e):
                df.loc[ch, f"{e}_per_load"] = df[e] / df.steps
    th = m.isin(["bw", "loadlat"])
    if th.any():
        acc = df.gen_accesses
        df.loc[th, "gbs_useful"] = acc * df.gran / df.ns
        df.loc[th, "gbs_lines"] = acc * line_bytes_per_access(df.stride) / df.ns
        wfrac = df.rw_w / (df.rw_r + df.rw_w)
        # DRAM-level traffic model: a normal store reads the line for
        # ownership and later writes it back (2 line transfers); a
        # non-temporal store writes it once.
        rfo = np.where(df.nt.astype(bool), 0.0, 1.0)
        df.loc[th, "gbs_traffic_model"] = df.gbs_lines * (1 + wfrac * rfo)
        lat = df.lat_steps.where(df.lat_steps > 0)
        df.loc[th, "lat_ns"] = df.ns / lat
        df.loc[th, "lat_ghz"] = df.lat_cycles.where(df.lat_cycles > 0) / df.ns
        df.loc[th, "lat_cyc"] = df.lat_cycles.where(df.lat_cycles > 0) / lat
        # total bandwidth seen by memory, including the probe's own lines
        df.loc[th, "gbs_total"] = df.gbs_lines + (lat.fillna(0) * LINE / df.ns)
        if has("cycles"):
            df.loc[th, "ghz"] = df.cycles / (df.ns * df.gens.clip(lower=1))
    kn = m == "kern"
    if kn.any():
        elems = df.reps * df.n
        df.loc[kn, "ns_per_elem"] = df.ns / elems
        if has("cycles"):
            df.loc[kn, "cyc_per_elem"] = df.cycles / elems
        for e in EVENTS[1:]:
            if has(e):
                df.loc[kn, f"{e}_per_elem"] = df[e] / elems
    return df


# ------------------------------------------------- Little's Law / AMAT --
def little_outstanding(gbs, lat_ns, line=LINE):
    """Little's Law L = lambda * W: requests in flight = (lines/ns) * ns.
    gbs in GB/s (1e9 B/s == B/ns)."""
    return np.asarray(gbs) / line * np.asarray(lat_ns)


def little_bandwidth(outstanding, lat_ns, line=LINE):
    """Bandwidth (GB/s) sustainable with `outstanding` lines in flight."""
    return np.asarray(outstanding) * line / np.asarray(lat_ns)


def amat(hit_ns, miss_rates, penalties_ns):
    """Classic AMAT recursion: t = h1 + m1*(t2 + m2*(t3 + ...)), where
    penalties_ns[i] is the access time of level i+2 and miss_rates[i] the
    local miss rate of level i+1."""
    t = 0.0
    for m, p in zip(reversed(list(miss_rates)), reversed(list(penalties_ns))):
        t = m * (p + t)
    return hit_ns + t


def amat_from_fills(loads, fills, lat_ns):
    """Serial (no-overlap) memory time per element from fill-source counts:
    every load costs the L1 latency; a load filled from level X additionally
    costs (lat_X - lat_L1). fills/lat_ns are dicts keyed L2/L3/DRAM."""
    t = loads * lat_ns["L1"]
    for lv in ("L2", "L3", "DRAM"):
        t = t + fills[lv] * (lat_ns[lv] - lat_ns["L1"])
    return t


def knee(x, y):
    """Kneedle-style knee of a rising convex curve y(x) (latency vs
    bandwidth: flat, then climbing): in coordinates normalized to [0, 1], the
    point lying farthest below the chord from the first to the last point.
    Returns the index into the original (unsorted) arrays."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    o = np.argsort(x)
    xs, ys = x[o], y[o]
    if len(xs) < 3:
        return int(o[-1])
    xn = (xs - xs[0]) / (xs[-1] - xs[0] or 1)
    yn = (ys - ys[0]) / (ys[-1] - ys[0] or 1)
    d = xn - yn  # convex rising curve: chord lies above, knee maximizes xn - yn
    return int(o[int(np.argmax(d))])


# ------------------------------------------------------------ raw data --
def load_raw(exp, run=None):
    """Concatenate raw CSVs of one experiment run (latest completed full run
    if run is None)."""
    d = RAW / exp
    files = sorted(d.glob("*.csv"))
    if not files:
        raise FileNotFoundError(f"no raw data for {exp} in {d}")
    runs = sorted({f.name.split("__")[0] for f in files
                   if (d / (f.name.split("__")[0] + ".meta.json")).exists()})
    if not runs:
        raise FileNotFoundError(f"no completed run for {exp} in {d}")
    full = [r for r in runs if not r.endswith("-quick")]
    run = run or (full or runs)[-1]
    parts = [pd.read_csv(f) for f in files if f.name.startswith(run + "__")]
    return pd.concat(parts, ignore_index=True), run


def bootstrap_median_ci(x, n_boot=2000, alpha=0.05, rng=None):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if len(x) == 0:
        return np.nan, np.nan, np.nan
    rng = rng or np.random.default_rng(0)
    meds = np.median(rng.choice(x, (n_boot, len(x)), replace=True), axis=1)
    return float(np.median(x)), float(np.quantile(meds, alpha / 2)), float(np.quantile(meds, 1 - alpha / 2))


def ratio_ci(num, den, n_boot=2000, alpha=0.05, rng=None):
    """Median(num)/median(den) with a bootstrap CI (independent resampling)."""
    num, den = np.asarray(num, float), np.asarray(den, float)
    rng = rng or np.random.default_rng(1)
    a = np.median(rng.choice(num, (n_boot, len(num))), axis=1)
    b = np.median(rng.choice(den, (n_boot, len(den))), axis=1)
    r = a / b
    return (float(np.median(num) / np.median(den)), float(np.quantile(r, alpha / 2)),
            float(np.quantile(r, 1 - alpha / 2)))
