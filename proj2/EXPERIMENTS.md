# EXPERIMENTS: central registry (methods, status, results)

This is the single place to find what was run, how, and what it showed.
- **Figures:** `results/plots/`, one graph per JPG.
- **Numbers behind every figure:** `results/summary/<ID>.csv`. Each row gives medians, a bootstrap 95% CI (`*_lo`, `*_hi`), the CV, and `others_util_max`.
- **Raw samples:** `results/raw/` (gitignored). Regenerate them with the commands at the bottom.

**Machine:**
- 2 × AMD EPYC 9334 (Zen 4, Genoa): 4 CCDs × 8 cores per socket, no SMT.
- Caches: 32 KiB L1d and 1 MiB L2 per core; 32 MiB L3 per CCD.
- TLBs: 72-entry L1 DTLB and 3072-entry L2 DTLB, for both 4 KiB and 2 MiB pages.
- Memory: 12 DDR5 channels and 384 GiB per socket.
- Clock: boost on, `performance` governor. 3.89 GHz measured on one core.
- Software: no root; THP `always` with defrag `madvise`; no hugetlbfs pool; `perf_event_paranoid=2`.

**Status (2026-10-21):** E0–E8 are complete.

Verification passes:
- 225 C unit checks (`results/env/unit_tests.txt`)
- 29 PMU validations (`results/env/pmu_validation.txt`)
- 24 pytest tests

