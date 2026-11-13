# proj3: NVMe storage, throughput–latency and queueing (fio)

This project measures how NVMe storage trades latency for throughput as concurrency grows, using fio. It covers:
- QD=1 latency for 4 KiB random and 128 KiB sequential reads and writes
- peak IOPS and bandwidth
- block-size, read/write-mix and queue-depth / parallelism sweeps
- the knee of the throughput–latency curve, explained with Little's Law
- p95/p99/p99.9 tail latency
- LBA-range effects
- device state (burst vs sustained writes, idle recovery)
- common benchmarking pitfalls

Where things are:
- **[EXPERIMENTS.md](EXPERIMENTS.md)**: the central registry, with method, run ids, results, interpretation and figures for every experiment.
- `results/plots/*.jpg`: one graph per file, named after its experiment.
- `results/summary/*.csv`: the parsed data behind every figure (medians, min–max over rounds, bootstrap CIs). `*_hist.csv` holds the pooled latency histograms behind the CCDF plots.
- `results/raw_runs/<experiment>/`: the raw results behind every figure, gzipped. That's every fio JSON (`<run>/jNNN_rR.json.gz`), the IOPS logs, the per-job CSV rows and the run's `.meta.json`. The runs are pinned in `results/runs.json`.
- `results/fio_jobs/<experiment>/*.fio`: one fio job file per job, rebuilt from the options fio recorded in its own JSON, i.e. exactly what ran (`scripts/export_fio_jobs.py`). Run one with `fio --output-format=json <file>`.
- `results/env/`: storage stack, tool versions, data-file verification.

## Required items and where they are
| Requirement | Experiment ID | Figures (`results/plots/`) | Data (`results/summary/`) |
|---|---|---|---|
| Zero-queue baselines: QD=1 latency, 4 KiB random R/W and 128 KiB sequential R/W, with p95/p99 | `qd1` | `qd1_latency_table.jpg` (IOPS, MB/s, mean, p50/p95/p99/p99.9, ranges), `qd1_latency_percentiles.jpg`, `qd1_latency_ccdf.jpg` | `qd1.csv`, `qd1_hist.csv` |
| Block-size sweep: throughput and latency from the same runs, random and sequential | `blocksize` | `blocksize_throughput_{random,sequential}.jpg`, `blocksize_latency_{random,sequential}.jpg` | `blocksize.csv` |
| Read/write mix sweep at a fixed block size and pattern | `rwmix` | `rwmix_throughput_{rand4k,seq128k}.jpg`, `rwmix_latency_{rand4k,seq128k}.jpg` | `rwmix.csv` |
| Queue-depth / parallelism sweep: throughput-vs-latency curve, knee, Little's Law | `qdsweep` | `qdsweep_curve_{randread4k,randwrite4k,seq128k}.jpg` (knee starred, Little's-Law isolines), `qdsweep_iodepth_vs_jobs.jpg`, `qdsweep_little_check.jpg` | `qdsweep.csv` |
| Tail latency at mid QD and near-knee QD (and past the knee) | `tail` | `tail_ccdf_{randread4k,randwrite4k,randrw70-4k}.jpg`, `tail_percentiles.jpg` | `tail.csv`, `tail_hist.csv` |
| Working set / LBA range | `lba` | `lba_iops_qd32.jpg`, `lba_latency.jpg` | `lba.csv` |
| Device state: burst vs steady state, idle recovery, RAID5 amplification | `state` | `state_sustained_iops.jpg`, `state_sustained_latency.jpg`, `state_probes.jpg`, `state_device_io_amplification.jpg` | `state.csv`, `state_hist.csv` |
| Misleading setups: page cache, buffered writes, wrong effective QD, unwritten extents / no preconditioning, data pattern, CPU-bound generator | `pitfall` | `pitfall_page_cache_reads.jpg`, `pitfall_buffered_write.jpg`, `pitfall_effective_qd.jpg`, `pitfall_unwritten_reads.jpg`, `pitfall_first_write.jpg`, `pitfall_data_pattern.jpg`, `pitfall_generator.jpg` | `pitfall.csv` |
| Calibration: fio's software floor (null engine), QD1 sanity check | `calib` | — | `calib.csv` |

Old experiment IDs (`F0`–`F8`, in the order `calib qd1 blocksize rwmix qdsweep tail lba state pitfall`) still work as aliases in every script.

**Figure conventions**
- **Fixed vs. varied knobs:** every figure states them on its second subtitle line, e.g. "Fixed: 4 KiB random, QD 32 (4 × 8), O_DIRECT, 64 GiB range | Varied: read share".
- **Error bars:** most configurations ran 2–3 rounds of 20–60 s, so bars and bands show the **min–max over rounds**. With 2–3 values, a range is more honest than a bootstrap CI; the summaries also carry bootstrap CIs (`_lo`/`_hi`) for the primary metrics.
- **CCDFs** pool every I/O of every round; their round-to-round spread is shown on `tail_percentiles.jpg`.
- **Device state** is a single ordered run. It can't be repeated without re-preconditioning, which would mean rewriting other users' data, so those figures say "no repeats".

## Methodology: system under test (no root)
Without root, fio can only reach the NVMe drives through a filesystem. So the system under test is the whole stack a user of this machine gets:

