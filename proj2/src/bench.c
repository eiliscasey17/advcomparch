/* membench: memory-hierarchy probe (our MLC-equivalent). One CSV row per
 * sample on stdout.
 *
 *   membench chase   --size 1G[,...] [--pattern random|seq|fakerand|pagerand]
 *                    [--stride 64] [--param P] [--chains K] [--cpu C] [--mem-cpu M]
 *   membench bw      --cpus 33,41,... --size 64M (per thread) [--rw R:W] [--nt]
 *                    [--gran 64] [--stride 64] [--order seq|random] [--delay D] [--ms 200]
 *   membench loadlat --cpus LAT,GEN1,GEN2,... (same generator options as bw)
 *                    [--lat-size 1G]
 *   membench kern    --kernel mul|axpy --pattern contig|stride|gather [--gstride S]
 *                    --n N[,...] [--cpu C]
 *   membench info    cpuid cache/TLB geometry (key=value)
 * Common: [--pages 4k|2m|default] [--samples 11] [--min-ms 20] [--counters SET]
 *         [--seed S] [--header]
 *
 * Pinning is done here (pthread_setaffinity_np); --cpu -1 leaves the thread
 * unpinned (the E8 pitfall). First touch happens on the measuring thread
 * unless --mem-cpu names another cpu (remote-NUMA placement).
 */
#define _GNU_SOURCE
#include <cpuid.h>
#include <errno.h>
#include <math.h>
#include <pthread.h>
#include <sched.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "bw.h"
#include "chase.h"
#include "kernels.h"
#include "mem.h"
#include "perfctr.h"
#include "timing.h"

#define MAXT 64
#define MAXN 256

static const char *EVCOLS[] = {"cycles",     "instructions",   "l1d_miss",  "l2_miss_dc",
                               "fill_l2",    "fill_ccx",       "fill_other_ccx", "fill_dram",
                               "l2pf_hit_l3", "l2pf_miss_l3",  "dtlb_miss", "dtlb_l2_miss"};
#define NEVCOLS (int)(sizeof EVCOLS / sizeof EVCOLS[0])

typedef struct {
    const char *mode;
    size_t sizes[MAXN];
    int nsizes;
    int pattern;
    size_t stride;
    size_t param;
    int chains;
    int cpu, mem_cpu;
    int cpus[MAXT];
    int ncpus;
    page_mode pages;
    int samples;
    double min_ms;
    const char *counters;
    uint64_t seed;
    int header;
    /* bw / loadlat */
    int r, w, nt, gran, order_random, delay;
    double ms;
    size_t lat_size;
    /* kern */
    const char *kernel, *kpattern;
    size_t gstride;
} opts;

static void die(const char *msg) {
    fprintf(stderr, "membench: %s\n", msg);
    exit(2);
}

static size_t parse_size(const char *s) {
    char *end;
    double v = strtod(s, &end);
    size_t m = 1;
    if (*end == 'K' || *end == 'k') m = 1ull << 10;
    else if (*end == 'M' || *end == 'm') m = 1ull << 20;
    else if (*end == 'G' || *end == 'g') m = 1ull << 30;
    else if (*end) die("bad size");
    if (v <= 0) die("bad size");
    return (size_t)llround(v * (double)m);
}

static int parse_list_sizes(const char *s, size_t *out) {
    char *buf = strdup(s), *save = NULL;
    int n = 0;
    for (char *t = strtok_r(buf, ",", &save); t && n < MAXN; t = strtok_r(NULL, ",", &save))
        out[n++] = parse_size(t);
    free(buf);
    return n;
}

static int parse_list_ints(const char *s, int *out, int max) {
    char *buf = strdup(s), *save = NULL;
    int n = 0;
    for (char *t = strtok_r(buf, ",", &save); t && n < max; t = strtok_r(NULL, ",", &save))
        out[n++] = atoi(t);
    free(buf);
    return n;
}

