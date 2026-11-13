#!/usr/bin/env python3
"""Collapse raw per-sample rows into one row per measured configuration.

  python scripts/summarize.py simd [--run RUN_ID]    -> results/summary/simd.csv
  python scripts/summarize.py all                    (old IDs like E1 also work)

Statistics are over all samples of all rounds (rounds ran in shuffled order).
Columns: medians of time/throughput/cycle metrics; p10/p90 of GFLOP/s; a
bootstrap 95% CI of the median (`<metric>_lo`, `<metric>_hi`) for GFLOP/s,
GiB/s, cycles/element and ns/call (these are the error bars in the figures);
coefficient of variation of time per call; and per-element medians of every
PMU event that was collected.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

KEYS = ["exp", "build", "compiler", "config", "kernel", "dtype", "n", "span", "offset_x",
        "offset_y", "offset_z", "pattern", "gstride", "tag", "serialize"]
MED = ["ns_per_call", "ns_per_elem", "gflops", "gibs", "cyc_per_elem", "ghz", "ipc",
       "flops_per_elem_pmu", "footprint"]
CI = ["gflops", "gibs", "cyc_per_elem", "ns_per_call"]  # metrics that get a bootstrap CI
EVENTS = ["instructions", "fp_scalar", "fp_p128", "fp_p256", "fp_p512", "l1d_miss",
          "l2pf_hit_l3", "l2pf_miss_l3", "fill_dram", "misal_64", "misal_4k"]


def summarize(df):
    df = C.add_metrics(df)
    for col, default in (("tag", ""), ("serialize", False)):
        if col not in df:  # all-empty columns may have been dropped upstream
            df[col] = default
    df["serialize"] = df.serialize.fillna(False).astype(bool)
    df["tag"] = df.tag.fillna("")
    rows = []
    for key, g in df.groupby(KEYS, dropna=False, sort=True):
        r = dict(zip(KEYS, key))
        for m in MED:
            if m in g:
                r[m] = float(np.nanmedian(g[m])) if g[m].notna().any() else np.nan
        r["gflops_p10"], r["gflops_p90"] = np.nanquantile(g.gflops, [0.1, 0.9])
        for m in CI:
            if m in g and g[m].notna().any():
                _, r[f"{m}_lo"], r[f"{m}_hi"] = C.bootstrap_median_ci(g[m])
        t = g.ns_per_call
        r["cv"] = float(t.std() / t.mean()) if len(t) > 1 else np.nan
        r["samples"] = len(g)
        r["rounds"] = g["round"].nunique()
        r["neighbor_util_max"] = g.neighbor_util.max()
        for e in EVENTS:
            if e in g and g[e].notna().any():
                r[f"{e}_per_elem"] = float(np.nanmedian(g[e] / g.elems))
        # mean core frequency is per sample; multiplexing should never happen
        if "mux" in g:
            r["mux_min"] = g.mux.min()
        r["regime"] = C.regime(r["footprint"])
        rows.append(r)
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("exp")
    ap.add_argument("--run")
    a = ap.parse_args()
    have = {p.name for d in (C.RAW, C.RAW_COMMITTED) if d.exists() for p in d.iterdir() if p.is_dir()}
    exps = [e for e in C.EXPERIMENT_IDS if e in have] if a.exp == "all" else [C.exp_id(a.exp)]
    C.SUMMARY.mkdir(parents=True, exist_ok=True)
    for e in exps:
        if e == "calib":
            raw, run = C.load_raw(e, a.run)
            peak = raw[raw.build == "calib"].dropna(axis=1, how="all")
            peak.groupby(["test", "instr", "dtype", "width_bits", "lanes"], as_index=False).agg(
                per_cycle=("per_cycle", "median"), ghz=("ghz", "median")).to_csv(
                C.SUMMARY / "calib_peak.csv", index=False)
            al = raw[raw.build == "calib_align"].dropna(axis=1, how="all")
            if len(al):
                al.groupby(["op", "bits", "offset"], as_index=False)[
                    ["cyc_per_insn", "misal64_per_insn"]].median().to_csv(
                    C.SUMMARY / "calib_align.csv", index=False)
            bw = raw[~raw.build.isin(["calib", "calib_align"])].dropna(axis=1, how="all")
            summarize(bw).assign(run=run).to_csv(C.SUMMARY / "calib_bw.csv", index=False)
            print(f"calib ({run}) -> calib_peak.csv, calib_align.csv, calib_bw.csv")
            continue
        raw, run = C.load_raw(e, a.run)
        s = summarize(raw).assign(run=run)
        s.to_csv(C.SUMMARY / f"{e}.csv", index=False)
        noisy = (s.cv > 0.05).sum()
        print(f"{e} ({run}): {len(s)} configurations, {noisy} with CV > 5%  -> {e}.csv")


if __name__ == "__main__":
    main()
