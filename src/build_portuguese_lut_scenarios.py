"""Build explicit Portuguese electrical LUT scenario tables.

This script organizes source-backed partial values, scenario assumptions,
benchmark-only placeholders, and required missing values. It does not promote the
Portuguese model to source-backed solver readiness.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
OUT = PROCESSED / "lut_scenarios"
REPORTS = ROOT / "reports"
MIXED_POLICY = PROCESSED / "mixed_corridor_policy_table.csv"


def thermal_mva(voltage_kv: float, current_a: float) -> float:
    return math.sqrt(3.0) * voltage_kv * current_a / 1000.0


def read_existing(name: str) -> pd.DataFrame:
    path = PROCESSED / name
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def line_lut_scenarios() -> pd.DataFrame:
    overhead = read_existing("step3b_overhead_line_parameter_candidates.csv")
    cable = read_existing("step3b_cable_parameter_candidates.csv")
    best_available = read_existing("step3b_best_available_parameter_table.csv")
    cable_diagnostic = read_existing("step3b_best_available_cable_diagnostic_table.csv")
    mixed_policy = pd.read_csv(MIXED_POLICY) if MIXED_POLICY.exists() else pd.DataFrame()
    rows: list[dict[str, Any]] = []

    def add(**kwargs: Any) -> None:
        base = {
            "scenario_id": "",
            "asset_type": "",
            "voltage_kv": 60,
            "parameter_set_id": "",
            "r_ohm_per_km": "",
            "x_ohm_per_km": "",
            "c_nf_per_km": "",
            "b_siemens_per_km": "",
            "rated_current_a": "",
            "thermal_limit_mva": "",
            "value_status": "",
            "source_id": "",
            "source_confidence": "",
            "publication_allowed": False,
            "solver_allowed": False,
            "notes": "",
        }
        base.update(kwargs)
        rows.append(base)

    for _, row in overhead.iterrows():
        if row.get("source_id") == "SRC_EREDES_DMAC34110":
            add(
                scenario_id="S1_SOURCE_BACKED_PARTIAL_NO_SOLVER",
                asset_type="overhead",
                parameter_set_id=f"OH_EREDES_COPPER_R_{row.get('cross_section_mm2')}",
                r_ohm_per_km=row.get("r_ohm_per_km", ""),
                value_status="SOURCE_BACKED_PARTIAL_R_ONLY",
                source_id=row.get("source_id", ""),
                source_confidence="low_branch_applicability",
                notes="E-REDES copper conductor resistance only; voltage/conductor applicability, X/B/current missing.",
            )
        elif row.get("source_id") == "SRC_PANDAPOWER_STD":
            add(
                scenario_id="S2_BENCHMARK_PLUMBING_BASE",
                asset_type="overhead",
                parameter_set_id=f"OH_PP_BENCH_{row.get('cross_section_mm2')}",
                r_ohm_per_km=row.get("r_ohm_per_km", ""),
                x_ohm_per_km=row.get("x_ohm_per_km", ""),
                c_nf_per_km=str(row.get("capacitance_or_b", "")).replace(" nF/km", ""),
                rated_current_a=row.get("rated_current_a", ""),
                thermal_limit_mva=thermal_mva(60.0, float(row.get("rated_current_a"))) if pd.notna(row.get("rated_current_a")) and str(row.get("rated_current_a")) else "",
                value_status="BENCHMARK_ONLY",
                source_id=row.get("source_id", ""),
                source_confidence="low",
                solver_allowed=True,
                notes="Pandapower 110 kV overhead benchmark adapted only for plumbing/sensitivity; not Portugal-specific.",
            )

    for _, row in cable.iterrows():
        if row.get("source_id") == "SRC_EREDES_DMAC33281":
            current = row.get("rated_current_a", "")
            add(
                scenario_id="S1_SOURCE_BACKED_PARTIAL_NO_SOLVER",
                asset_type="cable",
                parameter_set_id=f"CB_EREDES_CURRENT_{row.get('cross_section_mm2')}_{row.get('installation_type')}",
                rated_current_a=current,
                thermal_limit_mva=thermal_mva(60.0, float(current)) if pd.notna(current) and str(current) else "",
                value_status="SOURCE_BACKED_PARTIAL_CURRENT_ONLY",
                source_id=row.get("source_id", ""),
                source_confidence="high_source_medium_branch_applicability",
                notes="E-REDES 36/60 kV cable current scenario; cable R/X/C and branch section/installation remain missing.",
            )
        elif row.get("source_id") == "SRC_PANDAPOWER_STD":
            add(
                scenario_id="S2_BENCHMARK_PLUMBING_BASE",
                asset_type="cable",
                parameter_set_id=f"CB_PP_BENCH_{row.get('cross_section_mm2')}",
                r_ohm_per_km=row.get("r_ohm_per_km", ""),
                x_ohm_per_km=row.get("x_ohm_per_km", ""),
                c_nf_per_km=str(row.get("capacitance_per_km", "")).replace(" nF/km", ""),
                rated_current_a=row.get("rated_current_a", ""),
                thermal_limit_mva=thermal_mva(60.0, float(row.get("rated_current_a"))) if pd.notna(row.get("rated_current_a")) and str(row.get("rated_current_a")) else "",
                value_status="BENCHMARK_ONLY",
                source_id=row.get("source_id", ""),
                source_confidence="low",
                solver_allowed=True,
                notes="Pandapower 64/110 kV cable benchmark adapted only for plumbing/sensitivity; not Portugal-specific.",
            )

    add(
        scenario_id="S_PT_LXHIOLE_1000_VALIDATED_CROSSCHECK",
        asset_type="cable",
        parameter_set_id="CB_PT_LXHIOLE_1000_36_60_72P5_TREFOIL",
        r_ohm_per_km=0.0291,
        x_ohm_per_km=0.10,
        c_nf_per_km=280.0,
        rated_current_a="1059 air_40C / 724 buried_25C",
        thermal_limit_mva=f"{round(thermal_mva(60.0, 1059), 3)} air_40C / {round(thermal_mva(60.0, 724), 3)} buried_25C",
        value_status="SOURCE_BACKED_PARTIAL_PROJECT_CROSSCHECK",
        source_id="SRC_PT_PROJECT_PE_TOCHA_II_2019 + SRC_NEXANS_TSLF_72KV_1000_AL_CROSSCHECK",
        source_confidence="high_for_matching_lxhiole_1000_project_medium_for_general_lut",
        notes="Portuguese PE Tocha II LXHIOLE 1x1000/135 36/60(72.5) kV trefoil R/X/C/current, externally cross-checked by Nexans TSLF 72 kV 1x1000 aluminium values. Keep as project-matched cable scenario, not a full branch LUT.",
    )

    if not cable_diagnostic.empty:
        for _, row in cable_diagnostic.iterrows():
            current = row.get("rated_current_a", "")
            add(
                scenario_id="S5_BEST_AVAILABLE_MULTILINGUAL_DIAGNOSTIC",
                asset_type="cable",
                parameter_set_id=f"CB_BEST_AVAILABLE_DIAGNOSTIC_{row.get('cross_section_mm2')}",
                r_ohm_per_km=row.get("r_ohm_per_km", ""),
                x_ohm_per_km=row.get("x_ohm_per_km", ""),
                c_nf_per_km=row.get("c_nf_per_km", ""),
                rated_current_a=current,
                thermal_limit_mva=thermal_mva(60.0, float(current)) if pd.notna(current) and str(current) else "",
                value_status="BEST_AVAILABLE_CABLE_DIAGNOSTIC_MERGED",
                source_id=row.get("source_ids", ""),
                source_confidence="best_available_multilingual_diagnostic",
                solver_allowed=bool(row.get("diagnostic_allowed", False)),
                notes=f"Merged cable diagnostic bucket. Current voltage={row.get('nominal_voltage_kv_for_current', '')}; impedance voltage={row.get('nominal_voltage_kv_for_impedance', '')}; statuses={row.get('selected_evidence_statuses', '')}.",
            )

    if not best_available.empty:
        selected = best_available[best_available["selection_status"] == "BEST_AVAILABLE"].copy()
        for _, row in selected.iterrows():
            parameter = str(row.get("parameter_name", ""))
            parameter_set = str(row.get("parameter_set", ""))
            values: dict[str, Any] = {
                "scenario_id": "S5_BEST_AVAILABLE_MULTILINGUAL_DIAGNOSTIC",
                "asset_type": "cable" if str(row.get("parameter_family", "")) == "cable" else "overhead" if str(row.get("parameter_family", "")) == "overhead_line" else str(row.get("asset_type", "")),
                "parameter_set_id": f"BEST_AVAILABLE_{parameter_set or parameter}",
                "value_status": f"BEST_AVAILABLE_{row.get('evidence_status', 'UNKNOWN')}",
                "source_id": row.get("source_id", ""),
                "source_confidence": row.get("confidence", ""),
                "solver_allowed": bool(row.get("diagnostic_allowed", False)),
                "notes": f"Best-available multilingual selection. {row.get('selection_reason', '')} {row.get('notes', '')}",
            }
            numeric_value = row.get("numeric_value", "")
            if parameter == "r_ohm_per_km":
                values["r_ohm_per_km"] = numeric_value
            elif parameter == "x_ohm_per_km":
                values["x_ohm_per_km"] = numeric_value
            elif parameter == "c_nf_per_km":
                values["c_nf_per_km"] = numeric_value
            elif parameter == "rated_current_a":
                values["rated_current_a"] = numeric_value
                if pd.notna(numeric_value) and str(numeric_value) != "":
                    values["thermal_limit_mva"] = thermal_mva(60.0, float(numeric_value))
            add(**values)

    if not mixed_policy.empty:
        for _, row in mixed_policy[mixed_policy["policy_class"].astype(str) == "MIXED_WEIGHTED_ALLOWED"].iterrows():
            add(
                scenario_id="S5_BEST_AVAILABLE_MULTILINGUAL_DIAGNOSTIC",
                asset_type="mixed",
                parameter_set_id=f"MIXED_POLICY_{row.get('line_id')}",
                value_status="MIXED_WEIGHTED_ALLOWED",
                source_id=f"mixed_corridor_policy_table:{row.get('line_id')}",
                source_confidence="manual_review_weighted_diagnostic",
                solver_allowed=True,
                notes=f"Policy-backed weighted mixed corridor. overhead_share={row.get('overhead_share')}; cable_share={row.get('cable_share')}; next={row.get('preferred_next_scenario')}",
            )
        for _, row in mixed_policy[mixed_policy["policy_class"].astype(str) == "MIXED_UNRESOLVED_EXCLUDE_FROM_HIGHER_GRADE_SCENARIOS"].iterrows():
            add(
                scenario_id="S0_MISSING_ONLY_AUDIT",
                asset_type="mixed",
                parameter_set_id=f"MIXED_UNRESOLVED_{row.get('line_id')}",
                value_status="MIXED_UNRESOLVED_EXCLUDE_FROM_HIGHER_GRADE_SCENARIOS",
                source_id=f"mixed_corridor_policy_table:{row.get('line_id')}",
                source_confidence="manual_review_pending",
                notes=f"Unresolved mixed corridor excluded from higher-grade scenarios until review. next={row.get('preferred_next_scenario')}",
            )

    for asset in ["overhead", "cable", "mixed"]:
        add(
            scenario_id="S0_MISSING_ONLY_AUDIT",
            asset_type=asset,
            parameter_set_id=f"{asset.upper()}_MISSING_REQUIRED",
            value_status="MISSING_REQUIRED",
            source_confidence="red",
            notes="Portugal-specific complete R/X/B/current branch-level LUT is required before source-backed PF readiness.",
        )

    return pd.DataFrame(rows)


def transformer_lut_scenarios() -> pd.DataFrame:
    trafo = read_existing("step3b_transformer_parameter_candidates.csv")
    rows: list[dict[str, Any]] = []
    for _, row in trafo.iterrows():
        rows.append(
            {
                "scenario_id": "S1_SOURCE_BACKED_PARTIAL_NO_SOLVER",
                "parameter_set_id": f"TR_EREDES_UK_{row.get('rated_mva')}",
                "voltage_pair": "60/MT",
                "rated_mva": row.get("rated_mva", ""),
                "vk_percent": row.get("short_circuit_impedance_percent", ""),
                "vkr_percent": "",
                "x_r_ratio": "",
                "tap_min": -11,
                "tap_max": 11,
                "tap_step_percent": 1.5,
                "tap_pos": "",
                "unit_count": "",
                "value_status": "SOURCE_BACKED_PARTIAL_UK_AND_TAP_RANGE",
                "source_id": row.get("source_id", "SRC_EREDES_DMAC52140"),
                "source_confidence": "high_for_uk_tap_range_missing_rx_unit_tap_pos",
                "publication_allowed": False,
                "solver_allowed": False,
                "notes": "E-REDES uk% and tap range/step are available; R/X split, unit count, and actual tap position/control remain missing.",
            }
        )
    for xr in [10, 20, 40]:
        rows.append(
            {
                "scenario_id": "S3_SENSITIVITY_WEAK_GRID" if xr == 10 else "S2_BENCHMARK_PLUMBING_BASE" if xr == 20 else "S4_SENSITIVITY_STRONG_GRID",
                "parameter_set_id": f"TR_SCENARIO_XR_{xr}",
                "voltage_pair": "60/MT",
                "rated_mva": "matched_or_installed_power_proxy",
                "vk_percent": "from_E_REDES_when_matched",
                "vkr_percent": "vk/sqrt(1+xr^2)",
                "x_r_ratio": xr,
                "tap_min": -11,
                "tap_max": 11,
                "tap_step_percent": 1.5,
                "tap_pos": 0,
                "unit_count": 1,
                "value_status": "SCENARIO_ASSUMED",
                "source_id": "SRC_EREDES_DMAC52140_FOR_UK_ONLY",
                "source_confidence": "scenario_for_rx_unit_tap_pos",
                "publication_allowed": False,
                "solver_allowed": True,
                "notes": "Scenario transformer completion for diagnostics only; not operator-grade.",
            }
        )
    return pd.DataFrame(rows)


def scenario_definitions() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"scenario_id": "S0_MISSING_ONLY_AUDIT", "purpose": "Audit missing Portuguese LUT values.", "solver_allowed": False, "publication_allowed": False, "status": "BLOCKED_EXTERNAL_DATA"},
            {"scenario_id": "S1_SOURCE_BACKED_PARTIAL_NO_SOLVER", "purpose": "Collect partial source-backed values without filling missing solver fields.", "solver_allowed": False, "publication_allowed": False, "status": "PARTIAL"},
            {"scenario_id": "S_PT_LXHIOLE_1000_VALIDATED_CROSSCHECK", "purpose": "Use the Portuguese PE Tocha II LXHIOLE 1x1000/135 cable parameters with Nexans 72 kV aluminium cross-check as a project-matched validation scenario.", "solver_allowed": False, "publication_allowed": False, "status": "PARTIAL_PROJECT_MATCHED"},
            {"scenario_id": "S5_BEST_AVAILABLE_MULTILINGUAL_DIAGNOSTIC", "purpose": "Use the highest-ranked multilingual direct, same-spec, cross-check, or adjacent-spec candidate rows for diagnostic-only parameter fill.", "solver_allowed": True, "publication_allowed": False, "status": "DIAGNOSTIC_ONLY"},
            {"scenario_id": "S2_BENCHMARK_PLUMBING_BASE", "purpose": "Use benchmark-only line values and scenario transformer completion for plumbing diagnostics.", "solver_allowed": True, "publication_allowed": False, "status": "DIAGNOSTIC_ONLY"},
            {"scenario_id": "S3_SENSITIVITY_WEAK_GRID", "purpose": "Sensitivity scenario with weaker grid/transformer assumptions.", "solver_allowed": True, "publication_allowed": False, "status": "DIAGNOSTIC_ONLY"},
            {"scenario_id": "S4_SENSITIVITY_STRONG_GRID", "purpose": "Sensitivity scenario with stronger grid/transformer assumptions.", "solver_allowed": True, "publication_allowed": False, "status": "DIAGNOSTIC_ONLY"},
        ]
    )


def gap_status(line: pd.DataFrame, trafo: pd.DataFrame) -> pd.DataFrame:
    rows = [
        {"object_type": "line", "gap": "Portuguese overhead R/X/B/current", "status": "BLOCKED_EXTERNAL_DATA", "evidence": "Only partial R or benchmark rows available; multilingual best-available rows may support diagnostics but no complete Portuguese branch-level overhead LUT exists."},
        {"object_type": "line", "gap": "Portuguese cable R/X/C for all sections/installations", "status": "PARTIAL", "evidence": "LXHIOLE 1x1000/135 36/60(72.5) kV trefoil now has Portugal-specific project R/X/C/current cross-checked by Nexans, and multilingual best-available rows can fill some cable buckets diagnostically, but 400/630 mm2 and broader installation variants remain missing or proxy-only."},
        {"object_type": "line", "gap": "Branch-level cable/overhead assignment and circuit count", "status": "BLOCKED_EXTERNAL_DATA", "evidence": "Even with stronger parameter rows, each branch still lacks confirmed conductor/cable family, section, installation condition, and circuit count."},
        {"object_type": "line", "gap": "Mixed segment split", "status": "BLOCKED_EXTERNAL_DATA", "evidence": "Mixed branches still need segment-level split before a final LUT can be assigned consistently."},
        {"object_type": "transformer", "gap": "R/X split, unit count, actual tap position", "status": "PARTIAL", "evidence": "E-REDES uk% and tap range exist; completion remains scenario-assumed."},
        {"object_type": "opf", "gap": "Generation cost and dispatch data", "status": "NOT_READY_FOR_PUBLICATION", "evidence": "No OPF-ready generator/cost tables in current pipeline."},
    ]
    return pd.DataFrame(rows)


def markdown_table(df: pd.DataFrame, columns: list[str] | None = None, max_rows: int = 20) -> str:
    if columns is None:
        columns = list(df.columns)
    view = df[columns].head(max_rows).copy()
    if view.empty:
        return "_No rows._\n"
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for _, row in view.iterrows():
        lines.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|") for c in columns) + " |")
    return "\n".join(lines) + "\n"


def write_report(line: pd.DataFrame, trafo: pd.DataFrame, scenarios: pd.DataFrame, gaps: pd.DataFrame, summary: dict[str, Any]) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    status_counts = line["value_status"].value_counts().rename_axis("value_status").reset_index(name="line_rows")
    text = [
        "# 16 Portuguese LUT Scenarios",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "Scope: scenario organization only. This report does not create a final source-backed Portuguese electrical LUT and does not promote OPF readiness.",
        "",
        "## Scenario Definitions",
        "",
        markdown_table(scenarios, max_rows=10),
        "",
        "## Line LUT Status Counts",
        "",
        markdown_table(status_counts),
        "",
        "## Example Line LUT Rows",
        "",
        markdown_table(line, ["scenario_id", "asset_type", "parameter_set_id", "value_status", "source_id", "source_confidence", "solver_allowed"], 25),
        "",
        "## Example Transformer LUT Rows",
        "",
        markdown_table(trafo, ["scenario_id", "parameter_set_id", "value_status", "source_id", "solver_allowed"], 20),
        "",
        "## Remaining Gaps",
        "",
        markdown_table(gaps, max_rows=20),
        "",
        "## LXHIOLE 1000 Cross-Check Update",
        "",
        "The new `S_PT_LXHIOLE_1000_VALIDATED_CROSSCHECK` scenario captures a Portugal-specific LXHIOLE 1x1000/135 36/60(72.5) kV trefoil cable row from the PE Tocha II project and marks it as externally cross-validated against Nexans TSLF 72 kV 1x1000 aluminium values. This improves confidence for that specific cable/application match, but it does not create a complete Portuguese branch-level LUT because other sections, installation variants, overhead electrical constants, branch assignments, and transformer completion fields remain incomplete.",
    ]
    (REPORTS / "16_portuguese_lut_scenarios.md").write_text("\n".join(text), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    line = line_lut_scenarios()
    trafo = transformer_lut_scenarios()
    scenarios = scenario_definitions()
    gaps = gap_status(line, trafo)
    line.to_csv(OUT / "pt_line_lut_scenarios.csv", index=False)
    trafo.to_csv(OUT / "pt_transformer_lut_scenarios.csv", index=False)
    scenarios.to_csv(OUT / "pt_lut_scenario_definitions.csv", index=False)
    gaps.to_csv(OUT / "pt_lut_gap_status.csv", index=False)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "line_lut_rows": int(len(line)),
        "transformer_lut_rows": int(len(trafo)),
        "scenario_count": int(len(scenarios)),
        "line_status_counts": line["value_status"].value_counts().to_dict(),
        "status": "PARTIAL",
        "publication_allowed": False,
        "source_backed_pf_ready": False,
    }
    (OUT / "pt_lut_scenarios_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    write_report(line, trafo, scenarios, gaps, summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
