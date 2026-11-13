#!/usr/bin/env python3
"""Make every figure: one graph per JPG in results/plots/.

  python scripts/plot.py all      (or qd1 qdsweep ...; old IDs F1..F8 also work)

Style and helpers are proj2's: validated categorical palette in fixed order,
markers on every series (several slots are < 3:1 contrast on the surface, so
identity never rests on color alone), one y-axis per chart, recessive grid.
Numbers come from results/summary/<EXP>.csv (the table view of every figure);
latency distributions (CCDFs) come from the raw fio json+ histograms.

Every figure states its fixed and varied knobs on the second subtitle line.
Error bars / bands = min-max over the rounds of that configuration (2-3 rounds
of 20-60 s each: a range is more honest than a bootstrap CI from 2-3 values);
figures without repeats (the ordered device-state run) say so.
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
    for ax in fig.axes:  # make room for the two-line (subtitle + knobs) header
        if getattr(ax, "_two_line", False):
            ax.set_title(ax.get_title(loc="left"), loc="left", pad=32)
    C.PLOTS.mkdir(parents=True, exist_ok=True)
    p = C.PLOTS / f"{name}.jpg"
    fig.savefig(p, dpi=160, bbox_inches="tight", pil_kwargs={"quality": 92})
    plt.close(fig)
    print("  wrote", p.relative_to(C.ROOT))


def fig(w=7.2, h=4.2):
    return plt.subplots(figsize=(w, h))


def subtitle(ax, text, fixed=None, varied=None):
    """Grey line(s) under the title: the note, then 'Fixed: ... | Varied: ...'."""
    if fixed or varied:
        knobs = " | ".join(p for p in (f"Fixed: {fixed}" if fixed else "",
                                       f"Varied: {varied}" if varied else "") if p)
        text = f"{text}\n{knobs}"
        ax._two_line = True
    ax.text(0, 1.015, text, transform=ax.transAxes, fontsize=8.5, color=INK2, va="bottom",
            linespacing=1.4)


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
                        alpha=0.18, lw=0)


def yerr(v, lo, hi):
    """Asymmetric error-bar array from a value and its range."""
    v, lo, hi = (np.asarray(a, float) for a in (v, lo, hi))
    return np.vstack([np.clip(v - lo, 0, None), np.clip(hi - v, 0, None)])


def rng(d, col):
    """(min, max) over rounds of a summary metric, as arrays (NaN if absent)."""
    nan = np.full(len(d), np.nan)
    return (np.asarray(d[f"{col}_min"], float) if f"{col}_min" in d else nan,
            np.asarray(d[f"{col}_max"], float) if f"{col}_max" in d else nan)


def bars(ax, labels, groups, ylabel, fmt="{:.0f}", log=False, width=0.8, stacked=False, annotate=True):
    """groups: [(label, values[, lo, hi])]; lo/hi draw min-max error bars."""
    n = len(groups)
    x = np.arange(len(labels))
    w = width if stacked else width / n
    bottom = np.zeros(len(labels))
    for i, grp in enumerate(groups):
        name, vals = grp[0], np.asarray(grp[1], float)
        pos = x if stacked else x - width / 2 + w * (i + 0.5)
        b = ax.bar(pos, vals, w * 0.92, bottom=bottom if stacked else None, color=SLOTS[i],
                   label=name, zorder=2, edgecolor=SURFACE if stacked else None,
                   linewidth=1.5 if stacked else 0)
        if len(grp) == 4:
            top = (bottom if stacked else 0) + vals
            e = yerr(vals, grp[2], grp[3])
            ok = np.isfinite(vals) & (vals > 0)
            ax.errorbar(np.asarray(pos)[ok], top[ok], yerr=e[:, ok], fmt="none", ecolor=INK,
                        elinewidth=0.8, capsize=2, zorder=3)
        for r, v, b0 in zip(b, vals, bottom):
            if annotate and np.isfinite(v) and v > 0:
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


def ccdf_plot(ax, curves, title, sub, fixed=None, varied=None):
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
    subtitle(ax, sub + "; markers: ● p99, ■ p99.9", fixed=fixed, varied=varied)
    ax.legend(loc="lower left")


DIRS = {"read": "r", "write": "w"}


def pick(s, tag, **kw):
    d = s[s.tag == tag]
    for k, v in kw.items():
        d = d[d[k] == v]
    return d


# ------------------------------------------------------------------------ qd1
QD1_ROWS = [("qd1:rand:read", "4 KiB random read", "r"), ("qd1:rand:write", "4 KiB random write", "w"),
           ("qd1:seq:read", "128 KiB sequential read", "r"),
           ("qd1:seq:write", "128 KiB sequential write", "w")]


def plot_qd1():
    s = summary("qd1")
    rows = []
    for tag, name, d in QD1_ROWS:
        r = s[s.tag == tag].iloc[0]
        p99 = d + "_p99_us"
        rows.append([name, f"{r.iops:,.0f}", f"{r.iops_min:,.0f}–{r.iops_max:,.0f}", f"{r.bw_MBps:,.0f}",
                     f"{r[d + '_lat_mean_us']:.1f}", f"{r[d + '_p50_us']:.1f}", f"{r[d + '_p95_us']:.1f}",
                     f"{r[p99]:.1f}", f"{r[p99 + '_min']:.1f}–{r[p99 + '_max']:.1f}",
                     f"{r[d + '_p99.9_us']:.1f}", f"{r.little_qd:.2f}"])
    f, ax = fig(11.6, 2.3)
    ax.axis("off")
    cols = ["workload (QD=1)", "IOPS", "IOPS range", "MB/s", "mean us", "p50 us", "p95 us", "p99 us",
            "p99 range", "p99.9 us", "IOPS x mean lat"]
    t = ax.table(cellText=rows, colLabels=cols, loc="center", cellLoc="center",
                 colWidths=[0.17, 0.06, 0.11, 0.05, 0.07, 0.06, 0.06, 0.06, 0.1, 0.07, 0.12])
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
            "(4 x Micron 6.4 TB NVMe); medians of 3 x 30 s (ranges = min-max over the 3 rounds); "
            "completion-latency percentiles. Fixed: QD 1, 64 GiB range | Varied: workload.",
            transform=ax.transAxes, fontsize=7.5, color=INK2, va="top")
    save(f, "qd1_latency_table")

    f, ax = fig()
    labels = [n.replace(" random", "\nrandom").replace(" sequential", "\nsequential")
              for _, n, _ in QD1_ROWS]
    groups = []
    for col, nm in (("lat_mean_us", "mean"), ("p95_us", "p95"), ("p99_us", "p99")):
        rs = [s[s.tag == t].iloc[0] for t, _, d in QD1_ROWS]
        ks = [f"{d}_{col}" for _, _, d in QD1_ROWS]
        groups.append((nm, [r[k] for r, k in zip(rs, ks)], [r[k + "_min"] for r, k in zip(rs, ks)],
                       [r[k + "_max"] for r, k in zip(rs, ks)]))
    bars(ax, labels, groups, "latency (us)")
    ax.set_title("QD=1 latency: mean, p95, p99")
    subtitle(ax, "random 4 KiB writes pay RAID5 read-modify-write; error bars: min-max over 3 rounds",
             fixed="QD 1, O_DIRECT, 64 GiB range", varied="workload (pattern x direction)")
    save(f, "qd1_latency_percentiles")

    f, ax = fig()
    curves = [(n, *pooled_hist("qd1", t, {"r": "read", "w": "write"}[d])) for t, n, d in QD1_ROWS]
    ccdf_plot(ax, curves, "QD=1 latency distributions", "all I/Os of 3 x 30 s runs pooled",
              fixed="QD 1, O_DIRECT, 64 GiB range", varied="workload")
    save(f, "qd1_latency_ccdf")


# ------------------------------------------------------------------------ blocksize
def plot_blocksize():
    s = summary("blocksize")
    for p, pname in (("rand", "random"), ("seq", "sequential")):
        d = s[s.tag.str.startswith(f"bs:{p}:")]
        f, ax = fig()
        for i, dr in enumerate(("read", "write")):
            q = d[d.tag == f"bs:{p}:{dr}"].sort_values("bs_bytes")
            series(ax, q.bs_bytes, q.bw_MBps, i, dr, lo=q.bw_MBps_min, hi=q.bw_MBps_max)
        bytes_axis(ax, d.bs_bytes, "block size")
        ax.set_ylabel("throughput (MB/s)")
        ax.set_ylim(bottom=0)
        if p == "seq" and (d.bs_bytes == C.FULL_STRIPE).any():
            ax.axvline(C.FULL_STRIPE, color=MUTED, ls=":", lw=0.8)
            ax.text(C.FULL_STRIPE, 0.97, " full RAID5\n stripe 1.5 MiB", transform=ax.get_xaxis_transform(),
                    fontsize=7, color=INK2, va="top")
        ax.set_title(f"Block-size sweep: {pname} throughput")
        subtitle(ax, "same runs as the latency plot; bands: min-max over 2 rounds",
                 fixed=f"{pname}, QD 32 (4 jobs x iodepth 8), O_DIRECT, 64 GiB range",
                 varied="block size, direction")
        ax.legend()
        save(f, f"blocksize_throughput_{pname}")

        f, ax = fig()
        k = 0
        for dr in ("read", "write"):
            q = d[d.tag == f"bs:{p}:{dr}"].sort_values("bs_bytes")
            c = DIRS[dr]
            series(ax, q.bs_bytes, q[f"{c}_lat_mean_us"], k, f"{dr} mean",
                   lo=q[f"{c}_lat_mean_us_min"], hi=q[f"{c}_lat_mean_us_max"])
            series(ax, q.bs_bytes, q[f"{c}_p99_us"], k + 1, f"{dr} p99", ls="--",
                   lo=q[f"{c}_p99_us_min"], hi=q[f"{c}_p99_us_max"])
            k += 2
        bytes_axis(ax, d.bs_bytes, "block size")
        plain_log(ax)
        ax.set_ylabel("latency (us, log)")
        ax.set_title(f"Block-size sweep: {pname} latency")
        subtitle(ax, "latency grows with transfer time once bandwidth saturates; bands: min-max over "
                 "2 rounds", fixed=f"{pname}, QD 32 (4 x 8), O_DIRECT, 64 GiB range",
                 varied="block size, direction")
        legend_below(ax, ncol=4)
        save(f, f"blocksize_latency_{pname}")


# ------------------------------------------------------------------------ rwmix
MIX_LABEL = {100: "100% read", 70: "70/30", 50: "50/50", 0: "100% write"}


def plot_rwmix():
    s = summary("rwmix")
    for tag, name, unit in (("mix:rand4k", "4 KiB random", "IOPS"),
                            ("mix:seq128k", "128 KiB sequential", "MB/s")):
        d = s[s.tag == tag].set_index("mix").loc[[100, 70, 50, 0]]
        col = "iops" if unit == "IOPS" else "bw_MBps"
        def part(c, keep):
            return [[d.loc[m].get(f"{c}{sfx}", np.nan) if keep(m) else 0 for m in d.index]
                    for sfx in ("", "_min", "_max")]
        rv = part(f"r_{col}", lambda m: m > 0)
        wv = part(f"w_{col}", lambda m: m < 100)
        f, ax = fig()
        bars(ax, [MIX_LABEL[m] for m in d.index], [("read", *rv), ("write", *wv)],
             unit, stacked=True, fmt="{:,.0f}")
        ax.set_title(f"Read/write mix: {name} throughput")
        subtitle(ax, "stacked = read + write share of the same run; error bars: min-max over 3 rounds",
                 fixed=f"{name}, QD 32 (4 x 8), O_DIRECT, 64 GiB range", varied="read share")
        save(f, f"rwmix_throughput_{tag.split(':')[1]}")

        f, ax = fig()
        groups = []
        for dr, c in (("read", "r"), ("write", "w")):
            for col2, nm in (("lat_mean_us", "mean"), ("p99_us", "p99")):
                k = f"{c}_{col2}"
                groups.append((f"{dr} {nm}", *[[d.loc[m].get(k + sfx, np.nan) for m in d.index]
                                               for sfx in ("", "_min", "_max")]))
        bars(ax, [MIX_LABEL[m] for m in d.index], groups, "latency (us)")
        ax.set_title(f"Read/write mix: {name} latency")
        subtitle(ax, "writes slow the reads they share the array with; error bars: min-max over 3 rounds",
                 fixed=f"{name}, QD 32 (4 x 8), O_DIRECT, 64 GiB range", varied="read share")
        legend_below(ax, ncol=4)
        save(f, f"rwmix_latency_{tag.split(':')[1]}")


# ------------------------------------------------------------------------ qdsweep
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


def plot_qdsweep():
    s = summary("qdsweep")
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
            ax.errorbar(d.iops / 1e3, d.lat_mean_us,
                        xerr=yerr(d.iops / 1e3, d.iops_min / 1e3, d.iops_max / 1e3),
                        yerr=yerr(d.lat_mean_us, d.lat_mean_us_min, d.lat_mean_us_max),
                        color=SLOTS[i], marker=MARKS[i], elinewidth=0.8, capsize=1.5, label=lab)
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
        subtitle(ax, "labels = total QD; dotted = Little's Law, latency = QD / IOPS; bars: min-max "
                 "over 2 rounds", fixed=f"{title}, O_DIRECT, 64 GiB range",
                 varied="iodepth and numjobs (total QD)")
        legend_below(ax, ncol=3)
        save(f, f"qdsweep_curve_{wl}")

    f, ax = fig(7.4, 4.6)
    for i, (tag, lab) in enumerate((("qd:seqread128k:j1", "read, 1 job"),
                                    ("qd:seqread128k:j4", "read, 4 jobs"),
                                    ("qd:seqwrite128k:j1", "write, 1 job"),
                                    ("qd:seqwrite128k:j4", "write, 4 jobs"))):
        d = s[s.tag == tag].sort_values("qd_total")
        ax.errorbar(d.bw_MBps / 1e3, d.lat_mean_us,
                    xerr=yerr(d.bw_MBps / 1e3, d.bw_MBps_min / 1e3, d.bw_MBps_max / 1e3),
                    yerr=yerr(d.lat_mean_us, d.lat_mean_us_min, d.lat_mean_us_max),
                    color=SLOTS[i], marker=MARKS[i], label=lab, elinewidth=0.8, capsize=1.5,
                    ls="-" if "read" in tag else "--")
        for xx, yy, q in zip(d.bw_MBps / 1e3, d.lat_mean_us, d.qd_total):
            ax.annotate(f"{q:g}", (xx, yy), xytext=(3, 3), textcoords="offset points",
                        fontsize=6.5, color=INK2)
    plain_log(ax)
    ax.set_xlabel("throughput (GB/s)")
    ax.set_ylabel("mean latency (us, log)")
    ax.set_title("Throughput vs latency: 128 KiB sequential")
    subtitle(ax, "labels = total QD; bars: min-max over 2 rounds",
             fixed="128 KiB sequential, O_DIRECT, 64 GiB range (disjoint stream per job)",
             varied="iodepth, numjobs, direction")
    legend_below(ax, ncol=4)
    save(f, "qdsweep_curve_seq128k")

    f, ax = fig()
    for i, (tag, lab) in enumerate((("qd:randread4k:j1", "1 job, deeper iodepth"),
                                    ("qd:randread4k:j8", "8 jobs, deeper iodepth"),
                                    ("qd:randread4k:jobs@32", "iodepth 32, more jobs"))):
        d = s[s.tag == tag].sort_values("qd_total")
        series(ax, d.qd_total, d.iops / 1e3, i, lab, lo=d.iops_min / 1e3, hi=d.iops_max / 1e3)
    qd_axis(ax, s[s.tag.str.startswith("qd:randread4k")].qd_total)
    ax.set_ylabel("K IOPS")
    ax.set_ylim(bottom=0)
    ax.set_title("Parallelism: iodepth vs numjobs (4 KiB random read)")
    subtitle(ax, "the same total depth from one submitting thread or many; bands: min-max over 2 rounds",
             fixed="4 KiB random read, O_DIRECT, 64 GiB range", varied="iodepth vs numjobs")
    ax.legend()
    save(f, "qdsweep_iodepth_vs_jobs")

    f, ax = fig(6.2, 5.0)
    d = s.copy()
    for i, (key, lab) in enumerate((("randread4k", "4 KiB rand read"), ("randwrite4k", "4 KiB rand write"),
                                    ("seqread128k", "128 KiB seq read"),
                                    ("seqwrite128k", "128 KiB seq write"))):
        q = d[d.tag.str.contains(key)]
        ax.errorbar(q.qd_total, q.little_qd, yerr=yerr(q.little_qd, q.little_qd_min, q.little_qd_max),
                    fmt="none", ecolor=SLOTS[i], elinewidth=0.8, zorder=2)
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
    subtitle(ax, "on the diagonal: the requested depth really is in flight; bars: min-max over 2 rounds",
             fixed="O_DIRECT libaio, 64 GiB range", varied="workload, iodepth x numjobs")
    ax.legend(loc="upper left")
    save(f, "qdsweep_little_check")


# ------------------------------------------------------------------------ tail
def plot_tail():
    s = summary("tail")
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
            lat, cnt = pooled_hist("tail", tag, dirs[0])
            curves.append((f"{lv} (QD {q}, {r.iops.iloc[0] / 1e3:.0f}K IOPS)", lat, cnt))
        f, ax = fig()
        ccdf_plot(ax, curves, f"Tail latency: {title}", "60 s x 2 rounds per QD, pooled (round spread: "
                  "tail_percentiles.jpg)", fixed=f"{title}, O_DIRECT, 64 GiB range",
                  varied="QD: mid / near knee / past knee")
        save(f, f"tail_ccdf_{wl}")

    f, ax = fig(9.4, 4.6)
    labels, groups = [], {p: ([], [], []) for p in ("p50", "p99", "p99.9", "p99.99")}
    for wl, short, c in (("randread4k", "read", "r"), ("randwrite4k", "write", "w"),
                         ("randrw70-4k", "70/30 read", "r")):
        for lv in ("mid", "knee", "past"):
            r = s[s.tag == f"tail:{wl}:{lv}"]
            if r.empty:
                continue
            labels.append(f"{short}\n{lv} QD{int(r.qd_total.iloc[0])}")
            for p in groups:
                for lst, sfx in zip(groups[p], ("", "_min", "_max")):
                    lst.append(float(r[f"{c}_{p}_us{sfx}"].iloc[0]))
    bars(ax, labels, [(p, *v) for p, v in groups.items()], "latency (us, log)", log=True,
         annotate=False)  # 36 bars: values are in tail.csv
    ax.set_title("Tail percentiles by queue depth")
    subtitle(ax, "past the knee the tail grows much faster than the median; bars: min-max over 2 rounds",
             fixed="4 KiB random, O_DIRECT, 64 GiB range, 60 s", varied="workload, QD")
    legend_below(ax, ncol=4)
    plt.setp(ax.get_xticklabels(), fontsize=7.5)
    save(f, "tail_percentiles")


# ------------------------------------------------------------------------ lba
def plot_lba():
    s = summary("lba")
    f, ax = fig()
    for i, (tag, lab) in enumerate((("lba:randread4k:qd32", "read"), ("lba:randwrite4k:qd32", "write"))):
        d = s[s.tag == tag].sort_values("region")
        series(ax, d.region, d.iops / 1e3, i, lab, lo=d.iops_min / 1e3, hi=d.iops_max / 1e3)
    bytes_axis(ax, s.region, "LBA range addressed (file region)")
    ax.set_ylabel("K IOPS")
    ax.set_ylim(bottom=0)
    ax.set_title("Working set / LBA range: 4 KiB random, QD 32")
    subtitle(ax, "same file, random offsets restricted to the first N bytes; bands: min-max over 2 rounds",
             fixed="4 KiB random, QD 32 (4 x 8), O_DIRECT", varied="LBA range, direction")
    ax.legend()
    save(f, "lba_iops_qd32")

    f, ax = fig()
    k = 0
    for tag, c, lab in (("lba:randread4k:qd32", "r", "read QD32"), ("lba:randwrite4k:qd32", "w", "write QD32"),
                        ("lba:randread4k:qd1", "r", "read QD1")):
        d = s[s.tag == tag].sort_values("region")
        series(ax, d.region, d[f"{c}_p50_us"], k, f"{lab} p50",
               lo=d[f"{c}_p50_us_min"], hi=d[f"{c}_p50_us_max"])
        series(ax, d.region, d[f"{c}_p99_us"], k + 1, f"{lab} p99", ls="--",
               lo=d[f"{c}_p99_us_min"], hi=d[f"{c}_p99_us_max"])
        k += 2
    bytes_axis(ax, s.region, "LBA range addressed (file region)")
    plain_log(ax)
    ax.set_ylabel("latency (us, log)")
    ax.set_title("Working set / LBA range: latency")
    subtitle(ax, "bands: min-max over 2 rounds", fixed="4 KiB random, O_DIRECT",
             varied="LBA range, direction, QD (1 or 32)")
    legend_below(ax, ncol=3)
    save(f, "lba_latency")


# ------------------------------------------------------------------------ state
def iops_logs(exp, run, raw):
    """Per-second total IOPS of the sustained segments, in run order."""
    out = []
    t_off = 0.0
    for _, r in raw[raw.tag == "state:sustain:randwrite4k"].sort_values("segment").iterrows():
        logs = C.raw_glob(exp, run, f"j{int(r.job):03d}_r{int(r['round'])}_iops.*.log")
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


def plot_state():
    s = summary("state")
    raw, run = C.load_raw("state")
    ts = iops_logs("state", run, raw)
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
    subtitle(ax, f"{cum.iloc[-1] if len(cum) else 0:.2f} TB written by the host (x2 on the drives: RAID5 "
                 "parity); one ordered run, no repeats (the state cannot be reset without root)",
             fixed=f"4 KiB random write, QD 32 (4 x 8), O_DIRECT, {fmt_bytes(tb)} range", varied="time")
    ax.legend(loc="lower right")
    save(f, "state_sustained_iops")

    f, ax = fig(8.0, 4.2)
    for i, (col, lab) in enumerate((("w_p50_us", "p50"), ("w_p99_us", "p99"), ("w_p99.9_us", "p99.9"))):
        ax.plot(tmid, seg[col], color=SLOTS[i], marker=MARKS[i], label=lab)
    plain_log(ax)
    ax.set_xlabel("time since start of sustained writing (min)")
    ax.set_ylabel("write latency (us, log)")
    ax.set_title("Device state: latency percentiles over time")
    subtitle(ax, "each point = one back-to-back 60 s fio segment; one ordered run, no repeats",
             fixed=f"4 KiB random write, QD 32, O_DIRECT, {fmt_bytes(tb)} range", varied="time")
    ax.legend()
    save(f, "state_sustained_latency")

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
    subtitle(ax, "background garbage collection would show up as slower probes right after; one probe "
                 "per phase, no repeats", fixed="4 KiB random, QD 1, O_DIRECT",
             varied="phase (before / after / 10 min idle), direction")
    save(f, "state_probes")

    # RAID5 device-level amplification from fio's disk_util (qd1, blocksize, rwmix runs)
    rows = []
    for exp, tag, lab in (("qd1", "qd1:rand:read", "4K rand read"), ("qd1", "qd1:rand:write", "4K rand write"),
                          ("qd1", "qd1:seq:write", "128K seq write QD1"),
                          ("blocksize", "bs:seq:write", None), ("rwmix", "mix:rand4k", None)):
        try:
            d = summary(exp)
        except FileNotFoundError:
            continue
        q = d[d.tag == tag]
        if exp == "blocksize":
            for bs in ("128k", "1m", "1536k"):
                r = q[q.bs == bs]
                if len(r):
                    rows.append((f"{bs} seq write QD32", r.iloc[0]))
        elif exp == "rwmix":
            r = q[q.mix == 70]
            if len(r):
                rows.append(("4K rand 70/30", r.iloc[0]))
        elif len(q):
            rows.append((lab, q.iloc[0]))
    if rows:
        f, ax = fig(8.2, 4.4)
        labels = [r[0].replace(" seq write", "\nseq write").replace(" rand", "\nrand")
                  .replace(" QD", "\nQD") for r in rows]
        def col3(c):
            return [[r[1].get(c + sfx, np.nan) for r in rows] for sfx in ("", "_min", "_max")]
        bars(ax, labels, [("drive bytes read per host byte", *col3("mem_read_bytes_per_host_byte")),
                          ("drive bytes written per host byte", *col3("mem_write_bytes_per_host_byte"))],
             "bytes at the 4 drives per host byte", fmt="{:.2f}")
        for y, lab in ((2.0, "small-write RMW: 2 + 2"), (4 / 3, "full stripe: 4/3")):
            ax.axhline(y, color=MUTED, ls=":", lw=0.7)
            ax.text(-0.45, y, lab, fontsize=7, color=INK2, ha="left", va="bottom")
        ax.set_title("What the drives really do (RAID5 read-modify-write)")
        subtitle(ax, "fio disk_util: md124 vs its 4 member NVMe drives over the same job; bars: "
                     "min-max over rounds", fixed="O_DIRECT, 64 GiB range (runs from qd1, blocksize, "
                     "rwmix)", varied="workload")
        legend_below(ax, ncol=2)
        plt.setp(ax.get_xticklabels(), fontsize=7.5)
        save(f, "state_device_io_amplification")


# ------------------------------------------------------------------------ pitfall
def rcol(rows, col):
    """[values, mins, maxes] of one summary column across a list of rows."""
    return [[r.get(col + sfx, np.nan) for r in rows] for sfx in ("", "_min", "_max")]


def plot_pitfall():
    s = summary("pitfall")
    g = lambda tag: s[s.tag == tag].iloc[0] if (s.tag == tag).any() else None  # noqa: E731

    f, ax = fig()
    labs, rs = [], []
    for tag, lab in (("pc:randread4k:direct", "O_DIRECT"), ("pc:randread4k:buffered-cold", "buffered,\ncold cache"),
                     ("pc:randread4k:buffered-warm", "buffered,\nfile in page cache")):
        r = g(tag)
        if r is None:
            continue
        labs.append(f"{lab}\n({r.md_vs_fio_bytes:.2f} B from disk per B)")
        rs.append(r)
    bars(ax, labs, [("IOPS (QD=1, 4 KiB random read)", *rcol(rs, "iops"))], "IOPS (log)",
         fmt="{:,.0f}", log=True)
    ax.set_title("Pitfall: the page cache answers instead of the SSD")
    subtitle(ax, "8 GiB file, 755 GiB of RAM: a warm 'disk' benchmark measures DRAM; bars: min-max over "
                 "2 rounds", fixed="4 KiB random read, QD 1, 8 GiB file", varied="O_DIRECT vs buffered, cache state")
    save(f, "pitfall_page_cache_reads")

    f, ax = fig()
    rd, rb = g("pc:randwrite4k:direct"), g("pc:randwrite4k:buffered")
    if rd is not None and rb is not None:
        bars(ax, ["O_DIRECT", "buffered"],
             [("MB/s fio reports", *rcol([rd, rb], "bw_MBps")),
              ("MB/s reaching /data during the run", *rcol([rd, rb], "md_MBps"))],
             "MB/s (log)", fmt="{:,.0f}", log=True)
        ax.set_title("Pitfall: buffered writes are not disk writes")
        subtitle(ax, "buffered data sits dirty in RAM when fio stops timing; bars: min-max over 2 rounds",
                 fixed="4 KiB random write, QD 1", varied="O_DIRECT vs buffered")
        save(f, "pitfall_buffered_write")

    f, ax = fig(7.6, 4.2)
    cfg = [("qd:libaio-direct-qd32", "libaio O_DIRECT\niodepth 32"),
           ("qd:libaio-buffered-qd32", "libaio buffered\niodepth 32"),
           ("qd:psync-qd32", "psync\niodepth 32"), ("qd:psync-32jobs", "psync\n32 jobs")]
    rs = [(lab, g(t)) for t, lab in cfg if g(t) is not None]
    rr = [r for _, r in rs]
    bars(ax, [l for l, _ in rs], [("requested", [32] * len(rs)),
                                  ("fio's view (IOPS x fio latency)", *rcol(rr, "little_qd")),
                                  ("at the drives (diskstats in-flight)", *rcol(rr, "dev_qd"))],
         "queue depth", fmt="{:.1f}")
    for i, (_, r) in enumerate(rs):
        ax.text(i, -0.18, f"{r.iops / 1e3:.0f}K IOPS", transform=ax.get_xaxis_transform(),
                ha="center", fontsize=7.5, color=INK2)
    ax.set_title("Pitfall: the queue depth you ask for is not the one you get")
    subtitle(ax, "buffered libaio blocks in io_submit: fio 'queues' 32, the SSDs see ~1; bars: min-max "
                 "over 2 rounds", fixed="4 KiB random read, requested QD 32",
             varied="engine, O_DIRECT vs buffered, iodepth vs numjobs")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=3, fontsize=8)
    save(f, "pitfall_effective_qd")

    f, ax = fig()
    a, b = g("fa:randread4k:qd32:unwritten"), g("fa:randread4k:qd32:written")
    c, d = g("fa:randwrite4k:qd1:first-write"), g("fa:randwrite4k:qd1:overwrite")
    if a is not None and b is not None:
        bars(ax, [f"fallocated, never written\n({a.md_vs_fio_bytes:.2f} B from disk per B)",
                  f"written data\n({b.md_vs_fio_bytes:.2f} B from disk per B)"],
             [("4 KiB random read IOPS, QD 32", *rcol([a, b], "iops"))], "IOPS (log)", fmt="{:,.0f}", log=True)
        ax.set_title("Pitfall: reading space that was never written")
        subtitle(ax, "XFS returns zeros for unwritten extents without touching the SSDs; bars: min-max "
                     "over 2 rounds", fixed="4 KiB random read, QD 32, 16 GiB file",
                 varied="extent state (fallocated vs written)")
        save(f, "pitfall_unwritten_reads")
    if c is not None and d is not None:
        f, ax = fig()
        bars(ax, ["first write to\nfallocated space", "overwrite of\nwritten data"],
             [("mean", *rcol([c, d], "w_lat_mean_us")), ("p99", *rcol([c, d], "w_p99_us"))],
             "latency (us)")
        ax.set_title("Pitfall: insufficient preconditioning (first writes)")
        subtitle(ax, "first writes also convert unwritten extents; bars: min-max over 2 rounds",
                 fixed="4 KiB random write, QD 1, 16 GiB file", varied="first write vs overwrite")
        save(f, "pitfall_first_write")

    f, ax = fig()
    pats = ["incompressible", "compress50", "zeros"]
    groups = []
    for wl, col, lab in (("seqwrite128k", "bw_MBps", "128 KiB seq write MB/s"),
                         ("randwrite4k", "iops", "4 KiB rand write IOPS")):
        base = g(f"pattern:{wl}:incompressible")
        if base is None:
            continue
        rows3 = [g(f"pattern:{wl}:{p}") for p in pats]
        groups.append((lab, [r[col] / base[col] if r is not None else np.nan for r in rows3],
                       # conservative ratio range: worst case of both ranges
                       [r[col + "_min"] / base[col + "_max"] if r is not None else np.nan for r in rows3],
                       [r[col + "_max"] / base[col + "_min"] if r is not None else np.nan for r in rows3]))
    if groups:
        bars(ax, ["incompressible", "50% compressible", "all zeros"], groups,
             "throughput relative to incompressible", fmt="{:.2f}")
        ax.axhline(1, color=MUTED, ls="--", lw=0.8)
        ax.set_title("Data pattern: compressibility makes no difference here")
        subtitle(ax, "neither md RAID5 nor these drives compress or deduplicate; bars: min-max ratio over "
                     "2 rounds", fixed="QD 32 (4 x 8), O_DIRECT, 8 GiB file", varied="buffer content, workload")
        save(f, "pitfall_data_pattern")

    f, ax = fig()
    rows = [(lab, g(t)) for t, lab in (("gen:seqwrite128k:qd1:incompressible", "QD1, reused\nbuffers"),
                                       ("gen:seqwrite128k:qd1:refill", "QD1, refill\nevery buffer"),
                                       ("gen:seqwrite128k:qd32:incompressible", "QD32, reused\nbuffers"),
                                       ("gen:seqwrite128k:qd32:refill", "QD32, refill\nevery buffer"))
            if g(t) is not None]
    if rows:
        bars(ax, [l for l, _ in rows], [("MB/s", *rcol([r for _, r in rows], "bw_MBps"))], "MB/s",
             fmt="{:,.0f}")
        for i, (_, r) in enumerate(rows):
            ax.text(i, 0.04, f"in flight {r.little_qd:.2f}\nfio usr {r.usr_cpu:.0f}%",
                    transform=ax.get_xaxis_transform(), ha="center", fontsize=7, color=SURFACE)
        ax.set_title("Pitfall: the load generator is the bottleneck")
        subtitle(ax, "--refill_buffers costs ~3 us of CPU per KiB; bars: min-max over 2 rounds",
                 fixed="128 KiB sequential write, O_DIRECT", varied="QD (1 / 32), buffer refill")
        save(f, "pitfall_generator")


PLOTS = {"qd1": plot_qd1, "blocksize": plot_blocksize, "rwmix": plot_rwmix,
         "qdsweep": plot_qdsweep, "tail": plot_tail, "lba": plot_lba, "state": plot_state,
         "pitfall": plot_pitfall}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("exp", nargs="+")
    a = ap.parse_args()
    exps = list(PLOTS) if a.exp == ["all"] else [C.exp_id(e) for e in a.exp]
    for e in exps:
        if e in PLOTS:
            print(e)
            try:
                PLOTS[e]()
            except FileNotFoundError as err:
                print(f"  skipped: {err}")


if __name__ == "__main__":
    main()
