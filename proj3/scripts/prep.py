#!/usr/bin/env python3
"""Create (or verify) the test files on /data, outside the repo:

  data.dat       224 GiB  every byte written once, sequentially, in full
                          RAID5 stripes (1.5 MiB) with --refill_buffers ->
                          no unwritten extents, no holes, incompressible data
  pagecache.dat    8 GiB  same, for the page-cache pitfall (F8)

  python scripts/prep.py            create what is missing, then verify
  python scripts/prep.py --verify   verify only
  python scripts/prep.py --clean    delete all test files (end of project)

Verification (also asserted by tests/test_analysis.py): size, fully allocated
(no sparse holes: st_blocks*512 >= size), no unwritten extents (filefrag -v), sampled 4 KiB blocks are incompressible (zlib saves < 5% per block:
ratio > 0.95) and not all-zero. Total footprint 232 GiB (budget: 256 GiB).
"""
import argparse
import os
import random
import subprocess
import sys
import time
import zlib

sys.path.insert(0, os.path.dirname(__file__))
import common as C  # noqa: E402
import run as R  # noqa: E402

FILES = [(C.DATAFILE, C.DATA_SIZE), (R.PC_FILE, 8 * C.GiB)]


def create(path, size):
    R.wait_quiet(" before prefill")
    t0 = time.time()
    stripe = 1536 * C.KiB
    size = -(-size // stripe) * stripe  # fio rounds down to a whole bs: round up instead
    # never extend an existing file with fio: without --overwrite=1 it unlinks
    # and re-lays-out a file that is too short (found the hard way)
    if path.exists():
        path.unlink()
    cmd = [str(C.FIO), "--name=prefill", f"--filename={path}", f"--size={size}", "--rw=write",
           "--bs=1536k", "--iodepth=32", "--refill_buffers=1", "--direct=1", "--ioengine=libaio", "--fallocate=none",
           "--output-format=json"]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode:
        sys.exit(p.stderr)
    dt = time.time() - t0
    print(f"wrote {path} ({size / C.GiB:.0f} GiB) in {dt:.0f} s ({size / dt / 1e9:.2f} GB/s)")


def unwritten_extents(path):
    out = subprocess.run(["filefrag", "-v", str(path)], capture_output=True, text=True).stdout
    return sum("unwritten" in line for line in out.splitlines())


def compress_ratio(path, samples=16, chunk=1 << 20, seed=0, block=4096):
    """Min over sampled 1 MiB chunks of the mean per-4 KiB-block zlib ratio.
    Per block, because that is what a compressing drive/controller sees; whole
    chunks would also count duplicate blocks (fio reuses a few buffers for
    random writes, so written data is dedupable -- neither md nor these drives
    deduplicate)."""
    rnd = random.Random(seed)
    size = path.stat().st_size
    fd = os.open(path, os.O_RDONLY)
    ratios, zeros = [], 0
    try:
        for _ in range(samples):
            off = rnd.randrange(0, size - chunk) // 4096 * 4096
            b = os.pread(fd, chunk, off)
            zeros += b.count(0) == len(b)
            ratios.append(sum(len(zlib.compress(b[i:i + block], 6))
                              for i in range(0, len(b), block)) / len(b))
    finally:
        os.close(fd)
    return min(ratios), zeros


def verify(path, size):
    ok = path.exists() and path.stat().st_size >= size
    if not ok:
        print(f"MISSING/SHORT {path}")
        return False
    uw = unwritten_extents(path)
    st = path.stat()
    alloc = st.st_blocks * 512 / st.st_size  # < 1: sparse holes (read back as zeros)
    ratio, zeros = compress_ratio(path)
    good = uw == 0 and alloc >= 0.999 and ratio > 0.95 and zeros == 0
    print(f"{'OK ' if good else 'BAD'} {path}: {st.st_size / C.GiB:.2f} GiB, allocated "
          f"{alloc:.4f}, {uw} unwritten extents, min zlib ratio {ratio:.3f}, {zeros} all-zero chunks")
    return good


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--clean", action="store_true")
    a = ap.parse_args()
    if a.clean:
        for p, _ in FILES + [(R.FA_FILE, 0)]:
            if p.exists():
                p.unlink()
                print("removed", p)
        return
    C.SCRATCH.mkdir(parents=True, exist_ok=True)
    if not a.verify:
        for p, s in FILES:
            if not p.exists() or p.stat().st_size < s:
                create(p, s)
    sys.exit(0 if all(verify(p, s) for p, s in FILES) else 1)


if __name__ == "__main__":
    main()
