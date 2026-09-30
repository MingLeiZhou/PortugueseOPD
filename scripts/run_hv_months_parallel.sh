#!/usr/bin/env bash
# Run several HV months at the same time, each with its own writer process and its own staging DB
# (the single writer per month is the bottleneck). Then summarise and compress all 11 months.
# Usage (repo root):  bash scripts/run_hv_months_parallel.sh "2026-01 2026-02 2026-03" 4
set -uo pipefail
MONTHS_LIST="${1:?months}"; W="${2:-4}"
SRC_DB="${HV_SRC_DB:-data/external/PT60_public_data_2025-05-01_2026-03-24/pt60_public_timeseries.duckdb}"
OUT="${HV_OUT:-data/external/PT60_public_data_2025-05-01_2026-03-24/simpt_power_hv_core_ren_2026-09-30-r2}"
STAGE="${HV_STAGE:-$PWD/output/simpt_power_release/hv_stage_r2}"
PY=.venv/bin/python; WORK=work/hv_ren_fix_2026-09-29; LOG="$OUT/run.log"
exec >> "$LOG" 2>&1 < /dev/null
run_month() {
  M="$1"; FINAL="$OUT/$M/pt60_models_$M.compact.duckdb"; LOCAL="$STAGE/$M/pt60_models_$M.compact.duckdb"
  if [ -f "$FINAL" ] && grep -q "Published" "$OUT/$M/execution.log" 2>/dev/null; then echo "== $M already published"; return 0; fi
  mkdir -p "$STAGE/$M"
  echo "== $(date) parallel month $M workers=$W"
  $PY $WORK/src/run_monthly_15min.py --database "$SRC_DB" --month "$M" --output-dir "$STAGE/$M" --workers "$W" --publish-database "$FINAL" < /dev/null
  if [ -f "$FINAL" ]; then mkdir -p "$OUT/$M"; cp "$STAGE/$M"/*.log "$OUT/$M/" 2>/dev/null; echo "Published $(date)" >> "$OUT/$M/execution.log"; rm -rf "$STAGE/$M"; echo "== $(date) $M published"
  else echo "!! $M not published; staging kept"; return 1; fi
}
pids=(); for M in $MONTHS_LIST; do run_month "$M" & pids+=($!); sleep 20; done
fail=0; for p in "${pids[@]}"; do wait "$p" || fail=1; done
[ "$fail" = 0 ] || { echo "!! some month failed; not summarising"; exit 1; }
for M in 2025-05 2025-06 2025-07 2025-08 2025-09 2025-10 2025-11 2025-12 2026-01 2026-02 2026-03; do
  grep -q "Published" "$OUT/$M/execution.log" 2>/dev/null || { echo "!! $M not published yet; run this script for it"; exit 1; }
done
echo "== $(date) summarising"; $PY src/summarise_hv_core_resolve.py "$OUT"
echo "== $(date) compressing"
for M in 2025-05 2025-06 2025-07 2025-08 2025-09 2025-10 2025-11 2025-12 2026-01 2026-02 2026-03; do
  f="$OUT/$M/pt60_models_$M.compact.duckdb"; [ -f "$f.zst" ] || zstd -q -19 -T0 "$f" -o "$f.zst" &
done; wait
echo "== $(date) done"
