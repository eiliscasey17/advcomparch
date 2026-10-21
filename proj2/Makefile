# proj2 build: build/{membench,test_core,test_perfctr}
#   make          build everything
#   make test     C unit tests + PMU validation + pytest
include config/flags.mk

PY   ?= $(CONDA_ENV)/bin/python
B    := build
OBJS := $(B)/mem.o $(B)/chase.o $(B)/bw.o $(B)/perfctr.o $(B)/kernels.o
HDRS := $(wildcard src/*.h)

.PHONY: all test test-c test-perfctr test-py clean
all: $(B)/membench $(B)/test_core $(B)/test_perfctr $(B)/kernels.objdump.txt

$(B):
	mkdir -p $@

$(B)/kernels.o: src/kernels.c src/kernels.h config/flags.mk | $(B)
	$(CC) $(KERNEL_CFLAGS) -c $< -o $@

$(B)/%.o: src/%.c $(HDRS) config/flags.mk | $(B)
	$(CC) $(CFLAGS) -c $< -o $@

$(B)/membench: $(B)/bench.o $(OBJS)
	$(CC) $(CFLAGS) $^ -o $@ $(LDLIBS)

$(B)/test_core: tests/test_core.c $(OBJS) $(HDRS)
	$(CC) $(CFLAGS) -Isrc $< $(OBJS) -o $@ $(LDLIBS)

$(B)/test_perfctr: tests/test_perfctr.c $(OBJS) $(HDRS)
	$(CC) $(CFLAGS) -Isrc $< $(OBJS) -o $@ $(LDLIBS)

$(B)/kernels.objdump.txt: $(B)/kernels.o
	objdump -d --no-show-raw-insn $< > $@

# Unit tests run pinned to one cpu of node 1's last CCD by default; override
# with TEST_CPU=<n> (keep them off cores used by a running experiment).
TEST_CPU ?= 62
test: test-c test-perfctr test-py
test-c: $(B)/test_core
	taskset -c $(TEST_CPU) ./$(B)/test_core
test-perfctr: $(B)/test_perfctr
	taskset -c $(TEST_CPU) ./$(B)/test_perfctr
test-py: $(B)/membench
	$(PY) -m pytest -q tests

clean:
	rm -rf $(B)
