/* Validates the raw Zen 4 PMU event encodings in src/perfctr.c against
 * inline-asm loops whose retired-instruction mix is known exactly.
 *
 * Each microloop executes ITERS iterations of 8 identical target instructions
 * (independent destinations, zeroed sources -> no denormal/latency effects),
 * plus integer loop overhead that no FP/misalignment event should count.
 *
 * Exit status is non-zero if any expectation is off by more than TOL.
 * Results are informational for events we only use as estimates (memory).
 */
#define _GNU_SOURCE
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "perfctr.h"

#define ITERS 4000000L
#define TOL 0.02

#define R8(ins) ins ins ins ins ins ins ins ins

#define ZERO_SRC "vxorps %%xmm0,%%xmm0,%%xmm0\n\tvxorps %%xmm1,%%xmm1,%%xmm1\n\t"
#define CLOB "xmm0", "xmm1", "xmm2", "cc", "memory"

static void loop_mulss(long it) {
    __asm__ volatile(ZERO_SRC "1:\n\t" R8("vmulss %%xmm0,%%xmm1,%%xmm2\n\t") "dec %0\n\tjnz 1b\n\t"
                     : "+r"(it)::CLOB);
}
static void loop_mulps_x(long it) {
    __asm__ volatile(ZERO_SRC "1:\n\t" R8("vmulps %%xmm0,%%xmm1,%%xmm2\n\t") "dec %0\n\tjnz 1b\n\t"
                     : "+r"(it)::CLOB);
}
static void loop_mulps_y(long it) {
    __asm__ volatile(ZERO_SRC "1:\n\t" R8("vmulps %%ymm0,%%ymm1,%%ymm2\n\t") "dec %0\n\tjnz 1b\n\tvzeroupper\n\t"
                     : "+r"(it)::CLOB);
}
static void loop_mulps_z(long it) {
    __asm__ volatile(ZERO_SRC "1:\n\t" R8("vmulps %%zmm0,%%zmm1,%%zmm2\n\t") "dec %0\n\tjnz 1b\n\tvzeroupper\n\t"
                     : "+r"(it)::CLOB);
}
static void loop_fmaps_z(long it) {
    __asm__ volatile(ZERO_SRC "1:\n\t" R8("vfmadd231ps %%zmm0,%%zmm1,%%zmm2\n\t") "dec %0\n\tjnz 1b\n\tvzeroupper\n\t"
                     : "+r"(it)::CLOB);
}
static void loop_fmapd_y(long it) {
    __asm__ volatile(ZERO_SRC "1:\n\t" R8("vfmadd231pd %%ymm0,%%ymm1,%%ymm2\n\t") "dec %0\n\tjnz 1b\n\tvzeroupper\n\t"
                     : "+r"(it)::CLOB);
}
static void loop_addsd(long it) {
    __asm__ volatile(ZERO_SRC "1:\n\t" R8("vaddsd %%xmm0,%%xmm1,%%xmm2\n\t") "dec %0\n\tjnz 1b\n\t"
                     : "+r"(it)::CLOB);
}
static void loop_pmulld_z(long it) {
    __asm__ volatile(ZERO_SRC "1:\n\t" R8("vpmulld %%zmm0,%%zmm1,%%zmm2\n\t") "dec %0\n\tjnz 1b\n\tvzeroupper\n\t"
                     : "+r"(it)::CLOB);
}

static char *buf; /* 2 pages, 4K aligned */
static void loop_load_aligned(long it) {
    __asm__ volatile("1:\n\t" R8("vmovups (%1),%%ymm2\n\t") "dec %0\n\tjnz 1b\n\tvzeroupper\n\t"
                     : "+r"(it) : "r"(buf) : CLOB);
}
static void loop_load_cross64(long it) { /* 32B load at line offset 48 -> crosses line */
    __asm__ volatile("1:\n\t" R8("vmovups (%1),%%ymm2\n\t") "dec %0\n\tjnz 1b\n\tvzeroupper\n\t"
                     : "+r"(it) : "r"(buf + 48) : CLOB);
}
static void loop_load_cross4k(long it) { /* crosses the 4K page boundary */
    __asm__ volatile("1:\n\t" R8("vmovups (%1),%%ymm2\n\t") "dec %0\n\tjnz 1b\n\tvzeroupper\n\t"
                     : "+r"(it) : "r"(buf + 4096 - 16) : CLOB);
}

typedef struct {
    const char *loop;
    void (*fn)(long);
    const char *event;
    double per_ins; /* expected count per target instruction */
} expect_t;

