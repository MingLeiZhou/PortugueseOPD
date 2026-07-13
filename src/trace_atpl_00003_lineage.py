"""Trace ATPL_00003 source lineage and candidate repair interpretation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
OUT = PROCESSED / "acpf_s8_pathology_exclusion"
REPORTS = ROOT / "reports"
LINE_ID = "ATPL_00003"
FROM_FACILITY = "1101S5423500"
TO_FACILITY = "1101S5335200"
CIRCUIT_ID = "UF_0p5_voltage-status-aware_00007"


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def markdown_table(df: pd.DataFrame, max_rows: int = 40) -> str:
    if df.empty:
        return "_No rows._\n"
    view = df.head(max_rows).copy()
    cols = list(view.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in view.iterrows():
        lines.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|").replace("\n", " ") for c in cols) + " |")
    return "\n".join(lines) + "\n"


def parse_source_line_ids(value: Any) -> list[str]:
    if pd.isna(value):
        return []
    return [x.strip() for x in str(value).split(",") if x.strip()]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)

    candidate = read_csv(PROCESSED / "pandapower_schema" / "pt_line_table_candidate.csv")
    acpf = read_csv(PROCESSED / "acpf_ready" / "pt_line_table_acpf.csv")
    interfacility = read_csv(PROCESSED / "at_interfacility_candidate_branches.csv")
    classification = read_csv(PROCESSED / "at_circuit_classification.csv")
    endpoints = read_csv(PROCESSED / "at_line_endpoints.csv")
    endpoint_matches = read_csv(PROCESSED / "at_endpoint_matches.csv")
    bus = read_csv(PROCESSED / "pandapower_schema" / "pt_bus_table_candidate.csv")
    repair = read_csv(PROCESSED / "topology_repair" / "at_topology_repair_candidates.csv")
    parameter_estimates = read_csv(PROCESSED / "at_candidate_branch_parameter_estimates.csv")

    candidate_row = candidate[candidate["line_id"] == LINE_ID].copy() if len(candidate) else pd.DataFrame()
    acpf_row = acpf[acpf["line_id"] == LINE_ID].copy() if len(acpf) else pd.DataFrame()
    estimate_row = parameter_estimates[parameter_estimates["line_id"] == LINE_ID].copy() if len(parameter_estimates) and "line_id" in parameter_estimates.columns else pd.DataFrame()
    circuit_row = classification[classification["circuit_id"] == CIRCUIT_ID].copy() if len(classification) else pd.DataFrame()
    if circuit_row.empty and len(classification):
        circuit_row = classification[
            classification.get("terminal_facility_uids", pd.Series(dtype=str)).astype(str).str.contains(FROM_FACILITY, na=False)
            & classification.get("terminal_facility_uids", pd.Series(dtype=str)).astype(str).str.contains(TO_FACILITY, na=False)
        ].copy()

    source_ids: list[str] = []
    if len(circuit_row) and "source_line_ids" in circuit_row.columns:
        source_ids = parse_source_line_ids(circuit_row.iloc[0].get("source_line_ids"))
    if not source_ids and len(candidate_row) and "source_line_ids" in candidate_row.columns:
        source_ids = parse_source_line_ids(candidate_row.iloc[0].get("source_line_ids"))

    source_segments = pd.DataFrame()
    if source_ids and len(endpoints):
        source_segments = endpoints[endpoints["source_line_id"].astype(str).isin(source_ids)].copy()
    endpoint_match_rows = pd.DataFrame()
    if source_ids and len(endpoint_matches):
        if "source_line_id" in endpoint_matches.columns:
            endpoint_match_rows = endpoint_matches[endpoint_matches["source_line_id"].astype(str).isin(source_ids)].copy()
        elif "line_id" in endpoint_matches.columns:
            endpoint_match_rows = endpoint_matches[endpoint_matches["line_id"].astype(str).str.split("_").str[-1].isin(source_ids)].copy()

    facilities = pd.DataFrame()
    if len(bus):
        facilities = bus[bus["facility_code"].astype(str).isin([FROM_FACILITY, TO_FACILITY])].copy()

    parallel_or_duplicate = pd.DataFrame()
    if len(interfacility):
        f1 = interfacility.get("from_facility_code", pd.Series(dtype=str)).astype(str)
        f2 = interfacility.get("to_facility_code", pd.Series(dtype=str)).astype(str)
        parallel_or_duplicate = interfacility[((f1 == FROM_FACILITY) & (f2 == TO_FACILITY)) | ((f1 == TO_FACILITY) & (f2 == FROM_FACILITY))].copy()

    neighboring_same_facility = pd.DataFrame()
    if len(interfacility):
        f1 = interfacility.get("from_facility_code", pd.Series(dtype=str)).astype(str)
        f2 = interfacility.get("to_facility_code", pd.Series(dtype=str)).astype(str)
        neighboring_same_facility = interfacility[(f1.isin([FROM_FACILITY, TO_FACILITY])) | (f2.isin([FROM_FACILITY, TO_FACILITY]))].copy()

    repair_related = pd.DataFrame()
    if len(repair):
        repair_related = repair[
            repair.get("source_line_ids", pd.Series(dtype=str)).astype(str).apply(lambda x: any(sid in x for sid in source_ids))
            | repair.get("nearest_from_facility_code", pd.Series(dtype=str)).astype(str).isin([FROM_FACILITY, TO_FACILITY])
            | repair.get("nearest_to_facility_code", pd.Series(dtype=str)).astype(str).isin([FROM_FACILITY, TO_FACILITY])
        ].copy()

    lineage = pd.DataFrame(
        [
            {
                "line_id": LINE_ID,
                "circuit_id": CIRCUIT_ID,
                "from_facility_code": FROM_FACILITY,
                "to_facility_code": TO_FACILITY,
                "from_facility_name": facilities[facilities["facility_code"] == FROM_FACILITY]["facility_name"].iloc[0] if len(facilities[facilities["facility_code"] == FROM_FACILITY]) else "",
                "to_facility_name": facilities[facilities["facility_code"] == TO_FACILITY]["facility_name"].iloc[0] if len(facilities[facilities["facility_code"] == TO_FACILITY]) else "",
                "length_km": candidate_row.iloc[0].get("length_km", "") if len(candidate_row) else "",
                "asset_type": candidate_row.iloc[0].get("asset_type", "") if len(candidate_row) else "",
                "asset_type_source": candidate_row.iloc[0].get("asset_type_source", "") if len(candidate_row) else "",
                "number_of_original_segments": acpf_row.iloc[0].get("number_of_original_segments", "") if len(acpf_row) else "",
                "topology_confidence_score": candidate_row.iloc[0].get("topology_confidence_score", "") if len(candidate_row) else "",
                "source_line_ids": ";".join(source_ids),
                "source_segment_count": len(source_ids),
                "parallel_duplicate_count_between_same_facilities": int(len(parallel_or_duplicate)),
                "repair_related_candidate_count": int(len(repair_related)),
                "acpf_parameter_status": acpf_row.iloc[0].get("parameter_source_status", "") if len(acpf_row) else "",
                "acpf_assumption_note": acpf_row.iloc[0].get("acpf_assumption_note", "") if len(acpf_row) else "",
                "recommended_action": "MANUALLY_VALIDATE_OR_EXCLUDE_DIAGNOSTIC_ONLY",
                "reason": "S8 sensitivity shows excluding ATPL_00003 restores full-load diagnostic convergence; lineage shows mixed asset with unsplit 50/50 proxy and slack-adjacent topology.",
            }
        ]
    )

    lineage.to_csv(OUT / "atpl_00003_lineage.csv", index=False)
    candidate_row.to_csv(OUT / "atpl_00003_candidate_line_row.csv", index=False)
    acpf_row.to_csv(OUT / "atpl_00003_acpf_line_row.csv", index=False)
    estimate_row.to_csv(OUT / "atpl_00003_parameter_estimate_row.csv", index=False)
    circuit_row.to_csv(OUT / "atpl_00003_circuit_classification_row.csv", index=False)
    source_segments.to_csv(OUT / "atpl_00003_source_segment_endpoints.csv", index=False)
    endpoint_match_rows.to_csv(OUT / "atpl_00003_endpoint_matches.csv", index=False)
    facilities.to_csv(OUT / "atpl_00003_facilities.csv", index=False)
    parallel_or_duplicate.to_csv(OUT / "atpl_00003_parallel_duplicate_candidates.csv", index=False)
    neighboring_same_facility.to_csv(OUT / "atpl_00003_neighboring_facility_branches.csv", index=False)
    repair_related.to_csv(OUT / "atpl_00003_repair_related_candidates.csv", index=False)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "line_id": LINE_ID,
        "circuit_id": CIRCUIT_ID,
        "from_facility_code": FROM_FACILITY,
        "to_facility_code": TO_FACILITY,
        "source_segment_count": len(source_ids),
        "parallel_duplicate_count_between_same_facilities": int(len(parallel_or_duplicate)),
        "neighboring_facility_branch_count": int(len(neighboring_same_facility)),
        "repair_related_candidate_count": int(len(repair_related)),
        "asset_type": lineage.iloc[0]["asset_type"],
        "parameter_status": lineage.iloc[0]["acpf_parameter_status"],
        "recommended_action": lineage.iloc[0]["recommended_action"],
        "publication_allowed": False,
        "status": "DIAGNOSTIC_DONE",
    }
    (OUT / "atpl_00003_lineage_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    text = [
        "# 29 ATPL_00003 Lineage Trace",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "Scope: trace the source lineage of `ATPL_00003`, the slack-adjacent line whose diagnostic exclusion restores S8 full-load ACPF convergence. This does not prove the real line is invalid; it identifies what must be manually validated.",
        "",
        "## Lineage Summary",
        "",
        markdown_table(lineage),
        "",
        "## Candidate Line Row",
        "",
        markdown_table(candidate_row),
        "",
        "## ACPF Line Row",
        "",
        markdown_table(acpf_row),
        "",
        "## Circuit Classification Row",
        "",
        markdown_table(circuit_row),
        "",
        "## Facilities",
        "",
        markdown_table(facilities),
        "",
        "## Source Segment Endpoints",
        "",
        markdown_table(source_segments, 30),
        "",
        "## Endpoint Match Rows",
        "",
        markdown_table(endpoint_match_rows, 30),
        "",
        "## Same-Facility Parallel/Duplicate Candidates",
        "",
        markdown_table(parallel_or_duplicate, 20),
        "",
        "## Neighboring Branches Around Both Facilities",
        "",
        markdown_table(neighboring_same_facility, 40),
        "",
        "## Repair-Related Candidates",
        "",
        markdown_table(repair_related, 20),
        "",
        "## Interpretation",
        "",
        "`ATPL_00003` is an inter-facility 60 kV line between MERCEANA (`1101S5423500`) and VALE TEJO (`1101S5335200`). It is classified as `mixed` and receives a 50/50 mixed overhead/cable proxy in S5 because segment-level asset lengths are unavailable. Its topology confidence is lower than adjacent `ATPL_00004`, and it is directly adjacent to the selected slack facility. Since excluding it restores full-load diagnostic convergence, the next action is manual topology/source validation: inspect the six source segments, confirm whether the mixed asset should be split, verify the endpoint assignment to VALE TEJO/MERCEANA, and check if it duplicates or should be represented differently around the slack facility.",
    ]
    (REPORTS / "29_atpl_00003_lineage_trace.md").write_text("\n".join(text), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
