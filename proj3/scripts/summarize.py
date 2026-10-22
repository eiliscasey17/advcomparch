#!/usr/bin/env python3
"""Raw per-job rows -> one summary row per configuration.

  python scripts/summarize.py F4|all [--run RUN_ID]

Writes results/summary/<EXP>.csv: medians over rounds of every metric,
bootstrap 95% CIs for the primary ones (`<m>_lo/_hi`), Little's-Law depth
(IOPS x mean latency) and RAID member I/O per host I/O.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

KEYS = ["exp", "tag", "rw", "bs", "bs_bytes", "qd", "jobs", "qd_total", "region", "mix", "direct",
        "engine", "extra", "pattern", "phase", "segment", "file"]
PRIMARY = ["iops", "bw_MBps", "lat_mean_us", "r_p99_us", "w_p99_us"]
SKIP = {"round", "job", "attempts", "runtime", "ramp", "runtime_eff", "ramp_eff"}


def reparse(df, exp, run):
    """Re-derive every fio-parsed field from the stored raw JSON (the single
    source of truth), so parser improvements apply to finished runs too."""
    rows = []
    for _, r in df.iterrows():
        p = C.RAW / exp / run / f"j{int(r.job):03d}_r{int(r['round'])}.json"
        rows.append(C.parse_fio(json.loads(p.read_text())) if p.exists() else {})
    new = pd.DataFrame(rows, index=df.index)
    for c in new:
        df[c] = new[c]
    return df


def add_metrics(df):
    df = df.copy()
    df["little_qd"] = C.little_qd(df.iops, df.lat_mean_us)
    host = df.get("md_read_ios", 0) + df.get("md_write_ios", 0)
    if "mem_read_ios" in df:
        df["mem_reads_per_host_io"] = df.mem_read_ios / host.where(host > 0)
        df["mem_writes_per_host_io"] = df.mem_write_ios / host.where(host > 0)
    if "md_write_sectors" in df:
        # drive bytes per host byte: RAID5 RMW of a small write = 2 reads + 2 writes
        # (data + parity); a full stripe = 4/3 writes (3 data chunks + 1 parity), 0 reads
        hb = (df.md_read_sectors + df.md_write_sectors).where(lambda x: x > 0)
        df["mem_read_bytes_per_host_byte"] = df.mem_read_sectors / hb
        df["mem_write_bytes_per_host_byte"] = df.mem_write_sectors / hb
    # bytes that reached /data per byte fio transferred (ramp included); ~1 for
    # O_DIRECT, ~0 when the page cache or unwritten extents answer instead
    fio_total = df.io_bytes * (df.runtime_eff + df.ramp_eff) / df.runtime_eff
    df["md_vs_fio_bytes"] = df.md_bytes_wall / fio_total.where(df.io_bytes > 0)
    return df


def summarize(raw):
    df = add_metrics(raw)
    keys = [k for k in KEYS if k in df]
    for k in keys:
        df[k] = df[k].fillna("-") if df[k].dtype == object else df[k].fillna(-1)
    num = [c for c in df.select_dtypes("number").columns if c not in keys and c not in SKIP]
    rows = []
    for key, g in df.groupby(keys, sort=False):
        row = dict(zip(keys, key))
        for c in num:
            v = g[c].dropna()
            if len(v):
                row[c] = float(v.median())
        for c in PRIMARY:
            if c in g and g[c].notna().any():
                med, lo, hi = C.bootstrap_median_ci(g[c].values)
                row[c], row[f"{c}_lo"], row[f"{c}_hi"] = med, lo, hi
        row["samples"] = len(g)
        row["others_MBps_max"] = float(g.others_MBps.max())
        rows.append(row)
    return pd.DataFrame(rows)


def pooled_hists(raw, exp, run):
    """Pool the json+ latency histograms of every hist job over rounds ->
    long table (tag, dir, lat_us, count), so the CCDF figures can be
    regenerated from committed summaries without the raw JSON."""
    out = []
    if "hist" not in raw:
        return pd.DataFrame()
    for tag, g in raw[raw["hist"].astype(str) == "True"].groupby("tag"):
        for direction in ("read", "write"):
            acc = {}
            for _, r in g.iterrows():
                lat, cnt = C.hist_bins(C.load_json(exp, run, int(r.job), int(r["round"])), direction)
                for l_, c_ in zip(lat, cnt):
                    acc[l_] = acc.get(l_, 0) + c_
            out += [dict(tag=tag, dir=direction, lat_us=l_, count=c_) for l_, c_ in sorted(acc.items())]
    return pd.DataFrame(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("exp", nargs="+")
    ap.add_argument("--run")
    a = ap.parse_args()
    exps = sorted(p.name for p in C.RAW.iterdir() if p.is_dir()) if a.exp == ["all"] else \
        [e.upper() for e in a.exp]
    C.SUMMARY.mkdir(parents=True, exist_ok=True)
    for e in exps:
        try:
            raw, run = C.load_raw(e, a.run)
        except FileNotFoundError as err:
            print(f"{e}: {err}")
            continue
        raw["exp"] = e
        raw = reparse(raw, e, run)
        s = summarize(raw).assign(run=run)
        s.to_csv(C.SUMMARY / f"{e}.csv", index=False)
        h = pooled_hists(raw, e, run)
        if len(h):
            h.to_csv(C.SUMMARY / f"{e}_hist.csv", index=False)
        print(f"{e}: {len(raw)} jobs -> {len(s)} rows (run {run})")


if __name__ == "__main__":
    main()
