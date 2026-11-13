#!/usr/bin/env python3
"""Make every figure: one graph per JPG in results/plots/.

  python scripts/plot.py all      (or calib simd locality ...; old IDs E0..E9 also work)

Encoding is fixed across all figures so a series always looks the same:
  color + marker = build config  (validated categorical order, see
                                  dataviz palette; never re-assigned by rank)
  line style     = compiler      (solid gcc, dashed clang)
Variability: every line carries a shaded band and every bar/point an error
bar = bootstrap 95% CI of the median (speedups: CI of the ratio of medians),
unless a subtitle says otherwise (deterministic PMU counts, model lines).
"""
import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.ticker import FuncFormatter, NullLocator  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402
import summarize as S  # noqa: E402

SURFACE, INK, INK2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#8a8984", "#e4e3df"
SLOTS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
CFG_ORDER = ["scalar", "default", "native256", "native512", "native512fm"]
CFG_COLOR = dict(zip(CFG_ORDER, SLOTS))
CFG_MARK = dict(zip(CFG_ORDER, ["o", "s", "^", "D", "v"]))
CFG_LABEL = {"scalar": "scalar (vectorizers off)", "default": "default -O3 (SSE2, 128-bit)",
             "native256": "-march=native (256-bit)", "native512": "-march=native 512-bit",
             "native512fm": "512-bit + -ffast-math"}
CC_LS = {"gcc": "-", "clang": "--"}
CC_HATCH = {"gcc": "", "clang": "////"}
REGIME_SPAN = [("L1", 0, C.CACHE["L1"]), ("L2", C.CACHE["L1"], C.CACHE["L2"]),
               ("L3 (CCD)", C.CACHE["L2"], C.CACHE["L3"]), ("DRAM", C.CACHE["L3"], 4 * C.GiB)]

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": MUTED, "axes.labelcolor": INK, "text.color": INK, "xtick.color": INK2,
    "ytick.color": INK2, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "axes.spines.top": False, "axes.spines.right": False, "font.size": 10,
    "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlelocation": "left",
    "axes.titlepad": 20,
    "legend.frameon": False, "legend.fontsize": 8.5, "lines.linewidth": 1.6,
    "lines.markersize": 4.5, "figure.dpi": 160,
})


# ------------------------------------------------------------------ helpers
def save(fig, name):
    C.PLOTS.mkdir(parents=True, exist_ok=True)
    p = C.PLOTS / f"{name}.jpg"
    fig.savefig(p, dpi=160, bbox_inches="tight", pil_kwargs={"quality": 92})
    plt.close(fig)
    print("  wrote", p.relative_to(C.ROOT))


def fig(w=7.2, h=4.2):
    return plt.subplots(figsize=(w, h))


def subtitle(ax, text):
    ax.text(0, 1.015, text, transform=ax.transAxes, fontsize=8.5, color=INK2, va="bottom")


def fmt_bytes(x, _=None):
    for unit, v in (("GiB", C.GiB), ("MiB", C.MiB), ("KiB", C.KiB)):
        if x >= v:
            q = x / v
            return f"{q:.0f} {unit}" if q >= 1 and abs(q - round(q)) < 1e-6 else f"{q:.1f} {unit}"
    return f"{x:.0f} B"


def legend_below(ax, ncol=2, **kw):
    """Legend under the x-axis label, so dense sweep plots stay unobstructed."""
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.24), ncol=ncol, fontsize=8, **kw)


def footprint_axis(ax, lo=8 * C.KiB, hi=1 * C.GiB, shade=True):
    ax.set_xscale("log", base=2)
    ticks = [2 ** i for i in range(int(np.log2(lo)), int(np.log2(hi)) + 1, 2)]
    ax.set_xticks(ticks)
    ax.xaxis.set_major_formatter(FuncFormatter(fmt_bytes))
    ax.xaxis.set_minor_locator(NullLocator())
    ax.set_xlim(lo / 1.15, hi * 1.15)
    ax.set_xlabel("working set (all arrays)")
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
    if shade:
        for i, (name, a, b) in enumerate(REGIME_SPAN):
            a, b = max(a, lo / 1.15), min(b, hi * 1.15)
            if i % 2 == 0:
                ax.axvspan(a, b, color="#f1f0ec", zorder=0, lw=0)
            ax.text(np.sqrt(a * b), 1.0, name, transform=ax.get_xaxis_transform(), ha="center",
                    va="top", fontsize=8, color=INK2)


def summary(exp):
    p = C.SUMMARY / f"{exp}.csv"
    if not p.exists():
        raw, run = C.load_raw(exp)
        S.summarize(raw).assign(run=run).to_csv(p, index=False)
    return pd.read_csv(p, keep_default_na=False, na_values=[""])


def raw_metrics(exp):
    raw, _ = C.load_raw(exp)
    raw = C.add_metrics(raw)
    raw["tag"] = raw.tag.fillna("") if "tag" in raw else ""
    return raw


def line(ax, x, y, cfg, cc, label=None, lo=None, hi=None, **kw):
    """One build's series; lo/hi (same length) draw its CI band."""
    ax.plot(x, y, color=CFG_COLOR[cfg], ls=CC_LS[cc], marker=CFG_MARK[cfg],
            markerfacecolor=CFG_COLOR[cfg] if cc == "gcc" else SURFACE, markeredgewidth=1.1,
            label=label, **kw)
    if lo is not None:
        band(ax, x, lo, hi, CFG_COLOR[cfg])


def band(ax, x, lo, hi, color):
    ax.fill_between(np.asarray(x, float), np.asarray(lo, float), np.asarray(hi, float),
                    color=color, alpha=0.18, lw=0, zorder=1)


def yerr(v, lo, hi):
    """Asymmetric error-bar array from a median and its CI bounds."""
    v, lo, hi = (np.asarray(a, float) for a in (v, lo, hi))
    return np.vstack([np.clip(v - lo, 0, None), np.clip(hi - v, 0, None)])


def ratio_ci_of(num_rows, den_rows, metric):
    """median(num)/median(den) with bootstrap CI from raw samples."""
    if len(num_rows) == 0 or len(den_rows) == 0:
        return np.nan, np.nan, np.nan
    return C.ratio_ci(num_rows[metric].values, den_rows[metric].values)


def speedup_table(raw, kernel, metric="gflops", by=("compiler", "n")):
    """Per (compiler, config, n): median ratio vs same-compiler scalar + CI."""
    rows = []
    sub = raw[raw.kernel == kernel]
    for (cc, n), g in sub.groupby(list(by)):
        base = g[g.config == "scalar"][metric].values
        if len(base) == 0:
            continue
        for cfg, h in g.groupby("config"):
            if cfg == "scalar":
                continue
            r, lo, hi = C.ratio_ci(h[metric].values, base)
            rows.append(dict(compiler=cc, config=cfg, n=n, footprint=h.footprint.iloc[0],
                             speedup=r, lo=lo, hi=hi))
    return pd.DataFrame(rows).sort_values("footprint")


