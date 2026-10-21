#!/usr/bin/env python3
"""Make every figure: one graph per JPG in results/plots/.

  python scripts/plot.py all      (or E1 E2 ...)

Style and helpers are proj1's (scripts/plot.py there): validated categorical
palette in fixed order, markers on every series (several slots are below 3:1
contrast on the light surface, so identity never rests on color alone),
recessive grid, one y-axis per chart, footprint axes shaded by cache level.
Numbers come from results/summary/<EXP>.csv (the table view of every figure).
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
SLOTS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#4a3aa7", "#008300", "#e34948"]
MARKS = ["o", "s", "^", "D", "v", "P", "X", "*"]
BAND = "#f1f0ec"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": MUTED, "axes.labelcolor": INK, "text.color": INK, "xtick.color": INK2,
    "ytick.color": INK2, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "axes.spines.top": False, "axes.spines.right": False, "font.size": 10,
    "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlelocation": "left",
    "axes.titlepad": 20, "legend.frameon": False, "legend.fontsize": 8.5,
    "lines.linewidth": 1.6, "lines.markersize": 4.5, "figure.dpi": 160,
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
            return f"{q:.0f} {unit}" if abs(q - round(q)) < 1e-6 else f"{q:.1f} {unit}"
    return f"{x:.0f} B"


def legend_below(ax, ncol=2, **kw):
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.24), ncol=ncol, fontsize=8, **kw)


def plain_log_y(ax):
    """Log y-axis with plain numbers (no 4x10^1 style labels)."""
    ax.set_yscale("log")
    f = FuncFormatter(lambda v, _: f"{v:g}")
    ax.yaxis.set_major_formatter(f)
    ax.yaxis.set_minor_formatter(FuncFormatter(
        lambda v, _: f"{v:g}" if str(f"{v:g}")[0] in "25" else ""))


def bytes_axis(ax, lo, hi, label="working set", spans=None, step=2):
    """log2 byte axis; spans = [(name, a, b)] shaded alternately and labelled."""
    ax.set_xscale("log", base=2)
    ticks = [2 ** i for i in range(int(np.ceil(np.log2(lo))), int(np.log2(hi * 1.15)) + 1, step)]
    ax.set_xticks(ticks)
    ax.xaxis.set_major_formatter(FuncFormatter(fmt_bytes))
    ax.xaxis.set_minor_locator(NullLocator())
    ax.set_xlim(lo / 1.15, hi * 1.15)
    ax.set_xlabel(label)
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")
    for i, (name, a, b) in enumerate(spans or []):
        a, b = max(a, lo / 1.15), min(b, hi * 1.15)
        if a >= b:
            continue
        if i % 2 == 0:
            ax.axvspan(a, b, color=BAND, zorder=0, lw=0)
        ax.text(np.sqrt(a * b), 1.0, name, transform=ax.get_xaxis_transform(), ha="center",
                va="top", fontsize=8, color=INK2)


CORE_SPANS = [("L1", 0, C.CACHE["L1"]), ("L2", C.CACHE["L1"], C.CACHE["L2"]),
              ("L3 (CCD)", C.CACHE["L2"], C.CACHE["L3"]), ("DRAM", C.CACHE["L3"], 64 * C.GiB)]
NODE_T = 31
NODE_SPANS = [("Σ L1", 0, NODE_T * C.CACHE["L1"]),
              ("Σ L2", NODE_T * C.CACHE["L1"], NODE_T * C.CACHE["L2"]),
              ("Σ L3", NODE_T * C.CACHE["L2"], 4 * C.CACHE["L3"]),
              ("DRAM", 4 * C.CACHE["L3"], 64 * C.GiB)]


def summary(exp):
    p = C.SUMMARY / f"{exp}.csv"
    if not p.exists():
        raw, run = C.load_raw(exp)
        S.summarize(raw).assign(run=run).to_csv(p, index=False)
    return pd.read_csv(p, keep_default_na=False, na_values=[""])


def raw_metrics(exp):
    raw, _ = C.load_raw(exp)
    return C.add_metrics(raw)


def series(ax, x, y, i, label, ls="-", lo=None, hi=None, **kw):
    o = np.argsort(np.asarray(x))
    x, y = np.asarray(x)[o], np.asarray(y)[o]
    ax.plot(x, y, color=SLOTS[i], marker=MARKS[i], ls=ls, label=label, **kw)
    if lo is not None and hi is not None:
        ax.fill_between(x, np.asarray(lo)[o], np.asarray(hi)[o], color=SLOTS[i], alpha=0.15, lw=0)


def bars(ax, labels, groups, ylabel, fmt="{:.0f}", log=False, width=0.8):
    """groups: [(series label, values)] -> grouped bars with value labels."""
    n = len(groups)
    x = np.arange(len(labels))
    w = width / n
    for i, (name, vals) in enumerate(groups):
        pos = x - width / 2 + w * (i + 0.5)
        b = ax.bar(pos, vals, w * 0.92, color=SLOTS[i], label=name, zorder=2)
        for r, v in zip(b, vals):
            if np.isfinite(v):
                ax.annotate(fmt.format(v), (r.get_x() + r.get_width() / 2, v), xytext=(0, 2),
                            textcoords="offset points", ha="center", va="bottom", fontsize=7,
                            color=INK2)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel(ylabel)
    if log:
        plain_log_y(ax)
    ax.grid(axis="x", visible=False)
    if n > 1:
        ax.legend(fontsize=8)


def e1_latency():
    """Median ns/load per level from E1 (2 MiB pages, local)."""
    s = summary("E1")
    out = {}
    for lv in ("L1", "L2", "L3", "DRAM"):
        r = s[s.tag == f"{lv}:2m"]
        out[lv] = float(r.ns_per_load.iloc[0])
    return out


# ------------------------------------------------------------------------ E1
def plot_e1():
    s = summary("E1")
    rows = []
    for lv, name in (("L1", "L1d"), ("L2", "L2"), ("L3", "L3 (own CCD)"), ("DRAM", "DRAM local"),
                     ("DRAM-remote", "DRAM remote")):
        r2 = s[s.tag == f"{lv}:2m"].iloc[0]
        r4 = s[s.tag == f"{lv}:4k"]
        ns4 = float(r4.ns_per_load.iloc[0]) if len(r4) else np.nan
        src = {"L1": 1 - r2.get("l1d_miss_per_load", np.nan), "L2": r2.get("fill_l2_per_load"),
               "L3": r2.get("fill_ccx_per_load"), "DRAM": r2.get("fill_dram_per_load"),
               "DRAM-remote": r2.get("fill_dram_per_load")}[lv]
        rows.append([name, fmt_bytes(r2["size"]), f"{r2.ns_per_load:.2f}",
                     f"[{r2.ns_per_load_lo:.2f}, {r2.ns_per_load_hi:.2f}]",
                     f"{r2.cyc_per_load:.1f}", f"{r2.ghz:.2f}", "—" if np.isnan(ns4) else f"{ns4:.2f}",
                     f"{100 * src:.0f}%"])
    f, ax = fig(9.6, 2.6)
    ax.axis("off")
    cols = ["level", "working set", "ns / load", "95% CI (ns)", "cycles / load", "GHz",
            "ns, 4 KiB pages", "served by level"]
    t = ax.table(cellText=rows, colLabels=cols, loc="center", cellLoc="center",
                 colWidths=[0.15, 0.11, 0.1, 0.17, 0.11, 0.08, 0.14, 0.14])
    t.auto_set_font_size(False)
    t.set_fontsize(8)
    t.scale(1, 1.5)
    for (r, c), cell in t.get_celld().items():
        cell.set_edgecolor(GRID)
        if r == 0:
            cell.set_text_props(weight="bold")
            cell.set_facecolor(BAND)
    ax.set_title("Zero-queue load-to-use latency per level", pad=8)
    ax.text(0, -0.05, "Random 64 B pointer chase, 1 pinned core, 2 MiB pages unless noted; cycles = PMU "
            "core cycles (boost on). Fill-source PMU counters confirm where each probe lives.",
            transform=ax.transAxes, fontsize=7.5, color=INK2, va="top")
    save(f, "e1_latency_table")

    f, ax = fig(7.2, 4.2)
    labels = ["L1d\n16 KiB", "L2\n512 KiB", "L3\n16 MiB", "DRAM\n1 GiB", "DRAM remote\n1 GiB"]
    tags = ["L1", "L2", "L3", "DRAM", "DRAM-remote"]
    v2 = [float(s[s.tag == f"{t}:2m"].ns_per_load.iloc[0]) for t in tags]
    v4 = [float(s[s.tag == f"{t}:4k"].ns_per_load.iloc[0]) if (s.tag == f"{t}:4k").any() else np.nan
          for t in tags]
    bars(ax, labels, [("2 MiB pages", v2), ("4 KiB pages", v4)], "ns per load (log)",
         fmt="{:.1f}", log=True)
    ax.set_title("Load latency by level")
    subtitle(ax, "random pointer chase, idle machine; 4 KiB pages add page walks once the "
             "set exceeds the 12 MiB L2-DTLB reach")
    save(f, "e1_latency_by_level")


# ------------------------------------------------------------------------ E2
def plot_e2():
    s = summary("E2")
    lat = s[s.tag == "lat"].sort_values("size")
    f, ax = fig()
    series(ax, lat["size"], lat.ns_per_load, 0, "random chase", lo=lat.ns_per_load_lo,
           hi=lat.ns_per_load_hi)
    plain_log_y(ax)
    ax.set_ylabel("ns per load (log)")
    bytes_axis(ax, lat["size"].min(), lat["size"].max(), spans=CORE_SPANS)
    for lv, v in e1_latency().items():
        ax.axhline(v, color=MUTED, lw=0.7, ls=":", zorder=1)
        left = lv == "DRAM"  # the data hug the right end of the DRAM line
        ax.text(lat["size"].min() if left else lat["size"].max() * 1.1, v * 0.93,
                f"E1 {lv}: {v:.1f} ns", fontsize=7, color=INK2, ha="left" if left else "right",
                va="top")
    ax.set_title("Latency vs working set")
    subtitle(ax, "1 pinned core, 2 MiB pages; shaded bands are the sysfs capacities "
             "(32 KiB L1d, 1 MiB L2, 32 MiB L3 per CCD)")
    save(f, "e2_latency_vs_working_set")

    f, ax = fig()
    for i, (c, name) in enumerate((("fill_l2_per_load", "from L2"), ("fill_ccx_per_load", "from L3 (own CCD)"),
                                   ("fill_other_ccx_per_load", "from another CCD's cache"),
                                   ("fill_dram_per_load", "from DRAM"))):
        if c in lat:
            series(ax, lat["size"], lat[c], i, name)
    if "l1d_miss_per_load" in lat:
        series(ax, lat["size"], 1 - lat.l1d_miss_per_load, 4, "L1 hit")
    ax.set_ylabel("fraction of loads")
    ax.set_ylim(-0.03, 1.05)
    bytes_axis(ax, lat["size"].min(), lat["size"].max(), spans=CORE_SPANS)
    ax.set_title("Where each load is served from")
    subtitle(ax, "PMU LsAnyFillsFromSys (0x44) per load of the random chase; transitions "
             "line up with the cache capacities")
    legend_below(ax, ncol=3)
    save(f, "e2_fill_source_vs_working_set")

    for tag, name, spans, xl in (("bw1", "core", CORE_SPANS, "working set"),
                                 ("bwnode", "node", NODE_SPANS, "working set (all 31 threads)")):
        b = s[s.tag == tag].copy()
        if b.empty:
            continue
        b["ws"] = b["size"] * b["gens"]
        b = b.sort_values("ws")
        f, ax = fig()
        series(ax, b.ws, b.gbs_useful, 0 if tag == "bw1" else 1, "sequential read",
               lo=b.gbs_useful_lo, hi=b.gbs_useful_hi)
        ax.set_ylabel("read bandwidth (GB/s)" + (" (log)" if tag == "bwnode" else ""))
        if tag == "bwnode":
            plain_log_y(ax)
        else:
            ax.set_ylim(bottom=0)
        bytes_axis(ax, b.ws.min(), b.ws.max(), label=xl, spans=spans)
        if tag == "bw1":
            ax.set_title("Single-core read bandwidth vs working set")
            subtitle(ax, "AVX-512 loads, 4 accumulators, 1 pinned core, 2 MiB pages")
        else:
            ax.axhline(C.DRAM_PEAK_GBS, color=MUTED, ls="--", lw=0.8)
            ax.text(b.ws.min(), C.DRAM_PEAK_GBS * 0.97, "DDR5-4800 x 12 ch peak 460.8 GB/s",
                    fontsize=7, color=INK2, va="top")
            ax.set_title("Whole-node read bandwidth vs working set")
            subtitle(ax, "31 threads (one per core, round-robin over 4 CCDs), private slices; "
                         "bands = summed capacities")
        save(f, f"e2_bandwidth_vs_working_set_{name}")


# ------------------------------------------------------------------------ E3
def plot_e3():
    s = summary("E3")
    lat = s[s.tag.str.startswith("lat:")]
    labels, g16, g1g = [], [], []
    for pat in ("seq", "random"):
        for st in (64, 256, 1024):
            labels.append(f"{'sequential' if pat == 'seq' else 'random'}\n{st} B")
            r = lat[(lat.pattern == pat) & (lat.stride == st)]
            g16.append(float(r[r["size"] == 16 * C.MiB].ns_per_load.iloc[0]))
            g1g.append(float(r[r["size"] == C.GiB].ns_per_load.iloc[0]))
    f, ax = fig(7.6, 4.2)
    bars(ax, labels, [("16 MiB (L3)", g16), ("1 GiB (DRAM)", g1g)], "ns per load (log)",
         fmt="{:.1f}", log=True)
    ax.set_title("Dependent-load latency by access pattern and stride")
    subtitle(ax, "pointer chase, 1 core: constant strides are prefetched (even 1 KiB apart); "
                 "random order exposes the full latency")
    save(f, "e3_latency_by_pattern")

    for who, name in (("core", "1 core, 1 GiB buffer"), ("node", "31 threads, 64 MiB each")):
        b = s[s.tag == f"bw:{who}:stride"]
        if b.empty:
            continue
        labels = ["64 B", "256 B", "1024 B"]
        groups = []
        for order in ("seq", "random"):
            vals = [float(b[(b.order == order) & (b.stride == st)].gbs_useful.iloc[0])
                    for st in (64, 256, 1024)]
            groups.append(("sequential" if order == "seq" else "random", vals))
        f, ax = fig()
        bars(ax, labels, groups, "useful bandwidth (GB/s)")
        ax.set_xlabel("stride between 64 B accesses")
        ax.set_title(f"Read bandwidth by stride and order ({who})")
        subtitle(ax, f"{name}; one 64 B line used per access, so useful = line bandwidth")
        save(f, f"e3_bandwidth_stride_{who}")

        d = s[s.tag == f"bw:{who}:dense"].sort_values("gran")
        p = s[s.tag == f"bw:{who}:partial"].sort_values("gran")
        f, ax = fig()
        series(ax, d.gran, d.gbs_useful, 0, "dense (stride = granularity): useful")
        series(ax, p.gran, p.gbs_useful, 1, "one access per 64 B line: useful")
        series(ax, p.gran, p.gbs_lines, 2, "one access per 64 B line: lines moved", ls="--")
        ax.set_xscale("log", base=2)
        ax.set_xticks([8, 16, 32, 64])
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f} B"))
        ax.xaxis.set_minor_locator(NullLocator())
        ax.set_xlabel("access granularity (bytes per load)")
        ax.set_ylabel("bandwidth (GB/s)")
        ax.set_ylim(bottom=0)
        ax.set_title(f"Read bandwidth by access granularity ({who})")
        subtitle(ax, f"{name}, DRAM-resident; DRAM always moves whole lines")
        legend_below(ax, ncol=1)
        save(f, f"e3_bandwidth_granularity_{who}")


# ------------------------------------------------------------------------ E4
MIX_ORDER = ["1:0", "0:1", "0:1:nt", "7:3", "7:3:nt", "1:1", "1:1:nt"]
MIX_LABEL = {"1:0": "100%\nread", "0:1": "100%\nwrite", "0:1:nt": "100%\nwrite NT",
             "7:3": "70/30", "7:3:nt": "70/30\nNT", "1:1": "50/50", "1:1:nt": "50/50\nNT"}


def plot_e4():
    s = summary("E4")
    for who, name in (("node", "31 threads"), ("core", "1 core")):
        b = s[s.tag.str.startswith(f"{who}:")]
        order = [m for m in MIX_ORDER if (b.tag == f"{who}:{m}").any()]
        useful = [float(b[b.tag == f"{who}:{m}"].gbs_useful.iloc[0]) for m in order]
        traffic = [float(b[b.tag == f"{who}:{m}"].gbs_traffic_model.iloc[0]) for m in order]
        f, ax = fig(7.6, 4.2)
        bars(ax, [MIX_LABEL[m] for m in order],
             [("bytes the program read+wrote", useful),
              ("DRAM traffic incl. read-for-ownership (model)", traffic)], "GB/s")
        if who == "node":
            ax.axhline(C.DRAM_PEAK_GBS, color=MUTED, ls="--", lw=0.8)
            ax.text(len(order) - 0.6, C.DRAM_PEAK_GBS * 0.98, "DDR5 peak 460.8 GB/s",
                    fontsize=7.5, color=INK2, ha="right", va="top")
        ax.set_title(f"Bandwidth by read/write mix ({who})")
        subtitle(ax, f"{name}, sequential 64 B accesses, DRAM-resident; normal stores read each "
                 "line before writing it, NT stores don't")
        legend_below(ax, ncol=1)
        save(f, f"e4_rw_mix_bandwidth_{who}")
    b = s[s.tag.str.startswith("loaded:")]
    order = [m for m in MIX_ORDER if (b.tag == f"loaded:{m}").any()]
    lat = [float(b[b.tag == f"loaded:{m}"].lat_ns.iloc[0]) for m in order]
    idle = float(s[s.tag == "idle"].lat_ns.iloc[0])
    f, ax = fig(7.6, 4.2)
    bars(ax, [MIX_LABEL[m] for m in order], [("loaded latency", lat)], "ns per load")
    ax.axhline(idle, color=MUTED, ls="--", lw=0.8)
    ax.text(len(order) - 0.6, idle * 0.97, f"idle {idle:.0f} ns", fontsize=7.5, color=INK2,
            ha="right", va="top", bbox=dict(fc=SURFACE, ec="none", pad=1))
    ax.set_title("Loaded DRAM latency at full load, by mix")
    subtitle(ax, "random-chase probe on 1 core while 30 cores generate the mix at full rate")
    save(f, "e4_rw_mix_loaded_latency")


# ------------------------------------------------------------------------ E5
def plot_e5():
    s = summary("E5")
    f, ax = fig(7.4, 4.6)
    knees = {}
    allbw = []
    for i, rw in enumerate(("1:0", "7:3", "1:1")):
        d = s[s.tag == f"delay:{rw}"]
        g = s[(s.tag == f"gens:{rw}") & (s.gens == 30)]  # delay 0 point
        c = pd.concat([d, g]).sort_values("delay", ascending=False)  # rising intensity
        lab = {"1:0": "100% read", "7:3": "70/30 R/W", "1:1": "50/50 R/W"}[rw]
        ax.plot(c.gbs_total, c.lat_ns, color=SLOTS[i], marker=MARKS[i],
                label=f"{lab} (30 generators, delay sweep)")
        gg = s[s.tag == f"gens:{rw}"].sort_values("gbs_total")
        ax.plot(gg.gbs_total, gg.lat_ns, ls="none", marker=MARKS[i], mfc=SURFACE, mec=SLOTS[i],
                ms=6, label=f"{lab} (1-30 generators, no delay)")
        allbw += list(gg.gbs_total)
        k = C.knee(c.gbs_total.values, c.lat_ns.values)
        knees[rw] = (c.gbs_total.values[k], c.lat_ns.values[k])
        ax.plot(*knees[rw], marker="*", ms=13, color=SLOTS[i], mec=INK, mew=0.6, zorder=5)
        allbw += list(c.gbs_total)
    txt = "knees (★): " + ", ".join(
        f"{({'1:0': 'read', '7:3': '70/30', '1:1': '50/50'})[rw]} {x:.0f} GB/s @ {y:.0f} ns"
        for rw, (x, y) in knees.items())
    ax.text(0.02, 0.97, txt, transform=ax.transAxes, fontsize=7.5, color=INK, va="top")
    xmax = max(allbw) * 1.08
    xs = np.linspace(5, xmax, 200)
    for n in (128, 256, 512, 1024):
        ax.plot(xs, n * C.LINE / xs, color=MUTED, lw=0.6, ls=":")
        y = n * C.LINE / xmax
        ax.text(xmax, y, f"{n} in flight", fontsize=7, color=INK2, va="bottom", ha="right")
    ax.set_xlim(0, xmax)
    ax.set_ylim(0, s.lat_ns.max() * 1.1)
    ax.set_xlabel("memory bandwidth (GB/s, all cores incl. probe)")
    ax.set_ylabel("loaded latency (ns)")
    ax.set_title("Throughput vs latency as intensity grows")
    subtitle(ax, "MLC-style loaded latency on one node; dotted = Little's Law curves "
                 "latency = in-flight lines x 64 B / bandwidth")
    legend_below(ax, ncol=2)
    save(f, "e5_latency_vs_bandwidth")

    f, ax = fig()
    for i, rw in enumerate(("1:0", "7:3", "1:1")):
        d = s[s.tag.isin([f"delay:{rw}"]) | ((s.tag == f"gens:{rw}") & (s.gens == 30))]
        d = d.sort_values("delay", ascending=False)
        n = C.little_outstanding(d.gbs_total, d.lat_ns)
        ax.plot(d.gbs_total, n, color=SLOTS[i], marker=MARKS[i],
                label={"1:0": "100% read", "7:3": "70/30", "1:1": "50/50"}[rw])
    ax.set_xlabel("memory bandwidth (GB/s)")
    ax.set_ylabel("lines in flight  L = λ·W")
    ax.set_title("Little's Law: concurrency needed for each bandwidth")
    subtitle(ax, "L = bandwidth/64 B x loaded latency; past the knee more concurrency buys "
                 "latency, not bandwidth")
    ax.legend()
    save(f, "e5_littles_law_concurrency")

    for lv, i in (("DRAM", 0), ("L3", 1)):
        m = s[s.tag == f"mlp:{lv}"].sort_values("chains")
        if m.empty:
            continue
        f, ax = fig()
        gbs = m.loads_per_ns * C.LINE
        series(ax, m.chains, gbs, i, "measured")
        lat1 = float(m[m.chains == 1].ns_per_step.iloc[0])
        ideal = C.little_bandwidth(m.chains, lat1)
        ax.plot(m.chains, ideal, color=MUTED, ls="--", lw=1, label=f"Little's Law, latency fixed at {lat1:.0f} ns")
        sat = gbs.max()
        k_sat = int(m.chains[gbs >= 0.97 * sat].min())
        nmax = C.little_outstanding(sat, lat1)  # concurrency the plateau represents
        ax.axhline(sat, color=MUTED, lw=0.6, ls=":")
        ax.text(1, sat * 1.02, f"plateau {sat:.1f} GB/s from k ≈ {k_sat}: Little's Law with the "
                f"idle {lat1:.0f} ns gives ≈ {nmax:.0f} misses in flight", fontsize=7.5,
                color=INK2, va="bottom")
        ax.set_ylim(0, max(sat * 1.25, 1))
        ax.set_xlabel("independent pointer chains on one core (k)")
        ax.set_ylabel("bandwidth (GB/s)")
        ax.set_title(f"Single-core memory-level parallelism ({lv})")
        subtitle(ax, f"k random chases in lockstep over {fmt_bytes(m['size'].iloc[0])}; "
                     "the plateau is the per-core miss-handling limit")
        ax.legend()
        save(f, f"e5_mlp_{lv.lower()}")


# ------------------------------------------------------------------------ E6
PAT_I = {"contig": 0, "stride": 1, "gather": 2}
PAT_LABEL = {"contig": "contiguous", "stride": "stride 8 (1 elem per line)", "gather": "random gather"}


def plot_e6():
    s = summary("E6")
    s = s[s.ns_per_elem.notna()]
    f, ax = fig(7.4, 4.4)
    for k, ls in (("mul", "-"), ("axpy", "--")):
        for p, i in PAT_I.items():
            d = s[(s.kernel == k) & (s.pattern == p)].sort_values("footprint")
            series(ax, d.footprint, d.ns_per_elem, i, f"{k} {PAT_LABEL[p]}", ls=ls)
    plain_log_y(ax)
    ax.set_ylabel("ns per element (log)")
    bytes_axis(ax, s.footprint.min(), s.footprint.max(), label="footprint (all arrays)",
               spans=CORE_SPANS)
    ax.set_title("Kernel runtime vs footprint")
    subtitle(ax, "z = x·y (solid) and y = a·x + y (dashed), f64, 1 core, -O3 -march=native")
    legend_below(ax, ncol=2)
    save(f, "e6_runtime_vs_footprint")

    for col, xl, name in (("l1d_miss_per_elem", "L1D misses per element", "l1_misses"),
                          ("l2_miss_dc_per_elem", "L2 demand misses per element", "l2_misses"),
                          ("dram_lines_per_elem", "DRAM lines per element (L2-prefetch + demand fills)",
                           "dram_lines")):
        d = s.copy()
        if col == "dram_lines_per_elem":
            d[col] = d.get("l2pf_miss_l3_per_elem", 0) + d.get("fill_dram_per_elem", 0)
        if col not in d or d[col].isna().all():
            continue
        f, ax = fig()
        for k, mk in (("mul", "o"), ("axpy", "s")):
            for p, i in PAT_I.items():
                q = d[(d.kernel == k) & (d.pattern == p)]
                ax.scatter(q[col], q.ns_per_elem, color=SLOTS[i], marker=mk, s=22,
                           edgecolor=SURFACE, lw=0.6, label=f"{k} {PAT_LABEL[p]}", zorder=3)
        ok = d[[col, "ns_per_elem"]].dropna()
        r = np.corrcoef(ok[col], ok.ns_per_elem)[0, 1]
        ax.set_xlabel(xl)
        ax.set_ylabel("ns per element")
        ax.set_title(f"Runtime vs {xl.split(' per')[0]}")
        subtitle(ax, f"every footprint 16 KiB-1 GiB; Pearson r = {r:.2f} over all points")
        legend_below(ax, ncol=2)
        save(f, f"e6_runtime_vs_{name}")

    lat = e1_latency()
    need = ["fill_l2_per_elem", "fill_ccx_per_elem", "fill_dram_per_elem"]
    if all(c in s for c in need):
        d = s.dropna(subset=need).copy()
        acc = np.where(d.pattern == "contig", 3 / 4, 3.0)  # ymm: 4 f64 per access
        other = d.get("fill_other_ccx_per_elem", 0).fillna(0)
        d["amat"] = C.amat_from_fills(acc, {"L2": d.fill_l2_per_elem,
                                            "L3": d.fill_ccx_per_elem + other,
                                            "DRAM": d.fill_dram_per_elem}, lat)
        f, ax = fig(6.4, 5.0)
        for p, i in PAT_I.items():
            q = d[d.pattern == p]
            ax.scatter(q.amat, q.ns_per_elem, color=SLOTS[i], marker=MARKS[i], s=24,
                       edgecolor=SURFACE, lw=0.6, label=PAT_LABEL[p], zorder=3)
        lo = min(d.amat.min(), d.ns_per_elem.min()) / 1.3
        hi = max(d.amat.max(), d.ns_per_elem.max()) * 1.3
        ax.plot([lo, hi], [lo, hi], color=MUTED, lw=0.8, ls="--")
        for fct in (4, 16):
            ax.plot([lo, hi], [lo / fct, hi / fct], color=MUTED, lw=0.5, ls=":")
            ax.text(hi, hi / fct, f"{fct}x overlap", fontsize=7, color=INK2, ha="right", va="bottom")
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlim(lo, hi)
        ax.set_ylim(lo, hi)
        ax.set_xlabel("AMAT prediction, serial (ns per element)")
        ax.set_ylabel("measured (ns per element)")
        ax.set_title("AMAT (no overlap) vs measured runtime")
        subtitle(ax, "serial AMAT from PMU fill sources x E1 idle latencies; distance below the "
                     "diagonal = accesses overlapped (MLP)")
        ax.legend()
        save(f, "e6_amat_vs_measured")


# ------------------------------------------------------------------------ E7
def plot_e7():
    s = summary("E7")
    pr = s[s.tag.str.startswith("pagerand:")].copy()
    pr["npages"] = pr["size"] // 4096
    f, ax = fig()
    for i, pg in enumerate(("4k", "2m")):
        d = pr[pr.pages == pg].sort_values("npages")
        series(ax, d.npages, d.ns_per_load, i, f"{'4 KiB' if pg == '4k' else '2 MiB'} pages",
               lo=d.ns_per_load_lo, hi=d.ns_per_load_hi)
    for n, name in ((C.DTLB["L1"], "L1 DTLB: 72 entries"), (C.DTLB["L2"], "L2 DTLB: 3072 entries")):
        ax.axvline(n, color=MUTED, ls="--", lw=0.8)
        ax.text(n * 1.08, 0.97, name, transform=ax.get_xaxis_transform(), fontsize=7.5,
                color=INK2, va="top")
    ax.set_xscale("log", base=2)
    ax.set_xlabel("distinct 4 KiB pages touched (one line each, random order)")
    ax.set_ylabel("ns per load")
    ax.set_ylim(bottom=0)
    ax.set_title("TLB reach: latency vs pages touched")
    subtitle(ax, "one 64 B line per page: both curves carry the same cache misses, so the gap "
                 "between them is the TLB cost")
    ax.legend()
    save(f, "e7_tlb_pagerand_latency")

    f, ax = fig()
    k = 0
    for pg in ("4k", "2m"):
        d = pr[pr.pages == pg].sort_values("npages")
        for c, nm in (("dtlb_miss_per_load", "L1 DTLB misses"), ("dtlb_l2_miss_per_load", "L2 DTLB misses (walks)")):
            series(ax, d.npages, d[c], k, f"{nm}, {'4 KiB' if pg == '4k' else '2 MiB'}",
                   ls="-" if pg == "4k" else "--")
            k += 1
    for n in C.DTLB.values():
        ax.axvline(n, color=MUTED, ls="--", lw=0.8)
    ax.set_xscale("log", base=2)
    ax.set_xlabel("distinct 4 KiB pages touched")
    ax.set_ylabel("misses per load")
    ax.set_title("DTLB misses per load vs pages touched")
    subtitle(ax, "PMU LsL1DTlbMiss (0x45): all reloads vs L2-TLB misses")
    legend_below(ax, ncol=2)
    save(f, "e7_tlb_pagerand_misses")

    rc = s[s.tag.str.startswith("random:")]
    f, ax = fig()
    for i, pg in enumerate(("4k", "2m")):
        d = rc[rc.pages == pg].sort_values("size")
        series(ax, d["size"], d.ns_per_load, i, f"{'4 KiB' if pg == '4k' else '2 MiB'} pages")
    marks = [(C.TLB_REACH[("L1", "4k")], "L1 TLB reach 4K"), (C.TLB_REACH[("L2", "4k")], "L2 TLB reach 4K"),
             (C.TLB_REACH[("L1", "2m")], "L1 TLB reach 2M")]
    for x, nm in marks:
        ax.axvline(x, color=MUTED, ls=":", lw=0.8)
        ax.text(x * 1.08, 0.97, nm, transform=ax.get_xaxis_transform(), fontsize=7, color=INK2,
                va="top", rotation=90)
    plain_log_y(ax)
    ax.set_ylabel("ns per load (log)")
    bytes_axis(ax, rc["size"].min(), rc["size"].max(), spans=CORE_SPANS)
    ax.set_title("Random-chase latency: 4 KiB vs 2 MiB pages")
    subtitle(ax, "same data, same order; the gap is page-walk time")
    ax.legend(loc="lower right")
    save(f, "e7_tlb_random_chase")

    a = rc[rc.pages == "4k"].set_index("size")
    b = rc[rc.pages == "2m"].set_index("size")
    j = a.join(b, lsuffix="_4k", rsuffix="_2m", how="inner")
    if len(j):
        dx = j.dtlb_l2_miss_per_load_4k - j.dtlb_l2_miss_per_load_2m
        dy = j.ns_per_load_4k - j.ns_per_load_2m
        # DRAM-resident sets with enough walks per load for a stable per-walk figure
        # (inside L3, 4 KiB pages also cost cache capacity, which would be misattributed)
        big = (dx > 0.5) & (dx.index >= 2 * C.CACHE["L3"])
        if big.sum() >= 2:
            f, ax = fig()
            per = dy[big] / dx[big]
            series(ax, per.index, per.values, 0, "extra ns per L2-DTLB miss (4 KiB − 2 MiB)")
            ax.set_ylim(bottom=0)
            ax.set_ylabel("ns per page walk")
            bytes_axis(ax, per.index.min(), per.index.max(), spans=CORE_SPANS)
            for sz in (per.index.min(), per.index.max()):
                ax.annotate(f"{per[sz]:.0f} ns", (sz, per[sz]), xytext=(0, 6),
                            textcoords="offset points", ha="center", fontsize=8, color=INK)
            ax.set_title("Cost of a page walk grows with the working set")
            subtitle(ax, "random chase, (4 KiB − 2 MiB) latency / extra walks per load; page-table "
                         "entries fall out of the caches as the footprint grows")
            save(f, "e7_tlb_walk_cost")

    kn = s[s["mode"] == "kern"]
    f, ax = fig()
    k = 0
    for p, nm in (("stride", "one element per 4 KiB page"), ("gather", "random gather")):
        for pg in ("4k", "2m"):
            d = kn[(kn.pattern == p) & (kn.pages == pg)].sort_values("footprint")
            series(ax, d.footprint, d.ns_per_elem, k, f"mul, {nm}, {'4 KiB' if pg == '4k' else '2 MiB'}",
                   ls="-" if pg == "4k" else "--")
            k += 1
    plain_log_y(ax)
    ax.set_ylabel("ns per element (log)")
    bytes_axis(ax, kn.footprint.min(), kn.footprint.max(), label="footprint (all arrays)",
               spans=CORE_SPANS)
    ax.set_title("Kernel runtime: 4 KiB vs 2 MiB pages")
    subtitle(ax, "z = x·y, 1 core; huge pages help the random gather, but NOT the 4 KiB-stride "
                 "loop in DRAM (see EXPERIMENTS.md E7)")
    legend_below(ax, ncol=2)
    save(f, "e7_tlb_kernels")


# ------------------------------------------------------------------------ E8
def strip(ax, groups, ylabel):
    for i, (name, vals) in enumerate(groups):
        v = np.asarray(vals, float)
        jit = (np.random.default_rng(i).random(len(v)) - 0.5) * 0.35
        ax.scatter(i + jit, v, color=SLOTS[i], marker=MARKS[i], s=16, alpha=0.8, zorder=3,
                   edgecolor=SURFACE, lw=0.4)
        med = np.median(v)
        ax.plot([i - 0.3, i + 0.3], [med, med], color=INK, lw=1.4, zorder=4)
        ax.text(i + 0.32, med, f"median {med:.0f}\nrange {v.min():.0f}-{v.max():.0f}",
                fontsize=7, color=INK2, va="center")
    ax.set_xticks(range(len(groups)))
    ax.set_xticklabels([g[0] for g in groups])
    ax.set_xlim(-0.6, len(groups) - 0.1)
    ax.set_ylabel(ylabel)
    ax.grid(axis="x", visible=False)


def plot_e8():
    s = summary("E8")
    true_lat = e1_latency()["DRAM"]
    r = s[s.tag == "reuse:lat"].sort_values("size")
    f, ax = fig()
    bars(ax, [fmt_bytes(v) for v in r["size"]], [("reported 'DRAM latency'", list(r.ns_per_load))],
         "ns per load", fmt="{:.0f}")
    ax.axhline(true_lat, color=SLOTS[1], ls="--", lw=1)
    ax.text(-0.4, true_lat * 0.97, f"true DRAM latency (E1) {true_lat:.0f} ns", fontsize=7.5,
            color=INK2, va="top")
    ax.set_xlabel("buffer size of the 'DRAM' test (L3 = 32 MiB per CCD)")
    ax.set_title("Pitfall: measuring cache reuse instead of DRAM (latency)")
    subtitle(ax, "same random chase; buffers that fit in L3 report L3 latency")
    save(f, "e8_pitfall_cache_reuse_latency")

    b = s[s.tag == "reuse:bw"].sort_values("size")
    f, ax = fig()
    bars(ax, [fmt_bytes(v) for v in b["size"]], [("reported 'DRAM bandwidth'", list(b.gbs_useful))],
         "GB/s", fmt="{:.0f}")
    ax.set_xlabel("buffer size of the 'DRAM' test")
    ax.set_title("Pitfall: measuring cache reuse instead of DRAM (bandwidth)")
    subtitle(ax, "single-core sequential read; only buffers well beyond 32 MiB see DRAM")
    save(f, "e8_pitfall_cache_reuse_bandwidth")

    raw = raw_metrics("E8")
    groups = []
    for tag, name in (("pin:local", "pinned,\nlocal memory"), ("pin:remote", "pinned,\nremote memory"),
                      ("pin:unpinned", "unpinned")):
        v = raw[raw.tag == tag].ns_per_load.dropna()
        if len(v):
            groups.append((name, v))
    if groups:
        f, ax = fig()
        strip(ax, groups, "ns per load (each dot = one sample)")
        ax.set_title("Pitfall: thread and memory placement (latency)")
        subtitle(ax, "1 GiB random chase, 10 separate processes x 5 samples each; remote = memory "
                     "first-touched on the other socket")
        save(f, "e8_pitfall_pinning_latency")
    groups = []
    for tag, name in (("pin:bw-pinned", "16 threads pinned,\none per core, one node"),
                      ("pin:bw-unpinned", "16 threads\nunpinned")):
        v = raw[raw.tag == tag].gbs_useful.dropna()
        if len(v):
            groups.append((name, v))
    if groups:
        f, ax = fig()
        strip(ax, groups, "read bandwidth (GB/s, each dot = one sample)")
        ax.axhline(C.DRAM_PEAK_GBS, color=MUTED, ls="--", lw=0.8)
        ax.text(-0.5, C.DRAM_PEAK_GBS * 1.01, "one socket's DDR5 peak (460.8 GB/s)", fontsize=7.5,
                color=INK2, va="bottom")
        ax.set_title("Pitfall: thread placement (bandwidth)")
        subtitle(ax, "16 x 64 MiB sequential read, 10 processes x 5 samples; unpinned threads "
                     "spread over BOTH sockets, so the 'node' number is really the machine's")
        save(f, "e8_pitfall_pinning_bandwidth")

    pf = s[s.tag.str.startswith("prefetch:")].copy()
    pf["label"] = [
        "random" if t == "prefetch:random" else "sequential" if t == "prefetch:seq"
        else f"'random' =\n+{int(p)} lines" for t, p in zip(pf.tag, pf.param)]
    fake = pf[pf.tag == "prefetch:fakerand"].sort_values("param").label.tolist()
    order = ["random"] + fake + ["sequential"]
    pf = pf.set_index("label").loc[order]
    f, ax = fig()
    bars(ax, list(pf.index), [("ns per load", list(pf.ns_per_load))], "ns per load (log)",
         fmt="{:.1f}", log=True)
    ax.set_title("Pitfall: 'random' that is really a constant stride")
    subtitle(ax, "1 GiB chase where next = (i + step) mod n looks scrambled but the stride "
                 "prefetchers learn it")
    save(f, "e8_pitfall_prefetch_random")

    e5 = summary("E5")
    g = e5[e5.tag == "gens:1:0"].sort_values("gens")
    if len(g):
        f, ax = fig()
        series(ax, g.gens + 1, g.lat_ghz, 0, "latency-probe core")
        gg = g[g.gens > 0]
        series(ax, gg.gens + 1, gg.ghz, 1, "load-generator cores (mean)")
        ax.axhline(C.TSC_GHZ, color=MUTED, ls=":", lw=0.8)
        ax.text(1, C.TSC_GHZ * 1.01, "TSC 2.7 GHz (nominal)", fontsize=7.5, color=INK2, va="bottom")
        ax.set_ylim(2.0, 4.2)
        ax.set_xlabel("active cores on the node")
        ax.set_ylabel("measured core clock (GHz, PMU cycles / ns)")
        ax.set_title("Pitfall: the clock moves with load")
        subtitle(ax, "E5 loaded-latency runs; boost cannot be disabled, so one fixed GHz for "
                     "ns -> cycles is wrong by up to ~30% (TSC too)")
        ax.legend()
        save(f, "e8_pitfall_frequency")


PLOTS = {"E1": plot_e1, "E2": plot_e2, "E3": plot_e3, "E4": plot_e4, "E5": plot_e5,
         "E6": plot_e6, "E7": plot_e7, "E8": plot_e8}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("exp", nargs="+")
    a = ap.parse_args()
    exps = list(PLOTS) if a.exp == ["all"] else [e.upper() for e in a.exp]
    for e in exps:
        if e not in PLOTS:
            continue
        print(e)
        try:
            PLOTS[e]()
        except FileNotFoundError as err:
            print(f"  skipped: {err}")


if __name__ == "__main__":
    main()
