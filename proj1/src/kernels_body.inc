/* Kernel bodies, instantiated once per data type by kernels_{f32,f64,i32}.c.
 *
 * The including file defines:
 *   T    element type (float, double, int32_t)
 *   SFX  name suffix (f32, f64, i32)
 *
 * Each dtype is its own translation unit, so compiler-report line numbers in
 * this file map unambiguously to a kernel (scripts/vec_evidence.py parses the
 * FN(name) definitions below to build that map). Keep one kernel per FN().
 *
 * Every kernel is noinline and lives in a separate TU from the harness, so the
 * compiler can neither inline it into the timing loop nor elide it.
 */
#include "kernels.h"

/* ---------------------------------------------------------------- dot -- */

/* Main dot product: explicit SIMD reduction (legal without -ffast-math). */
NOINLINE T FN(dot_simd)(const T *restrict x, const T *restrict y, size_t n) {
    T s = 0;
    OMP_SIMD_RED(s)
    for (size_t i = 0; i < n; i++) s += x[i] * y[i];
    return s;
}

/* Negative example: looks SIMD-friendly, but IEEE FP forbids reassociating
 * the sum. GCC "vectorizes" it as an in-order (fold-left) reduction; Clang
 * refuses. Integer addition is associative, so the i32 version vectorizes. */
NOINLINE T FN(dot_plain)(const T *restrict x, const T *restrict y, size_t n) {
    T s = 0;
    for (size_t i = 0; i < n; i++) s += x[i] * y[i];
    return s;
}

/* Multi-accumulator dots: K contiguous chunks, one accumulator each. In vector
 * builds each accumulator becomes its own vector register (K independent add
 * chains); in the scalar build it is K independent scalar chains (ILP). */
NOINLINE T FN(dot_acc2)(const T *restrict x, const T *restrict y, size_t n) {
    size_t q = n / 2;
    T s0 = 0, s1 = 0;
    OMP_SIMD_RED(s0, s1)
    for (size_t i = 0; i < q; i++) {
        s0 += x[i] * y[i];
        s1 += x[i + q] * y[i + q];
    }
    T s = s0 + s1;
    for (size_t i = 2 * q; i < n; i++) s += x[i] * y[i];
    return s;
}

NOINLINE T FN(dot_acc4)(const T *restrict x, const T *restrict y, size_t n) {
    size_t q = n / 4;
    T s0 = 0, s1 = 0, s2 = 0, s3 = 0;
    OMP_SIMD_RED(s0, s1, s2, s3)
    for (size_t i = 0; i < q; i++) {
        s0 += x[i] * y[i];
        s1 += x[i + q] * y[i + q];
        s2 += x[i + 2 * q] * y[i + 2 * q];
        s3 += x[i + 3 * q] * y[i + 3 * q];
    }
    T s = (s0 + s1) + (s2 + s3);
    for (size_t i = 4 * q; i < n; i++) s += x[i] * y[i];
    return s;
}

NOINLINE T FN(dot_acc8)(const T *restrict x, const T *restrict y, size_t n) {
    size_t q = n / 8;
    T s0 = 0, s1 = 0, s2 = 0, s3 = 0, s4 = 0, s5 = 0, s6 = 0, s7 = 0;
    OMP_SIMD_RED(s0, s1, s2, s3, s4, s5, s6, s7)
    for (size_t i = 0; i < q; i++) {
        s0 += x[i] * y[i];
        s1 += x[i + q] * y[i + q];
        s2 += x[i + 2 * q] * y[i + 2 * q];
        s3 += x[i + 3 * q] * y[i + 3 * q];
        s4 += x[i + 4 * q] * y[i + 4 * q];
        s5 += x[i + 5 * q] * y[i + 5 * q];
        s6 += x[i + 6 * q] * y[i + 6 * q];
        s7 += x[i + 7 * q] * y[i + 7 * q];
    }
    T s = ((s0 + s1) + (s2 + s3)) + ((s4 + s5) + (s6 + s7));
    for (size_t i = 8 * q; i < n; i++) s += x[i] * y[i];
    return s;
}

/* -------------------------------------------------- elementwise multiply -- */

NOINLINE void FN(mul)(T *restrict z, const T *restrict x, const T *restrict y, size_t n) {
    for (size_t i = 0; i < n; i++) z[i] = x[i] * y[i];
}

/* Same loop, but promises 64-byte alignment: lets the compiler drop any
 * alignment peeling / use aligned moves. Only call with aligned pointers. */
NOINLINE void FN(mul_aligned)(T *restrict z, const T *restrict x, const T *restrict y, size_t n) {
    T *za = __builtin_assume_aligned(z, 64);
    const T *xa = __builtin_assume_aligned(x, 64);
    const T *ya = __builtin_assume_aligned(y, 64);
    for (size_t i = 0; i < n; i++) za[i] = xa[i] * ya[i];
}

