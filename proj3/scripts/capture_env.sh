#!/usr/bin/env bash
# Record the storage stack / OS / tool state the results depend on.
# Output: results/env/{environment.txt,datafiles.txt,unit_tests.txt}
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-$HOME/miniconda3/envs/advcomparch/bin/python}
FIO=${FIO:-$HOME/miniconda3/envs/advcomparch/bin/fio}
OUT=results/env
mkdir -p "$OUT"
{
  echo "# captured $(date -Is) on $(hostname)  git $(git rev-parse --short HEAD 2>/dev/null || echo none)"
  echo "## tools";   $FIO --version; echo "libaio engine: $($FIO --enghelp 2>&1 | grep -c libaio)"
  echo "io_uring_disabled=$(cat /proc/sys/kernel/io_uring_disabled) (2 = io_uring unavailable to everyone)"
  echo "## kernel";  uname -r
  echo "## /data";   df -hT /data | tail -1
  xfs_info /data | sed -n '1,4p'
  echo "## md124";   grep -A2 '^md124' /proc/mdstat
  echo "chunk=$(cat /sys/block/md124/md/chunk_size) level=$(cat /sys/block/md124/md/level) raid_disks=$(cat /sys/block/md124/md/raid_disks) stripe_cache_size=$(cat /sys/block/md124/md/stripe_cache_size) group_thread_cnt=$(cat /sys/block/md124/md/group_thread_cnt)"
  echo "## member drives"
  # from sysfs: NVMe names change across reboots (nvme1n1 became nvme2n1 on 2026-10-22)
  for d in $(ls /sys/block/md124/slaves | sed 's/p[0-9]*$//'); do
    # controller of this namespace, from sysfs (namespace and controller numbers need not match)
    c=$(basename "$(readlink -f /sys/block/$d/device)")
    pci=/sys/class/nvme/$c/device
    echo "$d ctrl=$c model=$(cat /sys/block/$d/device/model | xargs) fw=$(cat /sys/block/$d/device/firmware_rev | xargs) size=$(( $(cat /sys/block/$d/size) * 512 / 1000000000 ))GB sched=$(cat /sys/block/$d/queue/scheduler) nr_requests=$(cat /sys/block/$d/queue/nr_requests) numa=$(cat /sys/class/nvme/$c/numa_node) hw_queues=$(cat /sys/class/nvme/$c/queue_count) sqsize=$(cat /sys/class/nvme/$c/sqsize) pcie=$(cat $pci/current_link_speed | tr -d ' ')x$(cat $pci/current_link_width) (max $(cat $pci/max_link_speed | tr -d ' ')x$(cat $pci/max_link_width))"
  done
  echo "## health / temperature"
  smartctl -a /dev/nvme1 2>&1 | head -3 | tail -1 || true
  echo "nvme hwmon sensors visible: $(grep -l nvme /sys/class/hwmon/*/name 2>/dev/null | wc -l) (temperature/throttling not observable without root)"
  echo "## memory";  grep -E 'MemTotal|Dirty:' /proc/meminfo; echo "dirty_ratio=$(cat /proc/sys/vm/dirty_ratio) dirty_background_ratio=$(cat /proc/sys/vm/dirty_background_ratio)"
  echo "## cpu";     lscpu | grep -E 'Model name|NUMA node[0-9]'
  echo "## python";  $PY -c "import sys,numpy,pandas,matplotlib;print(sys.version.split()[0],'numpy',numpy.__version__,'pandas',pandas.__version__,'matplotlib',matplotlib.__version__)"
} > "$OUT/environment.txt"
$PY scripts/prep.py --verify > "$OUT/datafiles.txt" 2>&1 || true
echo "wrote $OUT/{environment.txt,datafiles.txt}"
