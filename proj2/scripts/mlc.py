#!/usr/bin/env python3
"""Intel MLC cross-check of membench (optional; MLC is not redistributable).

Install: accept the license at
  https://www.intel.com/content/www/us/en/download/736633/intel-memory-latency-checker-intel-mlc.html
and unpack the tgz into proj2/tools/mlc/ (so tools/mlc/Linux/mlc exists; chmod +x it).

  python scripts/mlc.py           run the cross-check (same idle-hardware gate as run.py)

Runs, pinned to the same idle core / node that run.py would pick:
  idle latency at L1/L2/L3/DRAM buffer sizes (random access, -r)
  latency matrix (local vs remote node)
  peak injection bandwidth on the node (all reads, 3:1, 2:1, 1:1, stream triad)
  loaded latency sweep on the node (MLC's injection-delay table)
Raw output -> results/raw/MLC/<run_id>/*.txt, parsed -> results/summary/MLC.csv
(MLC prints MB/sec; gbs = MB/sec / 1000).
MLC is built for Intel CPUs; without root it cannot disable prefetchers, and
v3.13 refuses to run at all unless hugetlbfs 2 MiB pages are reserved
(root-only), which is the case on this machine: the script then records that
in results/env/mlc_status.txt instead of producing numbers.
"""
import os
import re
import subprocess
import sys
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
import common as C  # noqa: E402
import pick_core  # noqa: E402
import run as R  # noqa: E402


def mlc(args, out, timeout=900):
    # -e: never try to change prefetcher MSRs (no root here; MLC would fall back anyway)
    args = [*args, "-e"]
    p = subprocess.run([str(C.MLC), *args], capture_output=True, text=True, timeout=timeout)
    (out / ("_".join(a.strip("-").replace("/", "") for a in args)[:80] + ".txt")).write_text(
        f"$ mlc {' '.join(args)}\n# exit {p.returncode}\n{p.stdout}\n{p.stderr}")
    return p.returncode, p.stdout + p.stderr


def parse_idle(text):
    m = re.search(r"\(\s*([\d.]+)\s*ns\)", text)
    return float(m.group(1)) if m else float("nan")


def main():
    if not C.MLC.exists():
        print(f"MLC not installed at {C.MLC}; see this script's docstring. Skipping cross-check.")
        return 0
    cp = pick_core.pick()
    nd = pick_core.pick_node()
    if not (cp["ok"] and nd["ok"]):
        sys.exit("hardware busy; retry later")
    core, node = cp["core"], nd["node"]
    gens = pick_core.node_order(node)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = C.RAW / "MLC" / run_id
    out.mkdir(parents=True, exist_ok=True)
    rc, txt = mlc(["--idle_latency", f"-c{core}", "-b1g", "-r"], out)
    if "Hugepages need to be allocated" in txt:
        # MLC v3.13 refuses to run without >= 1000 reserved hugetlbfs 2 MiB pages
        # per node (no flag skips the check), and reserving them needs root.
        msg = (f"{run_id}: Intel MLC v3.13 refused to run (exit {rc}): it requires reserved "
               "hugetlbfs pages (echo 4000 > /proc/sys/vm/nr_hugepages, root only). "
               "Cross-check not possible on this machine without admin help; raw output in "
               f"results/raw/MLC/{run_id}/.\n")
        (C.ENV / "mlc_status.txt").write_text(msg)
        print(msg, end="")
        return 0
    rows = []
    for lv, size in R.LEVELS.items():
        rc, txt = mlc(["--idle_latency", f"-c{core}", f"-b{size // C.KiB}k", "-r"], out)
        rows.append(dict(test="idle_latency", level=lv, size=size, rc=rc, lat_ns=parse_idle(txt)))
    rc, txt = mlc(["--latency_matrix"], out)
    rows.append(dict(test="latency_matrix", rc=rc, raw=" ".join(txt.split()[-12:])))
    klist = ",".join(map(str, gens))
    rc, txt = mlc(["--peak_injection_bandwidth", f"-k{klist}"], out)
    for name, val in re.findall(r"^(ALL Reads|3:1 Reads-Writes|2:1 Reads-Writes|1:1 Reads-Writes|"
                                r"Stream-triad like)\s*:\s*([\d.]+)", txt, re.M):
        rows.append(dict(test="peak_bw", mix=name, rc=rc, gbs=float(val) / 1000))
    rc, txt = mlc(["--loaded_latency", f"-c{gens[0]}", f"-k{','.join(map(str, gens[1:]))}"], out)
    for d, lat, bwv in re.findall(r"^\s*(\d{5})\s+([\d.]+)\s+([\d.]+)", txt, re.M):
        rows.append(dict(test="loaded_latency", delay=int(d), rc=rc, lat_ns=float(lat),
                         gbs=float(bwv) / 1000))
    df = pd.DataFrame(rows).assign(run=run_id, core=core, node=node)
    C.SUMMARY.mkdir(parents=True, exist_ok=True)
    df.to_csv(C.SUMMARY / "MLC.csv", index=False)
    print(df.to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
