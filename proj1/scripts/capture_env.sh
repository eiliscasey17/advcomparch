#!/usr/bin/env bash
# Record the machine / compiler / OS state that the results depend on.
# Output: results/env/{environment.txt,flags.json,pmu_validation.txt}
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-$HOME/miniconda3/envs/advcomparch/bin/python}
OUT=results/env
mkdir -p "$OUT"
{
  echo "# captured $(date -Is) on $(hostname)  git $(git rev-parse --short HEAD 2>/dev/null || echo none)"
  echo "## CPU";            lscpu | grep -E 'Model name|Socket|Core\(s\)|Thread|NUMA node|L1d|L2|L3|max MHz|min MHz'
  echo "AVX-512 flags: $(grep -m1 '^flags' /proc/cpuinfo | grep -o 'avx512[a-z_0-9]*' | tr '\n' ' ')"
  echo "## frequency";      echo "governor=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor) boost=$(cat /sys/devices/system/cpu/cpufreq/boost 2>/dev/null)"
  echo "## memory";         grep -E 'MemTotal' /proc/meminfo; echo "THP=$(cat /sys/kernel/mm/transparent_hugepage/enabled)"
  echo "## kernel";         uname -r; echo "perf_event_paranoid=$(cat /proc/sys/kernel/perf_event_paranoid) nmi_watchdog=$(cat /proc/sys/kernel/nmi_watchdog)"
  echo "## compilers";      /usr/bin/gcc --version | head -1; $HOME/miniconda3/envs/advcomparch/bin/clang --version | head -1
  echo "gcc -march=native resolves to:"; /usr/bin/gcc -march=native -Q --help=target | grep -E '^\s+-(march|mtune|mprefer-vector-width)='
  echo "## python";         $PY -c "import sys,numpy,pandas,matplotlib;print(sys.version.split()[0],'numpy',numpy.__version__,'pandas',pandas.__version__,'matplotlib',matplotlib.__version__)"
} > "$OUT/environment.txt"
$PY scripts/dump_flags.py > "$OUT/flags.json"
CORE=$($PY scripts/pick_core.py || true)
taskset -c "${CORE:-1}" ./build/common/test_perfctr > "$OUT/pmu_validation.txt" 2>&1 || true
taskset -c "${CORE:-1}" ./build/common/calib_peak > "$OUT/calib_peak_snapshot.csv"
echo "wrote $OUT/{environment.txt,flags.json,pmu_validation.txt,calib_peak_snapshot.csv}"
