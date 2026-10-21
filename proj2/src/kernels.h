/* Simple loop kernels used to correlate cache/TLB misses with runtime (E6,
 * E7). f64 arrays. Three address patterns over the same arithmetic:
 *   contig  element i                       (unit stride, vectorized)
 *   stride  element i*s                     (one element per s; s=8 -> one per
 *                                            64 B line, s=512 -> one per 4 KiB page)
 *   gather  element idx[i] (random permutation of 0..n-1)
 * mul:  z[k] = x[k] * y[k]
 * axpy: y[k] = a * x[k] + y[k]
 */
#ifndef KERNELS_H
#define KERNELS_H

#include <stddef.h>
#include <stdint.h>

void mul_contig(size_t n, const double *restrict x, const double *restrict y, double *restrict z);
void axpy_contig(size_t n, double a, const double *restrict x, double *restrict y);
void mul_stride(size_t n, size_t s, const double *restrict x, const double *restrict y,
                double *restrict z);
void axpy_stride(size_t n, size_t s, double a, const double *restrict x, double *restrict y);
void mul_gather(size_t n, const uint32_t *restrict idx, const double *restrict x,
                const double *restrict y, double *restrict z);
void axpy_gather(size_t n, const uint32_t *restrict idx, double a, const double *restrict x,
                 double *restrict y);

#endif
