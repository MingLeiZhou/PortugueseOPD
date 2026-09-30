#!/usr/bin/env bash
# Nationwide MV customer connection estimate from parish-level E-REDES counts. Run from repo root:  bash scripts/run_mv_customers_national.sh 6
set -euo pipefail
WORKERS="${1:-6}"; PY=.venv/bin/python
mkdir -p output/all_voltage/mv_customer_national; LOG=output/all_voltage/mv_customer_national/run.log
exec > >(tee -a "$LOG") 2>&1
echo "== $(date) start"
$PY -c "import osmium, shapely" 2>/dev/null || $PY -m pip install osmium shapely
[ -f data/raw/eredes_mv_customers/parishes_caop2024.geojson ] || $PY src/fetch_eredes_mv_cpe_parish.py
if [ ! -f output/all_voltage/mv_customer_national/osm/mvc_0101.json ]; then
  echo "== [1/2] OSM buildings >=300 m2 + industrial/commercial landuse (one PBF pass, ~5-15 min)"; $PY src/extract_osm_mv_customer_national.py
fi
echo "== [2/2] parish placement + street routing per municipality (resumable)"
$PY src/run_mv_customers_parish_national.py --workers "$WORKERS"
echo "== $(date) done"
