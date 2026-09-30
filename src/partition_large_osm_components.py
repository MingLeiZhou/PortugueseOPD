#!/usr/bin/env python3
"""Partition large mapped MV components by shortest path to public stations.

Cross-zone OSM segments become *candidate* normally-open boundaries. Neither
the nearest-source zone nor the boundary state is operator-confirmed.
"""

from __future__ import annotations

import argparse
import csv
import heapq
import json
import math
from collections import Counter
from pathlib import Path

import duckdb

ROOT=Path(__file__).resolve().parents[1]
DATABASE=ROOT/"output/all_voltage/all_voltage_staging.duckdb"
OUT=ROOT/"output/all_voltage/mv_source_zones"


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database",type=Path,default=DATABASE)
    parser.add_argument("--output",type=Path,default=OUT)
    parser.add_argument("--min-nodes",type=int,default=5001)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    with duckdb.connect(str(args.database),read_only=True) as con:
        components=con.execute("""SELECT component_id,node_count FROM candidate.osm_components
            WHERE node_count>=? AND near_station_count>0 ORDER BY node_count DESC""",
            [args.min_nodes]).fetchall()
        ids=[r[0] for r in components]
        if not ids:
            parser.error("No source-anchored large components")
        nodes=con.execute("SELECT node_id,component_id FROM candidate.osm_nodes WHERE component_id IN (SELECT unnest(?))",
                          [ids]).fetchall()
        edges=con.execute("""SELECT edge_id,from_node,to_node,length_km,component_id
            FROM candidate.osm_segments WHERE component_id IN (SELECT unnest(?))""",
            [ids]).fetchall()
        stations=con.execute("""SELECT station_code,mv_voltage_v,nearest_osm_node,component_id,
                installed_mva_public,node_distance_km
            FROM candidate.station_sources
            WHERE component_id IN (SELECT unnest(?))
              AND anchor_status='NEAR_STATION_CANDIDATE'
              AND short_circuit_max_mv_mva_public>0
              AND short_circuit_min_mv_mva_public>0
            ORDER BY component_id,station_code""",[ids]).fetchall()
        ptds=con.execute("""SELECT p.ptd_code,p.osm_segment_id_candidate,p.component_id,
                p.connection_status,p.distance_to_osm_segment_km
            FROM candidate.ptd_segment_candidates p
            WHERE p.component_id IN (SELECT unnest(?))""",[ids]).fetchall()
    node_ids=[r[0] for r in nodes]
    index={node:i for i,node in enumerate(node_ids)}
    adjacency=[[] for _ in nodes]
    for edge_id,a,b,length,component in edges:
        ai,bi=index[a],index[b]
        weight=float(length)
        if weight<=0:
            parser.error(f"Nonpositive OSM segment length: {edge_id}")
        adjacency[ai].append((bi,weight))
        adjacency[bi].append((ai,weight))
    dist=[math.inf]*len(nodes)
    owner=[None]*len(nodes)
    heap=[]
    anchor_collisions=0
    for code,voltage,nearest,component,mva,gap in stations:
        key=f"OSM:{int(voltage)}:{int(nearest)}"
        if key not in index:
            parser.error(f"Station anchor {code} not found in OSM graph")
        idx=index[key]
        if owner[idx] is not None and owner[idx]!=code:
            anchor_collisions+=1
        if owner[idx] is None or code<owner[idx]:
            owner[idx]=code
            dist[idx]=0.0
            heapq.heappush(heap,(0.0,code,idx))
    while heap:
        current,code,idx=heapq.heappop(heap)
        if current>dist[idx]+1e-12 or owner[idx]!=code:
            continue
        for neighbor,length in adjacency[idx]:
            candidate=current+length
            if candidate<dist[neighbor]-1e-10 or (abs(candidate-dist[neighbor])<=1e-10
                                                   and (owner[neighbor] is None or code<owner[neighbor])):
                dist[neighbor]=candidate
                owner[neighbor]=code
                heapq.heappush(heap,(candidate,code,neighbor))
    node_path=args.output/"zone_nodes.csv"
    with node_path.open("w",newline="",encoding="utf-8") as handle:
        writer=csv.writer(handle)
        writer.writerow(["node_id","component_id","candidate_station_code","path_km","evidence_status"])
        for (node,component),code,d in zip(nodes,owner,dist):
            writer.writerow([node,component,code,round(d,6),"GRAPH_DISTANCE_ZONE_UNVERIFIED"])
    edge_path=args.output/"zone_edges.csv"
    edge_zone={}
    boundary=Counter()
    with edge_path.open("w",newline="",encoding="utf-8") as handle:
        writer=csv.writer(handle)
        writer.writerow(["edge_id","component_id","from_station_code","to_station_code",
                         "candidate_station_code","boundary_status","length_km"])
        for edge_id,a,b,length,component in edges:
            ao,bo=owner[index[a]],owner[index[b]]
            cross=ao!=bo
            status="VIRTUAL_OPEN_BOUNDARY_UNVERIFIED" if cross else "INTRA_ZONE_OSM_SEGMENT"
            if cross:boundary[component]+=1
            zone=ao if not cross else ""
            edge_zone[edge_id]=(zone,status)
            writer.writerow([edge_id,component,ao,bo,zone,status,length])
    ptd_path=args.output/"zone_ptds.csv"
    ambiguous=0
    with ptd_path.open("w",newline="",encoding="utf-8") as handle:
        writer=csv.writer(handle)
        writer.writerow(["ptd_code","component_id","osm_segment_id_candidate",
                         "candidate_station_code","zone_status","connection_status","distance_to_osm_segment_km"])
        for code,edge,component,connection,distance in ptds:
            zone,status=edge_zone[edge]
            if not zone:ambiguous+=1
            writer.writerow([code,component,edge,zone,
                             "BOUNDARY_AMBIGUOUS" if not zone else "GRAPH_DISTANCE_CANDIDATE",
                             connection,distance])
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS candidate")
        for table,path in (("mv_zone_nodes",node_path),("mv_zone_edges",edge_path),
                           ("ptd_zone_candidates",ptd_path)):
            con.execute(f"CREATE OR REPLACE TABLE candidate.{table} AS SELECT * FROM read_csv_auto(?,header=true)",
                        [str(path)])
        zone_count=con.execute("""SELECT count(*) FROM (
            SELECT DISTINCT component_id,candidate_station_code FROM candidate.mv_zone_nodes)""").fetchone()[0]
        max_zone_nodes=con.execute("""SELECT max(n) FROM (
            SELECT component_id,candidate_station_code,count(*) n
            FROM candidate.mv_zone_nodes GROUP BY 1,2)""").fetchone()[0]
        max_zone_ptds=con.execute("""SELECT max(n) FROM (
            SELECT component_id,candidate_station_code,count(*) n
            FROM candidate.ptd_zone_candidates
            WHERE candidate_station_code IS NOT NULL GROUP BY 1,2)""").fetchone()[0]
        orphan_ptd_edges=con.execute("""SELECT count(*) FROM candidate.ptd_zone_candidates p
            LEFT JOIN candidate.mv_zone_edges e ON p.osm_segment_id_candidate=e.edge_id
            WHERE e.edge_id IS NULL""").fetchone()[0]
    checks={"large_components":len(components),"osm_nodes":len(nodes),"osm_segments":len(edges),
            "public_station_voltage_records":len(stations),"candidate_zones":zone_count,
            "candidate_ptds":len(ptds),"boundary_ambiguous_ptds":ambiguous,
            "virtual_open_boundary_segments":sum(boundary.values()),
            "max_zone_nodes":max_zone_nodes,"max_zone_ptds":max_zone_ptds,
            "unreached_nodes":sum(not math.isfinite(x) for x in dist),
            "station_anchor_collisions":anchor_collisions,"orphan_ptd_edges":orphan_ptd_edges}
    errors=[]
    if checks["unreached_nodes"] or orphan_ptd_edges:
        errors.append("Unreached node or PTD edge reference")
    report={"result":"PASS" if not errors else "FAIL","checks":checks,
            "per_component_boundary_segments":dict(boundary),"errors":errors,
            "scope":"Multi-source shortest-path partition of large OSM MV components",
            "limitations":["Source assignments and virtual open boundaries are graph-distance hypotheses",
                           "No operator feeder map, normally-open point or switch state validates zones",
                           "Partition alone does not establish an AC-solvable network"]}
    (args.output/"validation.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,indent=2))
    return 1 if errors else 0


if __name__=="__main__":
    raise SystemExit(main())
