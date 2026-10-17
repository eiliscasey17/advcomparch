/* E0: measured peak FP throughput and FP latencies, single core.
 *
 * Throughput: 12 independent FMA accumulators (> latency 4 x 2 pipes = 8 in
 * flight) per width (scalar / xmm / ymm / zmm) and dtype -> FLOP/cycle.
 * Latency: a dependent chain of one instruction -> cycles per instruction.
 * Inline asm, so the compiler and its flags cannot change what is measured.
 * Cycles are core cycles from the PMU (boost-aware); GHz = cycles / ns.
 *
 * Output (CSV): test,instr,dtype,width_bits,lanes,ns,cycles,ghz,per_cycle
 *   per_cycle = FLOP/cycle for test=peak, cycles/instr for test=latency.
 */
#include <stdio.h>
#include <stdlib.h>

#include "perfctr.h"
#include "timing.h"

#define ITERS 20000000L

#define F(op, r, i) op " %%" r "12, %%" r "13, %%" r #i "\n\t"
#define F12(op, r)                                                                       \
    F(op, r, 0) F(op, r, 1) F(op, r, 2) F(op, r, 3) F(op, r, 4) F(op, r, 5) F(op, r, 6) \
    F(op, r, 7) F(op, r, 8) F(op, r, 9) F(op, r, 10) F(op, r, 11)
#define CHAIN(op, r) /* 8 dependent ops on register 0 */                                  \
    op " %%" r "12, %%" r "0, %%" r "0\n\t" op " %%" r "12, %%" r "0, %%" r "0\n\t"         \
    op " %%" r "12, %%" r "0, %%" r "0\n\t" op " %%" r "12, %%" r "0, %%" r "0\n\t"         \
    op " %%" r "12, %%" r "0, %%" r "0\n\t" op " %%" r "12, %%" r "0, %%" r "0\n\t"         \
    op " %%" r "12, %%" r "0, %%" r "0\n\t" op " %%" r "12, %%" r "0, %%" r "0\n\t"
#define ZERO "vxorps %%xmm0,%%xmm0,%%xmm0\n\tvxorps %%xmm12,%%xmm12,%%xmm12\n\tvxorps %%xmm13,%%xmm13,%%xmm13\n\t"
#define CLOBS "xmm0", "xmm1", "xmm2", "xmm3", "xmm4", "xmm5", "xmm6", "xmm7", "xmm8", \
              "xmm9", "xmm10", "xmm11", "xmm12", "xmm13", "cc"
#define LOOP_FN(name, body)                                                         \
    static void name(long it) {                                                     \
        __asm__ volatile(ZERO "1:\n\t" body "dec %0\n\tjnz 1b\n\tvzeroupper\n\t"    \
                         : "+r"(it)::CLOBS);                                        \
    }

LOOP_FN(fma_ss, F12("vfmadd231ss", "xmm"))
LOOP_FN(fma_ps_x, F12("vfmadd231ps", "xmm"))
LOOP_FN(fma_ps_y, F12("vfmadd231ps", "ymm"))
LOOP_FN(fma_ps_z, F12("vfmadd231ps", "zmm"))
LOOP_FN(fma_sd, F12("vfmadd231sd", "xmm"))
LOOP_FN(fma_pd_x, F12("vfmadd231pd", "xmm"))
LOOP_FN(fma_pd_y, F12("vfmadd231pd", "ymm"))
LOOP_FN(fma_pd_z, F12("vfmadd231pd", "zmm"))
LOOP_FN(add_ps_z, F12("vaddps", "zmm"))
LOOP_FN(mul_ps_z, F12("vmulps", "zmm"))
LOOP_FN(add_ps_y, F12("vaddps", "ymm"))
LOOP_FN(pmulld_z, F12("vpmulld", "zmm"))
LOOP_FN(pmulld_y, F12("vpmulld", "ymm"))
LOOP_FN(paddd_z, F12("vpaddd", "zmm"))

