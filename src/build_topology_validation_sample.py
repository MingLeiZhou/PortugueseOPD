"""Build a stratified, fail-closed external topology review sample.

This script prepares evidence and annotation tables. It does not infer review
labels and does not treat internal topology consistency as external truth.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from urllib.parse import quote_plus

import networkx as nx
import numpy as np
import pandas as pd

import config
from utils import ensure_directories, utc_now, write_json, write_text


ROOT = config.ROOT_DIR
PROCESSED = config.PROCESSED_DIR
REPORTS = config.REPORTS_DIR
OUT = PROCESSED / "topology_validation"
BRANCHES = PROCESSED / "at_interfacility_candidate_branches.csv"
LINE_INPUTS = PROCESSED / "at_line_parameter_inputs.csv"
GRAPH = PROCESSED / "at_paper_logic_graph.graphml"


def flatten_coordinates(value: object) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    if isinstance(value, list):
        if len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
            points.append((float(value[0]), float(value[1])))
        else:
            for item in value:
                points.extend(flatten_coordinates(item))
    return points


def geometry_context(raw: object) -> dict[str, float | str]:
    try:
        geometry = json.loads(str(raw))
        points = flatten_coordinates(geometry.get("coordinates", []))
    except (TypeError, ValueError, json.JSONDecodeError):
        points = []
    if not points:
        return {
            "midpoint_lon": math.nan,
            "midpoint_lat": math.nan,
            "bbox_west": math.nan,
            "bbox_south": math.nan,
            "bbox_east": math.nan,
            "bbox_north": math.nan,
            "geometry_parse_status": "INVALID",
        }
    lon = np.asarray([point[0] for point in points], dtype=float)
    lat = np.asarray([point[1] for point in points], dtype=float)
    return {
        "midpoint_lon": float(np.mean(lon)),
        "midpoint_lat": float(np.mean(lat)),
        "bbox_west": float(np.min(lon)),
        "bbox_south": float(np.min(lat)),
        "bbox_east": float(np.max(lon)),
        "bbox_north": float(np.max(lat)),
        "geometry_parse_status": "VALID",
    }


def proportional_quotas(counts: pd.Series, target: int) -> dict[str, int]:
    counts = counts.astype(int)
    if target >= int(counts.sum()):
        return counts.to_dict()
    raw = counts / counts.sum() * target
    quotas = np.floor(raw).astype(int)
    if target >= len(counts):
        quotas = quotas.clip(lower=1)
    quotas = np.minimum(quotas, counts)
    while int(quotas.sum()) < target:
        candidates = counts[counts > quotas].index
        if len(candidates) == 0:
            break
        remainder = (raw - quotas).loc[candidates]
        chosen = str(remainder.sort_values(ascending=False, kind="mergesort").index[0])
        quotas.loc[chosen] += 1
    while int(quotas.sum()) > target:
        candidates = quotas[quotas > 1].index if target >= len(counts) else quotas[quotas > 0].index
        if len(candidates) == 0:
            break
        surplus = (quotas - raw).loc[candidates]
        chosen = str(surplus.sort_values(ascending=False, kind="mergesort").index[0])
        quotas.loc[chosen] -= 1
    return {str(key): int(value) for key, value in quotas.items()}


def build_sample(sample_size: int, seed: int) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, object]]:
    for path in (BRANCHES, LINE_INPUTS, GRAPH):
        if not path.exists():
            raise RuntimeError(f"Fail-closed: missing required topology artifact: {path}")

    branches = pd.read_csv(BRANCHES)
    line_inputs = pd.read_csv(LINE_INPUTS)
    keep = [
        "branch_id",
        "asset_type",
        "ambiguous_line_type",
        "is_parallel_branch",
        "parallel_branch_count_between_facilities_voltage",
        "geometry_length_mismatch_gt_5pct",
        "topology_confidence_score",
    ]
    available = [column for column in keep if column in line_inputs.columns]
    branches = branches.merge(line_inputs[available], on="branch_id", how="left", validate="one_to_one")
    branches["confidence_score"] = pd.to_numeric(branches["confidence_score"], errors="coerce")
    branches["total_length_km"] = pd.to_numeric(branches["total_length_km"], errors="coerce")
    branches["asset_type"] = branches.get("asset_type", pd.Series("unknown", index=branches.index)).fillna("unknown")

    graphml = nx.read_graphml(GRAPH)
    node_attrs = {str(node): attrs for node, attrs in graphml.nodes(data=True)}
    simple = nx.Graph()
    pair_counts: dict[tuple[str, str], int] = {}
    for row in branches.itertuples(index=False):
        left = str(row.from_facility_uid)
        right = str(row.to_facility_uid)
        pair = tuple(sorted((left, right)))
        pair_counts[pair] = pair_counts.get(pair, 0) + 1
        simple.add_edge(left, right)
    bridge_pairs = {tuple(sorted(edge)) for edge in nx.bridges(simple)}
    components = list(nx.connected_components(simple))
    component_by_node: dict[str, tuple[int, int]] = {}
    for component_id, nodes in enumerate(sorted(components, key=lambda values: (-len(values), sorted(values)[0]))):
        for node in nodes:
            component_by_node[str(node)] = (component_id, len(nodes))
    largest_size = max((len(component) for component in components), default=0)

    records: list[dict[str, object]] = []
    for row in branches.to_dict("records"):
        left = str(row["from_facility_uid"])
        right = str(row["to_facility_uid"])
        pair = tuple(sorted((left, right)))
        pair_count = pair_counts[pair]
        if pair_count > 1:
            component_role = "parallel"
        elif pair in bridge_pairs:
            component_role = "bridge"
        else:
            component_role = "cycle_or_meshed"
        component_id, component_size = component_by_node.get(left, (-1, 0))
        geom = geometry_context(row.get("geometry"))
        midpoint_lat = geom["midpoint_lat"]
        midpoint_lon = geom["midpoint_lon"]
        from_node = node_attrs.get(left, {})
        to_node = node_attrs.get(right, {})
        from_lat = pd.to_numeric(from_node.get("lat"), errors="coerce")
        from_lon = pd.to_numeric(from_node.get("lon"), errors="coerce")
        to_lat = pd.to_numeric(to_node.get("lat"), errors="coerce")
        to_lon = pd.to_numeric(to_node.get("lon"), errors="coerce")
        if pd.isna(midpoint_lat) or pd.isna(midpoint_lon):
            osm_url = ""
            satellite_url = ""
        else:
            osm_url = f"https://www.openstreetmap.org/#map=13/{midpoint_lat:.6f}/{midpoint_lon:.6f}"
            satellite_url = (
                "https://www.google.com/maps/@?api=1&map_action=map"
                f"&center={midpoint_lat:.6f}%2C{midpoint_lon:.6f}&zoom=13&basemap=satellite"
            )
        record = {
            **row,
            **geom,
            "from_lat": from_lat,
            "from_lon": from_lon,
            "to_lat": to_lat,
            "to_lon": to_lon,
            "component_id": component_id,
            "component_size": component_size,
            "in_largest_component": component_size == largest_size,
            "component_role": component_role,
            "parallel_edge_count": pair_count,
            "osm_context_url": osm_url,
            "satellite_context_url": satellite_url,
            "from_facility_search_url": "https://www.openstreetmap.org/search?query=" + quote_plus(str(row["from_facility_name"])),
            "to_facility_search_url": "https://www.openstreetmap.org/search?query=" + quote_plus(str(row["to_facility_name"])),
            "publication_allowed": False,
            "external_truth_status": "UNREVIEWED",
        }
        records.append(record)
    enriched = pd.DataFrame(records)

    enriched["confidence_band"] = pd.qcut(
        enriched["confidence_score"].rank(method="first"),
        q=3,
        labels=["low", "medium", "high"],
    ).astype(str)
    enriched["validation_stratum"] = (
        enriched["confidence_band"].astype(str)
        + "|" + enriched["asset_type"].astype(str)
        + "|" + enriched["component_role"].astype(str)
    )
    sample_size = min(max(1, sample_size), len(enriched))
    counts = enriched.groupby("validation_stratum").size().sort_index()
    quotas = proportional_quotas(counts, sample_size)
    sampled_parts: list[pd.DataFrame] = []
    for offset, (stratum, quota) in enumerate(sorted(quotas.items())):
        subset = enriched[enriched["validation_stratum"] == stratum]
        sampled_parts.append(subset.sample(n=quota, random_state=seed + offset))
    sample = pd.concat(sampled_parts, ignore_index=True)
    sample["stratum_population"] = sample["validation_stratum"].map(counts.to_dict()).astype(int)
    sample_counts = sample.groupby("validation_stratum").size().to_dict()
    sample["stratum_sample"] = sample["validation_stratum"].map(sample_counts).astype(int)
    sample["sampling_weight"] = sample["stratum_population"] / sample["stratum_sample"]
    sample = sample.sort_values(
        ["confidence_band", "component_role", "asset_type", "branch_id"],
        kind="mergesort",
    ).reset_index(drop=True)
    sample.insert(0, "review_order", np.arange(1, len(sample) + 1))

    review_rows: list[dict[str, object]] = []
    for row in sample.to_dict("records"):
        for reviewer_slot in (1, 2):
            review_rows.append(
                {
                    "review_order": row["review_order"],
                    "branch_id": row["branch_id"],
                    "reviewer_slot": reviewer_slot,
                    "reviewer_id": "",
                    "review_date": "",
                    "review_label": "",
                    "endpoint_a_confirmed": "",
                    "endpoint_b_confirmed": "",
                    "continuous_route_confirmed": "",
                    "evidence_source_type": "",
                    "evidence_reference": "",
                    "evidence_access_date": "",
                    "evidence_notes": "",
                    "confidence": "",
                    "adjudication_required": "",
                    "allowed_labels": "CONFIRMED|REJECTED|UNCERTAIN|ABSTAIN",
                }
            )
    reviews = pd.DataFrame(review_rows)
    summary = {
        "generated_at": utc_now(),
        "status": "SAMPLE_READY_REVIEW_NOT_PERFORMED",
        "population_branches": int(len(enriched)),
        "sample_branches": int(len(sample)),
        "review_rows_required": int(len(reviews)),
        "reviewers_per_branch": 2,
        "seed": seed,
        "strata": int(len(counts)),
        "largest_component_size": int(largest_size),
        "precision_claim_allowed": False,
        "reason": "External evidence and independent reviewer labels have not been completed.",
    }
    return sample, reviews, summary


def write_geojson(sample: pd.DataFrame, path: Path) -> None:
    features = []
    for row in sample.to_dict("records"):
        try:
            geometry = json.loads(str(row.get("geometry", "")))
        except (TypeError, ValueError, json.JSONDecodeError):
            geometry = None
        properties = {
            key: value
            for key, value in row.items()
            if key not in {"geometry", "r", "x", "b", "thermal_limit", "transformer_impedance", "tap_settings"}
        }
        properties = {
            key: (None if pd.isna(value) else value)
            for key, value in properties.items()
        }
        features.append({"type": "Feature", "geometry": geometry, "properties": properties})
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-size", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20260713)
    args = parser.parse_args()

    ensure_directories()
    OUT.mkdir(parents=True, exist_ok=True)
    sample, reviews, summary = build_sample(args.sample_size, args.seed)
    sample.to_csv(OUT / "pt_topology_validation_sample.csv", index=False)
    reviews.to_csv(OUT / "pt_topology_validation_review_template.csv", index=False)
    review_path = OUT / "pt_topology_validation_reviews.csv"
    if not review_path.exists():
        reviews.to_csv(review_path, index=False)
    write_geojson(sample, OUT / "pt_topology_validation_sample.geojson")
    write_json(OUT / "pt_topology_validation_sample_summary.json", summary)

    text = [
        "# 98 External Topology Validation Protocol",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "## Status",
        "",
        "`SAMPLE_READY_REVIEW_NOT_PERFORMED`",
        "",
        "The sample is not ground truth. It is a proportional stratified review set that preserves confidence, asset-type, and structural-role coverage. No precision or recall claim is allowed until external evidence is recorded and independently adjudicated.",
        "",
        "## Sample",
        "",
        f"- population: {summary['population_branches']} candidate branches",
        f"- sample: {summary['sample_branches']} branches",
        f"- required reviews: {summary['review_rows_required']} rows (two reviewers per branch)",
        f"- deterministic seed: {summary['seed']}",
        "",
        "## Review rule",
        "",
        "A `CONFIRMED` label requires evidence for both endpoint facilities and a continuous electrical route between them. `REJECTED` requires evidence that at least one endpoint or the route is wrong. Use `UNCERTAIN` when evidence is incomplete and `ABSTAIN` when the reviewer cannot assess the record. Internal reconstruction scores are context only and must not be used as truth.",
        "",
        "Acceptable evidence references include operator planning documents, another independently maintained grid layer, dated aerial/satellite inspection, or other public records that identify both facilities and their connection. Record the exact URL/document/page and access date.",
        "",
        "## Outputs",
        "",
        "- `pt_topology_validation_sample.csv`: sampled records and sampling weights",
        "- `pt_topology_validation_sample.geojson`: geometry review layer; redistribution remains blocked",
        "- `pt_topology_validation_reviews.csv`: persistent two-reviewer annotation file",
        "- `pt_topology_validation_review_template.csv`: resettable blank template",
        "",
        "Run `python src/summarize_topology_external_validation.py` after reviews are complete. The summarizer fails closed if fewer than 50 branches are adjudicated or evidence fields are missing.",
    ]
    write_text(REPORTS / "98_topology_external_validation_protocol.md", "\n".join(text) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
