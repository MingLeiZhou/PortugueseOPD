#!/usr/bin/env python3
"""Solve one public OSM MV component with nearby public PTDs.

OSM shared vertices supply the MV graph. Spatial matches to the source station
and PTDs are candidate connections, not evidence of actual utility terminals.
Electrical parameters use public E-REDES standard catalogues plus explicitly
labelled asset assignments and engineering inferences.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import warnings
from collections import defaultdict
from pathlib import Path

import duckdb
import pandapower as pp
import pandapower.shortcircuit as sc

ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
COMPONENT = "OSMCOMP:15000:13722049055"
OUT = ROOT / "output/all_voltage/osm_component_pilot"
CHARACTERISTICS = ROOT / "portuguese_hv_network/data/raw/eredes/caracteristicas-da-rede.csv"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--component-id", default=COMPONENT)
    parser.add_argument("--zone-station-code", default=None,
                        help="Solve one multi-source graph-distance zone instead of the whole component")
    parser.add_argument("--zone-component-id", default=None,
                        help="Disambiguate a station code that has voltage records in more than one OSM component")
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--characteristics", type=Path, default=CHARACTERISTICS)
    parser.add_argument("--max-ptd-distance-km", type=float, default=0.2)
    parser.add_argument("--algorithm", choices=("nr","bfsw","iwamoto_nr"), default="nr")
    parser.add_argument("--attachment-graph", action="store_true",
                        help="Use the constrained OSM graph with inferred PTD taps and spurs")
    args = parser.parse_args()
    if args.max_ptd_distance_km <= 0:
        parser.error("--max-ptd-distance-km must be positive")
    if args.attachment_graph and args.zone_station_code:
        parser.error("--attachment-graph and --zone-station-code cannot yet be combined")
    args.output.mkdir(parents=True, exist_ok=True)

    with duckdb.connect(str(args.database), read_only=True) as con:
        edge_select = ("SELECT s.edge_id, s.from_node, s.to_node, s.length_km, s.power_tag, s.osm_way_id, "
                       "p.r_ohm_per_km, p.x_ohm_per_km, p.c_nf_per_km, p.max_i_ka, "
                       "p.r0_ohm_per_km, p.x0_ohm_per_km FROM candidate.osm_segments s "
                       "JOIN parameter.mv_line_parameters p USING(edge_id) ")
        ptd_select = ("SELECT p.ptd_code, p.osm_segment_id_candidate, p.segment_fraction, "
                      "p.projected_lon, p.projected_lat, p.distance_to_osm_segment_km, "
                      "n.capacity_kva_public, e.peak_load_proxy_mva, t.vk_percent, "
                      "t.vkr_percent, t.vk0_percent, t.vkr0_percent, t.vector_group "
                      "FROM candidate.ptd_segment_candidates p "
                      "JOIN candidate.ptd_node_candidates n USING(ptd_code) "
                      "JOIN equivalent.ptd_connections e USING(ptd_code) "
                      "JOIN parameter.ptd_transformer_parameters t USING(ptd_code) ")
        if args.zone_station_code:
            zone_component=con.execute("""SELECT DISTINCT component_id FROM candidate.mv_zone_nodes
                WHERE candidate_station_code=? AND (? IS NULL OR component_id=?)""",
                [args.zone_station_code,args.zone_component_id,args.zone_component_id]).fetchall()
            if len(zone_component)!=1:
                parser.error(f"Expected one large-component zone for station {args.zone_station_code}; "
                             "pass --zone-component-id when the station has multiple voltage records")
            source = con.execute("""SELECT component_id,mv_voltage_v FROM candidate.station_sources
                WHERE station_code=? AND component_id=?
                  AND anchor_status='NEAR_STATION_CANDIDATE'
                  AND short_circuit_max_mv_mva_public>0
                  AND short_circuit_min_mv_mva_public>0""",
                [args.zone_station_code,zone_component[0][0]]).fetchone()
            if source is None:
                parser.error(f"Unknown anchored source station: {args.zone_station_code}")
            args.component_id=source[0]
            nodes=con.execute("""SELECT n.node_id,n.lat,n.lon FROM candidate.mv_zone_nodes z
                JOIN candidate.osm_nodes n USING(node_id)
                WHERE z.candidate_station_code=? AND z.component_id=? ORDER BY n.node_id""",
                [args.zone_station_code,args.component_id]).fetchall()
            component=(source[1]/1000,len(nodes))
            edges=con.execute(edge_select+"JOIN candidate.mv_zone_edges z ON z.edge_id=s.edge_id "
                              "WHERE z.candidate_station_code=? AND z.component_id=? ORDER BY s.edge_id",
                              [args.zone_station_code,args.component_id]).fetchall()
            stations=con.execute("""SELECT station_code,nearest_osm_node,installed_mva_public,
                node_distance_km,short_circuit_max_mv_mva_public,short_circuit_min_mv_mva_public
                FROM candidate.station_sources WHERE station_code=? AND component_id=?""",
                [args.zone_station_code,args.component_id]).fetchall()
            ptds=con.execute(ptd_select+"JOIN candidate.ptd_zone_candidates z USING(ptd_code) "
                             "WHERE z.candidate_station_code=? AND z.component_id=? "
                             "AND p.connection_status='NEAR_ANCHORED_COMPONENT' "
                             "AND p.distance_to_osm_segment_km<=? AND n.capacity_kva_public>0 "
                             "ORDER BY p.ptd_code",
                             [args.zone_station_code,args.component_id,args.max_ptd_distance_km]).fetchall()
        elif args.attachment_graph:
            component=con.execute("""SELECT min(voltage_kv),count(*)
                FROM candidate.mv_attachment_graph_node WHERE component_id=?""",
                [args.component_id]).fetchone()
            if component is None or not component[1]:
                parser.error(f"Unknown attachment-graph component: {args.component_id}")
            nodes=con.execute("""SELECT node_id,lat,lon
                FROM candidate.mv_attachment_graph_node WHERE component_id=? ORDER BY node_id""",
                [args.component_id]).fetchall()
            edges=con.execute("""SELECT g.edge_id,g.from_node,g.to_node,g.solver_length_km,
                       s.power_tag,g.osm_way_id,p.r_ohm_per_km,p.x_ohm_per_km,
                       p.c_nf_per_km,p.max_i_ka,p.r0_ohm_per_km,p.x0_ohm_per_km
                FROM candidate.mv_attachment_graph_edge g
                JOIN candidate.osm_segments s ON g.parent_segment_id=s.edge_id
                JOIN parameter.mv_line_parameters p ON g.parent_segment_id=p.edge_id
                WHERE g.component_id=? ORDER BY g.edge_id""",
                [args.component_id]).fetchall()
            stations=con.execute("""SELECT station_code,nearest_osm_node,installed_mva_public,
                node_distance_km,short_circuit_max_mv_mva_public,short_circuit_min_mv_mva_public
                FROM candidate.station_sources WHERE component_id=?
                  AND anchor_status='NEAR_STATION_CANDIDATE'
                  AND short_circuit_max_mv_mva_public>0
                  AND short_circuit_min_mv_mva_public>0
                ORDER BY node_distance_km,station_code""",[args.component_id]).fetchall()
            ptds=con.execute("""SELECT p.ptd_code,p.osm_segment_id_candidate,p.segment_fraction,
                       p.projected_lon,p.projected_lat,p.distance_to_osm_segment_km,
                       n.capacity_kva_public,e.peak_load_proxy_mva,t.vk_percent,
                       t.vkr_percent,t.vk0_percent,t.vkr0_percent,t.vector_group
                FROM candidate.ptd_mv_terminal_candidate p
                JOIN candidate.ptd_node_candidates n USING(ptd_code)
                JOIN equivalent.ptd_connections e USING(ptd_code)
                JOIN parameter.ptd_transformer_parameters t USING(ptd_code)
                WHERE p.component_id=?
                  AND p.selected_model_layer='OSM_GEOMETRY_WITH_INFERRED_PTD_TAP'
                  AND n.capacity_kva_public>0 ORDER BY p.ptd_code""",
                [args.component_id]).fetchall()
        else:
            component=con.execute("SELECT voltage_kv,node_count FROM candidate.osm_components WHERE component_id=?",
                                  [args.component_id]).fetchone()
            if component is None:
                parser.error(f"Unknown component: {args.component_id}")
            nodes=con.execute("SELECT node_id,lat,lon FROM candidate.osm_nodes WHERE component_id=? ORDER BY node_id",
                              [args.component_id]).fetchall()
            edges=con.execute(edge_select+"WHERE s.component_id=? ORDER BY s.edge_id",
                              [args.component_id]).fetchall()
            stations=con.execute("""SELECT station_code,nearest_osm_node,installed_mva_public,
                node_distance_km,short_circuit_max_mv_mva_public,short_circuit_min_mv_mva_public
                FROM candidate.station_sources WHERE component_id=?
                  AND anchor_status='NEAR_STATION_CANDIDATE'
                  AND short_circuit_max_mv_mva_public>0
                  AND short_circuit_min_mv_mva_public>0
                ORDER BY node_distance_km,station_code""",[args.component_id]).fetchall()
            ptds=con.execute(ptd_select+"WHERE p.component_id=? AND p.connection_status='NEAR_ANCHORED_COMPONENT' "
                             "AND p.distance_to_osm_segment_km<=? AND n.capacity_kva_public>0 "
                             "ORDER BY p.ptd_code",[args.component_id,args.max_ptd_distance_km]).fetchall()

    if len(nodes) != component[1] or not edges or not stations or not ptds:
        parser.error("Component has no complete node/edge/source/PTD pilot inputs")
    station = stations[0]
    station_code, station_node, station_mva, station_gap, public_max, public_min = station
    source_station_codes = [row[0] for row in stations]
    mv_kv = float(component[0])
    station_node = f"OSM:{int(mv_kv * 1000)}:{station_node}"
    net = pp.create_empty_network(sn_mva=100.0, name=f"OSM component {args.component_id}")
    mv_buses = {}
    for node_id, lat, lon in nodes:
        mv_buses[node_id] = pp.create_bus(net, vn_kv=mv_kv, name=f"OSM:{int(mv_kv * 1000)}:{node_id}",
                                          geodata=(float(lon), float(lat)))
    if station_node not in mv_buses:
        parser.error("Source anchor references a node outside the component")
    source_mv_bus = mv_buses[station_node]
    # E-REDES publishes short-circuit power at each MV boundary.  Use every
    # anchored station in the component directly so a multi-source OSM graph
    # is not forced through one arbitrary 60/MV transformer proxy.
    for source_code, source_node, _source_mva, _source_gap, source_max, source_min in stations:
        source_node_id=f"OSM:{int(mv_kv * 1000)}:{source_node}"
        if source_node_id not in mv_buses:
            parser.error(f"Source anchor {source_code} references a node outside the component")
        pp.create_ext_grid(net, mv_buses[source_node_id], vm_pu=1.0,
                           name=f"STATION:{source_code}:MV_THEVENIN",
                           s_sc_max_mva=float(source_max), s_sc_min_mva=float(source_min),
                           rx_max=0.1, rx_min=0.1, x0x_max=1.0, r0x0_max=0.1)
    ptd_mv_bus = {}
    split_length_km = 0.0
    if args.attachment_graph:
        for edge_id, from_node, to_node, length_km, power_tag, way_id, r, x, c, max_i, r0, x0 in edges:
            if from_node not in mv_buses or to_node not in mv_buses or length_km <= 0:
                parser.error(f"Invalid attachment-graph edge {edge_id}")
            split_length_km += float(length_km)
            pp.create_line_from_parameters(
                net, mv_buses[from_node], mv_buses[to_node], float(length_km),
                r_ohm_per_km=float(r), x_ohm_per_km=float(x),
                c_nf_per_km=float(c), max_i_ka=float(max_i),
                r0_ohm_per_km=float(r0), x0_ohm_per_km=float(x0), c0_nf_per_km=float(c),
                name=edge_id, type="cs" if power_tag == "cable" else "ol",
                endtemp_degree=80.0)
        for row in ptds:
            code=row[0]
            terminal=f"PTDMV:{code}"
            if terminal not in mv_buses:
                parser.error(f"PTD terminal {terminal} absent from attachment graph")
            ptd_mv_bus[code]=mv_buses[terminal]
    else:
        taps_by_edge = defaultdict(list)
        for code, edge_id, fraction, lon, lat, distance_km, capacity_kva, peak_mva, vk, vkr, vk0, vkr0, vector_group in ptds:
            taps_by_edge[edge_id].append((float(fraction), code, float(lon), float(lat)))
        for edge_id, from_node, to_node, length_km, power_tag, way_id, r, x, c, max_i, r0, x0 in edges:
            if from_node not in mv_buses or to_node not in mv_buses or length_km <= 0:
                parser.error(f"Invalid OSM edge {edge_id}")
            cable = power_tag == "cable"
            split_points = [(0.0, mv_buses[from_node])]
            for fraction, code, lon, lat in sorted(taps_by_edge[edge_id]):
                if fraction <= 1e-7:
                    ptd_mv_bus[code] = mv_buses[from_node]
                elif fraction >= 1-1e-7:
                    ptd_mv_bus[code] = mv_buses[to_node]
                elif fraction-split_points[-1][0] <= 1e-7:
                    ptd_mv_bus[code] = split_points[-1][1]
                else:
                    bus_id = pp.create_bus(net, vn_kv=mv_kv, name=f"PTD_TAP:{code}:MV",
                                           geodata=(lon,lat))
                    split_points.append((fraction,bus_id))
                    ptd_mv_bus[code] = bus_id
            split_points.append((1.0,mv_buses[to_node]))
            for part, ((t0,bus0),(t1,bus1)) in enumerate(zip(split_points,split_points[1:]),start=1):
                part_length = float(length_km)*(t1-t0)
                split_length_km += part_length
                pp.create_line_from_parameters(
                    net, bus0, bus1, part_length,
                    r_ohm_per_km=float(r), x_ohm_per_km=float(x),
                    c_nf_per_km=float(c), max_i_ka=float(max_i),
                    r0_ohm_per_km=float(r0), x0_ohm_per_km=float(x0), c0_nf_per_km=float(c),
                    name=f"{edge_id}:PART:{part}", type="cs" if cable else "ol",
                    endtemp_degree=80.0)
    for code, edge_id, fraction, lon, lat, distance_km, capacity_kva, peak_mva, vk, vkr, vk0, vkr0, vector_group in ptds:
        if code not in ptd_mv_bus:
            parser.error(f"PTD {code} has no projected MV tap")
        lv = pp.create_bus(net, vn_kv=0.4, name=f"PTD:{code}:LV")
        pp.create_transformer_from_parameters(
            net, ptd_mv_bus[code], lv, sn_mva=float(capacity_kva)/1000.0,
            vn_hv_kv=mv_kv, vn_lv_kv=0.4,
            vk_percent=float(vk), vkr_percent=float(vkr),
            pfe_kw=0.0, i0_percent=0.0, shift_degree=150.0,
            name=f"PTD:{code}", vk0_percent=float(vk0), vkr0_percent=float(vkr0),
            mag0_percent=100.0, mag0_rx=0.0, si0_hv_partial=0.9,
            vector_group=str(vector_group))
        apparent = float(peak_mva or 0.0)
        pf = 0.97
        pp.create_load(net, lv, p_mw=apparent*pf,
                       q_mvar=apparent*math.sqrt(1-pf*pf), name=f"PTD:{code}:PEAK_PROXY")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            pp.runpp(net, algorithm=args.algorithm, numba=False, max_iteration=100)
    except pp.LoadflowNotConverged as exc:
        pp.to_json(net, str(args.output / "unsolved_network.json"))
        failure = {"result":"FAIL", "component_id":args.component_id,
                   "source_station_code":station_code,"source_station_codes":source_station_codes,
                   "algorithm":args.algorithm,
                   "counts":{"osm_mv_nodes":len(nodes),"osm_mv_segments":len(edges),
                             "near_ptds":len(ptds),"buses":len(net.bus)},
                   "total_osm_line_km":sum(float(edge[3]) for edge in edges),
                   "total_ptd_peak_proxy_mva":sum(float(row[7] or 0) for row in ptds),
                   "error":str(exc),
                   "scope":"Unsolved candidate network saved for topology/parameter diagnosis"}
        (args.output/"validation.json").write_text(json.dumps(failure,indent=2)+"\n",encoding="utf-8")
        print(json.dumps(failure,indent=2))
        return 1
    bus = net.res_bus.copy()
    bus.insert(0, "bus_id", net.bus.name)
    bus.to_csv(args.output / "bus_results.csv", index=False)
    line = net.res_line.copy()
    line.insert(0, "edge_id", net.line.name)
    line.to_csv(args.output / "line_results.csv", index=False)
    trafo = net.res_trafo.copy()
    trafo.insert(0, "transformer_id", net.trafo.name)
    trafo.to_csv(args.output / "transformer_results.csv", index=False)
    pp.to_json(net, str(args.output / "network.json"))

    fault_rows = []
    for case, reference in (("max", public_max), ("min", public_min)):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            sc.calc_sc(net, case=case, fault="3ph", branch_results=False)
        ikss = float(net.res_bus_sc.at[source_mv_bus, "ikss_ka"])
        calculated = math.sqrt(3)*mv_kv*ikss
        fault_rows.append({"case": case, "public_mv_ssc_mva": float(reference),
                           "calculated_mv_ssc_mva": calculated,
                           "relative_error_percent": 100*(calculated-float(reference))/float(reference)})
    with (args.output / "fault_comparison.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fault_rows[0]))
        writer.writeheader()
        writer.writerows(fault_rows)
    errors = []
    if not net.converged or not math.isfinite(float(bus.vm_pu.min())):
        errors.append("AC power flow did not converge to finite bus voltages")
    expected_transformers = len(ptds)
    if len(net.line) < len(edges) or len(net.trafo) != expected_transformers:
        errors.append("Network element counts differ from public graph/PTD candidates")
    original_length_km = sum(float(edge[3]) for edge in edges)
    if not args.attachment_graph and abs(split_length_km-original_length_km)>1e-8:
        errors.append("Splitting OSM line segments changed total length")
    report = {
        "result": "FAIL" if errors else "PARTIAL",
        "component_id": args.component_id,
        "zone_station_code": args.zone_station_code,
        "attachment_graph": args.attachment_graph,
        "algorithm": args.algorithm,
        "source_station_code": station_code,
        "source_station_codes": source_station_codes,
        "source_station_count": len(stations),
        "source_anchor_gap_km": float(station_gap),
        "counts": {"osm_mv_nodes": len(nodes), "osm_mv_segments": len(edges),
                   "split_mv_line_parts": len(net.line), "near_ptds": len(ptds), "buses": len(net.bus)},
        "line_length_km": ({"attachment_graph_solver_length": split_length_km}
                           if args.attachment_graph else
                           {"original_osm": original_length_km, "after_tap_splits": split_length_km}),
        "power_flow": {"converged": bool(net.converged),
                       "min_bus_voltage_pu": float(bus.vm_pu.min()),
                       "max_line_loading_percent": float(line.loading_percent.max()),
                       "max_transformer_loading_percent": float(trafo.loading_percent.max())},
        "fault_comparison": fault_rows,
        "source_boundary": {"basis":"PUBLIC_EREDES_MV_SHORT_CIRCUIT_THEVENIN_ALL_ANCHORED_STATIONS",
                            "primary_max_ssc_mva":float(public_max),
                            "primary_min_ssc_mva":float(public_min),
                            "source_station_count":len(stations)},
        "parameter_status": {
            "catalogue": "PUBLIC_EREDES_STANDARD_VALUES",
            "asset_assignment": "INFERRED_NOT_ASSET_NAMEPLATE",
            "mv_r": "PUBLIC_STANDARD_R20_PLUS_INFERRED_OPERATING_TEMPERATURE",
            "mv_x_c": "GEOMETRY_CLASS_INFERENCE",
            "transformer_vk_losses_vector_group": "PUBLIC_STANDARD_PLUS_INFERRED_OIL_TYPE",
            "zero_sequence": "SIMULATED_UNCALIBRATED",
        },
        "connection_status": ("CONSTRAINED_OSM_GRAPH_WITH_INFERRED_PTD_TAPS_AND_SPURS_UNVERIFIED"
                              if args.attachment_graph else
                              "GRAPH_DISTANCE_STATION_ZONE_AND_PROJECTED_PTD_TAPS_UNVERIFIED"
                              if args.zone_station_code else
                              "OSM_SHARED_VERTEX_GRAPH_WITH_PROJECTED_PTD_SEGMENT_TAPS_UNVERIFIED"),
        "limitations": ["Station and PTD terminal connections are spatial candidates",
                        "The standard catalogue is public, but installed conductor and transformer types are inferred",
                        "Line X/C, overhead ampacity, operating temperature and zero-sequence values remain uncalibrated inferences",
                        "PTD loading is a peak proxy, not a measured simultaneous snapshot",
                        "Single balanced component/zone study; not a nationwide or four-wire validation"],
        "errors": errors,
    }
    if args.attachment_graph:
        with duckdb.connect(str(args.database)) as con:
            con.execute("CREATE SCHEMA IF NOT EXISTS study")
            for table, path in (
                ("osm_attachment_pilot_bus_results", args.output / "bus_results.csv"),
                ("osm_attachment_pilot_line_results", args.output / "line_results.csv"),
                ("osm_attachment_pilot_transformer_results", args.output / "transformer_results.csv"),
                ("osm_attachment_pilot_fault_comparison", args.output / "fault_comparison.csv"),
            ):
                con.execute(f"CREATE OR REPLACE TABLE study.{table} AS "
                            "SELECT ?::VARCHAR AS component_id,* FROM read_csv_auto(?,header=true)",
                            [args.component_id, str(path)])
            con.execute("""CREATE OR REPLACE TABLE study.osm_attachment_pilot_summary AS
                SELECT ?::VARCHAR AS scenario_id,?::VARCHAR AS component_id,
                       ?::VARCHAR AS source_station_code,?::DOUBLE AS source_anchor_gap_km,
                       ?::BIGINT AS mv_nodes,?::BIGINT AS mv_edges,?::BIGINT AS ptd_count,
                       ?::DOUBLE AS min_voltage_pu,?::DOUBLE AS max_line_loading_percent,
                       ?::DOUBLE AS max_transformer_loading_percent,
                       ?::VARCHAR AS source_boundary_basis,
                       'CANDIDATE_TOPOLOGY_VALIDATION_NOT_OPERATOR_NETWORK' AS evidence_status""",
                        ["OSM_PTD_ATTACHMENT_CANDIDATE_V1", args.component_id,
                         station_code, float(station_gap), len(nodes), len(edges), len(ptds),
                         float(bus.vm_pu.min()), float(line.loading_percent.max()),
                         float(trafo.loading_percent.max()),
                         "PUBLIC_EREDES_MV_SHORT_CIRCUIT_THEVENIN"])
    (args.output / "validation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
