/* Buffer allocation with explicit page-size control and placement checks.
 *
 * THP is system-wide `always` on this machine, so a "4 KiB page" buffer must
 * be madvise(MADV_NOHUGEPAGE)'d explicitly and a "2 MiB page" buffer
 * madvise(MADV_HUGEPAGE)'d (defrag=madvise -> fault-time compaction). Both are
 * applied before first touch; the achieved page mix is read back from
 * /proc/self/smaps so every result row reports what it actually ran on. */
#ifndef MEM_H
#define MEM_H

#include <stddef.h>

typedef enum { PAGES_4K = 0, PAGES_2M = 1, PAGES_DEFAULT = 2 } page_mode;

typedef struct {
    void *base;     /* 2 MiB aligned */
    size_t bytes;   /* requested size */
    size_t mapped;  /* mmap length (for munmap) */
    void *map;      /* mmap return value */
    page_mode mode;
} membuf;

page_mode parse_pages(const char *s); /* "4k" | "2m" | "default"; -1 on error */
const char *pages_name(page_mode m);

/* mmap + madvise, no touch. Returns 0 on success. */
int mem_alloc(membuf *b, size_t bytes, page_mode mode);
void mem_free(membuf *b);

/* Write every page (first touch, places pages on the calling thread's node). */
void mem_touch(membuf *b);

/* Fraction of the resident bytes of [p, p+len) backed by AnonHugePages,
 * from /proc/self/smaps (sums all VMAs overlapping the range). Also returns
 * resident bytes in *rss if non-NULL. Returns -1 on error. */
double mem_thp_fraction(const void *p, size_t len, size_t *rss);

/* NUMA node of the page holding p (move_pages query); -1 on error. */
int mem_node_of(const void *p);
/* Fraction of `samples` evenly spaced pages of the buffer on `node`. */
double mem_node_fraction(const void *p, size_t len, int node, int samples);

/* NUMA node of a cpu, from sysfs; -1 if unknown. */
int cpu_node(int cpu);

/* Pin the calling thread to one cpu (cpu < 0: leave unpinned). 0 on success. */
int pin_self(int cpu);

#endif
