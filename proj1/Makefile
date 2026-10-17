# Build matrix: build/<compiler>-<config>/{bench,test_kernels,kernels_*.o,...}
#   make            build everything (10 kernel builds + common tools)
#   make test       C correctness tests (all builds) + PMU validation + pytest
#   make flags-json write results/env/flags.json
include config/flags.mk

PY      ?= $(CONDA_ENV)/bin/python
DTYPES  := f32 f64 i32
BUILDS  := $(foreach c,$(COMPILERS),$(foreach f,$(CONFIGS),$(c)-$(f)))
COMMON  := build/common

COMMON_OBJS := $(COMMON)/harness.o $(COMMON)/perfctr.o
HDRS        := src/kernels.h src/perfctr.h src/timing.h

.PHONY: all test test-c test-perfctr test-py flags-json clean
all: $(foreach b,$(BUILDS),build/$(b)/bench build/$(b)/test_kernels build/$(b)/kernels.objdump.txt) \
     $(COMMON)/calib_peak $(COMMON)/calib_align $(COMMON)/test_perfctr

$(COMMON):
	mkdir -p $@

$(COMMON)/%.o: src/%.c $(HDRS) | $(COMMON)
	$(FIXED_CC) $(FIXED_CFLAGS) -c $< -o $@

$(COMMON)/reference.o: tests/reference.c tests/reference.h src/kernels.h | $(COMMON)
	$(FIXED_CC) $(REF_CFLAGS) -Isrc -c $< -o $@

$(COMMON)/test_kernels.o: tests/test_kernels.c tests/reference.h src/kernels.h | $(COMMON)
	$(FIXED_CC) $(FIXED_CFLAGS) -Isrc -c $< -o $@

# Peak-FLOP calibration uses inline asm, so its flags don't affect the result;
# -march=native only so the assembler/OS path is enabled for zmm.
$(COMMON)/calib_peak: src/calib_peak.c src/perfctr.h src/timing.h $(COMMON)/perfctr.o
	$(FIXED_CC) $(FIXED_CFLAGS) -march=native $< $(COMMON)/perfctr.o -o $@ -lm

$(COMMON)/calib_align: src/calib_align.c src/perfctr.h $(COMMON)/perfctr.o
	$(FIXED_CC) $(FIXED_CFLAGS) -march=native $< $(COMMON)/perfctr.o -o $@

$(COMMON)/test_perfctr: tests/test_perfctr.c src/perfctr.h $(COMMON)/perfctr.o
	$(FIXED_CC) $(FIXED_CFLAGS) -march=native -Isrc $< $(COMMON)/perfctr.o -o $@ -lm

# ---- per-(compiler, config) kernel builds --------------------------------
define BUILD_RULES
# $(1)=compiler $(2)=config
build/$(1)-$(2):
	mkdir -p $$@

build/$(1)-$(2)/kernels_%.o: src/kernels_%.c src/kernels_body.inc src/kernels.h config/flags.mk | build/$(1)-$(2)
ifeq ($(1),gcc)
	$$(CC_gcc) $$(KERNEL_BASE) $$(CFG_gcc_$(2)) $$(REPORT_gcc) -c $$< -o $$@
else
	$$(CC_clang) $$(KERNEL_BASE) $$(CFG_clang_$(2)) $$(REPORT_clang) -c $$< -o $$@ \
	  2> $$(basename $$@).remarks.txt || (cat $$(basename $$@).remarks.txt; false)
endif

build/$(1)-$(2)/bench: $$(COMMON_OBJS) $(foreach d,$(DTYPES),build/$(1)-$(2)/kernels_$(d).o)
	$$(FIXED_CC) $$^ -o $$@ -lm

build/$(1)-$(2)/test_kernels: $$(COMMON)/test_kernels.o $$(COMMON)/reference.o \
                             $(foreach d,$(DTYPES),build/$(1)-$(2)/kernels_$(d).o)
	$$(FIXED_CC) $$^ -o $$@ -lm

build/$(1)-$(2)/kernels.objdump.txt: $(foreach d,$(DTYPES),build/$(1)-$(2)/kernels_$(d).o)
	objdump -d --no-show-raw-insn -M att $$^ > $$@
endef
$(foreach c,$(COMPILERS),$(foreach f,$(CONFIGS),$(eval $(call BUILD_RULES,$(c),$(f)))))

# ---- tests -----------------------------------------------------------------
test: test-c test-perfctr test-py

test-c: all
	@set -e; for b in $(BUILDS); do printf '%-22s ' $$b; ./build/$$b/test_kernels; done

test-perfctr: $(COMMON)/test_perfctr
	./$(COMMON)/test_perfctr

test-py: all
	$(PY) -m pytest -q tests

flags-json:
	@mkdir -p results/env
	@$(PY) scripts/dump_flags.py > results/env/flags.json
	@echo wrote results/env/flags.json

clean:
	rm -rf build

print-%:
	@echo '$($*)'
