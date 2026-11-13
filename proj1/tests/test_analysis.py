"""Tests for the analysis layer (scripts/common.py) and the bench CLI contract.

The bench smoke tests run tiny L1-resident problems pinned away from node 0
(where experiments run), so they are safe to run alongside a measurement.
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

BENCH = ROOT / "build" / "gcc-native512" / "bench"
OFF_NODE0 = str(max(os.sched_getaffinity(0)))  # a node-1 cpu on this machine


# ------------------------------------------------------------- formulas --
@pytest.mark.parametrize("kernel,dtype,bytes_,bytes_wa", [
    ("dot_simd", "f32", 8, 8), ("dot_simd", "f64", 16, 16), ("mul", "f32", 12, 16),
    ("stencil", "f32", 8, 12), ("mul_gather", "f32", 16, 20), ("dot_gather", "f64", 20, 20),
    ("bw_write", "f32", 4, 8), ("bw_triad", "f32", 12, 16), ("dot_s4", "f32", 8, 8),
])
def test_useful_bytes(kernel, dtype, bytes_, bytes_wa):
    assert C.useful_bytes_per_elem(kernel, dtype) == bytes_
    assert C.useful_bytes_per_elem(kernel, dtype, write_allocate=True) == bytes_wa


def test_arithmetic_intensity():
    assert C.arithmetic_intensity("dot_simd", "f32") == pytest.approx(2 / 8)
    assert C.arithmetic_intensity("mul", "f32") == pytest.approx(1 / 12)
    assert C.arithmetic_intensity("stencil", "f32") == pytest.approx(5 / 8)
    assert C.arithmetic_intensity("stencil", "f64") == pytest.approx(5 / 16)


def test_stencil_elements_skip_boundaries():
    assert C.elements("stencil", 100) == 98
    assert C.elements("stencil", 1) == 0
    assert C.elements("mul", 100) == 100


@pytest.mark.parametrize("kernel,dtype,n,kw,expect", [
    ("dot_simd", "f32", 1000, {}, 8000),
    ("dot_s4", "f32", 1000, {}, 32000),          # x and y each span 4n
    ("mul_s8", "f64", 100, {}, 2 * 800 * 8 + 800),
    ("stencil", "f32", 1000, {}, 8000),
    ("mul_gather", "f32", 100, {"gstride": 4, "pattern": "stride"}, 1600 + 400 + 400 + 400),
    ("dot_gather", "f32", 100, {"pattern": "random"}, 400 + 400 + 400),
])
def test_footprint(kernel, dtype, n, kw, expect):
    assert C.footprint_bytes(kernel, dtype, n, **kw) == expect


@pytest.mark.parametrize("kernel", ["dot_simd", "mul", "stencil", "mul_s8", "dot_gather"])
@pytest.mark.parametrize("target", [8 * C.KiB, 1 * C.MiB, 512 * C.MiB])
def test_n_for_footprint_is_largest_fitting_multiple(kernel, target):
    n = C.n_for_footprint(kernel, "f32", target)
    assert n % 64 == 0
    assert C.footprint_bytes(kernel, "f32", n) <= target
    assert C.footprint_bytes(kernel, "f32", n + 64) > target


def test_footprint_sweep():
    s = C.footprint_sweep()
    assert s[0] == 8 * C.KiB and s[-1] == 1 * C.GiB
    assert len(s) == 17 * 4 + 1
    assert all(b > a for a, b in zip(s, s[1:]))


def test_regimes():
    assert C.regime(32 * C.KiB) == "L1"
    assert C.regime(32 * C.KiB + 1) == "L2"
    assert C.regime(32 * C.MiB) == "L3"
    assert C.regime(64 * C.MiB) == "DRAM"


def test_add_metrics_formulas():
    df = pd.DataFrame([dict(kernel="mul", dtype="f32", n=1000, span=1000, reps=10, ns=5000,
                            pattern="-", gstride=0, cycles=20000, instructions=40000,
                            fp_flops=10000)])
    m = C.add_metrics(df).iloc[0]
    assert m.elems == 10000
    assert m.gflops == pytest.approx(10000 / 5000)                    # FLOP/ns
    assert m.gibs == pytest.approx(12 * 10000 / 5000 * 1e9 / C.GiB)
    assert m.cyc_per_elem == pytest.approx(2.0)
    assert m.ghz == pytest.approx(4.0)
    assert m.ipc == pytest.approx(2.0)
    assert m.flops_per_elem_pmu == pytest.approx(1.0)
    assert m.footprint == 12000


def test_bootstrap_and_ratio():
    rng = np.random.default_rng(3)
    x = rng.normal(10, 1, 200)
    med, lo, hi = C.bootstrap_median_ci(x)
    assert lo <= med <= hi and hi - lo < 1
    r, lo, hi = C.ratio_ci(2 * x, x)
    assert r == pytest.approx(2.0) and lo < 2.0 < hi


# ------------------------------------------------------------ core pick --
def test_pick_core_is_on_node0_and_not_ccd_leader():
    r = pick_core.pick(window=0.2)
    assert r["core"] in pick_core.node_cpus(0)
    assert r["core"] in r["ccd"] and r["core"] != r["ccd"][0]
    assert all(len(g) == 8 for g in pick_core.ccds())


# ----------------------------------------------------------- bench CLI --
def bench(*args, check=True):
    if not BENCH.exists():
        pytest.skip("bench not built")
    p = subprocess.run(["taskset", "-c", OFF_NODE0, str(BENCH), *args, "--header"],
                       capture_output=True, text=True)
    if check:
        assert p.returncode == 0, p.stderr
    return p


def test_bench_csv_schema_and_counts():
    p = bench("--kernel", "mul", "--n", "1000,2000", "--samples", "2", "--min-ms", "0.2")
    df = pd.read_csv(io.StringIO(p.stdout))
    assert len(df) == 4 and set(df.n) == {1000, 2000}
    assert {"ns", "reps", "cycles", "fp_flops", "mux"} <= set(df.columns)
    assert (df.reps >= 1).all() and (df.ns > 0).all()
    if df.cycles.notna().all() and (df.mux > 0.99).all():
        # PMU-counted FLOPs must equal the analytic count: 1 per element
        assert (df.fp_flops / (df.n * df.reps)).between(0.999, 1.01).all()


def test_bench_span_matches_footprint_model():
    for args, kernel, kw in ((["--kernel", "mul_gather", "--pattern", "stride", "--gstride", "4"],
                              "mul_gather", {"pattern": "stride", "gstride": 4}),
                             (["--kernel", "dot_s8"], "dot_s8", {})):
        df = pd.read_csv(io.StringIO(bench(*args, "--n", "512", "--samples", "1",
                                           "--min-ms", "0.1", "--counters", "none").stdout))
        r = df.iloc[0]
        m = C.KERNELS[kernel]
        span_model = 512 * (kw.get("gstride", 1) if m.family in ("dotg", "mulg") else m.stride)
        assert r.span == span_model


def test_bench_offsets_are_applied():
    df = pd.read_csv(io.StringIO(bench("--kernel", "mul", "--n", "256", "--offset-x", "4",
                                       "--offset-y", "8", "--offset-z", "12", "--samples", "1",
                                       "--min-ms", "0.1", "--counters", "align").stdout))
    r = df.iloc[0]
    assert (r.offset_x, r.offset_y, r.offset_z) == (4, 8, 12)


@pytest.mark.parametrize("args", [
    ["--kernel", "mul_aligned", "--offset", "4"],   # aligned promise on misaligned data
    ["--kernel", "mul", "--dtype", "f64", "--offset", "4"],  # not a multiple of 8
    ["--kernel", "nope"],
])
def test_bench_rejects_invalid(args):
    p = bench(*args, "--n", "64", check=False)
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
    for exp in ("negative", "multiacc"):  # small ones; same code path as the rest
        raw, run = C.load_raw(exp)
        new = S.summarize(raw).assign(run=run)
        old = pd.read_csv(C.SUMMARY / f"{exp}.csv")
        new = pd.read_csv(io.StringIO(new.to_csv(index=False)))  # same float round-trip
        pd.testing.assert_frame_equal(new, old)


def test_legacy_experiment_ids():
    assert [C.exp_id(f"E{i}") for i in range(10)] == C.EXPERIMENT_IDS
    assert C.exp_id("stride") == "stride" and C.exp_id("e4") == "stride"
    with pytest.raises(ValueError):
        C.exp_id("E10")
