"""Audit OSM/OpenInfraMap evidence independence for PT60 candidate matching.

This audit separates public-source concordance from stronger corroboration. It
does not treat OSM/OpenInfraMap as ground truth and it does not treat
``operator=E-REDES`` as operator validation. OSM element metadata are required so
that edit timestamps, versions, changesets, and mapper identity can be recorded.
"""

from __future__ import annotations

import argparse
import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import requests

import config
from utils import utc_now, write_json, write_text


OUT = config.PROCESSED_DIR / "topology_validation"
MATCHES = OUT / "pt_topology_cross_validation_osm_matches.csv"
OSM_META_CACHE = OUT / "pt_osm_openinframap_60kv_power_ways_meta.json"
OSM_HISTORY_CACHE = OUT / "pt_osm_matched_way_histories.json"
AUDIT_CSV = OUT / "pt_osm_openinframap_independence_audit.csv"
HISTORY_CSV = OUT / "pt_osm_openinframap_matched_way_history_audit.csv"
BRANCH_AUDIT_CSV = OUT / "pt_topology_cross_validation_osm_matches_independence_audit.csv"
SUMMARY_JSON = OUT / "pt_osm_openinframap_independence_audit_summary.json"
REPORT = config.REPORTS_DIR / "105_pt60_osm_openinframap_independence_audit.md"


OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OSM_WAY_HISTORY_URL = "https://api.openstreetmap.org/api/0.6/way/{osm_id}/history"
OVERPASS_QUERY = """
[out:json][timeout:120];
area["ISO3166-1"="PT"][admin_level=2]->.pt;
(
  way(area.pt)["power"~"line|cable"]["voltage"~"(^|;)60000($|;)|60 ?kV",i];
);
out tags meta;
"""


def fetch_osm_meta(cache_path: Path, refresh: bool) -> dict[str, object]:
    if cache_path.exists() and not refresh:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    response = requests.post(
        OVERPASS_URL,
        data={"data": OVERPASS_QUERY},
        timeout=150,
        headers={"User-Agent": "PortugueseOPD OSM independence audit research"},
    )
    response.raise_for_status()
    data = response.json()
    cache_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return data


def fetch_way_history(osm_id: int, pause_seconds: float) -> list[dict[str, object]]:
    response = requests.get(
        OSM_WAY_HISTORY_URL.format(osm_id=osm_id),
        timeout=60,
        headers={"User-Agent": "PortugueseOPD OSM independence audit research"},
    )
    response.raise_for_status()
    if pause_seconds > 0:
        time.sleep(pause_seconds)
    root = ET.fromstring(response.text)
    rows: list[dict[str, object]] = []
    for way in root.findall("way"):
        tags = {tag.attrib.get("k", ""): tag.attrib.get("v", "") for tag in way.findall("tag")}
        rows.append(
            {
                "osm_id": osm_id,
                "version": int(way.attrib.get("version", "0") or 0),
                "timestamp": way.attrib.get("timestamp", ""),
                "changeset": way.attrib.get("changeset", ""),
                "user": way.attrib.get("user", ""),
                "uid": way.attrib.get("uid", ""),
                "tag_source": tags.get("source", ""),
                "tag_source_ref": tags.get("source:ref", ""),
                "tag_note": tags.get("note", ""),
                "tag_description": tags.get("description", ""),
                "tag_operator": tags.get("operator", ""),
                "tag_name": tags.get("name", ""),
                "tag_ref": tags.get("ref", ""),
                "tag_old_name": tags.get("old_name", ""),
                "tag_old_ref": tags.get("old_ref", ""),
            }
        )
    return rows


def fetch_histories(osm_ids: list[int], cache_path: Path, refresh: bool, pause_seconds: float) -> dict[str, list[dict[str, object]]]:
    if cache_path.exists() and not refresh:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    histories: dict[str, list[dict[str, object]]] = {}
    for idx, osm_id in enumerate(osm_ids, start=1):
        histories[str(osm_id)] = fetch_way_history(osm_id, pause_seconds=pause_seconds)
        if idx % 25 == 0:
            print(f"Fetched OSM histories: {idx}/{len(osm_ids)}")
    cache_path.write_text(json.dumps(histories, indent=2, ensure_ascii=False), encoding="utf-8")
    return histories


