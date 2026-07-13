"""Organize newly searched Portuguese 60 kV parameter sources.

This script records exact values found in public E-REDES PDFs and separates
usable partial values from still-missing solver parameters.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "processed" / "portuguese_60kv_parameter_search"
REPORTS = ROOT / "reports"


def thermal_mva(voltage_kv: float, current_a: float) -> float:
    return math.sqrt(3.0) * voltage_kv * current_a / 1000.0


def source_rows() -> list[dict[str, Any]]:
    return [
        {
            "source_id": "SRC_EREDES_DMAC34127",
            "title": "DMA-C34-127/N Cabos nus de liga alumínio-aço para linhas aéreas em zonas de gelo",
            "url": "https://www.e-redes.pt/sites/edd/files/normative_docs/DMA-C34-127.pdf",
            "asset_type": "overhead_line_conductor",
            "voltage_relevance": "Distribution network includes 60 kV; conductor spec applies to E-REDES distribution overhead lines, especially ice zones.",
            "usable_values": "AL3/ST1A conductor construction and maximum DC resistance at 20C for DA56 and DA110.",
            "missing_values": "No line-level reactance, capacitance/susceptance, rated continuous current, or branch circuit count.",
            "confidence": "medium_for_conductor_R_low_for_branch_parameterization",
        },
        {
            "source_id": "SRC_EREDES_DMAC33281",
            "title": "DMA-C33-281/N Cabos isolados AT, 36/60 (72.5) kV",
            "url": "https://www.e-redes.pt/sites/eredes/files/2020-05/DMA%20C33%20281.pdf",
            "asset_type": "underground_cable",
            "voltage_relevance": "Directly applicable to 36/60 (72.5) kV E-REDES AT cables.",
            "usable_values": "Standard sections 400/630/1000 mm2, aluminum conductor, installation assumptions, indicative permanent current table.",
            "missing_values": "Cable R/X/capacitance are fields manufacturers must declare; no fixed numeric R/X/C table in the public spec.",
            "confidence": "high_for_current_scenarios_medium_for_branch_use",
        },
        {
            "source_id": "SRC_EREDES_DMAC52140",
            "title": "DMA-C52-140/N Transformadores trifásicos de 60 kV/MT",
            "url": "https://www.e-redes.pt/sites/eredes/files/2020-06/DMA-C52-140_0.pdf",
            "asset_type": "transformer_60kv_mt",
            "voltage_relevance": "Directly applicable to E-REDES 60 kV/MT power transformers.",
            "usable_values": "Rated powers, primary/secondary voltages, short-circuit impedance uk%, OLTC tap range/step.",
            "missing_values": "Losses and no-load current must be specified by constructor; R/X split, actual tap position/control setpoint, and unit inventory are not branch-level data.",
            "confidence": "high_for_uk_and_tap_range_medium_for_model_completion",
        },
        {
            "source_id": "SRC_EREDES_DMAC66917",
            "title": "DMA-C66-917/N Balizores para linhas aéreas AT 60 kV",
            "url": "https://www.e-redes.pt/sites/eredes/files/normative_docs/DMA-C66-917N.pdf",
            "asset_type": "overhead_line_equipment_reference",
            "voltage_relevance": "Mentions 60 kV overhead line marker/equipment compatibility with conductor names/sizes.",
            "usable_values": "Confirms 60 kV conductor references: Partridge ACSR 160 mm2, ACSR 235 mm2, Bear ACSR 325 mm2, diameters.",
            "missing_values": "No R/X/B/current parameter table; equipment document only.",
            "confidence": "medium_as_conductor_family_evidence_low_for_electrical_parameters",
        },
        {
            "source_id": "SRC_REN_RNT_CHARACTERIZATION",
            "title": "REN Caracterização da Rede Nacional de Transporte",
            "url": "https://www.ren.pt/media/4bjaxy3z/caracteriza%C3%A7%C3%A3o-da-rnt.pdf",
            "asset_type": "transmission_grid_reference",
            "voltage_relevance": "REN RNT is mainly 150/220/400 kV, not the E-REDES 60 kV network.",
            "usable_values": "Method reference for R/X/susceptance table style at transmission voltage levels.",
            "missing_values": "Not a 60 kV RND parameter source.",
            "confidence": "high_for_RNT_context_low_for_60kv_RND_values",
        },
    ]


def overhead_rows() -> list[dict[str, Any]]:
    return [
        {
            "source_id": "SRC_EREDES_DMAC34127",
            "conductor_reference": "DA56 / 47-AL3/8-ST1A",
            "applicability": "E-REDES distribution overhead line conductor, ice zones; network table includes 60 kV.",
            "section_total_mm2": 54.6,
            "aluminium_section_mm2": 46.8,
            "steel_section_mm2": 7.8,
            "diameter_mm": 9.45,
            "mass_kg_per_km": 188.6,
            "rated_breaking_load_kn": 22.37,
            "r_dc_20c_ohm_per_km": 0.7054,
            "x_ohm_per_km": "MISSING_NOT_IN_SOURCE",
            "c_or_b_per_km": "MISSING_NOT_IN_SOURCE",
            "rated_current_a": "MISSING_NOT_IN_SOURCE",
            "thermal_limit_mva_60kv": "MISSING_NOT_IN_SOURCE",
            "value_status": "SOURCE_BACKED_PARTIAL_R_ONLY",
            "source_confidence": "medium_source_low_branch_applicability",
            "notes": "Resistance is max DC resistance at 20C, not AC operating-temperature positive-sequence R. Use only as weak candidate until branch conductor identity is known.",
        },
        {
            "source_id": "SRC_EREDES_DMAC34127",
            "conductor_reference": "DA110 / 94-AL3/22-ST1A",
            "applicability": "E-REDES distribution overhead line conductor, ice zones; network table includes 60 kV.",
            "section_total_mm2": 116.2,
            "aluminium_section_mm2": 94.2,
            "steel_section_mm2": 22.0,
            "diameter_mm": 14.0,
            "mass_kg_per_km": 432.2,
            "rated_breaking_load_kn": 53.53,
            "r_dc_20c_ohm_per_km": 0.353,
            "x_ohm_per_km": "MISSING_NOT_IN_SOURCE",
            "c_or_b_per_km": "MISSING_NOT_IN_SOURCE",
            "rated_current_a": "MISSING_NOT_IN_SOURCE",
            "thermal_limit_mva_60kv": "MISSING_NOT_IN_SOURCE",
            "value_status": "SOURCE_BACKED_PARTIAL_R_ONLY",
            "source_confidence": "medium_source_low_branch_applicability",
            "notes": "Resistance is max DC resistance at 20C, not AC operating-temperature positive-sequence R. Use only as weak candidate until branch conductor identity is known.",
        },
        {
            "source_id": "SRC_EREDES_DMAC66917",
            "conductor_reference": "60BAL160 Partridge ACSR 160 mm2",
            "applicability": "60 kV overhead line marker/equipment compatibility evidence.",
            "section_total_mm2": 160,
            "aluminium_section_mm2": "",
            "steel_section_mm2": "",
            "diameter_mm": 16.32,
            "mass_kg_per_km": "",
            "rated_breaking_load_kn": ">10",
            "r_dc_20c_ohm_per_km": "MISSING_NOT_IN_SOURCE",
            "x_ohm_per_km": "MISSING_NOT_IN_SOURCE",
            "c_or_b_per_km": "MISSING_NOT_IN_SOURCE",
            "rated_current_a": "MISSING_NOT_IN_SOURCE",
            "thermal_limit_mva_60kv": "MISSING_NOT_IN_SOURCE",
            "value_status": "CONDUCTOR_FAMILY_EVIDENCE_ONLY",
            "source_confidence": "medium_for_family_low_for_parameters",
            "notes": "Useful evidence that Partridge/ACSR 160 appears in E-REDES 60 kV equipment context; no electrical constants in this source.",
        },
        {
            "source_id": "SRC_EREDES_DMAC66917",
            "conductor_reference": "60BAL235 ACSR 235 mm2",
            "applicability": "60 kV overhead line marker/equipment compatibility evidence.",
            "section_total_mm2": 235,
            "aluminium_section_mm2": "",
            "steel_section_mm2": "",
            "diameter_mm": 19.89,
            "mass_kg_per_km": "",
            "rated_breaking_load_kn": ">10",
            "r_dc_20c_ohm_per_km": "MISSING_NOT_IN_SOURCE",
            "x_ohm_per_km": "MISSING_NOT_IN_SOURCE",
            "c_or_b_per_km": "MISSING_NOT_IN_SOURCE",
            "rated_current_a": "MISSING_NOT_IN_SOURCE",
            "thermal_limit_mva_60kv": "MISSING_NOT_IN_SOURCE",
            "value_status": "CONDUCTOR_FAMILY_EVIDENCE_ONLY",
            "source_confidence": "medium_for_family_low_for_parameters",
            "notes": "No electrical constants in this source.",
        },
        {
            "source_id": "SRC_EREDES_DMAC66917",
            "conductor_reference": "60BAL325 Bear ACSR 325 mm2",
            "applicability": "60 kV overhead line marker/equipment compatibility evidence.",
            "section_total_mm2": 325,
            "aluminium_section_mm2": "",
            "steel_section_mm2": "",
            "diameter_mm": 23.45,
            "mass_kg_per_km": "",
            "rated_breaking_load_kn": ">10",
            "r_dc_20c_ohm_per_km": "MISSING_NOT_IN_SOURCE",
            "x_ohm_per_km": "MISSING_NOT_IN_SOURCE",
            "c_or_b_per_km": "MISSING_NOT_IN_SOURCE",
            "rated_current_a": "MISSING_NOT_IN_SOURCE",
            "thermal_limit_mva_60kv": "MISSING_NOT_IN_SOURCE",
            "value_status": "CONDUCTOR_FAMILY_EVIDENCE_ONLY",
            "source_confidence": "medium_for_family_low_for_parameters",
            "notes": "No electrical constants in this source.",
        },
    ]


def cable_rows() -> list[dict[str, Any]]:
    ampacity = {
        400: [474, 582, 400, 496, 630, 689, 393, 429],
        630: [599, 740, 505, 629, 831, 909, 491, 535],
        1000: [725, 899, 613, 766, 1048, 1147, 585, 639],
    }
    labels = [
        "buried_soil_1_circuit_hot",
        "buried_soil_1_circuit_cold",
        "buried_soil_2_circuits_hot",
        "buried_soil_2_circuits_cold",
        "free_air_hot",
        "free_air_cold",
        "ducts_hot",
        "ducts_cold",
    ]
    rows: list[dict[str, Any]] = []
    for section, currents in ampacity.items():
        for label, current in zip(labels, currents):
            rows.append(
                {
                    "source_id": "SRC_EREDES_DMAC33281",
                    "cable_type": "LXHIOLE (cbe) single-core 36/60 (72.5) kV",
                    "conductor_material": "aluminium compacted class 2",
                    "section_mm2": section,
                    "installation_condition": label,
                    "rated_current_a": current,
                    "thermal_limit_mva_60kv": round(thermal_mva(60.0, current), 3),
                    "r_ohm_per_km": "MISSING_MANUFACTURER_FICHA_REQUIRED",
                    "x_ohm_per_km": "MISSING_MANUFACTURER_FICHA_REQUIRED",
                    "capacitance_uf_per_km": "MISSING_MANUFACTURER_FICHA_REQUIRED",
                    "value_status": "SOURCE_BACKED_CURRENT_ONLY",
                    "source_confidence": "high_for_table_medium_for_branch_selection",
                    "notes": "DMA-C33-281 Annex B gives indicative permanent current only. Annex E requires manufacturers to provide capacity/capacitance and reactance fields.",
                }
            )
    return rows


def transformer_rows() -> list[dict[str, Any]]:
    uk = [(3.15, 6.25), (6.3, 7.15), (10.0, 8.35), (12.5, 8.35), (20.0, 10.0), (31.5, 12.5), (40.0, 15.0)]
    rows = []
    for mva, uk_percent in uk:
        rows.append(
            {
                "source_id": "SRC_EREDES_DMAC52140",
                "voltage_pair": "60 kV / MT",
                "rated_mva_reference": mva,
                "vk_percent": uk_percent,
                "vkr_percent": "MISSING_LOSS_OR_RX_SPLIT_REQUIRED",
                "x_r_ratio": "MISSING_LOSS_OR_RX_SPLIT_REQUIRED",
                "primary_voltage_kv": 60.0,
                "secondary_voltage_options_kv": "10.5;15.75;31.5;31.5+10.5;31.5+15.75;31.5 or 15.75",
                "onan_mva_options": "7;15;25;30",
                "onaf_mva_options": "10;20;31.5;40",
                "tap_positions": 23,
                "tap_range": "Un ± 11 x 1.5%",
                "tap_step_percent": 1.5,
                "tap_side": "primary winding",
                "losses": "constructor_must_specify_no_load_load_losses_and_no_load_current",
                "value_status": "SOURCE_BACKED_UK_AND_TAP_RANGE_ONLY",
                "source_confidence": "high_for_uk_tap_range_missing_rx_losses_unit_inventory",
                "notes": "Use uk% as impedance magnitude. R/X split requires load losses or scenario assumption; actual tap position/control and unit inventory remain missing.",
            }
        )
    return rows


def gap_rows() -> list[dict[str, Any]]:
    return [
        {"asset": "overhead_line", "gap": "Positive-sequence X and B/C for 60 kV overhead branches", "status": "BLOCKED_EXTERNAL_DATA", "recommended_action": "Find E-REDES/REN standard line design tables, conductor geometry, or citable engineering literature for the actual conductor families."},
        {"asset": "overhead_line", "gap": "Continuous rated current for overhead conductors", "status": "BLOCKED_EXTERNAL_DATA", "recommended_action": "Find current-rating/ampacity tables for DA56, DA110, Partridge/ACSR160, ACSR235, Bear/ACSR325 under Portuguese conditions."},
        {"asset": "overhead_line", "gap": "Branch-level conductor family and circuit count", "status": "BLOCKED_EXTERNAL_DATA", "recommended_action": "Request branch conductor/circuit metadata or aggregate circuit-km and conductor-family distributions from E-REDES."},
        {"asset": "underground_cable", "gap": "Cable R/X/capacitance by 36/60 kV cable type", "status": "BLOCKED_MANUFACTURER_FICHA", "recommended_action": "Obtain manufacturer fichas for LXHIOLE 400/630/1000 mm2 approved cables; Annex E explicitly asks manufacturers for capacitance and reactance."},
        {"asset": "underground_cable", "gap": "Branch-level cable section and installation condition", "status": "BLOCKED_EXTERNAL_DATA", "recommended_action": "Map each cable branch to 400/630/1000 mm2 and installation condition, or keep ampacity as scenario bands."},
        {"asset": "transformer", "gap": "vkr/R-X split and losses", "status": "BLOCKED_CONSTRUCTOR_OR_OPERATOR_DATA", "recommended_action": "Use constructor load-loss/no-load-loss data or an explicitly labelled X/R scenario; do not claim source-backed R/X."},
        {"asset": "transformer", "gap": "Actual unit count and tap position/control", "status": "BLOCKED_OPERATOR_DATA", "recommended_action": "Request substation transformer unit inventory and OLTC control/setpoints from E-REDES or use scenario assumptions only."},
    ]


def markdown_table(df: pd.DataFrame, max_rows: int = 40) -> str:
    if df.empty:
        return "_No rows._\n"
    view = df.head(max_rows)
    cols = list(view.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in view.iterrows():
        lines.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|").replace("\n", " ") for c in cols) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    sources = pd.DataFrame(source_rows())
    overhead = pd.DataFrame(overhead_rows())
    cable = pd.DataFrame(cable_rows())
    transformer = pd.DataFrame(transformer_rows())
    gaps = pd.DataFrame(gap_rows())
    sources.to_csv(OUT / "pt_60kv_source_inventory.csv", index=False)
    overhead.to_csv(OUT / "pt_60kv_overhead_parameter_candidates.csv", index=False)
    cable.to_csv(OUT / "pt_60kv_cable_parameter_candidates.csv", index=False)
    transformer.to_csv(OUT / "pt_60kv_transformer_parameter_candidates.csv", index=False)
    gaps.to_csv(OUT / "pt_60kv_remaining_parameter_gaps.csv", index=False)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_count": int(len(sources)),
        "overhead_candidate_rows": int(len(overhead)),
        "cable_candidate_rows": int(len(cable)),
        "transformer_candidate_rows": int(len(transformer)),
        "source_backed_complete_line_lut_ready": False,
        "source_backed_transformer_uk_ready": True,
        "source_backed_cable_current_ready": True,
        "status": "PARTIAL_BLOCKED_EXTERNAL_DATA",
    }
    (OUT / "pt_60kv_parameter_search_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    text = [
        "# 20 Portuguese 60 kV Parameter Source Search",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "Scope: organize publicly found Portuguese/E-REDES parameter evidence. This report does not create a complete source-backed PF/OPF LUT because key line and cable impedance fields remain missing.",
        "",
        "## Source Inventory",
        "",
        markdown_table(sources),
        "",
        "## Overhead Line Candidates",
        "",
        markdown_table(overhead),
        "",
        "## Cable Current Candidates",
        "",
        markdown_table(cable, 30),
        "",
        "## Transformer Candidates",
        "",
        markdown_table(transformer),
        "",
        "## Remaining Gaps",
        "",
        markdown_table(gaps),
        "",
        "## Conclusion",
        "",
        "New search improves the Portuguese evidence base: E-REDES provides source-backed 36/60 kV cable ampacity bands, 60 kV/MT transformer uk% and OLTC tap range, and partial overhead conductor resistance/family evidence. It still does not provide a complete source-backed 60 kV line R/X/B/current LUT or transformer R/X split. The next ACPF/OPF stage therefore remains diagnostic unless external E-REDES/REN/manufacturer data are obtained.",
    ]
    (REPORTS / "20_portuguese_60kv_parameter_source_search.md").write_text("\n".join(text), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
