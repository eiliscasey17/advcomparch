# proj2: memory-hierarchy characterization on AMD Zen 4

> **AI disclosure:** I used an AI assistant (Claude) to help write the code, such as benchmarks, run scripts and tests, and to make the plots. The writing, analysis and conclusions are my own.

**Report:** [report.pdf](report.pdf)

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
- `results/plots/*.jpg`: one graph per file, named after its experiment.
- `results/summary/*.csv`: the numbers behind every figure (medians, bootstrap 95% CIs, CV).
- `results/raw_runs/<experiment>/`: the raw measurements behind every figure (gzipped CSV, one file per round, plus the run's `.meta.json`).
- `results/env/`: machine state, cpuid geometry, PMU validation and unit-test output.

## Required items and where they are
| Requirement | Experiment ID | Figures (`results/plots/`) | Data (`results/summary/`) |
|---|---|---|---|
| Zero-queue baselines: per-level latency table, units, CPU-frequency conversion | `latency` | `latency_table.jpg` (ns, 95% CI, cycles at the measured GHz, 4 KiB-page variant, fraction served by each level), `latency_by_level.jpg` | `latency.csv` |
| Pattern and granularity sweep: latency and bandwidth, sequential/random, stride/granularity | `pattern` | `pattern_latency.jpg`, `pattern_bandwidth_stride_{core,node}.jpg`, `pattern_bandwidth_granularity_{core,node}.jpg` | `pattern.csv` |
| Read/write mix sweep: throughput and latency for 100% R, 100% W, 70/30, 50/50 | `rwmix` | `rwmix_bandwidth_{core,node}.jpg`, `rwmix_loaded_latency.jpg` | `rwmix.csv` |
| Intensity sweep: throughput vs. latency, the knee, Little's Law | `intensity` | `intensity_latency_vs_bandwidth.jpg` (knees starred, in-flight isolines), `intensity_littles_law.jpg`, `intensity_mlp_{dram,l3}.jpg` | `intensity.csv` |
| Working-set sweep with L1/L2/LLC/DRAM transitions annotated | `workingset` | `workingset_latency.jpg`, `workingset_fill_source.jpg`, `workingset_bandwidth_{core,node}.jpg` | `workingset.csv` |
| Cache-miss impact: footprint/pattern vs. perf counters vs. runtime | `cachemiss` | `cachemiss_runtime_vs_footprint.jpg`, `cachemiss_runtime_vs_{l1_misses,l2_misses,dram_lines}.jpg`, `cachemiss_amat_vs_measured.jpg` | `cachemiss.csv` |
| TLB-miss impact: page locality, 4 KiB vs. 2 MiB huge pages | `tlb` | `tlb_pagerand_latency.jpg`, `tlb_pagerand_misses.jpg`, `tlb_random_chase.jpg`, `tlb_walk_cost.jpg`, `tlb_kernels.jpg` | `tlb.csv` |
| Benchmark pitfalls: cache reuse posing as DRAM, unpinned / remote placement, prefetchable "random", frequency scaling | `pitfall` | `pitfall_cache_reuse_{latency,bandwidth}.jpg`, `pitfall_pinning_{latency,bandwidth}.jpg`, `pitfall_prefetch_random.jpg`, `pitfall_frequency.jpg` | `pitfall.csv` |
| Calibration (clock, PMU validation, cpuid geometry) | `calib` | — | `calib.csv`, `results/env/` |

Old experiment IDs (`E0`–`E8`, in the order `calib latency workingset pattern rwmix intensity cachemiss tlb pitfall`) still work as aliases in every script.

**Repeated trials and error bars:** every configuration is measured as 5–11 samples × 2–3 rounds (1 round for `pitfall`), with job order shuffled each round. Figures show the median.
- **Lines** carry a shaded band and **bars/points** an error bar: the bootstrap 95% CI of the median (2000 resamples).
- **Throughput–latency points** have both x and y bars.
- **Page-walk cost** uses a bootstrap of the ratio of the two page sizes' medians.
- **The pinning pitfall** plots every sample, so its spread is visible directly.

## System configuration
| | |
|---|---|
| **CPU** | 2 × AMD EPYC 9334 (Zen 4 "Genoa", family 19h model 11h), 32 cores per socket = 4 CCDs × 8 cores. Caches: 32 KiB L1d and 1 MiB L2 per core, 32 MiB L3 per CCD (128 MiB per socket). DTLB: 72-entry L1 and 3072-entry L2, for 4 KiB and 2 MiB pages alike (verified against cpuid by the tests). |
| **SMT** | Off: 1 thread per core (`lscpu`). |
| **Memory** | 12 DDR5 channels per socket (24 ranks on the EDAC controller), 384 GiB per NUMA node, 768 GiB total. The DIMM speed is not readable without root; DDR5-4800 (the SKU maximum) gives the 460.8 GB/s per-socket upper bound used in the plots. |
| **NUMA** | 2 nodes (node 0 = cpus 0–31, node 1 = cpus 32–63). Memory is first-touched by the measuring thread, so it is node-local, and every row records its node (`move_pages`). Remote placement is deliberate in `latency` (DRAM remote) and `pitfall`, which first-touch from a cpu on the other socket. |
| **Pages** | THP `always`, defrag `madvise`; no hugetlbfs pool (no root). 2 MiB pages come from `madvise(MADV_HUGEPAGE)` and 4 KiB pages from `MADV_NOHUGEPAGE`. Every row records the achieved THP fraction: 100% or 0% in every run. |
| **OS / kernel** | Red Hat Enterprise Linux 9.8, kernel 5.14.0-687.57.1.el9_8 (exact build in `results/env/environment.txt`). `perf_event_paranoid=2`, NMI watchdog on, so at most 5 user-space PMU counters per group. |
| **Governor / frequency** | `performance` governor, boost on; neither can be changed without root. Every row records PMU cycles / ns: 3.89 GHz on one core, falling to about 3.0 GHz with a whole node loaded (`pitfall_frequency.jpg`). Cycle figures always use the measured clock, never the 2.7 GHz TSC. |
| **Pinning** | `membench` pins its own threads (`pthread_setaffinity_np`). Single-thread probes run on an idle core of the quietest CCD. Multi-thread runs use one NUMA node, one thread per core, round-robin over its 4 CCDs, leaving the node's first core free. The driver process runs off the measured cpus. `pitfall` also runs deliberately unpinned. |
| **Shared-machine gate** | Before each job the measured CCD must carry ≤ 0.5 core of other activity, or the measured node ≤ 2 cores (background services keep about 1.1 busy on every node). During the job, other users' activity is measured as busy jiffies minus our own CPU time. A disturbed job is re-measured, up to 3 attempts; the `attempts` and `others_util` columns record this. |
| **Timing** | `CLOCK_MONOTONIC_RAW`. Chase and kern samples last ≥ 20 ms. bw and loadlat measure all threads together in a fixed 50–200 ms window after a warm-up window. |
| **Tools** | gcc 11.5.0 (`-O2 -march=native`, which resolves to znver4; loop kernels `-O3`). Python 3.11.17 with numpy 2.4.6, pandas 3.0.6, matplotlib 3.11.2. PMU via in-process `perf_event_open` (no `perf` binary on the machine). Intel MLC v3.13 is installed but blocked (see below). |

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
./scripts/run_all.sh                         # env capture, all experiments, summaries, MLC (if present), plots
python scripts/run.py intensity [--quick]; python scripts/summarize.py intensity; python scripts/plot.py intensity
```
- `make test` pins to `TEST_CPU` (default 62).
- **From a fresh clone (no re-measurement):** `summarize.py` and `plot.py` read the committed raw runs in `results/raw_runs/`, pinned by `results/runs.json`. So `python scripts/summarize.py all && python scripts/plot.py all` regenerates every summary and figure, and a test checks it.
