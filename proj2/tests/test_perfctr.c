/* Validates the Zen 4 PMU events used by proj2 against probes whose miss
 * behaviour is known by construction (and, in turn, that the probes really
 * live in the level they claim):
 *
 *   random 64 B chase, 2 MiB pages, fits in level X  -> ~1 fill from X per load
 *       (minus the ~capacity/WS fraction still hitting the level above)
 *   PAGERAND chase over P 4 KiB pages                -> L1 DTLB miss per load
 *       when P > 72 entries; L2 DTLB miss per load when P > 3072 entries;
 *       ~0 of both with 2 MiB pages (P*4 KiB fits in a few huge pages)
 *   known-length integer loop                        -> instructions
 *   512 MiB streaming read                           -> DRAM-line estimate
 *
 * Expectations are per load; "~1" passes within [0.90, 1.10] and "~0" below
 * 0.05 (memory events include a little noise from the timer interrupt and
 * page-walk loads). Exit status = number of failures.
 */
#define _GNU_SOURCE
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "chase.h"
#include "mem.h"
#include "perfctr.h"

static int fails, skipped;

static void report(const char *test, const char *ev, double want, double got, double lo,
                   double hi) {
    const char *st;
    if (isnan(got)) {
        st = "SKIP (event unavailable)";
        skipped++;
    } else if (got >= lo && got <= hi) {
        st = "PASS";
    } else {
        st = "FAIL";
        fails++;
    }
    printf("%-34s %-14s %9.3f %9.3f   [%.2f, %.2f]  %s\n", test, ev, want, got, lo, hi, st);
}

/* Measure `events` (<=4 plus cycles) over a chase of `steps` loads. */
static int chase_counts(void *start, size_t n, size_t steps, const char *events, double *per,
                        int maxev) {
    char set[256];
    snprintf(set, sizeof set, "cycles,%s", events);
    pc_group g;
    int opened = pc_open(&g, set);
    void *p = chase_run(start, (2 * n < (4u << 20) ? 2 * n : (4u << 20)) + 16); /* warm */
    uint64_t v[PC_MAX] = {0};
    pc_start(&g);
    p = chase_run(p, steps);
    pc_stop(&g, v);
    for (int i = 0; i < maxev; i++) per[i] = i + 1 < opened ? (double)v[i + 1] / (double)steps : NAN;
    if (opened && g.mux < 0.99) printf("[warn] multiplexed (%.2f)\n", g.mux);
    printf("[info] %.1f cycles/load\n", (double)v[0] / (double)steps);
    pc_close(&g);
    return p != NULL;
}

static void level_case(const char *name, size_t bytes, int level) {
    membuf b;
    mem_alloc(&b, bytes, PAGES_2M);
    mem_touch(&b);
    void *start;
    size_t n = chase_build(b.base, bytes, 64, CH_RANDOM, 0, 11, &start);
    double per[4];
    size_t steps = 4u << 20;
    chase_counts(start, n, steps, "l1d_miss,fill_l2,fill_ccx,fill_dram", per, 4);
    char t[64];
    snprintf(t, sizeof t, "chase %s (%zu KiB)", name, bytes >> 10);
    const double ONE_LO = 0.90, ONE_HI = 1.10, ZERO_HI = 0.05;
    report(t, "l1d_miss", level ? 1 : 0, per[0], level ? ONE_LO : 0, level ? ONE_HI : ZERO_HI);
    /* A random set larger than a level still hits it with probability
     * ~capacity/WS (the L2 keeps 1 MiB of a 16 MiB set): widen "~0" bands. */
    double l2res = (double)(1 << 20) / (double)bytes;
    report(t, "fill_l2", level == 1, per[1], level == 1 ? ONE_LO : 0,
           level == 1 ? ONE_HI : ZERO_HI + (level > 1 ? l2res : 0));
    report(t, "fill_ccx", level == 2, per[2], level == 2 ? ONE_LO - l2res : 0,
           level == 2 ? ONE_HI : ZERO_HI);
    report(t, "fill_dram", level == 3, per[3], level == 3 ? ONE_LO : 0, level == 3 ? ONE_HI : ZERO_HI);
    if (level >= 1) {
        chase_counts(start, n, steps, "l2_miss_dc", per, 1);
        report(t, "l2_miss_dc", level >= 2, per[0], level >= 2 ? ONE_LO : 0,
               level >= 2 ? ONE_HI : ZERO_HI);
    }
    mem_free(&b);
}

