#!/usr/bin/env python3
"""Raw samples -> one summary row per configuration.

  python scripts/summarize.py latency|all [--run RUN_ID]    (old IDs like E1 also work)

Writes results/summary/<EXP>.csv: medians of every derived metric; bootstrap
95% CIs of the median (`<m>_lo`, `<m>_hi`, the error bars in the figures) for
the primary metrics (plus their CV) and for the clock, Little's-Law lines in
flight, the RFO traffic model and every per-load / per-element PMU count;
sample/round counts; and the worst other-user activity seen
(`others_util_max`).
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

KEYS = ["exp", "mode", "tag", "place", "remote", "size", "pattern", "stride", "param", "chains",
        "pages", "gens", "rw_r", "rw_w", "nt", "gran", "order", "delay", "kernel", "n",
        "gstride", "footprint"]
PRIMARY = ["ns_per_load", "ns_per_step", "gbs_useful", "gbs_lines", "gbs_total", "lat_ns",
           "ns_per_elem"]
CI_EXTRA = ["loads_per_ns", "ghz", "lat_ghz", "gbs_traffic_model", "lines_in_flight"]
SKIP = {"sample", "round", "job", "attempts", "rep", "cpu", "cpu_end", "cpu0", "others_util"}


def summarize(raw):
    df = C.add_metrics(raw)
    keys = [k for k in KEYS if k in df]
    for k in keys:  # NaN keys would drop rows in groupby
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
                v = g[c].dropna()
                row[f"{c}_cv"] = float(v.std() / v.mean()) if len(v) > 1 and v.mean() else np.nan
        for c in CI_EXTRA + [c for c in num if c.endswith(("_per_load", "_per_elem"))]:
            if c not in PRIMARY and c in g and g[c].notna().any():
                med, row[f"{c}_lo"], row[f"{c}_hi"] = C.bootstrap_median_ci(g[c].values)
        row["samples"] = len(g)
        row["rounds"] = g["round"].nunique() if "round" in g else 1
        row["others_util_max"] = float(g.others_util.max()) if "others_util" in g else np.nan
        row["regime"] = C.regime(row["size"]) if row.get("mode") == "chase" else (
            C.regime(row["footprint"]) if row.get("mode") == "kern" else "")
        rows.append(row)
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("exp", nargs="+")
    ap.add_argument("--run")
    a = ap.parse_args()
    have = {p.name for d in (C.RAW, C.RAW_COMMITTED) if d.exists() for p in d.iterdir() if p.is_dir()}
    exps = [e for e in C.EXPERIMENT_IDS if e in have] if a.exp == ["all"] else \
        [C.exp_id(e) for e in a.exp]
    C.SUMMARY.mkdir(parents=True, exist_ok=True)
    for e in exps:
        try:
            raw, run = C.load_raw(e, a.run)
        except FileNotFoundError as err:
            print(f"{e}: {err}")
            continue
        s = summarize(raw).assign(run=run)
        s.to_csv(C.SUMMARY / f"{e}.csv", index=False)
        print(f"{e}: {len(raw)} samples -> {len(s)} rows (run {run})")


if __name__ == "__main__":
    main()