static void parse(int argc, char **argv, opts *o) {
    memset(o, 0, sizeof *o);
    o->mode = argv[1];
    o->pattern = CH_RANDOM;
    o->stride = 64;
    o->chains = 1;
    o->cpu = o->mem_cpu = -2; /* -2 = not given */
    o->pages = PAGES_2M;
    o->samples = 11;
    o->min_ms = 20;
    o->counters = "clock";
    o->seed = 12345;
    o->r = 1;
    o->gran = 64;
    o->ms = 200;
    o->lat_size = 1ull << 30;
    o->kernel = "mul";
    o->kpattern = "contig";
    o->gstride = 1;
    for (int i = 2; i < argc; i++) {
        const char *a = argv[i];
        const char *v = i + 1 < argc ? argv[i + 1] : NULL;
#define ARG(name) (!strcmp(a, name) && v && (i++, 1))
        if (ARG("--size") || ARG("--n")) o->nsizes = parse_list_sizes(v, o->sizes);
        else if (ARG("--pattern")) {
            if (!strcmp(o->mode, "kern")) o->kpattern = v;
            else if ((o->pattern = chase_parse(v)) < 0) die("bad --pattern");
        } else if (ARG("--stride")) o->stride = parse_size(v);
        else if (ARG("--param")) o->param = (size_t)atoll(v);
        else if (ARG("--chains")) o->chains = atoi(v);
        else if (ARG("--cpu")) o->cpu = atoi(v);
        else if (ARG("--mem-cpu")) o->mem_cpu = atoi(v);
        else if (ARG("--cpus")) o->ncpus = parse_list_ints(v, o->cpus, MAXT);
        else if (ARG("--pages")) {
            if ((int)(o->pages = parse_pages(v)) < 0) die("bad --pages");
        } else if (ARG("--samples")) o->samples = atoi(v);
        else if (ARG("--min-ms")) o->min_ms = atof(v);
        else if (ARG("--counters")) o->counters = v;
        else if (ARG("--seed")) o->seed = strtoull(v, NULL, 0);
        else if (ARG("--rw")) {
            if (sscanf(v, "%d:%d", &o->r, &o->w) != 2) die("bad --rw (R:W)");
        } else if (ARG("--gran")) o->gran = atoi(v);
        else if (ARG("--order")) o->order_random = !strcmp(v, "random");
        else if (ARG("--delay")) o->delay = atoi(v);
        else if (ARG("--ms")) o->ms = atof(v);
        else if (ARG("--lat-size")) o->lat_size = parse_size(v);
        else if (ARG("--kernel")) o->kernel = v;
        else if (ARG("--gstride")) o->gstride = (size_t)atoll(v);
        else if (!strcmp(a, "--nt")) o->nt = 1;
        else if (!strcmp(a, "--header")) o->header = 1;
        else {
            fprintf(stderr, "membench: unknown or incomplete argument %s\n", a);
            exit(2);
        }
#undef ARG
    }
    if (o->samples < 1) die("--samples must be >= 1");
}

static void print_ev_header(void) {
    for (int i = 0; i < NEVCOLS; i++) printf(",%s", EVCOLS[i]);
    printf(",mux");
}

/* Counter values in EVCOLS order; empty for events not collected. */
static void print_ev(const pc_group *g, const uint64_t *v, double mux) {
    for (int i = 0; i < NEVCOLS; i++) {
        int k = -1;
        for (int j = 0; j < g->n; j++)
            if (!strcmp(g->name[j], EVCOLS[i])) k = j;
        if (k >= 0) printf(",%llu", (unsigned long long)v[k]);
        else printf(",");
    }
    if (g->n) printf(",%.4f", mux);
    else printf(",");
}

static void page_info(const membuf *b, double *thp, int *node) {
    *thp = mem_thp_fraction(b->base, b->bytes, NULL);
    *node = mem_node_of((char *)b->base + b->bytes / 2);
}

/* ------------------------------------------------------------------ chase */
typedef struct {
    const opts *o;
    size_t bytes;
    membuf buf;
    void *start;
    size_t n;
    int err;
} chase_setup;

static void *chase_setup_fn(void *arg) {
    chase_setup *s = arg;
    if (s->o->mem_cpu >= 0) pin_self(s->o->mem_cpu);
    if (mem_alloc(&s->buf, s->bytes, s->o->pages)) {
        s->err = 1;
        return NULL;
    }
    mem_touch(&s->buf);
    s->n = chase_build(s->buf.base, s->bytes, s->o->stride, s->o->pattern, s->o->param, s->o->seed,
                       &s->start);
    if (!s->n) s->err = 2;
    return NULL;
}

