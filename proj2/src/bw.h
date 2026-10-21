/* Bandwidth / load-generator kernels.
 *
 * An "access" i touches `gran` bytes at byte offset off(i) = slot(i) * stride
 * of the buffer, where slot(i) = i (seq order) or idx[i] (random order, a
 * permutation of all slots). Access i is a READ if (i mod (r+w)) < r, else a
 * WRITE -- so the read:write ratio of accesses is exactly r:w, and reads and
 * writes are interleaved over the same address stream (distinct lines).
 *   100% read = 1:0   100% write = 0:1   70/30 = 7:3   50/50 = 1:1
 * Writes are normal stores (the line is read-for-ownership first, so DRAM
 * sees an extra read per written line) or non-temporal streaming stores
 * (no RFO). After each access the generator can spin `delay` iterations of
 * an empty loop (MLC-style injection delay) to throttle its request rate.
 */
#ifndef BW_H
#define BW_H

#include <stddef.h>
#include <stdint.h>

typedef struct {
    int gran;       /* bytes per access: 8, 16, 32, 64 */
    size_t stride;  /* bytes between consecutive slots, multiple of gran */
    int r, w;       /* read:write access ratio (r+w >= 1) */
    int nt;         /* non-temporal stores */
    int delay;      /* empty-loop iterations after each access */
    const uint32_t *idx; /* NULL = sequential slot order */
    size_t nslots;  /* slots in the buffer */
} bw_cfg;

/* Validate a config; returns NULL if fine, else an error string. */
const char *bw_check(const bw_cfg *c);

/* Run accesses [first, first+count) (access i uses slot i mod nslots).
 * Returns a value derived from everything read (so loads can't be elided). */
uint64_t bw_run(const bw_cfg *c, char *base, size_t first, size_t count);

/* Fill idx[0..n) with a random permutation (Fisher-Yates, seeded). */
void bw_perm(uint32_t *idx, size_t n, uint64_t seed);

#endif
