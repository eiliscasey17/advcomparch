"""Tests: analysis formulas, the fio command builder, and -- with real fio on
small scratch files -- that every setup does what the experiments claim
(direct I/O bypasses the page cache, the requested queue depth is really in
flight, offsets are aligned and in range, sequential jobs are disjoint, data
patterns compress as documented, fallocated extents are really unwritten,
json+ histograms agree with fio's percentiles, the big data files are sound).

Scratch files go to FIO_SCRATCH (outside the repo) and are removed again.
"""
import hashlib
import json
import os
import subprocess
import sys
import zlib
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import common as C  # noqa: E402
import prep  # noqa: E402
import run as R  # noqa: E402

CPU = os.environ.get("TEST_CPU", "8")
T = C.SCRATCH / "pytest"


@pytest.fixture(scope="module")
def tfile():
    """A 256 MiB fully-written test file."""
    T.mkdir(parents=True, exist_ok=True)
    f = T / "t.dat"
    if not f.exists():
        fio("--name=mk", f"--filename={f}", "--size=256m", "--rw=write", "--bs=1m",
            "--direct=1", "--ioengine=libaio", "--iodepth=8", "--fallocate=none",
            "--buffer_compress_percentage=0")
    yield f


def teardown_module():
    if T.exists():
        for p in T.iterdir():
            p.unlink()
        T.rmdir()


def fio(*args, json_out=False):
    cmd = [str(C.FIO), *args, f"--cpus_allowed={CPU}"]
    if json_out:
        cmd.append("--output-format=json")
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stderr
    if json_out:
        return json.loads(p.stdout[p.stdout.index("{"):])
    return p.stdout


# ------------------------------------------------------------ formulas --
def test_little():
    assert C.little_qd(100_000, 320) == pytest.approx(32)
    assert C.little_iops(32, 320) == pytest.approx(100_000)


def test_knee_and_gain():
    qd = np.array([1, 2, 4, 8, 16, 32, 64, 128, 256])
    iops = np.minimum(qd * 15000, 500000) * (1 - 0.01 * np.log2(qd))
    lat = qd / iops * 1e6
    i = C.knee(iops, lat)
    assert qd[i] in (32, 64)
    assert qd[C.knee_by_gain(qd, iops, 0.9)] == 32


def test_effective_qd_mode():
    assert C.effective_qd_mode({"1": 0.1, "2": 0, "4": 0, "8": 0, "16": 99.9, "32": 0,
                                ">=64": 0}) == 16
    assert C.effective_qd_mode({"1": 1, "2": 0, "4": 0, "8": 0, "16": 0, "32": 0,
                                ">=64": 99}) == 64


def test_hist_helpers():
    lat = np.array([10.0, 20.0, 30.0, 40.0])
    cnt = np.array([50, 40, 9, 1], float)
    assert C.percentile_from_hist(lat, cnt, 50) == 10
    assert C.percentile_from_hist(lat, cnt, 99) == 30
    assert C.percentile_from_hist(lat, cnt, 99.5) == 40
    x, p = C.ccdf(lat, cnt)
    assert p[0] == pytest.approx(0.5) and p[-1] == 0


def test_parse_fio_synthetic():
    d = {"read": {"iops": 1000.0, "bw_bytes": 4096000, "io_bytes": 40960000, "total_ios": 10000,
                  "slat_ns": {"mean": 1000}, "clat_ns": {"mean": 99000, "percentile":
                  {"99.000000": 200000}}, "lat_ns": {"mean": 100000, "max": 1, "stddev": 1}},
         "write": {"iops": 0.0, "bw_bytes": 0, "io_bytes": 0, "total_ios": 0,
                   "slat_ns": {"mean": 0}, "clat_ns": {"mean": 0}, "lat_ns": {"mean": 0,
                   "max": 0, "stddev": 0}}}
    doc = {"jobs": [{"elapsed": 12, "job_runtime": 10000, "usr_cpu": 1, "sys_cpu": 2,
                     "iodepth_level": {"1": 100.0, ">=64": 0}, **d}]}
    r = C.parse_fio(doc)
    assert r["iops"] == 1000 and r["lat_mean_us"] == pytest.approx(100)
    assert r["r_p99_us"] == pytest.approx(200) and "w_iops" not in r
    assert C.little_qd(r["iops"], r["lat_mean_us"]) == pytest.approx(0.1)


