"""Trace ATPL_00075 lineage and bottleneck context inside the S16 backbone core."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
OUT = PROCESSED / "acpf_s16_backbone_core_depth6"
REPORTS = ROOT / "reports"
LINE_ID = "ATPL_00075"
FROM_FACILITY = "1106P5397000"
TO_FACILITY = "1116S5340000"


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def markdown_table(df: pd.DataFrame, max_rows: int = 60) -> str:
    if df.empty:
        return "_No rows._\n"
    view = df.head(max_rows)
    cols = list(view.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in view.iterrows():
        lines.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|").replace("\n", " ") for c in cols) + " |")
    return "\n".join(lines) + "\n"


def parse_ids(value: Any) -> list[str]:
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
    facilities = read_csv(PROCESSED / "pandapower_schema" / "pt_bus_table_candidate.csv")
    backbone_lines = read_csv(OUT / "s16_backbone_lines.csv")
    backbone_buses = read_csv(OUT / "s16_backbone_buses.csv")
    backbone_loads = read_csv(OUT / "s16_backbone_loads.csv")

    candidate_row = candidate[candidate["line_id"] == LINE_ID].copy() if len(candidate) else pd.DataFrame()
    acpf_row = acpf[acpf["line_id"] == LINE_ID].copy() if len(acpf) else pd.DataFrame()
    interfacility_row = interfacility[interfacility["branch_id"] == LINE_ID].copy() if len(interfacility) else pd.DataFrame()

    source_ids: list[str] = []
    circuit_id = ""
    if len(interfacility_row):
        circuit_id = str(interfacility_row.iloc[0].get("circuit_id", ""))
        source_ids = parse_ids(interfacility_row.iloc[0].get("source_line_ids"))

    classification_row = classification[classification["circuit_id"].astype(str) == circuit_id].copy() if circuit_id and len(classification) else pd.DataFrame()
    source_segments = endpoints[endpoints["source_line_id"].astype(str).isin(source_ids)].copy() if source_ids and len(endpoints) else pd.DataFrame()
    endpoint_match_rows = endpoint_matches[endpoint_matches["line_id"].astype(str).str.split("_").str[-1].isin(source_ids)].copy() if source_ids and len(endpoint_matches) else pd.DataFrame()

    facility_rows = facilities[facilities["facility_code"].astype(str).isin([FROM_FACILITY, TO_FACILITY])].copy() if len(facilities) else pd.DataFrame()
    neighboring = pd.DataFrame()
    if len(interfacility):
        f1 = interfacility["from_facility_code"].astype(str)
        f2 = interfacility["to_facility_code"].astype(str)
        neighboring = interfacility[(f1.isin([FROM_FACILITY, TO_FACILITY])) | (f2.isin([FROM_FACILITY, TO_FACILITY]))].copy()

    backbone_context_lines = pd.DataFrame()
    if len(backbone_lines):
        backbone_context_lines = backbone_lines[backbone_lines["name"].astype(str).isin([LINE_ID]) | backbone_lines["from_bus"].isin(candidate_row["from_bus"].tolist() if len(candidate_row) else []) | backbone_lines["to_bus"].isin(candidate_row["to_bus"].tolist() if len(candidate_row) else [])].copy()

    backbone_context_buses = pd.DataFrame()
    if len(backbone_buses) and len(candidate_row):
        bus_names = [candidate_row.iloc[0]["from_bus"], candidate_row.iloc[0]["to_bus"]]
        backbone_context_buses = backbone_buses[backbone_buses["name"].astype(str).isin(bus_names)].copy()

    load_rows = pd.DataFrame()
    if len(backbone_loads):
        related_buses = set(backbone_context_buses["bus_index"].tolist()) if len(backbone_context_buses) else set()
        load_rows = backbone_loads[backbone_loads["bus"].isin(list(related_buses))].copy()

    lineage = pd.DataFrame(
        [
            {
                "line_id": LINE_ID,
                "circuit_id": circuit_id,
                "from_facility_code": FROM_FACILITY,
                "to_facility_code": TO_FACILITY,
                "from_facility_name": facility_rows[facility_rows["facility_code"] == FROM_FACILITY]["facility_name"].iloc[0] if len(facility_rows[facility_rows["facility_code"] == FROM_FACILITY]) else "",
                "to_facility_name": facility_rows[facility_rows["facility_code"] == TO_FACILITY]["facility_name"].iloc[0] if len(facility_rows[facility_rows["facility_code"] == TO_FACILITY]) else "",
                "length_km": candidate_row.iloc[0].get("length_km", "") if len(candidate_row) else "",
                "asset_type": candidate_row.iloc[0].get("asset_type", "") if len(candidate_row) else "",
                "number_of_original_segments": candidate_row.iloc[0].get("number_of_original_segments", "") if len(candidate_row) else "",
                "topology_confidence_score": candidate_row.iloc[0].get("topology_confidence_score", "") if len(candidate_row) else "",
                "source_segment_count": len(source_ids),
                "parameter_status": acpf_row.iloc[0].get("parameter_source_status", "") if len(acpf_row) else "",
                "acpf_assumption_note": acpf_row.iloc[0].get("acpf_assumption_note", "") if len(acpf_row) else "",
                "backbone_included": bool(len(backbone_context_lines)),
                "neighboring_branch_count": int(len(neighboring)),
                "recommended_action": "TRACE_AND_REPARAMETERIZE_OR_REDUCE_BACKBONE_DEPTH",
                "reason": "ATPL_00075 is the dominant bottleneck across S12-S15 and remains the worst line in the depth-6 backbone core.",
            }
        ]
    )

    lineage.to_csv(OUT / "atpl_00075_lineage.csv", index=False)
    candidate_row.to_csv(OUT / "atpl_00075_candidate_line_row.csv", index=False)
    acpf_row.to_csv(OUT / "atpl_00075_acpf_line_row.csv", index=False)
    interfacility_row.to_csv(OUT / "atpl_00075_interfacility_row.csv", index=False)
    classification_row.to_csv(OUT / "atpl_00075_classification_row.csv", index=False)
    source_segments.to_csv(OUT / "atpl_00075_source_segment_endpoints.csv", index=False)
    endpoint_match_rows.to_csv(OUT / "atpl_00075_endpoint_matches.csv", index=False)
    facility_rows.to_csv(OUT / "atpl_00075_facilities.csv", index=False)
    neighboring.to_csv(OUT / "atpl_00075_neighboring_facility_branches.csv", index=False)
    backbone_context_lines.to_csv(OUT / "atpl_00075_backbone_context_lines.csv", index=False)
    backbone_context_buses.to_csv(OUT / "atpl_00075_backbone_context_buses.csv", index=False)
    load_rows.to_csv(OUT / "atpl_00075_backbone_local_loads.csv", index=False)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "line_id": LINE_ID,
        "circuit_id": circuit_id,
        "from_facility_code": FROM_FACILITY,
        "to_facility_code": TO_FACILITY,
        "source_segment_count": len(source_ids),
        "neighboring_branch_count": int(len(neighboring)),
        "backbone_included": bool(len(backbone_context_lines)),
        "asset_type": lineage.iloc[0]["asset_type"],
        "parameter_status": lineage.iloc[0]["parameter_status"],
        "recommended_action": lineage.iloc[0]["recommended_action"],
        "publication_allowed": False,
        "status": "DIAGNOSTIC_DONE",
    }
    (OUT / "atpl_00075_lineage_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    text = [
        "# 40 ATPL_00075 Bottleneck Trace",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "Scope: trace `ATPL_00075`, the dominant bottleneck line in S12-S15, and place it in the depth-6 backbone diagnostic core context.",
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
        "## Interfacility Row",
        "",
        markdown_table(interfacility_row),
        "",
        "## Classification Row",
        "",
        markdown_table(classification_row),
        "",
        "## Facilities",
        "",
        markdown_table(facility_rows),
        "",
        "## Source Segment Endpoints",
        "",
        markdown_table(source_segments, 40),
        "",
        "## Endpoint Match Rows",
        "",
        markdown_table(endpoint_match_rows, 40),
        "",
        "## Neighboring Facility Branches",
        "",
        markdown_table(neighboring, 40),
        "",
        "## Backbone Context Lines",
        "",
        markdown_table(backbone_context_lines, 40),
        "",
        "## Backbone Context Buses",
        "",
        markdown_table(backbone_context_buses, 40),
        "",
        "## Backbone Local Loads",
        "",
        markdown_table(load_rows, 40),
        "",
        "## Interpretation",
        "",
        "`ATPL_00075` is a short mixed branch between CARRICHE (`1106P5397000`) and ARROJA (`1116S5340000`) with low topology confidence and the same unsplit 50/50 mixed proxy pattern previously seen on `ATPL_00003`. It sits directly in the depth-6 backbone and remains the worst line even after load reallocation and corridor strengthening sensitivities. The next engineering action should focus on validating whether this corridor is truly mixed, whether it should be split or reclassified, and whether the backbone should temporarily exclude or re-route beyond this edge for a more stable diagnostic core.",
    ]
    (REPORTS / "40_atpl_00075_bottleneck_trace.md").write_text("\n".join(text), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
