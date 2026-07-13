"""Conservative topology repair diagnostics for Portuguese AT topology.

This script does not overwrite the accepted topology. It proposes endpoint-to-line
repair candidates for dangling/non-clean circuits and reports how many candidates
could plausibly improve connectivity after manual validation.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
OUT = PROCESSED / "topology_repair"
REPORTS = ROOT / "reports"
MAPS = REPORTS / "maps"

THRESHOLDS_M = (25.0, 50.0, 100.0)
HIGH_CONFIDENCE_THRESHOLD_M = 50.0
REPAIRABLE_CLASSES = {"single-facility", "isolated", "tap / multi-terminal", "ambiguous"}


def parse_json(value: Any, default: Any) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return default
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return default


def normalize_voltage(value: Any) -> str:
    text = str(value or "").lower().strip()
    return text.replace(" ", "")


def normalize_status(value: Any) -> str:
    return str(value or "").lower().strip()


def point_segment_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> tuple[float, float, float]:
    dx = bx - ax
    dy = by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay), ax, ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    sx = ax + t * dx
    sy = ay + t * dy
    return math.hypot(px - sx, py - sy), sx, sy


class LocalMetricProjection:
    def __init__(self, lon0: float, lat0: float, radius_m: float = 6_371_008.8) -> None:
        self.lon0 = lon0
        self.lat0 = lat0
        self.radius_m = radius_m

    def xy(self, lon: float, lat: float) -> tuple[float, float]:
        x = self.radius_m * math.radians(lon - self.lon0) * math.cos(math.radians(self.lat0))
        y = self.radius_m * math.radians(lat - self.lat0)
        return x, y


def geometry_parts(geometry: dict[str, Any]) -> list[list[list[float]]]:
    geom_type = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if geom_type == "LineString":
        return [coords]
    if geom_type == "MultiLineString":
        return [part for part in coords if part]
    return []


def build_projection(branches: pd.DataFrame) -> LocalMetricProjection:
    lons: list[float] = []
    lats: list[float] = []
    for geometry_text in branches["geometry"].dropna().astype(str):
        geometry = parse_json(geometry_text, {})
        for part in geometry_parts(geometry):
            for lon, lat, *_ in part:
                lons.append(float(lon))
                lats.append(float(lat))
    if not lons:
        raise RuntimeError("No branch geometry available for topology repair diagnostics.")
    return LocalMetricProjection(sum(lons) / len(lons), sum(lats) / len(lats))


def target_segments(branches: pd.DataFrame, projection: LocalMetricProjection) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for _, row in branches.iterrows():
        geometry = parse_json(row.get("geometry"), {})
        for part_idx, part in enumerate(geometry_parts(geometry)):
            points = [projection.xy(float(lon), float(lat)) for lon, lat, *_ in part]
            for seg_idx, ((ax, ay), (bx, by)) in enumerate(zip(points, points[1:])):
                segments.append(
                    {
                        "branch_id": row.get("branch_id"),
                        "circuit_id": row.get("circuit_id"),
                        "from_facility_code": row.get("from_facility_code"),
                        "to_facility_code": row.get("to_facility_code"),
                        "voltage": row.get("voltage"),
                        "status": row.get("status"),
                        "part_idx": part_idx,
                        "segment_idx": seg_idx,
                        "ax": ax,
                        "ay": ay,
                        "bx": bx,
                        "by": by,
                    }
                )
    return segments


def dangling_endpoints(classified: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    subset = classified[classified["classification"].isin(REPAIRABLE_CLASSES)].copy()
    for _, row in subset.iterrows():
        terminal_details = parse_json(row.get("terminal_details_json"), [])
        for terminal in terminal_details:
            facility_uid = str(terminal.get("facility_uid") or "")
            inside = bool(terminal.get("inside_facility"))
            candidate_count = int(terminal.get("candidate_facility_count") or 0)
            if row["classification"] == "isolated" or not facility_uid or not inside or candidate_count == 0:
                rows.append(
                    {
                        "circuit_id": row.get("circuit_id"),
                        "classification": row.get("classification"),
                        "endpoint_cluster_id": terminal.get("endpoint_cluster_id"),
                        "x": float(terminal.get("x")),
                        "y": float(terminal.get("y")),
                        "facility_uid": facility_uid,
                        "facility_code": terminal.get("facility_code") or "",
                        "voltage": row.get("voltage"),
                        "status": row.get("status"),
                        "source_line_ids": row.get("source_line_ids"),
                    }
                )
    return pd.DataFrame(rows)


def classify_candidate(endpoint: pd.Series, matches: list[dict[str, Any]]) -> dict[str, Any]:
    if not matches:
        return {"repair_classification": "too_far_no_repair"}
    compatible = [m for m in matches if m["voltage_compatible"] and m["status_compatible"]]
    best_pool = compatible or matches
    best = best_pool[0]
    same_distance = [m for m in best_pool if abs(m["distance_m"] - best["distance_m"]) <= 1.0]
    if not compatible:
        classification = "voltage_or_status_incompatible"
    elif len({m["branch_id"] for m in same_distance}) > 1:
        classification = "ambiguous_multiple_targets"
    else:
        classification = "endpoint_to_line_snap_candidate"
    out = dict(best)
    out["repair_classification"] = classification
    return out


def find_repair_candidates(endpoints: pd.DataFrame, segments: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for _, endpoint in endpoints.iterrows():
        nearby: list[dict[str, Any]] = []
        for seg in segments:
            dist, sx, sy = point_segment_distance(endpoint["x"], endpoint["y"], seg["ax"], seg["ay"], seg["bx"], seg["by"])
            if dist <= max(THRESHOLDS_M):
                nearby.append(
                    {
                        **seg,
                        "distance_m": round(float(dist), 3),
                        "snap_x": round(float(sx), 3),
                        "snap_y": round(float(sy), 3),
                        "voltage_compatible": normalize_voltage(endpoint["voltage"]) == normalize_voltage(seg["voltage"]),
                        "status_compatible": normalize_status(endpoint["status"]) == normalize_status(seg["status"]),
                    }
                )
        nearby = sorted(nearby, key=lambda item: item["distance_m"])
        selected = classify_candidate(endpoint, nearby[:5])
        threshold_bucket = "none"
        if "distance_m" in selected:
            for threshold in THRESHOLDS_M:
                if selected["distance_m"] <= threshold:
                    threshold_bucket = f"<={int(threshold)}m"
                    break
        rows.append(
            {
                **endpoint.to_dict(),
                "nearest_branch_id": selected.get("branch_id", ""),
                "nearest_circuit_id": selected.get("circuit_id", ""),
                "nearest_from_facility_code": selected.get("from_facility_code", ""),
                "nearest_to_facility_code": selected.get("to_facility_code", ""),
                "distance_m": selected.get("distance_m", ""),
                "threshold_bucket": threshold_bucket,
                "snap_x": selected.get("snap_x", ""),
                "snap_y": selected.get("snap_y", ""),
                "repair_classification": selected.get("repair_classification", "too_far_no_repair"),
                "voltage_compatible": selected.get("voltage_compatible", False),
                "status_compatible": selected.get("status_compatible", False),
                "manual_validation_required": True,
                "topology_status": "DIAGNOSTIC_REPAIR_CANDIDATE" if selected.get("repair_classification") == "endpoint_to_line_snap_candidate" else "NOT_REPAIRED",
            }
        )
    return pd.DataFrame(rows)


def graph_components(edges: list[tuple[str, str]]) -> dict[str, Any]:
    graph: dict[str, set[str]] = defaultdict(set)
    nodes: set[str] = set()
    for a, b in edges:
        if not a or not b or a == b:
            continue
        graph[a].add(b)
        graph[b].add(a)
        nodes.add(a)
        nodes.add(b)
    seen: set[str] = set()
    sizes: list[int] = []
    for node in nodes:
        if node in seen:
            continue
        q: deque[str] = deque([node])
        seen.add(node)
        size = 0
        while q:
            cur = q.popleft()
            size += 1
            for nxt in graph[cur]:
                if nxt not in seen:
                    seen.add(nxt)
                    q.append(nxt)
        sizes.append(size)
    sizes = sorted(sizes, reverse=True)
    return {
        "nodes": len(nodes),
        "edges": len(edges),
        "connected_components": len(sizes),
        "largest_component_size": sizes[0] if sizes else 0,
        "component_sizes_top10": sizes[:10],
    }


def repair_graph_summary(branches: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    base_edges = [(str(r.from_facility_code), str(r.to_facility_code)) for r in branches.itertuples()]
    high = candidates[
        (candidates["repair_classification"] == "endpoint_to_line_snap_candidate")
        & (pd.to_numeric(candidates["distance_m"], errors="coerce") <= HIGH_CONFIDENCE_THRESHOLD_M)
    ]
    repair_edges = base_edges.copy()
    for _, row in high.iterrows():
        dangling = str(row.get("facility_code") or "")
        if dangling:
            repair_edges.append((dangling, str(row["nearest_from_facility_code"])))
            repair_edges.append((dangling, str(row["nearest_to_facility_code"])))
    base = graph_components(base_edges)
    repaired = graph_components(repair_edges)
    return pd.DataFrame(
        [
            {"metric": key, "baseline": base[key], "with_high_confidence_candidates": repaired[key]}
            for key in ["nodes", "edges", "connected_components", "largest_component_size", "component_sizes_top10"]
        ]
    )


def markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No rows._\n"
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|") for c in cols) + " |")
    return "\n".join(lines) + "\n"


def write_report(candidates: pd.DataFrame, graph_summary: pd.DataFrame, summary: dict[str, Any]) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    class_counts = candidates["repair_classification"].value_counts().rename_axis("repair_classification").reset_index(name="count")
    threshold_counts = candidates["threshold_bucket"].value_counts().rename_axis("threshold_bucket").reset_index(name="count")
    text = [
        "# 15 AT Topology Repair Diagnostics",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "Scope: diagnostic repair candidates only. This report does not overwrite the accepted topology and does not claim validated branch terminals.",
        "",
        "## Summary",
        "",
        markdown_table(pd.DataFrame([summary]).drop(columns=["generated_at"])),
        "",
        "## Repair Classification Counts",
        "",
        markdown_table(class_counts),
        "",
        "## Distance Buckets",
        "",
        markdown_table(threshold_counts),
        "",
        "## Graph Impact If High-Confidence Candidates Are Accepted",
        "",
        markdown_table(graph_summary),
        "",
        "## Interpretation",
        "",
        "Rows marked `endpoint_to_line_snap_candidate` are candidates for manual/map validation. They remain diagnostic and should not be promoted to source-backed topology without validation against operator or additional topology data.",
    ]
    (REPORTS / "15_at_topology_repair.md").write_text("\n".join(text), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    classified = pd.read_csv(PROCESSED / "at_circuit_classification.csv")
    branches = pd.read_csv(PROCESSED / "at_interfacility_candidate_branches.csv")
    projection = build_projection(branches)
    endpoints = dangling_endpoints(classified)
    segments = target_segments(branches, projection)
    candidates = find_repair_candidates(endpoints, segments)
    graph_summary = repair_graph_summary(branches, candidates)

    candidates.to_csv(OUT / "at_topology_repair_candidates.csv", index=False)
    graph_summary.to_csv(OUT / "at_topology_repair_graph_summary.csv", index=False)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "diagnostic topology repair candidates only",
        "baseline_interfacility_branches": int(len(branches)),
        "non_clean_circuits_examined": int(classified["classification"].isin(REPAIRABLE_CLASSES).sum()),
        "dangling_endpoints_examined": int(len(endpoints)),
        "repair_candidates_total": int((candidates["repair_classification"] == "endpoint_to_line_snap_candidate").sum()),
        "high_confidence_candidates_le_50m": int(((candidates["repair_classification"] == "endpoint_to_line_snap_candidate") & (pd.to_numeric(candidates["distance_m"], errors="coerce") <= HIGH_CONFIDENCE_THRESHOLD_M)).sum()),
        "status": "DIAGNOSTIC_DONE",
        "publication_allowed": False,
        "manual_validation_required": True,
    }
    (OUT / "at_topology_repair_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    write_report(candidates, graph_summary, summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
