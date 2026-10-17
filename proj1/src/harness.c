/* Benchmark driver. Linked against one (compiler, config) build of the
 * kernels; this file itself is compiled with fixed flags.
 *
 *   bench --kernel mul --dtype f32 --n 1024,4096 [--offset 4 | --offset-x/-y/-z B]
 *         [--pattern identity|stride|random --gstride S]
 *         [--samples 15 --warmup 1 --min-ms 2] [--counters core|width|mem|align|none]
 *         [--serialize] [--header]
 *
 * One CSV row per timed sample on stdout. A sample = `reps` back-to-back
 * calls, with reps chosen so a sample lasts >= min-ms. Counters bracket
 * exactly the same region as the timer.
 */
#define _GNU_SOURCE
#include <getopt.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "kernels.h"
#include "perfctr.h"
#include "timing.h"

/* All event columns the CSV can carry, in a fixed order. */
static const char *EVCOLS[] = {"cycles",   "instructions", "fp_flops",    "fp_scalar",
                               "fp_p128",  "fp_p256",      "fp_p512",     "l1d_miss",
                               "l2pf_hit_l3", "l2pf_miss_l3", "fill_dram", "misal_64",
                               "misal_4k"};
#define NEVCOLS (int)(sizeof EVCOLS / sizeof EVCOLS[0])

typedef enum { DOT, MUL, STENCIL, DOTG, MULG, BW_READ, BW_WRITE, BW_SCALE, BW_TRIAD } kind_t;
typedef enum { DT_F32, DT_F64, DT_I32 } dtype_t;
static const size_t DT_SIZE[] = {4, 8, 4};
static const char *DT_NAME[] = {"f32", "f64", "i32"};

typedef struct {
    const char *name;
    kind_t kind;
    size_t stride; /* input span multiplier for compile-time-strided kernels */
    void *fn[3];   /* per dtype */
} kernel_t;

#define KROW(nm, kind, stride) {#nm, kind, stride, {(void *)nm##_f32, (void *)nm##_f64, (void *)nm##_i32}}
static const kernel_t KERNELS[] = {
    KROW(dot_simd, DOT, 1),    KROW(dot_plain, DOT, 1),     KROW(dot_acc2, DOT, 1),
    KROW(dot_acc4, DOT, 1),    KROW(dot_acc8, DOT, 1),      KROW(mul, MUL, 1),
    KROW(mul_aligned, MUL, 1), KROW(mul_pred, MUL, 1),      KROW(stencil, STENCIL, 1),
    KROW(dot_s2, DOT, 2),      KROW(dot_s4, DOT, 4),        KROW(dot_s8, DOT, 8),
    KROW(mul_s2, MUL, 2),      KROW(mul_s4, MUL, 4),        KROW(mul_s8, MUL, 8),
    KROW(dot_gather, DOTG, 1), KROW(mul_gather, MULG, 1),   KROW(bw_read, BW_READ, 1),
    KROW(bw_write, BW_WRITE, 1), KROW(bw_scale, BW_SCALE, 1), KROW(bw_triad, BW_TRIAD, 1),
};

typedef struct {
    const kernel_t *k;
    dtype_t dt;
    size_t n;
    void *x, *y, *z;
    int32_t *idx;
} call_t;

static volatile double sink;

