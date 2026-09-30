#!/usr/bin/env python3
"""Re-run failed MV zones with a synchronized PTD peak on the common UTC axis.

The former stress case adds every PTD's individual peak proxy, although those
peaks do not occur at one time.  This validator finds, for each failed zone,
the maximum aggregate PTD load across the existing 31,492 UTC snapshots and
tests both the original candidate graph and the deterministic radialized graph.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import warnings
from pathlib import Path

import duckdb
import pandapower as pp
import pandapower.shortcircuit as sc

from diagnose_mv_zone_radialization import shortest_path_tree_lines


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
BATCH = ROOT / "output/all_voltage/mv_zone_batch"
OUT = ROOT / "output/all_voltage/mv_zone_synchronized_peak"
OUT_TOPOLOGY = ROOT / "output/all_voltage/mv_zone_topology_consistent_peak"


def solve(net: pp.pandapowerNet) -> tuple[bool, float | None, float | None, float | None]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            pp.runpp(net, algorithm="nr", numba=False, max_iteration=100, init="flat")
        return (bool(net.converged), float(net.res_bus.vm_pu.min()),
                float(net.res_line.loading_percent.max()),
                float(net.res_trafo.loading_percent.max()))
    except (pp.LoadflowNotConverged, ValueError, RuntimeError, FloatingPointError):
        return False, None, None, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--batch", type=Path, default=BATCH)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--allocation-mode", choices=("baseline", "topology-consistent"), default="baseline")
    args = parser.parse_args()
    if args.output is None:
        args.output = OUT if args.allocation_mode == "baseline" else OUT_TOPOLOGY
    args.output.mkdir(parents=True, exist_ok=True)
    report = json.loads((args.batch / "validation.json").read_text())
    failures = [(item["station_code"], item["component_id"]) for item in report["failures"]]
    if not failures:
        parser.error("No failed zones in the batch report")

    zone_networks: dict[str, Path] = {}
    zone_meta: dict[str, tuple[str, str, float]] = {}
    zone_ptd_rows: list[tuple[str, str]] = []
    for station_code, component_id in failures:
        zone_id = f"{station_code}__{component_id.replace(':', '_')}"
        path = args.batch / zone_id / "unsolved_network.json"
        if not path.exists():
            raise FileNotFoundError(path)
        net = pp.from_json(str(path))
        ptd_codes = [str(name).split(":")[1] for name in net.load.name]
        if len(ptd_codes) != len(set(ptd_codes)):
            raise ValueError(f"Duplicate PTD load names in {zone_id}")
        zone_networks[zone_id] = path
        zone_meta[zone_id] = (station_code, component_id, float(net.load.p_mw.sum()))
        zone_ptd_rows.extend((zone_id, code) for code in ptd_codes)

    with duckdb.connect(str(args.database), read_only=True) as con:
        if args.allocation_mode == "baseline":
            con.execute("""CREATE TEMP VIEW selected_ptd_allocation AS
                SELECT ptd_code,profile_station_code,factor,station_lv_fraction,
                       allocation_status AS assignment_status
                FROM operating.ptd_allocations""")
        else:
            con.execute("""CREATE TEMP VIEW selected_ptd_allocation AS
                SELECT ptd_code,scenario_profile_station_code AS profile_station_code,
                       scenario_factor AS factor,
                       scenario_station_lv_fraction AS station_lv_fraction,
                       assignment_status
                FROM scenario.osm_zone_ptd_assignment""")
        con.execute("CREATE TEMP TABLE failed_zone_ptd(zone_id VARCHAR,ptd_code VARCHAR)")
        con.executemany("INSERT INTO failed_zone_ptd VALUES (?,?)", zone_ptd_rows)
        con.execute("""CREATE TEMP TABLE failed_zone_profile_weight AS
            SELECT z.zone_id,a.profile_station_code,
                   sum(a.factor*a.station_lv_fraction) AS weight
            FROM failed_zone_ptd z JOIN selected_ptd_allocation a USING(ptd_code)
            GROUP BY 1,2""")
        con.execute("""CREATE TEMP TABLE failed_zone_peak AS
            SELECT zone_id,timestamp_utc,p_mw,profile_count,public_profile_count
            FROM (
              SELECT w.zone_id,s.timestamp_utc,sum(w.weight*s.p_mw) AS p_mw,
                     count(*) AS profile_count,
                     count(*) FILTER (WHERE s.data_status='PUBLIC_EREDES_STATION_AGGREGATE') AS public_profile_count,
                     row_number() OVER (PARTITION BY w.zone_id
                         ORDER BY sum(w.weight*s.p_mw) DESC,s.timestamp_utc) AS rank
              FROM failed_zone_profile_weight w
              JOIN operating.station_15min_complete s ON s.station_code=w.profile_station_code
              GROUP BY 1,2
            ) WHERE rank=1""")
        peaks = {row[0]: row[1:] for row in con.execute(
            "SELECT zone_id,timestamp_utc,p_mw,profile_count,public_profile_count FROM failed_zone_peak").fetchall()}
        ptd_loads = {(row[0], row[1]): row[2:] for row in con.execute("""
            SELECT z.zone_id,z.ptd_code,
                   s.p_mw*a.factor*a.station_lv_fraction AS p_mw,
                   s.p_mw*a.factor*a.station_lv_fraction*tan(acos(0.97)) AS q_mvar,
                   s.data_status
            FROM failed_zone_ptd z
            JOIN selected_ptd_allocation a USING(ptd_code)
            JOIN failed_zone_peak p USING(zone_id)
            JOIN operating.station_15min_complete s
              ON s.station_code=a.profile_station_code AND s.timestamp_utc=p.timestamp_utc
            """).fetchall()}
        public_fault = {
            (row[0], row[1]): (float(row[2]), float(row[3]))
            for row in con.execute("""SELECT station_code,component_id,
                       short_circuit_max_mv_mva_public,short_circuit_min_mv_mva_public
                FROM candidate.station_sources
                WHERE short_circuit_max_mv_mva_public>0 AND short_circuit_min_mv_mva_public>0""").fetchall()
        }
    if len(peaks) != len(failures) or len(ptd_loads) != len(zone_ptd_rows):
        raise RuntimeError("Synchronized peak did not cover every failed zone and PTD")

    rows: list[dict[str, object]] = []
    detail_rows: list[dict[str, object]] = []
    for position, (zone_id, path) in enumerate(zone_networks.items(), start=1):
        station_code, component_id, individual_peak_mw = zone_meta[zone_id]
        timestamp, synchronized_peak_mw, profile_count, public_profile_count = peaks[zone_id]
        for scenario in ("ORIGINAL_CANDIDATE_GRAPH", "SOURCE_SHORTEST_PATH_RADIALIZED"):
            net = pp.from_json(str(path))
            for index, name in net.load.name.items():
                code = str(name).split(":")[1]
                p_mw, q_mvar, status = ptd_loads[(zone_id, code)]
                net.load.at[index, "p_mw"] = float(p_mw)
                net.load.at[index, "q_mvar"] = float(q_mvar)
                detail_rows.append({
                    "zone_id": zone_id, "station_code": station_code,
                    "component_id": component_id, "scenario": scenario,
                    "timestamp_utc": timestamp, "ptd_code": code,
                    "p_mw": float(p_mw), "q_mvar": float(q_mvar),
                    "profile_data_status": status,
                })
            lv_leaf_only = all(
                int((net.trafo.lv_bus == bus).sum()) == 1
                and int((net.line.from_bus == bus).sum() + (net.line.to_bus == bus).sum()) == 0
                for bus in net.trafo.lv_bus
            )
            if not lv_leaf_only:
                raise ValueError(f"Non-leaf LV bus prevents clock-angle suppression in {zone_id}")
            net.trafo["shift_degree"] = 0.0
            opened = 0
            if scenario == "SOURCE_SHORTEST_PATH_RADIALIZED":
                tree_lines, unreachable, _ = shortest_path_tree_lines(net)
                if unreachable:
                    raise ValueError(f"Radialization leaves unreachable nodes in {zone_id}")
                mv_lines = {
                    int(index) for index, line in net.line.iterrows()
                    if float(net.bus.at[int(line.from_bus), "vn_kv"]) > 1.0
                    and float(net.bus.at[int(line.to_bus), "vn_kv"]) > 1.0
                }
                open_lines = sorted(mv_lines - tree_lines)
                net.line.loc[open_lines, "in_service"] = False
                opened = len(open_lines)
            converged, min_v, max_line, max_trafo = solve(net)
            max_error = None
            min_error = None
            if len(net.ext_grid) == 1:
                source_bus = int(net.ext_grid.bus.iloc[0])
                mv_kv = float(net.bus.at[source_bus, "vn_kv"])
                reference_max, reference_min = public_fault[(station_code, component_id)]
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    sc.calc_sc(net, case="max", fault="3ph", branch_results=False)
                calculated = math.sqrt(3) * mv_kv * float(net.res_bus_sc.at[source_bus, "ikss_ka"])
                max_error = 100 * (calculated - reference_max) / reference_max
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    sc.calc_sc(net, case="min", fault="3ph", branch_results=False)
                calculated = math.sqrt(3) * mv_kv * float(net.res_bus_sc.at[source_bus, "ikss_ka"])
                min_error = 100 * (calculated - reference_min) / reference_min
            rows.append({
                "zone_id": zone_id, "station_code": station_code,
                "component_id": component_id, "scenario": scenario,
                "timestamp_utc": timestamp,
                "individual_ptd_peak_sum_mw": individual_peak_mw,
                "synchronized_zone_peak_mw": float(synchronized_peak_mw),
                "synchronized_to_individual_peak_ratio": float(synchronized_peak_mw) / individual_peak_mw,
                "profile_station_count": int(profile_count),
                "public_profile_station_count_at_peak": int(public_profile_count),
                "ptd_count": len(net.load), "candidate_open_edge_count": opened,
                "converged": converged, "min_voltage_pu": min_v,
                "max_line_loading_percent": max_line,
                "max_transformer_loading_percent": max_trafo,
                "within_proxy_operating_limits": bool(converged and min_v is not None and min_v >= 0.9
                                                      and max_line <= 100 and max_trafo <= 100),
                "fault_max_error_percent": max_error,
                "fault_min_error_percent": min_error,
                "load_evidence_status": ("SYNCHRONIZED_BASELINE_FACTORIZED_PTD_SCENARIO"
                                         if args.allocation_mode == "baseline"
                                         else "SYNCHRONIZED_OSM_ZONE_TOPOLOGY_CONSISTENT_STATION_TOTAL_SCENARIO"),
                "topology_evidence_status": ("OSM_ZONE_CANDIDATE_UNVERIFIED" if scenario == "ORIGINAL_CANDIDATE_GRAPH"
                                             else "OSM_ZONE_RADIALIZATION_UNVERIFIED"),
            })
        print(f"{position}/{len(zone_networks)} {zone_id} "
              f"ratio={float(synchronized_peak_mw)/individual_peak_mw:.3f} "
              f"original={rows[-2]['converged']} radial={rows[-1]['converged']}", flush=True)

    summary_path = args.output / "zone_synchronized_peak_summary.csv"
    detail_path = args.output / "zone_synchronized_peak_ptd_loads.csv"
    for path, output_rows in ((summary_path, rows), (detail_path, detail_rows)):
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(output_rows[0]))
            writer.writeheader(); writer.writerows(output_rows)
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS study")
        summary_table = ("study.mv_zone_synchronized_peak_validation" if args.allocation_mode == "baseline"
                         else "study.mv_zone_topology_consistent_peak_validation")
        detail_table = ("study.mv_zone_synchronized_peak_ptd_load" if args.allocation_mode == "baseline"
                        else "study.mv_zone_topology_consistent_peak_ptd_load")
        con.execute(f"CREATE OR REPLACE TABLE {summary_table} AS SELECT * FROM read_csv_auto(?,header=true)", [str(summary_path)])
        con.execute(f"CREATE OR REPLACE TABLE {detail_table} AS SELECT * FROM read_csv_auto(?,header=true)", [str(detail_path)])

    by_scenario = {}
    for scenario in ("ORIGINAL_CANDIDATE_GRAPH", "SOURCE_SHORTEST_PATH_RADIALIZED"):
        subset = [row for row in rows if row["scenario"] == scenario]
        by_scenario[scenario] = {
            "zones": len(subset),
            "converged": sum(bool(row["converged"]) for row in subset),
            "within_proxy_operating_limits": sum(bool(row["within_proxy_operating_limits"]) for row in subset),
            "fault_max_abs_error_within_10_percent": sum(row["fault_max_error_percent"] is not None and abs(float(row["fault_max_error_percent"])) <= 10 for row in subset),
            "fault_min_abs_error_within_10_percent": sum(row["fault_min_error_percent"] is not None and abs(float(row["fault_min_error_percent"])) <= 10 for row in subset),
        }
    ratios = [float(row["synchronized_to_individual_peak_ratio"]) for row in rows if row["scenario"] == "ORIGINAL_CANDIDATE_GRAPH"]
    validation = {
        "result": "PARTIAL",
        "allocation_mode": args.allocation_mode,
        "checks": {
            "failed_zones_screened": len(failures),
            "ptd_load_rows": len(detail_rows),
            "minimum_synchronized_to_individual_peak_ratio": min(ratios),
            "maximum_synchronized_to_individual_peak_ratio": max(ratios),
        },
        "by_scenario": by_scenario,
        "interpretation": [
            "The synchronized peak is the maximum sum of the selected PTD allocation on one common UTC timestamp.",
            "It replaces the physically impossible sum of each PTD's separate peak but remains a scenario because PTD allocation and profile-station assignment are inferred.",
            "In topology-consistent mode, OSM-zone PTDs use the candidate source station profile and normalized factors that conserve that station's LV total; one station without a profile uses the documented baseline fallback.",
            "Dyn5 clock angle is suppressed only in the balanced positive-sequence solve after verifying all LV buses are leaves; stored transformer vector groups remain unchanged.",
        ],
    }
    (args.output / "validation.json").write_text(json.dumps(validation, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": validation["result"], "checks": validation["checks"],
                      "by_scenario": by_scenario}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
