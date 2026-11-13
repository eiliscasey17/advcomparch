"""Tests for the analysis layer (scripts/common.py), the machine constants it
assumes, the placement helpers, and the membench CLI contract.

membench smoke tests run tiny problems pinned to the cpu given by TEST_CPU
(default 62, node 1's last CCD) so they don't disturb a running experiment.
"""
import io
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import common as C  # noqa: E402
import pick_core  # noqa: E402
import run as R  # noqa: E402

CPU = os.environ.get("TEST_CPU", "62")


def membench(*args):
    p = subprocess.run([str(C.MEMBENCH), *args], capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr
    return p.stdout


def csv(*args):
    return pd.read_csv(io.StringIO(membench(*args, "--header")))


# ----------------------------------------------------------- constants --
def sysfs_size(cpu, index):
    s = Path(f"/sys/devices/system/cpu/cpu{cpu}/cache/index{index}/size").read_text().strip()
    return int(s[:-1]) * {"K": C.KiB, "M": C.MiB}[s[-1]]


def test_cache_sizes_match_sysfs():
    assert sysfs_size(0, 0) == C.CACHE["L1"]  # index0 = L1d
    assert sysfs_size(0, 2) == C.CACHE["L2"]
    assert sysfs_size(0, 3) == C.CACHE["L3"]


def test_tlb_and_caches_match_cpuid():
    info = dict(l.split("=") for l in membench("info").split())
    assert int(info["l1_dtlb_4k_entries"]) == C.DTLB["L1"]
    assert int(info["l1_dtlb_2m_entries"]) == C.DTLB["L1"]
    assert int(info["l2_dtlb_4k_entries"]) == C.DTLB["L2"]
    assert int(info["l2_dtlb_2m_entries"]) == C.DTLB["L2"]
    assert int(info["l1d_kib"]) * C.KiB == C.CACHE["L1"]
    assert int(info["l2_kib"]) * C.KiB == C.CACHE["L2"]
    assert C.TLB_REACH[("L2", "4k")] == 12 * C.MiB
    assert C.TLB_REACH[("L1", "2m")] == 144 * C.MiB


def test_dram_peak():
    assert C.DRAM_PEAK_GBS == pytest.approx(460.8)


# ------------------------------------------------------------ formulas --
def test_little():
    # 10 lines in flight at 100 ns -> 0.1 lines/ns -> 6.4 GB/s
    assert C.little_bandwidth(10, 100) == pytest.approx(6.4)
    assert C.little_outstanding(6.4, 100) == pytest.approx(10)
    bw = np.array([1.0, 50.0, 300.0])
    lat = np.array([100.0, 120.0, 400.0])
    assert np.allclose(C.little_bandwidth(C.little_outstanding(bw, lat), lat), bw)


def test_amat():
    assert C.amat(1.0, [0.1], [10.0]) == pytest.approx(2.0)
    # 1 + 0.1*(10 + 0.5*(100)) = 7
    assert C.amat(1.0, [0.1, 0.5], [10.0, 100.0]) == pytest.approx(7.0)
    lat = {"L1": 1.0, "L2": 4.0, "L3": 14.0, "DRAM": 125.0}
    t = C.amat_from_fills(2.0, {"L2": 0.5, "L3": 0.25, "DRAM": 0.125}, lat)
    assert t == pytest.approx(2 + 0.5 * 3 + 0.25 * 13 + 0.125 * 124)


def test_knee():
    x = np.linspace(0, 300, 31)
    y = 100 + 0.02 * x + np.clip(x - 200, 0, None) ** 1.6 / 10
    i = C.knee(x, y)
    assert 170 <= x[i] <= 230
    # order-independent
    p = np.random.default_rng(0).permutation(len(x))
    assert x[p][C.knee(x[p], y[p])] == x[i]


def test_footprint_sweep_and_regime():
    s = C.footprint_sweep(4 * C.KiB, 1 * C.MiB, 4)
    assert s[0] == 4 * C.KiB and s[-1] == 1 * C.MiB and all(v % 64 == 0 for v in s)
    assert len(s) == 33
    assert [C.regime(v) for v in (16 * C.KiB, 512 * C.KiB, 16 * C.MiB, C.GiB)] == \
        ["L1", "L2", "L3", "DRAM"]


def test_add_metrics_chase_and_bw():
    df = pd.DataFrame([
        dict(mode="chase", steps=1000, chains=4, ns=50000.0, cycles=200000.0, fill_dram=900.0),
        dict(mode="bw", gens=2, gen_accesses=1000, gran=16, stride=64, rw_r=7, rw_w=3, nt=0,
             ns=1000.0, lat_steps=0, lat_cycles=0, cycles=8000.0),
        dict(mode="loadlat", gens=1, gen_accesses=100, gran=64, stride=32, rw_r=1, rw_w=0, nt=0,
             ns=1000.0, lat_steps=10, lat_cycles=4000, cycles=4000.0),
    ])
    m = C.add_metrics(df)
    c, b, l = m.iloc[0], m.iloc[1], m.iloc[2]
    assert c.ns_per_load == pytest.approx(50) and c.ns_per_step == pytest.approx(200)
    assert c.cyc_per_load == pytest.approx(200) and c.fill_dram_per_load == pytest.approx(0.9)
    assert c.ghz == pytest.approx(4.0)
    assert b.gbs_useful == pytest.approx(16.0) and b.gbs_lines == pytest.approx(64.0)
    assert b.gbs_traffic_model == pytest.approx(64 * 1.3)  # 30% writes each add an RFO
    assert np.isnan(b.lat_ns) and b.ghz == pytest.approx(4.0)
    assert l.gbs_lines == pytest.approx(100 * 32 / 1000)  # stride 32 < line: half a line each
    assert l.lat_ns == pytest.approx(100) and l.lat_ghz == pytest.approx(4.0)
    assert l.gbs_total == pytest.approx(3.2 + 10 * 64 / 1000)


def test_stats():
    med, lo, hi = C.bootstrap_median_ci([1, 2, 3, 4, 100])
    assert med == 3 and lo <= med <= hi
    r, lo, hi = C.ratio_ci([2, 2, 2], [1, 1, 1])
    assert r == lo == hi == 2


# ------------------------------------------------------------ placement --
def test_node_order_round_robin():
    for n in pick_core.nodes():
        order = pick_core.node_order(n)
        cpus = pick_core.node_cpus(n)
        assert set(order) == cpus - {min(cpus)}
        ccd_of = {c: i for i, g in enumerate(pick_core.ccds()) for c in g}
        first = [ccd_of[c] for c in order[:4]]
        assert len(set(first)) == 4  # first 4 threads on 4 different CCDs


def test_kern_n_footprint():
    for k, p, gs in (("mul", "contig", 1), ("axpy", "gather", 1), ("mul", "stride", 512)):
        n = R.kern_n(k, p, 64 * C.MiB, gs)
        arrays = 3 if k == "mul" else 2
        fp = arrays * n * gs * 8 + (4 * n if p == "gather" else 0)
        assert 0.99 * 64 * C.MiB <= fp <= 64 * C.MiB


def test_job_cmd_placement():
    pl = R.Placement(1, 1)
    j = R.loadlat(3, "t", rw="7:3", nt=True)
    cmd = R.job_cmd(j, pl, 5, 20, 100)
    assert cmd[1] == "loadlat" and "--nt" in cmd
    assert cmd[cmd.index("--cpus") + 1] == ",".join(map(str, pl.node_cpus[:4]))
    assert cmd[cmd.index("--rw") + 1] == "7:3"
    c = R.job_cmd(R.chase(C.GiB, "t", remote=True), pl, 5, 20, 100)
    assert pick_core.cpu_node_of(int(c[c.index("--mem-cpu") + 1])) != pick_core.cpu_node_of(1)
    u = R.job_cmd(R.bw(4, C.MiB, "t", place="unpinned"), pl, 5, 20, 100)
    assert u[u.index("--cpus") + 1] == "-1,-1,-1,-1"


def test_experiment_lists_build():
    for name, f in R.EXPERIMENTS.items():
        for j in f(True):
            R.job_cmd(j, R.Placement(1, 1), 3, 5, 50)


# ---------------------------------------------------------- membench CLI --
def test_chase_levels_and_schema():
    df = csv("chase", "--size", "16K,512K", "--cpu", CPU, "--samples", "2", "--min-ms", "2",
             "--counters", "fills")
    assert list(df.columns[:16]) == ["mode", "size", "pattern", "stride", "param", "chains",
                                     "pages", "thp_frac", "nodes", "cpu", "cpu_end", "cpu_node",
                                     "mem_node", "sample", "steps", "ns"]
    m = C.add_metrics(df).groupby("size").median(numeric_only=True)
    assert 3.5 <= m.loc[16 * C.KiB, "cyc_per_load"] <= 5     # L1: 4-cycle load-to-use
    assert 11 <= m.loc[512 * C.KiB, "cyc_per_load"] <= 18    # L2: ~14 cycles
    assert m.loc[512 * C.KiB, "fill_l2_per_load"] > 0.9
    assert (df.thp_frac == 1).all() and (df.cpu == int(CPU)).all() and (df.mem_node == df.cpu_node).all()


def test_chase_pages_4k_reported():
    df = csv("chase", "--size", "8M", "--cpu", CPU, "--samples", "1", "--min-ms", "2",
             "--pages", "4k")
    assert (df.thp_frac == 0).all() and (df.pages == "4k").all()


def test_chase_chains_steps():
    df = csv("chase", "--size", "1M", "--chains", "4", "--cpu", CPU, "--samples", "1",
             "--min-ms", "2")
    assert (df.chains == 4).all() and (df.steps % 4 == 0).all()


def test_bw_and_loadlat_rows():
    b = csv("bw", "--cpus", CPU, "--size", "1M", "--ms", "20", "--samples", "2", "--rw", "7:3",
            "--gran", "32", "--stride", "256", "--order", "random")
    assert len(b) == 2 and (b.rw_r == 7).all() and (b.order == "random").all()
    assert (C.add_metrics(b).gbs_useful > 0.5).all()
    assert (b.ns > 15e6).all()  # the window is honoured
    l = csv("loadlat", "--cpus", CPU, "--lat-size", "16M", "--ms", "20", "--samples", "1")
    m = C.add_metrics(l)
    assert (l.gens == 0).all() and 5 < m.lat_ns.iloc[0] < 40  # L3-resident probe, unloaded


def test_kern_rows():
    df = csv("kern", "--kernel", "axpy", "--pattern", "gather", "--n", "4096", "--cpu", CPU,
             "--samples", "1", "--min-ms", "2", "--counters", "cache")
    assert (df.footprint == 4096 * 2 * 8 + 4096 * 4).all()
    assert df.reps.iloc[0] >= 1 and df.instructions.iloc[0] > 0


@pytest.mark.parametrize("args", [["chase"], ["chase", "--size", "1M", "--chains", "7"],
                                  ["bw", "--cpus", CPU, "--gran", "48"],
                                  ["bw", "--cpus", CPU, "--gran", "64", "--stride", "96"],
                                  ["kern", "--n", "100", "--kernel", "dot"],
                                  ["chase", "--size", "1M", "--pages", "1g"]])
def test_invalid_args_rejected(args):
    p = subprocess.run([str(C.MEMBENCH), *args, "--cpu", CPU] if args[0] != "bw" else
                       [str(C.MEMBENCH), *args, "--ms", "5", "--samples", "1"],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode != 0


# ------------------------------------------------ committed raw data --
def test_committed_raw_reproduces_summaries(tmp_path, monkeypatch):
    """A fresh clone has only results/raw_runs/ (gzipped pinned runs). Every
    pinned run must load from there alone, and re-summarizing must give back
    the committed summary exactly."""
    import json
    import summarize as S
    monkeypatch.setattr(C, "RAW", tmp_path)  # hide the local (gitignored) raw tree
    pins = json.loads((C.RESULTS / "runs.json").read_text())
    for exp, run in pins.items():
        if exp.startswith("_"):
            continue
        df, got = C.load_raw(exp)
        assert got == run and len(df) > 0 and (df.exp == exp).all()
    for exp in ("latency", "rwmix", "pitfall"):  # small ones; same code path as the rest
        raw, run = C.load_raw(exp)
        new = S.summarize(raw).assign(run=run)
        old = pd.read_csv(C.SUMMARY / f"{exp}.csv")
        new = pd.read_csv(io.StringIO(new.to_csv(index=False)))  # same float round-trip
        pd.testing.assert_frame_equal(new, old)


def test_legacy_experiment_ids():
    assert [C.exp_id(f"E{i}") for i in range(9)] == C.EXPERIMENT_IDS
    assert C.exp_id("tlb") == "tlb" and C.exp_id("e7") == "tlb"
    assert set(R.EXPERIMENTS) | {"calib"} >= set(C.EXPERIMENT_IDS) - {"calib"}
    with pytest.raises(ValueError):
        C.exp_id("E9")


def test_little_in_flight_per_sample():
    df = pd.DataFrame([dict(mode="loadlat", gens=1, gen_accesses=1000, gran=64, stride=64, rw_r=1,
                            rw_w=0, nt=0, ns=1000.0, lat_steps=10, lat_cycles=4000, cycles=4000.0)])
    m = C.add_metrics(df).iloc[0]
    assert m.lines_in_flight == pytest.approx(C.little_outstanding(m.gbs_total, m.lat_ns))