The Intel MLC cross-check could not run (see [MLC](#mlc-cross-check-blocked)).

## Common method
- **Probe.** `build/membench`, our MLC-equivalent (see [README](README.md#tools)):
  - **`chase`:** dependent pointer chase.
  - **`bw`:** bandwidth kernels.
  - **`loadlat`:** 1 chase thread plus N throttled generators.
  - **`kern`:** the loop kernels `z=x*y` and `y=a*x+y`.

  Every sample row reports:
  - the achieved THP fraction, from `/proc/self/smaps`
  - the memory's NUMA node, from `move_pages`
  - the cpu at the start and end of the sample
  - PMU cycles, so cycle figures always use the measured clock
- **Pinning.**
  - Single-thread probes run on an idle core of the quietest CCD.
  - Multi-thread runs use one NUMA node, one thread per core, round-robin over its 4 CCDs. They use 31 threads and leave the node's first core free.
  - The driver process runs off the measured cpus.
- **Shared machine (gate).**
  - Before each job, the measured CCD must carry at most 0.5 core of other activity, or the measured node at most 2 cores. Background services keep about 1.1 cores busy on every node.
  - During each job, other users' activity on those cpus is measured as busy jiffies minus our own child CPU time, and saved as `others_util`.
  - A disturbed job is re-measured, up to 3 attempts.
  - The gate waits without limit for quiet hardware. That is why E6's wall time is 19 h: it waited out the other jobs running at the time (MAGI and LongVILA evals).
- **Memory.**
  - 2 MiB pages by default, via `madvise(MADV_HUGEPAGE)`; verified at 100% THP in every row.
  - 4 KiB pages via `MADV_NOHUGEPAGE`; verified at 0% THP.
  - Memory is first-touched by the measuring thread, so it is local. Remote placement first-touches from a cpu on the other socket.
- **Timing.**
  - chase and kern samples last ≥ 20 ms each.
  - bw and loadlat measure all threads together in a fixed 50–200 ms window, after a warm-up window.
  - Job order is shuffled in every round.
- **Statistics.** The reported value is the median over samples × rounds, with a bootstrap 95% CI.
- **PMU.**
  - Events are opened in-process with `perf_event_open`, user space only, at most 5 per group.
  - Fill-source events (0x44) attribute each L1D fill to L2, own-CCD L3, another CCD, or DRAM.
  - L2 demand misses come from 0x64, L2-prefetch misses in L3 from 0x72, and L1/L2 DTLB misses from 0x45.
  - Each event was validated on probes with known behaviour. For example, a 512 KiB random chase gives fill_l2/load = 1.000. A 16384-page 4 KiB page-stride chase gives dtlb_l2_miss/load = 0.997.
- **Little's Law.** In-flight lines = bandwidth / 64 B × latency. **AMAT:** t = Σ (fraction served by level × that level's latency, from E1).

| ID | run_id | core / node | rounds × samples | jobs | wall time | re-measured / still busy |
|---|---|---|---|---|---|---|
| E0 | 20261020-134542 | core 9 | 3 × 11 | 2 | 7 s | 0 / 0 |
| E1 | 20261020-134551 | core 57 (node 1); remote memory on node 0 | 3 × 11 | 18 | 58 s | 0 / 0 |
| E2 | 20261020-152318 | core 49, node 1 | 2 × 7 | 143 | 24 min | 7 / 0 |
| E3 | 20261020-154658 | core 57, node 1 | 3 × 7 | 38 | 10 min | 2 / 0 |
| E4 | 20261020-155702 | core 49, node 1 | 3 × 7 | 22 | 5 min | 0 / 0 |
| E5 | 20261021-120458 | core 9, node 0 (31 cpus) | 2 × 5 | 114 | 52 min (incl. waiting) | 0 / 0 |
| E6 | 20261020-163329 | core 2 | 2 × 7 | 594 | 19.2 h (waiting) | 35 / 9 |
| E7 | 20261021-114610 | core 1 | 2 × 7 | 200 | 7 min | 5 / 1 |
| E8 | 20261021-115649 | core 9, node 0; remote memory on node 1 | 1 × 7 (pinning: 10 processes × 5) | 70 | 2 min | 0 / 0 |

Commands for every experiment:
```
python scripts/run.py <ID>
python scripts/summarize.py <ID>
python scripts/plot.py <ID>
```
`./scripts/run_all.sh` runs the whole suite. It resumes interrupted runs and skips completed ones.

---

## E0: Calibration
**Question:** what clock do cycle figures use, and does the probe behave?

**Results:**
- The L1-resident chase takes **4.00 cycles/load at 3.89 GHz**. That is Zen 4's 4-cycle load-to-use latency, so the PMU cycle counts and the chase are both right.
- The DRAM anchor is 125 ns.
- cpuid geometry (`results/env/cpuid_info.txt`) matches sysfs and is asserted by pytest.

## E1: Zero-queue latency per level
**Question:** what is the unloaded load-to-use latency of each level?

**Method:**
- Random 64 B Sattolo chase on 1 pinned core, idle machine.
- Working sets: 16 KiB (L1), 512 KiB (L2), 16 MiB (L3), 1 GiB (DRAM); 2 MiB pages, plus 4 KiB pages for comparison.
- DRAM is also measured remote (memory first-touched on the other socket).
- Fill-source counters confirm where each probe is served from.

**Results** (median; the CIs are within ±1% except remote):

| level | WS | ns | cycles @ 3.89 GHz | served by that level | 4 KiB pages |
|---|---|---|---|---|---|
| L1d | 16 KiB | **1.03** | **4.0** | 100% | 1.03 ns |
| L2 | 512 KiB | **3.63** | **14.1** | 100% | 4.55 ns (44% L1-DTLB misses) |
| L3 (own CCD) | 16 MiB | **15.5** | **60** | 94% (3% L2, 3% DRAM) | 20.1 ns (26% page walks) |
| DRAM local | 1 GiB | **126.5** | **492** | 99% | 143.6 ns (99% page walks) |
| DRAM remote | 1 GiB | **196.5** (node 1 → node 0 memory) | 764 | 99% | – |

**Interpretation:**
- Each level costs about 3.5–8× the one above it.
- Remote DRAM adds about 70 ns, one hop over the socket-to-socket link. The other direction (node 0 → node 1 memory, measured in E8) is 217 ns.
- With 4 KiB pages, a 1 GiB working set misses the L2 TLB on 99% of loads, which adds 17 ns (+14%) to every DRAM access.
- The 16 MiB "L3" figure includes 2.6% DRAM fills. A pure L3 hit is about 12.9 ns, 50 cycles.

**Figures:** `e1_latency_table.jpg`, `e1_latency_by_level.jpg`. **Data:** `E1.csv`.

## E2: Working-set sweep
**Question:** where are the transitions, and do they match the capacities?

**Method:**
- Random-chase latency from 4 KiB to 2 GiB, 4 points per octave, with fill-source counters.
- Sequential read bandwidth on 1 core, 8 KiB → 2 GiB.
- Sequential read bandwidth on 31 threads (one node), 256 KiB → 4 GiB total.

**Results:**
- **Latency.**
  - 1.03 ns flat up to 27.5 KiB.
  - 3.6 ns from 38 KiB to 880 KiB.
  - 8–19 ns from 1.2 to 28 MiB, a mix of L2 and L3 hits (the L2 share falls from 52% to 2%).
  - Then the DRAM ramp: 24 ns at 32 MiB, 87 ns at 64 MiB, 117 ns at 256 MiB, 126 ns from 1 GiB on.
  - The PMU fill fractions switch exactly at 32 KiB, 1 MiB and 32 MiB.
- **AMAT check.** Σ (fraction served by level × E1 latency) predicts the measured latency to within 0–3% at every point from 45 MiB to 1 GiB. Examples: 64 MiB, 88.5 predicted vs 86.8 ns measured; 128 MiB, 107.7 vs 106.1 ns.
- **Single-core read bandwidth.**

  | level | bandwidth | per cycle |
  |---|---|---|
  | L1 | 186–193 GB/s | about 49 B/cycle |
  | L2 | 114 GB/s | 29 B/cycle |
  | L3 | 90 GB/s | – |
  | DRAM | **43.5 GB/s** | – |

- **Node read bandwidth (31 threads).** Σ L1 4.4 TB/s, Σ L2 2.85 TB/s, Σ L3 1.6–1.7 TB/s, DRAM **≈ 300 GB/s**. That is 65% of the 460.8 GB/s theoretical peak, an upper bound that assumes DDR5-4800 (the DIMM speed cannot be read without root).

**Interpretation:**
- The DRAM transition is gradual, not a step. A random set of size WS still hits the 32 MiB L3 with probability ≈ 32 MiB / WS. AMAT with the measured hit fractions reproduces the whole ramp.
- **Little's Law for one core:**
  - 43.5 GB/s at 126 ns needs 43.5 / 64 × 126 ≈ **86 lines in flight**.
  - E5 shows that demand misses alone saturate at about 19 per core.
  - The rest of the concurrency comes from the L2 hardware prefetcher, which is why sequential bandwidth is 4.6× the random-access bandwidth (E3).

**Figures:** `e2_latency_vs_working_set.jpg`, `e2_fill_source_vs_working_set.jpg`, `e2_bandwidth_vs_working_set_core.jpg`, `e2_bandwidth_vs_working_set_node.jpg`. **Data:** `E2.csv`.

## E3: Access pattern and granularity
**Question:** how do order, stride and access size change latency and bandwidth?

**Method:**
- **Latency:** dependent chase, sequential or random order, 64/256/1024 B strides, at 16 MiB and 1 GiB.
- **Bandwidth:** independent 64 B loads, sequential or random order, 64/256/1024 B strides. Granularity (bytes per load) 8/16/32/64 B, either dense (stride = granularity) or one access per 64 B line.
- Run on 1 core (1 GiB buffer, with PMU) and on 31 threads (64 MiB each).

**Results:**

| DRAM, 1 core | 64 B | 256 B | 1024 B |
|---|---|---|---|
| chase latency, sequential | 1.83 ns | 2.78 ns | 3.77 ns |
| chase latency, random | 125.4 ns | 127.0 ns | 127.7 ns |
| read bandwidth, sequential | **43.5 GB/s** | 9.7 GB/s | 10.5 GB/s |
| read bandwidth, random | 9.0 GB/s | 9.1 GB/s | 9.0 GB/s |
| read bandwidth, node, sequential | **301 GB/s** | 137 GB/s | 71 GB/s |
| read bandwidth, node, random | 241 GB/s | 133 GB/s | 70 GB/s |

- **Granularity, 1 core, dense:** 8 B: 23.6 GB/s, 16 B: 38.0, 32 B: 42.3, 64 B: 43.4. On the node, 283–301 GB/s at every granularity.
- **One access per line:** the useful bandwidth is gran/64 of the line bandwidth. 1 core: 5.4 / 9.7 / 20.0 GB/s for 8 / 16 / 32 B, while 38–43 GB/s of whole lines move; node: 38 / 75 / 145 GB/s useful against 290–302 GB/s of lines. The PMU confirms 1.00 DRAM line per access.

**Interpretation:**
- **Random order.** Random bandwidth on one core is purely latency × MLP bound. 9.0 GB/s × 126 ns / 64 B = **17.6 lines in flight**, which is the per-core demand-miss limit from E5.
- **Sequential dependent chases are prefetched**, even at a 1 KiB stride (3.8 ns instead of 126 ns). Constant-stride "latency tests" therefore measure the prefetcher, not DRAM (E8c).
- **Strided independent loads (256 B and 1024 B) are no faster than random**, unlike the strided chase. A possible explanation, not verified: the bandwidth loop is unrolled 4×, so each load instruction sees a 4 × stride step (1–4 KiB), which the per-instruction stride prefetcher may not follow.
- **Granularity.** In DRAM, granularity only matters until there are enough loads in flight. One core cannot issue 8 B loads fast enough (23.6 GB/s is an instruction-rate limit); 31 cores can. Touching part of a line still moves the whole line, so useful bandwidth scales with gran/64.

**Figures:** `e3_latency_by_pattern.jpg`, `e3_bandwidth_stride_{core,node}.jpg`, `e3_bandwidth_granularity_{core,node}.jpg`. **Data:** `E3.csv`.

## E4: Read/write mix
**Question:** what do 100% read, 100% write, 70/30 and 50/50 sustain, and at what latency?

**Method:**
- Sequential 64 B accesses at an exact R:W ratio per access; normal stores or non-temporal (NT) stores.
- Run on 1 core (1 GiB buffer), on 31 threads (64 MiB each), and as loaded latency (1 probe core + 30 generators at full rate).

**Results** (GB/s of program bytes; brackets give the modelled DRAM traffic including read-for-ownership, RFO):

| mix | 1 core | node | node loaded latency |
|---|---|---|---|
| 100% read | 43.5 | **301** | 268 ns |
| 100% write | 34.7 (69) | 171 (343) | 457 ns |
| 100% write, NT | 43.2 | 229 | 321 ns |
| 70/30 | 34.1 (44) | 249 (323) | 339 ns |
| 70/30, NT | 31.8 | 229 | 277 ns |
| 50/50 | 35.0 (52) | 218 (327) | 386 ns |
| 50/50, NT | 14.2 (anomaly, see below) | 226 | 179 ns |

The idle latency is 126 ns.

**Interpretation:**
- **Normal stores.** A normal store first reads the line (RFO) and later writes it back, so 100% writes move 2 lines per line written. Program bandwidth halves (171 GB/s), while DRAM moves about 343 GB/s, more than the read-only 301.
- **NT stores** avoid the RFO, and every NT mix lands on the same ≈ 229 GB/s write-path ceiling.
- **Loaded latency** at full load rises from +113% (read) to +263% (100% write). The write-to-read turnaround and the doubled traffic of RFO writes add queueing.
- **Anomaly, unexplained:** one core interleaving NT stores 1:1 with loads reaches only 14.2 GB/s. Both 0:1 NT and 7:3 NT are fine. It is listed under open items.

**Figures:** `e4_rw_mix_bandwidth_{node,core}.jpg`, `e4_rw_mix_loaded_latency.jpg`. **Data:** `E4.csv`.

## E5: Intensity, throughput vs latency, Little's Law
**Question:** how does latency grow as memory intensity grows? Where is the knee, and what concurrency does Little's Law say each point needs?

**Method:**
- **(a) Loaded latency, MLC-style.** On one node, 1 core runs a random-chase probe (1 GiB) while N generator cores run a mix (100% read, 70/30, 50/50) at 64 MiB each.
  - Generator sweep: N = 0–30 at full rate.
  - Delay sweep: N = 30, with an injection delay of 0–1600 empty-loop iterations after each access.
  - Bandwidth includes the probe's own lines.
- **(b) Single-core MLP.** k = 1–32 independent random chases in lockstep, on 1 GiB (DRAM) and 16 MiB (L3).

An earlier run (20261020-160206) had its two rounds on different nodes and days. They differed by a median 5% (up to 18%) in bandwidth, so the whole experiment was re-run on one quiet node in one sitting. That run's rounds agree within ±1% (bandwidth, median) and 3.7% at worst. The earlier run is kept in `results/raw` for reference.

**Results (a), 30 generators, delay sweep:**

| mix | knee (Kneedle) | lines in flight at knee | full-rate (delay 0) | in flight at full rate |
|---|---|---|---|---|
| 100% read | **239 GB/s @ 167 ns** | 623 | 283 GB/s @ 300 ns | 1323 |
| 70/30 | 225 GB/s @ 191 ns | 670 | 246 GB/s @ 347 ns | 1331 |
| 50/50 | 179 GB/s @ 194 ns | 542 | 204 GB/s @ 353 ns | 1126 |

- Up to the knee, latency rises slowly: 125 ns idle → 158 ns at 216 GB/s for reads.
- Past the knee, the in-flight lines more than double (623 → 1323) while bandwidth grows only 18%. The extra concurrency becomes queueing delay.
- **Generator sweep, 100% read, no delay:** 1 core 48 GB/s; 4 cores 170; 8 cores 233; **12–20 cores 303 GB/s (peak)**; 30 cores 283 GB/s.
  - Oversubscribing the memory system *loses* 7% bandwidth while latency keeps rising (198 → 300 ns).
  - 12 cores already saturate the socket.

**Results (b), single-core MLP:**
- **DRAM.** Bandwidth grows linearly with k (each chain still sees 125–137 ns) up to **k ≈ 20**, then plateaus at 9.6 GB/s. Beyond that, extra chains only wait: at k = 32, each chain sees 214 ns = 32 × 64 B / 9.57 GB/s, exactly Little's Law.
  - The plateau corresponds to 9.6 GB/s × 125 ns / 64 B ≈ **19 misses in flight per core**.
- **L3.** It plateaus at about 76 GB/s from k ≈ 20–24. 76 × 15 ns / 64 ≈ 18: the same per-core limit, so the cap is in the core (its outstanding-miss tracking), not in DRAM.

**Interpretation:**
- **The core's limit.** One core can keep about 19 demand misses in flight to any level beyond L2. Random reads (E3, 17.6) and gathers (E6, 19) hit the same ceiling.
- **The socket's limit.** Saturating the socket takes about 1300 lines in flight. Thirty cores × 19 demand misses cannot supply that alone; hardware prefetch on the sequential generators supplies the rest.
- **The knee is the useful operating point.** It reaches 80–85% of peak bandwidth at only 1.3–1.5× idle latency. Beyond it, the device queues are full, and Little's Law turns more concurrency directly into latency.
- **Writes move the knee left.** 50/50 kneels at 179 GB/s. RFO doubles the write traffic, and DDR5 read/write turnarounds cost bus time.

**Figures:**
- `e5_latency_vs_bandwidth.jpg`: knees starred, Little's-Law in-flight isolines dotted
- `e5_littles_law_concurrency.jpg`
- `e5_mlp_dram.jpg`, `e5_mlp_l3.jpg`

**Data:** `E5.csv`.

**Caveat:** other users kept 1–1.4 cores busy on node 0 during part of the run, within the 2-core gate and recorded per row. The two rounds agree within ±1%.

## E6: Cache-miss impact on loop kernels
**Question:** how do footprint and access pattern turn into misses, and misses into runtime?

**Method:**
- `mul` (z = x·y) and `axpy` (y = a·x + y), f64, compiled `-O3 -march=native` (vectorized with ymm).
- Three patterns: contiguous; stride 8 elements (one element per line); random gather over a permutation.
- Footprints 16 KiB → 1 GiB, 2 per octave.
- Three PMU sets per point (cache, fills, mem).

**Results** (ns per element):

| | L1 | L2 | L3 | DRAM (1 GiB) |
|---|---|---|---|---|
| mul contiguous | 0.069 | 0.195 | 0.25 | 0.53 |
| mul stride 8 | 0.54 | 1.65 | 2.1 | 4.38 |
| mul gather | 0.92 | 1.3–1.7 | 2.0–2.6 | **19.6–20.1** |
| axpy gather | 0.72 | 0.8–1.1 | 1.3–1.9 | **13.1–13.5** |

- **Correlation with runtime** over all 196 points: DRAM demand fills/element r = 0.98; L2 demand misses r = 0.76; L1 misses r = 0.49 (Spearman 0.90).
- **AMAT, serial and with no overlap** (fills × E1 latencies), overpredicts every point:
  - gather in DRAM by 16–20×
  - contiguous by 3–30×

**Interpretation:**
- **Runtime follows the misses of the level that is the bottleneck**, not L1 misses. Contiguous and stride-8 have the same L1 misses per element in L2 as in DRAM, but very different runtimes.
- **Stride 8 equals contiguous × 8.** In DRAM, both move a line in about 1.45 ns (contiguous: 0.53 ns × 8 elements / 3 lines; stride 8: 4.38 ns / 3 lines). Both are bound by line bandwidth, about 44 GB/s on one core.
- **Random gather is bound by Little's Law.**
  - mul: 3.0 L2 misses per element in 20 ns, i.e. 6.7 ns per miss.
  - axpy: 2.0 misses in 13.4 ns, i.e. 6.7 ns per miss.
  - 126 ns / 6.7 ns ≈ **19 misses in flight**: the same per-core limit E5 measures with pointer chains.
- **The serial AMAT** is the right model for a dependent chase (E2, to within 3%). For independent accesses it must be divided by the achieved MLP. The distance below the diagonal in `e6_amat_vs_measured.jpg` is that MLP.

**Figures:** `e6_runtime_vs_footprint.jpg`, `e6_runtime_vs_{l1_misses,l2_misses,dram_lines}.jpg`, `e6_amat_vs_measured.jpg`. **Data:** `E6.csv`.

**Caveat:** 9 of the 1188 jobs still saw more than 0.5 core of neighbour activity after 3 attempts. Their rows carry `others_util` > 0.5; see `E6.csv` `others_util_max`.

## E7: TLB-miss impact, 4 KiB vs 2 MiB pages
**Question:** what do DTLB misses cost, and when do huge pages help?

**Method:**
- (a) **PAGERAND chase:** one 64 B line per 4 KiB page, random page order, 16 → 262144 pages. Both page sizes carry the same cache behaviour, so the gap between them is the TLB.
- (b) **Random chase:** 256 KiB → 4 GiB.
- (c) **Kernels:** `mul` with one element per 4 KiB page, and a random gather, 1 MiB → 1 GiB.
- All with the `tlb` PMU set.

**Results:**
- **(a) PAGERAND, 4 KiB pages:**
  - 1.03 ns up to 64 pages.
  - **2.83 ns from 90 pages**, past the 72 L1-DTLB entries: dtlb_miss/load = 1.0, an L2-TLB hit costing 1.8 ns ≈ 7 cycles.
  - Past 3072 pages, page walks start: dtlb_l2_miss/load = 0.15 at 2896 pages, 0.51 at 4096, 1.0 from 5792. Latency then rises to 12.6 ns (8K pages) and 50 ns (256K pages).
  - With 2 MiB pages, no L1-DTLB misses occur until 46K pages (90 huge pages > 72 entries).
- **(b) Random chase, 4 KiB vs 2 MiB pages:**

  | working set | 4 KiB pages | 2 MiB pages | difference |
  |---|---|---|---|
  | 1 MiB | 8.8 ns | 3.8 ns | – |
  | 32 MiB | 50.7 ns | 24.7 ns | 2× |
  | 1 GiB | 143 ns | 125 ns | +14% |
  | 4 GiB | 161 ns | 127 ns | +27% |

  **The cost per page walk grows from 9 ns (64 MiB) to 34 ns (4 GiB)** as the page tables (8 B per 4 KiB) themselves fall out of L2 and L3.
- **(c) Kernels:**
  - **Random gather:** 2 MiB pages are faster, by 11% at 1 GiB (20.0 vs 22.3 ns/element) and 67% at 32 MiB (3.1 vs 5.2 ns/element).
  - **One element per 4 KiB page:** 2 MiB pages are **slower**, by 9% in DRAM (44.5 vs 40.9 ns/element) and by 10–15% at small footprints, even though they remove every walk.

**Interpretation:**
- Huge pages help exactly when the page count exceeds the TLB reach: 288 KiB / 12 MiB with 4 KiB pages, against 144 MiB / 6 GiB with 2 MiB pages.
- The 4 KiB-stride slowdown is attributed, not proven, to physical contiguity. Inside a 2 MiB page, a 4 KiB power-of-two stride maps to the same cache-set and DRAM-bank/channel bits over and over. Scattered 4 KiB physical pages randomize those bits. Huge pages are not a free win.
- PAGERAND with 2 MiB pages is non-monotonic between 724 and 2048 pages (5.7 / 3.7 / 6.0 ns). The probe places line (page × 5 mod 64) in each page, so these sizes create L1/L2 set conflicts. The effect is the same for both page sizes, so the gap is still valid.

**Figures:** `e7_tlb_pagerand_latency.jpg`, `e7_tlb_pagerand_misses.jpg`, `e7_tlb_random_chase.jpg`, `e7_tlb_walk_cost.jpg`, `e7_tlb_kernels.jpg`. **Data:** `E7.csv`.

## E8: Benchmark pitfalls
**(a) Cache reuse posing as DRAM.**
- A random-chase "DRAM latency test" reports these values by buffer size:

  | buffer | reported latency |
  |---|---|
  | 4 MiB | 12.3 ns |
  | 16 MiB | 15.2 ns |
  | 32 MiB | 22.5 ns |
  | 64 MiB | 85 ns |
  | 128 MiB | 106 ns |
  | 1 GiB | 125 ns |

- A 16 MiB buffer reads **8× too fast**; even 128 MiB is 16% low.
- A single-core "DRAM bandwidth" test on a 4–16 MiB buffer reports **90 GB/s, 2× the true 43.5 GB/s**.
- Rule: the buffer must be well beyond L3. On this part, ≥ 256 MiB for latency (within 7%) and ≥ 128 MiB for bandwidth.

**(b) Placement** (10 separate processes × 5 samples per configuration):

| configuration | result |
|---|---|
| latency, pinned, local memory | 125 ns, range 124–127 |
| latency, pinned, remote memory | 217–220 ns in 8 of 10 processes, about 393 ns in the other 2 |
| latency, unpinned | 126.5 ns: on the mostly idle machine, the scheduler kept the thread near its first-touched memory |
| bandwidth, 16 threads pinned on one node | 308 GB/s |
| bandwidth, 16 threads unpinned | **475 GB/s median**, above one socket's 460.8 GB/s peak |

- Unpinned threads spread over **both** sockets and first-touch locally on each. A "node bandwidth" test then silently measures the machine. 8 cores per socket give 245 GB/s (E5), and 2 × 245 ≈ 490 GB/s.
- Remote latency also depends on direction and on load on the remote socket: 196 ns from node 1 (E1), 217 ns from node 0.

**(c) Prefetchable "random".**
- A chase with next = (i + step) mod n looks scrambled. For steps of 3, 17, 67 and 257 lines it reports 4.2–4.6 ns, **27–30× below** the true 126.6 ns.
- The stride prefetchers learn any constant step, even 16 KiB.
- A pure sequential chase gives 1.8 ns.

**(d) Frequency.**
- The core clock is 3.88 GHz with ≤ 9 cores busy on a node (E5 generator sweep), 3.53 GHz at 13 cores, 3.23 GHz at 17, and 2.97–3.06 GHz at 21–31 cores (−23%).
- Converting ns to cycles with a fixed single-core clock overstates cycles by up to 30% under load; the 2.7 GHz TSC understates them by about 30% on an idle core. Every cycle figure here uses PMU cycles / ns.

**Figures:**
- `e8_pitfall_cache_reuse_latency.jpg`, `e8_pitfall_cache_reuse_bandwidth.jpg`
- `e8_pitfall_pinning_latency.jpg`, `e8_pitfall_pinning_bandwidth.jpg`
- `e8_pitfall_prefetch_random.jpg`
- `e8_pitfall_frequency.jpg`

**Data:** `E8.csv`, plus raw E8 samples for the strip plots.

---

## MLC cross-check (blocked)
- Intel MLC v3.13 is installed in `tools/mlc`. The archive's SHA256 matches Intel's published value.
- It refuses every mode with exit 22 unless at least 1000 hugetlbfs 2 MiB pages are reserved per node (`echo 4000 > /proc/sys/vm/nr_hugepages`). That requires root, and v3.13 has no flag to skip the check.
- The raw output is in `results/raw/MLC/`, and the status in `results/env/mlc_status.txt`.
- **To run it:** an admin reserves the pages (8 GiB; free them afterwards), then run `python scripts/mlc.py`.
- Without root, MLC would also leave the prefetchers on (`-e`).

## Cross-cutting findings
1. **The hierarchy per core:**
   - latency: 4 / 14 / 50–60 / 492 cycles (1.0 / 3.6 / 13–15.5 / 126 ns)
   - read bandwidth: 190 / 114 / 90 / 43.5 GB/s
   - one node of 31 cores reads DRAM at about 300 GB/s
2. **Concurrency, not latency, sets bandwidth.**
   - One core sustains about 19 demand misses in flight (E5 chains, E3 random reads, E6 gathers all agree through Little's Law).
   - Prefetching raises that to about 86 lines for sequential reads.
   - The socket saturates at about 303 GB/s with 12 cores. The knee sits at about 240 GB/s / 167 ns (reads); past it, extra concurrency only adds queueing latency, and oversubscribing even loses bandwidth.
3. **AMAT works for dependent accesses** (E2, within 3%). **For independent ones, divide by MLP** (E6).
4. **TLB.** 4 KiB pages cost 7 cycles per L1-DTLB miss and 9–34 ns per page walk. Huge pages fix that, but can hurt power-of-two strides (E7c).
5. **Writes cost about 2× in DRAM traffic** because of read-for-ownership. NT stores remove that but cap at about 229 GB/s.

## Open items
- The 1:1 NT-store/load mix on one core (14 GB/s) is unexplained. Next steps: PMU write-combining events and a sweep of the interleave granularity.
- E3: why strided independent loads (256 B, 1 KiB) don't get prefetched while the strided chase does. Test with non-unrolled loads and per-instruction strides.
- E7c: confirm the 2 MiB-page 4 KiB-stride conflict hypothesis by adding a page-offset skew to the stride and measuring cache-set / DRAM-bank distribution.
- Uncore DRAM counters (`amd_umc`, `amd_df`) would measure the true DRAM traffic, but need `perf_event_paranoid ≤ 0`.
- The MLC cross-check needs root to reserve hugetlbfs pages.
