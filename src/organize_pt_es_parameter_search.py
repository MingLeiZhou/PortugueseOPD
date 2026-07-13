"""Organize Portuguese/Spanish-language supplemental parameter search results."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "processed" / "pt_es_parameter_search"
REPORTS = ROOT / "reports"


def thermal_mva(voltage_kv: float, current_a: float) -> float:
    return math.sqrt(3.0) * voltage_kv * current_a / 1000.0


def rows() -> list[dict[str, Any]]:
    return [
        {
            "asset_type": "overhead_line",
            "source_id": "SRC_PT_PROJECT_PE_TOCHA_II_2019",
            "language": "pt",
            "source_title": "Memória Descritiva Linha 60 kV PE Tocha II – Tocha",
            "url": "https://siaia.apambiente.pt/AIADOC/AIA3274/projeto%20linha%20eletrica%20pe%20tocha%20ii2019729153214.pdf",
            "parameter_set": "ACSR/ALACO 325 mm2, 60/63 kV project line",
            "voltage_kv": 63,
            "r_ohm_per_km": 0.1093,
            "x_ohm_per_km": round(2.9398 / 7.9701, 6),
            "c_or_b": "MISSING_NOT_IN_PROJECT_EXCERPT",
            "rated_current_a": "derived_project_current_274.93_for_30MVA_not_ampacity",
            "thermal_limit_mva": "MISSING_CONTINUOUS_RATING",
            "evidence_type": "project_specific_portuguese_calculation",
            "applicability": "Useful as Portugal-specific 60 kV ACSR 325 project parameter evidence; not a universal E-REDES LUT without conductor assignment and geometry validation.",
            "confidence": "medium_high_for_this_project_medium_as_generic_proxy",
            "status": "PORTUGAL_SPECIFIC_PROXY_PARTIAL",
            "notes": "PDF gives R20=0.1093 ohm/km; total aerial X=2.9398 ohm over 7.9701 km, implying ~0.36885 ohm/km for the project geometry.",
        },
        {
            "asset_type": "underground_cable",
            "source_id": "SRC_PT_PROJECT_PE_TOCHA_II_2019",
            "language": "pt",
            "source_title": "Memória Descritiva Linha 60 kV PE Tocha II – Tocha",
            "url": "https://siaia.apambiente.pt/AIADOC/AIA3274/projeto%20linha%20eletrica%20pe%20tocha%20ii2019729153214.pdf",
            "parameter_set": "LXHIOLE 1x1000/135 36/60(72.5) kV in trefoil",
            "voltage_kv": 60,
            "r_ohm_per_km": 0.0291,
            "x_ohm_per_km": 0.10,
            "c_or_b": "0.28 uF/km per phase",
            "rated_current_a": "1059 air 40C / 724 buried 25C",
            "thermal_limit_mva": f"{round(thermal_mva(60, 1059), 3)} air / {round(thermal_mva(60, 724), 3)} buried",
            "evidence_type": "project_specific_portuguese_cable_parameter",
            "applicability": "Strong candidate for Portuguese LXHIOLE 1000/135 cable scenario; branch-level use requires matching section and installation condition.",
            "confidence": "high_for_LXHIOLE_1000_project_medium_for_general_LUT",
            "status": "PORTUGAL_SPECIFIC_CABLE_PARAMETER_CANDIDATE",
            "notes": "Complements E-REDES DMA-C33-281 by filling R/X/C for one specific 1000/135 cable project.",
        },
        {
            "asset_type": "underground_cable",
            "source_id": "SRC_ES_PRYSMIAN_VOLTALENE_RHZ1",
            "language": "es",
            "source_title": "Prysmian/VOLTALENE H 36/66 kV AL RHZ1 catalogue data via Spanish technical guide mirrors",
            "url": "https://studylib.es/doc/5589455/voltalene-h-26-45-kv--36-66-kv",
            "parameter_set": "RHZ1 36/66 kV 1x630/25 Al",
            "voltage_kv": 66,
            "r_ohm_per_km": 0.0469,
            "x_ohm_per_km": 0.105,
            "c_or_b": "0.308 uF/km",
            "rated_current_a": "659 buried / 788 air",
            "thermal_limit_mva": f"{round(thermal_mva(60, 659), 3)} buried_at_60kV / {round(thermal_mva(60, 788), 3)} air_at_60kV",
            "evidence_type": "spanish_manufacturer_proxy",
            "applicability": "Not Portugal-specific and not LXHIOLE; useful only as sensitivity/proxy if no Portuguese manufacturer ficha exists.",
            "confidence": "medium_for_spanish_RHZ1_low_for_portuguese_LXHIOLE",
            "status": "SPANISH_PROXY_SENSITIVITY_ONLY",
            "notes": "Do not mark source-backed for Portuguese E-REDES model.",
        },
        {
            "asset_type": "underground_cable",
            "source_id": "SRC_ES_PROJECT_TYPE_LAST_132KV",
            "language": "es",
            "source_title": "Proyecto Tipo de Líneas de Alta Tensión Subterráneas (>36 kV)",
            "url": "https://studylib.net/doc/26254979/proyecto-tipo-last-132-kv",
            "parameter_set": "RHZ1 36/66 kV 1x630 Al project-type values",
            "voltage_kv": 66,
            "r_ohm_per_km": "MISSING_IN_SEARCH_SNIPPET",
            "x_ohm_per_km": 0.19757,
            "c_or_b": "0.33092 uF/km",
            "rated_current_a": "MISSING_IN_SEARCH_SNIPPET",
            "thermal_limit_mva": "MISSING_IN_SEARCH_SNIPPET",
            "evidence_type": "spanish_project_type_proxy",
            "applicability": "Spanish proxy only; also conflicts with Prysmian x value, showing assumptions matter.",
            "confidence": "low_for_portuguese_model",
            "status": "SPANISH_PROXY_SENSITIVITY_ONLY",
            "notes": "Keep separate from Portuguese LXHIOLE evidence.",
        },
        {
            "asset_type": "underground_cable",
            "source_id": "SRC_EN_EESCABLE_36_66KV_1X400_AL",
            "language": "en",
            "source_title": "EES Cable 36/66 (72.5) kV aluminium XLPE cable datasheet",
            "url": "https://www.eescable.com/wp-content/uploads/2024/04/36-66KV-HV-POWER-CABLE.pdf",
            "parameter_set": "36/66 (72.5) kV 1x400 Al XLPE",
            "voltage_kv": 66,
            "r_ohm_per_km": 0.0754,
            "x_ohm_per_km": 0.08,
            "c_or_b": "0.25 uF/km",
            "rated_current_a": "590",
            "thermal_limit_mva": f"{round(thermal_mva(60, 590), 3)} at_60kV",
            "evidence_type": "english_manufacturer_same_spec_proxy",
            "applicability": "Exact voltage-class aluminium XLPE foreign manufacturer row for 1x400; useful as same-spec best-available proxy when Portuguese 400 mm2 R/X/C ficha is unavailable.",
            "confidence": "medium_high_for_same_spec_proxy_low_for_portuguese_source_backed_use",
            "status": "SAME_SPEC_FOREIGN_PROXY_CANDIDATE",
            "notes": "Web extraction found R20=0.0754 ohm/km, X=0.080 trefoil / 0.096 flat, C=0.25 uF/km, current=590 A.",
        },
        {
            "asset_type": "underground_cable",
            "source_id": "SRC_EN_EESCABLE_36_66KV_1X630_AL",
            "language": "en",
            "source_title": "EES Cable 36/66 (72.5) kV aluminium XLPE cable datasheet",
            "url": "https://www.eescable.com/wp-content/uploads/2024/04/36-66KV-HV-POWER-CABLE.pdf",
            "parameter_set": "36/66 (72.5) kV 1x630 Al XLPE",
            "voltage_kv": 66,
            "r_ohm_per_km": 0.0470,
            "x_ohm_per_km": 0.078,
            "c_or_b": "0.27 uF/km",
            "rated_current_a": "740",
            "thermal_limit_mva": f"{round(thermal_mva(60, 740), 3)} at_60kV",
            "evidence_type": "english_manufacturer_same_spec_proxy",
            "applicability": "Exact voltage-class aluminium XLPE foreign manufacturer row for 1x630; useful as same-spec best-available proxy when Portuguese 630 mm2 R/X/C ficha is unavailable.",
            "confidence": "medium_high_for_same_spec_proxy_low_for_portuguese_source_backed_use",
            "status": "SAME_SPEC_FOREIGN_PROXY_CANDIDATE",
            "notes": "Web extraction found R20=0.0470 ohm/km, X=0.078 trefoil / 0.094 flat, C=0.27 uF/km, current=740 A.",
        },
        {
            "asset_type": "underground_cable",
            "source_id": "SRC_ES_SELT_36_66KV_1X630_AL",
            "language": "es",
            "source_title": "SELT RHZ1-RA-2OL (S) 36/66 (72) kV 1x630 kAL + H95 technical sheet",
            "url": "https://www.selt.es/wp-content/uploads/2025/04/FT-AT-Al-001_Ed00-0_SELT_RHZ1-RA-2OLS-36-66_MAR2025.pdf",
            "parameter_set": "RHZ1-RA-2OL (S) 36/66 (72) kV 1x630 Al",
            "voltage_kv": 66,
            "r_ohm_per_km": 0.0469,
            "x_ohm_per_km": 0.17,
            "c_or_b": "0.23 uF/km",
            "rated_current_a": "MISSING_NOT_EXTRACTED",
            "thermal_limit_mva": "MISSING_NOT_EXTRACTED",
            "evidence_type": "spanish_manufacturer_same_spec_proxy",
            "applicability": "Exact voltage-class aluminium XLPE foreign manufacturer row for 1x630 with independent R/X/C confirmation; useful as same-spec proxy and cross-check against English manufacturer values.",
            "confidence": "medium_high_for_same_spec_proxy",
            "status": "SAME_SPEC_FOREIGN_PROXY_CANDIDATE",
            "notes": "Search extraction found R20=0.0469 ohm/km, X=0.17 ohm/km, C=0.23 uF/km.",
        },
        {
            "asset_type": "overhead_line",
            "source_id": "SRC_PT_THESIS_60KV_LINE_DESIGN",
            "language": "pt",
            "source_title": "Projeto e modelação de linhas elétricas de média e alta tensão até 60 kV",
            "url": "https://comum.rcaap.pt/bitstreams/517b9bc5-6657-4218-9216-a6a654f8743c/download",
            "parameter_set": "60 kV overhead design thesis/project",
            "voltage_kv": 60,
            "r_ohm_per_km": "TO_EXTRACT_IF_NEEDED",
            "x_ohm_per_km": "TO_EXTRACT_IF_NEEDED",
            "c_or_b": "TO_EXTRACT_IF_NEEDED",
            "rated_current_a": "TO_EXTRACT_IF_NEEDED",
            "thermal_limit_mva": "TO_EXTRACT_IF_NEEDED",
            "evidence_type": "portuguese_academic_project_source",
            "applicability": "Potentially useful formulas/project examples; not yet extracted into concrete LUT rows in this pass.",
            "confidence": "pending_extraction",
            "status": "FOLLOW_UP_EXTRACTION_CANDIDATE",
            "notes": "Downloaded locally for future detailed extraction if more overhead cases are needed.",
        },
    ]


def gaps() -> list[dict[str, Any]]:
    return [
        {"gap": "Complete overhead 60 kV B/C and continuous ampacity", "filled_by_pt_es_search": False, "note": "PE Tocha provides R and geometry-specific X for ACSR 325, but no capacitance/susceptance or ampacity LUT."},
        {"gap": "Cable R/X/C for all E-REDES sections 400/630/1000", "filled_by_pt_es_search": "partial", "note": "LXHIOLE 1000/135 has Portugal-specific R/X/C/current from PE Tocha; 400/630 still need fichas or remain proxy."},
        {"gap": "Transformer R/X split from losses", "filled_by_pt_es_search": False, "note": "PT/ES search found standards/procedures but no E-REDES 60/MT loss table for vkr calculation."},
        {"gap": "Branch-level conductor/cable assignment", "filled_by_pt_es_search": False, "note": "Even with candidate values, each branch still needs conductor family, cable section, installation condition, and circuit count."},
    ]


def markdown_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|").replace("\n", " ") for c in cols) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows())
    gap_df = pd.DataFrame(gaps())
    df.to_csv(OUT / "pt_es_supplemental_parameter_candidates.csv", index=False)
    gap_df.to_csv(OUT / "pt_es_search_gap_status.csv", index=False)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidate_rows": int(len(df)),
        "portugal_specific_candidate_rows": int(df["status"].astype(str).str.contains("PORTUGAL_SPECIFIC", na=False).sum()),
        "spanish_proxy_rows": int(df["status"].astype(str).str.contains("SPANISH_PROXY", na=False).sum()),
        "complete_pf_lut_ready": False,
        "status": "PARTIAL_IMPROVED_BUT_NOT_SOURCE_BACKED_READY",
    }
    (OUT / "pt_es_parameter_search_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    text = [
        "# 21 PT/ES Supplemental Parameter Search",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "Scope: Portuguese and Spanish language search for missing 60 kV parameters. This improves the parameter evidence base but does not make the Portuguese model source-backed PF/OPF ready.",
        "",
        "## Supplemental Candidates",
        "",
        markdown_table(df),
        "",
        "## Gap Status After PT/ES Search",
        "",
        markdown_table(gap_df),
        "",
        "## Conclusion",
        "",
        "The most useful new source is the Portuguese PE Tocha II – Tocha 60 kV project, which provides ACSR 325 overhead R and project-specific X plus LXHIOLE 1x1000/135 cable R/X/C/current. This can support a Portugal-specific sensitivity scenario for branches that match those assets. It still cannot fill every E-REDES branch because branch-level conductor/cable section/circuit count is missing, overhead B/C and ampacity remain incomplete, and transformer R/X split remains unavailable.",
    ]
    (REPORTS / "21_pt_es_supplemental_parameter_search.md").write_text("\n".join(text), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
