"""Cross-validate PT60 candidate branches against external public topology layers.

The script is intentionally fail-closed. It does not convert external matches
into ground truth labels and does not overwrite the manual-review tables. The
main automated evidence source is OSM/OpenInfraMap 60 kV power infrastructure;
official Portuguese sources are recorded as consistency or boundary evidence
unless they expose an explicit branch-level topology relation.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests

import config
from metric_projection import PortugalTM06Projection
from utils import write_json, write_text


OUT = config.PROCESSED_DIR / "topology_validation"
REPORT = config.REPORTS_DIR / "102_pt60_external_topology_cross_validation.md"
BRANCHES = config.PROCESSED_DIR / "at_interfacility_candidate_branches.csv"

OSM_CACHE = OUT / "pt_osm_openinframap_60kv_power_ways.json"
OSM_EVIDENCE = OUT / "pt_osm_openinframap_60kv_evidence.csv"
MATCHES = OUT / "pt_topology_cross_validation_osm_matches.csv"
SOURCE_AUDIT = OUT / "pt_topology_cross_validation_source_audit.csv"
SUMMARY_JSON = OUT / "pt_topology_cross_validation_summary.json"

METRIC_PROJECTION = PortugalTM06Projection()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_text(value: object) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def lonlat_to_xy(point: tuple[float, float]) -> tuple[float, float]:
    lon, lat = point
    return METRIC_PROJECTION.xy(lon, lat)


def parse_line_part(value: object) -> list[tuple[float, float]]:
    if not isinstance(value, list):
        return []
    part: list[tuple[float, float]] = []
    for point in value:
        if (
            isinstance(point, list)
            and len(point) >= 2
            and isinstance(point[0], (int, float))
            and isinstance(point[1], (int, float))
        ):
            part.append((float(point[0]), float(point[1])))
    return part if len(part) >= 2 else []


def parse_branch_geometry(raw: object) -> list[list[tuple[float, float]]]:
    try:
        geometry = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    coordinates = geometry.get("coordinates", [])
    geometry_type = geometry.get("type")
    if geometry_type == "LineString":
        part = parse_line_part(coordinates)
        return [part] if part else []
    if geometry_type == "MultiLineString":
        parts = [parse_line_part(part) for part in coordinates if isinstance(part, list)]
        return [part for part in parts if part]
    return []


def osm_geometry_to_parts(element: dict[str, object]) -> list[list[tuple[float, float]]]:
    geometry = element.get("geometry")
    if not isinstance(geometry, list):
        return []
    part: list[tuple[float, float]] = []
    for point in geometry:
        if isinstance(point, dict) and "lon" in point and "lat" in point:
            part.append((float(point["lon"]), float(point["lat"])))
    return [part] if len(part) >= 2 else []


def part_length_m(part: list[tuple[float, float]]) -> float:
    xy = [lonlat_to_xy(point) for point in part]
    return sum(math.dist(a, b) for a, b in zip(xy, xy[1:]))


def geometry_length_m(parts: list[list[tuple[float, float]]]) -> float:
    return sum(part_length_m(part) for part in parts)


def bbox(parts: list[list[tuple[float, float]]]) -> tuple[float, float, float, float] | None:
    points = [point for part in parts for point in part]
    if not points:
        return None
    lon = [point[0] for point in points]
    lat = [point[1] for point in points]
    return min(lon), min(lat), max(lon), max(lat)


def bboxes_far(
    left: tuple[float, float, float, float] | None,
    right: tuple[float, float, float, float] | None,
    margin_deg: float,
) -> bool:
    if left is None or right is None:
        return True
    return (
        left[2] + margin_deg < right[0]
        or right[2] + margin_deg < left[0]
        or left[3] + margin_deg < right[1]
        or right[3] + margin_deg < left[1]
    )


def point_to_segment_distance_m(
    point_xy: tuple[float, float],
    a_xy: tuple[float, float],
    b_xy: tuple[float, float],
) -> float:
    ax, ay = a_xy
    bx, by = b_xy
    px, py = point_xy
    dx = bx - ax
    dy = by - ay
    denom = dx * dx + dy * dy
    if denom == 0:
        return math.dist(point_xy, a_xy)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def segments(parts: list[list[tuple[float, float]]]) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    out: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for part in parts:
        xy = [lonlat_to_xy(point) for point in part]
        out.extend(zip(xy, xy[1:]))
    return out


def sample_part(part: list[tuple[float, float]], spacing_m: float = 500.0) -> list[tuple[float, float]]:
    if len(part) < 2:
        return []
    out: list[tuple[float, float]] = [lonlat_to_xy(part[0])]
    xy = [lonlat_to_xy(point) for point in part]
    for a, b in zip(xy, xy[1:]):
        length = math.dist(a, b)
        steps = max(1, int(length // spacing_m))
        for step in range(1, steps + 1):
            frac = step / steps
            out.append((a[0] + (b[0] - a[0]) * frac, a[1] + (b[1] - a[1]) * frac))
    return out


def sample_geometry(parts: list[list[tuple[float, float]]], spacing_m: float = 500.0) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for part in parts:
        points.extend(sample_part(part, spacing_m=spacing_m))
    return points


def min_distance_to_segments(point_xy: tuple[float, float], segs: list[tuple[tuple[float, float], tuple[float, float]]]) -> float:
    if not segs:
        return math.inf
    return min(point_to_segment_distance_m(point_xy, a, b) for a, b in segs)


def geometry_distance_metrics(
    branch_parts: list[list[tuple[float, float]]],
    osm_parts: list[list[tuple[float, float]]],
) -> dict[str, float]:
    branch_points = sample_geometry(branch_parts, spacing_m=400.0)
    osm_points = sample_geometry(osm_parts, spacing_m=400.0)
    branch_segments = segments(branch_parts)
    osm_segments = segments(osm_parts)
    if not branch_points or not osm_points or not branch_segments or not osm_segments:
        return {
            "min_distance_m": math.inf,
            "median_branch_to_osm_m": math.inf,
            "branch_coverage_250m": 0.0,
            "branch_coverage_500m": 0.0,
            "osm_coverage_500m": 0.0,
        }
    b_to_o = [min_distance_to_segments(point, osm_segments) for point in branch_points]
    o_to_b = [min_distance_to_segments(point, branch_segments) for point in osm_points]
    b_sorted = sorted(b_to_o)
    median = b_sorted[len(b_sorted) // 2]
    return {
        "min_distance_m": float(min(min(b_to_o), min(o_to_b))),
        "median_branch_to_osm_m": float(median),
        "branch_coverage_250m": float(sum(d <= 250 for d in b_to_o) / len(b_to_o)),
        "branch_coverage_500m": float(sum(d <= 500 for d in b_to_o) / len(b_to_o)),
        "osm_coverage_500m": float(sum(d <= 500 for d in o_to_b) / len(o_to_b)),
    }


def token_score(name: object, evidence_text: str) -> float:
    norm = normalize_text(name)
    if not norm or not evidence_text:
        return 0.0
    if len(norm) >= 3 and norm in evidence_text:
        return 1.0
    tokens = [tok for tok in norm.split() if len(tok) >= 3 and tok not in {"subestacao", "posto", "corte", "ren"}]
    if not tokens:
        return 0.0
    hits = sum(tok in evidence_text for tok in tokens)
    return hits / len(tokens)


def voltage_is_60kv(tags: dict[str, object]) -> bool:
    voltage = normalize_text(tags.get("voltage", ""))
    return "60000" in voltage or "60 kv" in voltage or voltage == "60kv"


def fetch_osm_60kv_power_ways(cache_path: Path, refresh: bool) -> dict[str, object]:
    if cache_path.exists() and not refresh:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    query = """
