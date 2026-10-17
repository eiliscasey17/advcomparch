# Single source of truth for compiler flags. `make flags-json` dumps these to
# results/env/flags.json so every result can be traced to exact flags.
#
# Kernel TUs (src/kernels_*.c) are the ONLY code compiled with per-config
# flags. Harness / tests / reference use FIXED_CFLAGS with GCC.

CONDA_ENV  ?= $(HOME)/miniconda3/envs/advcomparch
CC_gcc     := /usr/bin/gcc
CC_clang   := $(CONDA_ENV)/bin/clang

COMPILERS  := gcc clang
CONFIGS    := scalar default native256 native512 native512fm

# Applied to every kernel build. IEEE FP semantics (no fast-math) except the
# explicit *fm config. -Wno-unknown-pragmas: scalar builds deliberately ignore
# `omp simd`; GCC ignores `clang loop`.
KERNEL_BASE := -O3 -std=c11 -Wall -Wextra -Wno-unknown-pragmas

# scalar: same ISA/tuning as native, vectorizers off. NOTE: must NOT get
# -fopenmp-simd -- `#pragma omp simd` forces vectorization even with
# -fno-tree-vectorize (verified on GCC 11.5). Likewise Clang's
# `#pragma clang loop vectorize_predicate(enable)` forces vectorization under
# -fno-vectorize (verified on Clang 20.1.8), so -DKERNELS_SCALAR compiles that
# pragma out (src/kernels.h). tests/test_vectorization.py guards both.
CFG_gcc_scalar       := -march=native -fno-tree-vectorize -fno-tree-slp-vectorize -DKERNELS_SCALAR
CFG_gcc_default      := -fopenmp-simd
CFG_gcc_native256    := -march=native -fopenmp-simd
CFG_gcc_native512    := -march=native -mprefer-vector-width=512 -fopenmp-simd
CFG_gcc_native512fm  := $(CFG_gcc_native512) -ffast-math

CFG_clang_scalar      := -march=native -fno-vectorize -fno-slp-vectorize -DKERNELS_SCALAR
CFG_clang_default     := -fopenmp-simd
CFG_clang_native256   := -march=native -mprefer-vector-width=256 -fopenmp-simd
CFG_clang_native512   := -march=native -mprefer-vector-width=512 -fopenmp-simd
CFG_clang_native512fm := $(CFG_clang_native512) -ffast-math

# Vectorization reports (appended per kernel object).
REPORT_gcc   = -fopt-info-vec-all=$(basename $@).optinfo.txt
# (one -Rpass regex only: a second -Rpass= silently replaces the first)
REPORT_clang = -Rpass='loop-vectorize|slp-vectorizer' -Rpass-missed=loop-vectorize \
               -Rpass-analysis=loop-vectorize

FIXED_CC     := /usr/bin/gcc
FIXED_CFLAGS := -O2 -std=gnu11 -Wall -Wextra -g
# Reference implementations: no optimization, generic ISA, no FMA contraction.
REF_CFLAGS   := -O0 -std=gnu11 -ffp-contract=off -Wall
