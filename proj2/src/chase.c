#include "chase.h"

#include <stdlib.h>
#include <string.h>

static const char *NAMES[] = {"random", "seq", "fakerand", "pagerand"};

int chase_parse(const char *s) {
    for (int i = 0; i < 4; i++)
        if (!strcmp(s, NAMES[i])) return i;
    return -1;
}

const char *chase_name(chase_pattern p) { return NAMES[p]; }

size_t chase_nodes(size_t bytes, size_t stride, chase_pattern p) {
    return p == CH_PAGERAND ? bytes / 4096 : bytes / stride;
}

size_t chase_offset(size_t slot, size_t stride, chase_pattern p) {
    if (p == CH_PAGERAND) return slot * 4096 + ((slot * 5) % 64) * 64;
    return slot * stride;
}

static uint64_t xorshift(uint64_t *s) {
    uint64_t x = *s;
    x ^= x >> 12;
    x ^= x << 25;
    x ^= x >> 27;
    *s = x;
    return x * 0x2545F4914F6CDD1Dull;
}

static size_t gcd(size_t a, size_t b) {
    while (b) {
        size_t t = a % b;
        a = b;
        b = t;
    }
    return a;
}

size_t chase_build(void *base, size_t bytes, size_t stride, chase_pattern p, size_t param,
                   uint64_t seed, void **start) {
    if (stride < sizeof(void *) || stride % sizeof(void *)) return 0;
    size_t n = chase_nodes(bytes, stride, p);
    if (n < 2) return 0;
    char *b = base;
#define NODE(i) ((void **)(b + chase_offset((i), stride, p)))
    if (p == CH_SEQ) {
        for (size_t i = 0; i < n; i++) *NODE(i) = NODE((i + 1) % n);
    } else if (p == CH_FAKERAND) {
        size_t step = param % n ? param % n : 1;
        while (gcd(step, n) != 1) step++;
        for (size_t i = 0; i < n; i++) *NODE(i) = NODE((i + step) % n);
    } else {
        /* Sattolo's algorithm: a uniformly random permutation with exactly
         * one cycle, so the chase visits all n nodes before repeating. */
        uint32_t *next = malloc(n * sizeof *next);
        if (!next) return 0;
        for (size_t i = 0; i < n; i++) next[i] = (uint32_t)i;
        uint64_t s = seed ? seed : 0x9E3779B97F4A7C15ull;
        for (size_t i = n - 1; i > 0; i--) {
            size_t j = xorshift(&s) % i; /* j < i: never a fixed point */
            uint32_t t = next[i];
            next[i] = next[j];
            next[j] = t;
        }
        for (size_t i = 0; i < n; i++) *NODE(i) = NODE(next[i]);
        free(next);
    }
    *start = NODE(0);
#undef NODE
    return n;
}

#define STEP p = *(void **)p;
#define STEP16 STEP STEP STEP STEP STEP STEP STEP STEP STEP STEP STEP STEP STEP STEP STEP STEP

void *chase_run(void *p, size_t steps) {
    for (size_t i = steps / 16; i; i--) {
        STEP16
    }
    __asm__ volatile("" : "+r"(p)); /* keep the result live */
    return p;
}

/* Lockstep k-chain walkers with compile-time k, fully unrolled inner loop:
 * the k loads of one round are independent, so the core can have up to k
 * misses in flight -- the memory-level-parallelism probe for Little's Law. */
#define DEF_K(K)                                                   \
    static void run_k##K(void **p, size_t steps) {                 \
        void *q[K];                                                \
        for (int j = 0; j < K; j++) q[j] = p[j];                   \
        for (size_t s = 0; s < steps; s++) {                       \
            _Pragma("GCC unroll 32") for (int j = 0; j < K; j++)   \
                q[j] = *(void **)q[j];                             \
        }                                                          \
        for (int j = 0; j < K; j++) p[j] = q[j];                   \
    }
DEF_K(1)
DEF_K(2)
DEF_K(3)
DEF_K(4)
DEF_K(5)
DEF_K(6)
DEF_K(8)
DEF_K(10)
DEF_K(12)
DEF_K(14)
DEF_K(16)
DEF_K(20)
DEF_K(24)
DEF_K(28)
DEF_K(32)

static const struct {
    int k;
    void (*fn)(void **, size_t);
} KFN[] = {{1, run_k1},   {2, run_k2},   {3, run_k3},   {4, run_k4},   {5, run_k5},
           {6, run_k6},   {8, run_k8},   {10, run_k10}, {12, run_k12}, {14, run_k14},
           {16, run_k16}, {20, run_k20}, {24, run_k24}, {28, run_k28}, {32, run_k32}};

int chase_k_supported(int k) {
    for (size_t i = 0; i < sizeof KFN / sizeof KFN[0]; i++)
        if (KFN[i].k == k) return 1;
    return 0;
}

int chase_run_k(void **p, int k, size_t steps) {
    for (size_t i = 0; i < sizeof KFN / sizeof KFN[0]; i++)
        if (KFN[i].k == k) {
            KFN[i].fn(p, steps);
            return 0;
        }
    return -1;
}

void chase_starts(void *start, size_t n, int k, void **out) {
    void *p = start;
    int j = 0;
    for (size_t i = 0; i < n && j < k; i++) {
        if (i == (size_t)j * (n / (size_t)k)) out[j++] = p;
        p = *(void **)p;
    }
}