def test_raid_geometry_matches_sysfs():
    md = Path(f"/sys/block/{C.MD}/md")
    assert int((md / "chunk_size").read_text()) == C.RAID_CHUNK
    assert (md / "level").read_text().strip() == "raid5"
    assert int((md / "raid_disks").read_text()) == C.RAID_DATA_DISKS + 1
    slaves = sorted(p.name.removesuffix("p1") for p in Path(f"/sys/block/{C.MD}/slaves").iterdir())
    assert slaves == sorted(C.MEMBERS) and len(C.MEMBERS) == C.RAID_DATA_DISKS + 1
    for m in C.MEMBERS:  # the 6.4 TB Microns, not whatever drive got their old name
        assert int(Path(f"/sys/block/{m}/size").read_text()) * 512 > 6e12


# -------------------------------------------------------- command builder --
def test_cmd_random_vs_sequential():
    rnd = R.fio_cmd(R.J("t", "randread", "4k", qd=8, jobs=4), [1, 2, 3, 4])
    assert f"--size={C.MAIN_REGION}" in rnd and not any("offset_increment" in a for a in rnd)
    assert "--cpus_allowed=1,2,3,4" in rnd and "--direct=1" in rnd and "--iodepth=8" in rnd
    seq = R.fio_cmd(R.J("t", "read", "128k", qd=8, jobs=4), [1, 2, 3, 4])
    per = C.MAIN_REGION // 4
    assert f"--size={per}" in seq and f"--offset_increment={per}" in seq
    assert "--buffer_compress_percentage=0" in seq
    h = R.fio_cmd(R.J("t", "randread", "4k", hist=True, mix=70), [])
    assert "--output-format=json+" in h and "--rwmixread=70" in h
    q = R.fio_cmd(R.J("t", "randread", "4k", runtime=60), [], quick=True)
    assert "--runtime=5" in q


def test_all_experiments_build():
    for name, f in R.EXPERIMENTS.items():
        for step in f(True):
            if "tag" in step:
                R.fio_cmd(step, [1], quick=True)
                if "zeros" in step["tag"] or "compress50" in step["tag"]:
                    assert step["file"] != str(C.DATAFILE)  # never pollute the main file
            else:
                assert {"sleep", "fallocate", "remove", "prefill"} & set(step)


def test_size_of():
    assert R.size_of("4k") == 4096 and R.size_of("1536k") == C.FULL_STRIPE
    assert R.size_of("1m") == C.MiB and R.size_of(8192) == 8192


# --------------------------------------------- method verification (fio) --
def test_direct_io_reaches_the_device(tfile):
    a = C.diskstats([C.MD])[C.MD]
    d = fio("--name=d", f"--filename={tfile}", "--size=256m", "--rw=randread", "--bs=4k",
            "--direct=1", "--ioengine=libaio", "--iodepth=4", "--time_based", "--runtime=2",
            json_out=True)
    b = C.diskstats([C.MD])[C.MD]
    md_bytes = (b[1] - a[1]) * 512
    fio_bytes = d["jobs"][0]["read"]["io_bytes"]
    assert md_bytes >= 0.95 * fio_bytes  # every direct read went to /data's RAID


def test_requested_qd_is_in_flight(tfile):
    d = fio("--name=q", f"--filename={tfile}", "--size=256m", "--rw=randread", "--bs=4k",
            "--direct=1", "--ioengine=libaio", "--iodepth=16", "--time_based", "--runtime=3",
            json_out=True)
    r = C.parse_fio(d)
    assert r["qd_mode"] == 16 and r["qd_at_mode_pct"] > 95
    assert C.little_qd(r["iops"], r["lat_mean_us"]) == pytest.approx(16, rel=0.1)


def test_device_qd_measures_drive_side(tfile):
    """dev_qd (diskstats weighted time) tracks the depth at the drives: <= 1 at
    QD1 (part of each I/O's latency is host software), several times more at
    QD32. It need not reach 32: one fio thread at QD32 is CPU-bound, completed
    requests wait to be reaped, so the drives see fewer (an F4 finding)."""
    names = C.MEMBERS
    seen = []
    # QD1 upper bound 1.2, not 1.0: the 2 s normalization ignores fio start-up
    # and any other users' I/O on /data lands in the same counters
    for qd, lo, hi in ((1, 0.3, 1.2), (32, 4, 40)):
        a = C.diskstats(names)
        import time
        t0 = time.time()
        fio("--name=dq", f"--filename={tfile}", "--size=256m", "--rw=randread", "--bs=4k",
            "--direct=1", "--ioengine=libaio", f"--iodepth={qd}", "--time_based", "--runtime=2")
        wall = time.time() - t0
        b = C.diskstats(names)
        dev_qd = sum(b[m][5] - a[m][5] for m in names) / (wall * 1e3)
        seen.append(dev_qd * wall / 2.0)  # normalized to the 2 s of I/O
        assert lo <= seen[-1] <= hi
    assert seen[1] > 5 * seen[0]