def roofs():
    """Measured single-core bandwidth per level from calib (median over the
    level's interior so boundary transitions don't bias it)."""
    bw = pd.read_csv(C.SUMMARY / "calib_bw.csv")
    interior = {"L1": (8 * C.KiB, 24 * C.KiB), "L2": (96 * C.KiB, 640 * C.KiB),
                "L3": (3 * C.MiB, 20 * C.MiB), "DRAM": (256 * C.MiB, 2 * C.GiB)}
    rows = []
    for k, g in bw.groupby("kernel"):
        for lvl, (a, b) in interior.items():
            h = g[(g.footprint >= a) & (g.footprint <= b)]
            rows.append(dict(kernel=k, level=lvl, gibs=h.gibs.median(),
                             gbs=h.gibs.median() * C.GiB / 1e9))
    out = pd.DataFrame(rows)
    best = out.loc[out.groupby("level").gibs.idxmax()].assign(kernel="best")
    out = pd.concat([out, best], ignore_index=True)
    out.to_csv(C.SUMMARY / "calib_roofs.csv", index=False)
    return out


def peak():
    return pd.read_csv(C.SUMMARY / "calib_peak.csv")


# ---------------------------------------------------------------------- calib
def plot_calib():
    raw, run = C.load_raw("calib")
    pk = raw[raw.build == "calib"].dropna(axis=1, how="all")
    pk.groupby(["test", "instr", "dtype", "width_bits", "lanes"], as_index=False).agg(
        per_cycle=("per_cycle", "median"), ghz=("ghz", "median")).to_csv(
        C.SUMMARY / "calib_peak.csv", index=False)
    S.summarize(raw[~raw.build.isin(["calib", "calib_align"])].dropna(axis=1, how="all")).assign(
        run=run).to_csv(C.SUMMARY / "calib_bw.csv", index=False)
    al_raw = raw[raw.build == "calib_align"].dropna(axis=1, how="all")
    al = al_raw.groupby(["op", "bits", "offset"], as_index=False)[
        ["cyc_per_insn", "misal64_per_insn"]].median()
    al.to_csv(C.SUMMARY / "calib_align.csv", index=False)
    pk_raw = raw[raw.build == "calib"]
    p = peak()
    ghz = p.ghz.median()

    f, ax = fig(6.4, 4.0)
    fma = p[(p.test == "peak") & p.instr.str.startswith("vfmadd")]
    widths = ["scalar", "128-bit", "256-bit", "512-bit"]
    theory = {"f32": [4, 16, 32, 32], "f64": [4, 8, 16, 16]}
    x = np.arange(4)
    for i, d in enumerate(("f32", "f64")):
        g = fma[fma.dtype == d].sort_values("width_bits")
        ci = [C.bootstrap_median_ci(pk_raw[(pk_raw.test == "peak") & (pk_raw.instr == r.instr) &
                                           (pk_raw.dtype == d) & (pk_raw.width_bits == r.width_bits)
                                           ].per_cycle) for r in g.itertuples()]
        ax.bar(x + (i - 0.5) * 0.36, g.per_cycle.values, 0.34, color=SLOTS[i], label=f"{d} measured",
               zorder=3, edgecolor=SURFACE, linewidth=1,
               yerr=yerr(g.per_cycle.values, [c[1] for c in ci], [c[2] for c in ci]),
               error_kw=dict(ecolor=INK2, lw=1, capsize=3))
        for xi, t, m in zip(x + (i - 0.5) * 0.36, theory[d], g.per_cycle.values):
            ax.plot([xi - 0.17, xi + 0.17], [t, t], color=INK, lw=1.2, zorder=4)
            ax.text(xi, m + 0.6, f"{m:.1f}", ha="center", fontsize=8, color=INK)
    ax.plot([], [], color=INK, lw=1.2, label="theory: 2 FMA pipes x 256 bit")
    ax.set_xticks(x, widths)
    ax.set_ylabel("FLOP / cycle (one core)")
    ax.set_title("Peak FMA throughput: 512-bit gives no FLOP/cycle gain on Zen 4")
    subtitle(ax, f"12 independent vfmadd231 chains, inline asm; {ghz:.2f} GHz measured -> "
                 f"f32 peak {32 * ghz:.0f} GFLOP/s, f64 {16 * ghz:.0f} GFLOP/s; error bars: 95% CI")
    ax.legend(loc="upper left")
    save(f, "calib_peak_flops")

    bw = pd.read_csv(C.SUMMARY / "calib_bw.csv")
    f, ax = fig()
    names = {"bw_read": "read  (sum x)", "bw_write": "write  (z = c)",
             "bw_scale": "scale  (z = c*x)", "bw_triad": "triad  (z = x + c*y)"}
    for i, k in enumerate(names):
        g = bw[bw.kernel == k].sort_values("footprint")
        ax.plot(g.footprint, g.gibs, color=SLOTS[i], marker="o", label=names[k])
        band(ax, g.footprint, g.gibs_lo, g.gibs_hi, SLOTS[i])
    footprint_axis(ax)
    ax.set_ylabel("GiB/s (compulsory bytes, no write-allocate)")
    ax.set_title("Single-core memory bandwidth by level (AVX-512 build)")
    subtitle(ax, "gcc native512; stores also pay a hidden read-for-ownership not counted here; band: 95% CI")
    ax.set_ylim(bottom=0)
    legend_below(ax, ncol=4)
    save(f, "calib_bandwidth_by_level")
    roofs()

    f, ax = fig(6.8, 4.0)
    for i, (op, bits) in enumerate((("load", 256), ("load", 512), ("store", 256), ("store", 512))):
        g = al[(al.op == op) & (al.bits == bits)].sort_values("offset")
        ci = [C.bootstrap_median_ci(al_raw[(al_raw.op == op) & (al_raw.bits == bits) &
                                           (al_raw.offset == o)].cyc_per_insn) for o in g.offset]
        ax.errorbar(g.offset, g.cyc_per_insn, yerr=yerr(g.cyc_per_insn, [c[1] for c in ci],
                                                         [c[2] for c in ci]),
                    color=SLOTS[i], marker="os^D"[i], ls="-" if op == "load" else "--",
                    capsize=2, label=f"{bits}-bit {op}")
    ax.set_xticks([0, 4, 8, 16, 32, 48, 60])
    ax.set_xlabel("byte offset from a 64-byte line boundary")
    ax.set_ylabel("core cycles per instruction (throughput)")
    ax.set_ylim(bottom=0)
    ax.set_title("Unaligned access cost on Zen 4 (microbenchmark, L1)")
    subtitle(ax, "loads pay when a 256-bit half crosses a 64-B line; stores pay whenever a half is not "
                 "32-B aligned; error bars: 95% CI")
    legend_below(ax, ncol=4)
    save(f, "calib_unaligned_access_cost")


