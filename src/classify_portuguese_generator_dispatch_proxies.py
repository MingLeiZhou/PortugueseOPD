"""Classify assigned Portuguese generator candidates into dispatch proxy classes."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
ASSIGN = ROOT / "data" / "processed" / "generator_assignment"
OUT = ROOT / "data" / "processed" / "generator_dispatch_proxies"
REPORTS = ROOT / "reports"


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def classify(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, row in df.iterrows():
        if row.get("assignment_status") != "ASSIGNED_TO_BACKBONE_BUS":
            dispatch_class = "reject_for_opf"
            dispatch_reason = "not_assigned_to_backbone"
            pmax = ""
        else:
            facility_type = str(row.get("facility_type", ""))
            capacity_field = str(row.get("capacity_field", ""))
            cap = pd.to_numeric(pd.Series([row.get("capacity_mva_or_mw")]), errors="coerce").iloc[0]
            name = str(row.get("installation_name", "")).upper()
            bus_name = str(row.get("assigned_bus_name", ""))
            if facility_type == "PC_AT":
                dispatch_class = "import_interface_proxy"
                dispatch_reason = "pc_at_assumed_interface_or_injection_point"
                pmax = float(cap) if pd.notna(cap) else ""
            elif capacity_field == "potencia_de_ligacao_ligado_mva_rari":
                dispatch_class = "dispatchable_proxy"
                dispatch_reason = "linked_connection_capacity_assumed_dispatchable_proxy"
                pmax = float(cap) if pd.notna(cap) else ""
            elif capacity_field == "potencia_instalada":
                dispatch_class = "capacity_context_only"
                dispatch_reason = "substation_installed_power_not_direct_generation_unit"
                pmax = ""
            else:
                dispatch_class = "capacity_context_only"
                dispatch_reason = "unclassified_capacity_context"
                pmax = ""

            # Conservative blacklist for obviously load-serving urban substation rows in the backbone.
            if any(token in name for token in ["AEROPORTO", "TELHEIRAS", "MARVILA", "ENTRECAMPOS", "LUZ", "SANTA MARTA", "VALE ESCURO", "COLOMBO"]):
                dispatch_class = "capacity_context_only"
                dispatch_reason = "urban_substation_name_more_likely_load_supply_context"
                pmax = ""

            # If the matched bus is a 10 kV bus, keep as context only for first OPF pass.
            if str(bus_name).endswith("_10"):
                if dispatch_class == "dispatchable_proxy":
                    dispatch_class = "capacity_context_only"
                    dispatch_reason = "lv_side_backbone_match_hold_out_from_first_dcopf_pass"
                    pmax = ""

        rows.append(
            {
                **row.to_dict(),
                "dispatch_proxy_class": dispatch_class,
                "dispatch_reason": dispatch_reason,
                "pmax_mw_proxy": pmax,
                "pmin_mw_proxy": 0.0 if pmax != "" else "",
                "cost_status": "MISSING_COST_SCENARIO",
                "publication_allowed": False,
            }
        )
    return pd.DataFrame(rows)


def markdown_table(df: pd.DataFrame, max_rows: int = 80) -> str:
    if df.empty:
        return "_No rows._\n"
    view = df.head(max_rows)
    cols = list(view.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in view.iterrows():
        lines.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|") for c in cols) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    assigned = read_csv(ASSIGN / "pt_generator_bus_assignment.csv")
    classified = classify(assigned)
    classified.to_csv(OUT / "pt_generator_dispatch_proxy_table.csv", index=False)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "row_count": int(len(classified)),
        "class_counts": classified["dispatch_proxy_class"].value_counts().to_dict() if len(classified) else {},
        "dispatchable_proxy_count": int((classified["dispatch_proxy_class"] == "dispatchable_proxy").sum()) if len(classified) else 0,
        "import_interface_proxy_count": int((classified["dispatch_proxy_class"] == "import_interface_proxy").sum()) if len(classified) else 0,
        "capacity_context_only_count": int((classified["dispatch_proxy_class"] == "capacity_context_only").sum()) if len(classified) else 0,
        "reject_for_opf_count": int((classified["dispatch_proxy_class"] == "reject_for_opf").sum()) if len(classified) else 0,
        "publication_allowed": False,
        "status": "SEMANTIC_CLASSIFICATION_FIRST_PASS",
    }
    (OUT / "pt_generator_dispatch_proxy_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    dispatchable = classified[classified["dispatch_proxy_class"].isin(["dispatchable_proxy", "import_interface_proxy"])].copy() if len(classified) else pd.DataFrame()
    text = [
        "# 48 Portuguese Generator Dispatch Proxy Classification",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "Scope: first semantic classification pass for assigned generator candidates. This is still diagnostic-only and not yet an OPF-ready generator fleet.",
        "",
        "## Summary",
        "",
        markdown_table(pd.DataFrame([summary]).drop(columns=["generated_at"])),
        "",
        "## Dispatch / Interface Proxy Rows",
        "",
        markdown_table(dispatchable, 120),
        "",
        "## Interpretation",
        "",
        "This first pass is deliberately conservative. Many assigned rows are still treated as capacity context only, especially 10 kV-side matches and clearly urban load-serving substations. The next step is to build cost assumptions only for dispatchable_proxy and import_interface_proxy rows and then attempt a first diagnostic DC OPF on the backbone core.",
    ]
    (REPORTS / "48_portuguese_generator_dispatch_proxy_classification.md").write_text("\n".join(text), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
