# Single source of truth for compiler flags (recorded in results/env/environment.txt).
CONDA_ENV ?= $(HOME)/miniconda3/envs/advcomparch
CC        := /usr/bin/gcc
# Harness, PMU wrapper, chase/bw probes, tests. -march=native for AVX-512
# loads/stores in the bandwidth kernels (gcc 11.5 resolves it to znver4).
CFLAGS    := -O2 -std=gnu11 -march=native -Wall -Wextra -g -pthread
# The E6/E7 loop kernels: the "normal optimized build" a user would measure.
KERNEL_CFLAGS := -O3 -std=gnu11 -march=native -Wall -Wextra
LDLIBS    := -lm -pthread
