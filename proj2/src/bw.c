#include "bw.h"

#include <immintrin.h>
#include <string.h>

const char *bw_check(const bw_cfg *c) {
    if (c->gran != 8 && c->gran != 16 && c->gran != 32 && c->gran != 64) return "gran must be 8/16/32/64";
    if (c->stride < (size_t)c->gran || c->stride % (size_t)c->gran) return "stride must be a multiple of gran";
    if (c->r < 0 || c->w < 0 || c->r + c->w < 1) return "bad r:w";
    if (c->delay < 0) return "bad delay";
    if (c->nslots < 1) return "empty buffer";
    return NULL;
}

static inline void spin(int d) {
    for (int k = 0; k < d; k++) __asm__ volatile("");
}

/* Bytes written by the write kernels (tests check for it). */
#define WBYTE 0xA5

/* One template per access granularity. OFF(s) is the byte offset of slot s.
 * Pure-read and pure-write segments without delay take an unrolled path with
 * 4 independent accumulators; everything else takes the generic loop. */
#define DEF_GRAN(G, VT, LOAD, XOR, STORE, STREAM, ZERO, SET1, RED)                               \
    static uint64_t seg_##G(const bw_cfg *c, char *base, size_t s0, size_t n, int ph) {          \
        const uint32_t *idx = c->idx;                                                            \
        const size_t st = c->stride;                                                             \
        VT a0 = ZERO, a1 = ZERO, a2 = ZERO, a3 = ZERO;                                           \
        const VT v = SET1;                                                                       \
        size_t i = 0;                                                                            \
        if (c->w == 0 && !c->delay) {                                                            \
            if (idx)                                                                             \
                for (; i + 4 <= n; i += 4) {                                                     \
                    a0 = XOR(a0, LOAD(base + (size_t)idx[s0 + i] * st));                         \
                    a1 = XOR(a1, LOAD(base + (size_t)idx[s0 + i + 1] * st));                     \
                    a2 = XOR(a2, LOAD(base + (size_t)idx[s0 + i + 2] * st));                     \
                    a3 = XOR(a3, LOAD(base + (size_t)idx[s0 + i + 3] * st));                     \
                }                                                                                \
            else                                                                                 \
                for (; i + 4 <= n; i += 4) {                                                     \
                    char *p = base + (s0 + i) * st;                                              \
                    a0 = XOR(a0, LOAD(p));                                                       \
                    a1 = XOR(a1, LOAD(p + st));                                                  \
                    a2 = XOR(a2, LOAD(p + 2 * st));                                              \
                    a3 = XOR(a3, LOAD(p + 3 * st));                                              \
                }                                                                                \
        } else if (c->r == 0 && !c->delay) {                                                     \
            if (c->nt) {                                                                         \
                if (idx)                                                                         \
                    for (; i < n; i++) STREAM(base + (size_t)idx[s0 + i] * st, v);               \
                else                                                                             \
                    for (; i < n; i++) STREAM(base + (s0 + i) * st, v);                          \
            } else {                                                                             \
                if (idx)                                                                         \
                    for (; i < n; i++) STORE(base + (size_t)idx[s0 + i] * st, v);                \
                else                                                                             \
                    for (; i < n; i++) STORE(base + (s0 + i) * st, v);                           \
            }                                                                                    \
        }                                                                                        \
        const int period = c->r + c->w;                                                          \
        ph = (int)((ph + i) % (size_t)period);                                                   \
        for (; i < n; i++) {                                                                     \
            char *p = base + (idx ? (size_t)idx[s0 + i] : s0 + i) * st;                          \
            if (ph < c->r)                                                                       \
                a0 = XOR(a0, LOAD(p));                                                           \
            else if (c->nt)                                                                      \
                STREAM(p, v);                                                                    \
            else                                                                                 \
                STORE(p, v);                                                                     \
            if (++ph == period) ph = 0;                                                          \
            spin(c->delay);                                                                      \
        }                                                                                        \
        return RED(XOR(XOR(a0, a1), XOR(a2, a3)));                                               \
    }