static const expect_t EXPECT[] = {
    {"vmulss", loop_mulss, "fp_scalar", 1},
    {"vmulss", loop_mulss, "fp_flops", 1},
    {"vaddsd", loop_addsd, "fp_scalar", 1},
    {"vaddsd", loop_addsd, "fp_flops", 1},
    {"vmulps xmm", loop_mulps_x, "fp_p128", 1},
    {"vmulps xmm", loop_mulps_x, "fp_flops", 4},
    {"vmulps ymm", loop_mulps_y, "fp_p256", 1},
    {"vmulps ymm", loop_mulps_y, "fp_flops", 8},
    {"vmulps zmm", loop_mulps_z, "fp_p512", 1},
    {"vmulps zmm", loop_mulps_z, "fp_flops", 16},
    {"vfmadd231ps zmm", loop_fmaps_z, "fp_p512", 1},
    {"vfmadd231ps zmm", loop_fmaps_z, "fp_flops", 32},
    {"vfmadd231pd ymm", loop_fmapd_y, "fp_p256", 1},
    {"vfmadd231pd ymm", loop_fmapd_y, "fp_flops", 8},
    {"vmulps zmm", loop_mulps_z, "fp_scalar", 0},
    {"vmulss", loop_mulss, "fp_p512", 0},
    {"vmovups aligned", loop_load_aligned, "misal_64", 0},
    {"vmovups +48", loop_load_cross64, "misal_64", 1},
    {"vmovups 4K-16", loop_load_cross4k, "misal_4k", 1},
};

/* Informational only (integer SIMD is not an FP op; we just record what the
 * width counters do with it). */
static const char *INFO_SET = "cycles,instructions,fp_scalar,fp_p256,fp_p512";

static double measure(void (*fn)(long), const char *event, double *cycles_per_ins) {
    char set[128];
    snprintf(set, sizeof set, "cycles,%s", event);
    pc_group g;
    if (pc_open(&g, set) != 2) {
        pc_close(&g);
        return NAN;
    }
    fn(ITERS / 10); /* warm up */
    uint64_t v[PC_MAX];
    pc_start(&g);
    fn(ITERS);
    pc_stop(&g, v);
    pc_close(&g);
    if (cycles_per_ins) *cycles_per_ins = (double)v[0] / (8.0 * ITERS);
    return (double)v[1];
}

/* Stream through a DRAM-sized buffer touching one int per 64B line, and see
 * what the memory-traffic estimate reports vs. the true line count. */
static int mem_check(void) {
    size_t bytes = (size_t)512 << 20;
    volatile int *a = aligned_alloc(1 << 21, bytes);
    for (size_t i = 0; i < bytes / sizeof(int); i++) a[i] = (int)i;
    /* a 512 MiB init evicts most of it from the 32 MiB L3; now read it back */
    pc_group g;
    pc_open(&g, "mem");
    uint64_t v[PC_MAX] = {0};
    long sum = 0;
    pc_start(&g);
    for (size_t i = 0; i < bytes / sizeof(int); i += 16) sum += a[i];
    pc_stop(&g, v);
    double lines = (double)(bytes / 64);
    printf("\n[info] streaming read of %zu MiB (%.0f lines), sum=%ld\n", bytes >> 20, lines, sum);
    for (int i = 0; i < g.n; i++)
        printf("[info]   %-14s %14llu  (%.3f per line)\n", g.name[i], (unsigned long long)v[i],
               (double)v[i] / lines);
    /* DRAM read-traffic estimate used by the harness: prefetches that missed
     * L3 + demand fills from DRAM. Must land within [0.85, 1.25] lines/line. */
    double est = NAN;
    if (g.n == 5) est = (double)(v[3] + v[4]) / lines;
    int fail = !(est >= 0.85 && est <= 1.25);
    printf("[check] DRAM-line estimate (l2pf_miss_l3+fill_dram)/lines = %.3f  %s\n", est,
           isnan(est) ? "SKIP" : fail ? "FAIL" : "PASS");
    pc_close(&g);
    free((void *)a);
    return isnan(est) ? 0 : fail;
}

int main(void) {
    buf = aligned_alloc(4096, 2 * 4096);
    memset(buf, 0, 2 * 4096);
    int fails = 0, skipped = 0;
    printf("%-18s %-11s %14s %14s %8s %8s  %s\n", "loop", "event", "expected", "measured",
           "ratio", "cyc/ins", "status");
    for (size_t i = 0; i < sizeof EXPECT / sizeof EXPECT[0]; i++) {
        const expect_t *e = &EXPECT[i];
        double cpi = NAN;
        double got = measure(e->fn, e->event, &cpi);
        double want = e->per_ins * 8.0 * ITERS;
        const char *st;
        double ratio = want > 0 ? got / want : NAN;
        if (isnan(got)) {
            st = "SKIP (event unavailable)";
            skipped++;
        } else if (want == 0) {
            st = got <= TOL * 8.0 * ITERS ? "PASS" : "FAIL";
        } else {
            st = fabs(ratio - 1) <= TOL ? "PASS" : "FAIL";
        }
        if (!strcmp(st, "FAIL")) fails++;
        printf("%-18s %-11s %14.0f %14.0f %8.3f %8.3f  %s\n", e->loop, e->event, want, got, ratio,
               cpi, st);
    }

    /* Integer SIMD under the FP-width events (informational). */
    pc_group g;
    pc_open(&g, INFO_SET);
    uint64_t v[PC_MAX] = {0};
    pc_start(&g);
    loop_pmulld_z(ITERS);
    pc_stop(&g, v);
    printf("\n[info] vpmulld zmm x %ld:", 8 * ITERS);
    for (int i = 0; i < g.n; i++) printf(" %s=%llu", g.name[i], (unsigned long long)v[i]);
    printf("\n");
    pc_close(&g);

    fails += mem_check();
    printf("\n%d failed, %d skipped\n", fails, skipped);
    return fails ? 1 : 0;
}
