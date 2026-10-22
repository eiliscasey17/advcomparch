#!/usr/bin/env python3
"""Make every figure: one graph per JPG in results/plots/.

  python scripts/plot.py all      (or F1 F4 ...)

Style and helpers are proj2's: validated categorical palette in fixed order,
markers on every series (several slots are < 3:1 contrast on the surface, so
identity never rests on color alone), one y-axis per chart, recessive grid.
Numbers come from results/summary/<EXP>.csv (the table view of every figure);
latency distributions (CCDFs) come from the raw fio json+ histograms.
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


def legend_below(ax, ncol=2, **kw):
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=ncol, fontsize=8, **kw)


def plain_log(ax, axis="y"):
    getattr(ax, f"set_{axis}scale")("log")
    f = FuncFormatter(lambda v, _: f"{v:g}")
    a = getattr(ax, f"{axis}axis")
    a.set_major_formatter(f)
    a.set_minor_formatter(FuncFormatter(lambda v, _: f"{v:g}" if f"{v:g}"[0] in "25" else ""))


def fmt_bytes(x, _=None):
    for unit, v in (("GiB", C.GiB), ("MiB", C.MiB), ("KiB", C.KiB)):
        if x >= v:
            q = x / v
            return f"{q:.0f} {unit}" if abs(q - round(q)) < 1e-6 else f"{q:.1f} {unit}"
    return f"{x:.0f} B"


def bytes_axis(ax, values, label):
    ax.set_xscale("log", base=2)
    v = sorted(set(values))
    ax.set_xticks(v)
    ax.xaxis.set_major_formatter(FuncFormatter(fmt_bytes))
    ax.xaxis.set_minor_locator(NullLocator())
    ax.set_xlabel(label)
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")


def qd_axis(ax, values, label="total queue depth (iodepth x numjobs)"):
    ax.set_xscale("log", base=2)
    ax.set_xticks(sorted(set(values)))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    ax.xaxis.set_minor_locator(NullLocator())
    ax.set_xlabel(label)


def summary(exp):
    p = C.SUMMARY / f"{exp}.csv"
    if not p.exists():
        raw, run = C.load_raw(exp)
        raw["exp"] = exp
        S.summarize(raw).assign(run=run).to_csv(p, index=False)
    return pd.read_csv(p, keep_default_na=False, na_values=["", "nan"])


def series(ax, x, y, i, label, ls="-", lo=None, hi=None, **kw):
    o = np.argsort(np.asarray(x, float))
    x, y = np.asarray(x, float)[o], np.asarray(y, float)[o]
    ax.plot(x, y, color=SLOTS[i], marker=MARKS[i], ls=ls, label=label, **kw)
    if lo is not None and hi is not None:
        ax.fill_between(x, np.asarray(lo, float)[o], np.asarray(hi, float)[o], color=SLOTS[i],
                        alpha=0.15, lw=0)


def bars(ax, labels, groups, ylabel, fmt="{:.0f}", log=False, width=0.8, stacked=False):
    n = len(groups)
    x = np.arange(len(labels))
    w = width if stacked else width / n
    bottom = np.zeros(len(labels))
    for i, (name, vals) in enumerate(groups):
        vals = np.asarray(vals, float)
        pos = x if stacked else x - width / 2 + w * (i + 0.5)
        b = ax.bar(pos, vals, w * 0.92, bottom=bottom if stacked else None, color=SLOTS[i],
                   label=name, zorder=2, edgecolor=SURFACE if stacked else None,
                   linewidth=1.5 if stacked else 0)
        for r, v, b0 in zip(b, vals, bottom):
            if np.isfinite(v) and v > 0:
                y = (b0 + v / 2) if stacked else v
                ax.annotate(fmt.format(v), (r.get_x() + r.get_width() / 2, y),
                            xytext=(0, 0 if stacked else 2), textcoords="offset points",
                            ha="center", va="center" if stacked else "bottom", fontsize=7,
                            color=SURFACE if stacked else INK2)
        if stacked:
            bottom = bottom + np.nan_to_num(vals)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel(ylabel)
    if log:
        plain_log(ax)
    ax.grid(axis="x", visible=False)
    if n > 1:
        ax.legend(fontsize=8)


def pooled_hist(exp, tag, direction):
    """Pooled json+ histogram of one tag: from results/summary/<EXP>_hist.csv
    (committed) if present, else summed from the raw JSON of every round."""
    hp = C.SUMMARY / f"{exp}_hist.csv"
    if hp.exists():
        h = pd.read_csv(hp)
        h = h[(h.tag == tag) & (h.dir == direction)].sort_values("lat_us")
        return h.lat_us.values, h["count"].values
    raw, run = C.load_raw(exp)
    lat_all, cnt_all = [], []
    for _, r in raw[raw.tag == tag].iterrows():
        doc = C.load_json(exp, run, int(r.job), int(r["round"]))
        lat, cnt = C.hist_bins(doc, direction)
        lat_all.append(lat)
        cnt_all.append(cnt)
    if not lat_all:
        return np.array([]), np.array([])
    lat = np.concatenate(lat_all)
    cnt = np.concatenate(cnt_all)
    u, inv = np.unique(lat, return_inverse=True)
    return u, np.bincount(inv, weights=cnt)


def ccdf_plot(ax, curves, title, sub):
    """curves: [(label, lat_us, counts)] -> P(latency > x), log-log."""
    for i, (lab, lat, cnt) in enumerate(curves):
        if not len(lat):
            continue
        x, p = C.ccdf(lat, cnt)
        keep = p > 0
        ax.step(x[keep], p[keep], where="post", color=SLOTS[i], label=lab, lw=1.6)
        for q, mk in ((99, "o"), (99.9, "s")):
            v = C.percentile_from_hist(lat, cnt, q)
            ax.plot(v, 1 - q / 100, marker=mk, color=SLOTS[i], ms=6, mec=INK, mew=0.5)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    for q in (1e-2, 1e-3, 1e-4):
        ax.axhline(q, color=MUTED, lw=0.6, ls=":")
        ax.text(1.0, q, {1e-2: " p99", 1e-3: " p99.9", 1e-4: " p99.99"}[q],
                transform=ax.get_yaxis_transform(), fontsize=7, color=INK2, va="center")
    ax.set_ylim(bottom=1e-6)
    ax.set_xlabel("completion latency (us, log)")
    ax.set_ylabel("P(latency > x)")
    ax.set_title(title)
    subtitle(ax, sub + "; markers: ● p99, ■ p99.9")
    ax.legend(loc="lower left")


DIRS = {"read": "r", "write": "w"}


def pick(s, tag, **kw):
    d = s[s.tag == tag]
    for k, v in kw.items():
        d = d[d[k] == v]
    return d


# ------------------------------------------------------------------------ F1
F1_ROWS = [("qd1:rand:read", "4 KiB random read", "r"), ("qd1:rand:write", "4 KiB random write", "w"),
           ("qd1:seq:read", "128 KiB sequential read", "r"),
           ("qd1:seq:write", "128 KiB sequential write", "w")]


def plot_f1():
    s = summary("F1")
    rows = []
    for tag, name, d in F1_ROWS:
        r = s[s.tag == tag].iloc[0]
        rows.append([name, f"{r.iops:,.0f}", f"{r.bw_MBps:,.0f}", f"{r[d + '_lat_mean_us']:.1f}",
                     f"{r[d + '_p50_us']:.1f}", f"{r[d + '_p95_us']:.1f}", f"{r[d + '_p99_us']:.1f}",
                     f"{r[d + '_p99.9_us']:.1f}", f"{r.little_qd:.2f}"])
    f, ax = fig(9.6, 2.3)
    ax.axis("off")
    cols = ["workload (QD=1)", "IOPS", "MB/s", "mean us", "p50 us", "p95 us", "p99 us", "p99.9 us",
            "IOPS x mean lat"]
    t = ax.table(cellText=rows, colLabels=cols, loc="center", cellLoc="center",
                 colWidths=[0.22, 0.09, 0.08, 0.09, 0.09, 0.09, 0.09, 0.09, 0.12])
    t.auto_set_font_size(False)
    t.set_fontsize(8)
    t.scale(1, 1.5)
    for (r, c), cell in t.get_celld().items():
        cell.set_edgecolor(GRID)
        if r == 0:
            cell.set_text_props(weight="bold")
            cell.set_facecolor(BAND)
    ax.set_title("Zero-queue (QD=1) baselines", pad=8)
    ax.text(0, -0.08, "fio libaio, O_DIRECT, 1 job pinned on the NVMe node; /data = XFS on md RAID5 "
            "(4 x Micron 6.4 TB NVMe); medians of 3 x 30 s; completion-latency percentiles.",
            transform=ax.transAxes, fontsize=7.5, color=INK2, va="top")
    save(f, "f1_qd1_latency_table")

    f, ax = fig()
    labels = [n.replace(" random", "\nrandom").replace(" sequential", "\nsequential")
              for _, n, _ in F1_ROWS]
    groups = []
    for col, nm in (("lat_mean_us", "mean"), ("p95_us", "p95"), ("p99_us", "p99")):
        groups.append((nm, [float(s[s.tag == t].iloc[0][f"{d}_{col}"]) for t, _, d in F1_ROWS]))
    bars(ax, labels, groups, "latency (us)")
    ax.set_title("QD=1 latency: mean, p95, p99")
    subtitle(ax, "one outstanding request; random 4 KiB writes pay RAID5 read-modify-write")
    save(f, "f1_qd1_latency_percentiles")

    f, ax = fig()
    curves = [(n, *pooled_hist("F1", t, {"r": "read", "w": "write"}[d])) for t, n, d in F1_ROWS]
    ccdf_plot(ax, curves, "QD=1 latency distributions", "all I/Os of 3 x 30 s runs")
    save(f, "f1_qd1_latency_ccdf")


# ------------------------------------------------------------------------ F2
def plot_f2():
    s = summary("F2")
    for p, pname in (("rand", "random"), ("seq", "sequential")):
        d = s[s.tag.str.startswith(f"bs:{p}:")]
        f, ax = fig()
        for i, dr in enumerate(("read", "write")):
            q = d[d.tag == f"bs:{p}:{dr}"].sort_values("bs_bytes")
            series(ax, q.bs_bytes, q.bw_MBps, i, dr, lo=q.bw_MBps_lo, hi=q.bw_MBps_hi)
        bytes_axis(ax, d.bs_bytes, "block size")
        ax.set_ylabel("throughput (MB/s)")
        ax.set_ylim(bottom=0)
        if p == "seq" and (d.bs_bytes == C.FULL_STRIPE).any():
            ax.axvline(C.FULL_STRIPE, color=MUTED, ls=":", lw=0.8)
            ax.text(C.FULL_STRIPE, 0.97, " full RAID5\n stripe 1.5 MiB", transform=ax.get_xaxis_transform(),
                    fontsize=7, color=INK2, va="top")
        ax.set_title(f"Block-size sweep: {pname} throughput")
        subtitle(ax, "QD 32 (4 jobs x iodepth 8), O_DIRECT; same runs as the latency plot")
        ax.legend()
        save(f, f"f2_bs_throughput_{pname}")

        f, ax = fig()
        k = 0
        for dr in ("read", "write"):
            q = d[d.tag == f"bs:{p}:{dr}"].sort_values("bs_bytes")
            c = DIRS[dr]
            series(ax, q.bs_bytes, q[f"{c}_lat_mean_us"], k, f"{dr} mean")
            series(ax, q.bs_bytes, q[f"{c}_p99_us"], k + 1, f"{dr} p99", ls="--")
            k += 2
        bytes_axis(ax, d.bs_bytes, "block size")
        plain_log(ax)
        ax.set_ylabel("latency (us, log)")
        ax.set_title(f"Block-size sweep: {pname} latency")
        subtitle(ax, "QD 32; latency grows with transfer time once bandwidth saturates")
        legend_below(ax, ncol=4)
        save(f, f"f2_bs_latency_{pname}")


# ------------------------------------------------------------------------ F3
MIX_LABEL = {100: "100% read", 70: "70/30", 50: "50/50", 0: "100% write"}


def plot_f3():
    s = summary("F3")
    for tag, name, unit in (("mix:rand4k", "4 KiB random", "IOPS"),
                            ("mix:seq128k", "128 KiB sequential", "MB/s")):
        d = s[s.tag == tag].set_index("mix").loc[[100, 70, 50, 0]]
        col = "iops" if unit == "IOPS" else "bw_MBps"
        rv = [d.loc[m].get(f"r_{col}", np.nan) if m > 0 else 0 for m in d.index]
        wv = [d.loc[m].get(f"w_{col}", np.nan) if m < 100 else 0 for m in d.index]
        f, ax = fig()
        bars(ax, [MIX_LABEL[m] for m in d.index], [("read", rv), ("write", wv)],
             unit, stacked=True, fmt="{:,.0f}")
        ax.set_title(f"Read/write mix: {name} throughput")
        subtitle(ax, "QD 32 (4 x 8), O_DIRECT; stacked = read + write share of the same run")
        save(f, f"f3_mix_throughput_{tag.split(':')[1]}")

        f, ax = fig()
        groups = []
        for dr, c in (("read", "r"), ("write", "w")):
            groups.append((f"{dr} mean", [d.loc[m].get(f"{c}_lat_mean_us", np.nan) for m in d.index]))
            groups.append((f"{dr} p99", [d.loc[m].get(f"{c}_p99_us", np.nan) for m in d.index]))
        bars(ax, [MIX_LABEL[m] for m in d.index], groups, "latency (us)")
        ax.set_title(f"Read/write mix: {name} latency")
        subtitle(ax, "writes slow the reads they share the array with")
        legend_below(ax, ncol=4)
        save(f, f"f3_mix_latency_{tag.split(':')[1]}")


# ------------------------------------------------------------------------ F4
def qd_curve(ax, s, sets, xcol, xlabel, isolines=True, scale=1.0):
    allx = []
    for i, (tag, lab) in enumerate(sets):
        d = s[s.tag == tag].sort_values("qd_total")
        if d.empty:
            continue
        x = d[xcol] / scale
        ax.plot(x, d.lat_mean_us, color=SLOTS[i], marker=MARKS[i], label=lab)
        for xx, yy, q in zip(x, d.lat_mean_us, d.qd_total):
            ax.annotate(f"{q:g}", (xx, yy), xytext=(3, 3), textcoords="offset points",
                        fontsize=6.5, color=INK2)
        allx += list(x)
    if isolines and allx:
        xs = np.linspace(max(allx) * 0.01, max(allx) * 1.08, 200)
        for qd in (1, 4, 16, 64, 256):
            ax.plot(xs, qd / (xs * scale) * 1e6 if xcol == "iops" else qd * 0, color=MUTED,
                    lw=0.6, ls=":") if xcol == "iops" else None
    ax.set_xlabel(xlabel)
    ax.set_ylabel("mean latency (us)")


def plot_f4():
    s = summary("F4")
    for wl, sets, title in (
            ("randread4k", [("qd:randread4k:j1", "1 job, iodepth 1-256"),
                            ("qd:randread4k:j8", "8 jobs, iodepth 1-128"),
                            ("qd:randread4k:jobs@32", "iodepth 32, 2-16 jobs")],
             "4 KiB random read"),
            ("randwrite4k", [("qd:randwrite4k:j1", "1 job"), ("qd:randwrite4k:j4", "4 jobs")],
             "4 KiB random write")):
        f, ax = fig(7.4, 4.6)
        k = 0
        for i, (tag, lab) in enumerate(sets):
            d = s[s.tag == tag].sort_values("qd_total")
            ax.plot(d.iops / 1e3, d.lat_mean_us, color=SLOTS[i], marker=MARKS[i], label=lab)
            for xx, yy, q in zip(d.iops / 1e3, d.lat_mean_us, d.qd_total):
                ax.annotate(f"{q:g}", (xx, yy), xytext=(3, 3), textcoords="offset points",
                            fontsize=6.5, color=INK2)
        main = s[s.tag == sets[1][0]].sort_values("qd_total")
        ki = C.knee(main.iops.values, main.lat_mean_us.values)
        kx, ky, kq = main.iops.values[ki] / 1e3, main.lat_mean_us.values[ki], main.qd_total.values[ki]
        ax.plot(kx, ky, marker="*", ms=14, color=SLOTS[1], mec=INK, mew=0.6, zorder=5)
        xmax = s[s.tag.isin([t for t, _ in sets])].iops.max() / 1e3 * 1.1
        ymax = s[s.tag.isin([t for t, _ in sets])].lat_mean_us.max() * 1.1
        xs = np.linspace(xmax * 0.005, xmax, 300)
        for qd in (4, 16, 64, 256, 1024):
            y = qd / (xs * 1e3) * 1e6
            m = y < ymax
            ax.plot(xs[m], y[m], color=MUTED, lw=0.6, ls=":")
            if m.any():
                ax.text(xs[m][-1], y[m][-1], f"QD {qd}", fontsize=6.5, color=INK2, ha="right",
                        va="bottom")
        ax.text(0.02, 0.97, f"knee (★): QD {kq:g}, {kx:.0f}K IOPS at {ky:.0f} us mean",
                transform=ax.transAxes, fontsize=8, va="top")
        ax.set_xlim(0, xmax)
        ax.set_ylim(0, ymax)
        ax.set_xlabel("throughput (K IOPS)")
        ax.set_ylabel("mean latency (us)")
        ax.set_title(f"Throughput vs latency: {title}")
        subtitle(ax, "labels = total QD; dotted = Little's Law, latency = QD / IOPS")
        legend_below(ax, ncol=3)
        save(f, f"f4_qd_curve_{wl}")

    f, ax = fig(7.4, 4.6)
    for i, (tag, lab) in enumerate((("qd:seqread128k:j1", "read, 1 job"),
                                    ("qd:seqread128k:j4", "read, 4 jobs"),
                                    ("qd:seqwrite128k:j1", "write, 1 job"),
                                    ("qd:seqwrite128k:j4", "write, 4 jobs"))):
        d = s[s.tag == tag].sort_values("qd_total")
        ax.plot(d.bw_MBps / 1e3, d.lat_mean_us, color=SLOTS[i], marker=MARKS[i], label=lab,
                ls="-" if "read" in tag else "--")
        for xx, yy, q in zip(d.bw_MBps / 1e3, d.lat_mean_us, d.qd_total):
            ax.annotate(f"{q:g}", (xx, yy), xytext=(3, 3), textcoords="offset points",
                        fontsize=6.5, color=INK2)
    plain_log(ax)
    ax.set_xlabel("throughput (GB/s)")
    ax.set_ylabel("mean latency (us, log)")
    ax.set_title("Throughput vs latency: 128 KiB sequential")
    subtitle(ax, "labels = total QD")
    legend_below(ax, ncol=4)
    save(f, "f4_qd_curve_seq128k")

    f, ax = fig()
    for i, (tag, lab) in enumerate((("qd:randread4k:j1", "1 job, deeper iodepth"),
                                    ("qd:randread4k:j8", "8 jobs, deeper iodepth"),
                                    ("qd:randread4k:jobs@32", "iodepth 32, more jobs"))):
        d = s[s.tag == tag].sort_values("qd_total")
        series(ax, d.qd_total, d.iops / 1e3, i, lab, lo=d.iops_lo / 1e3, hi=d.iops_hi / 1e3)
    qd_axis(ax, s[s.tag.str.startswith("qd:randread4k")].qd_total)
    ax.set_ylabel("K IOPS")
    ax.set_ylim(bottom=0)
    ax.set_title("Parallelism: iodepth vs numjobs (4 KiB random read)")
    subtitle(ax, "the same total depth from one submitting thread or many")
    ax.legend()
    save(f, "f4_qd_iodepth_vs_jobs")

    f, ax = fig(6.2, 5.0)
    d = s.copy()
    for i, (key, lab) in enumerate((("randread4k", "4 KiB rand read"), ("randwrite4k", "4 KiB rand write"),
                                    ("seqread128k", "128 KiB seq read"),
                                    ("seqwrite128k", "128 KiB seq write"))):
        q = d[d.tag.str.contains(key)]
        ax.scatter(q.qd_total, q.little_qd, color=SLOTS[i], marker=MARKS[i], s=26,
                   edgecolor=SURFACE, lw=0.5, label=lab, zorder=3)
    lim = [0.7, d.qd_total.max() * 1.5]
    ax.plot(lim, lim, color=MUTED, ls="--", lw=0.8)
    for a in ("x", "y"):
        plain_log(ax, a)
    ax.set_xlim(lim)
    ax.set_ylim(0.3, lim[1])
    ax.set_xlabel("configured QD (iodepth x numjobs)")
    ax.set_ylabel("Little's Law QD = IOPS x mean latency")
    ax.set_title("Little's Law check")
    subtitle(ax, "on the diagonal: the requested depth really is in flight")
    ax.legend(loc="upper left")
    save(f, "f4_little_check")


# ------------------------------------------------------------------------ F5
def plot_f5():
    s = summary("F5")
    for wl, title, dirs in (("randread4k", "4 KiB random read", ["read"]),
                            ("randwrite4k", "4 KiB random write", ["write"]),
                            ("randrw70-4k", "4 KiB random 70/30 (reads)", ["read"])):
        curves = []
        for lv in ("mid", "knee", "past"):
            tag = f"tail:{wl}:{lv}"
            r = s[s.tag == tag]
            if r.empty:
                continue
            q = int(r.qd_total.iloc[0])
            lat, cnt = pooled_hist("F5", tag, dirs[0])
            curves.append((f"{lv} (QD {q}, {r.iops.iloc[0] / 1e3:.0f}K IOPS)", lat, cnt))
        f, ax = fig()
        ccdf_plot(ax, curves, f"Tail latency: {title}", "60 s x 2 rounds per QD")
        save(f, f"f5_tail_ccdf_{wl}")

    f, ax = fig(7.6, 4.4)
    labels, groups = [], {p: [] for p in ("p50", "p99", "p99.9", "p99.99")}
    for wl, short, c in (("randread4k", "read", "r"), ("randwrite4k", "write", "w"),
                         ("randrw70-4k", "70/30 read", "r")):
        for lv in ("mid", "knee", "past"):
            r = s[s.tag == f"tail:{wl}:{lv}"]
            if r.empty:
                continue
            labels.append(f"{short}\n{lv} QD{int(r.qd_total.iloc[0])}")
            for p in groups:
                groups[p].append(float(r[f"{c}_{p}_us"].iloc[0]))
    bars(ax, labels, list(groups.items()), "latency (us, log)", log=True)
    ax.set_title("Tail percentiles by queue depth")
    subtitle(ax, "past the knee the tail grows much faster than the median")
    legend_below(ax, ncol=4)
    plt.setp(ax.get_xticklabels(), fontsize=7.5)
    save(f, "f5_tail_percentiles")


# ------------------------------------------------------------------------ F6
def plot_f6():
    s = summary("F6")
    f, ax = fig()
    for i, (tag, lab) in enumerate((("lba:randread4k:qd32", "read"), ("lba:randwrite4k:qd32", "write"))):
        d = s[s.tag == tag].sort_values("region")
        series(ax, d.region, d.iops / 1e3, i, lab, lo=d.iops_lo / 1e3, hi=d.iops_hi / 1e3)
    bytes_axis(ax, s.region, "LBA range addressed (file region)")
    ax.set_ylabel("K IOPS")
    ax.set_ylim(bottom=0)
    ax.set_title("Working set / LBA range: 4 KiB random, QD 32")
    subtitle(ax, "same file, random offsets restricted to the first N bytes")
    ax.legend()
    save(f, "f6_lba_iops_qd32")

    f, ax = fig()
    k = 0
    for tag, c, lab in (("lba:randread4k:qd32", "r", "read QD32"), ("lba:randwrite4k:qd32", "w", "write QD32"),
                        ("lba:randread4k:qd1", "r", "read QD1")):
        d = s[s.tag == tag].sort_values("region")
        series(ax, d.region, d[f"{c}_p50_us"], k, f"{lab} p50")
        series(ax, d.region, d[f"{c}_p99_us"], k + 1, f"{lab} p99", ls="--")
        k += 2
    bytes_axis(ax, s.region, "LBA range addressed (file region)")
    plain_log(ax)
    ax.set_ylabel("latency (us, log)")
    ax.set_title("Working set / LBA range: latency")
    subtitle(ax, "4 KiB random")
    legend_below(ax, ncol=3)
    save(f, "f6_lba_latency")


# ------------------------------------------------------------------------ F7
def iops_logs(exp, run, raw):
    """Per-second total IOPS of the sustained segments, in run order."""
    out = []
    t_off = 0.0
    for _, r in raw[raw.tag == "state:sustain:randwrite4k"].sort_values("segment").iterrows():
        logs = sorted((C.RAW / exp / run).glob(f"j{int(r.job):03d}_r{int(r['round'])}_iops.*.log"))
        if not logs:
            continue
        parts = []
        for lg in logs:
            a = pd.read_csv(lg, header=None, usecols=[0, 1], names=["ms", "iops"])
            a["sec"] = (a.ms / 1000).round().astype(int)
            parts.append(a.groupby("sec").iops.sum())
        tot = pd.concat(parts, axis=1).sum(axis=1)
        out.append(pd.DataFrame({"t_s": t_off + tot.index.values, "iops": tot.values}))
        t_off += r.wall_s
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=["t_s", "iops"])


def plot_f7():
    s = summary("F7")
    raw, run = C.load_raw("F7")
    ts = iops_logs("F7", run, raw)
    seg = s[s.tag == "state:sustain:randwrite4k"].sort_values("segment")
    f, ax = fig(8.0, 4.2)
    if len(ts):
        ax.plot(ts.t_s / 60, ts.iops / 1e3, color=SLOTS[0], lw=0.7, alpha=0.6, label="per second")
    tmid = (seg.segment + 0.5) * (seg.wall_s.median() / 60)
    ax.plot(tmid, seg.iops / 1e3, color=SLOTS[1], marker="o", label="per 60 s segment")
    tb = seg.region.iloc[0] if len(seg) else C.DATA_SIZE
    cum = (seg.io_bytes.cumsum() / 1e12)
    ax.set_xlabel("time since start of sustained writing (min)")
    ax.set_ylabel("K IOPS")
    ax.set_ylim(bottom=0)
    ax.set_title("Device state: sustained 4 KiB random write")
    subtitle(ax, f"QD 32 over {fmt_bytes(tb)}; {cum.iloc[-1] if len(cum) else 0:.2f} TB written "
                 "by the host (x2 on the drives: RAID5 parity)")
    ax.legend(loc="lower right")
    save(f, "f7_sustained_iops")

    f, ax = fig(8.0, 4.2)
    for i, (col, lab) in enumerate((("w_p50_us", "p50"), ("w_p99_us", "p99"), ("w_p99.9_us", "p99.9"))):
        ax.plot(tmid, seg[col], color=SLOTS[i], marker=MARKS[i], label=lab)
    plain_log(ax)
    ax.set_xlabel("time since start of sustained writing (min)")
    ax.set_ylabel("write latency (us, log)")
    ax.set_title("Device state: latency percentiles over time")
    subtitle(ax, "each point = one back-to-back 60 s fio segment")
    ax.legend()
    save(f, "f7_sustained_latency")

    f, ax = fig()
    labels, gm, g99 = [], [], []
    for wl, c in (("randread4k:qd1", "r"), ("randwrite4k:qd1", "w")):
        for ph in ("before", "after", "idle10m"):
            r = s[s.tag == f"state:{ph}:{wl}"]
            if r.empty:
                continue
            labels.append(f"{wl.split(':')[0].replace('4k', '')}\n{ph}")
            gm.append(float(r[f"{c}_lat_mean_us"].iloc[0]))
            g99.append(float(r[f"{c}_p99_us"].iloc[0]))
    bars(ax, labels, [("mean", gm), ("p99", g99)], "latency (us)")
    ax.set_title("QD=1 probes before / right after / 10 min after sustained writes")
    subtitle(ax, "background garbage collection would show up as slower probes right after")
    save(f, "f7_state_probes")

    # RAID5 device-level amplification from fio's disk_util (F1-F4 runs)
    rows = []
    for exp, tag, lab in (("F1", "qd1:rand:read", "4K rand read"), ("F1", "qd1:rand:write", "4K rand write"),
                          ("F1", "qd1:seq:write", "128K seq write QD1"),
                          ("F2", "bs:seq:write", None), ("F3", "mix:rand4k", None)):
        try:
            d = summary(exp)
        except FileNotFoundError:
            continue
        q = d[d.tag == tag]
        if exp == "F2":
            for bs in ("128k", "1m", "1536k"):
                r = q[q.bs == bs]
                if len(r):
                    rows.append((f"{bs} seq write QD32", r.iloc[0]))
        elif exp == "F3":
            r = q[q.mix == 70]
            if len(r):
                rows.append(("4K rand 70/30", r.iloc[0]))
        elif len(q):
            rows.append((lab, q.iloc[0]))
    if rows:
        f, ax = fig(8.2, 4.4)
        labels = [r[0].replace(" seq write", "\nseq write").replace(" rand", "\nrand")
                  .replace(" QD", "\nQD") for r in rows]
        bars(ax, labels, [("drive bytes read per host byte",
                           [r[1].mem_read_bytes_per_host_byte for r in rows]),
                          ("drive bytes written per host byte",
                           [r[1].mem_write_bytes_per_host_byte for r in rows])],
             "bytes at the 4 drives per host byte", fmt="{:.2f}")
        for y, lab in ((2.0, "small-write RMW: 2 + 2"), (4 / 3, "full stripe: 4/3")):
            ax.axhline(y, color=MUTED, ls=":", lw=0.7)
            ax.text(-0.45, y, lab, fontsize=7, color=INK2, ha="left", va="bottom")
        ax.set_title("What the drives really do (RAID5 read-modify-write)")
        subtitle(ax, "fio disk_util: md124 vs its 4 member NVMe drives over the same job")
        legend_below(ax, ncol=2)
        plt.setp(ax.get_xticklabels(), fontsize=7.5)
        save(f, "f7_device_io_amplification")


# ------------------------------------------------------------------------ F8
def plot_f8():
    s = summary("F8")
    g = lambda tag: s[s.tag == tag].iloc[0] if (s.tag == tag).any() else None  # noqa: E731

    f, ax = fig()
    labs, iops = [], []
    for tag, lab in (("pc:randread4k:direct", "O_DIRECT"), ("pc:randread4k:buffered-cold", "buffered,\ncold cache"),
                     ("pc:randread4k:buffered-warm", "buffered,\nfile in page cache")):
        r = g(tag)
        if r is None:
            continue
        labs.append(f"{lab}\n({r.md_vs_fio_bytes:.2f} B from disk per B)")
        iops.append(r.iops)
    bars(ax, labs, [("IOPS (QD=1, 4 KiB random read)", iops)], "IOPS (log)", fmt="{:,.0f}", log=True)
    ax.set_title("Pitfall: the page cache answers instead of the SSD")
    subtitle(ax, "8 GiB file, 755 GiB of RAM: a warm 'disk' benchmark measures DRAM")
    save(f, "f8_pitfall_page_cache_reads")

    f, ax = fig()
    rd, rb = g("pc:randwrite4k:direct"), g("pc:randwrite4k:buffered")
    if rd is not None and rb is not None:
        bars(ax, ["O_DIRECT", "buffered"],
             [("MB/s fio reports", [rd.bw_MBps, rb.bw_MBps]),
              ("MB/s reaching /data during the run", [rd.md_bytes_wall / rd.wall_s / 1e6,
                                                      rb.md_bytes_wall / rb.wall_s / 1e6])],
             "MB/s (log)", fmt="{:,.0f}", log=True)
        ax.set_title("Pitfall: buffered writes are not disk writes")
        subtitle(ax, "4 KiB random write, QD=1: buffered data sits dirty in RAM when fio stops timing")
        save(f, "f8_pitfall_buffered_write")

    f, ax = fig(7.6, 4.2)
    cfg = [("qd:libaio-direct-qd32", "libaio O_DIRECT\niodepth 32"),
           ("qd:libaio-buffered-qd32", "libaio buffered\niodepth 32"),
           ("qd:psync-qd32", "psync\niodepth 32"), ("qd:psync-32jobs", "psync\n32 jobs")]
    rs = [(lab, g(t)) for t, lab in cfg if g(t) is not None]
    bars(ax, [l for l, _ in rs], [("requested", [32] * len(rs)),
                                  ("fio's view (IOPS x fio latency)", [r.little_qd for _, r in rs]),
                                  ("at the drives (diskstats in-flight)", [r.get("dev_qd", np.nan) for _, r in rs])],
         "queue depth", fmt="{:.1f}")
    for i, (_, r) in enumerate(rs):
        ax.text(i, -0.18, f"{r.iops / 1e3:.0f}K IOPS", transform=ax.get_xaxis_transform(),
                ha="center", fontsize=7.5, color=INK2)
    ax.set_title("Pitfall: the queue depth you ask for is not the one you get")
    subtitle(ax, "4 KiB random read; buffered libaio blocks in io_submit: fio 'queues' 32, the SSDs see ~1")
    save(f, "f8_pitfall_effective_qd")

    f, ax = fig()
    a, b = g("fa:randread4k:qd32:unwritten"), g("fa:randread4k:qd32:written")
    c, d = g("fa:randwrite4k:qd1:first-write"), g("fa:randwrite4k:qd1:overwrite")
    if a is not None and b is not None:
        bars(ax, [f"fallocated, never written\n({a.md_vs_fio_bytes:.2f} B from disk per B)",
                  f"written data\n({b.md_vs_fio_bytes:.2f} B from disk per B)"],
             [("4 KiB random read IOPS, QD 32", [a.iops, b.iops])], "IOPS (log)", fmt="{:,.0f}", log=True)
        ax.set_title("Pitfall: reading space that was never written")
        subtitle(ax, "XFS returns zeros for unwritten extents without touching the SSDs")
        save(f, "f8_pitfall_unwritten_reads")
    if c is not None and d is not None:
        f, ax = fig()
        bars(ax, ["first write to\nfallocated space", "overwrite of\nwritten data"],
             [("mean", [c.w_lat_mean_us, d.w_lat_mean_us]), ("p99", [c.w_p99_us, d.w_p99_us])],
             "latency (us)")
        ax.set_title("Pitfall: insufficient preconditioning (first writes)")
        subtitle(ax, "4 KiB random write, QD=1; first writes also convert unwritten extents")
        save(f, "f8_pitfall_first_write")

    f, ax = fig()
    pats = ["incompressible", "compress50", "zeros"]
    groups = []
    for wl, col, lab in (("seqwrite128k", "bw_MBps", "128 KiB seq write MB/s"),
                         ("randwrite4k", "iops", "4 KiB rand write IOPS")):
        base = g(f"pattern:{wl}:incompressible")
        if base is None:
            continue
        groups.append((lab, [g(f"pattern:{wl}:{p}")[col] / base[col] if g(f"pattern:{wl}:{p}") is not None
                             else np.nan for p in pats]))
    if groups:
        bars(ax, ["incompressible", "50% compressible", "all zeros"], groups,
             "throughput relative to incompressible", fmt="{:.2f}")
        ax.axhline(1, color=MUTED, ls="--", lw=0.8)
        ax.set_title("Data pattern: compressibility makes no difference here")
        subtitle(ax, "QD 32; neither md RAID5 nor these drives compress or deduplicate")
        save(f, "f8_data_pattern")

    f, ax = fig()
    rows = [(lab, g(t)) for t, lab in (("gen:seqwrite128k:qd1:incompressible", "QD1, reused\nbuffers"),
                                       ("gen:seqwrite128k:qd1:refill", "QD1, refill\nevery buffer"),
                                       ("gen:seqwrite128k:qd32:incompressible", "QD32, reused\nbuffers"),
                                       ("gen:seqwrite128k:qd32:refill", "QD32, refill\nevery buffer"))
            if g(t) is not None]
    if rows:
        bars(ax, [l for l, _ in rows], [("MB/s", [r.bw_MBps for _, r in rows])], "MB/s",
             fmt="{:,.0f}")
        for i, (_, r) in enumerate(rows):
            ax.text(i, 0.04, f"in flight {r.little_qd:.2f}\nfio usr {r.usr_cpu:.0f}%",
                    transform=ax.get_xaxis_transform(), ha="center", fontsize=7, color=SURFACE)
        ax.set_title("Pitfall: the load generator is the bottleneck")
        subtitle(ax, "128 KiB sequential write; --refill_buffers costs ~3 us of CPU per KiB")
        save(f, "f8_pitfall_generator")


PLOTS = {"F1": plot_f1, "F2": plot_f2, "F3": plot_f3, "F4": plot_f4, "F5": plot_f5,
         "F6": plot_f6, "F7": plot_f7, "F8": plot_f8}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("exp", nargs="+")
    a = ap.parse_args()
    exps = list(PLOTS) if a.exp == ["all"] else [e.upper() for e in a.exp]
    for e in exps:
        if e in PLOTS:
            print(e)
            try:
                PLOTS[e]()
            except FileNotFoundError as err:
                print(f"  skipped: {err}")


if __name__ == "__main__":
    main()
