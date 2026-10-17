/* E0/E3: cost of unaligned vector loads and stores, isolated from any kernel.
 *
 * 8 independent vmovups per iteration from one address at a given byte offset
 * from a 64-byte line (L1-resident, same line every time), for 256- and
 * 512-bit loads and 512-bit stores. Inline asm so codegen cannot change.
 * Reports core cycles per instruction and PMU MA64 (loads crossing a line).
 *
 * Output (CSV): op,bits,offset,cyc_per_insn,misal64_per_insn
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "perfctr.h"

#define IT 4000000L
#define R8(i) i i i i i i i i

static void ld_z(long it, char *p) {
    __asm__ volatile("1:\n\t" R8("vmovups (%1),%%zmm2\n\t") "dec %0\n\tjnz 1b\n\tvzeroupper"
                     : "+r"(it) : "r"(p) : "xmm2", "cc", "memory");
}
static void ld_y(long it, char *p) {
    __asm__ volatile("1:\n\t" R8("vmovups (%1),%%ymm2\n\t") "dec %0\n\tjnz 1b\n\tvzeroupper"
                     : "+r"(it) : "r"(p) : "xmm2", "cc", "memory");
}
static void st_z(long it, char *p) {
    __asm__ volatile("vxorps %%xmm2,%%xmm2,%%xmm2\n\t1:\n\t" R8("vmovups %%zmm2,(%1)\n\t")
                     "dec %0\n\tjnz 1b\n\tvzeroupper"
                     : "+r"(it) : "r"(p) : "xmm2", "cc", "memory");
}
static void st_y(long it, char *p) {
    __asm__ volatile("vxorps %%xmm2,%%xmm2,%%xmm2\n\t1:\n\t" R8("vmovups %%ymm2,(%1)\n\t")
                     "dec %0\n\tjnz 1b\n\tvzeroupper"
                     : "+r"(it) : "r"(p) : "xmm2", "cc", "memory");
}

int main(void) {
    char *buf = aligned_alloc(4096, 8192);
    memset(buf, 0, 8192);
    pc_group g;
    if (pc_open(&g, "cycles,misal_64") != 2) fprintf(stderr, "calib_align: PMU unavailable\n");
    static const struct { const char *op; int bits; void (*fn)(long, char *); } OPS[] = {
        {"load", 256, ld_y}, {"load", 512, ld_z}, {"store", 256, st_y}, {"store", 512, st_z}};
    static const int OFFS[] = {0, 4, 8, 16, 32, 48, 60};
    printf("op,bits,offset,cyc_per_insn,misal64_per_insn\n");
    for (size_t o = 0; o < sizeof OPS / sizeof OPS[0]; o++)
        for (size_t i = 0; i < sizeof OFFS / sizeof OFFS[0]; i++) {
            char *p = buf + 64 + OFFS[i];
            OPS[o].fn(IT / 10, p);
            uint64_t v[PC_MAX] = {0};
            pc_start(&g);
            OPS[o].fn(IT, p);
            pc_stop(&g, v);
            printf("%s,%d,%d,%.4f,%.4f\n", OPS[o].op, OPS[o].bits, OFFS[i],
                   (double)v[0] / (8.0 * IT), (double)v[1] / (8.0 * IT));
        }
    pc_close(&g);
    return 0;
}