static void walk(void **p, int k, size_t steps) {
    if (k == 1) p[0] = chase_run(p[0], steps);
    else chase_run_k(p, k, steps);
}

static int cmd_chase(opts *o) {
    if (!o->nsizes) die("chase needs --size");
    if (!chase_k_supported(o->chains)) die("unsupported --chains");
    int cpu = o->cpu == -2 ? -1 : o->cpu;
    if (pin_self(cpu)) die("cannot pin");
    if (o->header) {
        printf("mode,size,pattern,stride,param,chains,pages,thp_frac,nodes,cpu,cpu_end,cpu_node,"
               "mem_node,sample,steps,ns");
        print_ev_header();
        printf("\n");
    }
    pc_group g;
    pc_open(&g, o->counters);
    for (int si = 0; si < o->nsizes; si++) {
        chase_setup s = {.o = o, .bytes = o->sizes[si]};
        if (o->mem_cpu >= 0) { /* first touch on another cpu (remote placement) */
            pthread_t t;
            pthread_create(&t, NULL, chase_setup_fn, &s);
            pthread_join(t, NULL);
        } else {
            chase_setup_fn(&s);
        }
        if (s.err) die(s.err == 1 ? "mmap failed" : "chase build failed (size/stride)");
        int k = o->chains;
        if ((size_t)k * 16 > s.n) die("too few nodes for --chains");
        void *p[CHASE_K_MAX];
        if (k == 1) p[0] = s.start;
        else chase_starts(s.start, s.n, k, p);
        double thp;
        int mnode;
        page_info(&s.buf, &thp, &mnode);
        /* warm up: two passes over cache-sized buffers, a bounded walk otherwise */
        size_t warm = 2 * s.n < (4u << 20) ? 2 * s.n : (4u << 20);
        walk(p, k, warm / (size_t)k + 16);
        /* calibrate steps per chain so a sample lasts >= min_ms */
        size_t probe = 1 << 14;
        uint64_t t0 = now_ns();
        walk(p, k, probe);
        double ns_per = (double)(now_ns() - t0) / (double)probe;
        size_t steps = (size_t)(o->min_ms * 1e6 / (ns_per > 0 ? ns_per : 1));
        steps = (steps < 4096 ? 4096 : steps + 15) / 16 * 16;
        for (int smp = 0; smp < o->samples; smp++) {
            uint64_t v[PC_MAX] = {0};
            int c0 = sched_getcpu();
            pc_start(&g);
            uint64_t a = now_ns();
            walk(p, k, steps);
            uint64_t b = now_ns();
            pc_stop(&g, v);
            int c1 = sched_getcpu();
            printf("chase,%zu,%s,%zu,%zu,%d,%s,%.4f,%zu,%d,%d,%d,%d,%d,%zu,%llu", s.bytes,
                   chase_name(o->pattern), o->stride, o->param, k, pages_name(o->pages), thp, s.n,
                   c0, c1, cpu_node(c0), mnode, smp, steps * (size_t)k,
                   (unsigned long long)(b - a));
            print_ev(&g, v, g.mux);
            printf("\n");
        }
        fflush(stdout);
        mem_free(&s.buf);
    }
    pc_close(&g);
    return 0;
}

/* ------------------------------------------------------------ bw/loadlat */
typedef struct {
    const opts *o;
    int cpu, role; /* role 0 = generator, 1 = latency chase */
    membuf buf;
    uint32_t *idx;
    bw_cfg cfg;
    size_t pos;
    void *chase_p;
    pc_group g;
    uint64_t cnt[PC_MAX];
    double mux;
    size_t done;
    uint64_t t_end;
    uint64_t sink;
    double thp;
    int mnode;
    int err;
} worker;

static pthread_barrier_t bar_ready, bar_start, bar_end;
static atomic_int stop_flag;
static atomic_int quit_flag;