# ---------------------------------------------------------------------- simd
SIMD_TITLES = {"dot_simd": "dot product  s += x[i]*y[i]", "mul": "elementwise multiply  z = x*y",
             "stencil": "3-point stencil  y = a x[i-1] + b x[i] + c x[i+1]"}


def plot_simd():
    s = summary("simd")
    raw = raw_metrics("simd")
    for k, title in SIMD_TITLES.items():
        kn = k.replace("_simd", "")
        f, ax = fig()
        for cc in ("gcc", "clang"):
            for cfg in CFG_ORDER[:4]:
                g = s[(s.kernel == k) & (s.compiler == cc) & (s.config == cfg)].sort_values("footprint")
                if len(g):
                    line(ax, g.footprint, g.gflops, cfg, cc, f"{cc} {CFG_LABEL[cfg]}",
                         lo=g.gflops_lo, hi=g.gflops_hi)
        footprint_axis(ax)
        ax.set_ylabel("GFLOP/s")
        ax.set_ylim(bottom=0)
        ax.set_title(f"Achieved throughput: {title}")
        subtitle(ax, "f32, one pinned core, median of 3 shuffled rounds x 11 samples; band: 95% CI")
        legend_below(ax)
        save(f, f"simd_throughput_{kn}")

        f, ax = fig()
        sp = speedup_table(raw, k)
        for cc in ("gcc", "clang"):
            for cfg in CFG_ORDER[1:4]:
                g = sp[(sp.compiler == cc) & (sp.config == cfg)]
                if len(g):
                    line(ax, g.footprint, g.speedup, cfg, cc, f"{cc} {CFG_LABEL[cfg]}",
                         lo=g.lo, hi=g.hi)
        ax.axhline(1, color=INK2, lw=0.9)
        footprint_axis(ax)
        ax.set_ylabel("speedup vs. same-compiler scalar build")
        ax.set_ylim(bottom=0)
        ax.set_title(f"SIMD speedup: {title}")
        subtitle(ax, "ratio of medians; band = bootstrap 95% CI")
        legend_below(ax)
        save(f, f"simd_speedup_{kn}")


# ---------------------------------------------------------------------- locality
def regime_medians(df, ycol):
    out = {}
    for name, a, b in REGIME_SPAN:
        h = df[(df.footprint > a) & (df.footprint <= b)]
        if name.startswith("L3"):
            h = h[(h.footprint > 2 * C.MiB) & (h.footprint <= 16 * C.MiB)]  # avoid edges
        if name == "DRAM":
            h = h[h.footprint >= 128 * C.MiB]
        out[name] = h[ycol].median()
    return out


def plot_locality():
    s = summary("simd")
    raw = raw_metrics("simd")
    rf = roofs()
    k = "dot_simd"
    f, ax = fig()
    for cc in ("gcc", "clang"):
        for cfg in CFG_ORDER[:4]:
            g = s[(s.kernel == k) & (s.compiler == cc) & (s.config == cfg)].sort_values("footprint")
            line(ax, g.footprint, g.gibs, cfg, cc, f"{cc} {CFG_LABEL[cfg]}", lo=g.gibs_lo,
                 hi=g.gibs_hi)
    rd = rf[rf.kernel == "bw_read"].set_index("level").gibs
    for (name, a, b), lvl in zip(REGIME_SPAN, ("L1", "L2", "L3", "DRAM")):
        a, b = max(a, 8 * C.KiB), min(b, 1 * C.GiB)
        ax.plot([a, b], [rd[lvl]] * 2, color=INK, lw=1.0, ls=":")
    ax.plot([], [], color=INK, lw=1.0, ls=":", label="measured read-bandwidth roof (calib)")
    footprint_axis(ax)
    ax.set_ylabel("GiB/s (8 bytes per element)")
    ax.set_ylim(bottom=0)
    ax.set_title("Locality sweep: dot product bandwidth across the cache hierarchy")
    subtitle(ax, "f32; vector builds hit each level's bandwidth roof, scalar never leaves its "
                 "add-latency bound; band: 95% CI")
    legend_below(ax)
    save(f, "locality_dot_bandwidth")

    f, ax = fig()
    sp = speedup_table(raw, k)
    for cc in ("gcc", "clang"):
        for cfg in ("native256", "native512"):
            g = sp[(sp.compiler == cc) & (sp.config == cfg)]
            line(ax, g.footprint, g.speedup, cfg, cc, f"{cc} {CFG_LABEL[cfg]}", lo=g.lo, hi=g.hi)
    g = sp[(sp.compiler == "gcc") & (sp.config == "native512")]
    for name, v in regime_medians(g, "speedup").items():
        a, b = [(x, y) for n, x, y in REGIME_SPAN if n == name][0]
        a, b = max(a, 8 * C.KiB), min(b, 1 * C.GiB)
        ax.annotate(f"{v:.1f}x", (np.sqrt(a * b), v), textcoords="offset points", xytext=(0, 9),
                    ha="center", fontsize=9, fontweight="bold", color=INK)
    ax.axhline(1, color=INK2, lw=0.9)
    footprint_axis(ax)
    ax.set_ylabel("speedup vs. scalar")
    ax.set_ylim(bottom=0)
    ax.set_title("Where SIMD gains compress: dot product speedup by cache level")
    subtitle(ax, "labels: gcc 512-bit median speedup per regime; memory-bound regimes cap the gain; "
                 "band: 95% CI")
    legend_below(ax)
    save(f, "locality_dot_speedup_compression")

    f, ax = fig()
    for cc in ("gcc", "clang"):
        for cfg in CFG_ORDER[:4]:
            g = s[(s.kernel == k) & (s.compiler == cc) & (s.config == cfg)].sort_values("footprint")
            line(ax, g.footprint, g.cyc_per_elem, cfg, cc, f"{cc} {CFG_LABEL[cfg]}",
                 lo=g.cyc_per_elem_lo, hi=g.cyc_per_elem_hi)
    footprint_axis(ax)
    ax.set_yscale("log", base=2)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    ax.set_ylabel("core cycles per element (PMU)")
    ax.set_title("Dot product: cycles per element across working-set sizes")
    subtitle(ax, "scalar: 3 cycles = one dependent vaddss per element; vector builds step up at each "
                 "cache level; band: 95% CI")
    legend_below(ax)
    save(f, "locality_dot_cycles_per_elem")

    m = summary("locality")
    m["dram_read_B"] = (m.l2pf_miss_l3_per_elem + m.fill_dram_per_elem) * C.LINE
    mr = raw_metrics("locality")
    mr["dram_read_B"] = (mr.l2pf_miss_l3 + mr.fill_dram) / mr.elems * C.LINE
    f, ax = fig()
    for i, (kern, cfgs) in enumerate((("dot_simd", ("scalar", "native512")),
                                      ("mul", ("scalar", "native512")))):
        for cfg in cfgs:
            g = m[(m.kernel == kern) & (m.config == cfg)].sort_values("footprint")
            col = SLOTS[2 * i + (cfg == "native512")]
            ax.plot(g.footprint, g.dram_read_B, color=col,
                    marker=CFG_MARK[cfg], label=f"{'dot' if kern == 'dot_simd' else 'mul'} gcc {cfg}")
            h = mr[(mr.kernel == kern) & (mr.config == cfg)]
            ci = [C.bootstrap_median_ci(h[h.n == n].dram_read_B) for n in g.n]
            band(ax, g.footprint, [c[1] for c in ci], [c[2] for c in ci], col)
    ax.axhline(8, color=INK2, lw=1, ls=":")
    ax.text(9 * C.KiB, 8.3, "dot: 8 B/elem compulsory (x, y)", fontsize=8, color=INK2)
    ax.axhline(12, color=INK2, lw=1, ls="--")
    ax.text(9 * C.KiB, 12.3, "mul: 8 B reads + 4 B read-for-ownership of z", fontsize=8, color=INK2)
    footprint_axis(ax)
    ax.set_ylabel("estimated DRAM read bytes / element")
    ax.set_ylim(0, 16)
    ax.set_title("Measured memory traffic (PMU): DRAM reads per element")
    subtitle(ax, "(L2-prefetch misses to DRAM + demand DRAM fills) x 64 B; validated at 1.06 lines/line "
                 "in test_perfctr; band: 95% CI")
    ax.legend(loc="lower left", bbox_to_anchor=(0.0, 0.08))
    save(f, "locality_dram_traffic")


