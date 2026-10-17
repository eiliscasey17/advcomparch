/* Reference implementations (tests/reference.c, compiled -O0, generic ISA,
 * -ffp-contract=off). Sums are carried in long double so the reference is far
 * more accurate than any kernel under test. Strides/index arrays are general,
 * so one reference covers unit-stride, strided and gather kernels. */
#ifndef REFERENCE_H
#define REFERENCE_H

#include <stddef.h>
#include <stdint.h>

#include "kernels.h"

#define DECLARE_REF(T, S)                                                                         \
    /* sum_i x[ix(i)]*y[iy(i)], ix(i) = idx ? idx[i] : i*sx ; also returns sum |x*y| */          \
    long double ref_dot_##S(const T *x, size_t sx, const int32_t *idx, const T *y, size_t sy,    \
                            size_t n, long double *abs_sum);                                      \
    void ref_mul_##S(T *z, const T *x, size_t sx, const int32_t *idx, const T *y, size_t sy,     \
                     size_t n);                                                                   \
    /* long-double result + per-element magnitude bound, for i in [1, n-2] */                    \
    void ref_stencil_##S(long double *y, long double *mag, const T *x, size_t n, T a, T b, T c);  \
    long double ref_sum_##S(const T *x, size_t n, long double *abs_sum);

DECLARE_REF(f32, f32)
DECLARE_REF(f64, f64)
DECLARE_REF(i32, i32)

#endif
