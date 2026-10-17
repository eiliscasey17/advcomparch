/* Per-dtype correctness tests, included by test_kernels.c with
 * T, SFX, TEPS (machine epsilon; 0 for ints) and IS_INT defined. */

#define K(name) CAT(name, SFX)
#define R(name) CAT(ref_##name, SFX)
#define TNAME STR(SFX)

/* Allocate n elements with GUARD canary elements on each side; the returned
 * pointer is `off` elements past a 64-byte boundary. Everything (including the
 * payload) is pre-filled with the canary byte. */
static T *K(galloc)(size_t n, size_t off, void **base_out) {
    size_t bytes = (2 * GUARD + off + n) * sizeof(T);
    void *base = aligned_alloc(64, (bytes + 63) & ~(size_t)63);
    memset(base, CANARY, bytes);
    *base_out = base;
    return (T *)base + GUARD + off;
}

/* canaries before p (incl. alignment pad) and after p+n must be untouched */
static int K(guards_ok)(const T *p, size_t n, size_t off) {
    const unsigned char *pre = (const unsigned char *)(p - GUARD - off);
    const unsigned char *post = (const unsigned char *)(p + n);
    for (size_t i = 0; i < (GUARD + off) * sizeof(T); i++)
        if (pre[i] != CANARY) return 0;
    for (size_t i = 0; i < GUARD * sizeof(T); i++)
        if (post[i] != CANARY) return 0;
    return 1;
}

static int K(is_canary)(const T *p) {
    const unsigned char *b = (const unsigned char *)p;
    for (size_t i = 0; i < sizeof(T); i++)
        if (b[i] != CANARY) return 0;
    return 1;
}

static void K(fill)(T *p, size_t n, unsigned seed) {
    srand(seed);
    for (size_t i = 0; i < n; i++) {
#if IS_INT
        p[i] = (T)(rand() % 5) - 2; /* [-2, 2]: no i32 overflow */
#else
        p[i] = (T)(0.5 + (double)rand() / RAND_MAX); /* [0.5, 1.5]: no denormals */
#endif
    }
}

static int K(close)(long double got, long double want, long double mag, size_t nterms) {
#if IS_INT
    (void)mag; (void)nterms;
    return got == want;
#else
    long double tol = (long double)(nterms + 2) * TEPS * mag + TEPS;
    long double d = got - want;
    return (d < 0 ? -d : d) <= tol;
#endif
}

typedef T (*K(dot_fn))(const T *restrict, const T *restrict, size_t);
typedef void (*K(mul_fn))(T *restrict, const T *restrict, const T *restrict, size_t);

/* ----- dot family (unit stride and compile-time stride) ----- */
static void K(test_dot)(const char *name, K(dot_fn) f, size_t stride, size_t n, size_t ox,
                        size_t oy) {
    void *bx, *by;
    size_t span = n * stride;
    T *x = K(galloc)(span, ox, &bx), *y = K(galloc)(span, oy, &by);
    K(fill)(x, span, 1 + (unsigned)n);
    K(fill)(y, span, 2 + (unsigned)n);
    long double mag;
    long double want = R(dot)(x, stride, NULL, y, stride, n, &mag);
    T got = f(x, y, n);
    CHECK(K(close)(got, want, mag, n), "%s_%s n=%zu off=(%zu,%zu): got %.10Lg want %.10Lg", name,
          TNAME, n, ox, oy, (long double)got, want);
    CHECK(K(guards_ok)(x, span, ox) && K(guards_ok)(y, span, oy), "%s_%s n=%zu: input guard",
          name, TNAME, n);
    free(bx);
    free(by);
}

