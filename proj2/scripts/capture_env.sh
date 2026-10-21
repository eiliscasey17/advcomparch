#!/usr/bin/env bash
# Record the machine / OS / build state the results depend on (adapted from proj1).
# Output: results/env/{environment.txt,cpuid_info.txt,pmu_validation.txt,unit_tests.txt,mlc_*.txt}
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-$HOME/miniconda3/envs/advcomparch/bin/python}
OUT=results/env
mkdir -p "$OUT"
make -s all
{
  echo "# captured $(date -Is) on $(hostname)  git $(git rev-parse --short HEAD 2>/dev/null || echo none)"
  echo "## CPU";            lscpu | grep -E 'Model name|Socket|Core\(s\)|Thread|NUMA node|L1d|L2|L3|max MHz|min MHz'
  echo "## frequency";      echo "governor=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor) boost=$(cat /sys/devices/system/cpu/cpufreq/boost 2>/dev/null)"
  echo "## memory";         grep -E 'MemTotal' /proc/meminfo
  for n in /sys/devices/system/node/node[0-9]*; do echo "$(basename $n): $(grep MemTotal $n/meminfo | awk '{print $4, $5}') cpus $(cat $n/cpulist)"; done
  echo "DIMMs (EDAC, mc0): $(ls -d /sys/devices/system/edac/mc/mc0/rank* 2>/dev/null | wc -l) ranks, type $(cat /sys/devices/system/edac/mc/mc0/rank*/dimm_mem_type 2>/dev/null | sort -u | tr '\n' ' ')"
  echo "## pages"
  echo "THP enabled=$(cat /sys/kernel/mm/transparent_hugepage/enabled)"
  echo "THP defrag=$(cat /sys/kernel/mm/transparent_hugepage/defrag)"
  echo "khugepaged defrag=$(cat /sys/kernel/mm/transparent_hugepage/khugepaged/defrag)"
  for d in /sys/kernel/mm/hugepages/hugepages-*; do echo "hugetlb $(basename $d): nr=$(cat $d/nr_hugepages)"; done
  echo "## kernel";         uname -r; echo "perf_event_paranoid=$(cat /proc/sys/kernel/perf_event_paranoid) nmi_watchdog=$(cat /proc/sys/kernel/nmi_watchdog)"
  echo "## compiler";       /usr/bin/gcc --version | head -1
  echo "flags: $(make -s -f config/flags.mk -f - <<<'p:; @echo CFLAGS=$(CFLAGS) KERNEL_CFLAGS=$(KERNEL_CFLAGS)' p)"
  echo "gcc -march=native resolves to:"; /usr/bin/gcc -march=native -Q --help=target | grep -E '^\s+-(march|mtune|mprefer-vector-width)='
  echo "## python";         $PY -c "import sys,numpy,pandas,matplotlib;print(sys.version.split()[0],'numpy',numpy.__version__,'pandas',pandas.__version__,'matplotlib',matplotlib.__version__)"
  echo "## MLC";            if [ -x tools/mlc/Linux/mlc ]; then (tools/mlc/Linux/mlc --version 2>&1 || true) | head -1; else echo "not installed (see README: tools/mlc)"; fi
} > "$OUT/environment.txt"
./build/membench info > "$OUT/cpuid_info.txt"
CORE=$($PY scripts/pick_core.py 2>/dev/null | head -1 || true)  # prints a core even when busy (exit 2)
CORE=${CORE:-62}
taskset -c "$CORE" ./build/test_perfctr > "$OUT/pmu_validation.txt" 2>&1 || true
taskset -c "$CORE" ./build/test_core > "$OUT/unit_tests.txt" 2>&1 || true
echo "wrote $OUT/{environment.txt,cpuid_info.txt,pmu_validation.txt,unit_tests.txt} (core $CORE)"