| layer | details |
|---|---|
| filesystem | `/data`: XFS (sunit 512 KiB, swidth 1.5 MiB), 18 TB, shared with other users |
| RAID | `md124`: mdadm RAID5, 512 KiB chunk, 3 data + 1 parity, so a full stripe is 1.5 MiB; `group_thread_cnt=0` (parity on one kernel thread) |
| drives | 4 × Micron MTFDKCC6T4TGQ (firmware E3MQ000), 6.4 TB enterprise TLC NVMe (`nvme2n1`–`nvme5n1`), all on NUMA node 0. `none` scheduler, 64 hardware queues and `nr_requests` 1023 each. |
| interface | Each drive is on **PCIe 4.0 x4** (16 GT/s, running at its maximum link): about 7.9 GB/s of link bandwidth per drive and 31.5 GB/s across the four. |
| filesystem vs raw device | Every test goes **through the filesystem** to files on `/data` (XFS on md RAID5). Raw block-device access needs root. md and the four drives are observed through fio's `disk_util` and `/proc/diskstats`. |
| direct I/O | `--direct=1` (O_DIRECT) with `--ioengine=libaio` for every measurement, except where the pitfall experiment deliberately uses buffered I/O, `psync`, or the `null` engine (calibration). |
| capacity / range used | One 224 GiB prefilled test file (`data.dat`); 64 GiB of it is addressed unless an experiment varies the range (`lba`: 16 MiB to 224 GiB). The total budget is ≤ 256 GiB of an 18 TB shared filesystem, about 1.3% of it. |
| host | 2 × AMD EPYC 9334 (Zen 4, 32 cores each, SMT off), 755 GiB RAM. fio jobs are pinned one per cpu on node 0 (the NVMe node); the driver runs elsewhere. |
| OS / kernel / tools | Red Hat Enterprise Linux 9.8, kernel 5.14.0-687.61.1.el9_8. fio 3.43 from conda-forge with the `libaio` engine; the kernel disables io_uring (`io_uring_disabled=2`). Python 3.11.17, numpy 2.4.6, pandas 3.0.6, matplotlib 3.11.2. |
| temperature | **Not available.** No NVMe hwmon sensor is exposed without root, and SMART needs root, so thermal throttling can't be observed directly. The 40-minute sustained-write run shows no throughput decay, which is the observable signature throttling would leave. |

Not observable without root: SMART, temperature, throttling and the drives' internal state. Raw-device I/O is impossible, as is device-wide preconditioning, which would mean overwriting other users' data.

## Test files (outside the repo, 256 GiB budget)
`scripts/prep.py` creates and verifies the files in `$FIO_SCRATCH` (default `/data/home/caseye2/fio_proj3`):

| file | size | purpose |
|---|---|---|
| `data.dat` | 224 GiB | main file |
| `pagecache.dat` | 8 GiB | page-cache pitfall |
| `fallocated.dat`, `pattern.dat` | 16 / 8 GiB | temporary, during F8 only |

The prefill writes every byte once, sequentially, in full stripes, with unique incompressible data. Verification checks:
- full allocation
- no unwritten extents
- per-4 KiB-block zlib ratio > 0.95
- no zero chunks

`python scripts/prep.py --clean` removes everything at the end.

## Reproduce
```
conda env create -f environment.yml          # same env as proj1/2 + fio
python scripts/prep.py                       # ~6 min, writes 232 GiB
python -m pytest -q tests                    # formulas, fio method verification, committed data and job files (~70 s)
./scripts/run_all.sh                         # env, every experiment, summaries, plots (~4 h; resumable)
python scripts/run.py qdsweep [--quick]; python scripts/summarize.py qdsweep; python scripts/plot.py qdsweep
python scripts/export_fio_jobs.py            # rebuild results/fio_jobs/ from the pinned runs' fio JSON
```
**From a fresh clone (no re-measurement):** `summarize.py` and `plot.py` read the committed raw results in `results/raw_runs/`, so the command below regenerates every summary and figure, and a test checks it.
```
python scripts/summarize.py all && python scripts/plot.py all
```

## Measurement method (short)
- **fio settings.**
  - `--direct=1 --ioengine=libaio`, time-based with a ramp, `--norandommap`, random offsets aligned to the block size.
  - `--group_reporting`, full JSON. `json+` latency histograms are kept for the tail experiments.
  - Every raw JSON is kept, and `summarize.py` re-parses them.
- **Placement.** Jobs are pinned one per CPU on the NVMe node (node 0), round-robin over its CCDs. The driver runs elsewhere.
- **Data pattern** (`PATTERNS` in `run.py`, verified by tests).
  - Writes are incompressible (`--buffer_compress_percentage=0`; zlib ratio 1.00).
  - fio reuses its buffers, so written data is dedupable; neither md nor these drives deduplicate.
  - `--refill_buffers` would make every block unique, but it costs about 3 µs of CPU per KiB and turns fio into the bottleneck (F8).
- **Shared machine.**
  - Before each job, `/data` must be quiet (≤ 300 IOPS and ≤ 30 MB/s of other traffic).
  - During each job, foreign traffic = md124's byte delta minus fio's own block I/O, taken from the kernel's per-process accounting (`getrusage` children `ru_inblock`/`ru_oublock`).
  - A job that saw more than 30 MB/s of foreign traffic is re-measured, up to 3 attempts.
- **Device view.**
  - Per job, md124 and its member drives are read from `/proc/diskstats`.
  - This gives the real queue depth at the drives (time-weighted requests in flight) and RAID5 read-modify-write amplification (drive bytes per host byte).