/* ----- mul family ----- */
static void K(test_mul)(const char *name, K(mul_fn) f, size_t stride, size_t n, size_t oz,
                        size_t ox, size_t oy) {
    void *bz, *bx, *by, *br;
    size_t span = n * stride;
    T *x = K(galloc)(span, ox, &bx), *y = K(galloc)(span, oy, &by);
    T *z = K(galloc)(n, oz, &bz), *ref = K(galloc)(n, 0, &br);
    K(fill)(x, span, 3 + (unsigned)n);
    K(fill)(y, span, 4 + (unsigned)n);
    R(mul)(ref, x, stride, NULL, y, stride, n);
    f(z, x, y, n);
    size_t bad = n;
    for (size_t i = 0; i < n; i++)
        if (z[i] != ref[i]) { bad = i; break; }
    CHECK(bad == n, "%s_%s n=%zu off=(%zu,%zu,%zu): first mismatch at %zu", name, TNAME, n, oz,
          ox, oy, bad);
    CHECK(K(guards_ok)(z, n, oz), "%s_%s n=%zu off=%zu: OUT-OF-BOUNDS WRITE", name, TNAME, n, oz);
    free(bz); free(bx); free(by); free(br);
}

/* ----- gather (dot + mul) ----- */
static void K(test_gather)(int pat, size_t n, size_t off) {
    size_t span = pat == PAT_STRIDE3 ? 3 * n + 1 : n + 1;
    void *bx, *by, *bz, *br;
    T *x = K(galloc)(span, off, &bx), *y = K(galloc)(n, off, &by);
    T *z = K(galloc)(n, off, &bz), *ref = K(galloc)(n, 0, &br);
    int32_t *idx = malloc((n + 1) * sizeof *idx);
    K(fill)(x, span, 5 + (unsigned)n);
    K(fill)(y, n, 6 + (unsigned)n);
    for (size_t i = 0; i < n; i++) idx[i] = pat == PAT_STRIDE3 ? (int32_t)(3 * i) : (int32_t)i;
    if (pat == PAT_RANDOM)
        for (size_t i = n; i > 1; i--) { /* Fisher-Yates */
            size_t j = (size_t)rand() % i;
            int32_t t = idx[i - 1]; idx[i - 1] = idx[j]; idx[j] = t;
        }
    long double mag;
    long double want = R(dot)(x, 0, idx, y, 1, n, &mag);
    T got = K(dot_gather)(x, idx, y, n);
    CHECK(K(close)(got, want, mag, n), "dot_gather_%s pat=%d n=%zu: got %.10Lg want %.10Lg",
          TNAME, pat, n, (long double)got, want);
    R(mul)(ref, x, 0, idx, y, 1, n);
    K(mul_gather)(z, x, idx, y, n);
    size_t bad = n;
    for (size_t i = 0; i < n; i++)
        if (z[i] != ref[i]) { bad = i; break; }
    CHECK(bad == n, "mul_gather_%s pat=%d n=%zu: first mismatch at %zu", TNAME, pat, n, bad);
    CHECK(K(guards_ok)(z, n, off), "mul_gather_%s n=%zu: OUT-OF-BOUNDS WRITE", TNAME, n);
    free(bx); free(by); free(bz); free(br); free(idx);
}

/* ----- stencil ----- */
static void K(test_stencil)(size_t n, size_t oy, size_t ox) {
    void *by, *bx;
    T *x = K(galloc)(n, ox, &bx), *y = K(galloc)(n, oy, &by);
    K(fill)(x, n, 7 + (unsigned)n);
#if IS_INT
    T a = 1, b = -2, c = 3;
#else
    T a = (T)0.25, b = (T)0.5, c = (T)0.25;
#endif
    long double *want = calloc(n + 1, sizeof *want), *mag = calloc(n + 1, sizeof *mag);
    R(stencil)(want, mag, x, n, a, b, c);
    K(stencil)(y, x, n, a, b, c);
    size_t bad = n;
    for (size_t i = 1; i + 1 < n; i++)
        if (!K(close)(y[i], want[i], mag[i], 2)) { bad = i; break; }
    CHECK(bad == n, "stencil_%s n=%zu off=(%zu,%zu): mismatch at %zu", TNAME, n, oy, ox, bad);
    if (n >= 1)
        CHECK(K(is_canary)(&y[0]) && K(is_canary)(&y[n - 1]),
              "stencil_%s n=%zu: wrote a boundary element", TNAME, n);
    if (n < 3)
        for (size_t i = 0; i < n; i++)
            CHECK(K(is_canary)(&y[i]), "stencil_%s n=%zu: wrote y[%zu] (n<3)", TNAME, n, i);
    CHECK(K(guards_ok)(y, n, oy), "stencil_%s n=%zu: OUT-OF-BOUNDS WRITE", TNAME, n);
    free(bx); free(by); free(want); free(mag);
}

