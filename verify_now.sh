#!/usr/bin/env bash
# Run every module's selftest and print a table.
#
# verify_all.sh already exists and predates most of this work. This is the
# current sweep: no claim in this project should rest on the memory of a run
# that happened hours ago.
cd ~/engineai || exit 1
source ~/miniconda3/etc/profile.d/conda.sh
conda activate eng

MODULES="engines combustion geotech preview design_review optimise
         qbank_csv wb_agent wb_kernel fea_solid receipt tasks knowledge
         scope nl_router model_harness source_adapters workbench"

pass=0; fail=0; skip=0
printf "%-18s %-8s %s\n" "MODULE" "RESULT" "DETAIL"
printf "%-18s %-8s %s\n" "------------------" "--------" "------"
for m in $MODULES; do
  [ -f "$m.py" ] || { printf "%-18s %-8s\n" "$m" "ABSENT"; continue; }
  out=$(timeout 1800 python "$m.py" --selftest 2>&1)
  rc=$?
  nfail=$(printf '%s' "$out" | grep -cE '(^|  )FAIL ')
  nok=$(printf '%s' "$out" | grep -cE '(^|  )(ok|PASS) ')
  if [ "$rc" -eq 0 ] && [ "$nfail" -eq 0 ]; then
    printf "%-18s %-8s %s\n" "$m" "PASS" "$nok checks"; pass=$((pass+1))
  else
    printf "%-18s %-8s %s\n" "$m" "FAIL" "$nfail failing of $((nok+nfail))"
    printf '%s' "$out" | grep -E 'FAIL ' | head -2 | sed 's/^/                              /'
    fail=$((fail+1))
  fi
done
echo
echo "  $pass passed, $fail failed"
echo
echo "=== live services ==="
bash services.sh status
python - <<'PY'
try:
    import tasks
    s = tasks.status()
    print(f"  celery   broker {'up' if s['broker_up'] else 'down'}, "
          f"{s['workers']} worker(s)")
except Exception as e:
    print(f"  celery   unavailable: {str(e)[:50]}")
PY
curl -s -o /dev/null -m 3 -w "  workbench http %{http_code}\n" \
  http://127.0.0.1:8100/healthz 2>/dev/null || echo "  workbench down"