/* Same loop with tail-folding by predication requested (Clang: masked AVX-512
 * loads/stores, no scalar epilogue). GCC ignores the pragma, so under GCC this
 * is identical to mul. */
NOINLINE void FN(mul_pred)(T *restrict z, const T *restrict x, const T *restrict y, size_t n) {
    PRED_PRAGMA
    for (size_t i = 0; i < n; i++) z[i] = x[i] * y[i];
}

/* -------------------------------------------------------------- stencil -- */

/* y[i] = a*x[i-1] + b*x[i] + c*x[i+1] for i in [1, n-2]; y[0], y[n-1] untouched. */
NOINLINE void FN(stencil)(T *restrict y, const T *restrict x, size_t n, T a, T b, T c) {
    if (n < 3) return;
    for (size_t i = 1; i < n - 1; i++) y[i] = a * x[i - 1] + b * x[i] + c * x[i + 1];
}

/* -------------------------------------------- strided (compile-time S) -- */

NOINLINE T FN(dot_s2)(const T *restrict x, const T *restrict y, size_t n) {
    T s = 0;
    OMP_SIMD_RED(s)
    for (size_t i = 0; i < n; i++) s += x[2 * i] * y[2 * i];
    return s;
}

NOINLINE T FN(dot_s4)(const T *restrict x, const T *restrict y, size_t n) {
    T s = 0;
    OMP_SIMD_RED(s)
    for (size_t i = 0; i < n; i++) s += x[4 * i] * y[4 * i];
    return s;
}

NOINLINE T FN(dot_s8)(const T *restrict x, const T *restrict y, size_t n) {
    T s = 0;
    OMP_SIMD_RED(s)
    for (size_t i = 0; i < n; i++) s += x[8 * i] * y[8 * i];
    return s;
}

NOINLINE void FN(mul_s2)(T *restrict z, const T *restrict x, const T *restrict y, size_t n) {
    for (size_t i = 0; i < n; i++) z[i] = x[2 * i] * y[2 * i];
}

NOINLINE void FN(mul_s4)(T *restrict z, const T *restrict x, const T *restrict y, size_t n) {
    for (size_t i = 0; i < n; i++) z[i] = x[4 * i] * y[4 * i];
}

NOINLINE void FN(mul_s8)(T *restrict z, const T *restrict x, const T *restrict y, size_t n) {
    for (size_t i = 0; i < n; i++) z[i] = x[8 * i] * y[8 * i];
}

/* --------------------------------------------------------------- gather -- */

NOINLINE T FN(dot_gather)(const T *restrict x, const int32_t *restrict idx,
                          const T *restrict y, size_t n) {
    T s = 0;
    OMP_SIMD_RED(s)
    for (size_t i = 0; i < n; i++) s += x[idx[i]] * y[i];
    return s;
}

NOINLINE void FN(mul_gather)(T *restrict z, const T *restrict x, const int32_t *restrict idx,
                             const T *restrict y, size_t n) {
    for (size_t i = 0; i < n; i++) z[i] = x[idx[i]] * y[i];
}

/* ------------------------------------------- bandwidth calibration (E0) -- */
/* STREAM-style. No plain copy/fill on purpose: compilers turn those into
 * memcpy/memset calls. The scale factor c is a runtime value, so it can't be
 * idiom-recognized. */

NOINLINE T FN(bw_read)(const T *restrict x, size_t n) {
    size_t q = n / 4;
    T s0 = 0, s1 = 0, s2 = 0, s3 = 0;
    OMP_SIMD_RED(s0, s1, s2, s3)
    for (size_t i = 0; i < q; i++) {
        s0 += x[i];
        s1 += x[i + q];
        s2 += x[i + 2 * q];
        s3 += x[i + 3 * q];
    }
    T s = (s0 + s1) + (s2 + s3);
    for (size_t i = 4 * q; i < n; i++) s += x[i];
    return s;
}

NOINLINE void FN(bw_write)(T *restrict z, size_t n, T c) {
    for (size_t i = 0; i < n; i++) z[i] = c;  /* runtime c: not a memset idiom */
}

NOINLINE void FN(bw_scale)(T *restrict z, const T *restrict x, size_t n, T c) {
    for (size_t i = 0; i < n; i++) z[i] = c * x[i];
}

NOINLINE void FN(bw_triad)(T *restrict z, const T *restrict x, const T *restrict y, size_t n, T c) {
    for (size_t i = 0; i < n; i++) z[i] = x[i] + c * y[i];
}
