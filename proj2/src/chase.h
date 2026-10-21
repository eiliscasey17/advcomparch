/* Pointer-chase construction and traversal (the latency probe).
 *
 * A chase is a single cycle of pointers laid out in a buffer: each node holds
 * the address of the next one, so every load depends on the previous one and
 * the time per step is the load-to-use latency of wherever the node lives.
 *
 * Node placement ("slots"):
 *   RANDOM    one node per `stride`-byte slot, slots visited in a uniformly
 *             random single cycle (Sattolo) -- defeats every prefetcher.
 *   SEQ       slot i -> i+1 (wraps): constant stride, prefetcher-friendly.
 *   FAKERAND  slot i -> (i + param) mod n with gcd(param, n) = 1: looks
 *             scrambled, is a constant stride (the pitfall in E8).
 *   PAGERAND  one node per 4 KiB page, at line ((page*5) mod 64) of the page
 *             so pages don't alias the same cache sets; pages in random
 *             order. The data footprint is pages*64 B, the TLB footprint is
 *             pages*4 KiB: separates TLB misses from cache misses (E7).
 */
#ifndef CHASE_H
#define CHASE_H

#include <stddef.h>
#include <stdint.h>

typedef enum { CH_RANDOM = 0, CH_SEQ, CH_FAKERAND, CH_PAGERAND } chase_pattern;

int chase_parse(const char *s);  /* -1 on error */
const char *chase_name(chase_pattern p);

/* Number of nodes a buffer of `bytes` holds for this pattern/stride. */
size_t chase_nodes(size_t bytes, size_t stride, chase_pattern p);

/* Byte offset of node `slot` inside the buffer. */
size_t chase_offset(size_t slot, size_t stride, chase_pattern p);

/* Write the cycle into base[0..bytes). Returns the node count (0 on error)
 * and the address of slot 0 in *start. `param` is the FAKERAND step in slots
 * (bumped up to the next value coprime with n). Deterministic in `seed`. */
size_t chase_build(void *base, size_t bytes, size_t stride, chase_pattern p, size_t param,
                   uint64_t seed, void **start);

/* Follow `steps` pointers from p (steps rounded down to a multiple of 16);
 * returns where it stopped. */
void *chase_run(void *p, size_t steps);

/* k independent chains advanced in lockstep: `steps` per chain. k must be
 * one of CHASE_K_SUPPORTED (returns -1 otherwise). p[] is updated. */
int chase_run_k(void **p, int k, size_t steps);
#define CHASE_K_MAX 32
int chase_k_supported(int k);

/* k start points evenly spaced along the cycle (walks the cycle once). */
void chase_starts(void *start, size_t n, int k, void **out);

#endif