static void *worker_fn(void *arg) {
    worker *w = arg;
    const opts *o = w->o;
    if (pin_self(w->cpu)) w->err = 1;
    if (w->role == 1) {
        if (mem_alloc(&w->buf, o->lat_size, o->pages)) w->err = 2;
        else {
            mem_touch(&w->buf);
            if (!chase_build(w->buf.base, o->lat_size, 64, CH_RANDOM, 0, o->seed, &w->chase_p))
                w->err = 3;
        }
    } else {
        size_t bytes = o->sizes[0];
        if (mem_alloc(&w->buf, bytes, o->pages)) w->err = 2;
        else {
            mem_touch(&w->buf);
            w->cfg = (bw_cfg){.gran = o->gran, .stride = o->stride, .r = o->r, .w = o->w,
                              .nt = o->nt, .delay = o->delay, .nslots = bytes / o->stride};
            const char *e = bw_check(&w->cfg);
            if (e) {
                fprintf(stderr, "membench: %s\n", e);
                w->err = 4;
            }
            if (o->order_random && !w->err) {
                w->idx = malloc(w->cfg.nslots * sizeof *w->idx);
                bw_perm(w->idx, w->cfg.nslots, o->seed + (uint64_t)w->cpu);
                w->cfg.idx = w->idx;
            }
            /* fault in every line once (write if the kernel writes) */
            if (!w->err) w->sink += bw_run(&w->cfg, w->buf.base, 0, w->cfg.nslots);
        }
    }
    if (!w->err) page_info(&w->buf, &w->thp, &w->mnode);
    pc_open(&w->g, o->counters);
    pthread_barrier_wait(&bar_ready);
    size_t chunk = w->role ? 64 : (size_t)(256 / (1 + o->delay / 16));
    if (chunk < 4) chunk = 4;
    for (;;) {
        pthread_barrier_wait(&bar_start);
        if (atomic_load(&quit_flag)) break;
        w->done = 0;
        pc_start(&w->g);
        if (w->err) {
            while (!atomic_load_explicit(&stop_flag, memory_order_relaxed)) {}
        } else if (w->role == 1) {
            void *p = w->chase_p;
            while (!atomic_load_explicit(&stop_flag, memory_order_relaxed)) {
                p = chase_run(p, chunk);
                w->done += chunk;
            }
            w->chase_p = p;
        } else {
            while (!atomic_load_explicit(&stop_flag, memory_order_relaxed)) {
                w->sink += bw_run(&w->cfg, w->buf.base, w->pos, chunk);
                w->pos = (w->pos + chunk) % w->cfg.nslots;
                w->done += chunk;
            }
        }
        w->t_end = now_ns();
        pc_stop(&w->g, w->cnt);
        w->mux = w->g.mux;
        pthread_barrier_wait(&bar_end);
    }
    pc_close(&w->g);
    return NULL;
}

static void sleep_ms(double ms) {
    struct timespec ts = {(time_t)(ms / 1000), (long)(fmod(ms, 1000.0) * 1e6)};
    while (nanosleep(&ts, &ts) && errno == EINTR) {}
}