[out:json][timeout:90];
area["ISO3166-1"="PT"][admin_level=2]->.pt;
(
  way(area.pt)["power"~"line|cable"]["voltage"~"(^|;)60000($|;)|60 ?kV",i];
);
out tags geom;
"""
    response = requests.post(
        "https://overpass-api.de/api/interpreter",
        data={"data": query},
        timeout=120,
        headers={"User-Agent": "PortugueseOPD topology cross-validation research"},
    )
    response.raise_for_status()
    data = response.json()
    cache_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return data


def build_osm_evidence(osm_data: dict[str, object]) -> pd.DataFrame:
    records: list[dict[str, object]] = []
    for element in osm_data.get("elements", []):
        if not isinstance(element, dict):
            continue
        tags = element.get("tags", {})
        if not isinstance(tags, dict):
            tags = {}
        parts = osm_geometry_to_parts(element)
        if not parts:
            continue
        bb = bbox(parts)
        records.append(
            {
                "osm_type": element.get("type"),
                "osm_id": element.get("id"),
                "power": tags.get("power", ""),
                "voltage": tags.get("voltage", ""),
                "operator": tags.get("operator", ""),
                "name": tags.get("name", ""),
                "ref": tags.get("ref", ""),
                "old_ref": tags.get("old_ref", ""),
                "old_name": tags.get("old_name", ""),
                "circuits": tags.get("circuits", ""),
                "cables": tags.get("cables", ""),
                "frequency": tags.get("frequency", ""),
                "is_60kv": voltage_is_60kv(tags),
                "geometry_length_km": geometry_length_m(parts) / 1000.0,
                "bbox_west": bb[0] if bb else math.nan,
                "bbox_south": bb[1] if bb else math.nan,
                "bbox_east": bb[2] if bb else math.nan,
                "bbox_north": bb[3] if bb else math.nan,
                "_parts": parts,
                "_tags_text": normalize_text(" ".join(str(tags.get(key, "")) for key in ("name", "old_name", "ref", "old_ref", "operator"))),
            }
        )
    return pd.DataFrame(records)


def classify_match(row: dict[str, object]) -> tuple[str, str]:
    both_names = float(row["from_name_score"]) >= 0.8 and float(row["to_name_score"]) >= 0.8
    one_name = float(row["from_name_score"]) >= 0.8 or float(row["to_name_score"]) >= 0.8
    near = float(row["min_distance_m"]) <= 250
    close = float(row["median_branch_to_osm_m"]) <= 500
    coverage = float(row["branch_coverage_500m"]) >= 0.5 or float(row["osm_coverage_500m"]) >= 0.5
    operator = "e redes" in normalize_text(row.get("osm_operator", ""))
    if both_names and operator:
        return "OSM_NAME_OPERATOR_STRONG", "Both endpoint names appear in an OSM 60 kV E-REDES record."
    if both_names:
        return "OSM_NAME_STRONG", "Both endpoint names appear in an OSM 60 kV record."
    if near and close and coverage and operator:
        return "OSM_GEOMETRY_OPERATOR_STRONG", "Candidate geometry is close to an OSM 60 kV E-REDES corridor."
    if near and coverage:
        return "OSM_GEOMETRY_MEDIUM", "Candidate geometry overlaps an OSM 60 kV corridor."
    if one_name and near:
        return "OSM_PARTIAL_NAME_NEARBY", "One endpoint name appears and the OSM corridor is nearby."
    if float(row["min_distance_m"]) <= 1000:
        return "OSM_NEARBY_WEAK", "An OSM 60 kV feature is nearby but evidence is incomplete."
    return "NO_EXTERNAL_OSM_MATCH", "No close OSM 60 kV evidence found."


def cross_validate(branches: pd.DataFrame, osm: pd.DataFrame) -> pd.DataFrame:
    branch_records: list[dict[str, object]] = []
    osm_records = osm.to_dict("records")
    for branch in branches.to_dict("records"):
        branch_parts = parse_branch_geometry(branch.get("geometry"))
        branch_bbox = bbox(branch_parts)
        candidates: list[dict[str, object]] = []
        for evidence in osm_records:
            osm_bbox = (
                evidence["bbox_west"],
                evidence["bbox_south"],
                evidence["bbox_east"],
                evidence["bbox_north"],
            )
            if bboxes_far(branch_bbox, osm_bbox, margin_deg=0.08):
                continue
            metrics = geometry_distance_metrics(branch_parts, evidence["_parts"])
            text = str(evidence["_tags_text"])
            from_score = token_score(branch.get("from_facility_name"), text)
            to_score = token_score(branch.get("to_facility_name"), text)
            candidate = {
                "branch_id": branch.get("branch_id"),
                "from_facility_code": branch.get("from_facility_code"),
                "from_facility_name": branch.get("from_facility_name"),
                "to_facility_code": branch.get("to_facility_code"),
                "to_facility_name": branch.get("to_facility_name"),
                "branch_voltage": branch.get("voltage"),
                "branch_length_km": branch.get("total_length_km"),
                "branch_confidence_score": branch.get("confidence_score"),
                "osm_id": evidence.get("osm_id"),
                "osm_power": evidence.get("power"),
                "osm_voltage": evidence.get("voltage"),
                "osm_operator": evidence.get("operator"),
                "osm_name": evidence.get("name"),
                "osm_ref": evidence.get("ref"),
                "osm_old_ref": evidence.get("old_ref"),
                "osm_old_name": evidence.get("old_name"),
                "osm_circuits": evidence.get("circuits"),
                "osm_cables": evidence.get("cables"),
                "osm_length_km": evidence.get("geometry_length_km"),
                "from_name_score": from_score,
                "to_name_score": to_score,
                **metrics,
            }
            category, reason = classify_match(candidate)
            candidate["external_evidence_status"] = category
            candidate["evidence_reason"] = reason
            candidate["osm_url"] = f"https://www.openstreetmap.org/way/{evidence.get('osm_id')}"
            candidates.append(candidate)
        if candidates:
            best = sorted(
                candidates,
                key=lambda item: (
                    item["external_evidence_status"] == "NO_EXTERNAL_OSM_MATCH",
                    -float(item["from_name_score"] + item["to_name_score"]),
                    -float(item["branch_coverage_500m"]),
                    float(item["median_branch_to_osm_m"]),
                ),
            )[0]
        else:
            best = {
                "branch_id": branch.get("branch_id"),
                "from_facility_code": branch.get("from_facility_code"),
                "from_facility_name": branch.get("from_facility_name"),
                "to_facility_code": branch.get("to_facility_code"),
                "to_facility_name": branch.get("to_facility_name"),
                "branch_voltage": branch.get("voltage"),
                "branch_length_km": branch.get("total_length_km"),
                "branch_confidence_score": branch.get("confidence_score"),
                "osm_id": "",
                "osm_power": "",
                "osm_voltage": "",
                "osm_operator": "",
                "osm_name": "",
                "osm_ref": "",
                "osm_old_ref": "",
                "osm_old_name": "",
                "osm_circuits": "",
                "osm_cables": "",
                "osm_length_km": math.nan,
                "from_name_score": 0.0,
                "to_name_score": 0.0,
                "min_distance_m": math.inf,
                "median_branch_to_osm_m": math.inf,
                "branch_coverage_250m": 0.0,
                "branch_coverage_500m": 0.0,
                "osm_coverage_500m": 0.0,
                "external_evidence_status": "NO_EXTERNAL_OSM_MATCH",
                "evidence_reason": "No OSM 60 kV feature passed the bbox prefilter.",
                "osm_url": "",
            }
        branch_records.append(best)
    return pd.DataFrame(branch_records)


def source_audit() -> pd.DataFrame:
    rows = [
        {
            "source_id": "E_REDES_PORTAL_CATALOG",
            "source_name": "E-REDES Open Data catalog",
            "source_type": "official_operator_open_data",
            "url": "https://e-redes.opendatasoft.com/api/explore/v2.1/catalog/datasets?limit=100",
            "topology_validation_role": "official_consistency_only",
            "branch_truth_capability": "no_complete_branch_truth_table_found",
            "notes": "Useful for facility, load, reception-capacity, and network-feature consistency; not independent branch-level truth when the reconstructed topology is derived from E-REDES topology layers.",
        },
        {
            "source_id": "REN_PUBLIC",
            "source_name": "REN public website and electricity market pages",
            "source_type": "official_transmission_operator_reference",
            "url": "https://www.ren.pt/",
            "topology_validation_role": "rnt_boundary_context",
            "branch_truth_capability": "limited_for_60kv_distribution",
            "notes": "Useful for RNT interface and high-voltage boundary context; not a full E-REDES 60 kV distribution topology source.",
        },
        {
            "source_id": "ENTSOE_MAP",
            "source_name": "ENTSO-E transmission system map",
            "source_type": "official_european_transmission_reference",
            "url": "https://www.entsoe.eu/data/map/",
            "topology_validation_role": "transmission_boundary_context",
            "branch_truth_capability": "not_distribution_60kv_truth",
            "notes": "Transmission-level reference; useful for cross-border/RNT context rather than E-REDES 60 kV branch validation.",
        },
        {
            "source_id": "OSM_OPENINFRAMAP_60KV",
            "source_name": "OpenStreetMap/OpenInfraMap 60 kV power ways",
            "source_type": "independent_public_geospatial_layer",
            "url": "https://www.openinframap.org/",
            "topology_validation_role": "external_corridor_and_name_evidence",
            "branch_truth_capability": "partial_independent_evidence_not_official_truth",
            "notes": "Some records include voltage=60000, operator=E-REDES, circuit counts, refs, and endpoint names. Suitable for triangulation; not operator validation. Independence from E-REDES source geometry must be checked before treating it as a fully independent evidence source.",
        },
    ]
    return pd.DataFrame(rows)


def write_report(summary: dict[str, object], status_counts: pd.Series, examples: pd.DataFrame) -> None:
    lines = [
        "# 102 PT60 External Topology Cross-Validation",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "## Scope",
        "",
        "This report tests whether independent public sources can provide external evidence for the PT60-Candidate branch topology. It does not replace operator validation and it does not overwrite the manual review protocol.",
        "",
        "## Inputs",
        "",
        f"- candidate branches: `{BRANCHES}`",
        f"- OSM/OpenInfraMap cache: `{OSM_CACHE}`",
        f"- source audit: `{SOURCE_AUDIT}`",
        "",
        "## Source Findings",
        "",
        "- E-REDES official open-data tables remain useful for facility, voltage, load, capacity, and consistency checks, but they are not an independent branch-level truth table when the candidate topology is derived from E-REDES topology geometries.",
        "- REN and ENTSO-E sources are useful for RNT/transmission boundary context, not for complete 60 kV distribution branch validation.",
        "- OSM/OpenInfraMap contains external public 60 kV records in Portugal, including records with `voltage=60000`, `operator=E-REDES`, names, references, and circuit/cable tags. These are suitable for triangulation, not official truth. Their independence from E-REDES source geometry must be checked before using them as a strict external validation layer.",
        "",
        "## Automated Evidence Summary",
        "",
        f"- branches tested: {summary['branches_tested']}",
        f"- OSM 60 kV evidence ways downloaded: {summary['osm_60kv_way_count']}",
        f"- branches with any non-empty OSM match category: {summary['branches_with_osm_evidence']}",
        f"- strong OSM evidence branches: {summary['strong_osm_evidence_branches']}",
        f"- medium OSM evidence branches: {summary['medium_osm_evidence_branches']}",
        f"- weak OSM nearby branches: {summary['weak_osm_evidence_branches']}",
        f"- no OSM match branches: {summary['no_osm_match_branches']}",
        "",
        "## Status Counts",
        "",
        "| external_evidence_status | branches |",
        "|---|---:|",
    ]
    for status, count in status_counts.items():
        lines.append(f"| {status} | {int(count)} |")
    lines.extend(["", "## Example Matches", "", "| branch_id | endpoints | status | OSM evidence | distance_m | coverage_500m |", "|---|---|---|---|---:|---:|"])
    for row in examples.to_dict("records"):
        endpoints = f"{row['from_facility_name']} - {row['to_facility_name']}"
        evidence = row.get("osm_name") or row.get("osm_ref") or row.get("osm_url") or ""
        lines.append(
            f"| {row['branch_id']} | {endpoints} | {row['external_evidence_status']} | {evidence} | "
            f"{float(row['min_distance_m']):.1f} | {float(row['branch_coverage_500m']):.2f} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "The automated OSM/OpenInfraMap layer can reduce manual review effort by prioritizing branches with independent corridor/name evidence. It cannot by itself establish precision or recall because OSM coverage is incomplete and not an official operator truth source.",
            "",
            "Recommended paper language: `externally triangulated`, not `operator-validated`.",
            "",
            "## Next Action",
            "",
            "Use the match table to pre-fill evidence links in the 100-branch manual validation sample. Branches with strong OSM evidence should still be adjudicated, while branches without OSM evidence should be reviewed against planning documents, satellite imagery, or operator/regulator records.",
        ]
    )
    write_text(REPORT, "\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-osm", action="store_true", help="Refresh OSM/OpenInfraMap evidence from Overpass.")
    parser.add_argument("--offline", action="store_true", help="Use the cached OSM response only.")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    if not BRANCHES.exists():
        raise RuntimeError(f"Missing candidate branch table: {BRANCHES}")

    branches = pd.read_csv(BRANCHES)
    if args.offline and not OSM_CACHE.exists():
        raise RuntimeError(f"Offline mode requested but cache is missing: {OSM_CACHE}")
    if args.offline:
        osm_data = json.loads(OSM_CACHE.read_text(encoding="utf-8"))
    else:
        try:
            osm_data = fetch_osm_60kv_power_ways(OSM_CACHE, refresh=args.refresh_osm)
        except Exception as exc:
            if not OSM_CACHE.exists():
                raise RuntimeError("OSM/OpenInfraMap download failed and no cache is available.") from exc
            osm_data = json.loads(OSM_CACHE.read_text(encoding="utf-8"))

    osm = build_osm_evidence(osm_data)
    osm_public = osm.drop(columns=[col for col in ("_parts", "_tags_text") if col in osm.columns])
    osm_public.to_csv(OSM_EVIDENCE, index=False)

    matches = cross_validate(branches, osm)
    matches.to_csv(MATCHES, index=False)

    audit = source_audit()
    audit.to_csv(SOURCE_AUDIT, index=False)

    counts = matches["external_evidence_status"].value_counts().sort_index()
    strong_statuses = {
        "OSM_NAME_OPERATOR_STRONG",
        "OSM_NAME_STRONG",
        "OSM_GEOMETRY_OPERATOR_STRONG",
    }
    medium_statuses = {"OSM_GEOMETRY_MEDIUM", "OSM_PARTIAL_NAME_NEARBY"}
    weak_statuses = {"OSM_NEARBY_WEAK"}
    summary = {
        "generated_at": utc_now(),
        "branches_tested": int(len(matches)),
        "osm_60kv_way_count": int(len(osm)),
        "branches_with_osm_evidence": int((matches["external_evidence_status"] != "NO_EXTERNAL_OSM_MATCH").sum()),
        "strong_osm_evidence_branches": int(matches["external_evidence_status"].isin(strong_statuses).sum()),
        "medium_osm_evidence_branches": int(matches["external_evidence_status"].isin(medium_statuses).sum()),
        "weak_osm_evidence_branches": int(matches["external_evidence_status"].isin(weak_statuses).sum()),
        "no_osm_match_branches": int((matches["external_evidence_status"] == "NO_EXTERNAL_OSM_MATCH").sum()),
        "status_counts": {str(k): int(v) for k, v in counts.items()},
        "outputs": {
            "osm_evidence": str(OSM_EVIDENCE.relative_to(config.ROOT_DIR)),
            "matches": str(MATCHES.relative_to(config.ROOT_DIR)),
            "source_audit": str(SOURCE_AUDIT.relative_to(config.ROOT_DIR)),
            "report": str(REPORT.relative_to(config.ROOT_DIR)),
        },
            "caveat": "OSM/OpenInfraMap matches are external public evidence, not official operator validation or ground truth; source independence must be checked before using them as strict external validation.",
    }
    write_json(SUMMARY_JSON, summary)

    examples = matches[matches["external_evidence_status"] != "NO_EXTERNAL_OSM_MATCH"].head(10)
    write_report(summary, counts, examples)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