# ---------------------------------------------------------------------- align
ALIGN_BUILDS = [("gcc", "scalar"), ("gcc", "native256"), ("gcc", "native512"), ("clang", "native512")]


def rel_ci(raw, sel, ref_sel, metric="cyc_per_elem"):
    """Ratio of medians (point / aligned reference) with bootstrap CI."""
    return ratio_ci_of(raw[sel], raw[ref_sel], metric)


def plot_align():
    s = summary("align")
    raw = raw_metrics("align")
    for k in ("mul", "stencil", "dot_simd"):
        f, ax = fig(6.8, 4.0)
        for cc, cfg in ALIGN_BUILDS:
            base = s[(s.kernel == k) & (s.compiler == cc) & (s.config == cfg) & (s.tag == "align-L1")]
            g = base.sort_values("offset_x")
            sel = (raw.kernel == k) & (raw.compiler == cc) & (raw.config == cfg)
            ref = sel & (raw.tag == "align-L1") & (raw.offset_x == 0)
            ci = [rel_ci(raw, sel & (raw.tag == "align-L1") & (raw.offset_x == o), ref)
                  for o in g.offset_x]
            line(ax, g.offset_x, [c[0] for c in ci], cfg, cc, f"{cc} {CFG_LABEL[cfg]}",
                 lo=[c[1] for c in ci], hi=[c[2] for c in ci])
            r, lo, hi = rel_ci(raw, sel & (raw.tag == "align-L1-mixed"), ref)
            ax.errorbar([72], [r], yerr=yerr([r], [lo], [hi]), color=CFG_COLOR[cfg],
                        marker=CFG_MARK[cfg], mfc=CFG_COLOR[cfg] if cc == "gcc" else SURFACE,
                        capsize=2, zorder=4, ls="none")
        if k == "mul":
            for cc, cfg in (("gcc", "native512"),):
                sel = (raw.compiler == cc) & (raw.config == cfg) & (raw.tag == "align-L1")
                r, lo, hi = rel_ci(raw, sel & (raw.kernel == "mul_aligned"),
                                   sel & (raw.kernel == "mul") & (raw.offset_x == 0))
                ax.errorbar([-6], [r], yerr=yerr([r], [lo], [hi]), marker="*", ms=10, color=INK,
                            capsize=2, ls="none", zorder=5,
                            label="gcc 512-bit, __builtin_assume_aligned")
        ax.axhline(1, color=INK2, lw=0.9)
        ax.set_xticks([0, 4, 8, 16, 32, 48, 60, 72], ["0", "4", "8", "16", "32", "48", "60", "mixed"])
        ax.set_xlabel("byte offset of every array from a 64-byte line boundary")
        ax.set_ylabel("cycles/element relative to aligned")
        ax.set_title(f"Alignment sensitivity (L1-resident): {k.replace('_simd', '')}")
        subtitle(ax, "mixed = x+0, y+4, z+8 bytes (no common alignment to peel to); 24 KiB working set; "
                     "band/bars: 95% CI")
        legend_below(ax, ncol=3)
        save(f, f"align_{k.replace('_simd', '')}_l1")

    f, ax = fig(6.8, 4.0)
    for cc, cfg in ALIGN_BUILDS[1:]:
        g = s[(s.kernel == "mul") & (s.compiler == cc) & (s.config == cfg) & (s.tag == "align-L1")]
        g = g.sort_values("offset_x")
        line(ax, g.offset_x, g.misal_64_per_elem, cfg, cc, f"{cc} {CFG_LABEL[cfg]}")
    ax.set_xticks([0, 4, 8, 16, 32, 48, 60])
    ax.set_xlabel("byte offset from a 64-byte line boundary")
    ax.set_ylabel("loads crossing a 64 B line, per element (PMU)")
    ax.set_title("Why misalignment costs: line-crossing loads (mul, L1)")
    subtitle(ax, "PMCx047 MA64; a 64-byte load crosses a line at every nonzero offset, a 32-byte load at "
                 "half of them; exact counts (no spread)")
    ax.legend(loc="center right")
    save(f, "align_line_crossing_loads")

    f, ax = fig(6.8, 4.0)
    for cc, cfg in ALIGN_BUILDS:
        base = s[(s.kernel == "mul") & (s.compiler == cc) & (s.config == cfg) & (s.tag == "align-L2")]
        g = base.sort_values("offset_x")
        sel = (raw.kernel == "mul") & (raw.compiler == cc) & (raw.config == cfg) & (raw.tag == "align-L2")
        ci = [rel_ci(raw, sel & (raw.offset_x == o), sel & (raw.offset_x == 0)) for o in g.offset_x]
        line(ax, g.offset_x, [c[0] for c in ci], cfg, cc, f"{cc} {CFG_LABEL[cfg]}",
             lo=[c[1] for c in ci], hi=[c[2] for c in ci])
    ax.axhline(1, color=INK2, lw=0.9)
    ax.set_xticks([0, 4, 8, 16, 32, 48, 60])
    ax.set_xlabel("byte offset from a 64-byte line boundary")
    ax.set_ylabel("cycles/element relative to aligned")
    ax.set_title("Alignment sensitivity when L2-resident (mul, 512 KiB)")
    subtitle(ax, "once data comes from L2 the extra line-crossing load is mostly hidden; band: 95% CI")
    legend_below(ax, ncol=2)
    save(f, "align_mul_l2")

    variants = [("gcc", "native256", "mul"), ("gcc", "native512", "mul"),
                ("clang", "native512", "mul"), ("clang", "native256", "mul_pred"),
                ("clang", "native512", "mul_pred")]
    vlabel = lambda cc, cfg, k: f"{cc} {cfg} {'masked tail (vectorize_predicate)' if k == 'mul_pred' else 'epilogue'}"  # noqa: E731
    vcol = dict(zip(variants, SLOTS))
    f, ax = fig()
    tail_rows = []
    for v in variants:
        cc, cfg, k = v
        g = s[(s.kernel == k) & (s.compiler == cc) & (s.config == cfg) & (s.tag == "tail")]
        g = g.sort_values("n")
        cyc = g.cyc_per_elem * g.n  # cycles per call
        ax.plot(g.n, cyc, color=vcol[v], ls=CC_LS[cc], marker=".", label=vlabel(cc, cfg, k))
        band(ax, g.n, g.cyc_per_elem_lo * g.n, g.cyc_per_elem_hi * g.n, vcol[v])
        full = g.n % 16 == 0
        fit = np.polyfit(g.n[full], cyc[full], 1)
        resid = cyc - np.polyval(fit, g.n)
        for n_, r_ in zip(g.n, resid):
            tail_rows.append(dict(variant=vlabel(cc, cfg, k), n=n_, rem16=n_ % 16, extra_cycles=r_))
    ax.set_xlabel("n (elements)")
    ax.set_ylabel("core cycles per call")
    ax.set_title("Tail handling: cost per call around n = 1024 (mul f32, L1)")
    subtitle(ax, "steps = remainder handled by smaller vectors / scalar loop; masked tails stay flat; "
                 "band: 95% CI")
    ax.legend(loc="upper left", fontsize=8)
    save(f, "tail_sawtooth")
    tr = pd.DataFrame(tail_rows)
    tr.groupby(["variant", "rem16"], as_index=False).extra_cycles.agg(["mean", "std", "count"]).rename(
        columns={"mean": "extra_cycles", "std": "extra_cycles_sd", "count": "n_sizes"}).to_csv(
        C.SUMMARY / "align_tail_cost.csv", index=False)

    f, ax = fig()
    for v in variants:
        g = tr[tr.variant == vlabel(*v)].groupby("rem16").extra_cycles
        sem = g.std() / np.sqrt(g.count())  # standard error of the mean over n
        ax.errorbar(g.mean().index, g.mean().values, yerr=sem.values, color=vcol[v],
                    ls=CC_LS[v[0]], marker="o", capsize=2, label=vlabel(*v))
    ax.axhline(0, color=INK2, lw=0.9)
    ax.set_xticks(range(16))
    ax.set_xlabel("n mod 16 (leftover elements after full 512-bit vectors)")
    ax.set_ylabel("extra cycles vs. linear fit through n % 16 == 0")
    ax.set_title("Quantified epilogue cost by remainder size (mul f32)")
    subtitle(ax, "n = 1024..1088; positive = tail code costs more than its share of vector work; "
                 "error bars: SE of the mean over the 4-5 n per remainder")
    legend_below(ax, ncol=2)
    save(f, "tail_cost_by_remainder")

    f, ax = fig()
    for v in [("gcc", "scalar", "mul")] + variants:
        cc, cfg, k = v
        g = s[(s.kernel == k) & (s.compiler == cc) & (s.config == cfg) & (s.tag == "small")]
        g = g.sort_values("n")
        col = vcol.get(v, MUTED)
        ax.plot(g.n, g.ns_per_call, color=col, ls=CC_LS[cc], marker=".",
                label=vlabel(cc, cfg, k) if cfg != "scalar" else "gcc scalar")
        band(ax, g.n, g.ns_per_call_lo, g.ns_per_call_hi, col)
    ax.set_xlabel("n (elements)")
    ax.set_ylabel("ns per call")
    ax.set_title("Small-n overhead: prologue / epilogue dominate short loops (mul f32)")
    subtitle(ax, "vector code only pays off once n covers the vector width plus fixed setup; band: 95% CI")
    ax.legend(loc="upper left", fontsize=8)
    save(f, "tail_small_n_overhead")