LOOP_FN(lat_addss, CHAIN("vaddss", "xmm"))
LOOP_FN(lat_addps_z, CHAIN("vaddps", "zmm"))
LOOP_FN(lat_addps_y, CHAIN("vaddps", "ymm"))
LOOP_FN(lat_mulps_z, CHAIN("vmulps", "zmm"))
LOOP_FN(lat_fmaps_z, CHAIN("vfmadd231ps", "zmm"))
LOOP_FN(lat_paddd_z, CHAIN("vpaddd", "zmm"))
LOOP_FN(lat_pmulld_z, CHAIN("vpmulld", "zmm"))

typedef struct {
    const char *test, *instr, *dtype;
    int bits, lanes;
    double flop_per_instr; /* 0 for latency tests */
    int per_iter;          /* instructions per loop iteration */
    void (*fn)(long);
} case_t;

static const case_t CASES[] = {
    {"peak", "vfmadd231ss", "f32", 32, 1, 2, 12, fma_ss},
    {"peak", "vfmadd231ps", "f32", 128, 4, 8, 12, fma_ps_x},
    {"peak", "vfmadd231ps", "f32", 256, 8, 16, 12, fma_ps_y},
    {"peak", "vfmadd231ps", "f32", 512, 16, 32, 12, fma_ps_z},
    {"peak", "vfmadd231sd", "f64", 64, 1, 2, 12, fma_sd},
    {"peak", "vfmadd231pd", "f64", 128, 2, 4, 12, fma_pd_x},
    {"peak", "vfmadd231pd", "f64", 256, 4, 8, 12, fma_pd_y},
    {"peak", "vfmadd231pd", "f64", 512, 8, 16, 12, fma_pd_z},
    {"peak", "vaddps", "f32", 512, 16, 16, 12, add_ps_z},
    {"peak", "vaddps", "f32", 256, 8, 8, 12, add_ps_y},
    {"peak", "vmulps", "f32", 512, 16, 16, 12, mul_ps_z},
    {"peak", "vpmulld", "i32", 512, 16, 16, 12, pmulld_z},
    {"peak", "vpmulld", "i32", 256, 8, 8, 12, pmulld_y},
    {"peak", "vpaddd", "i32", 512, 16, 16, 12, paddd_z},
    {"latency", "vaddss", "f32", 32, 1, 0, 8, lat_addss},
    {"latency", "vaddps", "f32", 256, 8, 0, 8, lat_addps_y},
    {"latency", "vaddps", "f32", 512, 16, 0, 8, lat_addps_z},
    {"latency", "vmulps", "f32", 512, 16, 0, 8, lat_mulps_z},
    {"latency", "vfmadd231ps", "f32", 512, 16, 0, 8, lat_fmaps_z},
    {"latency", "vpaddd", "i32", 512, 16, 0, 8, lat_paddd_z},
    {"latency", "vpmulld", "i32", 512, 16, 0, 8, lat_pmulld_z},
};

int main(void) {
    pc_group g;
    int have = pc_open(&g, "cycles");
    if (!have) fprintf(stderr, "calib_peak: no PMU cycles; per_cycle will be NaN\n");
    printf("test,instr,dtype,width_bits,lanes,ns,cycles,ghz,per_cycle\n");
    for (size_t i = 0; i < sizeof CASES / sizeof CASES[0]; i++) {
        const case_t *c = &CASES[i];
        c->fn(ITERS / 10);
        double best = 0;
        uint64_t best_cyc = 0, best_ns = 0;
        for (int rep = 0; rep < 5; rep++) { /* keep the fastest of 5 */
            uint64_t v[PC_MAX] = {0};
            pc_start(&g);
            uint64_t a = now_ns();
            c->fn(ITERS);
            uint64_t b = now_ns();
            pc_stop(&g, v);
            double ninstr = (double)ITERS * c->per_iter;
            double val = c->flop_per_instr ? ninstr * c->flop_per_instr / (double)v[0]
                                           : (double)v[0] / ninstr;
            int better = c->flop_per_instr ? val > best : (best == 0 || val < best);
            if (better) { best = val; best_cyc = v[0]; best_ns = b - a; }
        }
        printf("%s,%s,%s,%d,%d,%llu,%llu,%.4f,%.4f\n", c->test, c->instr, c->dtype, c->bits,
               c->lanes, (unsigned long long)best_ns, (unsigned long long)best_cyc,
               (double)best_cyc / (double)best_ns, best);
    }
    pc_close(&g);
    return 0;
}