def text_present(value: object) -> bool:
    return not pd.isna(value) and bool(str(value).strip())


def normalize_operator(value: object) -> str:
    return "" if value is None else str(value).strip().lower()


def provenance_risk_text(value: object) -> bool:
    text = "" if value is None else str(value).strip().lower()
    risk_terms = (
        "e-redes",
        "eredes",
        "edp",
        "open data",
        "opendata",
        "operator",
        "cadastro",
        "import",
        "imported",
    )
    return any(term in text for term in risk_terms)


def classify_evidence_role(row: pd.Series) -> str:
    has_name = text_present(row.get("osm_name")) or text_present(row.get("osm_old_name"))
    has_ref = text_present(row.get("osm_ref")) or text_present(row.get("osm_old_ref"))
    has_operator = "e-redes" in normalize_operator(row.get("osm_operator"))
    near = float(row.get("min_distance_m", 1e30)) <= 250
    coverage = float(row.get("branch_coverage_500m", 0.0)) >= 0.5 or float(row.get("osm_coverage_500m", 0.0)) >= 0.5
    name_score = float(row.get("from_name_score", 0.0)) + float(row.get("to_name_score", 0.0))
    roles: list[str] = []
    if name_score >= 1.6 and (has_name or has_ref):
        roles.append("endpoint_name_or_ref")
    elif name_score >= 0.8 and (has_name or has_ref):
        roles.append("partial_endpoint_name_or_ref")
    if near and coverage:
        roles.append("geometry_corridor")
    if has_operator:
        roles.append("operator_tag")
    return "+".join(roles) if roles else "nearby_or_incomplete"


def classify_independence(row: pd.Series) -> tuple[str, str]:
    """Return conservative independence category and reason."""
    evidence_role = str(row["evidence_role"])
    timestamp_present = text_present(row.get("osm_timestamp"))
    user_present = text_present(row.get("osm_user"))
    has_name_ref = "endpoint_name_or_ref" in evidence_role or "partial_endpoint_name_or_ref" in evidence_role
    has_geometry = "geometry_corridor" in evidence_role
    operator_only = evidence_role == "operator_tag" or evidence_role == "operator_tag+nearby_or_incomplete"
    explicit_source_risk = bool(row.get("history_source_risk", False)) or provenance_risk_text(row.get("tag_source"))

    if not timestamp_present:
        return "unknown", "OSM metadata timestamp is unavailable, so edit provenance cannot be inspected."
    if explicit_source_risk:
        return "possibly_same_source", "Current or historical OSM source/note fields contain terms compatible with operator/open-data derivation."
    if operator_only:
        return "possibly_same_source", "Evidence is limited to an operator tag, which is not operator confirmation."
    if has_geometry and not has_name_ref:
        return "unknown", "Geometry concordance is present, but public geometry may still derive from operator/open-data sources."
    if has_name_ref and has_geometry and user_present:
        return (
            "more_independent_public_evidence",
            "The OSM record has inspectable current/history metadata plus name/ref and corridor concordance, with no explicit source-tag derivation risk found; independence is still not guaranteed.",
        )
    if has_name_ref:
        return (
            "unknown",
            "Name/ref concordance is present, but source derivation cannot be excluded from available metadata alone.",
        )
    return "unknown", "Evidence is incomplete or lacks a clear independence signal."


