#!/usr/bin/env bash
# Launch the workbench detached, and record its PID.
#
# Matching on the command line has now killed the calling shell three times:
# `pgrep -f 'python.* workbench\.py'` happily matches a `bash -lc "... python
# workbench.py --selftest ..."` wrapper, because that text really is in its
# argv. A PID file cannot be confused with a command line.
cd ~/engineai || exit 1
PIDFILE=data/workbench.pid
source ~/miniconda3/etc/profile.d/conda.sh 2>/dev/null
conda activate eng

if [ -f "$PIDFILE" ] && kill -0 "$(cat $PIDFILE)" 2>/dev/null; then
  echo "already running: $(cat $PIDFILE)"; exit 0
fi

setsid nohup python workbench.py > data/workbench.log 2>&1 < /dev/null &
echo $! > "$PIDFILE"
disown 2>/dev/null
sleep 6
if kill -0 "$(cat $PIDFILE)" 2>/dev/null; then
  echo "workbench pid $(cat $PIDFILE)"
else
  echo "NOT RUNNING"; tail -20 data/workbench.log; exit 1
fi
echo -n "healthz: "
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8100/healthz
