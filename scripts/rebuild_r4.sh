#!/usr/bin/env bash
# SimPT-Power r4: rebuild every load-dependent layer with the calibrated time-varying LV/MV split.
#
# Run from the repository root on the Mac (hours; resumable):
#   nohup bash scripts/rebuild_r4.sh 8 > /dev/null 2>&1 &
#   tail -f output/r4_rebuild/rebuild.log
# Re-running skips finished steps (markers in output/r4_rebuild/state/). To redo one step, delete its marker.
#
# Scope: steps from build_ptd_timeseries.py onwards in docs/ALL_VOLTAGE_DATABASE_PROGRESS_CN.md "运行顺序"
# (topology, parameters and OSM steps before it do not depend on loads), with src/build_station_lv_share.py
# inserted after import_national_voltage_consumption.py; then the MV-URBAN-CABLE-V1 root slices and the
# geographic LV networks (LV-GEO-A/B). The HV core (station totals) and the MV customer layer are unchanged.
set -uo pipefail
WORKERS="${1:-8}"
PY=.venv/bin/python
R=output/r4_rebuild; ST=$R/state; mkdir -p "$ST"
LOG=$R/rebuild.log
exec > >(tee -a "$LOG") 2>&1
DB=output/all_voltage/all_voltage_staging.duckdb
echo "== $(date '+%F %T') start r4 rebuild, workers=$WORKERS"

$PY -c "import openpyxl, scipy, duckdb, pandas" 2>/dev/null || $PY -m pip install openpyxl || exit 1
for f in data/raw/eredes_profiles/Perfil_Consumo_Injecao_E-REDES_2025.xlsx data/raw/openmeteo/temperature_daily_2025-04-25_2026-03-31.json; do
  [ -f "$f" ] || { echo "missing input $f"; exit 1; }
done
# 0. keep the r3 working database (mutable) once
if [ ! -f output/all_voltage/all_voltage_staging.BEFORE_R4.duckdb ]; then
  echo "== backup working DB"; cp "$DB" output/all_voltage/all_voltage_staging.BEFORE_R4.duckdb || exit 1
  [ -f "$DB.wal" ] && cp "$DB.wal" output/all_voltage/all_voltage_staging.BEFORE_R4.duckdb.wal
fi

N=0; FAILED=()
# step <mode> <command...>; mode core = stop on failure, diag = log and continue
step() {
  local mode="$1"; shift; N=$((N+1)); local id; id=$(printf '%03d' "$N")
  if [ -f "$ST/$id.done" ]; then echo "-- [$id] skip (done): $*"; return 0; fi
  echo "== [$id] $(date '+%F %T') $*"; local t0=$SECONDS
  if "$@"; then
    echo "$*" > "$ST/$id.done"; echo "   [$id] ok in $((SECONDS-t0)) s"
  else
    local rc=$?; echo "!! [$id] exit $rc after $((SECONDS-t0)) s: $*"
    if [ "$mode" = core ]; then echo "!! core step failed; stopping. Fix and re-run (finished steps are skipped)."; exit "$rc"; fi
    echo "$*" > "$ST/$id.failed"; echo "$*" > "$ST/$id.done"; FAILED+=("[$id] $*")
  fi
}
cable_sensitivity() {
  $PY - <<'PY'
import duckdb, subprocess, sys
with duckdb.connect('output/all_voltage/all_voltage_staging.duckdb', read_only=True) as con:
    types = [row[0] for row in con.execute("SELECT designation FROM parameter.lv_cable_catalog WHERE phase_conductor_count=3 AND phase_section_mm2=neutral_section_mm2 AND (designation LIKE 'LXS 4 x %' OR designation LIKE 'LSXAV 4x%') ORDER BY designation").fetchall()]
for designation in types:
    subprocess.run([sys.executable, 'src/batch_validate_lv_four_wire.py', '--peak-proxy', '--peak-feeder-layout', '--cable-designation', designation], check=True)
PY
}
geo_lv() {  # $1 = A|B
  local base="$PWD/output/simpt_power_release_r4/lv_geo_$1"
  if [ "$1" = A ]; then
    LV_WRITE_SEGMENTS=1 LV_NATIONAL_BASE="$base" $PY src/run_lv_geographic_national.py --workers "$WORKERS"
  else
    LV_STREET_FROM_POLES=1 LV_NATIONAL_BASE="$base" $PY src/run_lv_geographic_national.py --workers "$WORKERS"
  fi
}

