"""Trace ATPL_00244 lineage and current-best DC OPF bottleneck context."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
OUT = PROCESSED / "dcopf_s27_primary_transfer_path"
REPORTS = ROOT / "reports"
LINE_ID = "ATPL_00244"
FROM_FACILITY = "1107P5728100"
TO_FACILITY = "1109S5902500"


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
    s27_lines = read_csv(OUT / "s27_top_lines.csv")
    s27_gens = read_csv(OUT / "s27_top_generators.csv")

    candidate_row = candidate[candidate["line_id"] == LINE_ID].copy() if len(candidate) else pd.DataFrame()
    acpf_row = acpf[acpf["line_id"] == LINE_ID].copy() if len(acpf) else pd.DataFrame()
    interfacility_row = interfacility[interfacility["branch_id"] == LINE_ID].copy() if len(interfacility) else pd.DataFrame()

    circuit_id = str(interfacility_row.iloc[0].get("circuit_id", "")) if len(interfacility_row) else ""
    source_ids = parse_ids(interfacility_row.iloc[0].get("source_line_ids")) if len(interfacility_row) else []
    classification_row = classification[classification["circuit_id"].astype(str) == circuit_id].copy() if circuit_id and len(classification) else pd.DataFrame()
    source_segments = endpoints[endpoints["source_line_id"].astype(str).isin(source_ids)].copy() if source_ids and len(endpoints) else pd.DataFrame()
    endpoint_match_rows = endpoint_matches[endpoint_matches["line_id"].astype(str).str.split("_").str[-1].isin(source_ids)].copy() if source_ids and len(endpoint_matches) else pd.DataFrame()
    facility_rows = facilities[facilities["facility_code"].astype(str).isin([FROM_FACILITY, TO_FACILITY])].copy() if len(facilities) else pd.DataFrame()

    opf_context = s27_lines[s27_lines["name"].astype(str) == LINE_ID].copy() if len(s27_lines) else pd.DataFrame()
    top_dispatch = s27_gens.groupby("name", as_index=False)["p_mw_res"].max().sort_values("p_mw_res", ascending=False).head(10) if len(s27_gens) else pd.DataFrame()

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
                "opf_binding_observed": bool(len(opf_context)),
                "recommended_action": "MANUAL_REVIEW_AND_S28_TARGETED_REMEDIATION",
                "reason": "ATPL_00244 becomes the next dominant bottleneck after S27 primary transfer path strengthening, indicating the OPF is progressing from one constrained corridor to the next.",
            }
        ]
    )

    lineage.to_csv(OUT / "atpl_00244_lineage.csv", index=False)
    candidate_row.to_csv(OUT / "atpl_00244_candidate_line_row.csv", index=False)
    acpf_row.to_csv(OUT / "atpl_00244_acpf_line_row.csv", index=False)
    interfacility_row.to_csv(OUT / "atpl_00244_interfacility_row.csv", index=False)
    classification_row.to_csv(OUT / "atpl_00244_classification_row.csv", index=False)
    source_segments.to_csv(OUT / "atpl_00244_source_segment_endpoints.csv", index=False)
    endpoint_match_rows.to_csv(OUT / "atpl_00244_endpoint_matches.csv", index=False)
    facility_rows.to_csv(OUT / "atpl_00244_facilities.csv", index=False)
    opf_context.to_csv(OUT / "atpl_00244_opf_context.csv", index=False)
    top_dispatch.to_csv(OUT / "atpl_00244_top_dispatch_context.csv", index=False)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "line_id": LINE_ID,
        "circuit_id": circuit_id,
        "source_segment_count": len(source_ids),
        "asset_type": lineage.iloc[0]["asset_type"],
        "parameter_status": lineage.iloc[0]["parameter_status"],
        "opf_binding_observed": bool(len(opf_context)),
        "recommended_action": lineage.iloc[0]["recommended_action"],
        "publication_allowed": False,
        "status": "DIAGNOSTIC_DONE",
    }
    (OUT / "atpl_00244_lineage_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    text = [
        "# 64 ATPL_00244 OPF Bottleneck Trace",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "Scope: trace `ATPL_00244`, the next dominant bottleneck line after S27 transfer-path remediation, and connect it back to topology and parameter provenance.",
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
        "## OPF Binding Context",
        "",
        markdown_table(opf_context, 40),
        "",
        "## Top Dispatch Generators",
        "",
        markdown_table(top_dispatch, 20),
        "",
        "## Interpretation",
        "",
        "`ATPL_00244` is now the next bottleneck in the progressively strengthened internal DC OPF path. This suggests the network is no longer dominated by one catastrophic mixed-bridge error, but is revealing a more realistic sequence of constrained corridors. The next step should be a focused remediation scenario (`S28`) for this line or its surrounding path segment.",
    ]
    (REPORTS / "64_atpl_00244_opf_bottleneck_trace.md").write_text("\n".join(text), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