/* ----- bandwidth-calibration kernels ----- */
static void K(test_bw)(size_t n, size_t off) {
    void *bx, *by, *bz;
    T *x = K(galloc)(n, off, &bx), *y = K(galloc)(n, off, &by), *z = K(galloc)(n, off, &bz);
    K(fill)(x, n, 8 + (unsigned)n);
    K(fill)(y, n, 9 + (unsigned)n);
    long double mag, want = R(sum)(x, n, &mag);
    CHECK(K(close)(K(bw_read)(x, n), want, mag, n), "bw_read_%s n=%zu", TNAME, n);
    T c = (T)3;
    K(bw_write)(z, n, c);
    for (size_t i = 0; i < n; i++)
        if (z[i] != c) { CHECK(0, "bw_write_%s n=%zu at %zu", TNAME, n, i); break; }
    K(bw_scale)(z, x, n, c);
    for (size_t i = 0; i < n; i++)
        if (z[i] != (T)(c * x[i])) { CHECK(0, "bw_scale_%s n=%zu at %zu", TNAME, n, i); break; }
    K(bw_triad)(z, x, y, n, c);
    for (size_t i = 0; i < n; i++) {
        long double w = (long double)x[i] + (long double)c * y[i];
        long double m = (x[i] < 0 ? -x[i] : x[i]) + (long double)c * (y[i] < 0 ? -y[i] : y[i]);
        if (!K(close)(z[i], w, m, 1)) { CHECK(0, "bw_triad_%s n=%zu at %zu", TNAME, n, i); break; }
    }
    CHECK(K(guards_ok)(z, n, off), "bw_*_%s n=%zu: OUT-OF-BOUNDS WRITE", TNAME, n);
    free(bx); free(by); free(bz);
}

static void K(run_all)(void) {
    static const struct { const char *name; K(dot_fn) f; size_t stride; } DOTS[] = {
        {"dot_simd", K(dot_simd), 1}, {"dot_plain", K(dot_plain), 1},
        {"dot_acc2", K(dot_acc2), 1}, {"dot_acc4", K(dot_acc4), 1},
        {"dot_acc8", K(dot_acc8), 1}, {"dot_s2", K(dot_s2), 2},
        {"dot_s4", K(dot_s4), 4},     {"dot_s8", K(dot_s8), 8},
    };
    static const struct { const char *name; K(mul_fn) f; size_t stride; } MULS[] = {
        {"mul", K(mul), 1},       {"mul_pred", K(mul_pred), 1}, {"mul_s2", K(mul_s2), 2},
        {"mul_s4", K(mul_s4), 4}, {"mul_s8", K(mul_s8), 8},
    };
    for (size_t si = 0; si < NSIZES; si++) {
        size_t n = SIZES[si];
        for (size_t oi = 0; oi < NOFFS; oi++) {
            size_t o = OFFS[oi];
            for (size_t k = 0; k < sizeof DOTS / sizeof DOTS[0]; k++)
                K(test_dot)(DOTS[k].name, DOTS[k].f, DOTS[k].stride, n, o, o);
            for (size_t k = 0; k < sizeof MULS / sizeof MULS[0]; k++)
                K(test_mul)(MULS[k].name, MULS[k].f, MULS[k].stride, n, o, o, o);
            K(test_stencil)(n, o, o);
            K(test_bw)(n, o);
            for (int pat = 0; pat < 3; pat++) K(test_gather)(pat, n, o);
        }
        /* mixed relative misalignment: arrays on different offsets */
        K(test_dot)("dot_simd", K(dot_simd), 1, n, 0, 1);
        K(test_mul)("mul", K(mul), 1, n, 2, 0, 1);
        K(test_stencil)(n, 1, 0);
        /* the aligned-promise variant is only legal on 64B-aligned pointers */
        K(test_mul)("mul_aligned", K(mul_aligned), 1, n, 0, 0, 0);
    }
}

#undef K
#undef R
#undef TNAME