# 1. time series and the new LV/MV split
step core $PY src/build_ptd_timeseries.py
step core $PY src/import_national_voltage_consumption.py
step core $PY src/build_station_lv_share.py
step core $PY src/import_ptd_public_attributes.py
step core $PY src/import_ptd_utilization_histograms.py
step core $PY src/import_ptd_voltage_quality.py
step core $PY src/build_solar_der_timeseries.py
step diag $PY src/screen_ptd_reverse_flow_evidence.py
step core $PY src/build_ptd_reverse_flow_scenario.py
step core $PY src/build_lv_phase_model.py
step core $PY src/import_lv_cable_catalog.py
step diag $PY src/link_osm_lv_components.py
step diag $PY src/link_lv_poles_pilot.py
step diag $PY src/build_lv_pole_proximity_graph_pilot.py
step core $PY src/import_lv_poles_national.py
step diag $PY src/build_lv_topology_evidence_fusion.py
step core $PY src/build_normalized_network_model.py
step core $PY src/build_switching_topology_scenario.py
step core $PY src/build_resource_state_timeseries.py
step core $PY src/import_main_grid_generator_states.py
step core $PY src/build_main_grid_generator_timeseries.py
# 2. MV roots and LV designs at each root's peak
step core $PY src/build_mv_root_transformer_design_scenario.py
step diag $PY src/batch_validate_equivalent_mv_roots.py --per-root-peak --design-screening --workers "$WORKERS"
step core $PY src/refine_root_peak_distribution_design.py
step core $PY src/build_lv_feeder_route_geometry.py
step core $PY src/build_normalized_network_model.py
step core $PY src/build_switching_topology_scenario.py
step diag $PY src/batch_validate_equivalent_mv_roots.py --per-root-peak --workers "$WORKERS"
step diag $PY src/batch_validate_lv_four_wire.py --root-peak-design
step core $PY src/refine_lv_feeder_fuse_design.py
# r4: second solve-and-refine cycle (heavier LV loads need more than one linear refinement)
step core $PY src/build_lv_feeder_route_geometry.py
step core $PY src/build_normalized_network_model.py
step core $PY src/build_switching_topology_scenario.py
step diag $PY src/batch_validate_equivalent_mv_roots.py --per-root-peak --workers "$WORKERS"
step diag $PY src/batch_validate_lv_four_wire.py --root-peak-design
step core $PY src/refine_lv_feeder_fuse_design.py
step core $PY src/build_lv_feeder_route_geometry.py
step core $PY src/build_normalized_network_model.py
step core $PY src/build_switching_topology_scenario.py
step diag $PY src/batch_validate_equivalent_mv_roots.py --per-root-peak --workers "$WORKERS"
step diag $PY src/batch_validate_lv_four_wire.py --root-peak-design
step core $PY src/build_protection_design_scenario.py
step core $PY src/build_fault_impedance_scenarios.py
# 3. validation and sensitivity runs used by the paper and SI
step diag $PY src/validate_distribution_studies.py
step diag $PY src/validate_osm_component_powerflow.py
step diag $PY src/validate_lv_four_wire.py
step diag $PY src/batch_validate_lv_four_wire.py
step diag $PY src/screen_lv_parallel_feeders.py
step diag $PY src/batch_validate_lv_four_wire.py --feeder-layout
step diag $PY src/batch_validate_lv_four_wire.py --feeder-layout --timestamp-utc '2026-01-19T19:45:00+00:00' --output output/all_voltage/four_wire_parallel_holdout
step diag $PY src/batch_validate_lv_four_wire.py --peak-proxy
step diag $PY src/screen_lv_parallel_feeders.py --peak-envelope
step core $PY src/build_lv_feeder_design_scenario.py
step diag $PY src/batch_validate_lv_four_wire.py --peak-proxy --feeder-layout
step diag $PY src/batch_validate_lv_four_wire.py --peak-proxy --design-scenario
step diag $PY src/batch_validate_lv_four_wire.py --design-scenario --timestamp-utc '2026-01-19T19:45:00+00:00' --output output/all_voltage/four_wire_design_holdout
step diag $PY src/batch_validate_lv_four_wire.py --design-scenario --reverse-flow-scenario
step diag $PY src/batch_validate_lv_four_wire.py --peak-proxy --design-scenario --transformer-impedance
step diag $PY src/batch_validate_lv_four_wire.py --design-scenario --transformer-impedance --timestamp-utc '2026-01-19T19:45:00+00:00' --output output/all_voltage/four_wire_design_transformer_holdout
step diag $PY src/batch_validate_lv_four_wire.py --design-scenario --transformer-impedance --reverse-flow-scenario
step diag $PY src/batch_validate_lv_four_wire.py --peak-feeder-layout --output output/all_voltage/four_wire_peak_layout_solar
step diag $PY src/batch_validate_lv_four_wire.py --peak-feeder-layout --timestamp-utc '2026-01-19T19:45:00+00:00'
step diag cable_sensitivity
step diag $PY src/summarize_lv_cable_sensitivity.py
step diag $PY src/batch_validate_lv_four_wire.py --peak-feeder-layout --reverse-flow-scenario --cable-designation 'LXS 4 x 70'
step diag $PY src/validate_equivalent_island_powerflow.py
step diag $PY src/validate_equivalent_island_powerflow.py --open-switch-id 'SWITCH:MVFEEDER:0308D2008900:UPSTREAM'
step diag $PY src/validate_equivalent_island_powerflow.py --mv-root-bus MVROOT:00071 --output output/all_voltage/equivalent_mv_root_validation
step diag $PY src/validate_equivalent_island_powerflow.py --mv-root-bus MVROOT:00071 --timestamp-utc '2026-01-19T19:45:00+00:00' --output output/all_voltage/equivalent_mv_root_winter_holdout
step diag $PY src/validate_equivalent_island_powerflow.py --mv-root-bus MVROOT:00071 --open-switch-id 'SWITCH:MVFEEDER:0308D2001100:UPSTREAM' --output output/all_voltage/equivalent_mv_root_switch_contingency
step diag $PY src/batch_validate_equivalent_mv_roots.py --workers "$WORKERS"
step diag $PY src/validate_station_fault_modes.py
step diag $PY src/screen_station_fault_modes.py
# 4. OSM operating designs
step diag $PY src/batch_validate_osm_components.py
step diag $PY src/diagnose_component_loadability.py
step core $PY src/partition_large_osm_components.py
step diag $PY src/batch_validate_mv_zones.py --max-nodes 10000
step core $PY src/build_osm_zone_load_allocation_scenario.py
step core $PY src/build_mv_validated_operating_scenario.py --scope all
step core $PY src/build_osm_small_component_validated_scenario.py --scope all
step core $PY src/build_lv_feeder_route_geometry.py
step core $PY src/build_normalized_network_model.py
step diag $PY src/validate_complete_simulation_database.py
# 5. release layers that depend on loads
step diag $PY src/batch_validate_equivalent_mv_roots.py --per-root-peak --mv-urban-cable output/all_voltage/mv_urban_cable_v1 --output output/all_voltage/mv_urban_cable_v1/root_peak_batch --workers "$WORKERS"
step diag $PY src/validate_mv_urban_cable_network.py
step core geo_lv A
step core geo_lv B

echo "== $(date '+%F %T') finished; failed diagnostic steps: ${#FAILED[@]}"
for f in ${FAILED[@]+"${FAILED[@]}"}; do echo "   $f"; done
ls "$ST"/*.failed 2>/dev/null && echo "(markers *.failed list every diagnostic step that returned non-zero, including earlier runs)"
