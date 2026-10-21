/* Unit tests: the probes do what they claim.
 *   chase   - every pattern is ONE cycle over all nodes; random is not
 *             sequential; fakerand is a constant stride; k-chain starts are
 *             evenly spaced and k-chain walking == k single walks
 *   bw      - reads touch exactly the intended gran-byte blocks (checksum vs
 *             reference), writes modify exactly those bytes and nothing else,
 *             R:W mixes write exactly the w-of-(r+w) accesses, delay and
 *             order don't change which bytes are touched
 *   mem     - MADV_HUGEPAGE buffers are THP-backed, MADV_NOHUGEPAGE are not;
 *             first touch places pages on the toucher's node
 *   kernels - mul/axpy contig/stride/gather match a scalar reference
 * Exit status = number of failed checks.
 */
#define _GNU_SOURCE
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "bw.h"
#include "chase.h"
#include "kernels.h"
#include "mem.h"

static int fails, checks;
#define CHECK(cond, ...)                                  \
    do {                                                  \
        checks++;                                         \
        if (!(cond)) {                                    \
            fails++;                                      \
            printf("FAIL %s:%d: ", __FILE__, __LINE__);   \
            printf(__VA_ARGS__);                          \
            printf("\n");                                 \
        }                                                 \
    } while (0)

static uint64_t rng = 88172645463325252ull;
static uint64_t rnd(void) {
    rng ^= rng << 13;
    rng ^= rng >> 7;
    rng ^= rng << 17;
    return rng;
}

/* ------------------------------------------------------------------ chase */
static size_t slot_of(const char *base, const void *p, size_t stride, chase_pattern pat, int *ok) {
    size_t off = (size_t)((const char *)p - base);
    size_t slot = pat == CH_PAGERAND ? off / 4096 : off / stride;
    *ok = chase_offset(slot, stride, pat) == off;
    return slot;
}

static void test_chase_cycle(chase_pattern pat, size_t bytes, size_t stride, size_t param) {
    membuf b;
    mem_alloc(&b, bytes, PAGES_DEFAULT);
    void *start;
    size_t n = chase_build(b.base, bytes, stride, pat, param, 7, &start);
    CHECK(n == chase_nodes(bytes, stride, pat), "%s n=%zu", chase_name(pat), n);
    uint8_t *seen = calloc(n, 1);
    void *p = start;
    int okall = 1;
    size_t seq_steps = 0, prev = 0;
    long long delta0 = 0;
    int const_delta = 1;
    for (size_t i = 0; i < n; i++) {
        int ok;
        size_t s = slot_of(b.base, p, stride, pat, &ok);
        okall &= ok && s < n && !seen[s];
        if (s < n) seen[s] = 1;
        if (i) {
            seq_steps += s == (prev + 1) % n;
            long long d = ((long long)s - (long long)prev + (long long)n) % (long long)n;
            if (i == 1) delta0 = d;
            const_delta &= d == delta0;
        }
        prev = s;
        p = *(void **)p;
    }
    CHECK(okall, "%s stride %zu: visits a node twice or off-grid", chase_name(pat), stride);
    CHECK(p == start, "%s stride %zu: not a single n-cycle", chase_name(pat), stride);
    if ((pat == CH_RANDOM || pat == CH_PAGERAND) && n >= 100) /* tiny cycles are trivially sequential */
        CHECK(seq_steps < n / 100 + 2, "%s: %zu of %zu steps sequential", chase_name(pat),
              seq_steps, n);
    if (pat == CH_SEQ) CHECK(seq_steps == n - 1, "seq not sequential");
    if (pat == CH_FAKERAND) CHECK(const_delta && delta0 > 1, "fakerand not a constant stride");
    free(seen);
    mem_free(&b);
}

