#!/usr/bin/env python3
"""Link sparse OSM 0.4 kV components to nearby public PTDs as candidates.

Only unique close PTD-to-component matches are marked usable for a later LV
geometry pilot. Geographic proximity does not verify electrical terminals.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter,defaultdict
from pathlib import Path

import duckdb
import numpy as np
from pyproj import Transformer
from shapely import linestrings,points
from shapely.strtree import STRtree

ROOT=Path(__file__).resolve().parents[1]
DATABASE=ROOT/"output/all_voltage/all_voltage_staging.duckdb"
PTD=ROOT/"output/feasibility/ptd_national_export.json"
OUT=ROOT/"output/all_voltage/lv_osm_links"


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database",type=Path,default=DATABASE)
    parser.add_argument("--ptd",type=Path,default=PTD)
    parser.add_argument("--output",type=Path,default=OUT)
    parser.add_argument("--near-km",type=float,default=0.1)
    args=parser.parse_args()
    if args.near_km<=0:parser.error("--near-km must be positive")
    args.output.mkdir(parents=True,exist_ok=True)
    with duckdb.connect(str(args.database),read_only=True) as con:
        nodes=con.execute("SELECT node_id,lon,lat FROM candidate.osm_nodes WHERE voltage_kv=0.4").fetchall()
        segments=con.execute("""SELECT edge_id,from_node,to_node,component_id,length_km
            FROM candidate.osm_segments WHERE voltage_kv=0.4 ORDER BY edge_id""").fetchall()
    raw=json.loads(args.ptd.read_text())
    if isinstance(raw,dict):raw=raw["results"]
    ptds=[r for r in raw if r.get("coordenadas_geo")]
    forward=Transformer.from_crs("EPSG:4326","EPSG:3035",always_xy=True)
    inverse=Transformer.from_crs("EPSG:3035","EPSG:4326",always_xy=True)
    node_id=[r[0] for r in nodes]
    x,y=forward.transform(np.array([r[1] for r in nodes]),np.array([r[2] for r in nodes]))
    xy=dict(zip(node_id,zip(x,y)))
    a=np.asarray([xy[r[1]] for r in segments]);b=np.asarray([xy[r[2]] for r in segments])
    tree=STRtree(linestrings(np.stack((a,b),axis=1)))
    px,py=forward.transform(np.array([r["coordenadas_geo"]["lon"] for r in ptds]),
                            np.array([r["coordenadas_geo"]["lat"] for r in ptds]))
    selected=np.empty(len(ptds),dtype=np.int64)
    for start in range(0,len(ptds),5000):
        end=min(start+5000,len(ptds))
        indices=tree.query_nearest(points(px[start:end],py[start:end]),all_matches=False)
        selected[start:end][indices[0]]=indices[1]
    va=a[selected];vb=b[selected];v=vb-va
    t=np.clip(np.einsum("ij,ij->i",np.stack((px,py),axis=1)-va,v)/np.einsum("ij,ij->i",v,v),0,1)
    projected=va+t[:,None]*v
    distance=np.linalg.norm(np.stack((px,py),axis=1)-projected,axis=1)/1000
    plon,plat=inverse.transform(projected[:,0],projected[:,1])
    candidate_path=args.output/"ptd_lv_segment_candidates.csv"
    fields=["ptd_code","osm_lv_edge_id","osm_lv_component_id","distance_km",
            "projected_lon","projected_lat","segment_fraction","near_status","evidence_status"]
    near_by_component=defaultdict(list)
    with candidate_path.open("w",newline="",encoding="utf-8") as handle:
        writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader()
        for i,ptd in enumerate(ptds):
            code=ptd["cod_instalacao"]
            edge=segments[int(selected[i])]
            near=distance[i]<=args.near_km
            if near:near_by_component[edge[3]].append((code,float(distance[i])))
            writer.writerow({"ptd_code":code,"osm_lv_edge_id":edge[0],
                             "osm_lv_component_id":edge[3],"distance_km":round(float(distance[i]),6),
                             "projected_lon":round(float(plon[i]),8),
                             "projected_lat":round(float(plat[i]),8),
                             "segment_fraction":round(float(t[i]),8),
                             "near_status":"WITHIN_100M_CANDIDATE" if near else "DISTANT_NO_LV_EVIDENCE",
                             "evidence_status":"OSM_GEOMETRY_PROXIMITY_UNVERIFIED"})
    links=[]
    for component in sorted({r[3] for r in segments}):
        candidates=sorted(near_by_component[component],key=lambda x:(x[1],x[0]))
        status=("NO_NEAR_PTD" if not candidates else
                "UNIQUE_NEAR_PTD_CANDIDATE" if len(candidates)==1 else
                "AMBIGUOUS_MULTIPLE_NEAR_PTDS")
        links.append({"osm_lv_component_id":component,"near_ptd_count":len(candidates),
                      "candidate_ptd_code":candidates[0][0] if candidates else "",
                      "closest_distance_km":round(candidates[0][1],6) if candidates else "",
                      "link_status":status,"evidence_status":"OSM_GEOMETRY_PROXIMITY_UNVERIFIED"})
    link_path=args.output/"lv_component_ptd_links.csv"
    with link_path.open("w",newline="",encoding="utf-8") as handle:
        writer=csv.DictWriter(handle,fieldnames=list(links[0]));writer.writeheader();writer.writerows(links)
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS candidate")
        con.execute("CREATE OR REPLACE TABLE candidate.ptd_lv_segment_candidates AS SELECT * FROM read_csv_auto(?,header=true)",
                    [str(candidate_path)])
        con.execute("CREATE OR REPLACE TABLE candidate.lv_component_ptd_links AS SELECT * FROM read_csv_auto(?,header=true)",
                    [str(link_path)])
        orphan=con.execute("""SELECT count(*) FROM candidate.ptd_lv_segment_candidates p
            LEFT JOIN candidate.osm_segments s ON p.osm_lv_edge_id=s.edge_id
            WHERE s.edge_id IS NULL OR s.voltage_kv<>0.4""").fetchone()[0]
    counts=Counter(r["link_status"] for r in links)
    checks={"osm_lv_segments":len(segments),"osm_lv_components":len(links),
            "public_ptds_with_coordinates":len(ptds),
            "ptds_within_near_radius":sum(len(v) for v in near_by_component.values()),
            "unique_near_ptd_components":counts["UNIQUE_NEAR_PTD_CANDIDATE"],
            "ambiguous_multi_ptd_components":counts["AMBIGUOUS_MULTIPLE_NEAR_PTDS"],
            "components_without_near_ptd":counts["NO_NEAR_PTD"],
            "orphan_lv_segment_candidates":orphan}
    errors=[]
    if len(links)!=len({r[3] for r in segments}) or orphan:
        errors.append("OSM LV component coverage or edge reference failed")
    report={"result":"PASS" if not errors else "FAIL","near_radius_km":args.near_km,
            "checks":checks,"errors":errors,
            "scope":"Sparse OSM 0.4 kV geometry crosswalk to public PTDs; no electrical terminal verified",
            "limitations":["Unmapped LV feeders remain represented by synthetic equivalent topology",
                           "Near and unique spatial matches can still connect to another PTD in reality"]}
    (args.output/"validation.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,indent=2))
    return 1 if errors else 0


if __name__=="__main__":
    raise SystemExit(main())