/* One kernel invocation, dispatched on (kind, dtype). */
#define DISPATCH(T, S)                                                                           \
    case DT_##S: {                                                                               \
        const T *x = c->x, *y = c->y;                                                            \
        T *z = c->z;                                                                             \
        switch (c->k->kind) {                                                                    \
        case DOT: sink += ((T(*)(const T *, const T *, size_t))fn)(x, y, c->n); break;           \
        case MUL: ((void (*)(T *, const T *, const T *, size_t))fn)(z, x, y, c->n); break;       \
        case STENCIL:                                                                            \
            ((void (*)(T *, const T *, size_t, T, T, T))fn)(z, x, c->n, (T)1, (T)2, (T)1);       \
            break;                                                                               \
        case DOTG:                                                                               \
            sink += ((T(*)(const T *, const int32_t *, const T *, size_t))fn)(x, c->idx, y, c->n); \
            break;                                                                               \
        case MULG:                                                                               \
            ((void (*)(T *, const T *, const int32_t *, const T *, size_t))fn)(z, x, c->idx, y,  \
                                                                              c->n);             \
            break;                                                                               \
        case BW_READ: sink += ((T(*)(const T *, size_t))fn)(x, c->n); break;                     \
        case BW_WRITE: ((void (*)(T *, size_t, T))fn)(z, c->n, (T)1); break;                     \
        case BW_SCALE: ((void (*)(T *, const T *, size_t, T))fn)(z, x, c->n, (T)1); break;       \
        case BW_TRIAD:                                                                           \
            ((void (*)(T *, const T *, const T *, size_t, T))fn)(z, x, y, c->n, (T)1);           \
            break;                                                                               \
        }                                                                                        \
    } break;

static void call_once(const call_t *c) {
    void *fn = c->k->fn[c->dt];
    switch (c->dt) {
        DISPATCH(f32, F32)
        DISPATCH(f64, F64)
        DISPATCH(i32, I32)
    }
}

/* --serialize: lfence between calls (dispatch-serializing on this kernel's
 * AMD config), so one call's dependency chain cannot overlap the next call's
 * in the out-of-order window. Off by default: back-to-back calls DO overlap on
 * real hardware, which lets short L1-resident reductions beat their
 * single-chain latency bound. */
static int serialize;
static void run_reps(const call_t *c, long reps) {
    if (serialize)
        for (long r = 0; r < reps; r++) { call_once(c); _mm_lfence(); }
    else
        for (long r = 0; r < reps; r++) call_once(c);
}

/* ---- allocation: 2 MiB-aligned base + per-array stagger + user offset ----
 * Stagger (k * 1088 B, a multiple of 64) keeps arrays from sharing the same
 * low 12 address bits (4K aliasing between loads and stores) without changing
 * their alignment class, so --offset alone controls misalignment. */
#define BASE_ALIGN ((size_t)2 << 20)
static void *alloc_arr(size_t bytes, int k, size_t off, void **base) {
    size_t pad = (size_t)k * 1088 + off;
    if (posix_memalign(base, BASE_ALIGN, bytes + pad + 4096)) {
        fprintf(stderr, "bench: out of memory (%zu bytes)\n", bytes);
        exit(1);
    }
    return (char *)*base + pad;
}

static uint64_t rng = 0x9E3779B97F4A7C15ull;
static uint64_t next_rand(void) { /* xorshift64* */
    rng ^= rng >> 12; rng ^= rng << 25; rng ^= rng >> 27;
    return rng * 2685821657736338717ull;
}

static void fill(void *p, dtype_t dt, size_t n) {
    for (size_t i = 0; i < n; i++) {
        double u = (double)(next_rand() >> 11) / 9007199254740992.0;
        switch (dt) {
        case DT_F32: ((float *)p)[i] = (float)(0.5 + u); break;
        case DT_F64: ((double *)p)[i] = 0.5 + u; break;
        case DT_I32: ((int32_t *)p)[i] = (int32_t)(u * 5) - 2; break;
        }
    }
}

typedef struct {
    const char *kernel, *dtype, *counters, *pattern;
    size_t offx, offy, offz, gstride;
    int samples, warmup, header;
    double min_ms;
    char *nlist;
} opts_t;

static void usage(void) {
    fprintf(stderr, "usage: bench --kernel K --dtype f32|f64|i32 --n N[,N...] [options]\n"
                    "  kernels:");
    for (size_t i = 0; i < sizeof KERNELS / sizeof KERNELS[0]; i++)
        fprintf(stderr, " %s", KERNELS[i].name);
    fprintf(stderr, "\n");
    exit(2);
}