static void test_chase_k(void) {
    membuf b;
    size_t bytes = 1 << 20;
    mem_alloc(&b, bytes, PAGES_DEFAULT);
    void *start;
    size_t n = chase_build(b.base, bytes, 64, CH_RANDOM, 0, 3, &start);
    int ks[] = {2, 3, 8, 12, 32};
    for (size_t t = 0; t < sizeof ks / sizeof ks[0]; t++) {
        int k = ks[t];
        void *p[CHASE_K_MAX], *ref[CHASE_K_MAX];
        chase_starts(start, n, k, p);
        /* starts are n/k apart along the cycle */
        int spaced = 1;
        for (int j = 0; j < k; j++) {
            void *q = p[j];
            for (size_t s = 0; s < n / (size_t)k; s++) q = *(void **)q;
            spaced &= q == p[(j + 1) % k] || j == k - 1;
            ref[j] = p[j];
        }
        CHECK(spaced, "k=%d starts not evenly spaced", k);
        size_t steps = 1000;
        CHECK(chase_run_k(p, k, steps) == 0, "k=%d unsupported", k);
        int same = 1;
        for (int j = 0; j < k; j++) {
            void *q = ref[j];
            for (size_t s = 0; s < steps; s++) q = *(void **)q;
            same &= q == p[j];
        }
        CHECK(same, "k=%d lockstep walk != independent walks", k);
    }
    void *q = chase_run(start, 160);
    void *r = start;
    for (int s = 0; s < 160; s++) r = *(void **)r;
    CHECK(q == r, "chase_run(160) != 160 steps");
    CHECK(chase_k_supported(7) == 0 && chase_run_k(&q, 7, 1) == -1, "k=7 should be rejected");
    mem_free(&b);
}

/* --------------------------------------------------------------------- bw */
static size_t off_of(const bw_cfg *c, size_t i) {
    size_t s = i % c->nslots;
    return (c->idx ? c->idx[s] : s) * c->stride;
}

static uint64_t ref_read(const bw_cfg *c, const char *base, size_t first, size_t count) {
    uint64_t lanes[8] = {0};
    int nl = c->gran / 8;
    for (size_t i = first; i < first + count; i++) {
        if ((int)(i % (size_t)(c->r + c->w)) >= c->r) continue;
        const uint64_t *p = (const uint64_t *)(base + off_of(c, i));
        for (int l = 0; l < nl; l++) lanes[l] ^= p[l];
    }
    uint64_t s = 0;
    for (int l = 0; l < nl; l++) s += lanes[l];
    return s;
}

static void test_bw_one(int gran, size_t stride, int r, int w, int nt, int random, int delay) {
    size_t bytes = 256 << 10;
    membuf b;
    mem_alloc(&b, bytes, PAGES_DEFAULT);
    char *base = b.base;
    bw_cfg c = {.gran = gran, .stride = stride, .r = r, .w = w, .nt = nt, .delay = delay,
                .nslots = bytes / stride};
    CHECK(bw_check(&c) == NULL, "config rejected");
    uint32_t *idx = NULL;
    if (random) {
        idx = malloc(c.nslots * sizeof *idx);
        bw_perm(idx, c.nslots, 5);
        c.idx = idx;
    }
    for (size_t i = 0; i < bytes / 8; i++) ((uint64_t *)base)[i] = rnd();
    char *orig = malloc(bytes);
    memcpy(orig, base, bytes);
    size_t first = 3, count = c.nslots - 7; /* single segment, odd phase */
    uint64_t want = ref_read(&c, orig, first, count);
    uint64_t got = bw_run(&c, base, first, count);
    if (r > 0 && w == 0) CHECK(got == want, "read gran %d stride %zu %s: checksum mismatch", gran,
                               stride, random ? "random" : "seq");
    /* which bytes must have been written */
    char *expect = malloc(bytes);
    memcpy(expect, orig, bytes);
    size_t nwrites = 0;
    for (size_t i = first; i < first + count; i++)
        if ((int)(i % (size_t)(r + w)) >= r) {
            memset(expect + off_of(&c, i), 0xA5, (size_t)gran);
            nwrites++;
        }
    size_t bad = 0;
    for (size_t i = 0; i < bytes; i++) bad += base[i] != expect[i];
    CHECK(bad == 0, "gran %d stride %zu rw %d:%d nt %d %s delay %d: %zu bytes differ", gran, stride,
          r, w, nt, random ? "random" : "seq", delay, bad);
    if (r && w) {
        /* exact ratio: writes == w/(r+w) of accesses (up to one period) */
        double frac = (double)nwrites / (double)count;
        CHECK(frac > (double)w / (r + w) - 0.01 && frac < (double)w / (r + w) + 0.01,
              "mix %d:%d write fraction %.3f", r, w, frac);
    }
    free(expect);
    free(orig);
    free(idx);
    mem_free(&b);
}