# ---------------------------------------------------------------------- stride
STRIDE_BUILDS = [("gcc", "scalar"), ("gcc", "native256"), ("gcc", "native512"), ("clang", "native512")]


def plot_stride():
    s = summary("stride")
    raw = raw_metrics("stride")
    s["regime_tag"] = s.tag.str.split(":").str[0]
    s["pat"] = s.tag.str.split(":").str[1]
    pats = ["s1", "s2", "s4", "s8", "g-identity", "g-s2", "g-s4", "g-s8", "g-random"]
    plabel = ["stride 1", "stride 2", "stride 4", "stride 8", "gather\nidx=i", "gather\nidx=2i",
              "gather\nidx=4i", "gather\nidx=8i", "gather\nrandom"]
    for fam in ("mul", "dot"):
        for reg in ("L2", "DRAM"):
            f, ax = fig(8.0, 4.2)
            x = np.arange(len(pats))
            w = 0.2
            for i, (cc, cfg) in enumerate(STRIDE_BUILDS):
                vals, los, his = [], [], []
                for p in pats:
                    g = s[(s.regime_tag == reg) & (s.pat == p) & (s.compiler == cc) &
                          (s.config == cfg) & (s.kernel.str.startswith(fam))]
                    vals.append(g.gibs.iloc[0] if len(g) else np.nan)
                    los.append(g.gibs_lo.iloc[0] if len(g) else np.nan)
                    his.append(g.gibs_hi.iloc[0] if len(g) else np.nan)
                ax.bar(x + (i - 1.5) * w, vals, w * 0.92, color=CFG_COLOR[cfg],
                       hatch=CC_HATCH[cc], edgecolor=SURFACE if cc == "gcc" else INK2,
                       linewidth=0.5, label=f"{cc} {CFG_LABEL[cfg]}", zorder=3,
                       yerr=yerr(vals, los, his), error_kw=dict(ecolor=INK, lw=0.8, capsize=1.5))
            ax.set_xticks(x, plabel, fontsize=8)
            ax.set_ylabel("effective GiB/s (useful bytes only)")
            ax.axvline(3.5, color=MUTED, lw=0.8)
            ax.set_title(f"Access pattern vs. effective bandwidth: {fam}, {reg}-resident")
            subtitle(ax, ("512 KiB" if reg == "L2" else "512 MiB") + " address span per pattern; "
                         "useful bytes = elements used (gathers also count the 4-B index); "
                         "error bars: 95% CI")
            legend_below(ax)
            save(f, f"stride_{fam}_bandwidth_{reg.lower()}")

    f, ax = fig(8.0, 4.2)
    x = np.arange(len(pats))
    for i, (fam, reg) in enumerate([(f_, r_) for f_ in ("mul", "dot") for r_ in ("L2", "DRAM")]):
        ci = []
        for p in pats:
            sel = (raw.tag == f"{reg}:{p}") & (raw.compiler == "gcc") & raw.kernel.str.startswith(fam)
            ci.append(ratio_ci_of(raw[sel & (raw.config == "native512")],
                                  raw[sel & (raw.config == "scalar")], "gflops"))
        sp = [c[0] for c in ci]
        ax.errorbar(x, sp, yerr=yerr(sp, [c[1] for c in ci], [c[2] for c in ci]), color=SLOTS[i],
                    marker="o", ls="-" if reg == "L2" else "--", capsize=2, label=f"{fam}, {reg}")
    ax.axhline(1, color=INK2, lw=0.9)
    ax.set_xticks(x, plabel, fontsize=8)
    ax.set_ylabel("speedup gcc 512-bit vs. gcc scalar")
    ax.set_yscale("log", base=2)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    ax.set_title("SIMD efficiency collapses with non-unit stride and gathers")
    subtitle(ax, "below 1 = the vector code is slower than scalar; error bars: 95% CI of the ratio")
    ax.legend()
    save(f, "stride_simd_speedup")