static void tlb_case(size_t pages, page_mode mode, double want_l1, double want_l2) {
    size_t bytes = pages * 4096;
    membuf b;
    mem_alloc(&b, bytes, mode);
    mem_touch(&b);
    void *start;
    size_t n = chase_build(b.base, bytes, 64, CH_PAGERAND, 0, 13, &start);
    double per[2];
    chase_counts(start, n, 4u << 20, "dtlb_miss,dtlb_l2_miss", per, 2);
    char t[64];
    snprintf(t, sizeof t, "pagerand %zu x 4K, %s pages", pages, pages_name(mode));
    report(t, "dtlb_miss", want_l1, per[0], want_l1 ? 0.90 : 0, want_l1 ? 1.10 : 0.05);
    report(t, "dtlb_l2_miss", want_l2, per[1], want_l2 ? 0.90 : 0, want_l2 ? 1.10 : 0.05);
    mem_free(&b);
}

#define ITERS 10000000L
static void instr_case(void) {
    pc_group g;
    pc_open(&g, "clock");
    uint64_t v[PC_MAX] = {0};
    long it = ITERS;
    pc_start(&g);
    /* 8 adds + dec + jnz = 10 instructions per iteration */
    __asm__ volatile("1:\n\t"
                     "add $1,%%rax\n\tadd $1,%%rbx\n\tadd $1,%%rcx\n\tadd $1,%%rdx\n\t"
                     "add $1,%%rax\n\tadd $1,%%rbx\n\tadd $1,%%rcx\n\tadd $1,%%rdx\n\t"
                     "dec %0\n\tjnz 1b\n\t"
                     : "+r"(it)::"rax", "rbx", "rcx", "rdx", "cc");
    pc_stop(&g, v);
    double per = g.n == 2 ? (double)v[1] / (10.0 * ITERS) : NAN;
    report("10-instruction loop", "instructions", 1, per, 0.99, 1.01);
    printf("[info] clock: %.3f instructions/cycle\n", (double)v[1] / (double)v[0]);
    pc_close(&g);
}

static void stream_case(void) {
    size_t bytes = (size_t)512 << 20;
    membuf b;
    mem_alloc(&b, bytes, PAGES_2M);
    volatile int *a = b.base;
    for (size_t i = 0; i < bytes / sizeof(int); i++) a[i] = (int)i;
    pc_group g;
    pc_open(&g, "mem");
    uint64_t v[PC_MAX] = {0};
    long sum = 0;
    pc_start(&g);
    for (size_t i = 0; i < bytes / sizeof(int); i += 16) sum += a[i];
    pc_stop(&g, v);
    double lines = (double)(bytes / 64);
    printf("[info] streaming read %zu MiB, sum=%ld:", bytes >> 20, sum);
    for (int i = 0; i < g.n; i++) printf(" %s=%.3f/line", g.name[i], (double)v[i] / lines);
    printf("\n");
    double est = g.n == 5 ? (double)(v[3] + v[4]) / lines : NAN;
    report("stream read 512 MiB", "pf_miss_l3+fill_dram", 1, est, 0.85, 1.25);
    pc_close(&g);
    mem_free(&b);
}

int main(void) {
    printf("%-34s %-14s %9s %9s   %-12s  %s\n", "test", "event", "expect", "per-load", "band",
           "status");
    instr_case();
    level_case("L1", 16 << 10, 0);
    level_case("L2", 512 << 10, 1);
    level_case("L3", 16 << 20, 2);
    level_case("DRAM", (size_t)1 << 30, 3);
    tlb_case(48, PAGES_4K, 0, 0);        /* < 72 L1 DTLB entries */
    tlb_case(1024, PAGES_4K, 1, 0);      /* > 72, < 3072 L2 DTLB entries */
    tlb_case(16384, PAGES_4K, 1, 1);     /* > 3072 */
    tlb_case(16384, PAGES_2M, 0, 0);     /* 64 MiB = 32 huge pages */
    stream_case();
    printf("\n%d failed, %d skipped\n", fails, skipped);
    return fails;
}