static void print_header(void) {
    printf("kernel,dtype,n,span,offset_x,offset_y,offset_z,pattern,gstride,reps,sample,ns,tsc,mux");
    for (int i = 0; i < NEVCOLS; i++) printf(",%s", EVCOLS[i]);
    printf("\n");
}

static void bench_one(const opts_t *o, const kernel_t *k, dtype_t dt, size_t n, pc_group *g) {
    size_t es = DT_SIZE[dt];
    size_t span = n * k->stride; /* input elements actually addressed */
    int pattern_stride = 0;
    if (k->kind == DOTG || k->kind == MULG) {
        pattern_stride = !strcmp(o->pattern, "stride");
        span = pattern_stride ? n * o->gstride : n;
    }
    void *bx = NULL, *by = NULL, *bz = NULL, *bi = NULL;
    call_t c = {k, dt, n, NULL, NULL, NULL, NULL};
    int need_x = k->kind != BW_WRITE;
    int need_y = k->kind == DOT || k->kind == MUL || k->kind == DOTG || k->kind == MULG ||
                 k->kind == BW_TRIAD;
    int need_z = k->kind == MUL || k->kind == STENCIL || k->kind == MULG || k->kind >= BW_WRITE;
    size_t ylen = (k->kind == DOTG || k->kind == MULG) ? n : span;
    if (k->kind == STENCIL || k->kind >= BW_READ) span = n;
    if (need_x) { c.x = alloc_arr(span * es, 0, o->offx, &bx); fill(c.x, dt, span); }
    if (need_y) { c.y = alloc_arr(ylen * es, 1, o->offy, &by); fill(c.y, dt, ylen); }
    if (need_z) { c.z = alloc_arr(n * es, 2, o->offz, &bz); memset(c.z, 0, n * es); }
    if (k->kind == DOTG || k->kind == MULG) {
        c.idx = alloc_arr(n * sizeof(int32_t), 3, 0, &bi);
        for (size_t i = 0; i < n; i++)
            c.idx[i] = (int32_t)(pattern_stride ? i * o->gstride : i);
        if (!strcmp(o->pattern, "random"))
            for (size_t i = n; i > 1; i--) {
                size_t j = next_rand() % i;
                int32_t t = c.idx[i - 1]; c.idx[i - 1] = c.idx[j]; c.idx[j] = t;
            }
    }

    /* calibrate reps so a sample lasts >= min_ms */
    run_reps(&c, 1);
    uint64_t t0 = now_ns();
    run_reps(&c, 1);
    double one = (double)(now_ns() - t0);
    long reps = (long)ceil(o->min_ms * 1e6 / (one > 1 ? one : 1));
    if (reps < 1) reps = 1;
    if (reps > 50000000) reps = 50000000;
    for (int w = 0; w < o->warmup; w++) run_reps(&c, reps);

    uint64_t ev[PC_MAX];
    for (int s = 0; s < o->samples; s++) {
        memset(ev, 0, sizeof ev);
        pc_start(g);
        uint64_t c0 = tsc(), a = now_ns();
        run_reps(&c, reps);
        uint64_t b = now_ns(), c1 = tsc();
        pc_stop(g, ev);
        printf("%s,%s,%zu,%zu,%zu,%zu,%zu,%s,%zu,%ld,%d,%llu,%llu,%.4f", k->name, DT_NAME[dt], n,
               span, need_x ? o->offx : 0, need_y ? o->offy : 0, need_z ? o->offz : 0,
               (k->kind == DOTG || k->kind == MULG) ? o->pattern : "-",
               (k->kind == DOTG || k->kind == MULG) && pattern_stride ? o->gstride : 0, reps, s,
               (unsigned long long)(b - a), (unsigned long long)(c1 - c0), g->n ? g->mux : 1.0);
        for (int col = 0; col < NEVCOLS; col++) {
            int hit = -1;
            for (int e = 0; e < g->n; e++)
                if (!strcmp(g->name[e], EVCOLS[col])) hit = e;
            if (hit >= 0) printf(",%llu", (unsigned long long)ev[hit]);
            else printf(",");
        }
        printf("\n");
    }
    fflush(stdout);
    free(bx); free(by); free(bz); free(bi);
}

