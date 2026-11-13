# EXPERIMENTS: central registry (methods, status, results)

This is the single place to find what was run, how, and what it showed.
- **Figures:** `results/plots/`, one graph per JPG.
- **Numbers behind every figure:** `results/summary/<ID>.csv`. Rows hold medians over rounds, bootstrap 95% CIs (`*_lo`, `*_hi`) and `others_MBps_max` (foreign traffic seen). `results/summary/<ID>_hist.csv` holds the pooled json+ latency histograms.
- **Raw results behind every figure:** committed, gzipped, in `results/raw_runs/<experiment>/`. That's every fio JSON, the IOPS logs and the per-job CSV rows, with the runs pinned in `results/runs.json`. The full local `results/raw/` tree (quick and interrupted runs too) is gitignored.
- **fio job files:** `results/fio_jobs/<experiment>/*.fio`, rebuilt from the options in each job's own JSON.
- **Experiment IDs** were renamed from `F0`–`F8` to descriptive names. The old IDs still work as aliases in every script.

**System under test:**
- `/data`: XFS on mdadm RAID5 over 4 × Micron 6.4 TB NVMe, with a 512 KiB chunk (1.5 MiB full stripe).
- Shared machine, no root.
- fio 3.43 with `libaio`; io_uring is disabled by the kernel.

