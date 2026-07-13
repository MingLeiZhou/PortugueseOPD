"""Organize Chinese-language / China manufacturer cable search findings."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "processed" / "chinese_cable_search"
REPORTS = ROOT / "reports"


def thermal_mva(voltage_kv: float, current_a: float) -> float:
    return math.sqrt(3.0) * voltage_kv * current_a / 1000.0


def rows() -> list[dict[str, Any]]:
    return [
        {
            "source_id": "SRC_CN_TANO_GE_66KV_1000_CU",
            "language": "zh/en",
            "source_title": "Tano/GE Cable 66 kV 1000 mm2 XLPE corrugated aluminium sheath power cable",
            "url": "https://www.gecable.com/1000mm2-xlpe-hv-power-cable-segment-round-conductor-high-durability/",
            "country_or_region": "China",
            "voltage_class": "66 kV / Um 72.5 kV",
            "conductor_material": "copper",
            "cross_section_mm2": 1000,
            "r_dc_20c_ohm_per_km": 0.0176,
            "x_ohm_per_km": "MISSING_NOT_LISTED",
            "capacitance_uf_per_km": 0.33,
            "current_rating_a": "1119 buried / 1075 duct / 1361 air / 1615 trefoil",
            "thermal_mva_at_60kv": f"{round(thermal_mva(60, 1119), 3)} buried / {round(thermal_mva(60, 1361), 3)} air / {round(thermal_mva(60, 1615), 3)} trefoil",
            "same_as_lxhiole_1000_al": False,
            "applicability": "Chinese manufacturer can produce adjacent 72.5 kV 1000 mm2 XLPE cable, but this row is copper conductor, not aluminium LXHIOLE. Use only as non-equivalent reference.",
            "status": "CHINA_MANUFACTURED_ADJACENT_SPEC_NOT_DIRECT_PROXY",
        },
        {
            "source_id": "SRC_CN_VWCABLE_GRANDCABLE_48_66KV_1000_CU",
            "language": "zh/en",
            "source_title": "Grandcable/VWCable 48/66 kV 72.5 kV 1x1000 mm2 copper core XLPE cable",
            "url": "https://www.vwcable.com/48-66kv-72-5kv-1x1000mm2-copper-core-xlpe-insulated-corrugated-aluminum-sheath-ehv-underground-power-cable/",
            "country_or_region": "China / Jiangsu",
            "voltage_class": "48/66 kV / Um 72.5 kV",
            "conductor_material": "copper",
            "cross_section_mm2": 1000,
            "r_dc_20c_ohm_per_km": "MISSING_NOT_EXTRACTED",
            "x_ohm_per_km": "MISSING_NOT_EXTRACTED",
            "capacitance_uf_per_km": "MISSING_NOT_EXTRACTED",
            "current_rating_a": "MISSING_NOT_EXTRACTED",
            "thermal_mva_at_60kv": "MISSING_NOT_EXTRACTED",
            "same_as_lxhiole_1000_al": False,
            "applicability": "Evidence of Chinese production of same voltage class/section but copper-core construction; not directly applicable to aluminium LXHIOLE electrical constants.",
            "status": "CHINA_MANUFACTURED_ADJACENT_SPEC_NOT_DIRECT_PROXY",
        },
        {
            "source_id": "SRC_CN_WANMA_64_110KV_1000",
            "language": "zh/en",
            "source_title": "Wanma YJLW02 64/110 kV 1x1000 XLPE corrugated aluminium sheath cable",
            "url": "https://wanma-cable.en.made-in-china.com/product/GSZnsAfUHocI/China-Yjlw02-64-110kv-1X1000-Super-High-Voltage-110kv-Single-XLPE-Insulated-Corrugated-Aluminum-Sheath-PVC-Outer-Sheath-Power-Cable.html",
            "country_or_region": "China",
            "voltage_class": "64/110 kV / Um 126 kV",
            "conductor_material": "not confirmed in extracted content",
            "cross_section_mm2": 1000,
            "r_dc_20c_ohm_per_km": "MISSING_NOT_EXTRACTED",
            "x_ohm_per_km": "MISSING_NOT_EXTRACTED",
            "capacitance_uf_per_km": "MISSING_NOT_EXTRACTED",
            "current_rating_a": "MISSING_NOT_EXTRACTED",
            "thermal_mva_at_60kv": "MISSING_NOT_EXTRACTED",
            "same_as_lxhiole_1000_al": False,
            "applicability": "Higher voltage class than 36/60 kV; evidence of Chinese EHV cable manufacturing, not direct parameter source for LXHIOLE 72.5 kV.",
            "status": "CHINA_MANUFACTURED_HIGHER_VOLTAGE_NOT_DIRECT_PROXY",
        },
        {
            "source_id": "SRC_NEXANS_TSLF_72KV_1000_AL_CROSSCHECK",
            "language": "en",
            "source_title": "Nexans TSLF 72 kV 1x1000A aluminium conductor XLPE cable",
            "url": "https://www.nexans.no/en/products/Utility-and-Power-cables/72---170-kV-Distribusjonskabel/TSLF-72----66332/product~ID540173453~.html",
            "country_or_region": "Norway / international manufacturer reference",
            "voltage_class": "36/66 kV / Um 72.5 kV",
            "conductor_material": "aluminium",
            "cross_section_mm2": 1000,
            "r_dc_20c_ohm_per_km": 0.0291,
            "x_ohm_per_km": "0.10 trefoil / 0.16 flat",
            "capacitance_uf_per_km": 0.28,
            "current_rating_a": "745 buried flat / 895 buried trefoil / 1100 air flat / 1180 air trefoil",
            "thermal_mva_at_60kv": f"{round(thermal_mva(60, 895), 3)} buried_trefoil / {round(thermal_mva(60, 1180), 3)} air_trefoil",
            "same_as_lxhiole_1000_al": "very_close_electrical_crosscheck",
            "applicability": "Not Chinese and not LXHIOLE, but voltage/material/section match closely and R/X/C exactly cross-check PE Tocha II LXHIOLE 1000 values. Strong external validation reference.",
            "status": "NON_CHINESE_STRONG_CROSSCHECK_FOR_LXHIOLE_1000",
        },
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
    df.to_csv(OUT / "chinese_and_adjacent_cable_candidates.csv", index=False)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidate_rows": int(len(df)),
        "china_manufactured_rows": int(df["country_or_region"].astype(str).str.contains("China", case=False, na=False).sum()),
        "direct_same_spec_china_aluminium_lxhiole_found": False,
        "adjacent_china_72p5kv_1000mm2_found": True,
        "strong_non_chinese_crosscheck_found": True,
        "status": "CHINA_ADJACENT_SPEC_FOUND_NOT_DIRECT_LXHIOLE_REPLACEMENT",
    }
    (OUT / "chinese_cable_search_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    text = [
        "# 22 Chinese Cable Search for 36/60 kV 1x1000 mm² XLPE",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "Scope: Chinese-language / China-manufacturer search for same or adjacent cable specifications. The target Portuguese cable is LXHIOLE 1x1000/135 36/60(72.5) kV aluminium conductor.",
        "",
        "## Findings",
        "",
        markdown_table(df),
        "",
        "## Interpretation",
        "",
        "Chinese manufacturers do appear to produce adjacent high-voltage XLPE 1000 mm² cables in the 66 kV/72.5 kV and 110 kV classes, but the extracted China rows are copper-core or higher-voltage variants and cannot directly replace the Portuguese LXHIOLE aluminium-conductor 36/60 kV parameters. The strongest cross-check remains the Nexans TSLF 72 kV aluminium 1x1000 cable, whose R/X/C values match the Portuguese PE Tocha II LXHIOLE 1000 data closely.",
    ]
    (REPORTS / "22_chinese_cable_search.md").write_text("\n".join(text), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