#define X64(a, b) _mm512_xor_si512((a), (b))
#define X32(a, b) _mm256_xor_si256((a), (b))
#define X16(a, b) _mm_xor_si128((a), (b))
#define X8(a, b) ((a) ^ (b))
#define L64(p) _mm512_load_si512((const void *)(p))
#define L32(p) _mm256_load_si256((const __m256i *)(p))
#define L16(p) _mm_load_si128((const __m128i *)(p))
#define L8(p) (*(volatile const uint64_t *)(p))
#define S64(p, v) _mm512_store_si512((void *)(p), (v))
#define S32(p, v) _mm256_store_si256((__m256i *)(p), (v))
#define S16(p, v) _mm_store_si128((__m128i *)(p), (v))
#define S8(p, v) (*(volatile uint64_t *)(p) = (v))
#define N64(p, v) _mm512_stream_si512((void *)(p), (v))
#define N32(p, v) _mm256_stream_si256((__m256i *)(p), (v))
#define N16(p, v) _mm_stream_si128((__m128i *)(p), (v))
#define N8(p, v) _mm_stream_si64((long long *)(p), (long long)(v))
#define R64(a) ((uint64_t)_mm512_reduce_add_epi64(a))
#define R32(a)                                                                     \
    ((uint64_t)_mm256_extract_epi64((a), 0) + (uint64_t)_mm256_extract_epi64((a), 1) + \
     (uint64_t)_mm256_extract_epi64((a), 2) + (uint64_t)_mm256_extract_epi64((a), 3))
#define R16(a) ((uint64_t)_mm_cvtsi128_si64(a) + (uint64_t)_mm_extract_epi64((a), 1))
#define R8(a) (a)

DEF_GRAN(64, __m512i, L64, X64, S64, N64, _mm512_setzero_si512(), _mm512_set1_epi8((char)WBYTE), R64)
DEF_GRAN(32, __m256i, L32, X32, S32, N32, _mm256_setzero_si256(), _mm256_set1_epi8((char)WBYTE), R32)
DEF_GRAN(16, __m128i, L16, X16, S16, N16, _mm_setzero_si128(), _mm_set1_epi8((char)WBYTE), R16)
DEF_GRAN(8, uint64_t, L8, X8, S8, N8, 0, 0xA5A5A5A5A5A5A5A5ull, R8)

uint64_t bw_run(const bw_cfg *c, char *base, size_t first, size_t count) {
    uint64_t acc = 0;
    size_t s = first % c->nslots;
    int ph = (int)(first % (size_t)(c->r + c->w));
    while (count) {
        size_t n = c->nslots - s < count ? c->nslots - s : count;
        switch (c->gran) {
        case 64: acc += seg_64(c, base, s, n, ph); break;
        case 32: acc += seg_32(c, base, s, n, ph); break;
        case 16: acc += seg_16(c, base, s, n, ph); break;
        default: acc += seg_8(c, base, s, n, ph); break;
        }
        ph = (int)((ph + n) % (size_t)(c->r + c->w));
        count -= n;
        s = 0;
    }
    if (c->nt && c->w) _mm_sfence();
    return acc;
}

static uint64_t xs(uint64_t *s) {
    uint64_t x = *s;
    x ^= x >> 12;
    x ^= x << 25;
    x ^= x >> 27;
    *s = x;
    return x * 0x2545F4914F6CDD1Dull;
}

void bw_perm(uint32_t *idx, size_t n, uint64_t seed) {
    for (size_t i = 0; i < n; i++) idx[i] = (uint32_t)i;
    uint64_t s = seed ? seed : 0x243F6A8885A308D3ull;
    for (size_t i = n - 1; i > 0; i--) {
        size_t j = xs(&s) % (i + 1);
        uint32_t t = idx[i];
        idx[i] = idx[j];
        idx[j] = t;
    }
}