See the [README](README.md#system-under-test-no-root).

**Status (2026-10-29): complete.**
- **All nine experiments are done**, with summaries in `results/summary/` and 33 figures in `results/plots/`.
- **History:** the first full run stopped on 2026-10-22 during lba (the machine was restarted). lba, pitfall and state were re-run from scratch on 2026-10-29 with `./scripts/run_all.sh lba pitfall state`; the partial lba run (20261022-151518) is ignored.
- **Verification:** 23 pytest tests pass, covering the formulas and fio method checks. `results/env/datafiles.txt` confirms the test files are fully allocated, have no unwritten extents and are incompressible.

## Common method
- **Jobs.** fio `--direct=1 --ioengine=libaio --time_based`, with 20 s measured after a 5 s ramp, or 30–60 s for the latency and tail runs.
  - `--norandommap --randrepeat=0`; random offsets are aligned to the block size (verified).
  - `--group_reporting` with full JSON. The tail experiments use `json+`, which keeps the full latency histogram.
- **Fixed QD for the sweeps.** QD 32 = 4 jobs × iodepth 8. Sequential multi-job runs use disjoint per-job regions (`offset_increment`, verified).
- **Region.** The first 64 GiB of `data.dat`, unless the experiment sweeps it (lba goes up to 224 GiB).
- **Placement.** One CPU per job on NUMA node 0, where the NVMe controllers sit.
- **Data.** Writes are incompressible: `--buffer_compress_percentage=0`, zlib ratio 1.00. fio reuses its buffers, so the data is dedupable; neither md nor the drives deduplicate.
- **Order and repetition.** Job order is shuffled in each round (state and pitfall are ordered because state matters). Results are medians over 2–3 rounds.
- **Gate.**
  - Before each job: /data must carry ≤ 300 IOPS and ≤ 30 MB/s of other traffic.
  - During each job: foreign traffic = md124 bytes minus fio's own block I/O (per-process accounting). A job that saw more than 30 MB/s of it is re-measured.
- **Device view.** Per job, from `/proc/diskstats` for md124 and its members:
  - drive bytes per host byte, which shows RAID5 read-modify-write (RMW)
  - requests in flight at the drives
- **Little's Law.** L = IOPS × mean latency, compared with the configured QD.

| ID | run_id | rounds | jobs | wall time | re-measured / still disturbed |
|---|---|---|---|---|---|
| calib | see `results/summary/calib.csv` (`run`) | 3 | 3 | 1.5 min | 0 / 0 |
| qd1 | 〃 | 3 | 4 | 8 min | 0 / 0 |
| blocksize | 〃 | 2 | 34 | 32 min | 0 / 0 |
| rwmix | 20261022-135117 | 3 | 8 | 11 min | 0 / 0 |
| qdsweep | 20261022-140240 | 2 | 55 | 52 min | 0 / 0 |
| tail | 20261022-145446 | 2 | 9 | 21 min | 0 / 0 |
| lba | 20261029-111226 | 2 | 24 | 23 min | 0 / 0 |
| state | 20261029-115805 | 1 (ordered) | 49 | 57 min | 0 / 0 |
| pitfall | 20261029-113511 | 2 (ordered) | 26 | 23 min | 2 / 0 (writeback of pitfall's own buffered-write job) |

Commands:
```
python scripts/prep.py
python scripts/run.py <ID>
python scripts/summarize.py <ID>
python scripts/plot.py <ID>
```
`./scripts/run_all.sh` runs everything and is resumable.

---

## calib: Calibration
- **fio's own cost** (null engine, no I/O): 0.03 µs per I/O at QD1 (8.4M IOPS) and 0.13 µs at QD32. That is negligible against the 69 µs of a real 4 KiB read, so latencies measure the storage stack, not fio.
- **Sanity:** a 4 KiB random read at QD1 takes 69.0 µs mean, p99 73 µs.

## qd1: Zero-queue (QD=1) baselines
**Question:** what does one request cost when nothing else is queued?

**Results** (3 × 30 s, medians; completion-latency percentiles in µs):

| workload | IOPS | MB/s | mean | p50 | p99 | p99.9 | drive bytes per host byte (read / write) |
|---|---|---|---|---|---|---|---|
| 4 KiB random read | 14,412 | 59 | **69.2** | 61.7 | 74.2 | 76.3 | 1.00 / 0 |
| 4 KiB random write | 9,222 | 38 | **108.2** | 101.9 | 121.3 | 214.0 | 2.01 / 2.02 |
| 128 KiB sequential read | 14,305 | 1,875 | **69.7** | 45.3 | 301.1 | 366.6 | 1.00 / 0 |
| 128 KiB sequential write | 3,276 | 429 | **305.0** | 140.3 | 618.5 | 667.7 | 0.67 / 2.00 |

IOPS × mean latency = 1.00 for all four, so exactly one request was in flight.

**Interpretation:**
- **Random reads.** A 4 KiB random read is one NAND read on one drive plus the XFS / md / block-layer path, about 62 µs typical with a very tight tail (p99.9 is only 1.2× p50).
- **Random writes and RAID5.** A 4 KiB random write costs **2 drive reads + 2 drive writes** (old data and old parity, then new data and new parity). That is RAID5 read-modify-write (RMW), measured exactly at 2.01 / 2.02 bytes per host byte. Writes land in the drives' power-loss-protected DRAM, so the cost is mostly the reads plus software: 108 µs, about 1.6× a read.
- **128 KiB sequential reads** are bimodal. The p50 of 45 µs is below the 4 KiB random read, because the drive's internal read-ahead within a 512 KiB chunk serves the next request. The p99 of 301 µs comes from requests that cross a chunk or drive boundary.
- **128 KiB sequential writes** are partial stripes (128 KiB of a 1.5 MiB stripe), so md still reads 0.67 bytes per byte written. The latency is bimodal: p50 140 µs, p99 619 µs, which is when the stripe cache must read before it can write.

**Figures:** `qd1_latency_table.jpg`, `qd1_latency_percentiles.jpg`, `qd1_latency_ccdf.jpg`.

## blocksize: Block-size sweep (QD 32)
**Question:** how do throughput and latency, taken from the same runs, change with block size?

**Results:**

| bs | random read | sequential read | random write | sequential write |
|---|---|---|---|---|
| 4 KiB | **425K IOPS**, 1.74 GB/s, 75 µs | 192K IOPS, 0.79 GB/s, 167 µs | **61.5K IOPS**, 252 MB/s, 524 µs | 79K IOPS, 324 MB/s, 407 µs |
| 128 KiB | 11.7 GB/s, 358 µs | 10.1 GB/s, 413 µs | 427 MB/s, 9.9 ms | 866 MB/s, 4.8 ms |
| 1 MiB | **20.1 GB/s**, 1.67 ms | **21.1 GB/s**, 1.59 ms | 693 MB/s, 48.6 ms | **1.40 GB/s**, 24.1 ms |
| 1.5 MiB (full stripe) | – | **21.5 GB/s** | – | 1.27 GB/s |

**Interpretation:**
- **Reads** are IOPS-bound below about 32 KiB and bandwidth-bound above. Latency at a fixed QD rises in proportion to transfer time once bandwidth saturates (QD 32 × 1 MiB / 20 GB/s ≈ 1.6 ms, matching the measured mean, which is Little's Law).
  - Sequential 4 KiB reads are slower than random ones at QD 32. With 4 jobs each streaming its own region, small sequential requests concentrate on one 512 KiB chunk, i.e. one drive at a time; random reads spread across all 4 drives.
- **Writes** are capped by the md RAID5 write path at about 0.7–1.4 GB/s, against about 21 GB/s for reads.
  - Small random writes pay RMW: 2 bytes read + 2 bytes written on the drives per byte.
  - Large sequential writes (≥ 1 MiB) are merged by md's stripe cache into nearly full stripes: 0.1 bytes read per byte. That is why they are the fastest writes.
  - Even then, parity is computed by a single kernel thread (`group_thread_cnt=0`), which caps write bandwidth far below the drives' combined capability.
  - The full-stripe 1.5 MiB size is no faster than 1 MiB at QD 32, because both are already mostly full-stripe after merging.

**Figures:** `blocksize_throughput_{random,sequential}.jpg`, `blocksize_latency_{random,sequential}.jpg`.

## rwmix: Read/write mix (QD 32)
**Results:**

| mix | 4 KiB random: total IOPS (read / write) | read mean / p99 | write mean / p99 | 128 KiB sequential: total GB/s |
|---|---|---|---|---|
| 100% read | **424K** | 75 / 91 µs | – | **10.8** |
| 70/30 | 167K (117K / 50K) | 77 / 157 µs | 455 / 627 µs | 2.18 |
| 50/50 | 104K (52K / 52K) | 77 / 153 µs | 534 / 700 µs | 1.36 |
| 100% write | 56K | – | 572 / 700 µs | 0.73 |

**Interpretation:**
- **Writes set the pace.** With 4 KiB random I/O, the array completes about 50K writes/s whatever the mix. Any write share therefore drags total throughput towards the write ceiling: 30% writes cuts total IOPS by 2.5×.
- **Read latency.** The mean read latency stays at about 77 µs; reads are not blocked behind writes on average. The read p99, though, rises from 91 to about 155 µs, because some reads collide with RMW reads and writes on the same drive.
- **Sequential 128 KiB** shows the same effect, but stronger, because each sequential write costs a large partial-stripe RMW.

**Figures:** `rwmix_throughput_{rand4k,seq128k}.jpg`, `rwmix_latency_{rand4k,seq128k}.jpg`.

## qdsweep: Queue-depth / parallelism sweep
**Question:** how do throughput and latency trade off as concurrency grows, where is the knee, and does Little's Law hold?

**Results, 4 KiB random read:**

| total QD | 1 job: K IOPS / mean µs / p99 µs | 8 jobs: K IOPS / mean µs / p99 µs |
|---|---|---|
| 1 | 14.4 / 69 / 77 | – |
| 8 | 110 / 72 / 101 | 114 / 70 / 99 |
| 16 | **162 / 99** / 133 | 227 / 70 / 106 |
| 32 | 162 / 197 / 237 | 443 / 72 / 115 |
| 64 | 163 / 393 / 438 | 832 / 77 / 129 |
| 128 | 162 / 788 / 836 | **1114 / 115** / 167 |
| 256 | 162 / 1575 / 1630 | 1117 / 229 / 281 |
| 1024 | – | 1111 / 922 / 1026 |

- With iodepth 32 per job: 2 / 4 / 8 / 16 jobs give 298K / 573K / 1113K / **1947K IOPS** (16 jobs: 263 µs mean).
- **Little's Law holds everywhere:** IOPS × mean latency equals the configured QD to within 1% (e.g. 0.997 at QD1, 127.95 at QD128).
- **4 KiB random write** (4 jobs):

  | QD | throughput | mean latency |
  |---|---|---|
  | 4 | 32.6K IOPS | 123 µs |
  | 16 | 56.1K | 285 µs |
  | 32 | **64.4K** | 498 µs |
  | 128 | 58.7K | 2.2 ms |
  | 512 | 55.6K | 9.2 ms |

- **128 KiB sequential:**
  - Reads reach 15.7 GB/s (1 job, QD64) and 18.7 GB/s (4 jobs, QD128).
  - Writes reach 1.50 GB/s (1 job, QD64) and 1.22 GB/s (4 jobs, QD128), with latency rising to 5.6–13.8 ms.

**Interpretation:**
- **Below the knee, latency is flat** (69–72 µs up to QD 8 for one job, up to QD 64 for 8 jobs), so throughput grows linearly with QD = IOPS × latency.
- **Past the knee, throughput is flat and latency grows linearly with QD.** One job at QD 256 delivers the same 162K IOPS as at QD 16, at 16× the latency. Each extra request only waits in a queue.
- **For reads, the knee is set by the submitting CPU threads, not the SSDs.**
  - One fio thread saturates at about 162K IOPS (diskstats show only about 10 requests in flight at the drives).
  - Eight threads saturate at about 1.11M IOPS (about 73 at the drives).
  - Sixteen threads reach 1.95M IOPS.
  - To find the device's knee, you need more submitters (numjobs), not more iodepth per thread.
- **For writes, the knee comes early (QD ≈ 32) and is set by md RAID5.**
  - About 60K IOPS of small-write RMW go through a single parity thread.
  - Beyond the knee, throughput even drops slightly while latency explodes.
- **Device-side queue depth (`dev_qd`) is reliable for reads only.** For writes, md's member-request accounting yields meaningless values, so it is not used.

**Figures:** `qdsweep_curve_{randread4k,randwrite4k,seq128k}.jpg`, `qdsweep_iodepth_vs_jobs.jpg`, `qdsweep_little_check.jpg`.

## tail: Tail latency at mid-QD, knee and past-knee
QDs: the knee is taken from qdsweep (smallest total QD reaching 90% of the 8-job peak) = 128; mid = 32; past = 512. Each run uses 4 jobs, 2 × 60 s, with json+ histograms.

| workload | QD | K IOPS | p50 | p99 | p99.9 | p99.99 (µs) |
|---|---|---|---|---|---|---|
| 4 KiB random read | 32 (mid) | 424 | 69 | 91 | 118 | 140 |
| | 128 (knee) | 573 | 216 | 239 | 264 | 301 |
| | 512 (past) | 571 | 889 | 913 | 946 | 983 |
| 4 KiB random write | 32 | 55.8 | 565 | 696 | 811 | 1,047 |
| | 128 | 55.0 | 2,376 | 2,654 | 2,867 | 3,178 |
| | 512 | 54.0 | 9,372 | 10,551 | 11,076 | 13,304 |
| 70/30, reads | 32 | 166 total | 68 | 165 | 446 | 688 |
| | 128 | 170 total | 69 | 226 | 534 | 819 |
| | 512 | 169 total | 70 | 178 | 502 | 815 |
| 70/30, writes | 32 | | 449 | 635 | 807 | 1,042 |
| | 512 | | 9,896 | 10,420 | 10,813 | 13,566 |

**Interpretation:**
- **Past the knee, the whole distribution shifts right; it does not just grow a tail.**
  - With 4 jobs, the read knee is at about 573K IOPS (4 submitting threads).
  - At QD 512 the median is 889 µs, almost exactly QD / IOPS = 512 / 571K = 897 µs (Little's Law). The extra requests sit in fio/kernel queues, and every one of them waits.
  - The tail ratio p99.99 / p50 stays small (2.0 at mid, 1.1 past).
- **Write tails scale the same way** (p50 565 µs → 9.4 ms) because RAID5 RMW is the queueing server.
- **The mixed workload is where tails hurt reads.**
  - The read median stays at about 69 µs at every QD.
  - The read p99.9 is already 446–534 µs, 6–8× the median, even at QD 32.
  - These are reads that land on a drive busy with RMW traffic.
  - Pushing the mix deeper only inflates the write side (p50 10 ms at QD 512), with no throughput gain (166K → 169K IOPS).

**Figures:** `tail_ccdf_{randread4k,randwrite4k,randrw70-4k}.jpg`, `tail_percentiles.jpg`.

## lba: LBA range (working set)
**Question:** does the span of addresses touched matter? Candidates: the drives' FTL mapping cache, and md's stripe cache.

Run 20261029-111226: 2 rounds, 24 jobs. Random offsets are restricted to the first N bytes of `data.dat`, N = 16 MiB … 224 GiB.

| region | read QD1: K IOPS / mean / p99 (µs) | read QD32: K IOPS / p99 (µs) | write QD32: K IOPS / mean / p99 (µs) | write: drive reads per host byte |
|---|---|---|---|---|
| 16 MiB | 14.4 / 69 / 74 | 409 / 97 | **80.5 / 397 / 541** | **0.00** |
| 64 MiB | 14.4 / 69 / 74 | 409 / 100 | 64.6 / 495 / 745 | 1.26 |
| 256 MiB | 14.3 / 70 / 75 | 412 / 93 | 64.9 / 494 / 668 | 1.80 |
| 1 GiB | 14.4 / 69 / 74 | 411 / 93 | 59.8 / 535 / 668 | 1.95 |
| 4 GiB | 14.4 / 69 / 74 | 412 / 93 | 60.2 / 532 / 668 | 1.99 |
| 16 GiB | 14.4 / 69 / 74 | 412 / 93 | 59.7 / 536 / 664 | 2.00 |
| 64 GiB | 14.4 / 69 / 74 | 412 / 93 | 64.0 / 502 / 647 | 2.00 |
| 224 GiB | 14.4 / 69 / 74 | 411 / 100 | 60.7 / 527 / 672 | 2.00 |

**Interpretation:**
- **Reads do not care about the LBA range.**
  - Over a 14,000× range, QD1 latency stays at 69 µs (±0.3%) and QD32 throughput at 409–412K IOPS.
  - Every read reaches the drives (1.00 drive bytes per host byte), so no cache is hiding anything.
  - There is no sign of an FTL mapping-cache miss up to 224 GiB. That is 56 GiB per drive, about 1% of each 6.4 TB drive; spans close to the full drive cannot be tested without root.
- **Small write regions are faster, but because of md, not the SSDs.**
  - At 16 MiB the RAID5 read-modify-write reads disappear (0.00 drive reads per host byte, against the usual 2.0). The old data and parity are found in md's stripe cache, so throughput is 80K IOPS instead of about 60K (+30%), and the mean latency is 397 µs.
  - Hit rates fall as the region grows (1.26 reads at 64 MiB, 1.80 at 256 MiB). From 1 GiB on every write misses: 2 drive reads + 2 drive writes per host write, about 60K IOPS.
  - md grows its stripe cache above the configured `stripe_cache_size` (256) on demand, which is why tens of MiB are still partly cached.
  - Between 1 GiB and 224 GiB, write throughput varies between 60K and 64K IOPS. That is within the round-to-round spread already seen in the blocksize to tail experiments, not a trend.
- **Consequence for benchmarking:** a small test file overstates RAID5 small-write throughput by about a third. The other experiments use 64 GiB, well past this effect.

**Figures:** `lba_iops_qd32.jpg`, `lba_latency.jpg`.

## state: Device state (sustained writes, idle recovery) and RAID5 amplification
**Question:** do the drives slow down once their write cache and spare blocks are used up (the "fresh-out-of-box vs steady state" cliff), do they recover when idle, and what does the RAID5 layer really send to the drives?

Run 20261029-115805: one ordered pass, 57 min.
1. QD1 and QD32 probes.
2. 40 back-to-back 60 s segments of 4 KiB random writes at QD32 over the whole 224 GiB file.
3. The same probes right after.
4. 10 min idle.
5. The probes again.

**Sustained writing (40 × 60 s):**
- The host wrote **0.58 TB**. The drives wrote 1.17 TB and read 1.17 TB, an exact 2 + 2 RAID5 read-modify-write in every segment.
- Throughput per segment: median 58.8K IOPS, range 57.2K–67.0K.
  - **No downward trend:** 58.3K in the first 10 min, 59.5K in the last 10.
  - The short bursts to 62–67K (minutes 12–13, 16–17, 26, 31, 38–39) are the same RMW work running faster for a few seconds. In segment 17 there was about 3.6 MB/s of other traffic; md may have found more stripes in cache.
- Latency per segment stayed in a band: p50 465–545 µs, p99 635–791 µs, p99.9 733–1,122 µs.

**QD1 probes:**

| probe | before | right after | after 10 min idle |
|---|---|---|---|
| 4 KiB random read: mean / p99 / p99.9 (µs) | 69.0 / 73 / 77 | 69.2 / 74 / 78 | 69.6 / 74 / 87 |
| 4 KiB random write: mean / p99 / p99.9 (µs) | 110.9 / 116 / 216 | 99.5 / 106 / 208 | 99.7 / 106 / 212 |
| 4 KiB random read, QD32 (K IOPS) | 412 | 413 | 413 |

**Interpretation:**
- **No write cliff, and nothing to recover from.** These are enterprise TLC drives, and md RAID5 throttles the load: about 58K host writes/s is about 117 MB/s written per drive, far below what each drive can absorb. In 40 min each drive received about 0.29 TB of writes, roughly 5% of its 6.4 TB. That is not enough to exhaust the overprovisioned spare area, and background garbage collection, if any ran, kept up.
- **Reads were unaffected** by 40 min of heavy writes, both during the probes and 10 min later.
- **QD1 writes were about 10% faster after the sustained phase** (111 → 100 µs), and stayed that way after 10 min idle. One plausible cause is that the 64 GiB probe region had just been rewritten, so the drives' write path or md's stripe cache was warm. This cannot be confirmed without SMART or drive telemetry.
- **What would be needed to see steady state:** writing the full drive capacity several times over, at the raw device. Without root and on a shared filesystem, that is neither possible nor acceptable. Temperature, SLC-cache occupancy and garbage-collection activity are not observable here (no SMART), so this result is "no state effect visible from the host at this write volume", not "the drives have no state".
- **RAID5 amplification** (`state_device_io_amplification.jpg`, from the qd1, blocksize and rwmix runs):
  - Small random writes cost 2 drive reads + 2 drive writes per host byte.
  - Full-stripe (1.5 MiB) and 1 MiB sequential writes cost 4/3 writes and almost no reads (0.06–0.07).
  - 128 KiB sequential writes land in between (0.67 reads + 1.75–2.0 writes), depending on how much of each stripe md can merge.
  - This, plus md's single parity thread, is why every write result from qd1 to state is capped near 60K IOPS / 1.5 GB/s while reads reach 2M IOPS / 21 GB/s.

**Figures:** `state_sustained_iops.jpg`, `state_sustained_latency.jpg`, `state_probes.jpg`, `state_device_io_amplification.jpg`.

## pitfall: Pitfalls (misleading setups)
Run 20261029-113511: 2 rounds, 26 jobs in a fixed order (state matters). The page-cache jobs use the 8 GiB `pagecache.dat`; the data-pattern and generator jobs use a temporary 8 GiB `pattern.dat`, so zeros never reach `data.dat`.

**(a) Page cache: reads.** 4 KiB random reads at QD1, and 128 KiB sequential reads at QD1:

| setup | K IOPS | mean latency | bytes reaching /data per byte fio read |
|---|---|---|---|
| 4 KiB, `--direct=1` | 14.0 | 69 µs | 1.05 |
| 4 KiB, buffered, cold cache | 15.3 | 65 µs | 1.61 (readahead) |
| 4 KiB, buffered, file already in cache | **557** | **1.7 µs** | **0.01** |
| 128 KiB sequential, `--direct=1` | 8.4 (1.10 GB/s) | 113 µs | 1.05 |
| 128 KiB sequential, buffered, warm | **102 (13.4 GB/s)** | 9.6 µs | 0.04 |

- Once the file is in the page cache, a "4 KiB random read" benchmark reports 40× the IOPS and a 42× lower latency than the drives deliver, and the drives do almost no I/O.
- A sequential buffered read reports 13.4 GB/s at QD1, 12× the direct figure.
- Cold buffered reads look 6–9% faster than direct reads, but readahead moved 61% more bytes than were asked for.
- Caveats:
  - The page-cache jobs skip the foreign-traffic re-measure, because buffered I/O does not show up in fio's per-process block accounting. In round 2 they saw up to 84 MB/s of other traffic.
  - The direct 128 KiB row is round 1 only (round 2 was disturbed: 7.1K IOPS).
  - On this 8 GiB file, a 128 KiB direct read takes 113 µs, against 70 µs on `data.dat` in qd1. This was not investigated further.

**(b) Buffered writes.**
- 4 KiB random writes at QD1: direct gives 8.3K IOPS at 114 µs; buffered gives **204K IOPS at 4.7 µs** (24×).
- Only 39% of the buffered bytes reached /data while the job ran; the rest was written back afterwards.
- The foreign-traffic gate caught this: the job after it saw 118–133 MB/s of "other" traffic (the kernel flushing fio's dirty pages) and was re-measured both rounds.

**(c) Requested vs effective queue depth.** 4 KiB random read, iodepth 32:

| setup | K IOPS | mean latency (fio) | requests in flight at the 4 drives |
|---|---|---|---|
| libaio, `--direct=1`, 1 job | 166 | 193 µs | 10.4 |
| libaio, buffered | **14.1** | **2,272 µs** | **0.9–1.2** |
| psync, iodepth 32 | 14.6 | 68 µs | 0.9–1.9 |
| psync, 32 jobs | 454 | 70 µs | 28.9 |

- **libaio without O_DIRECT submits synchronously.** Only one I/O at a time reaches the device, so throughput equals QD1. fio still stamps 32 requests as queued, so its reported latency is 32 × 69 µs. Little's Law on fio's own numbers (IOPS × latency = 32) cannot reveal this; the device-side count (about 1 in flight) does.
- **`psync` ignores iodepth.** It runs at QD1. Parallelism has to come from numjobs.
- The 1-job direct row (166K IOPS, 10.4 in flight at the drives) is qdsweep's CPU-bound submitter again.
- In-flight ranges are the two rounds. The device-side count includes other users' requests on the same drives, which pushed round 2 of the QD1 rows above 1.

**(d) Never-written (fallocated) extents.**
- Random reads at QD32 from a fallocated, never-written 16 GiB file return **1.01M IOPS at 32 µs, with zero bytes reaching /data**. XFS returns zeros for unwritten extents without doing I/O. The same reads on written data give 413K IOPS at 77 µs.
- The first write into an unwritten extent costs about 18 µs more than an overwrite (QD1: 124 vs 106 µs, 8.0K vs 9.4K IOPS). Each first write converts the extent to written, which is a metadata transaction. Both still pay the full RAID5 RMW (2.0 drive reads + 2.0 drive writes per host byte).

**(e) Data pattern.** QD32 writes:

| pattern | 4 KiB random: K IOPS | 128 KiB sequential: MB/s |
|---|---|---|
| incompressible | 58.9 | 832 |
| 50% compressible | 60.8 | 669 |
| zeros | 62.6 | 749 |

- No consistent ordering by compressibility. Zeros are not the fastest sequential case, and the same incompressible 128 KiB configuration gives 832 MB/s here and 756 MB/s in (f), so the spread is run-to-run noise.
- Drive bytes per host byte are identical for all three patterns (sequential: 0.66 read, 1.75 write; random: 2.0 / 2.0). Nothing on this path compresses or deduplicates.
- Incompressible data stays the default anyway, because compressing SSDs exist and would make zeros look fast.

**(f) The load generator as the bottleneck.** 128 KiB sequential writes, `--refill_buffers` (a fresh random buffer for every I/O) vs reused incompressible buffers:

| QD | reused buffers | refill_buffers | fio user CPU (refill) | IOPS × latency (refill) |
|---|---|---|---|---|
| 1 | 3,231 IOPS (424 MB/s) | **1,528 IOPS (200 MB/s)** | 50% | **0.51** |
| 32 | 756 MB/s | 666 MB/s (−12%) | 29% | 30.8 |

- At QD1, refilling halves throughput. IOPS × latency = 0.51 means the device is idle half the time while fio generates data.
- At QD32 the cost mostly overlaps with I/O, and md's parity thread is the bottleneck anyway.

**Figures:** `pitfall_page_cache_reads.jpg`, `pitfall_buffered_write.jpg`, `pitfall_effective_qd.jpg`, `pitfall_unwritten_reads.jpg`, `pitfall_first_write.jpg`, `pitfall_data_pattern.jpg`, `pitfall_generator.jpg`.

## Findings during development (already verified)
- **NVMe device names changed across the 2026-10-22 restart.** The member that was `nvme1n1` became `nvme2n1`, and `nvme1n1` is now an unrelated 800 GB drive. The lba, state and pitfall runs sampled `/proc/diskstats` with the old hard-coded list (3 of 4 members plus that drive), so their device-side queue depth came out 25% low.
  - **Fix:** the member list is now read from sysfs, and `summarize.py` re-derives `dev_qd` from the member drives fio lists in its own JSON. On qdsweep this matches the diskstats value exactly.
  - **Not affected:** RAID amplification (drive bytes per host byte), which always came from fio's own member list.
  - New tests check the members are the 6.4 TB drives and that the JSON-based value agrees with diskstats.
- **fio deletes and recreates an existing file it is asked to extend** (unless `--overwrite=1`). The 224 GiB data file became a sparse file of zeros. The verification in `prep.py` (allocation, zero chunks) caught it; prep now never extends files.
- **`--refill_buffers` makes fio the bottleneck.** At about 3 µs of CPU per KiB, 128 KiB sequential writes at QD1 drop from 2,959 to 1,249 IOPS, with fio at 56% user CPU. IOPS × latency falls to 0.45, so the device is idle half the time.
- **One fio thread cannot keep 32 requests at the drives.** At iodepth 32, only about 11 are in flight at the drives (diskstats); completed I/Os wait for the CPU-bound thread to reap them. Several jobs are needed to fill the device.
