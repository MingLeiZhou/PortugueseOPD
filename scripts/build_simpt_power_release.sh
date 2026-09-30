#!/usr/bin/env bash
# SimPT-Power-2026.09.30-r2 local release (RID from SIMPT_RID). Run from repo root.
#   bash scripts/build_simpt_power_release.sh prepare [workers]   # LV-GEO-A with segments + release DB (can run while HV re-solve is running)
#   bash scripts/build_simpt_power_release.sh finalize            # after HV re-solve: validate, then freeze into data/releases/
set -euo pipefail
PY=.venv/bin/python; STEP="${1:?prepare|finalize}"; W="${2:-6}"
mkdir -p output/simpt_power_release; LOG=output/simpt_power_release/build.log
exec > >(tee -a "$LOG") 2>&1
echo "== $(date) $STEP"
command -v zstd >/dev/null || { echo "zstd missing: brew install zstd"; exit 1; }
if [ "$STEP" = "prepare" ]; then
  [ -f output/simpt_power_release/lv_geo_A/lv_geo_powerflow_national.csv ] || $PY src/build_simpt_power_release.py lv --workers "$W"
  $PY src/build_simpt_power_release.py db
elif [ "$STEP" = "finalize" ]; then
  $PY src/build_simpt_power_release.py validate
  $PY src/build_simpt_power_release.py freeze
fi
echo "== $(date) $STEP done"
