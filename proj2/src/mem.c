#define _GNU_SOURCE
#include "mem.h"

#include <pthread.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <unistd.h>

#define HUGE_SZ ((size_t)2 << 20)

page_mode parse_pages(const char *s) {
    if (!strcmp(s, "4k")) return PAGES_4K;
    if (!strcmp(s, "2m")) return PAGES_2M;
    if (!strcmp(s, "default")) return PAGES_DEFAULT;
    return (page_mode)-1;
}

const char *pages_name(page_mode m) {
    return m == PAGES_4K ? "4k" : m == PAGES_2M ? "2m" : "default";
}

int mem_alloc(membuf *b, size_t bytes, page_mode mode) {
    memset(b, 0, sizeof *b);
    size_t len = (bytes + HUGE_SZ - 1) / HUGE_SZ * HUGE_SZ;
    b->mapped = len + HUGE_SZ;
    b->map = mmap(NULL, b->mapped, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (b->map == MAP_FAILED) return -1;
    b->base = (void *)(((uintptr_t)b->map + HUGE_SZ - 1) & ~(HUGE_SZ - 1));
    b->bytes = bytes;
    b->mode = mode;
    if (mode == PAGES_2M && madvise(b->base, len, MADV_HUGEPAGE)) return -1;
    if (mode == PAGES_4K && madvise(b->base, len, MADV_NOHUGEPAGE)) return -1;
    return 0;
}

void mem_free(membuf *b) {
    if (b->map && b->map != MAP_FAILED) munmap(b->map, b->mapped);
    memset(b, 0, sizeof *b);
}

void mem_touch(membuf *b) {
    volatile char *p = b->base;
    for (size_t off = 0; off < b->bytes; off += 4096) p[off] = 1;
}

double mem_thp_fraction(const void *p, size_t len, size_t *rss_out) {
    FILE *f = fopen("/proc/self/smaps", "r");
    if (!f) return -1;
    uintptr_t lo = (uintptr_t)p, hi = lo + len;
    char line[512];
    int in = 0;
    size_t rss = 0, thp = 0;
    while (fgets(line, sizeof line, f)) {
        uintptr_t a, z;
        if (sscanf(line, "%lx-%lx ", &a, &z) == 2 && strchr(line, '-') < strchr(line, ' ')) {
            in = a < hi && z > lo;
            continue;
        }
        if (!in) continue;
        size_t kb;
        if (sscanf(line, "Rss: %zu kB", &kb) == 1) rss += kb << 10;
        else if (sscanf(line, "AnonHugePages: %zu kB", &kb) == 1) thp += kb << 10;
    }
    fclose(f);
    if (rss_out) *rss_out = rss;
    return rss ? (double)thp / (double)rss : 0.0;
}

int mem_node_of(const void *p) {
    void *page = (void *)((uintptr_t)p & ~(uintptr_t)4095);
    int status = -1;
    /* nodes == NULL: query only, status[] receives the node of each page */
    if (syscall(SYS_move_pages, 0, 1UL, &page, NULL, &status, 0)) return -1;
    return status;
}

double mem_node_fraction(const void *p, size_t len, int node, int samples) {
    int hit = 0;
    for (int i = 0; i < samples; i++)
        hit += mem_node_of((const char *)p + (len / samples) * (size_t)i) == node;
    return (double)hit / samples;
}

int cpu_node(int cpu) {
    for (int n = 0; n < 8; n++) {
        char path[96];
        snprintf(path, sizeof path, "/sys/devices/system/cpu/cpu%d/node%d", cpu, n);
        if (access(path, F_OK) == 0) return n;
    }
    return -1;
}

int pin_self(int cpu) {
    if (cpu < 0) return 0;
    cpu_set_t s;
    CPU_ZERO(&s);
    CPU_SET(cpu, &s);
    return pthread_setaffinity_np(pthread_self(), sizeof s, &s);
}
