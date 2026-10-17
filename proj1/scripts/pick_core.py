#!/usr/bin/env python3
"""Pick an idle core for single-threaded benchmarking on this shared machine.

Samples /proc/stat over a short window, groups CPUs by shared L3 (CCD), and
returns a core on the quietest CCD of NUMA node 0. A busy neighbour on the
same CCD shares our L3 and memory path, so the *whole CCD* must be quiet.

  python scripts/pick_core.py            -> prints the core id
  python scripts/pick_core.py --json     -> core + per-CCD utilization
"""
import argparse
import json
import sys
import time
from pathlib import Path


def cpu_times():
    out = {}
    for line in Path("/proc/stat").read_text().splitlines():
        parts = line.split()
        if parts[0].startswith("cpu") and parts[0] != "cpu":
            vals = list(map(int, parts[1:]))
            idle = vals[3] + vals[4]  # idle + iowait
            out[int(parts[0][3:])] = (sum(vals), idle)
    return out


def utilization(window=1.0):
    a = cpu_times()
    time.sleep(window)
    b = cpu_times()
    util = {}
    for c in a:
        dt = b[c][0] - a[c][0]
        di = b[c][1] - a[c][1]
        util[c] = 0.0 if dt <= 0 else 1.0 - di / dt
    return util


def ccds():
    """Map of frozenset(cpus sharing L3) -> sorted list."""
    groups = {}
    for d in sorted(Path("/sys/devices/system/cpu").glob("cpu[0-9]*")):
        f = d / "cache/index3/shared_cpu_list"
        if not f.exists():
            continue
        groups[f.read_text().strip()] = None
    out = []
    for spec in groups:
        cpus = []
        for part in spec.split(","):
            lo, _, hi = part.partition("-")
            cpus.extend(range(int(lo), int(hi or lo) + 1))
        out.append(sorted(cpus))
    return sorted(out)


def node_cpus(node=0):
    spec = Path(f"/sys/devices/system/node/node{node}/cpulist").read_text().strip()
    cpus = set()
    for part in spec.split(","):
        lo, _, hi = part.partition("-")
        cpus.update(range(int(lo), int(hi or lo) + 1))
    return cpus


def pick(node=0, window=1.0, max_ccd_util=0.15):
    util = utilization(window)
    allowed = node_cpus(node)
    report = []
    for g in ccds():
        if not set(g) <= allowed:
            continue
        report.append({"cpus": g, "util_sum": sum(util[c] for c in g),
                       "util_max": max(util[c] for c in g)})
    report.sort(key=lambda r: r["util_sum"])
    best = report[0]
    # avoid the first core of the CCD (irq/housekeeping tends to land on cpu0)
    core = min(best["cpus"][1:], key=lambda c: util[c])
    ok = best["util_sum"] <= max_ccd_util * len(best["cpus"])
    return {"core": core, "ccd": best["cpus"], "ccd_util_sum": best["util_sum"],
            "ok": ok, "ccds": report}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--node", type=int, default=0)
    ap.add_argument("--window", type=float, default=1.0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = pick(a.node, a.window)
    if a.json:
        print(json.dumps(r))
    else:
        if not r["ok"]:
            print(f"warning: quietest CCD {r['ccd']} is busy "
                  f"(util sum {r['ccd_util_sum']:.2f})", file=sys.stderr)
        print(r["core"])
    sys.exit(0 if r["ok"] else 2)


if __name__ == "__main__":
    main()
