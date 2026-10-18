#!/bin/bash
# Operator-approved 2026-10-18: run stage4_generate in /home/user/n2_stage with a 12 GiB cap.
set -u
PARENT=/sys/fs/cgroup/memory$(awk -F: '$2=="memory"{print $3}' /proc/self/cgroup)
CG=$PARENT/n2-run1
mkdir "$CG" || exit 90
echo $((12*1024*1024*1024)) > "$CG/memory.limit_in_bytes" || exit 91
echo $((24*1024*1024*1024)) > "$CG/memory.memsw.limit_in_bytes" || exit 92
echo $$ > "$CG/cgroup.procs" || exit 93
cd /home/user/gisindia4logistics
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 GDAL_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1
unset PYTHONPATH PYTHONHOME
( while sleep 5; do echo "$(date -u +%T) usage=$(cat $CG/memory.usage_in_bytes) peak=$(cat $CG/memory.max_usage_in_bytes) failcnt=$(cat $CG/memory.failcnt)"; done ) > /home/user/n2_stage/mem.log 2>&1 &
MON=$!
START=$(date +%s)
.venv/bin/python -B -u -m scripts.analyze.stage4_generate --source-root /home/user/gisindia4logistics/data --stage-root /home/user/n2_stage/run1 --cache-root /home/user/n2_stage/cache --run-id linux-2026-10-18-run1 > /home/user/n2_stage/run.log 2>&1
RC=$?
kill $MON 2>/dev/null
wait $MON 2>/dev/null
for i in 1 2 3 4 5 6 7 8 9 10; do [ "$(wc -l < "$CG/cgroup.procs")" -le 1 ] && break; sleep 1; done
echo "RESULT rc=$RC elapsed_s=$(( $(date +%s) - START )) peak=$(cat $CG/memory.max_usage_in_bytes) failcnt=$(cat $CG/memory.failcnt) $(grep oom_kill $CG/memory.oom_control | tr '\n' ' ')" >> /home/user/n2_stage/run.log
echo $$ > "$PARENT/cgroup.procs"
rmdir "$CG"
exit $RC
