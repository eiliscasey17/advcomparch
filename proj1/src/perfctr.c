#define _GNU_SOURCE
#include "perfctr.h"

#include <linux/perf_event.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/syscall.h>
#include <unistd.h>

/* AMD Zen 4 core-PMU raw events (PPR for Family 19h Model 11h). Encodings are
 * validated against known-count inline-asm loops by tests/test_perfctr.c; do
 * not trust a new one until it passes there. */
typedef struct {
    const char *name;
    uint32_t type; /* PERF_TYPE_HARDWARE / PERF_TYPE_HW_CACHE / PERF_TYPE_RAW */
    uint64_t config;
} pc_event;

#define AMD_RAW(evt, umask) \
    ((uint64_t)((evt)&0xff) | ((uint64_t)(umask) << 8) | (((uint64_t)(evt)&0xf00) << 24))

static const pc_event EVENTS[] = {
    {"cycles", PERF_TYPE_HARDWARE, PERF_COUNT_HW_CPU_CYCLES},
    {"instructions", PERF_TYPE_HARDWARE, PERF_COUNT_HW_INSTRUCTIONS},
    {"l1d_miss", PERF_TYPE_HW_CACHE,
     PERF_COUNT_HW_CACHE_L1D | (PERF_COUNT_HW_CACHE_OP_READ << 8) |
         (PERF_COUNT_HW_CACHE_RESULT_MISS << 16)},
    /* PMCx003 FpRetSseAvxOps: retired SSE/AVX FLOPs (FMA counts 2/lane). */
    {"fp_flops", PERF_TYPE_RAW, AMD_RAW(0x003, 0x1f)},
    /* PMCx008 FpOpsRetiredByWidth. */
    {"fp_scalar", PERF_TYPE_RAW, AMD_RAW(0x008, 0x04)},
    {"fp_p128", PERF_TYPE_RAW, AMD_RAW(0x008, 0x08)},
    {"fp_p256", PERF_TYPE_RAW, AMD_RAW(0x008, 0x10)},
    {"fp_p512", PERF_TYPE_RAW, AMD_RAW(0x008, 0x20)},
    /* PMCx047 LsMisalLoads: MA64 = crosses 64B line, MA4K = crosses 4K page. */
    {"misal_64", PERF_TYPE_RAW, AMD_RAW(0x047, 0x01)},
    {"misal_4k", PERF_TYPE_RAW, AMD_RAW(0x047, 0x02)},
    /* PMCx071 / PMCx072: L2 prefetches that missed L2 and hit / missed L3. */
    {"l2pf_hit_l3", PERF_TYPE_RAW, AMD_RAW(0x071, 0xff)},
    {"l2pf_miss_l3", PERF_TYPE_RAW, AMD_RAW(0x072, 0xff)},
    /* PMCx044 LsAnyFillsFromSys: DramIoNear (0x08) | DramIoFar (0x40). */
    {"fill_dram", PERF_TYPE_RAW, AMD_RAW(0x044, 0x48)},
};
#define NEVENTS (int)(sizeof(EVENTS) / sizeof(EVENTS[0]))

static const struct {
    const char *set;
    const char *events;
} SETS[] = {
    {"none", ""},
    {"core", "cycles,instructions,fp_flops"},
    {"width", "cycles,fp_scalar,fp_p128,fp_p256,fp_p512"},
    {"mem", "cycles,l1d_miss,l2pf_hit_l3,l2pf_miss_l3,fill_dram"},
    {"align", "cycles,instructions,misal_64,misal_4k"},
};

static long perf_open(struct perf_event_attr *a, int group_fd) {
    return syscall(SYS_perf_event_open, a, 0 /*this thread*/, -1 /*any cpu*/, group_fd, 0);
}

static int add_event(pc_group *g, const char *name, uint32_t type, uint64_t config) {
    if (g->n >= PC_MAX) return -1;
    struct perf_event_attr a;
    memset(&a, 0, sizeof a);
    a.size = sizeof a;
    a.type = type;
    a.config = config;
    a.disabled = (g->n == 0);
    a.exclude_kernel = 1;
    a.exclude_hv = 1;
    a.read_format = PERF_FORMAT_GROUP | PERF_FORMAT_TOTAL_TIME_ENABLED |
                    PERF_FORMAT_TOTAL_TIME_RUNNING;
    int fd = (int)perf_open(&a, g->n == 0 ? -1 : g->fd[0]);
    if (fd < 0) {
        fprintf(stderr, "perfctr: cannot open %s (type %u config 0x%llx): %m\n", name, type,
                (unsigned long long)config);
        return -1;
    }
    g->fd[g->n] = fd;
    g->name[g->n] = name;
    g->n++;
    return 0;
}

/* name=EVT:UMASK with hex fields, or a known event name. */
static void add_spec(pc_group *g, const char *spec) {
    const char *eq = strchr(spec, '=');
    if (!eq) {
        for (int i = 0; i < NEVENTS; i++)
            if (!strcmp(EVENTS[i].name, spec)) {
                add_event(g, EVENTS[i].name, EVENTS[i].type, EVENTS[i].config);
                return;
            }
        fprintf(stderr, "perfctr: unknown event %s\n", spec);
        return;
    }
    unsigned evt = 0, umask = 0;
    if (sscanf(eq + 1, "%x:%x", &evt, &umask) != 2) {
        fprintf(stderr, "perfctr: bad raw spec %s\n", spec);
        return;
    }
    add_event(g, strndup(spec, (size_t)(eq - spec)), PERF_TYPE_RAW, AMD_RAW(evt, umask));
}

int pc_open(pc_group *g, const char *set) {
    memset(g, 0, sizeof *g);
    g->mux = 1.0;
    if (!set) return 0;
    const char *list = set;
    for (size_t i = 0; i < sizeof SETS / sizeof SETS[0]; i++)
        if (!strcmp(SETS[i].set, set)) list = SETS[i].events;
    char *buf = strdup(list), *save = NULL;
    for (char *tok = strtok_r(buf, ",", &save); tok; tok = strtok_r(NULL, ",", &save))
        add_spec(g, tok);
    free(buf);
    return g->n;
}

void pc_close(pc_group *g) {
    for (int i = 0; i < g->n; i++) close(g->fd[i]);
    g->n = 0;
}

static void read_group(pc_group *g, uint64_t *vals) {
    uint64_t buf[3 + PC_MAX];
    if (read(g->fd[0], buf, sizeof buf) < (ssize_t)(3 * sizeof(uint64_t))) {
        memset(vals, 0, sizeof(uint64_t) * g->n);
        g->mux = 0;
        return;
    }
    g->mux = buf[1] ? (double)buf[2] / (double)buf[1] : 1.0;
    for (int i = 0; i < g->n && i < (int)buf[0]; i++) vals[i] = buf[3 + i];
}

void pc_start(pc_group *g) {
    if (!g->n) return;
    ioctl(g->fd[0], PERF_EVENT_IOC_ENABLE, PERF_IOC_FLAG_GROUP);
    read_group(g, g->start);
}

void pc_stop(pc_group *g, uint64_t *out) {
    if (!g->n) return;
    uint64_t now[PC_MAX];
    read_group(g, now);
    ioctl(g->fd[0], PERF_EVENT_IOC_DISABLE, PERF_IOC_FLAG_GROUP);
    for (int i = 0; i < g->n; i++) out[i] = now[i] - g->start[i];
}
