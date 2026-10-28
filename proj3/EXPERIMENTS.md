# EXPERIMENTS: central registry (methods, status, results)

This is the single place to find what was run, how, and what it showed.
- **Figures:** `results/plots/`, one graph per JPG.
- **Numbers behind every figure:** `results/summary/<ID>.csv`. Rows hold medians over rounds, bootstrap 95% CIs (`*_lo`, `*_hi`) and `others_MBps_max` (foreign traffic seen). `results/summary/<ID>_hist.csv` holds the pooled json+ latency histograms.
- **Raw fio JSON:** `results/raw/` (gitignored). Regenerate it with the commands below.

**System under test:**
- `/data`: XFS on mdadm RAID5 over 4 × Micron 6.4 TB NVMe, with a 512 KiB chunk (1.5 MiB full stripe).
- Shared machine, no root.
- fio 3.43 with `libaio`; io_uring is disabled by the kernel.

See the [README](README.md#system-under-test-no-root).

**Status (2026-10-28):**
- **Complete:** F0–F5. F4 and F5 have summaries in `results/summary/`. Their figures have not been rendered yet; that needs `python scripts/plot.py F4 F5`.
- **Not complete:** F6, F7 and F8. The suite stopped on 2026-10-22 during F6 round 2 (no error in the log; the machine was most likely restarted). F6 has one partial round (raw only); F8 and F7 never started.
- **To finish:** `./scripts/run_all.sh F6 F8 F7`. It is resumable and skips completed experiments.
- **Verification:** 22 pytest tests pass, covering the formulas and fio method checks. `results/env/datafiles.txt` confirms the test files are fully allocated, have no unwritten extents and are incompressible.

## Common method
- **Jobs.** fio `--direct=1 --ioengine=libaio --time_based`, with 20 s measured after a 5 s ramp, or 30–60 s for the latency and tail runs.
  - `--norandommap --randrepeat=0`; random offsets are aligned to the block size (verified).
  - `--group_reporting` with full JSON. The tail experiments use `json+`, which keeps the full latency histogram.
- **Fixed QD for the sweeps.** QD 32 = 4 jobs × iodepth 8. Sequential multi-job runs use disjoint per-job regions (`offset_increment`, verified).
- **Region.** The first 64 GiB of `data.dat`, unless the experiment sweeps it (F6 goes up to 224 GiB).
- **Placement.** One CPU per job on NUMA node 0, where the NVMe controllers sit.
- **Data.** Writes are incompressible: `--buffer_compress_percentage=0`, zlib ratio 1.00. fio reuses its buffers, so the data is dedupable; neither md nor the drives deduplicate.
- **Order and repetition.** Job order is shuffled in each round (F7 and F8 are ordered because state matters). Results are medians over 2–3 rounds.
- **Gate.**
  - Before each job: /data must carry ≤ 300 IOPS and ≤ 30 MB/s of other traffic.
  - During each job: foreign traffic = md124 bytes minus fio's own block I/O (per-process accounting). A job that saw more than 30 MB/s of it is re-measured.
- **Device view.** Per job, from `/proc/diskstats` for md124 and its members:
  - drive bytes per host byte, which shows RAID5 read-modify-write (RMW)
  - requests in flight at the drives
- **Little's Law.** L = IOPS × mean latency, compared with the configured QD.

| ID | run_id | rounds | jobs | wall time | re-measured / still disturbed |
|---|---|---|---|---|---|
| F0 | see `results/summary/F0.csv` (`run`) | 3 | 3 | 1.5 min | 0 / 0 |
| F1 | 〃 | 3 | 4 | 8 min | 0 / 0 |
| F2 | 〃 | 2 | 34 | 32 min | 0 / 0 |
| F3 | 20261022-135117 | 3 | 8 | 11 min | 0 / 0 |
| F4 | 20261022-140240 | 2 | 55 | 52 min | 0 / 0 |
| F5 | 20261022-145446 | 2 | 9 | 21 min | 0 / 0 |
| F6–F8 | not complete (see Status) | | | | |

Commands:
```
python scripts/prep.py
python scripts/run.py <ID>
python scripts/summarize.py <ID>
python scripts/plot.py <ID>
```
`./scripts/run_all.sh` runs everything and is resumable.

---

## F0: Calibration
- **fio's own cost** (null engine, no I/O): 0.03 µs per I/O at QD1 (8.4M IOPS) and 0.13 µs at QD32. That is negligible against the 69 µs of a real 4 KiB read, so latencies measure the storage stack, not fio.
- **Sanity:** a 4 KiB random read at QD1 takes 69.0 µs mean, p99 73 µs.

## F1: Zero-queue (QD=1) baselines
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

**Figures:** `f1_qd1_latency_table.jpg`, `f1_qd1_latency_percentiles.jpg`, `f1_qd1_latency_ccdf.jpg`.

## F2: Block-size sweep (QD 32)
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

**Figures:** `f2_bs_throughput_{random,sequential}.jpg`, `f2_bs_latency_{random,sequential}.jpg`.

## F3: Read/write mix (QD 32)
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

**Figures:** `f3_mix_throughput_{rand4k,seq128k}.jpg`, `f3_mix_latency_{rand4k,seq128k}.jpg`.

## F4: Queue-depth / parallelism sweep
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

## F5: Tail latency at mid-QD, knee and past-knee
QDs: the knee is taken from F4 (smallest total QD reaching 90% of the 8-job peak) = 128; mid = 32; past = 512. Each run uses 4 jobs, 2 × 60 s, with json+ histograms.

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

## F6–F8 (not complete)
Planned content:
- **F6:** LBA range from 16 MiB to 224 GiB.
- **F7:** device state. About 40 min of sustained 4 KiB random writes over 224 GiB (≈ 0.5 TB written by the host, about 1 TB on the drives), with QD1 probes before, right after, and after 10 min idle. Also RAID5 amplification.
- **F8:** pitfalls (re-run needed):
  - the page cache
  - buffered writes
  - effective vs requested QD
  - unwritten (fallocated) extents and first writes
  - data pattern
  - a CPU-bound load generator

## Findings during development (already verified)
- **fio deletes and recreates an existing file it is asked to extend** (unless `--overwrite=1`). The 224 GiB data file became a sparse file of zeros. The verification in `prep.py` (allocation, zero chunks) caught it; prep now never extends files.
- **`--refill_buffers` makes fio the bottleneck.** At about 3 µs of CPU per KiB, 128 KiB sequential writes at QD1 drop from 2,959 to 1,249 IOPS, with fio at 56% user CPU. IOPS × latency falls to 0.45, so the device is idle half the time.
- **One fio thread cannot keep 32 requests at the drives.** At iodepth 32, only about 11 are in flight at the drives (diskstats); completed I/Os wait for the CPU-bound thread to reap them. Several jobs are needed to fill the device.