static void test_bw(void) {
    int grans[] = {8, 16, 32, 64};
    size_t strides[] = {64, 256, 1024};
    for (int g = 0; g < 4; g++) {
        test_bw_one(grans[g], (size_t)grans[g], 1, 0, 0, 0, 0); /* dense */
        for (int s = 0; s < 3; s++) {
            test_bw_one(grans[g], strides[s], 1, 0, 0, 0, 0);
            test_bw_one(grans[g], strides[s], 1, 0, 0, 1, 0);
            test_bw_one(grans[g], strides[s], 0, 1, 0, 0, 0);
            test_bw_one(grans[g], strides[s], 0, 1, 1, 1, 0);
        }
    }
    test_bw_one(64, 64, 7, 3, 0, 0, 0);
    test_bw_one(64, 64, 1, 1, 0, 0, 0);
    test_bw_one(64, 64, 7, 3, 1, 1, 0);
    test_bw_one(64, 64, 1, 1, 0, 1, 5);
    test_bw_one(64, 256, 0, 1, 0, 0, 20);
    test_bw_one(64, 64, 1, 0, 0, 1, 3);

    /* wrap-around: count > nslots revisits from slot 0 */
    bw_cfg c = {.gran = 64, .stride = 64, .r = 0, .w = 1, .nslots = 16};
    char *buf = aligned_alloc(64, 16 * 64);
    memset(buf, 0, 16 * 64);
    bw_run(&c, buf, 10, 20);
    size_t written = 0;
    for (int i = 0; i < 16 * 64; i++) written += (unsigned char)buf[i] == 0xA5;
    CHECK(written == 16 * 64, "wrap-around write covered %zu bytes", written);
    free(buf);

    uint32_t perm[1000];
    bw_perm(perm, 1000, 9);
    uint8_t seen[1000] = {0};
    int ok = 1;
    for (int i = 0; i < 1000; i++) {
        ok &= perm[i] < 1000 && !seen[perm[i]];
        seen[perm[i] < 1000 ? perm[i] : 0] = 1;
    }
    CHECK(ok, "bw_perm not a permutation");
    bw_cfg badc = {.gran = 64, .stride = 96, .r = 1, .nslots = 4};
    CHECK(bw_check(&badc) != NULL, "stride 96 with gran 64 should be rejected");
}

