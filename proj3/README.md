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
- `results/plots/*.jpg`: one graph per file.
- `results/summary/*.csv`: the numbers behind every figure. `*_hist.csv` holds the pooled latency histograms behind the CCDF plots.
- `results/env/`: storage stack, tool versions, data-file verification.

## System under test (no root)
Without root, fio can only reach the NVMe drives through a filesystem. So the system under test is the whole stack a user of this machine gets:

| layer | details |
|---|---|
| filesystem | `/data`: XFS (sunit 512 KiB, swidth 1.5 MiB), 18 TB, shared with other users |
| RAID | `md124`: mdadm RAID5, 512 KiB chunk, 3 data + 1 parity, so a full stripe is 1.5 MiB; `group_thread_cnt=0` (parity on one kernel thread) |
| drives | 4 × Micron MTFDKCC6T4TGQ, 6.4 TB enterprise TLC NVMe (PCIe 4) on NUMA node 0; `none` scheduler; 64 hardware queues each |
| host | 2 × EPYC 9334, 755 GiB RAM; fio 3.43 from conda-forge; `libaio` engine (the kernel disables io_uring: `io_uring_disabled=2`) |

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
python -m pytest -q tests                    # formulas + fio method verification (~25 s)
./scripts/run_all.sh                         # env, F0-F8, summaries, plots (~4 h; resumable)
python scripts/run.py F4 [--quick]; python scripts/summarize.py F4; python scripts/plot.py F4
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
