# proj2: memory-hierarchy characterization on AMD Zen 4

This project measures the L1 / L2 / L3 / DRAM hierarchy of a 2-socket AMD EPYC 9334 (Zen 4, "Genoa"). It then explains kernel performance through locality, concurrency (Little's Law), AMAT, and cache and TLB misses.

What it covers:
- zero-queue latency per level, local and remote
- bandwidth by access pattern, stride, granularity and read/write mix
- the throughput–latency curve as intensity grows, and its knee
- working-set transitions
- PMU-backed cache-miss and TLB-miss impact on simple loop kernels
- benchmark pitfalls

Where things are:
- **[EXPERIMENTS.md](EXPERIMENTS.md)**: the central registry. For every experiment it gives the method, run ids, results, interpretation and figures.
- `results/plots/*.jpg`: one graph per file.
- `results/summary/*.csv`: the numbers behind every figure (medians, bootstrap 95% CIs, CV).
- `results/env/`: machine state, cpuid geometry, PMU validation and unit-test output.

## Machine
| | |
|---|---|
| CPU | 2 × EPYC 9334, 32 cores each (4 CCDs × 8), no SMT, boost on, `performance` governor |
| Caches | 32 KiB L1d and 1 MiB L2 per core; 32 MiB L3 per CCD (128 MiB per socket) |
| DTLB | L1: 72 entries; L2: 3072 entries (4 KiB and 2 MiB pages alike) |
| DRAM | 12 × DDR5 channels per socket, 384 GiB per node. Peak is at most 460.8 GB/s per socket at DDR5-4800. |
| OS | RHEL 9 kernel 5.14. THP is `always` with defrag `madvise`. No hugetlbfs pool and no root. `perf_event_paranoid=2`. |

## Tools
- **`build/membench`** (`src/`) is our MLC-equivalent probe. It pins its own threads and prints one CSV row per sample.

  | mode | what it measures |
  |---|---|
  | `chase` | Dependent pointer chase (latency). Patterns: Sattolo single-cycle `random`, `seq` with stride, `fakerand` (constant step), and `pagerand` (one line per 4 KiB page, for the TLB). `--chains k` runs k independent chases in lockstep to measure memory-level parallelism. |
  | `bw` | Bandwidth kernels. Granularity 8/16/32/64 B, any stride, `seq` or `random` order, exact R:W access ratio, normal or non-temporal stores, and an injection delay. Runs on any number of pinned threads. |
  | `loadlat` | MLC-style loaded latency: 1 chase thread plus N throttled generators. |
  | `kern` | The loop kernels `z[i]=x[i]*y[i]` and `y[i]=a*x[i]+y[i]`, contiguous, strided or gathered. |
  | `info` | cpuid cache/TLB geometry. |

  `--pages 4k|2m` applies `madvise(NOHUGEPAGE / HUGEPAGE)` before first touch. Every row reports the THP fraction actually achieved (read from `/proc/self/smaps`) and the NUMA node of the memory (via `move_pages`).
- **PMU:** in-process `perf_event_open` (`src/perfctr.c`, from proj1). There is no `perf` binary here, and none is needed. Events used:
  - L1D fills by source (0x44): L2, own-CCD L3, other CCD, DRAM
  - L2 demand misses (0x64)
  - L2-prefetch L3 misses (0x72)
  - L1 and L2 DTLB misses (0x45)
  - cycles and instructions

  Every encoding is validated by `tests/test_perfctr.c` against probes with known miss behaviour ([`results/env/pmu_validation.txt`](results/env/pmu_validation.txt)).
- **Intel MLC (optional cross-check).** It is not redistributable: download `mlc_v3.13.tgz` after accepting Intel's license, unpack it into `tools/mlc/`, then run `python scripts/mlc.py`.
  - **It is blocked on this machine.** MLC v3.13 will only run with reserved hugetlbfs pages, and reserving them needs root (`echo 4000 > /proc/sys/vm/nr_hugepages`). The script records this in `results/env/mlc_status.txt`.
  - Without root, MLC also cannot switch off the prefetchers.

## Reproduce
```
conda env create -f environment.yml          # same env as proj1
make && make test                            # C unit tests + PMU validation + pytest
./scripts/run_all.sh                         # env capture, E0-E8, summaries, MLC (if present), plots
python scripts/run.py E5 [--quick]; python scripts/summarize.py E5; python scripts/plot.py E5
```
`make test` pins to `TEST_CPU` (default 62).

## Measurement method (short)
- **Pinning.** Single-thread probes run on an idle core of the quietest CCD. Multi-thread runs use the quieter NUMA node, one thread per core round-robin over its 4 CCDs, and leave the node's first core alone. The driver process runs off the measured cpus.
- **Shared machine.**
  - Before each job, the measured CCD must be quiet (≤ 0.5 core of other activity) or the measured node must be (≤ 2 cores; background services keep about 1.1 cores busy on every node).
  - During each job, the other users' activity on it is measured as busy jiffies minus our own child CPU time.
  - A disturbed job is re-measured, up to 3 attempts; the `attempts` and `others_util` columns record this.
- **Memory.** 2 MiB pages unless a 4 KiB comparison is the point. First touch happens on the measuring thread, so memory is local unless remote placement is the point.
- **Timing.** Chase and kern samples last ≥ 20 ms each. bw and loadlat use fixed 50–200 ms windows on all threads at once, after a warm-up window. Results are medians over 5–11 samples × 2–3 rounds, with job order shuffled each round.
- **Clock.** Boost cannot be disabled. Every row records PMU cycles / ns, so cycle figures use the measured clock: 3.89 GHz on one core, about 2.9 GHz with all cores loaded (see the E8 frequency pitfall).
