/* Compiled with KERNEL_CFLAGS (-O3 -march=native); kept noinline so the
 * timed call is exactly one kernel invocation. */
#include "kernels.h"

#define NOINLINE __attribute__((noinline))

NOINLINE void mul_contig(size_t n, const double *restrict x, const double *restrict y,
                         double *restrict z) {
    for (size_t i = 0; i < n; i++) z[i] = x[i] * y[i];
}

NOINLINE void axpy_contig(size_t n, double a, const double *restrict x, double *restrict y) {
    for (size_t i = 0; i < n; i++) y[i] = a * x[i] + y[i];
}

NOINLINE void mul_stride(size_t n, size_t s, const double *restrict x, const double *restrict y,
                         double *restrict z) {
    for (size_t i = 0; i < n; i++) z[i * s] = x[i * s] * y[i * s];
}

NOINLINE void axpy_stride(size_t n, size_t s, double a, const double *restrict x,
                          double *restrict y) {
    for (size_t i = 0; i < n; i++) y[i * s] = a * x[i * s] + y[i * s];
}

NOINLINE void mul_gather(size_t n, const uint32_t *restrict idx, const double *restrict x,
                         const double *restrict y, double *restrict z) {
    for (size_t i = 0; i < n; i++) {
        uint32_t k = idx[i];
        z[k] = x[k] * y[k];
    }
}

NOINLINE void axpy_gather(size_t n, const uint32_t *restrict idx, double a,
                          const double *restrict x, double *restrict y) {
    for (size_t i = 0; i < n; i++) {
        uint32_t k = idx[i];
        y[k] = a * x[k] + y[k];
    }
}