# ---------------------------------------------------------------------- dtype
DTYPE_BUILDS = [("gcc", "scalar"), ("gcc", "native256"), ("gcc", "native512"),
             ("clang", "scalar"), ("clang", "native512")]


def plot_dtype():
    s = summary("dtype")
    raw = raw_metrics("dtype")
    groups = [(k, d) for k in ("dot_simd", "mul", "stencil") for d in ("f32", "f64", "i32")]
    glabel = [f"{k.replace('_simd', '')}\n{d}" for k, d in groups]
    for reg, ycol, ylab, fname in (("L1", "epc", "elements per cycle", "dtype_l1_elem_per_cycle"),
                                   ("DRAM", "gibs", "GiB/s (compulsory bytes)", "dtype_dram_bandwidth")):
        f, ax = fig(8.6, 4.2)
        x = np.arange(len(groups))
        w = 0.16
        for i, (cc, cfg) in enumerate(DTYPE_BUILDS):
            vals, los, his = [], [], []
            for k, d in groups:
                g = s[(s.tag == reg) & (s.kernel == k) & (s.dtype == d) & (s.compiler == cc) &
                      (s.config == cfg)]
                if not len(g):
                    vals.append(np.nan), los.append(np.nan), his.append(np.nan)
                elif ycol == "epc":  # elements/cycle = 1 / cycles/element (CI bounds swap)
                    vals.append(1 / g.cyc_per_elem.iloc[0])
                    los.append(1 / g.cyc_per_elem_hi.iloc[0]), his.append(1 / g.cyc_per_elem_lo.iloc[0])
                else:
                    vals.append(g.gibs.iloc[0])
                    los.append(g.gibs_lo.iloc[0]), his.append(g.gibs_hi.iloc[0])
            ax.bar(x + (i - 2) * w, vals, w * 0.92, color=CFG_COLOR[cfg], hatch=CC_HATCH[cc],
                   yerr=yerr(vals, los, his), error_kw=dict(ecolor=INK, lw=0.8, capsize=1.5),
                   edgecolor=SURFACE if cc == "gcc" else INK2, linewidth=0.5,
                   label=f"{cc} {CFG_LABEL[cfg]}", zorder=3)
        for b in (2.5, 5.5):
            ax.axvline(b, color=MUTED, lw=0.8)
        ax.set_xticks(x, glabel, fontsize=8.5)
        ax.set_ylabel(ylab)
        ax.set_title(f"Data type comparison, {reg}-resident"
                     + (" (16 KiB)" if reg == "L1" else " (512 MiB)"))
        subtitle(ax, "512-bit lanes: f32 16, f64 8, i32 16; i32 multiply = vpmulld (no integer FMA); "
                     "error bars: 95% CI")
        legend_below(ax, ncol=3)
        save(f, fname)

    f, ax = fig(8.0, 4.0)
    x = np.arange(len(groups))
    for i, reg in enumerate(("L1", "L2", "DRAM")):
        ci = []
        for k, d in groups:
            sel = (raw.tag == reg) & (raw.kernel == k) & (raw.dtype == d) & (raw.compiler == "gcc")
            ci.append(ratio_ci_of(raw[sel & (raw.config == "native512")],
                                  raw[sel & (raw.config == "scalar")], "gflops"))
        sp = [c[0] for c in ci]
        ax.errorbar(x, sp, yerr=yerr(sp, [c[1] for c in ci], [c[2] for c in ci]), color=SLOTS[i],
                    marker="o", capsize=2, label=reg)
    for b in (2.5, 5.5):
        ax.axvline(b, color=MUTED, lw=0.8)
    ax.axhline(1, color=INK2, lw=0.9)
    ax.set_xticks(x, glabel, fontsize=8.5)
    ax.set_ylabel("speedup gcc 512-bit vs. gcc scalar")
    ax.set_yscale("log", base=2)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    ax.set_title("SIMD speedup by data type and cache level")
    subtitle(ax, "f64 halves the lane count; in DRAM every type converges to its bandwidth bound; "
                 "error bars: 95% CI")
    legend_below(ax, ncol=3, title="working set")
    save(f, "dtype_simd_speedup")


