/* Kernel declarations for all dtypes. Definitions: kernels_body.inc. */
#ifndef KERNELS_H
#define KERNELS_H

#include <stddef.h>
#include <stdint.h>

typedef float f32;
typedef double f64;
typedef int32_t i32;

#define NOINLINE __attribute__((noinline))

#define PRAGMA_(x) _Pragma(#x)
#define PRAGMA(x) PRAGMA_(x)
/* Only effective with -fopenmp-simd (never passed to the scalar build). */
#define OMP_SIMD_RED(...) PRAGMA(omp simd reduction(+ : __VA_ARGS__))

/* Compiled out of scalar builds: the pragma forces vectorization even with
 * -fno-vectorize. */
#if defined(__clang__) && !defined(KERNELS_SCALAR)
#define PRED_PRAGMA PRAGMA(clang loop vectorize_predicate(enable))
#else
#define PRED_PRAGMA
#endif

#define CAT_(a, b) a##_##b
#define CAT(a, b) CAT_(a, b)
#define FN(name) CAT(name, SFX)

#define DECLARE_KERNELS(T, S)                                                                       \
    T dot_simd_##S(const T *restrict x, const T *restrict y, size_t n);                             \
    T dot_plain_##S(const T *restrict x, const T *restrict y, size_t n);                            \
    T dot_acc2_##S(const T *restrict x, const T *restrict y, size_t n);                             \
    T dot_acc4_##S(const T *restrict x, const T *restrict y, size_t n);                             \
    T dot_acc8_##S(const T *restrict x, const T *restrict y, size_t n);                             \
    void mul_##S(T *restrict z, const T *restrict x, const T *restrict y, size_t n);                \
    void mul_aligned_##S(T *restrict z, const T *restrict x, const T *restrict y, size_t n);        \
    void mul_pred_##S(T *restrict z, const T *restrict x, const T *restrict y, size_t n);           \
    void stencil_##S(T *restrict y, const T *restrict x, size_t n, T a, T b, T c);                  \
    T dot_s2_##S(const T *restrict x, const T *restrict y, size_t n);                               \
    T dot_s4_##S(const T *restrict x, const T *restrict y, size_t n);                               \
    T dot_s8_##S(const T *restrict x, const T *restrict y, size_t n);                               \
    void mul_s2_##S(T *restrict z, const T *restrict x, const T *restrict y, size_t n);             \
    void mul_s4_##S(T *restrict z, const T *restrict x, const T *restrict y, size_t n);             \
    void mul_s8_##S(T *restrict z, const T *restrict x, const T *restrict y, size_t n);             \
    T dot_gather_##S(const T *restrict x, const int32_t *restrict idx, const T *restrict y,         \
                     size_t n);                                                                     \
    void mul_gather_##S(T *restrict z, const T *restrict x, const int32_t *restrict idx,            \
                        const T *restrict y, size_t n);                                             \
    T bw_read_##S(const T *restrict x, size_t n);                                                   \
    void bw_write_##S(T *restrict z, size_t n, T c);                                                \
    void bw_scale_##S(T *restrict z, const T *restrict x, size_t n, T c);                           \
    void bw_triad_##S(T *restrict z, const T *restrict x, const T *restrict y, size_t n, T c);

DECLARE_KERNELS(f32, f32)
DECLARE_KERNELS(f64, f64)
DECLARE_KERNELS(i32, i32)

#endif
