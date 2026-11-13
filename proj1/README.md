# Project 1: How much does SIMD buy? (dot product, elementwise multiply, 3-point stencil)

Single-threaded study of compiler auto-vectorization on an **AMD EPYC 9334 (Zen 4)**,
comparing **GCC 11.5** and **Clang 20.1.8**. The study covers:
- scalar vs. SSE vs. AVX2-width vs. AVX-512
- data type
- alignment / tail
- stride / gather
- working-set size, from L1 to DRAM

Every speedup is checked against compiler reports, disassembly, and hardware counters.

- **Results, methods and status of every experiment: [EXPERIMENTS.md](EXPERIMENTS.md)**
- Figures: `results/plots/*.jpg` (one graph per file, named after its experiment)
- Summaries (the table view of every figure): `results/summary/*.csv`
- Raw measurements behind every figure: `results/raw_runs/<experiment>/` (gzipped CSV, one file per round, plus the run's `.meta.json`)
- Vectorization evidence: [`results/vec_evidence/evidence.md`](results/vec_evidence/evidence.md)
- Exact environment and flags: `results/env/` (`environment.txt`, `flags.json`, `pmu_validation.txt`)

## Kernels
| kernel | definition | FLOP/elem | compulsory B/elem (f32) |
|---|---|---|---|
| dot | `s += x[i]*y[i]` | 2 | 8 |
| elementwise mul | `z[i] = x[i]*y[i]` | 1 | 12 (+4 write-allocate) |
| 3-point stencil | `y[i] = a*x[i-1] + b*x[i] + c*x[i+1]` | 5 | 8 (+4 write-allocate) |

All kernels live in [src/kernels_body.inc](src/kernels_body.inc). There is one file per data type (`kernels_{f32,f64,i32}.c`), and these are the only code compiled with per-configuration flags. Variants:
- multi-accumulator dot
- `assume_aligned` and predicated-tail mul
- compile-time strides 2/4/8
- index-array gathers
- STREAM-style bandwidth kernels

## Build matrix
| config | GCC | Clang |
|---|---|---|
| `scalar` | `-O3 -march=native -fno-tree-vectorize -fno-tree-slp-vectorize` | `-O3 -march=native -fno-vectorize -fno-slp-vectorize` |
| `default` | `-O3 -fopenmp-simd` (x86-64 baseline = SSE2) | same |
| `native256` | `-O3 -march=native -fopenmp-simd` (znver4 tuning picks 256-bit) | `+ -mprefer-vector-width=256` |
| `native512` | `-O3 -march=native -mprefer-vector-width=512 -fopenmp-simd` | same |
| `native512fm` | native512 `+ -ffast-math` (negative-example fix only) | same |

The single source of truth for flags is [config/flags.mk](config/flags.mk). `make flags-json` records the resolved values. Notes:
- The scalar builds use the same `-march=native` ISA and tuning as the native builds, so the only thing that differs is SIMD.
- The scalar builds must not get `-fopenmp-simd`. GCC's `#pragma omp simd` and Clang's `vectorize_predicate` pragma both force vectorization even with the vectorizer disabled. `tests/test_vectorization.py` guards against this.

## Required items and where they are
| Requirement | Experiment ID | Figures (`results/plots/`) | Data (`results/summary/`) |
|---|---|---|---|
| Scalar baseline vs. auto-vectorized, each kernel, across working-set sizes: speedup and throughput | `simd` | `simd_throughput_{dot,mul,stencil}.jpg`, `simd_speedup_{dot,mul,stencil}.jpg` | `simd.csv` |
| Locality sweep across cache levels, where SIMD gains compress | `locality` | `locality_dot_speedup_compression.jpg` (gain per level annotated), `locality_dot_bandwidth.jpg`, `locality_dot_cycles_per_elem.jpg`, `locality_dram_traffic.jpg` | `simd.csv`, `locality.csv` |
| Alignment and tail handling (prologue/epilogue, unaligned loads, masking) | `align` | `align_{mul,stencil,dot}_l1.jpg`, `align_mul_l2.jpg`, `align_line_crossing_loads.jpg`, `tail_sawtooth.jpg`, `tail_cost_by_remainder.jpg`, `tail_small_n_overhead.jpg`, `calib_unaligned_access_cost.jpg` | `align.csv`, `align_tail_cost.csv`, `calib_align.csv` |
| Stride / gather: effective bandwidth and SIMD efficiency | `stride` | `stride_{mul,dot}_bandwidth_{l2,dram}.jpg`, `stride_simd_speedup.jpg` | `stride.csv` |
| Data-type comparison (lanes, arithmetic intensity, op cost) | `dtype` | `dtype_l1_elem_per_cycle.jpg`, `dtype_dram_bandwidth.jpg`, `dtype_simd_speedup.jpg` | `dtype.csv` |
| Vectorization verification (compiler report + disassembly, concise) | `verify` | `verify_counter_matrix.jpg`; excerpts in [`results/vec_evidence/evidence.md`](results/vec_evidence/evidence.md) | `verify.csv`, `verify_vec_summary.csv`, `results/vec_evidence/status_matrix.csv` |
| Roofline with measured bandwidth and estimated peak FLOP rate | `roofline` (uses `calib` + `simd`) | `roofline_f32.jpg`; inputs: `calib_peak_flops.jpg`, `calib_bandwidth_by_level.jpg` | `calib_roofs.csv`, `calib_peak.csv` |
| Negative example, verified and explained | `negative` | `negative_dot_not_really_vectorized.jpg` (reported "vectorized" but serial), `negative_mul_memory_bound.jpg` (vectorized, bandwidth-bound) | `negative.csv`, `simd.csv` |
| Extra: multi-accumulator dot product | `multiacc` | `multiacc_dot.jpg` | `multiacc.csv`, `multiacc_fit.csv` |

Old experiment IDs (`E0`–`E9`, in the order `calib simd locality align stride dtype verify roofline negative multiacc`) still work as aliases in every script.

**Error bars:** every line has a shaded band and every bar or point an error bar: the bootstrap 95% CI of the median (2000 resamples). Speedups use the CI of the ratio of medians. The exceptions say so in their subtitle:
- **PMU instruction and misaligned-load counts:** exact, no spread.
- **Roofline points:** bars show the min–max over the regime's sizes.
- **Tail cost by remainder:** standard error over the n values sharing a remainder.
- **Multi-accumulator slopes:** 95% CI of the fitted slope.

## Methodology
| | |
|---|---|
| **CPU** | AMD EPYC 9334 (Zen 4, family 19h model 11h), 2 sockets × 32 cores; 32 KiB L1d and 1 MiB L2 per core, 32 MiB L3 per 8-core CCD. One core measured at a time. |
| **ISA** | x86-64 with AVX2, FMA, AVX-512 F/DQ/CD/BW/VL/IFMA/VBMI/VBMI2/VNNI/BF16/BITALG/VPOPCNTDQ. Zen 4 executes 512-bit ops as 2 × 256-bit (the `calib` experiment measures this). |
| **SMT** | Off: 1 thread per core (`lscpu`), so there is no sibling hyper-thread. |
| **OS** | Red Hat Enterprise Linux 9.8, kernel 5.14.0 (`results/env/environment.txt` has the exact build). THP `always`. |
| **Frequency policy** | `performance` governor with boost on; neither can be changed without root. Every sample records PMU core cycles, and cycle metrics use those (≈ 3.89 GHz measured), not the 2.7 GHz TSC. |
| **Compilers** | GCC 11.5.0 (Red Hat) and Clang 20.1.8. Per-build flags are in the build matrix above and `config/flags.mk`; resolved values are in `results/env/flags.json`. Harness and tools: `gcc -O2`. Reference implementation: `-O0 -ffp-contract=off`. |
| **Timing** | `CLOCK_MONOTONIC_RAW` around each sample (plus `rdtsc` and PMU cycles). One sample is `reps` back-to-back calls, with `reps` calibrated so the sample lasts ≥ 2 ms, after 1 warm-up call. `multiacc` also runs with an `lfence` between calls to measure per-call latency. |
| **Repetitions** | Typically 11 samples × 3 rounds per configuration (per-experiment values in [EXPERIMENTS.md](EXPERIMENTS.md#common-method)). Builds and configurations run in a different shuffled order each round. Reported value: median; error: bootstrap 95% CI; CV > 5% is flagged in the summaries. |
| **Pinning and interference** | One thread pinned with `taskset` to an idle core on the quietest CCD. The other 7 cores of the CCD are monitored during every job, and a job is re-measured if they show more than 0.5 core of activity. |
| **Data initialization** | Arrays are allocated 2 MiB-aligned, each array staggered by k × 1088 B (avoids 4 KiB aliasing), plus the experiment's byte offset. Inputs come from a fixed-seed xorshift64* generator: f32/f64 uniform in [0.5, 1.5), so there are no denormals, NaNs or zeros; i32 is in {−2..2}, so there is no overflow. Outputs are zeroed. Pages are first-touched by the pinned thread, so memory is NUMA-local. The data is identical in every run. |
| **Correctness** | Each of the 10 builds is checked against the `-O0` reference at sizes 0..4109, at 8 byte offsets, with guard bands (`make test`). |
| **Counters** | `perf_event_open` in-process, user space only. Each raw Zen 4 event encoding is validated against inline-asm loops with known counts (`results/env/pmu_validation.txt`). |

## Reproduce
```bash
conda env create -f environment.yml        # python + clang 20 + nodejs (palette check)
make -j16                                   # 10 kernel builds + tools
make test                                   # C correctness (10 builds), PMU validation, pytest
./scripts/run_all.sh                        # all experiments, one pinned core (~25 min on an idle CCD)
python scripts/summarize.py all && python scripts/plot.py all && python scripts/vec_evidence.py
```
- **One experiment:** `python scripts/run.py stride` (append `--quick` for a smoke test).
- **From a fresh clone (no re-measurement):** `summarize.py` and `plot.py` read the committed raw runs in `results/raw_runs/`, so `python scripts/summarize.py all && python scripts/plot.py all` regenerates every summary and figure.
- **Pinning:** the runner picks an idle core on the quietest CCD of NUMA node 0 (`scripts/pick_core.py`). It keeps itself off that CCD and records neighbour-core utilization for every job.
