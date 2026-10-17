/* Correctness tests for every kernel x dtype, against the -O0 reference.
 *
 * Linked once per (compiler, config) build, so the SAME test exercises the
 * scalar, SSE, AVX2 and AVX-512 code paths (including vector epilogues,
 * peeled prologues and masked tails) at sizes around every vector width, at
 * every alignment offset, with canary guard bands to catch tail overruns.
 */
#include <float.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "kernels.h"
#include "reference.h"

#define GUARD 64
#define CANARY 0xA5
#define STR_(x) #x
#define STR(x) STR_(x)

static const size_t SIZES[] = {0,  1,  2,  3,   7,   8,   15,   16,   17,   31,   32,  33,
                               63, 64, 65, 127, 128, 129, 1000, 1023, 1024, 1025, 4109};
#define NSIZES (sizeof SIZES / sizeof SIZES[0])
/* element offsets from a 64B boundary (f32: 0..60 bytes) */
static const size_t OFFS[] = {0, 1, 2, 3, 5, 7, 8, 15};
#define NOFFS (sizeof OFFS / sizeof OFFS[0])

enum { PAT_IDENTITY, PAT_STRIDE3, PAT_RANDOM }; /* gather index patterns */

static long checks, fails;
#define CHECK(cond, ...)                                       \
    do {                                                       \
        checks++;                                              \
        if (!(cond)) {                                         \
            if (++fails <= 20) {                               \
                fprintf(stderr, "FAIL: " __VA_ARGS__);         \
                fputc('\n', stderr);                           \
            }                                                  \
        }                                                      \
    } while (0)

#define T f32
#define SFX f32
#define TEPS FLT_EPSILON
#define IS_INT 0
#include "test_body.inc"
#undef T
#undef SFX
#undef TEPS
#undef IS_INT

#define T f64
#define SFX f64
#define TEPS DBL_EPSILON
#define IS_INT 0
#include "test_body.inc"
#undef T
#undef SFX
#undef TEPS
#undef IS_INT

#define T i32
#define SFX i32
#define TEPS 0
#define IS_INT 1
#include "test_body.inc"

int main(void) {
    run_all_f32();
    run_all_f64();
    run_all_i32();
    printf("%ld checks, %ld failed\n", checks, fails);
    return fails ? 1 : 0;
}
