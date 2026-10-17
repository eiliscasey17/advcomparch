"""Shared definitions: paths, machine constants, kernel metadata, metrics.

Every derived number in the plots (GFLOP/s, GiB/s, cycles/element, arithmetic
intensity, footprint) is computed here and nowhere else, so tests can pin the
formulas down (tests/test_analysis.py).
"""
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
RESULTS = ROOT / "results"
RAW, SUMMARY, PLOTS, ENV, EVIDENCE = (RESULTS / d for d in
                                      ("raw", "summary", "plots", "env", "vec_evidence"))

KiB, MiB, GiB = 1 << 10, 1 << 20, 1 << 30
# AMD EPYC 9334 (Zen 4), per core / per CCD
CACHE = {"L1": 32 * KiB, "L2": 1 * MiB, "L3": 32 * MiB}
ELEM = {"f32": 4, "f64": 8, "i32": 4}
LINE = 64


@dataclass(frozen=True)
class KMeta:
    family: str      # dot | mul | stencil | dotg | mulg | bw_read | bw_write | bw_scale | bw_triad
    stride: int      # compile-time input stride
    flops: int       # FP (or int) ops per output element
    reads: int       # input arrays streamed (excluding idx)
    writes: int      # output arrays


KERNELS = {
    "dot_simd": KMeta("dot", 1, 2, 2, 0), "dot_plain": KMeta("dot", 1, 2, 2, 0),
    "dot_acc2": KMeta("dot", 1, 2, 2, 0), "dot_acc4": KMeta("dot", 1, 2, 2, 0),
    "dot_acc8": KMeta("dot", 1, 2, 2, 0),
    "mul": KMeta("mul", 1, 1, 2, 1), "mul_aligned": KMeta("mul", 1, 1, 2, 1),
    "mul_pred": KMeta("mul", 1, 1, 2, 1),
    "stencil": KMeta("stencil", 1, 5, 1, 1),
    "dot_s2": KMeta("dot", 2, 2, 2, 0), "dot_s4": KMeta("dot", 4, 2, 2, 0),
    "dot_s8": KMeta("dot", 8, 2, 2, 0),
    "mul_s2": KMeta("mul", 2, 1, 2, 1), "mul_s4": KMeta("mul", 4, 1, 2, 1),
    "mul_s8": KMeta("mul", 8, 1, 2, 1),
    "dot_gather": KMeta("dotg", 1, 2, 2, 0), "mul_gather": KMeta("mulg", 1, 1, 2, 1),
    "bw_read": KMeta("bw_read", 1, 1, 1, 0), "bw_write": KMeta("bw_write", 1, 0, 0, 1),
    "bw_scale": KMeta("bw_scale", 1, 1, 1, 1), "bw_triad": KMeta("bw_triad", 1, 2, 2, 1),
}


def elements(kernel, n):
    """Output elements computed per call (stencil skips the two boundaries)."""
    return max(n - 2, 0) if KERNELS[kernel].family == "stencil" else n


def useful_bytes_per_elem(kernel, dtype, write_allocate=False):
    """Compulsory bytes moved per element: every element of every array that
    the kernel actually uses, read or written once (stencil neighbours reuse
    x). idx (int32) counts for gathers. write_allocate adds the read-for-
    ownership of each written line (no non-temporal stores are generated)."""
    m, es = KERNELS[kernel], ELEM[dtype]
    b = (m.reads + m.writes) * es
    if m.family in ("dotg", "mulg"):
        b += 4
    if write_allocate:
        b += m.writes * es
    return b


def footprint_bytes(kernel, dtype, n, gstride=1, pattern="identity"):
    """Total bytes of all arrays allocated/addressed by one call (the working
    set used for cache-regime classification)."""
    m, es = KERNELS[kernel], ELEM[dtype]
    if m.family in ("dotg", "mulg"):
        span = n * (gstride if pattern == "stride" else 1)
        return span * es + n * es + n * 4 + (n * es if m.family == "mulg" else 0)
    span = n * m.stride
    return m.reads * span * es + m.writes * n * es


def n_for_footprint(kernel, dtype, target, gstride=1, pattern="identity", multiple=64):
    """Largest n (a multiple of `multiple`) whose footprint <= target."""
    per = footprint_bytes(kernel, dtype, 1, gstride, pattern)
    n = int(target // per)
    return max(multiple, n - n % multiple)


def footprint_sweep(lo=8 * KiB, hi=1 * GiB, per_octave=4):
    k = int(round(np.log2(hi / lo) * per_octave))
    return [int(lo * 2 ** (i / per_octave)) for i in range(k + 1)]


def regime(fp):
    if fp <= CACHE["L1"]:
        return "L1"
    if fp <= CACHE["L2"]:
        return "L2"
    if fp <= CACHE["L3"]:
        return "L3"
    return "DRAM"


def arithmetic_intensity(kernel, dtype, write_allocate=False):
    """FLOP per compulsory byte."""
    return KERNELS[kernel].flops / useful_bytes_per_elem(kernel, dtype, write_allocate)


def add_metrics(df):
    """Per-sample derived metrics on a raw bench DataFrame (in place + return)."""
    df = df.copy()
    el = np.array([elements(k, n) for k, n in zip(df.kernel, df.n)], dtype=float)
    df["elems"] = el * df.reps
    df["ns_per_call"] = df.ns / df.reps
    df["ns_per_elem"] = df.ns / df.elems
    fl = np.array([KERNELS[k].flops for k in df.kernel], dtype=float)
    ub = np.array([useful_bytes_per_elem(k, d) for k, d in zip(df.kernel, df.dtype)], float)
    df["gflops"] = fl * df.elems / df.ns            # FLOP/ns == GFLOP/s
    df["gibs"] = ub * df.elems / df.ns * 1e9 / GiB  # compulsory GiB/s
    df["footprint"] = [footprint_bytes(k, d, n, g if g else 1, p if p != "-" else "identity")
                       for k, d, n, g, p in zip(df.kernel, df.dtype, df.n, df.gstride, df.pattern)]
    if "cycles" in df and df.cycles.notna().any():
        df["cyc_per_elem"] = df.cycles / df.elems
        df["ghz"] = df.cycles / df.ns
    if "instructions" in df and df.instructions.notna().any():
        df["ipc"] = df.instructions / df.cycles
    if "fp_flops" in df and df.fp_flops.notna().any():
        df["flops_per_elem_pmu"] = df.fp_flops / df.elems
    return df


def load_raw(exp, run=None):
    """Concatenate raw CSVs of one experiment run (latest if run is None)."""
    d = RAW / exp
    files = sorted(d.glob("*.csv"))
    if not files:
        raise FileNotFoundError(f"no raw data for {exp} in {d}")
    runs = sorted({f.name.split("__")[0] for f in files})
    full = [r for r in runs if not r.endswith("-quick")]
    run = run or (full or runs)[-1]  # latest full run; quick runs only as fallback
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