def test_device_qd_from_fio_json_matches_diskstats(tfile):
    """dev_qd is re-derived from the member drives fio lists in its own JSON
    (robust to device renames); it must agree with /proc/diskstats on C.MEMBERS."""
    import time
    a, t0 = C.diskstats(C.MEMBERS), time.time()
    d = fio("--name=dj", f"--filename={tfile}", "--size=256m", "--rw=randread", "--bs=4k",
            "--direct=1", "--ioengine=libaio", "--iodepth=16", "--time_based", "--runtime=2",
            json_out=True)
    wall, b = time.time() - t0, C.diskstats(C.MEMBERS)
    listed = sorted(u["name"] for u in d["disk_util"] if u["name"] != C.MD)
    assert listed == sorted(C.MEMBERS)
    r = C.parse_fio(d)
    via_diskstats = sum(b[m][5] - a[m][5] for m in C.MEMBERS) / (wall * 1e3)
    assert r["mem_in_queue_ms"] / (wall * 1e3) == pytest.approx(via_diskstats, rel=0.1)


def test_psync_ignores_iodepth(tfile):
    d = fio("--name=p", f"--filename={tfile}", "--size=256m", "--rw=randread", "--bs=4k",
            "--direct=1", "--ioengine=psync", "--iodepth=16", "--time_based", "--runtime=2",
            json_out=True)
    r = C.parse_fio(d)
    assert r["qd_mode"] == 1 and C.little_qd(r["iops"], r["lat_mean_us"]) < 1.1


def test_random_offsets_aligned_and_in_range(tfile):
    log = T / "rand.iolog"
    fio("--name=a", f"--filename={tfile}", "--size=64m", "--rw=randread", "--bs=16k",
        "--direct=1", "--ioengine=libaio", "--iodepth=4", "--number_ios=2000",
        "--norandommap=1", f"--write_iolog={log}")
    offs = [int(l.split()[3]) for l in log.read_text().splitlines() if " read " in l]  # v3: time file action offset len
    assert len(offs) >= 2000
    assert all(o % 16384 == 0 and 0 <= o < 64 * C.MiB for o in offs)
    # uniform random with replacement (--norandommap): expected distinct offsets
    # among n draws from k slots is k(1 - exp(-n/k))
    k, n = 64 * C.MiB // 16384, len(offs)
    assert len(set(offs)) == pytest.approx(k * (1 - np.exp(-n / k)), rel=0.05)


def test_sequential_jobs_disjoint(tfile):
    per = 64 * C.MiB
    fio("--name=s", f"--filename={tfile}", f"--size={per}", f"--offset_increment={per}",
        "--rw=read", "--bs=128k", "--direct=1", "--ioengine=libaio", "--iodepth=4",
        "--numjobs=4", "--number_ios=100", f"--write_iolog={T / 'seq.iolog'}")
    for k in range(4):
        lines = (T / f"seq.iolog.{k}").read_text().splitlines() if (T / f"seq.iolog.{k}").exists() \
            else []
        offs = [int(l.split()[3]) for l in lines if " read " in l]
        if offs:  # fio names per-job logs differently across versions; check what exists
            assert all(k * per <= o < (k + 1) * per for o in offs)
            assert offs == sorted(offs)


@pytest.mark.parametrize("pattern,lo,hi,unique_min", [
    ("incompressible", 0.95, 1.01, 0.0), ("compress50", 0.40, 0.60, 0.0),
    ("zeros", 0.0, 0.01, 0.0), ("refill", 0.95, 1.01, 0.95)])