static int cmd_threads(opts *o, int loadlat) {
    if (o->ncpus < 1) die("--cpus too short");
    if (!o->nsizes) o->sizes[o->nsizes++] = 64ull << 20;
    int nt = o->ncpus;
    worker *w = calloc((size_t)nt, sizeof *w);
    pthread_t th[MAXT];
    pthread_barrier_init(&bar_ready, NULL, (unsigned)nt + 1);
    pthread_barrier_init(&bar_start, NULL, (unsigned)nt + 1);
    pthread_barrier_init(&bar_end, NULL, (unsigned)nt + 1);
    for (int i = 0; i < nt; i++) {
        w[i] = (worker){.o = o, .cpu = o->cpus[i], .role = loadlat && i == 0};
        pthread_create(&th[i], NULL, worker_fn, &w[i]);
    }
    pthread_barrier_wait(&bar_ready);
    for (int i = 0; i < nt; i++)
        if (w[i].err) {
            fprintf(stderr, "membench: worker on cpu %d failed setup (%d)\n", w[i].cpu, w[i].err);
            exit(3);
        }
    double thp_min = 1;
    int node_mismatch = 0;
    for (int i = 0; i < nt; i++) {
        if (w[i].thp < thp_min) thp_min = w[i].thp;
        node_mismatch += w[i].mnode != cpu_node(w[i].cpu);
    }
    if (o->header) {
        printf("mode,gens,cpu0,size,rw_r,rw_w,nt,gran,stride,order,delay,pages,thp_min,"
               "remote_bufs,sample,ns,gen_accesses,lat_steps,lat_cycles");
        print_ev_header();
        printf("\n");
    }
    int gens = nt - loadlat;
    for (int smp = -1; smp < o->samples; smp++) { /* sample -1: warm-up, not printed */
        atomic_store(&stop_flag, 0);
        pthread_barrier_wait(&bar_start);
        uint64_t t0 = now_ns();
        sleep_ms(o->ms);
        atomic_store(&stop_flag, 1);
        pthread_barrier_wait(&bar_end);
        if (smp < 0) continue;
        uint64_t tend = t0, sum[PC_MAX] = {0};
        size_t acc = 0, lat_steps = 0;
        uint64_t lat_cyc = 0;
        double mux = 1;
        for (int i = 0; i < nt; i++) {
            if (w[i].t_end > tend) tend = w[i].t_end;
            if (w[i].role) {
                lat_steps = w[i].done;
                lat_cyc = w[i].g.n ? w[i].cnt[0] : 0;
                continue;
            }
            acc += w[i].done;
            for (int j = 0; j < w[i].g.n; j++) sum[j] += w[i].cnt[j];
            if (w[i].mux < mux) mux = w[i].mux;
        }
        printf("%s,%d,%d,%zu,%d,%d,%d,%d,%zu,%s,%d,%s,%.4f,%d,%d,%llu,%zu,%zu,%llu",
               loadlat ? "loadlat" : "bw", gens, o->cpus[0], o->sizes[0], o->r, o->w, o->nt,
               o->gran, o->stride, o->order_random ? "random" : "seq", o->delay,
               pages_name(o->pages), thp_min, node_mismatch, smp, (unsigned long long)(tend - t0),
               acc, lat_steps, (unsigned long long)lat_cyc);
        print_ev(gens ? &w[loadlat].g : &w[0].g, sum, mux); /* gens=0: unloaded reference */
        printf("\n");
        fflush(stdout);
    }
    atomic_store(&quit_flag, 1);
    pthread_barrier_wait(&bar_start);
    uint64_t sink = 0;
    for (int i = 0; i < nt; i++) {
        pthread_join(th[i], NULL);
        sink += w[i].sink;
        mem_free(&w[i].buf);
        free(w[i].idx);
    }
    if (sink == 42) fprintf(stderr, "#");
    free(w);
    return 0;
}

