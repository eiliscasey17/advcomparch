/* Minimal perf_event_open wrapper: one counter group, user-space only
 * (perf_event_paranoid=2 permits this for our own process). */
#ifndef PERFCTR_H
#define PERFCTR_H

#include <stdint.h>

#define PC_MAX 8

typedef struct {
    int n;                     /* events successfully opened */
    int fd[PC_MAX];
    const char *name[PC_MAX];
    uint64_t start[PC_MAX];
    double mux;                /* time_running / time_enabled of last read (1.0 = no multiplexing) */
} pc_group;

/* Named event sets (see perfctr.c for encodings):
 *   none   - nothing
 *   core   - cycles, instructions, fp_flops
 *   width  - cycles, fp_scalar, fp_p128, fp_p256, fp_p512
 * (Max 5 counters per set: the NMI watchdog holds one of Zen 4's six. A set
 * that does not fit is never scheduled -- harness rows then show mux=0.)
 *   mem    - cycles, l1d_miss, l2pf_hit_l3, l2pf_miss_l3, fill_dram
 *   align  - cycles, instructions, misal_64, misal_4k
 * Or a custom list "name=EVT:UMASK,..." (hex) of AMD raw events; "cycles" and
 * "instructions" are accepted as generic names.
 * Returns number of events opened (events that fail to open are skipped and
 * reported on stderr). */
int pc_open(pc_group *g, const char *set);
void pc_close(pc_group *g);
void pc_start(pc_group *g);
/* Writes per-event deltas since pc_start into out[0..n-1]. */
void pc_stop(pc_group *g, uint64_t *out);

#endif
