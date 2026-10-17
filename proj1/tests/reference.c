#include "reference.h"

#define LDABS(v) ((v) < 0 ? -(v) : (v))

#define DEFINE_REF(T, S)                                                                          \
    long double ref_dot_##S(const T *x, size_t sx, const int32_t *idx, const T *y, size_t sy,    \
                            size_t n, long double *abs_sum) {                                     \
        long double s = 0, a = 0;                                                                 \
        for (size_t i = 0; i < n; i++) {                                                          \
            long double p = (long double)x[idx ? (size_t)idx[i] : i * sx] * (long double)y[i * sy]; \
            s += p;                                                                               \
            a += LDABS(p);                                                                        \
        }                                                                                         \
        if (abs_sum) *abs_sum = a;                                                                \
        return s;                                                                                 \
    }                                                                                             \
    void ref_mul_##S(T *z, const T *x, size_t sx, const int32_t *idx, const T *y, size_t sy,     \
                     size_t n) {                                                                  \
        for (size_t i = 0; i < n; i++) {                                                          \
            T xv = x[idx ? (size_t)idx[i] : i * sx];                                              \
            T yv = y[i * sy];                                                                     \
            T r = xv * yv; /* single rounding, same as any correct kernel */                     \
            z[i] = r;                                                                             \
        }                                                                                         \
    }                                                                                             \
    void ref_stencil_##S(long double *y, long double *mag, const T *x, size_t n, T a, T b, T c) { \
        for (size_t i = 1; i + 1 < n; i++) {                                                      \
            long double t0 = (long double)a * x[i - 1], t1 = (long double)b * x[i],              \
                        t2 = (long double)c * x[i + 1];                                           \
            y[i] = t0 + t1 + t2;                                                                  \
            mag[i] = LDABS(t0) + LDABS(t1) + LDABS(t2);                                           \
        }                                                                                         \
    }                                                                                             \
    long double ref_sum_##S(const T *x, size_t n, long double *abs_sum) {                         \
        long double s = 0, a = 0;                                                                 \
        for (size_t i = 0; i < n; i++) {                                                          \
            s += x[i];                                                                            \
            a += LDABS((long double)x[i]);                                                        \
        }                                                                                         \
        if (abs_sum) *abs_sum = a;                                                                \
        return s;                                                                                 \
    }

DEFINE_REF(f32, f32)
DEFINE_REF(f64, f64)
DEFINE_REF(i32, i32)