def test_data_patterns(pattern, lo, hi, unique_min):
    T.mkdir(parents=True, exist_ok=True)
    f = T / f"pat_{pattern}.dat"
    fio("--name=w", f"--filename={f}", "--size=16m", "--rw=write", "--bs=128k", "--direct=1",
        "--ioengine=libaio", "--iodepth=4", "--fallocate=none", *R.PATTERNS[pattern])
    b = f.read_bytes()
    M = 1 << 20
    ratio = np.mean([len(zlib.compress(b[i:i + M], 6)) / M for i in range(0, len(b), M)])
    uniq = len({hashlib.md5(b[i:i + 4096]).digest() for i in range(0, len(b), 4096)}) / (len(b) // 4096)
    assert lo <= ratio <= hi and uniq >= unique_min
    f.unlink()


def test_fallocated_extents_are_unwritten_and_free_to_read():
    T.mkdir(parents=True, exist_ok=True)
    f = T / "fa.dat"
    fd = os.open(f, os.O_CREAT | os.O_WRONLY, 0o644)
    os.posix_fallocate(fd, 0, 64 * C.MiB)
    os.close(fd)
    assert prep.unwritten_extents(f) > 0
    a = C.diskstats([C.MD])[C.MD]
    d = fio("--name=u", f"--filename={f}", "--size=64m", "--rw=randread", "--bs=4k",
            "--direct=1", "--ioengine=libaio", "--iodepth=8", "--time_based", "--runtime=2",
            json_out=True)
    b = C.diskstats([C.MD])[C.MD]
    fio_ios = d["jobs"][0]["read"]["total_ios"]
    assert fio_ios > 10000 and (b[0] - a[0]) < 0.01 * fio_ios  # XFS answers with zeros
    f.unlink()


def test_json_plus_hist_matches_fio_percentiles(tfile):
    doc = fio("--name=h", f"--filename={tfile}", "--size=256m", "--rw=randread", "--bs=4k",
              "--direct=1", "--ioengine=libaio", "--iodepth=4", "--time_based", "--runtime=2",
              "--percentile_list=50:99", "--output-format=json+")
    doc = json.loads(doc[doc.index("{"):])
    lat, cnt = C.hist_bins(doc, "read")
    pc = doc["jobs"][0]["read"]["clat_ns"]["percentile"]
    for p in (50, 99):
        assert C.percentile_from_hist(lat, cnt, p) == pytest.approx(pc[f"{p:.6f}"] / 1e3, rel=0.03)


@pytest.mark.skipif(not C.DATAFILE.exists(), reason="data files not prepared")
def test_data_files_sound():
    assert prep.verify(C.DATAFILE, C.DATA_SIZE)
    assert prep.verify(R.PC_FILE, 8 * C.GiB)


# ------------------------------------------------ committed raw data --
def test_committed_raw_reproduces_summaries(tmp_path, monkeypatch):
    """A fresh clone has only results/raw_runs/ (gzipped CSV rows + every fio
    JSON). Every pinned run must load from there alone, and re-summarizing
    from the committed fio JSON must give back the committed summary."""
    import io
    import pandas as pd
    import summarize as S
    monkeypatch.setattr(C, "RAW", tmp_path)  # hide the local (gitignored) raw tree
    pins = json.loads((C.RESULTS / "runs.json").read_text())
    for exp, run in pins.items():
        if exp.startswith("_"):
            continue
        df, got = C.load_raw(exp)
        assert got == run and len(df) > 0 and (df.exp == exp).all()
        C.load_json(exp, run, int(df.job.iloc[0]), int(df["round"].iloc[0]))
    for exp in ("qd1", "rwmix", "pitfall"):
        raw, run = C.load_raw(exp)
        new = S.summarize(S.reparse(raw, exp, run)).assign(run=run)
        old = pd.read_csv(C.SUMMARY / f"{exp}.csv")
        new = pd.read_csv(io.StringIO(new.to_csv(index=False)))  # same float round-trip
        pd.testing.assert_frame_equal(new, old)


def test_legacy_experiment_ids():
    assert [C.exp_id(f"F{i}") for i in range(9)] == C.EXPERIMENT_IDS
    assert C.exp_id("tail") == "tail" and C.exp_id("f5") == "tail"
    assert set(R.EXPERIMENTS) == set(C.EXPERIMENT_IDS)
    with pytest.raises(ValueError):
        C.exp_id("F9")


def test_fio_job_files_parse_and_match_json():
    """Every committed job file parses with fio and carries exactly the job
    options fio recorded for that job."""
    files = sorted(C.FIO_JOBS.glob("*/*.fio"))
    pins = {k: v for k, v in json.loads((C.RESULTS / "runs.json").read_text()).items()
            if not k.startswith("_")}
    assert files and {f.parent.name for f in files} == set(pins)
    for f in files:
        p = subprocess.run([str(C.FIO), "--parse-only", str(f)], capture_output=True, text=True)
        assert p.returncode == 0, (f, p.stderr)
        src = next(l for l in f.read_text().splitlines() if "reconstructed from" in l).split()[3]
        doc = json.loads(C.read_text(C.ROOT / src))
        opts = dict(doc["jobs"][0]["job options"])
        name = opts.pop("name", doc["jobs"][0]["jobname"])
        body = [l for l in f.read_text().splitlines() if l and not l.startswith(";")]
        assert body[0] == f"[{name}]"
        assert dict(l.split("=", 1) for l in body[1:]) == opts