# ---------------------------------------------------------------------- verify
def plot_verify():
    """Hardware-counter verification. Metric: retired instructions per element
    (PMU). It falls roughly by the lane count when a loop is truly vectorized.
    (PMCx008's by-width counts are kept in the CSV but not plotted: Zen 4 also
    classifies loads/stores there by register width -- a scalar vmovss load
    counts as a 128-bit op -- so it cannot separate scalar from SSE code.)"""
    import vecinfo as V
    from matplotlib.colors import LinearSegmentedColormap, LogNorm
    s = summary("verify")
    cols = ["fp_scalar_per_elem", "fp_p128_per_elem", "fp_p256_per_elem", "fp_p512_per_elem"]
    kernels = __import__("run").VERIFY_KERNELS
    builds = [f"{c}-{f}" for c in ("gcc", "clang") for f in CFG_ORDER]
    M = np.full((len(kernels), len(builds)), np.nan)
    ann = [["" for _ in builds] for _ in kernels]
    rows = []
    for j, b in enumerate(builds):
        rep = V.report_summary(b, "f32")
        for i, k in enumerate(kernels):
            g = s[(s.build == b) & (s.kernel == k)]
            ipe = g.instructions_per_elem.dropna()
            M[i, j] = ipe.iloc[0] if len(ipe) else np.nan
            st, wb, _ = rep.get(k, ("missed", 0, []))
            ann[i][j] = f"{wb * 8}" if st == "vectorized" else "no"
            wrow = g.dropna(subset=["fp_p512_per_elem"]) if "fp_p512_per_elem" in g else g.iloc[:0]
            rows.append(dict(build=b, kernel=k, report=st, report_bits=wb * 8,
                             instructions_per_elem=M[i, j],
                             **{c: wrow[c].iloc[0] if len(wrow) else np.nan for c in cols}))
    pd.DataFrame(rows).to_csv(C.SUMMARY / "verify_vec_summary.csv", index=False)
    cmap = LinearSegmentedColormap.from_list(
        "seqblue_r", ["#0d366b", "#1c5cab", "#3987e5", "#86b6ef", "#cde2fb"])
    f, ax = plt.subplots(figsize=(9.2, 5.2))
    im = ax.imshow(M, cmap=cmap, norm=LogNorm(vmin=0.1, vmax=12), aspect="auto")
    ax.grid(False)
    for i in range(len(kernels)):
        for j in range(len(builds)):
            v = M[i, j]
            ax.text(j, i, f"{v:.2f}\n{ann[i][j]}", ha="center", va="center", fontsize=7,
                    color="#ffffff" if v < 0.9 else INK)
    ax.set_xticks(range(len(builds)), [b.replace("-", "\n") for b in builds], fontsize=7.5)
    ax.set_yticks(range(len(kernels)), kernels, fontsize=8.5)
    ax.axvline(4.5, color=SURFACE, lw=3)
    cb = f.colorbar(im, ax=ax, fraction=0.03, pad=0.02)
    cb.set_label("PMU: retired instructions per element (log)")
    ax.set_title("Vectorization verified by hardware counters (f32, L1-resident)")
    subtitle(ax, "cell: measured instructions/element over compiler-reported width ('no' = not vectorized); "
                 "darker = fewer; counts are exact (no spread)")
    save(f, "verify_counter_matrix")


# ---------------------------------------------------------------------- roofline
def plot_roofline():
    s = summary("simd")
    rf = roofs().set_index(["kernel", "level"]).gbs
    p = peak()
    ghz = p.ghz.median()
    fma = p[(p.test == "peak") & (p.dtype == "f32") & p.instr.str.startswith("vfmadd")]
    peaks = {int(r.width_bits): r.per_cycle * ghz for r in fma.itertuples()}
    f, ax = fig(7.6, 5.0)
    ai = np.logspace(-2.3, 1.5, 200)
    for lvl, ls in (("L1", ":"), ("L2", "-."), ("DRAM", "--")):
        bw = rf[("best", lvl)]  # best calib bandwidth at that level
        ax.plot(ai, np.minimum(ai * bw, peaks[512]), color=INK2, lw=1.1, ls=ls)
        ax.text(ai[8], ai[8] * bw * 1.25, f"{lvl} {bw:.0f} GB/s", fontsize=8, color=INK2,
                rotation=33, rotation_mode="anchor")
    for bits, lab in ((512, "AVX-512 / AVX2 FMA peak"), (128, "SSE FMA peak"),
                      (32, "scalar FMA peak")):
        ax.axhline(peaks[bits], color=MUTED, lw=0.9)
        ax.text(10 ** 1.45, peaks[bits] * 1.07, f"{lab} {peaks[bits]:.0f}", fontsize=8,
                color=INK2, ha="right")
    # latency ceilings for the dot reduction: one dependent vaddss/vaddps chain
    x_dot = C.arithmetic_intensity("dot_simd", "f32")
    for flop_per_3cyc, lab in ((2, "scalar: 1 dependent add / 3 cyc"),
                               (32, "gcc zmm: 1 accumulator / 3 cyc")):
        y = flop_per_3cyc / 3 * ghz
        ax.plot([x_dot / 1.6, x_dot * 1.6], [y, y], color=INK, lw=1.4)
        ax.text(x_dot * 1.7, y, lab, fontsize=7.5, va="center", color=INK)
    for k, lab in (("dot_simd", "dot"), ("mul", "mul"), ("stencil", "stencil")):
        x0 = C.arithmetic_intensity(k, "f32")
        ax.text(x0, 1.0, lab, transform=ax.get_xaxis_transform(), ha="center", va="top",
                fontsize=9, fontweight="bold")
        for cc in ("gcc",):
            for cfg in CFG_ORDER[:4]:
                g = s[(s.kernel == k) & (s.compiler == cc) & (s.config == cfg)]
                for reg, filled in (("L1", False), ("DRAM", True)):
                    h = g[g.regime == reg]
                    if reg == "DRAM":
                        h = h[h.footprint >= 256 * C.MiB]
                    y = h.gflops.median()
                    xi = x0 * (1 + 0.06 * (CFG_ORDER.index(cfg) - 1.5))
                    ax.errorbar([xi], [y], yerr=yerr([y], [h.gflops.min()], [h.gflops.max()]),
                                color=CFG_COLOR[cfg], lw=0.9, capsize=2, zorder=3, ls="none")
                    ax.scatter([xi], [y], marker=CFG_MARK[cfg], s=42, color=CFG_COLOR[cfg],
                               facecolor=CFG_COLOR[cfg] if filled else SURFACE, linewidth=1.3,
                               zorder=4)
    for cfg in CFG_ORDER[:4]:
        ax.scatter([], [], marker=CFG_MARK[cfg], color=CFG_COLOR[cfg], label=f"gcc {CFG_LABEL[cfg]}")
    ax.scatter([], [], marker="o", color=INK2, facecolor=SURFACE, label="hollow = L1-resident")
    ax.scatter([], [], marker="o", color=INK2, label="filled = DRAM-resident")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlim(10 ** -2.3, 10 ** 1.5)
    ax.set_ylim(0.5, peaks[512] * 2)
    ax.set_xlabel("arithmetic intensity (FLOP per compulsory byte, f32, no write-allocate)")
    ax.set_ylabel("GFLOP/s (one core)")
    ax.set_title("Roofline (f32): memory roofs bound mul/stencil; latency bounds dot")
    subtitle(ax, f"roofs: best single-core bandwidth per level (calib, {ghz:.2f} GHz); ceilings: FMA "
                 "microbenchmark; points: median, bars: min-max over the regime's sizes")
    ax.legend(loc="upper left", fontsize=8)
    save(f, "roofline_f32")