/* -------------------------------------------------------------------- mem */
static void test_mem(void) {
    size_t bytes = 64 << 20;
    membuf h, s;
    CHECK(mem_alloc(&h, bytes, PAGES_2M) == 0, "alloc 2m");
    CHECK(mem_alloc(&s, bytes, PAGES_4K) == 0, "alloc 4k");
    CHECK(((uintptr_t)h.base & ((2 << 20) - 1)) == 0, "2 MiB alignment");
    mem_touch(&h);
    mem_touch(&s);
    size_t rss;
    double fh = mem_thp_fraction(h.base, bytes, &rss);
    CHECK(fh >= 0.9 && rss >= bytes, "MADV_HUGEPAGE buffer THP fraction %.3f rss %zu", fh, rss);
    double fs = mem_thp_fraction(s.base, bytes, &rss);
    CHECK(fs == 0.0 && rss >= bytes, "MADV_NOHUGEPAGE buffer THP fraction %.3f", fs);
    int node = cpu_node(sched_getcpu());
    double nf = mem_node_fraction(h.base, bytes, node, 64);
    CHECK(node >= 0 && nf == 1.0, "first touch on node %d: fraction %.2f", node, nf);
    printf("[info] 2m buffer thp=%.3f, 4k buffer thp=%.3f, first-touch node %d frac %.2f\n", fh,
           fs, node, nf);
    mem_free(&h);
    mem_free(&s);
    CHECK(parse_pages("4k") == PAGES_4K && parse_pages("2m") == PAGES_2M &&
              (int)parse_pages("1g") == -1, "parse_pages");
}

/* ---------------------------------------------------------------- kernels */
static void test_kernels(void) {
    size_t n = 1003, s = 9;
    double *x = malloc(n * s * 8), *y = malloc(n * s * 8), *z = malloc(n * s * 8),
           *y0 = malloc(n * s * 8);
    uint32_t *idx = malloc(n * 4);
    bw_perm(idx, n, 1);
    for (size_t i = 0; i < n * s; i++) {
        x[i] = (double)(rnd() % 1000) / 7.0;
        y0[i] = y[i] = (double)(rnd() % 1000) / 3.0;
        z[i] = -1;
    }
    int bad = 0;
    mul_contig(n, x, y, z);
    for (size_t i = 0; i < n; i++) bad += z[i] != x[i] * y[i];
    for (size_t i = 0; i < n * s; i++) z[i] = -1;
    mul_stride(n, s, x, y, z);
    for (size_t i = 0; i < n * s; i++) bad += z[i] != (i % s ? -1 : x[i] * y[i]);
    for (size_t i = 0; i < n; i++) z[i] = -1;
    mul_gather(n, idx, x, y, z);
    for (size_t i = 0; i < n; i++) bad += z[i] != x[i] * y[i];
    CHECK(bad == 0, "mul kernels: %d wrong elements", bad);
    bad = 0;
    axpy_contig(n, 2.0, x, y);
    for (size_t i = 0; i < n; i++) bad += y[i] != 2.0 * x[i] + y0[i];
    memcpy(y, y0, n * s * 8);
    axpy_stride(n, s, 2.0, x, y);
    for (size_t i = 0; i < n * s; i++) bad += y[i] != (i % s ? y0[i] : 2.0 * x[i] + y0[i]);
    memcpy(y, y0, n * s * 8);
    axpy_gather(n, idx, 2.0, x, y);
    for (size_t i = 0; i < n; i++) bad += y[i] != 2.0 * x[i] + y0[i];
    CHECK(bad == 0, "axpy kernels: %d wrong elements", bad);
    free(x);
    free(y);
    free(z);
    free(y0);
    free(idx);
}

int main(void) {
    size_t strides[] = {8, 64, 256, 1024};
    for (int i = 0; i < 4; i++) {
        test_chase_cycle(CH_RANDOM, 1 << 20, strides[i], 0);
        test_chase_cycle(CH_SEQ, 1 << 20, strides[i], 0);
    }
    test_chase_cycle(CH_FAKERAND, 1 << 20, 64, 3);
    test_chase_cycle(CH_FAKERAND, 64000, 64, 10); /* n=1000: 10 bumped to 11 */
    test_chase_cycle(CH_PAGERAND, 8 << 20, 64, 0);
    test_chase_cycle(CH_RANDOM, 3 * 64, 64, 0); /* tiny */
    test_chase_k();
    test_bw();
    test_mem();
    test_kernels();
    printf("%d checks, %d failed\n", checks, fails);
    return fails ? 1 : 0;
}
