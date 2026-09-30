#!/usr/bin/env bash
# Nationwide LV geographic networks (poles + OSM buildings/streets) and ABCN power flow.
# Run from the repository root on the Mac:  bash scripts/run_lv_geographic_national.sh 6
set -euo pipefail
WORKERS="${1:-6}"
PY=.venv/bin/python
mkdir -p output/all_voltage/lv_geo_national
LOG=output/all_voltage/lv_geo_national/run.log
exec > >(tee -a "$LOG") 2>&1
echo "== $(date) start, workers=$WORKERS"
$PY -c "import osmium" 2>/dev/null || $PY -m pip install osmium
if [ ! -f output/all_voltage/lv_geo_national/osm/bbox.json ]; then
  echo "== [1/2] OSM roads + buildings per municipality (~5-15 min)"
  $PY src/extract_osm_lv_national.py
fi
echo "== [2/2] geographic LV trees + ABCN power flow per municipality (resumable)"
$PY src/run_lv_geographic_national.py --workers "$WORKERS"
echo "== $(date) done"
