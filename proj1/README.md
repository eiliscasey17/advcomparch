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
- Figures: `results/plots/*.jpg` (one graph per file)
- Summaries (the table view of every figure): `results/summary/*.csv`
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

## Reproduce
```bash
conda env create -f environment.yml        # python + clang 20 + nodejs (palette check)
make -j16                                   # 10 kernel builds + tools
make test                                   # C correctness (10 builds), PMU validation, pytest
./scripts/run_all.sh                        # all experiments, one pinned core (~25 min on an idle CCD)
python scripts/summarize.py all && python scripts/plot.py all && python scripts/vec_evidence.py
```
- **One experiment:** `python scripts/run.py E4` (append `--quick` for a smoke test).
- **Pinning:** the runner picks an idle core on the quietest CCD of NUMA node 0 (`scripts/pick_core.py`). It keeps itself off that CCD and records neighbour-core utilization for every job.

## Measurement method (short)
- **Pinning and memory:** one thread, pinned with `taskset`. Memory is allocated by first touch on the pinned thread, and arrays are 2 MiB-aligned and staggered by 1088 B to avoid 4K aliasing.
- **Timing:** adaptive repetitions (≥2 ms per sample), 1 warm-up, 11 samples. There are 3 rounds, and the build/config order is shuffled in each round.
- **Statistics:** median, p10/p90, bootstrap 95% CI. Speedup CIs come from bootstrapped ratios of medians.
- **Counters:** core cycles, instructions, retired FLOPs, FP ops by vector width, misaligned loads and DRAM-fill estimates, all read in-process with `perf_event_open`. There is no `perf` binary on this machine. Raw Zen 4 encodings are validated by `tests/test_perfctr.c` against inline-asm loops with known counts.
- **Frequency:** boost can't be disabled without root, so cycles/element uses PMU core cycles (≈3.89 GHz measured), not the TSC.
