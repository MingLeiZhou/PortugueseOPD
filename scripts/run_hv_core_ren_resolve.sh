#!/usr/bin/env bash
# Re-solve all 31,492 HV snapshots with the REN Annex B/D corrected core (CORE-3787-REN) for SimPT-Power-2026.09.30-r1.
# The monthly DuckDB is written on the internal SSD (HV_STAGE) and published to the external disk (HV_OUT) only when the
# month is complete; the SSD copy is then removed. Resumable: finished months are skipped, partial months continue.
# Optional: HV_SRC_DB can point to an SSD copy of the (read-only) source database for faster input queries.
# Usage (repo root):  bash scripts/run_hv_core_ren_resolve.sh [workers] [--smoke]
set -euo pipefail
WORKERS="${1:-6}"; SMOKE="${2:-}"
SRC_DB="${HV_SRC_DB:-data/external/PT60_public_data_2025-05-01_2026-03-24/pt60_public_timeseries.duckdb}"
OUT="${HV_OUT:-data/external/PT60_public_data_2025-05-01_2026-03-24/simpt_power_hv_core_ren_2026-09-30-r2}"
STAGE="${HV_STAGE:-$PWD/output/simpt_power_release/hv_stage_r2}"
PY=.venv/bin/python; WORK=work/hv_ren_fix_2026-09-29
[ -f "$SRC_DB" ] || { echo "missing source DB: $SRC_DB (set HV_SRC_DB)"; exit 1; }
mkdir -p "$OUT" "$STAGE"; LOG="$OUT/run.log"
# plain file redirection (no tee subprocess) and closed stdin: robust under nohup / closed terminals
exec >> "$LOG" 2>&1 < /dev/null
echo "== $(date) start workers=$WORKERS src=$SRC_DB stage=$STAGE"
if [ "$SMOKE" = "--smoke" ]; then
  $PY $WORK/src/run_monthly_15min.py --database "$SRC_DB" --month 2025-05 --output-dir "$STAGE/smoke" --max-intervals 8 --workers 2 --fail-fast
  echo "== smoke done"; exit 0
fi
for M in 2025-05 2025-06 2025-07 2025-08 2025-09 2025-10 2025-11 2025-12 2026-01 2026-02 2026-03; do
  FINAL="$OUT/$M/pt60_models_$M.compact.duckdb"; LOCAL="$STAGE/$M/pt60_models_$M.compact.duckdb"
  if [ -f "$FINAL" ] && [ ! -f "$LOCAL" ] && grep -q "Published" "$OUT/$M/execution.log" 2>/dev/null; then echo "== $M already published"; continue; fi
  mkdir -p "$STAGE/$M"
  # a partial month written directly on the external disk by the first version of this script: move it to the SSD to resume
  if [ -f "$FINAL" ] && [ ! -f "$LOCAL" ]; then echo "== moving partial $M to SSD"; mv "$FINAL" "$LOCAL"; [ -f "$FINAL.wal" ] && mv "$FINAL.wal" "$LOCAL.wal"; fi
  echo "== $(date) month $M"
  $PY $WORK/src/run_monthly_15min.py --database "$SRC_DB" --month "$M" --output-dir "$STAGE/$M" --workers "$WORKERS" \
      --publish-database "$FINAL" < /dev/null
  if [ -f "$FINAL" ]; then
    cp "$STAGE/$M"/*.log "$OUT/$M/" 2>/dev/null || true
    echo "Published $(date)" >> "$OUT/$M/execution.log"
    rm -rf "$STAGE/$M"
  else
    echo "!! $M not published (incomplete or failed cases) - staging kept in $STAGE/$M"; exit 1
  fi
done
echo "== $(date) summarising"
$PY src/summarise_hv_core_resolve.py "$OUT"
echo "== $(date) compressing monthly databases"
for M in 2025-05 2025-06 2025-07 2025-08 2025-09 2025-10 2025-11 2025-12 2026-01 2026-02 2026-03; do
  f="$OUT/$M/pt60_models_$M.compact.duckdb"; [ -f "$f.zst" ] || zstd -q -19 -T0 "$f" -o "$f.zst"
done
echo "== $(date) done"
