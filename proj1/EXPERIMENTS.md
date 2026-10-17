# EXPERIMENTS: central registry (methods, status, results)

This is the single place to find what was run, how, and what it showed. The figures live in `results/plots/` (one graph per JPG). The numbers behind every figure are in `results/summary/*.csv`. Raw samples are in `results/raw/` (gitignored) and can be regenerated with the commands below.

**Machine:**
- AMD EPYC 9334 (Zen 4)
- 1 core measured at a time, at about 3.89 GHz under boost (the `performance` governor)
- 32 KiB L1d / 1 MiB L2 per core, 32 MiB L3 per 8-core CCD

**Compilers:** GCC 11.5.0 (RH) and Clang 20.1.8.

**Flags:** [README](README.md#build-matrix), with the exact resolved values in `results/env/flags.json`.

**Status (2026-10-17):** all experiments complete. Verification passes:
- 10 × 22,755 C correctness checks
- 19 + 1 PMU validations
- 757 pytest assertions

**Reproducibility:** E1 was run twice. The first run had 23 jobs disturbed by another user's job on the shared CCD. The per-configuration medians of the two runs agree within ±1% (5th–95th percentile) in every cache regime.

## Common method
- **Pinning:** one thread, pinned with `taskset` to an idle core on the quietest CCD of NUMA node 0 (`scripts/pick_core.py`). The driver keeps itself off that CCD.
- **Interference handling:** before each job and during it, the other 7 cores of the CCD are sampled. A job that sees more than 0.5 cores' worth of neighbour activity is re-measured after the CCD goes quiet (`attempts` column; the counts are in the table below).
- **Memory:** first-touch on the pinned thread puts pages on the local node. Arrays are 2 MiB-aligned plus k·1088 B of stagger (no 4K aliasing, alignment class unchanged) plus the experiment's byte offset.
- **Timing:** each sample is ≥2 ms of back-to-back calls, after 1 warm-up. Defaults are 11 samples × 3 rounds, with the build/config order shuffled in each round.
- **Statistics:** the reported value is the median over all samples. Errors are bootstrap 95% CIs, and speedups use bootstrapped ratios of medians. A point with CV > 5% is flagged in the summary `cv` column.
- **Metrics:**
  - GFLOP/s uses the analytic FLOPs: dot 2, mul 1, stencil 5 per element. These match the PMU-retired FLOPs exactly (2.000 / 1.000 / 5.000).
  - GiB/s counts compulsory bytes, with no write-allocate.
  - Cycles/element uses PMU core cycles, not the TSC.
- **Working-set sweep:** 8 KiB → 1 GiB total across all arrays, at 4 points per octave, with n a multiple of 64.
- **PMU:** `perf_event_open` in-process, user space only. Every raw Zen 4 encoding is validated by `tests/test_perfctr.c` against inline-asm loops with known counts ([`results/env/pmu_validation.txt`](results/env/pmu_validation.txt)). Each event set holds at most 5 counters, because the NMI watchdog holds one of the six.

| ID | run_id | core | rounds × samples | jobs | wall time | jobs re-measured |
|---|---|---|---|---|---|---|
| E0 | 20261017-190935 | 1 | 3 × 11 | 6 | 97 s | 0 |
| E1 | 20261017-184309 | 9 | 3 × 11 | 24 | 591 s | 1 |
| E2 | 20261017-185301 | 17 | 2 × 7 | 4 | 98 s | 1 |
| E3 | 20261017-185441 | 19 | 3 × 11 | 212 | 251 s | 5 |
| E4 | 20261017-185853 | 1 | 3 × 11 | 144 | 219 s | 1 |
| E5 | 20261017-190233 | 17 | 3 × 11 | 135 | 80 s | 0 |
| E6 | 20261017-190355 | 1 | 1 × 5 | 200 | 3 s | 0 |
| E8 | 20261017-190359 | 1 | 2 × 9 | 12 | 141 s | 0 |
| E9 | 20261017-190621 | 25 | 3 × 11 | 56 | 16 s | 0 |

The commands for every experiment are:
- `python scripts/run.py <ID>`, then
- `python scripts/summarize.py <ID>`, then
- `python scripts/plot.py <ID>`.

`./scripts/run_all.sh` runs the whole suite.

---

## E0: Calibration (peak FLOP rate, bandwidth per level, unaligned-access cost)
**Question:** what are this core's real ceilings?

**Method:**
- `src/calib_peak.c`: inline asm, 12 independent FMA chains per width, plus dependent chains for latency.
- `src/calib_align.c`: 8 vmovups per iteration at a byte offset.
- STREAM-style `bw_*` kernels over the size sweep (gcc native512).

**Results:**
- **Clock:** 3.89 GHz measured (PMU cycles / ns) in every test.
- **Peak FMA, FLOP/cycle:**

  | dtype | scalar | 128-bit | 256-bit | 512-bit |
  |---|---|---|---|---|
  | f32 | 4.0 | 16.0 | 32.0 | **32.0** |
  | f64 | 4.0 | 8.0 | 16.0 | 16.0 |

  - The f32 peak is 125 GFLOP/s and the f64 peak 62 GFLOP/s.
  - **512-bit gives no FLOP/cycle gain:** Zen 4 double-pumps 512-bit operations on 2 × 256-bit FMA pipes.
- **Latency (cycles):**

  | op | latency |
  |---|---|
  | vaddss / vaddps / vmulps | 3 |
  | vfmadd231ps | 4 |
  | vpaddd | 1 |
  | vpmulld | 3 |

  - vpmulld has half the throughput of vpaddd.
- **Single-core bandwidth, GB/s, best kernel (read-only):**

  | level | GB/s |
  |---|---|
  | L1 | 315 (177) |
  | L2 | 124 |
  | L3 | 96 |
  | DRAM | 49 |

  - Stores also pay a hidden read-for-ownership. The write-only kernel reaches only 35 GB/s from DRAM.
- **Unaligned access, cycles per instruction (aligned → misaligned):**

  | access | aligned | misaligned |
  |---|---|---|
  | 256-bit load | 0.5 | 1.0 (only when it crosses a 64 B line: offsets 48, 60) |
  | 512-bit load | 1.0 | 1.5 at every offset except +32, which is free |
  | 256-bit store | 1.0 | 2.0 unless 32 B-aligned |
  | 512-bit store | 2.0 | 3.1 unless 32 B-aligned |

  - A 512-bit access executes as two 256-bit halves, so it only pays when a half straddles a line (loads) or a 32 B boundary (stores).
  - **Counter caveat:** the MA64 event counts every aligned zmm load once, and misaligned ones twice.

**Figures:** `e0_peak_flops.jpg`, `e0_bandwidth_by_level.jpg`, `e0_unaligned_access_cost.jpg`. Data: `E0_peak.csv`, `E0_roofs.csv`, `E0_align.csv`, `E0_bw.csv`.

## E1: Scalar vs. auto-vectorized across working-set sizes (required plot 1)
**Question:** how much does SIMD buy for each kernel, and where?

**Method:**
- Kernels: `dot_simd`, `mul`, `stencil` (f32).
- Builds: {gcc, clang} × {scalar, default, native256, native512}.
- The full size sweep with `core` counters.

**Results:** median GFLOP/s per regime, with the speedup vs. same-compiler scalar in parentheses. L3 uses 2–16 MiB and DRAM uses ≥256 MiB.

| kernel | build | L1 | L2 | L3 | DRAM |
|---|---|---|---|---|---|
| dot | gcc scalar | 2.62 | 2.59 | 2.59 | 2.58 |
| dot | gcc default (SSE) | 10.8 (4.1×) | 10.4 (4.0×) | 10.3 (4.0×) | 9.8 (3.8×) |
| dot | gcc native256 | 23.0 (8.8×) | 20.9 (8.1×) | 20.6 (8.0×) | 11.6 (4.5×) |
| dot | gcc native512 | 42.8 (16.3×) | 31.0 (11.9×) | 23.4 (9.0×) | 12.0 (4.7×) |
| dot | clang native512 | 54.6 (27.3×) | 31.0 (15.9×) | 23.3 (12.0×) | 12.0 (6.2×) |
| mul | gcc scalar | 3.80 | 3.68 | 3.63 | 2.90 |
| mul | gcc native256 | 14.9 (3.9×) | 9.7 (2.6×) | 7.8 (2.2×) | 3.69 (1.27×) |
| mul | gcc native512 | 24.3 (6.4×) | 9.9 (2.7×) | 7.7 (2.1×) | 3.73 (1.29×) |
| mul | clang native512 | 25.3 (6.6×) | 10.0 (2.7×) | 7.7 (2.1×) | 3.72 (1.34×) |
| stencil | gcc scalar | 12.9 | 12.9 | 12.9 | 12.9 |
| stencil | gcc native256 | 70.5 (5.5×) | 59.9 (4.6×) | 53.1 (4.1×) | 25.0 (1.9×) |
| stencil | gcc native512 | 66.3 (5.2×) | 59.9 (4.6×) | 53.7 (4.2×) | 25.3 (2.0×) |
| stencil | clang native512 | 69.4 (5.5×) | 65.3 (5.1×) | 53.8 (4.3×) | 25.4 (2.0×) |

**Interpretation:**
- **Scalar dot is latency-bound.** It runs one dependent add per element (3 cycles, 0.33 elem/cycle), so it never touches the memory limit. That is why its SIMD speedup stays above 4.6× even in DRAM.
- **Clang scalar dot is slower than GCC's** (2.0 vs. 2.6 GFLOP/s). It contracts the multiply-add into an FMA, whose latency is 4 cycles.
- **Clang's 27× in L1** comes from interleaving the vector loop ×4 (4 accumulators). GCC keeps 1 accumulator, so it is bound by add latency at 16 lanes / 3 cycles. See E9.
- **mul is the opposite case:** its scalar version already moves 42 GiB/s, so SIMD only helps where bandwidth exceeds that.
- **The stencil is compute-bound when scalar** (5 FLOP/elem, flat 12.9 GFLOP/s), so it gains about 5× wherever the data is cached.

**Figures:** `e1_{dot_simd,mul,stencil}_{throughput,speedup}.jpg` (6). Data: `E1.csv`.

## E2: Locality sweep, dot product (required plot 2)
**Question:** where do SIMD gains compress as the working set leaves each cache level?

**Method:**
- The E1 dot sweep, plus E2's memory-counter run (`mem` set) for dot and mul, gcc scalar vs. native512.
- The DRAM-read estimate is (L2-prefetch misses to DRAM + demand DRAM fills) × 64 B. It was validated at 1.06 lines per line in `test_perfctr`.

**Results:**
- **GCC 512-bit speedup per regime:**

  | L1 | L2 | L3 | DRAM |
  |---|---|---|---|
  | 16.4× | 11.9× | 9.0× | 4.7× |

- **Vector bandwidth reaches 93–96% of each level's measured read roof:**

  | L1 | L2 | L3 | DRAM |
  |---|---|---|---|
  | 159 GiB/s (latency-bound below the L1 roof) | 115 | 87 | 45 |

- **Measured DRAM traffic:**
  - dot reads **8.0 B/elem**, exactly compulsory.
  - mul reads **12.0 B/elem**: 8 B of x and y plus 4 B of read-for-ownership on z. That is direct counter evidence of write-allocate.

**Figures:** `e2_dot_bandwidth.jpg`, `e2_dot_speedup_compression.jpg`, `e2_dot_cycles_per_elem.jpg`, `e2_dram_traffic.jpg`. Data: `E1.csv`, `E2.csv`.

## E3: Alignment and tail handling (required plot 3)
**Question:** what do unaligned starts, remainders and masking cost?

**Method:**
- **Offsets:** every array offset by {0,4,8,16,32,48,60} B, plus a mixed case (x+0, y+4, z+8), at L1 (24 KiB) and L2 (512 KiB) sizes. `align` counters.
- **Tails:** mul at n = 1024..1088, plus small n = 1..128.

**Results:**
- **mul, L1, 512-bit:**
  - +60% cycles/elem at offsets 4–16, +41% at 48/60, +43% for the mixed case.
  - **±0% at +32 B**, matching the E0 microbenchmark: the store is 32 B-aligned and the load halves stay inside their lines.
- **mul, L1, 256-bit and scalar:** no measurable penalty.
- **dot (load-only) at 512-bit:** GCC +19% and Clang +53%. Clang's 4-accumulator loop is load-bound, so it feels every split load.
- **stencil:** at most +20%.
- **When L2-resident, all penalties fall to ≤5%** (stencil ≤14%). Misalignment matters only when L1 bandwidth is the bottleneck.
- **`__builtin_assume_aligned`:** identical to plain `mul` (0.158 vs. 0.158 cyc/elem). GCC 11 with znver4 tuning doesn't peel for alignment and always uses `vmovups`, so there's no prologue to remove.
- **Epilogue cost:** at most 7 cycles out of about 165 per call at n ≈ 1024. It grows by about 0.8 cycles per leftover element, then resets at 8 leftovers, when the 256-bit vector epilogue takes over. Both compilers emit a 1 × ymm epilogue (verified in the asm). GCC then adds up to 7 unrolled `vmulss`, and Clang adds a scalar loop.
- **Masked tails (Clang `vectorize_predicate`) cost 2× everywhere** (321 vs. 159 cycles at n = 1024):
  - The tail-folded loop rebuilds the lane mask every iteration: `vpbroadcastq`, 2 × `vporq`, 2 × `vpcmpleuq`, `kunpckbw`. That's 6 extra instructions per 16 elements.
  - Clang also stops interleaving it ×4.
  - Masking removes the sawtooth, but on Zen 4 it is a net loss at this n. It wins only for very small n (it's flat at 2.8 ns up to n = 16).
  - In the SSE2 build, which has no mask registers, tail folding costs 12.8 instructions per element.
- **Small n:** the vector code beats scalar from n ≈ 8 (3.1 vs. 4.1 ns). Below that, setup dominates.

**Figures:** `e3_alignment_{mul,stencil,dot}_l1.jpg`, `e3_alignment_mul_l2.jpg`, `e3_misaligned_loads.jpg`, `e3_tail_sawtooth.jpg`, `e3_tail_cost_by_remainder.jpg`, `e3_small_n_overhead.jpg`. Data: `E3.csv`, `E3_tail_cost.csv`.

## E4: Stride and gather (required plot 4)
**Question:** how do non-unit strides and indexed loads reduce effective bandwidth and SIMD efficiency?

**Method:**
- `mul`/`dot` with compile-time stride 2/4/8 (GCC uses contiguous loads plus `vpermt2ps`), and `x[idx[i]]` gathers (`vgatherdps`).
- Index patterns: identity, 2i/4i/8i, random permutation.
- A fixed address span per regime (L2: 512 KiB, DRAM: 512 MiB). Effective GiB/s counts only the elements used.

**Results (GCC 512-bit vs. scalar):**
- **dot, L2:**

  | stride | 1 | 2 | 4 | 8 |
  |---|---|---|---|---|
  | GiB/s | 115 | 58 | 29 | 14 |
  | speedup | 12.0× | 6.0× | 3.0× | 1.5× |

  - Effective bandwidth halves with every doubling of stride, because the line traffic stays the same.
- **Gathers:**
  - dot, L2: even `idx = i` (perfectly sequential) runs at 31 GiB/s vs. 115 for a plain load. That's the instruction cost of `vgatherdps`.
  - **Random** gathers fall to 22 GiB/s (L2) and **1.9 GiB/s (DRAM)**. They are latency-bound, and SIMD helps only 1.1×.
- **In DRAM, stride ≥ 4 and strided gathers are no faster or slower than scalar** (0.84–1.03×). Every element costs a full line, whatever the instruction mix.
- **mul in L2:** stride 4/8 gives exactly scalar speed (1.0×). The store stream plus the 4–8 lines per vector dominate.

**Figures:** `e4_{mul,dot}_stride_bw_{l2,dram}.jpg` (4), `e4_stride_simd_speedup.jpg`. Data: `E4.csv`.

## E5: Data type: f32 vs. f64 vs. i32 (required plot 5)
**Question:** how do lane count, arithmetic intensity and operation cost change the results?

**Method:** `dot_simd`/`mul`/`stencil` × {f32, f64, i32} × {gcc scalar, gcc native256, gcc native512, clang scalar, clang native512}, at 16 KiB, 256 KiB and 512 MiB.

**Results (elements/cycle in L1, GCC 512-bit):**

| kernel | f32 | f64 | i32 |
|---|---|---|---|
| dot | 5.6 | 3.1 | 7.0 |
| mul | 6.3 | 3.1 | 6.3 |
| stencil | 3.4 | 1.7 | 3.4 |

- **f64 halves elements/cycle at every vector width** (8 lanes instead of 16). It also halves arithmetic intensity, so f64 reaches the DRAM roof at half the element rate. In DRAM all three types converge on the same GiB/s (dot ≈ 45, mul ≈ 42, stencil ≈ 38).
- **i32 dot isn't latency-bound.** `vpaddd` has 1-cycle latency, so a single accumulator already reaches 7.0 elem/cycle (L1 load bound), where f32 gets 5.6.
- **Scalar i32 is slower** (imul plus a 1-element chain).
- **The i32 stencil uses `vpmulld`**, which is 3-cycle latency and half throughput, with no integer FMA. Scalar i32 stencil is 2× slower than scalar f32 (0.33 vs. 0.66 elem/cycle), which is why the i32 stencil SIMD speedup is the largest (10× in L1).
- **SIMD speedup for f64 is half that of f32 in cache** (dot 9.0× vs. 16.4×). In DRAM it falls to 1.1–2.3×.

**Figures:** `e5_dtype_l1_elem_per_cycle.jpg`, `e5_dtype_dram_gibs.jpg`, `e5_dtype_simd_speedup.jpg`. Data: `E5.csv`.

## E6: Vectorization verification (required plot 6)
**Question:** does the emitted code actually do what the compiler claims?

**Method:**
1. **Compiler verdict:** the GCC `-fopt-info-vec-all` and Clang `-Rpass*` remark on each kernel's main loop.
2. **Disassembly:** the main loop is found from backward branches and classified by register width, packed vs. scalar FP, gathers, permutes and masks (`scripts/vecinfo.py`). Report and asm must agree for all 630 build × dtype × kernel instances, which `tests/test_vectorization.py` enforces.
3. **PMU:** retired instructions per element at an L1 size, for every build.

**Results:**
- **GCC `mul`:** 6.02 → 0.41 instructions per element (scalar → 512-bit, about the 16× lane count). Clang's ×4 unrolled loop gets 0.27.
- **GCC `dot_plain` at 512-bit:** "vectorized", yet 2.28 instructions per element, vs. 0.42 for the real reduction.
- **Gathers:** 0.55–0.63 per element.
- **Clang tail folding in SSE2:** 12.8 per element.

**Caveat:** PMCx008 by-width counts are **not** a clean lane metric on Zen 4. Loads and stores are classified by register width too (a scalar `vmovss` load counts as a 128-bit op), so they're kept in `E6_vec_summary.csv` but not plotted.

**Evidence:**
- [`results/vec_evidence/evidence.md`](results/vec_evidence/evidence.md): 15 concise cases, each with a one-line verdict plus ≤15 asm lines.
- `results/vec_evidence/status_matrix.csv`: all 630 instances.

**Figure:** `e6_vectorization_matrix.jpg`. Data: `E6.csv`, `E6_vec_summary.csv`.

## E7: Roofline, f32 (required plot 7)
**Method:**
- Memory roofs: the best measured single-core bandwidth per level from E0 (L1 315, L2 124, DRAM 49 GB/s).
- Compute ceilings: measured FMA peaks (scalar 16, SSE 62, AVX2/AVX-512 125 GFLOP/s).
- Kernel points come from E1 at L1- and DRAM-resident sizes. AI uses compulsory bytes: dot 0.25, mul 0.083, stencil 0.625 FLOP/B.

**Interpretation:**
- **Every kernel is far left of the ridge point** (125 / 49 ≈ 2.5 FLOP/B for DRAM). All three are memory-bound once vectorized.
- **In DRAM** the vector points sit on the DRAM roof: mul 3.7 vs. a roof of 4.1, stencil 25 vs. 31, dot 12.0 vs. 12.2. **512-bit can't help, because the roof isn't compute.**
- **Two dot points sit far below every roof.** Their limit is add latency:
  - scalar: 2 FLOP per 3 cycles = 2.6 GFLOP/s
  - GCC zmm: one accumulator, 32 FLOP per 3 cycles = 41.5 GFLOP/s, measured 42.8
  
  These ceilings are drawn on the plot. The roofline alone would wrongly call scalar dot memory-bound.

**Figure:** `e7_roofline_f32.jpg`. Data: `E0_roofs.csv`, `E0_peak.csv`, `E1.csv`.

## E8: Negative examples (required plot 8)
**(a) Looks vectorized, isn't (`dot_plain`, no pragma, IEEE FP):**
- **GCC's claim:** it reports `loop vectorized using 64 byte vectors`.
- **What it emits:** an in-order (fold-left) reduction, i.e. one `vmulps` and then **16 serial `vaddss`** into one register (see evidence.md).
- **Measured:** 2.64 GFLOP/s in L1 vs. 2.62 for scalar (**1.00×** at every size).
- **Clang:** refuses outright ("cannot prove it is safe to reorder floating-point operations"), at the same speed as scalar.
- **Fixes:** `#pragma omp simd reduction(+:s)` gives 42.7 GFLOP/s, and `-ffast-math` gives 49.9 (GCC) / 54.7 (Clang). Both permit reassociation.
- **i32 needs no fix:** integer addition is associative, and the tests assert it vectorizes.

**(b) Vectorized, little speedup (`mul` in DRAM):**
- Fully zmm (E6), yet only **1.29×** (GCC) / 1.34× (Clang) over scalar.
- The scalar loop already streams 32 GiB/s against a 42 GiB/s achievable roof. The PMU shows 12 B/elem of DRAM reads, including write-allocate.
- dot is shown for contrast. Its scalar version is latency-bound, so the vector version still wins 4.7× in DRAM.

**Figures:** `e8_neg_dot_inorder_reduction.jpg`, `e8_neg_mul_membound.jpg`. Data: `E8.csv`, `E1.csv`.

## E9: Multi-accumulator dot (extra)
**Question:** is the L1 dot limited by vector width or by add latency?

**Method:**
- `dot_simd` (K=1) and `dot_acc{2,4,8}` (K contiguous chunks, one accumulator each).
- n = 512..3584 (L1), with `--serialize` (lfence between calls).
- The steady-state cost is the slope of cycles/call vs. n. The intercept is the fixed per-call cost of about 35–215 cycles.

**Results (elements/cycle):**

| build | K=1 | K=2 | K=4 | K=8 | model K=1 = lanes / latency |
|---|---|---|---|---|---|
| gcc scalar | 0.33 | 0.67 | 0.97 | 0.96 | 1/3 |
| clang scalar | 0.25 | 0.50 | 0.98 | 0.84 | 1/4 (FMA latency 4) |
| gcc SSE | 1.33 | 2.66 | 3.85 | 2.79 | 4/3 |
| gcc 256-bit | 2.67 | 5.32 | 7.41 | 7.06 | 8/3 |
| gcc 512-bit | 5.26 | 7.46 | 7.14 | 7.06 | 16/3 |
| clang 512-bit | 6.92 (auto ×4) | 7.32 | 7.45 | 6.86 | already interleaved |

**Interpretation:**
- **Throughput is min(K × lanes / 3, ≈7.5)**, where 7.5 is just under the 8 elem/cycle L1 load bound (2 × 32 B/cycle ÷ 8 B per element).
- **With two or more accumulators, 256-bit and 512-bit perform the same.** On Zen 4, 512-bit buys nothing once the loop is load-bound.
- **The "SIMD speedup" depends on the baseline:** 16× against single-chain scalar, but only 7.5× against the best scalar code (K=4).
- **Methodology note:** without `lfence`, back-to-back calls overlap in the out-of-order window, and short reductions then measure up to 10% *better* than their latency bound.

**Figure:** `e9_multiacc_dot.jpg`. Data: `E9.csv`, `E9_fit.csv`.

---

## Cross-cutting findings and pitfalls (verified)
1. **The scalar baseline can leak SIMD.** Both of these force vectorization even with the vectorizer disabled: GCC's `#pragma omp simd` under `-fopenmp-simd -fno-tree-vectorize`, and Clang's `vectorize_predicate` under `-fno-vectorize`. The scalar builds therefore omit `-fopenmp-simd` and define `KERNELS_SCALAR`, and `test_scalar_build_has_no_simd` guards this.
2. **A compiler report isn't proof.** "Loop vectorized" (GCC `dot_plain`) can mean a serial reduction, and GCC's remark on the `omp simd` pragma line refers to internal helper loops. Only the `for` line's remark describes the user loop (Clang reports on the pragma line instead; `scripts/vecinfo.py:main_loop_lines`).
3. **`-march=native` with GCC 11 on Zen 4 means 256-bit vectors.** You need `-mprefer-vector-width=512` to get zmm.
4. **Two `-Rpass=` flags don't combine.** The last one wins, so pass one regex.
5. **PMU caveats:** at most 5 events per group (the NMI watchdog takes one counter). MA64 counts aligned zmm loads. PMCx008 classifies loads/stores by width.

## Open items / possible extensions
- Uncore DRAM counters (`amd_umc`) need system-wide perf rights (`perf_event_paranoid` ≤ 0). Store traffic is therefore inferred (write-allocate), not measured.
- Boost cannot be disabled without root. All cycle metrics use PMU core cycles, so this does not bias cycles/element.
