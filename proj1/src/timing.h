#ifndef TIMING_H
#define TIMING_H

#include <stdint.h>
#include <time.h>
#include <x86intrin.h>

/* Wall-clock nanoseconds, immune to NTP slewing. */
static inline uint64_t now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC_RAW, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

/* Constant-rate TSC (nominal 2.7 GHz on this machine; NOT core cycles under
 * boost -- core cycles come from the PMU). */
static inline uint64_t tsc(void) { return __rdtsc(); }

#endif