/* ------------------------------------------------------------------- kern */
static int cmd_kern(opts *o) {
    if (!o->nsizes) die("kern needs --n");
    int is_mul = !strcmp(o->kernel, "mul");
    if (!is_mul && strcmp(o->kernel, "axpy")) die("--kernel mul|axpy");
    int pat = !strcmp(o->kpattern, "contig") ? 0 : !strcmp(o->kpattern, "stride") ? 1
            : !strcmp(o->kpattern, "gather") ? 2 : -1;
    if (pat < 0) die("--pattern contig|stride|gather");
    size_t s = pat == 1 ? (o->gstride ? o->gstride : 1) : 1;
    int cpu = o->cpu == -2 ? -1 : o->cpu;
    if (pin_self(cpu)) die("cannot pin");
    if (o->header) {
        printf("mode,kernel,pattern,gstride,n,footprint,pages,thp_frac,sample,reps,ns");
        print_ev_header();
        printf("\n");
    }
    pc_group g;
    pc_open(&g, o->counters);
    for (int ni = 0; ni < o->nsizes; ni++) {
        size_t n = o->sizes[ni], span = n * s, arrays = is_mul ? 3 : 2;
        membuf bx, by, bz = {0}, bi = {0};
        if (mem_alloc(&bx, span * 8, o->pages) || mem_alloc(&by, span * 8, o->pages) ||
            (is_mul && mem_alloc(&bz, span * 8, o->pages)) ||
            (pat == 2 && mem_alloc(&bi, n * 4, o->pages)))
            die("mmap failed");
        double *x = bx.base, *y = by.base, *z = bz.base;
        uint32_t *idx = bi.base;
        for (size_t i = 0; i < span; i++) {
            x[i] = 1.0 + (double)(i % 7);
            y[i] = 0.5;
            if (z) z[i] = 0;
        }
        if (pat == 2) bw_perm(idx, n, o->seed);
        size_t footprint = arrays * span * 8 + (pat == 2 ? n * 4 : 0);
        double thp = mem_thp_fraction(bx.base, bx.bytes, NULL);
#define CALL()                                                          \
    do {                                                                \
        if (is_mul) {                                                   \
            if (pat == 0) mul_contig(n, x, y, z);                       \
            else if (pat == 1) mul_stride(n, s, x, y, z);               \
            else mul_gather(n, idx, x, y, z);                           \
        } else {                                                        \
            if (pat == 0) axpy_contig(n, 1e-9, x, y);                   \
            else if (pat == 1) axpy_stride(n, s, 1e-9, x, y);           \
            else axpy_gather(n, idx, 1e-9, x, y);                       \
        }                                                               \
    } while (0)
        CALL();
        uint64_t t0 = now_ns();
        CALL();
        double one = (double)(now_ns() - t0);
        size_t reps = (size_t)ceil(o->min_ms * 1e6 / (one > 1 ? one : 1));
        if (reps < 1) reps = 1;
        for (int smp = 0; smp < o->samples; smp++) {
            uint64_t v[PC_MAX] = {0};
            pc_start(&g);
            uint64_t a = now_ns();
            for (size_t r = 0; r < reps; r++) CALL();
            uint64_t b = now_ns();
            pc_stop(&g, v);
            printf("kern,%s,%s,%zu,%zu,%zu,%s,%.4f,%d,%zu,%llu", o->kernel, o->kpattern, s, n,
                   footprint, pages_name(o->pages), thp, smp, reps, (unsigned long long)(b - a));
            print_ev(&g, v, g.mux);
            printf("\n");
        }
#undef CALL
        fflush(stdout);
        mem_free(&bx);
        mem_free(&by);
        mem_free(&bz);
        mem_free(&bi);
    }
    pc_close(&g);
    return 0;
}

/* ------------------------------------------------------------------- info */
static int cmd_info(void) {
    unsigned a, b, c, d;
    /* AMD: Fn8000_0005 L1 TLB/cache, Fn8000_0006 L2 TLB/L2/L3, Fn8000_0019 1G TLB */
    __cpuid(0x80000005, a, b, c, d);
    printf("l1_dtlb_2m_entries=%u\nl1_dtlb_4k_entries=%u\n", (a >> 16) & 0xff, (b >> 16) & 0xff);
    printf("l1d_kib=%u\nl1d_assoc=%u\nl1d_line=%u\n", c >> 24, (c >> 16) & 0xff, c & 0xff);
    __cpuid(0x80000006, a, b, c, d);
    printf("l2_dtlb_2m_entries=%u\nl2_dtlb_4k_entries=%u\n", (a >> 16) & 0xfff, (b >> 16) & 0xfff);
    printf("l2_kib=%u\nl2_line=%u\n", c >> 16, c & 0xff);
    printf("l3_kib_cpuid_socket=%u\n", (d >> 18) * 512);
    __cpuid(0x80000019, a, b, c, d);
    printf("l1_dtlb_1g_entries=%u\nl2_dtlb_1g_entries=%u\n", (a >> 16) & 0xfff, (b >> 16) & 0xfff);
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 2) die("usage: membench chase|bw|loadlat|kern|info [options]");
    opts o;
    parse(argc, argv, &o);
    if (!strcmp(o.mode, "chase")) return cmd_chase(&o);
    if (!strcmp(o.mode, "bw")) return cmd_threads(&o, 0);
    if (!strcmp(o.mode, "loadlat")) return cmd_threads(&o, 1);
    if (!strcmp(o.mode, "kern")) return cmd_kern(&o);
    if (!strcmp(o.mode, "info")) return cmd_info();
    die("unknown mode");
    return 2;
}