def build_meta_table(osm_meta: dict[str, object]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for element in osm_meta.get("elements", []):
        if not isinstance(element, dict):
            continue
        tags = element.get("tags", {})
        if not isinstance(tags, dict):
            tags = {}
        rows.append(
            {
                "osm_type": element.get("type"),
                "osm_id": element.get("id"),
                "osm_timestamp": element.get("timestamp", ""),
                "osm_version": element.get("version", ""),
                "osm_changeset": element.get("changeset", ""),
                "osm_user": element.get("user", ""),
                "osm_uid": element.get("uid", ""),
                "tag_power": tags.get("power", ""),
                "tag_voltage": tags.get("voltage", ""),
                "tag_operator": tags.get("operator", ""),
                "tag_name": tags.get("name", ""),
                "tag_ref": tags.get("ref", ""),
                "tag_old_ref": tags.get("old_ref", ""),
                "tag_old_name": tags.get("old_name", ""),
                "tag_circuits": tags.get("circuits", ""),
                "tag_cables": tags.get("cables", ""),
                "tag_source": tags.get("source", ""),
                "tag_source_ref": tags.get("source:ref", ""),
                "tag_note": tags.get("note", ""),
                "tag_description": tags.get("description", ""),
            }
        )
    return pd.DataFrame(rows)


def build_history_table(histories: dict[str, list[dict[str, object]]]) -> pd.DataFrame:
    rows = [row for history in histories.values() for row in history]
    if not rows:
        return pd.DataFrame(columns=["osm_id"])
    return pd.DataFrame(rows)


def summarize_history(history: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    if history.empty:
        return pd.DataFrame(columns=["osm_id"])
    for osm_id, group in history.groupby("osm_id", sort=True):
        source_like_values: list[str] = []
        for col in ["tag_source", "tag_source_ref", "tag_note", "tag_description"]:
            source_like_values.extend(str(value) for value in group[col].dropna().tolist() if str(value).strip())
        rows.append(
            {
                "osm_id": osm_id,
                "history_versions": int(group["version"].nunique()),
                "history_first_timestamp": str(group["timestamp"].min()),
                "history_last_timestamp": str(group["timestamp"].max()),
                "history_unique_users": int(group["user"].nunique(dropna=True)),
                "history_source_fields_observed": " | ".join(sorted(set(source_like_values))),
                "history_source_risk": any(provenance_risk_text(value) for value in source_like_values),
            }
        )
    return pd.DataFrame(rows)


def write_report(summary: dict[str, object]) -> None:
    lines = [
        "# 105 PT60 OSM/OpenInfraMap Evidence Independence Audit",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "## Scope",
        "",
        "This audit classifies the provenance risk of OSM/OpenInfraMap evidence used for PT60 public-source triangulation. It does not treat OSM as ground truth and it does not treat `operator=E-REDES` as operator validation.",
        "",
        "## Inputs",
        "",
        f"- matched retained branches: `{summary['outputs']['branch_audit']}`",
        f"- OSM meta cache: `{summary['outputs']['osm_meta_cache']}`",
        f"- OSM history cache: `{summary['outputs']['osm_history_cache']}`",
        "",
        "## Results",
        "",
        f"- OSM records with metadata: {summary['osm_records_with_metadata']}",
        f"- matched branches audited: {summary['matched_branches_audited']}",
        f"- branches with timestamped matched OSM metadata: {summary['branches_with_timestamped_osm_metadata']}",
        f"- unique matched OSM ways with history: {summary['unique_matched_osm_ways_with_history']}",
        f"- branches with historical source-tag risk: {summary['branches_with_history_source_risk']}",
        "",
        "| independence_category | branches |",
        "|---|---:|",
    ]
    for category, count in summary["branch_independence_counts"].items():
        lines.append(f"| {category} | {int(count)} |")
    lines.extend(
        [
            "",
            "## Evidence Role Counts",
            "",
            "| evidence_role | branches |",
            "|---|---:|",
        ]
    )
    for role, count in summary["branch_evidence_role_counts"].items():
        lines.append(f"| {role} | {int(count)} |")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "Use `public-source triangulation` or `public-source concordance` in the manuscript. Do not use `independent validation`, `operator validation`, precision, recall, or accuracy language from these categories alone. Branches labelled `more_independent_public_evidence` have stronger inspectable public evidence, but their source derivation is still not guaranteed.",
        ]
    )
    write_text(REPORT, "\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-meta", action="store_true", help="Refresh OSM/OpenInfraMap metadata from Overpass.")
    parser.add_argument("--refresh-history", action="store_true", help="Refresh OSM way histories for matched OSM ways.")
    parser.add_argument("--history-pause-seconds", type=float, default=0.05)
    args = parser.parse_args()

    if not MATCHES.exists():
        raise RuntimeError(f"Missing OSM match table: {MATCHES}")
    OUT.mkdir(parents=True, exist_ok=True)
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    osm_meta = fetch_osm_meta(OSM_META_CACHE, refresh=args.refresh_meta)
    meta = build_meta_table(osm_meta)
    if meta.empty:
        raise RuntimeError("Fail-closed: OSM metadata query returned no auditable elements.")
    meta.to_csv(AUDIT_CSV, index=False)

    matches = pd.read_csv(MATCHES)
    matched_osm_ids = sorted(int(value) for value in matches["osm_id"].dropna().astype(int).unique())
    histories = fetch_histories(
        matched_osm_ids,
        cache_path=OSM_HISTORY_CACHE,
        refresh=args.refresh_history,
        pause_seconds=args.history_pause_seconds,
    )
    history = build_history_table(histories)
    history.to_csv(HISTORY_CSV, index=False)
    history_summary = summarize_history(history)
    audited = matches.merge(
        meta,
        on="osm_id",
        how="left",
        validate="many_to_one",
    )
    audited = audited.merge(
        history_summary,
        on="osm_id",
        how="left",
        validate="many_to_one",
    )
    audited["evidence_role"] = audited.apply(classify_evidence_role, axis=1)
    classified = audited.apply(classify_independence, axis=1, result_type="expand")
    audited["independence_category"] = classified[0]
    audited["independence_reason"] = classified[1]
    audited["operator_tag_is_operator_confirmation"] = False
    audited.to_csv(BRANCH_AUDIT_CSV, index=False)

    branch_counts = audited["independence_category"].value_counts().sort_index()
    role_counts = audited["evidence_role"].value_counts().sort_index()
    timestamped = audited["osm_timestamp"].apply(text_present)
    history_source_risk = audited["history_source_risk"].fillna(False).astype(bool)
    summary = {
        "generated_at": utc_now(),
        "osm_records_with_metadata": int(len(meta)),
        "matched_branches_audited": int(len(audited)),
        "branches_with_timestamped_osm_metadata": int(timestamped.sum()),
        "unique_matched_osm_ways": int(len(matched_osm_ids)),
        "unique_matched_osm_ways_with_history": int(history["osm_id"].nunique()) if not history.empty else 0,
        "history_versions_total": int(len(history)),
        "branches_with_history_source_risk": int(history_source_risk.sum()),
        "branch_independence_counts": {str(k): int(v) for k, v in branch_counts.items()},
        "branch_evidence_role_counts": {str(k): int(v) for k, v in role_counts.items()},
        "operator_tag_interpretation": "operator=E-REDES is treated as an OSM public tag, not operator confirmation.",
        "recommended_manuscript_language": "public-source triangulation/concordance",
        "prohibited_manuscript_language": [
            "operator validation",
            "independent precision",
            "recall",
            "accuracy",
            "ground truth",
        ],
        "outputs": {
            "osm_meta_cache": str(OSM_META_CACHE.relative_to(config.ROOT_DIR)),
            "osm_history_cache": str(OSM_HISTORY_CACHE.relative_to(config.ROOT_DIR)),
            "osm_element_audit": str(AUDIT_CSV.relative_to(config.ROOT_DIR)),
            "osm_history_audit": str(HISTORY_CSV.relative_to(config.ROOT_DIR)),
            "branch_audit": str(BRANCH_AUDIT_CSV.relative_to(config.ROOT_DIR)),
            "summary": str(SUMMARY_JSON.relative_to(config.ROOT_DIR)),
            "report": str(REPORT.relative_to(config.ROOT_DIR)),
        },
    }
    write_json(SUMMARY_JSON, summary)
    write_report(summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