int main(int argc, char **argv) {
    opts_t o = {NULL, "f32", "core", "identity", 0, 0, 0, 1, 15, 1, 0, 2.0, NULL};
    enum { O_OFFX = 256, O_OFFY, O_OFFZ, O_GSTRIDE, O_HEADER, O_MINMS, O_SERIAL };
    static struct option lo[] = {
        {"kernel", 1, 0, 'k'},       {"dtype", 1, 0, 'd'},        {"n", 1, 0, 'n'},
        {"offset", 1, 0, 'o'},       {"offset-x", 1, 0, O_OFFX},  {"offset-y", 1, 0, O_OFFY},
        {"offset-z", 1, 0, O_OFFZ},  {"pattern", 1, 0, 'p'},      {"gstride", 1, 0, O_GSTRIDE},
        {"samples", 1, 0, 's'},      {"warmup", 1, 0, 'w'},       {"min-ms", 1, 0, O_MINMS},
        {"counters", 1, 0, 'c'},     {"header", 0, 0, O_HEADER},  {"serialize", 0, 0, O_SERIAL},
        {0, 0, 0, 0}};
    int ch;
    while ((ch = getopt_long(argc, argv, "", lo, NULL)) != -1) {
        switch (ch) {
        case 'k': o.kernel = optarg; break;
        case 'd': o.dtype = optarg; break;
        case 'n': o.nlist = optarg; break;
        case 'o': o.offx = o.offy = o.offz = strtoul(optarg, 0, 0); break;
        case O_OFFX: o.offx = strtoul(optarg, 0, 0); break;
        case O_OFFY: o.offy = strtoul(optarg, 0, 0); break;
        case O_OFFZ: o.offz = strtoul(optarg, 0, 0); break;
        case 'p': o.pattern = optarg; break;
        case O_GSTRIDE: o.gstride = strtoul(optarg, 0, 0); break;
        case 's': o.samples = atoi(optarg); break;
        case 'w': o.warmup = atoi(optarg); break;
        case O_MINMS: o.min_ms = atof(optarg); break;
        case 'c': o.counters = optarg; break;
        case O_HEADER: o.header = 1; break;
        case O_SERIAL: serialize = 1; break;
        default: usage();
        }
    }
    if (o.header && !o.kernel) { print_header(); return 0; }
    if (!o.kernel || !o.nlist) usage();

    const kernel_t *k = NULL;
    for (size_t i = 0; i < sizeof KERNELS / sizeof KERNELS[0]; i++)
        if (!strcmp(KERNELS[i].name, o.kernel)) k = &KERNELS[i];
    if (!k) usage();
    dtype_t dt = !strcmp(o.dtype, "f64") ? DT_F64 : !strcmp(o.dtype, "i32") ? DT_I32 : DT_F32;
    if (strcmp(o.dtype, DT_NAME[dt])) usage();
    if (!strcmp(k->name, "mul_aligned") && (o.offx % 64 || o.offy % 64 || o.offz % 64)) {
        fprintf(stderr, "bench: mul_aligned requires 64B-aligned offsets\n");
        return 2;
    }
    size_t es = DT_SIZE[dt];
    if (o.offx % es || o.offy % es || o.offz % es) {
        fprintf(stderr, "bench: offsets must be multiples of the element size\n");
        return 2;
    }

    pc_group g;
    pc_open(&g, strcmp(o.counters, "none") ? o.counters : NULL);
    if (o.header) print_header();
    char *save = NULL;
    for (char *tok = strtok_r(o.nlist, ",", &save); tok; tok = strtok_r(NULL, ",", &save))
        bench_one(&o, k, dt, strtoull(tok, 0, 0), &g);
    pc_close(&g);
    fprintf(stderr, "sink=%g\n", (double)sink);
    return 0;
}