# ---------------------------------------------------------------------- negative
def plot_negative():
    s = summary("negative")
    variants = [("gcc", "scalar", "dot_plain", "gcc scalar"),
                ("gcc", "native512", "dot_plain", "gcc 512-bit, plain loop ('vectorized')"),
                ("clang", "native512", "dot_plain", "clang 512-bit, plain loop (not vectorized)"),
                ("gcc", "native512", "dot_simd", "gcc 512-bit + omp simd reduction"),
                ("gcc", "native512fm", "dot_plain", "gcc 512-bit + -ffast-math"),
                ("clang", "native512", "dot_simd", "clang 512-bit + omp simd reduction")]
    f, ax = fig()
    for i, (cc, cfg, k, lab) in enumerate(variants):
        g = s[(s.compiler == cc) & (s.config == cfg) & (s.kernel == k)].sort_values("footprint")
        big = cfg == "scalar"  # drawn as large hollow rings so the identical 'vectorized' line stays visible
        ax.plot(g.footprint, g.gflops, color=SLOTS[i], ls=CC_LS[cc], marker="o",
                markersize=8 if big else 4.5, markerfacecolor=SURFACE if big else SLOTS[i],
                lw=1.0 if big else 1.6, label=lab, zorder=2 if big else 3)
        band(ax, g.footprint, g.gflops_lo, g.gflops_hi, SLOTS[i])
    sc = s[(s.compiler == "gcc") & (s.config == "scalar") & (s.kernel == "dot_plain")]
    vp = s[(s.compiler == "gcc") & (s.config == "native512") & (s.kernel == "dot_plain")]
    r = (vp.set_index("n").gflops / sc.set_index("n").gflops).median()
    ax.text(1 * C.MiB, 2.9, f"gcc 'vectorized' plain loop = {r:.2f}x scalar at every size",
            fontsize=8.5, ha="center", color=INK)
    footprint_axis(ax)
    ax.set_yscale("log", base=2)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    ax.set_ylabel("GFLOP/s")
    ax.set_title("Negative example: a 'vectorized' dot product that is still serial")
    subtitle(ax, "GCC reports 'loop vectorized using 64 byte vectors' but keeps IEEE order: vmulps + 16 "
                 "serial vaddss; band: 95% CI")
    legend_below(ax)
    save(f, "negative_dot_not_really_vectorized")

    raw = raw_metrics("simd")
    f, ax = fig()
    for i, (k, cc) in enumerate((("mul", "gcc"), ("mul", "clang"), ("dot_simd", "gcc"))):
        sp = speedup_table(raw, k)
        g = sp[(sp.compiler == cc) & (sp.config == "native512")]
        ax.plot(g.footprint, g.speedup, color=SLOTS[i], ls=CC_LS[cc], marker="o",
                label=f"{k.replace('_simd', '')} {cc} 512-bit")
        ax.fill_between(g.footprint, g.lo, g.hi, color=SLOTS[i], alpha=0.15, lw=0)
    gm = speedup_table(raw, "mul")
    gm = gm[(gm.compiler == "gcc") & (gm.config == "native512") & (gm.footprint >= 256 * C.MiB)]
    ax.annotate(f"mul in DRAM: {gm.speedup.median():.2f}x\nfully vectorized, bandwidth-bound",
                (gm.footprint.median(), gm.speedup.median()), xytext=(1 * C.MiB, 4.2),
                textcoords="data", fontsize=8.5, arrowprops=dict(arrowstyle="-", color=INK2))
    ax.axhline(1, color=INK2, lw=0.9)
    footprint_axis(ax)
    ax.set_ylabel("speedup vs. same-compiler scalar")
    ax.set_ylim(bottom=0)
    ax.set_title("Negative example: vectorized but memory-bound (elementwise multiply)")
    subtitle(ax, "zmm code throughout (verify); dot for contrast: its scalar baseline is latency-bound, "
                 "not memory-bound; band: 95% CI")
    legend_below(ax, ncol=3)
    save(f, "negative_mul_memory_bound")


# ---------------------------------------------------------------------- multiacc
def plot_multiacc():
    s = summary("multiacc")
    ser = s[s.tag == "serial"].copy()
    ser["cyc_per_call"] = ser.cyc_per_elem * ser.n
    rows = []
    for (b, k), g in ser.groupby(["build", "kernel"]):
        (slope, icpt), cov = np.polyfit(g.n, g.cyc_per_call, 1, cov=True)
        se = float(np.sqrt(cov[0, 0]))  # standard error of the fitted slope
        rows.append(dict(build=b, kernel=k, K=int(k[-1]) if k[-1].isdigit() else 1,
                         cyc_per_elem=slope, cyc_per_elem_se=se, elem_per_cycle=1 / slope,
                         elem_per_cycle_lo=1 / (slope + 1.96 * se),
                         elem_per_cycle_hi=1 / (slope - 1.96 * se), fixed_cycles=icpt))
    fit = pd.DataFrame(rows)
    b2b = s[s.tag == "b2b"][["build", "kernel", "cyc_per_elem"]].rename(
        columns={"cyc_per_elem": "b2b_cyc_per_elem"})
    fit = fit.merge(b2b, on=["build", "kernel"], how="left")
    fit.to_csv(C.SUMMARY / "multiacc_fit.csv", index=False)
    f, ax = fig(6.8, 4.4)
    for b in ["gcc-scalar", "gcc-default", "gcc-native256", "gcc-native512", "clang-scalar",
              "clang-native256", "clang-native512"]:
        cc, cfg = b.split("-", 1)
        g = fit[fit.build == b].sort_values("K")
        line(ax, g.K, g.elem_per_cycle, cfg, cc, f"{cc} {CFG_LABEL[cfg]}",
             lo=g.elem_per_cycle_lo, hi=g.elem_per_cycle_hi)
    ax.axhline(8, color=INK, lw=1, ls=":")
    ax.text(1.05, 8.15, "L1 load bound: 2 x 32 B/cycle = 8 f32 elem/cycle", fontsize=8)
    ax.set_ylim(0, 9)
    ax.set_xscale("log", base=2)
    ax.set_xticks([1, 2, 4, 8], ["1", "2", "4", "8"])
    ax.set_xlabel("independent accumulators in the source (K)")
    ax.set_ylabel("steady-state elements per cycle")
    ax.set_title("Multi-accumulator dot product: latency-bound until the loads saturate")
    subtitle(ax, "slope of cycles/call vs n (n = 512..3584, L1, lfence between calls); FADD latency 3 "
                 "cycles; band: 95% CI of the fit")
    legend_below(ax)
    save(f, "multiacc_dot")


PLOTS = {"calib": plot_calib, "simd": plot_simd, "locality": plot_locality, "align": plot_align,
         "stride": plot_stride, "dtype": plot_dtype, "verify": plot_verify, "roofline": plot_roofline,
         "negative": plot_negative, "multiacc": plot_multiacc}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("exps", nargs="+")
    a = ap.parse_args()
    exps = list(PLOTS) if a.exps == ["all"] else [C.exp_id(e) for e in a.exps]
    for e in exps:
        print(e)
        PLOTS[e]()


if __name__ == "__main__":
    main()
