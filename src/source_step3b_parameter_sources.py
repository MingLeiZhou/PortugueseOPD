"""Build Step 3B source-backed parameter lookup inventory.

This script records citable Portuguese, European, manufacturer, and open-model
sources that can support future 60 kV parameter lookup tables. It does not
assign final electrical parameters to the candidate topology.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
REPORTS = ROOT / "reports"
ACCESS_DATE = date(2026, 7, 6).isoformat()


def thermal_mva(voltage_kv: float, current_a: float) -> float:
    """Three-phase apparent power at nominal voltage."""
    return math.sqrt(3.0) * voltage_kv * (current_a / 1000.0)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> pd.DataFrame:
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False)
    return df


def clean_numeric(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value))
    return float(match.group(0)) if match else None


def extract_cross_section_mm2(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value)
    for pattern in [r"1x(\d{2,4})(?:/\d+)?", r"(\d{2,4})\s*mm2", r"(\d{2,4})\s*k?AL\b", r"(\d{2,4})\s*Al\b"]:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return float(match.group(1))
    return clean_numeric(value)


def capacitance_to_nf_per_km(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    text = str(value)
    numeric = clean_numeric(text)
    if numeric is None:
        return None
    lowered = text.lower()
    if "uf" in lowered or "μf" in lowered:
        return numeric * 1000.0
    return numeric


def normalize_multilingual_candidates() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add(**kwargs: Any) -> None:
        base = {
            "source_id": "",
            "asset_type": "",
            "parameter_family": "",
            "parameter_name": "",
            "parameter_set": "",
            "language": "",
            "country_or_region": "",
            "source_title": "",
            "url": "",
            "voltage_kv": "",
            "material": "",
            "cross_section_mm2": "",
            "installation_type": "",
            "numeric_value": "",
            "unit": "",
            "evidence_status": "",
            "evidence_method": "",
            "spec_match_class": "",
            "match_score": "",
            "selection_status": "ALTERNATIVE_CANDIDATE",
            "fail_closed_class": "NO_SOLVER",
            "diagnostic_allowed": False,
            "publication_allowed": False,
            "confidence": "",
            "applicability": "",
            "notes": "",
        }
        base.update(kwargs)
        rows.append(base)

    pt_overhead = pd.read_csv(PROCESSED / "portuguese_60kv_parameter_search" / "pt_60kv_overhead_parameter_candidates.csv")
    pt_cable = pd.read_csv(PROCESSED / "portuguese_60kv_parameter_search" / "pt_60kv_cable_parameter_candidates.csv")
    pt_trafo = pd.read_csv(PROCESSED / "portuguese_60kv_parameter_search" / "pt_60kv_transformer_parameter_candidates.csv")
    pt_es = pd.read_csv(PROCESSED / "pt_es_parameter_search" / "pt_es_supplemental_parameter_candidates.csv")
    cn = pd.read_csv(PROCESSED / "chinese_cable_search" / "chinese_and_adjacent_cable_candidates.csv")

    for _, row in pt_overhead.iterrows():
        add(
            source_id=row["source_id"],
            asset_type="overhead_line",
            parameter_family="overhead_line",
            parameter_name="r_ohm_per_km",
            parameter_set=str(row.get("conductor_reference", "")),
            language="pt",
            country_or_region="Portugal",
            source_title=str(row.get("conductor_reference", "")),
            url="https://www.e-redes.pt/",
            voltage_kv=60,
            material="overhead_conductor",
            cross_section_mm2=clean_numeric(row.get("section_total_mm2")),
            numeric_value=clean_numeric(row.get("r_dc_20c_ohm_per_km")),
            unit="ohm/km",
            evidence_status="SOURCE_BACKED_PARTIAL" if str(row.get("value_status", "")).startswith("SOURCE_BACKED") else "CROSSCHECK_ONLY",
            evidence_method="direct_source",
            spec_match_class="direct_portugal",
            match_score=95 if str(row.get("value_status", "")).startswith("SOURCE_BACKED") else 55,
            fail_closed_class="NO_SOLVER",
            diagnostic_allowed=False,
            confidence=row.get("source_confidence", ""),
            applicability=row.get("applicability", ""),
            notes=row.get("notes", ""),
        )

    for _, row in pt_cable.iterrows():
        add(
            source_id=row["source_id"],
            asset_type="underground_cable",
            parameter_family="cable",
            parameter_name="rated_current_a",
            parameter_set=f"LXHIOLE_{row.get('section_mm2')}_{row.get('installation_condition')}",
            language="pt",
            country_or_region="Portugal",
            source_title=str(row.get("cable_type", "")),
            url="https://www.e-redes.pt/",
            voltage_kv=60,
            material="aluminium",
            cross_section_mm2=clean_numeric(row.get("section_mm2")),
            installation_type=row.get("installation_condition", ""),
            numeric_value=clean_numeric(row.get("rated_current_a")),
            unit="A",
            evidence_status="SOURCE_BACKED_PARTIAL",
            evidence_method="direct_source",
            spec_match_class="direct_portugal",
            match_score=96,
            fail_closed_class="DIAGNOSTIC_ONLY",
            diagnostic_allowed=True,
            confidence=row.get("source_confidence", ""),
            applicability="Direct E-REDES cable current scenario.",
            notes=row.get("notes", ""),
        )

    for _, row in pt_trafo.iterrows():
        add(
            source_id=row["source_id"],
            asset_type="transformer",
            parameter_family="transformer",
            parameter_name="vk_percent",
            parameter_set=f"TR_60_MT_{row.get('rated_mva_reference')}",
            language="pt",
            country_or_region="Portugal",
            source_title="E-REDES 60 kV/MT transformer spec",
            url="https://www.e-redes.pt/",
            voltage_kv=60,
            numeric_value=clean_numeric(row.get("vk_percent")),
            unit="percent",
            evidence_status="SOURCE_BACKED_PARTIAL",
            evidence_method="direct_source",
            spec_match_class="direct_portugal",
            match_score=97,
            fail_closed_class="NO_SOLVER",
            diagnostic_allowed=False,
            confidence=row.get("source_confidence", ""),
            applicability="Direct Portuguese transformer uk% source.",
            notes=row.get("notes", ""),
        )

    for _, row in pt_es.iterrows():
        mapping = [
            ("r_ohm_per_km", "ohm/km", clean_numeric(row.get("r_ohm_per_km"))),
            ("x_ohm_per_km", "ohm/km", clean_numeric(row.get("x_ohm_per_km"))),
            ("c_nf_per_km", "nF/km", capacitance_to_nf_per_km(row.get("c_or_b"))),
        ]
        for parameter_name, unit, numeric_value in mapping:
            if numeric_value is None:
                continue
            status = str(row.get("status", ""))
            spec_class = "project_specific_portugal" if "PORTUGAL_SPECIFIC" in status else "same_spec_foreign"
            evidence_method = "direct_source" if "PORTUGAL_SPECIFIC" in status else "same_spec_proxy"
            evidence_status = "SOURCE_BACKED_PARTIAL" if "PORTUGAL_SPECIFIC" in status else "PROXY_SAME_SPEC"
            score = 92 if "PORTUGAL_SPECIFIC_CABLE_PARAMETER_CANDIDATE" in status else 82 if "PORTUGAL_SPECIFIC" in status else 74 if "SPANISH_PROXY" in status else 40
            add(
                source_id=row["source_id"],
                asset_type=row.get("asset_type", ""),
                parameter_family="cable" if row.get("asset_type") == "underground_cable" else "overhead_line",
                parameter_name=parameter_name,
                parameter_set=str(row.get("parameter_set", "")),
                language=row.get("language", ""),
                country_or_region="Portugal" if row.get("language") == "pt" else "Spain",
                source_title=row.get("source_title", ""),
                url=row.get("url", ""),
                voltage_kv=clean_numeric(row.get("voltage_kv")),
                material="aluminium" if "Al" in str(row.get("parameter_set", "")) or "LXHIOLE" in str(row.get("parameter_set", "")) else "unknown",
                cross_section_mm2=extract_cross_section_mm2(row.get("parameter_set", "")),
                numeric_value=numeric_value,
                unit=unit,
                evidence_status=evidence_status,
                evidence_method=evidence_method,
                spec_match_class=spec_class,
                match_score=score,
                fail_closed_class="NO_SOLVER" if "PORTUGAL_SPECIFIC" in status else "DIAGNOSTIC_ONLY",
                diagnostic_allowed=bool("SPANISH_PROXY" in status or "PORTUGAL_SPECIFIC" in status),
                confidence=row.get("confidence", ""),
                applicability=row.get("applicability", ""),
                notes=row.get("notes", ""),
            )

    for _, row in cn.iterrows():
        mapping = [
            ("r_ohm_per_km", "ohm/km", clean_numeric(row.get("r_dc_20c_ohm_per_km"))),
            ("x_ohm_per_km", "ohm/km", clean_numeric(row.get("x_ohm_per_km"))),
            ("c_nf_per_km", "nF/km", capacitance_to_nf_per_km(row.get("capacitance_uf_per_km"))),
        ]
        for parameter_name, unit, numeric_value in mapping:
            if numeric_value is None:
                continue
            status = str(row.get("status", ""))
            if status == "NON_CHINESE_STRONG_CROSSCHECK_FOR_LXHIOLE_1000":
                evidence_status = "CROSSCHECK_ONLY"
                evidence_method = "crosscheck"
                spec_class = "same_spec_foreign"
                score = 88
            elif "ADJACENT_SPEC" in status:
                evidence_status = "PROXY_ADJACENT_SPEC"
                evidence_method = "adjacent_spec_proxy"
                spec_class = "adjacent_spec_foreign"
                score = 62
            else:
                evidence_status = "PROXY_ADJACENT_SPEC"
                evidence_method = "adjacent_spec_proxy"
                spec_class = "adjacent_spec_foreign"
                score = 52
            add(
                source_id=row["source_id"],
                asset_type="underground_cable",
                parameter_family="cable",
                parameter_name=parameter_name,
                parameter_set=f"{row.get('voltage_class', '')}_{row.get('cross_section_mm2', '')}",
                language=row.get("language", ""),
                country_or_region=row.get("country_or_region", ""),
                source_title=row.get("source_title", ""),
                url=row.get("url", ""),
                voltage_kv=60,
                material=row.get("conductor_material", ""),
                cross_section_mm2=clean_numeric(row.get("cross_section_mm2")),
                numeric_value=numeric_value,
                unit=unit,
                evidence_status=evidence_status,
                evidence_method=evidence_method,
                spec_match_class=spec_class,
                match_score=score,
                fail_closed_class="DIAGNOSTIC_ONLY",
                diagnostic_allowed=True,
                confidence="medium" if evidence_status == "CROSSCHECK_ONLY" else "low",
                applicability=row.get("applicability", ""),
                notes=status,
            )

    return rows


def best_available_parameter_table(evidence_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    df = pd.DataFrame(evidence_rows)
    if df.empty:
        return []
    df = df.copy()
    df["match_score_numeric"] = pd.to_numeric(df["match_score"], errors="coerce").fillna(-1)
    df["numeric_value_present"] = df["numeric_value"].notna() & (df["numeric_value"].astype(str) != "")
    selected_rows: list[dict[str, Any]] = []
    for _, group in df.groupby(["asset_type", "parameter_family", "parameter_name", "cross_section_mm2"], dropna=False):
        usable = group[group["numeric_value_present"]].sort_values(["match_score_numeric", "diagnostic_allowed"], ascending=[False, False])
        if usable.empty:
            continue
        best_idx = usable.index[0]
        for idx, row in group.iterrows():
            out = row.to_dict()
            out.pop("match_score_numeric", None)
            out.pop("numeric_value_present", None)
            out["selection_status"] = "BEST_AVAILABLE" if idx == best_idx else "ALTERNATIVE_CANDIDATE"
            if out["selection_status"] == "BEST_AVAILABLE":
                if out["evidence_status"] == "SOURCE_BACKED_PARTIAL":
                    out["selection_reason"] = "Highest-ranked direct Portuguese or Portugal-project-specific evidence with numeric value."
                elif out["evidence_status"] == "PROXY_SAME_SPEC":
                    out["selection_reason"] = "No direct Portuguese row available; selected same-spec foreign proxy."
                elif out["evidence_status"] == "PROXY_ADJACENT_SPEC":
                    out["selection_reason"] = "No direct or same-spec row available; selected adjacent-spec proxy for diagnostics only."
                elif out["evidence_status"] == "CROSSCHECK_ONLY":
                    out["selection_reason"] = "Selected as best external cross-check for a matching specification bucket."
                else:
                    out["selection_reason"] = "Selected highest-ranked available row."
            else:
                out["selection_reason"] = "Kept as alternative candidate for transparency."
            selected_rows.append(out)
    return selected_rows


def multilingual_gap_analysis(best_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    df = pd.DataFrame(best_rows)
    rows: list[dict[str, Any]] = []
    checks = [
        ("overhead_line", "r_ohm_per_km", "Portuguese overhead R availability"),
        ("overhead_line", "x_ohm_per_km", "Portuguese overhead X availability"),
        ("cable", "r_ohm_per_km", "Cable R availability"),
        ("cable", "x_ohm_per_km", "Cable X availability"),
        ("cable", "c_nf_per_km", "Cable capacitance availability"),
        ("transformer", "vk_percent", "Transformer uk availability"),
    ]
    for asset_type, parameter_name, label in checks:
        subset = df[(df["parameter_family"] == asset_type.replace("underground_", "")) | (df["asset_type"] == asset_type)]
        subset = subset[subset["parameter_name"] == parameter_name]
        best = subset[subset["selection_status"] == "BEST_AVAILABLE"] if not subset.empty else pd.DataFrame()
        if best.empty:
            rows.append({
                "asset_type": asset_type,
                "parameter_name": parameter_name,
                "gap": label,
                "source_backed_status": "MISSING_REQUIRED",
                "best_available_status": "MISSING_REQUIRED",
                "diagnostic_fill_status": "NOT_FILLABLE",
                "evidence": "No multilingual numeric candidate selected.",
            })
            continue
        top = best.iloc[0]
        evidence_status = str(top.get("evidence_status", ""))
        source_backed_status = "SOURCE_BACKED_PARTIAL" if evidence_status == "SOURCE_BACKED_PARTIAL" else "BLOCKED_EXTERNAL_DATA"
        best_status = evidence_status
        diagnostic_fill_status = "DIAGNOSTIC_FILLABLE" if bool(top.get("diagnostic_allowed")) else "NO_SOLVER"
        rows.append({
            "asset_type": asset_type,
            "parameter_name": parameter_name,
            "gap": label,
            "source_backed_status": source_backed_status,
            "best_available_status": best_status,
            "diagnostic_fill_status": diagnostic_fill_status,
            "evidence": top.get("selection_reason", ""),
        })
    return rows


def best_available_cable_diagnostic_table(best_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    df = pd.DataFrame(best_rows)
    if df.empty:
        return []
    cable = df[(df["parameter_family"] == "cable") & (df["selection_status"] == "BEST_AVAILABLE")].copy()
    if cable.empty:
        return []

    grouped_rows: list[dict[str, Any]] = []
    for cross_section_mm2, group in cable.groupby("cross_section_mm2", dropna=False):
        current_rows = group[group["parameter_name"] == "rated_current_a"].sort_values("match_score", ascending=False)
        impedance_rows = group[group["parameter_name"].isin(["r_ohm_per_km", "x_ohm_per_km", "c_nf_per_km"])].sort_values("match_score", ascending=False)

        preferred_parameter_set = ""
        if len(current_rows):
            preferred_parameter_set = str(current_rows.iloc[0].get("parameter_set", ""))
        elif len(impedance_rows):
            preferred_parameter_set = str(impedance_rows.iloc[0].get("parameter_set", ""))

        available_voltage_classes = sorted({str(x) for x in group["voltage_kv"].dropna().unique() if str(x)})
        record: dict[str, Any] = {
            "cross_section_mm2": cross_section_mm2,
            "nominal_voltage_kv_for_current": current_rows.iloc[0].get("voltage_kv", "") if len(current_rows) else "",
            "nominal_voltage_kv_for_impedance": impedance_rows.iloc[0].get("voltage_kv", "") if len(impedance_rows) else "",
            "available_voltage_classes_kv": "; ".join(available_voltage_classes),
            "parameter_set": preferred_parameter_set,
            "material": "; ".join(sorted(set(str(x) for x in group["material"].dropna().unique() if str(x)))),
            "diagnostic_allowed": bool(group["diagnostic_allowed"].fillna(False).any()),
            "publication_allowed": False,
            "selected_evidence_statuses": "; ".join(sorted(set(str(x) for x in group["evidence_status"].dropna().unique()))),
            "selected_evidence_methods": "; ".join(sorted(set(str(x) for x in group["evidence_method"].dropna().unique()))),
            "selected_spec_match_classes": "; ".join(sorted(set(str(x) for x in group["spec_match_class"].dropna().unique()))),
            "source_ids": "; ".join(sorted(set(str(x) for x in group["source_id"].dropna().unique()))),
            "languages": "; ".join(sorted(set(str(x) for x in group["language"].dropna().unique()))),
            "country_or_region": "; ".join(sorted(set(str(x) for x in group["country_or_region"].dropna().unique()))),
            "source_titles": " | ".join(sorted(set(str(x) for x in group["source_title"].dropna().unique()))),
            "urls": " | ".join(sorted(set(str(x) for x in group["url"].dropna().unique()))),
            "selection_reasons": " | ".join(sorted(set(str(x) for x in group["selection_reason"].dropna().unique()))),
            "notes": " | ".join(sorted(set(str(x) for x in group["notes"].dropna().unique()))),
        }
        for parameter_name in ["r_ohm_per_km", "x_ohm_per_km", "c_nf_per_km", "rated_current_a"]:
            subset = group[group["parameter_name"] == parameter_name]
            if len(subset):
                row = subset.sort_values("match_score", ascending=False).iloc[0]
                record[parameter_name] = row.get("numeric_value", "")
                record[f"{parameter_name}_source_id"] = row.get("source_id", "")
                record[f"{parameter_name}_evidence_status"] = row.get("evidence_status", "")
                record[f"{parameter_name}_spec_match_class"] = row.get("spec_match_class", "")
                record[f"{parameter_name}_language"] = row.get("language", "")
                record[f"{parameter_name}_voltage_kv"] = row.get("voltage_kv", "")
                record[f"{parameter_name}_parameter_set"] = row.get("parameter_set", "")
            else:
                record[parameter_name] = ""
                record[f"{parameter_name}_source_id"] = ""
                record[f"{parameter_name}_evidence_status"] = ""
                record[f"{parameter_name}_spec_match_class"] = ""
                record[f"{parameter_name}_language"] = ""
                record[f"{parameter_name}_voltage_kv"] = ""
                record[f"{parameter_name}_parameter_set"] = ""
        if pd.notna(record.get("rated_current_a")) and str(record.get("rated_current_a")) != "":
            record["thermal_limit_mva_at_60kv"] = round(thermal_mva(60.0, float(record["rated_current_a"])), 3)
        else:
            record["thermal_limit_mva_at_60kv"] = ""
        grouped_rows.append(record)
    return grouped_rows


def write_multilingual_report(evidence_df: pd.DataFrame, best_df: pd.DataFrame, gap_df: pd.DataFrame, summary: dict[str, Any]) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    status_counts = evidence_df["evidence_status"].value_counts().rename_axis("evidence_status").reset_index(name="rows") if len(evidence_df) else pd.DataFrame()
    selection_counts = best_df["selection_status"].value_counts().rename_axis("selection_status").reset_index(name="rows") if len(best_df) else pd.DataFrame()
    text = [
        "# 23 Multilingual Best-Available Parameter Evidence",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "Scope: merge Portuguese, Spanish, Chinese, and English parameter evidence into a ranked best-available inventory for diagnostic use. This does not promote the model to source-backed publication readiness.",
        "",
        "## Evidence Status Counts",
        "",
        markdown_table(status_counts, ["evidence_status", "rows"], max_rows=20) if len(status_counts) else "_No rows._",
        "",
        "## Selection Status Counts",
        "",
        markdown_table(selection_counts, ["selection_status", "rows"], max_rows=20) if len(selection_counts) else "_No rows._",
        "",
        "## Example Best-Available Rows",
        "",
        markdown_table(best_df, ["asset_type", "parameter_name", "parameter_set", "language", "evidence_status", "selection_status", "match_score", "diagnostic_allowed", "source_id"], max_rows=25) if len(best_df) else "_No rows._",
        "",
        "## Remaining Multilingual Gaps",
        "",
        markdown_table(gap_df, ["asset_type", "parameter_name", "source_backed_status", "best_available_status", "diagnostic_fill_status", "evidence"], max_rows=30) if len(gap_df) else "_No rows._",
    ]
    (REPORTS / "23_multilingual_best_available_parameter_evidence.md").write_text("\n".join(text), encoding="utf-8")


def source_inventory() -> list[dict[str, Any]]:
    return [
        {
            "source_id": "SRC_EREDES_NORM_CATALOG",
            "source_title": "E-REDES Catalogo Documentos Normativos",
            "source_type": "official_operator_document",
            "publisher": "E-REDES / EDP Distribuicao",
            "country_or_region": "Portugal",
            "URL": "https://www.e-redes.pt/pt-pt/clientes-e-parceiros/profissionais/documentos-normativos",
            "access_date": ACCESS_DATE,
            "language": "Portuguese",
            "voltage_level": "AT/MT/BT catalogue",
            "asset_type": "calibration_statistics",
            "directly_applicable_to_portugal": True,
            "values_extractable": False,
            "paywalled_or_public": "public",
            "confidence": "high",
            "notes": "Catalogue exposes public E-REDES normative PDFs, including 60 kV/MT transformers and 36/60 kV AT cables.",
        },
        {
            "source_id": "SRC_EREDES_DMAC52140",
            "source_title": "DMA-C52-140/N Transformadores de Potencia: transformadores trifasicos, de 60 kV/MT",
            "source_type": "official_operator_document",
            "publisher": "E-REDES / EDP Distribuicao",
            "country_or_region": "Portugal",
            "URL": "https://www.e-redes.pt/sites/eredes/files/2020-06/DMA-C52-140_0.pdf",
            "access_date": ACCESS_DATE,
            "language": "Portuguese",
            "voltage_level": "60 kV/MT",
            "asset_type": "transformer",
            "directly_applicable_to_portugal": True,
            "values_extractable": True,
            "paywalled_or_public": "public",
            "confidence": "high",
            "notes": "Official E-REDES transformer specification with rated powers, 60 kV primary, MT secondary options, uk% table, OLTC tap range/step, and EN/IEC references.",
        },
        {
            "source_id": "SRC_EREDES_DMAC33281",
            "source_title": "DMA-C33-281/N Cabos isolados AT, 36/60 (72.5) kV",
            "source_type": "official_operator_document",
            "publisher": "E-REDES / EDP Distribuicao",
            "country_or_region": "Portugal",
            "URL": "https://www.e-redes.pt/sites/eredes/files/2020-05/DMA%20C33%20281.pdf",
            "access_date": ACCESS_DATE,
            "language": "Portuguese",
            "voltage_level": "36/60 (72.5) kV",
            "asset_type": "underground_cable",
            "directly_applicable_to_portugal": True,
            "values_extractable": True,
            "paywalled_or_public": "public",
            "confidence": "high",
            "notes": "Official E-REDES AT cable specification. It gives standard sections and indicative ampacities by installation condition, but requires manufacturers to declare reactance and capacitance.",
        },
        {
            "source_id": "SRC_EREDES_DMAC34110",
            "source_title": "DMA-C34-110/N Condutores nus para linhas aereas: cabos de cobre",
            "source_type": "official_operator_document",
            "publisher": "E-REDES / EDP Distribuicao",
            "country_or_region": "Portugal",
            "URL": "https://www.e-redes.pt/sites/eredes/files/normative_docs/DMA-C34-110.pdf",
            "access_date": ACCESS_DATE,
            "language": "Portuguese",
            "voltage_level": "not voltage-specific",
            "asset_type": "overhead_line",
            "directly_applicable_to_portugal": True,
            "values_extractable": True,
            "paywalled_or_public": "public",
            "confidence": "medium",
            "notes": "Official E-REDES bare copper overhead conductor specification with resistance by section. Applicability to 60 kV AT phase conductors is unconfirmed.",
        },
        {
            "source_id": "SRC_EREDES_DMAC67020",
            "source_title": "DMA-C67-020/N Postes de aco reticulados da serie F para linhas aereas de AT (60 kV)",
            "source_type": "official_operator_document",
            "publisher": "E-REDES / EDP Distribuicao",
            "country_or_region": "Portugal",
            "URL": "https://www.e-redes.pt/sites/eredes/files/normative_docs/DMA-C67-020N.pdf",
            "access_date": ACCESS_DATE,
            "language": "Portuguese",
            "voltage_level": "60 kV",
            "asset_type": "overhead_line",
            "directly_applicable_to_portugal": True,
            "values_extractable": False,
            "paywalled_or_public": "public",
            "confidence": "high",
            "notes": "Confirms E-REDES standardized support structures for 60 kV overhead lines; does not give conductor impedance, capacitance, or ratings.",
        },
        {
            "source_id": "SRC_EREDES_AT_CONNECTION",
            "source_title": "E-REDES Ligar instalacoes em alta tensao",
            "source_type": "official_operator_document",
            "publisher": "E-REDES",
            "country_or_region": "Portugal",
            "URL": "https://www.e-redes.pt/pt-pt/clientes-e-parceiros/profissionais/alta-tensao/ligar-instalacoes-em-alta-tensao",
            "access_date": ACCESS_DATE,
            "language": "Portuguese",
            "voltage_level": "60 kV",
            "asset_type": "calibration_statistics",
            "directly_applicable_to_portugal": True,
            "values_extractable": False,
            "paywalled_or_public": "public",
            "confidence": "high",
            "notes": "States that AT connections use the 60 kV network and can be via overhead lines or underground cables; no electrical constants.",
        },
        {
            "source_id": "SRC_EREDES_NETWORK_PAGE",
            "source_title": "E-REDES Conhecer a rede",
            "source_type": "official_operator_document",
            "publisher": "E-REDES",
            "country_or_region": "Portugal",
            "URL": "https://www.e-redes.pt/pt-pt/sobre-nos/nossa-rede/conhecer-rede",
            "access_date": ACCESS_DATE,
            "language": "Portuguese",
            "voltage_level": "AT/MT/BT",
            "asset_type": "calibration_statistics",
            "directly_applicable_to_portugal": True,
            "values_extractable": False,
            "paywalled_or_public": "public",
            "confidence": "high",
            "notes": "Official network/planning entry point with PDIRD and characterization links. Useful for source context, not line constants.",
        },
        {
            "source_id": "SRC_CABELTE_PRODUCTS",
            "source_title": "Cabelte product portfolio",
            "source_type": "manufacturer_catalog",
            "publisher": "Cabelte",
            "country_or_region": "Portugal",
            "URL": "https://www.cabelte.pt/os-nossos-produtos/",
            "access_date": ACCESS_DATE,
            "language": "Portuguese",
            "voltage_level": "up to 275 kV cables; overhead conductors up to 400 kV",
            "asset_type": "underground_cable; overhead_line",
            "directly_applicable_to_portugal": True,
            "values_extractable": False,
            "paywalled_or_public": "public",
            "confidence": "medium",
            "notes": "Portuguese manufacturer confirms relevant cable/conductor product families, but the accessible page does not expose R/X/B/current tables.",
        },
        {
            "source_id": "SRC_EFACEC_TRANSFORMERS",
            "source_title": "Efacec transformers portfolio",
            "source_type": "manufacturer_catalog",
            "publisher": "Efacec",
            "country_or_region": "Portugal",
            "URL": "https://www.efacec.com/pt/negocio/transformadores/",
            "access_date": ACCESS_DATE,
            "language": "Portuguese",
            "voltage_level": "distribution and power transformers up to HV levels",
            "asset_type": "transformer",
            "directly_applicable_to_portugal": True,
            "values_extractable": False,
            "paywalled_or_public": "public",
            "confidence": "medium",
            "notes": "Portuguese transformer manufacturer; accessible page confirms scope but not standard uk%, R/X split, or tap settings.",
        },
        {
            "source_id": "SRC_PANDAPOWER_STD",
            "source_title": "pandapower standard line and transformer types",
            "source_type": "software_standard_type",
            "publisher": "pandapower project",
            "country_or_region": "Europe / open-source",
            "URL": "https://pandapower.readthedocs.io/en/latest/std_types/basic.html",
            "access_date": ACCESS_DATE,
            "language": "English",
            "voltage_level": "110 kV line/cable; 110/20 and 110/10 kV transformers",
            "asset_type": "overhead_line; underground_cable; transformer",
            "directly_applicable_to_portugal": False,
            "values_extractable": True,
            "paywalled_or_public": "public",
            "confidence": "low",
            "notes": "Inspectable 50 Hz European benchmark standard types. Not Portugal-specific and not 60 kV; use only for sensitivity checks or code dry-runs.",
        },
        {
            "source_id": "SRC_IEC_60287",
            "source_title": "IEC 60287 electric cables current rating calculation",
            "source_type": "standard",
            "publisher": "IEC",
            "country_or_region": "International",
            "URL": "https://webstore.iec.ch/",
            "access_date": ACCESS_DATE,
            "language": "English",
            "voltage_level": "cables",
            "asset_type": "underground_cable",
            "directly_applicable_to_portugal": True,
            "values_extractable": False,
            "paywalled_or_public": "paywalled",
            "confidence": "medium",
            "notes": "Referenced by E-REDES cable specification for current rating calculations; method standard, not an open numeric LUT.",
        },
        {
            "source_id": "SRC_IEC_60840",
            "source_title": "IEC 60840 power cables above 30 kV up to 150 kV",
            "source_type": "standard",
            "publisher": "IEC",
            "country_or_region": "International",
            "URL": "https://webstore.iec.ch/",
            "access_date": ACCESS_DATE,
            "language": "English",
            "voltage_level": "Um 36 kV to 170 kV",
            "asset_type": "underground_cable",
            "directly_applicable_to_portugal": True,
            "values_extractable": False,
            "paywalled_or_public": "paywalled",
            "confidence": "medium",
            "notes": "Referenced by E-REDES cable specification for tests and requirements; not a public source of branch R/X/B values.",
        },
        {
            "source_id": "SRC_EN_60076",
            "source_title": "EN/IEC 60076 power transformer series",
            "source_type": "standard",
            "publisher": "CENELEC / IEC",
            "country_or_region": "Europe / International",
            "URL": "https://webstore.iec.ch/",
            "access_date": ACCESS_DATE,
            "language": "English",
            "voltage_level": "transformers",
            "asset_type": "transformer",
            "directly_applicable_to_portugal": True,
            "values_extractable": False,
            "paywalled_or_public": "paywalled",
            "confidence": "medium",
            "notes": "Referenced by E-REDES transformer specification for transformer requirements and tests; not an open numeric R/X LUT.",
        },
        {
            "source_id": "SRC_REFERENCE_PAPER",
            "source_title": "Building Power Grid Models from Open Data: A Complete Pipeline from OpenStreetMap to Optimal Power Flow",
            "source_type": "academic_paper",
            "publisher": "arXiv",
            "country_or_region": "United States / method reference",
            "URL": "https://arxiv.org/abs/2605.04289",
            "access_date": ACCESS_DATE,
            "language": "English",
            "voltage_level": "not Portugal-specific",
            "asset_type": "other",
            "directly_applicable_to_portugal": False,
            "values_extractable": False,
            "paywalled_or_public": "public",
            "confidence": "high for method; low for Portuguese numeric values",
            "notes": "Methodological reference only. Numeric US lookup tables must not be reused for Portugal.",
        },
    ]


def overhead_candidates() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    copper = [
        (16, "7 x 1.70", 1.140, 1.163),
        (25, "7 x 2.14", 0.719, 0.734),
        (35, "7 x 2.52", 0.519, 0.529),
        (50, "7 x 3.00", 0.366, 0.374),
        (95, "19 x 2.52", 0.192, 0.196),
        (185, "37 x 2.52", 0.099, 0.101),
    ]
    for section, conductor, r_nom, r_max in copper:
        rows.append(
            {
                "voltage_kv": "",
                "conductor_type": f"bare stranded copper {conductor}",
                "conductor_material": "copper",
                "cross_section_mm2": section,
                "r_ohm_per_km": r_nom,
                "x_ohm_per_km": "",
                "capacitance_or_b": "",
                "rated_current_a": "",
                "operating_temperature_assumption": "20 C resistance table",
                "thermal_limit_mva": "",
                "source_id": "SRC_EREDES_DMAC34110",
                "page/table/section": "Quadro 5, Caracteristicas gerais dos cabos nus de cobre",
                "applicability_comment": "Official E-REDES bare copper overhead conductor resistance. Voltage and actual use on 60 kV AT branches are not confirmed; X/B/current require separate line-design data.",
                "confidence": "low",
                "notes": f"Maximum resistance in source is {r_max} ohm/km.",
            }
        )

    pandapower_overhead = [
        ("149-AL1/24-ST1A 110.0", 149, 0.1940, 0.410, 8.75, 470),
        ("184-AL1/30-ST1A 110.0", 184, 0.1571, 0.400, 8.80, 535),
        ("243-AL1/39-ST1A 110.0", 243, 0.1188, 0.390, 9.00, 645),
        ("305-AL1/39-ST1A 110.0", 305, 0.0949, 0.380, 9.20, 740),
        ("490-AL1/64-ST1A 110.0", 490, 0.0590, 0.370, 9.75, 960),
        ("679-AL1/86-ST1A 110.0", 679, 0.0420, 0.360, 9.95, 1150),
    ]
    for name, section, r, x, c_nf, current_a in pandapower_overhead:
        rows.append(
            {
                "voltage_kv": 110,
                "conductor_type": name,
                "conductor_material": "AL1/ST1A",
                "cross_section_mm2": section,
                "r_ohm_per_km": r,
                "x_ohm_per_km": x,
                "capacitance_or_b": f"{c_nf} nF/km",
                "rated_current_a": current_a,
                "operating_temperature_assumption": "pandapower standard type",
                "thermal_limit_mva": round(thermal_mva(110, current_a), 3),
                "source_id": "SRC_PANDAPOWER_STD",
                "page/table/section": "available_std_types line; 110 kV overhead AL1/ST1A",
                "applicability_comment": "European software benchmark, not Portuguese 60 kV. Use only for sensitivity or code dry-runs, not a final LUT.",
                "confidence": "low",
                "notes": "Capacitance given as c_nf_per_km in pandapower, not B in siemens/km.",
            }
        )
    return rows


def cable_candidates() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    ampacity = {
        400: {
            "buried_soil_1_circuit_hot": 474,
            "buried_soil_1_circuit_cold": 582,
            "buried_soil_2_circuits_hot": 400,
            "buried_soil_2_circuits_cold": 496,
            "free_air_hot": 630,
            "free_air_cold": 689,
            "in_ducts_hot": 393,
            "in_ducts_cold": 429,
        },
        630: {
            "buried_soil_1_circuit_hot": 599,
            "buried_soil_1_circuit_cold": 740,
            "buried_soil_2_circuits_hot": 505,
            "buried_soil_2_circuits_cold": 629,
            "free_air_hot": 831,
            "free_air_cold": 909,
            "in_ducts_hot": 491,
            "in_ducts_cold": 535,
        },
        1000: {
            "buried_soil_1_circuit_hot": 725,
            "buried_soil_1_circuit_cold": 899,
            "buried_soil_2_circuits_hot": 613,
            "buried_soil_2_circuits_cold": 766,
            "free_air_hot": 1048,
            "free_air_cold": 1147,
            "in_ducts_hot": 585,
            "in_ducts_cold": 639,
        },
    }
    for section, values in ampacity.items():
        for condition, current_a in values.items():
            rows.append(
                {
                    "voltage_kv": 60,
                    "cable_voltage_rating": "36/60 (72.5) kV",
                    "cable_type": "LXHIOLE radial-field single-core extruded solid dielectric",
                    "conductor_material": "aluminium",
                    "cross_section_mm2": section,
                    "installation_type": condition,
                    "r_ohm_per_km": "",
                    "x_ohm_per_km": "",
                    "capacitance_per_km": "",
                    "rated_current_a": current_a,
                    "thermal_limit_mva": "",
                    "derived_thermal_limit_mva_at_60kv": round(thermal_mva(60, current_a), 3),
                    "source_id": "SRC_EREDES_DMAC33281",
                    "page/table/section": "Anexo B, Quadro B.1; section 10 reference conditions",
                    "applicability_comment": "Direct E-REDES 60 kV cable ampacity candidate. Values are explicitly indicative and depend on installation condition; branch-level cable section/installation is unknown.",
                    "confidence": "high for source; medium for branch-level use",
                    "notes": "R/X/capacitance are not populated in the specification; the manufacturer proposal ficha must provide capacitance and reactance.",
                }
            )

    pandapower_cables = [
        ("N2XS(FL)2Y 1x120 RM/35 64/110 kV", 120, 0.153, 0.166, 112, 366),
        ("N2XS(FL)2Y 1x185 RM/35 64/110 kV", 185, 0.099, 0.156, 125, 457),
        ("N2XS(FL)2Y 1x240 RM/35 64/110 kV", 240, 0.075, 0.149, 135, 526),
        ("N2XS(FL)2Y 1x300 RM/35 64/110 kV", 300, 0.060, 0.144, 144, 588),
    ]
    for name, section, r, x, c_nf, current_a in pandapower_cables:
        rows.append(
            {
                "voltage_kv": 110,
                "cable_voltage_rating": "64/110 kV",
                "cable_type": name,
                "conductor_material": "not stated in std type name",
                "cross_section_mm2": section,
                "installation_type": "pandapower standard type",
                "r_ohm_per_km": r,
                "x_ohm_per_km": x,
                "capacitance_per_km": f"{c_nf} nF/km",
                "rated_current_a": current_a,
                "thermal_limit_mva": round(thermal_mva(110, current_a), 3),
                "derived_thermal_limit_mva_at_60kv": round(thermal_mva(60, current_a), 3),
                "source_id": "SRC_PANDAPOWER_STD",
                "page/table/section": "available_std_types line; 64/110 kV cable",
                "applicability_comment": "European software benchmark, not an E-REDES 36/60 kV cable. Use only for sensitivity or code dry-runs.",
                "confidence": "low",
                "notes": "Capacitance given as c_nf_per_km in pandapower.",
            }
        )
    return rows


def transformer_candidates() -> list[dict[str, Any]]:
    uk_rows = [
        (3.15, 6.25),
        (6.3, 7.15),
        (10.0, 8.35),
        (12.5, 8.35),
        (20.0, 10.0),
        (31.5, 12.5),
        (40.0, 15.0),
    ]
    rows: list[dict[str, Any]] = []
    for rated_mva, uk in uk_rows:
        rows.append(
            {
                "voltage_ratio": "60 kV / MT options: 10.5, 15.75, 31.5, 31.5+10.5, 31.5+15.75, or 31.5/15.75 kV",
                "hv_kv": 60,
                "lv_kv": "10.5; 15.75; 31.5; multi-secondary options",
                "rated_mva": rated_mva,
                "short_circuit_impedance_percent": uk,
                "r_pu": "",
                "x_pu": "",
                "tap_range": "+/- 11 x 1.5% around nominal; 23 positions",
                "tap_step": "1.5%",
                "vector_group": "",
                "cooling_type": "ONAN/ONAF",
                "source_id": "SRC_EREDES_DMAC52140",
                "page/table/section": "R7 powers; R11 voltages; R14 short-circuit impedance; R56 tap steps",
                "applicability_comment": "Direct E-REDES 60 kV/MT transformer specification. uk% is directly usable by voltage/rating class; R/X split still needs load-loss/resistance data or assumption.",
                "confidence": "high",
                "notes": "Rated MVA is the lower-power winding of the pair for uk%. Main ONAF powers in the document are 10, 20, 31.5, and 40 MVA; ONAN powers are 7, 15, 25, and 30 MVA.",
            }
        )
    return rows


def candidate_lut(
    overhead: list[dict[str, Any]],
    cables: list[dict[str, Any]],
    transformers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add(
        voltage_kv: Any,
        asset_type: str,
        parameter: str,
        candidate_value: Any,
        unit: str,
        source_id: str,
        confidence: str,
        applicability: str,
        assumption_needed: str,
        notes: str,
    ) -> None:
        rows.append(
            {
                "voltage_kv": voltage_kv,
                "asset_type": asset_type,
                "parameter": parameter,
                "candidate_value": candidate_value,
                "unit": unit,
                "source_id": source_id,
                "confidence": confidence,
                "applicability": applicability,
                "assumption_needed": assumption_needed,
                "notes": notes,
            }
        )

    for section in [400, 630, 1000]:
        section_rows = [r for r in cables if r["source_id"] == "SRC_EREDES_DMAC33281" and r["cross_section_mm2"] == section]
        for r in section_rows:
            add(
                60,
                "cable",
                "rated_current_a",
                r["rated_current_a"],
                "A",
                r["source_id"],
                "high for source; medium for branch-level use",
                "direct_portugal",
                "Need cable section and installation condition per branch before selecting.",
                f"{section} mm2 aluminium 36/60 kV cable, {r['installation_type']}; E-REDES labels ampacity values as indicative.",
            )
            add(
                60,
                "cable",
                "thermal_limit_mva",
                r["derived_thermal_limit_mva_at_60kv"],
                "MVA",
                r["source_id"],
                "medium",
                "direct_portugal_derived",
                "Derived from sqrt(3)*60kV*I; only valid if this cable section/installation condition is selected.",
                f"Derived from E-REDES indicative current {r['rated_current_a']} A for {section} mm2, {r['installation_type']}.",
            )

    for r in [r for r in overhead if r["source_id"] == "SRC_EREDES_DMAC34110"]:
        add(
            60,
            "overhead",
            "r_ohm_per_km",
            r["r_ohm_per_km"],
            "ohm/km",
            r["source_id"],
            "low",
            "weak_proxy",
            "Actual 60 kV AT conductor material/section must be confirmed; source is bare copper, voltage not specified.",
            f"{r['conductor_type']}; {r['notes']}",
        )

    for r in [r for r in overhead if r["source_id"] == "SRC_PANDAPOWER_STD"]:
        for param, unit, value in [
            ("r_ohm_per_km", "ohm/km", r["r_ohm_per_km"]),
            ("x_ohm_per_km", "ohm/km", r["x_ohm_per_km"]),
            ("capacitance_per_km", "nF/km", str(r["capacitance_or_b"]).replace(" nF/km", "")),
            ("rated_current_a", "A", r["rated_current_a"]),
        ]:
            add(
                60,
                "overhead",
                param,
                value,
                unit,
                r["source_id"],
                "low",
                "benchmark_only",
                "Adapts 110 kV software standard type to 60 kV sensitivity analysis only.",
                r["conductor_type"],
            )

    for r in [r for r in cables if r["source_id"] == "SRC_PANDAPOWER_STD"]:
        for param, unit, value in [
            ("r_ohm_per_km", "ohm/km", r["r_ohm_per_km"]),
            ("x_ohm_per_km", "ohm/km", r["x_ohm_per_km"]),
            ("capacitance_per_km", "nF/km", str(r["capacitance_per_km"]).replace(" nF/km", "")),
            ("rated_current_a", "A", r["rated_current_a"]),
        ]:
            add(
                60,
                "cable",
                param,
                value,
                unit,
                r["source_id"],
                "low",
                "benchmark_only",
                "Adapts 64/110 kV software standard cable type to 60 kV sensitivity analysis only.",
                r["cable_type"],
            )

    for r in transformers:
        add(
            "60/MT",
            "transformer",
            "uk_percent",
            r["short_circuit_impedance_percent"],
            "%",
            r["source_id"],
            "high",
            "direct_portugal",
            "Need facility transformer rating and winding pair before selecting.",
            f"{r['rated_mva']} MVA lower-power winding; {r['voltage_ratio']}",
        )
    add(
        "60/MT",
        "transformer",
        "tap_range",
        "+/-16.5",
        "%",
        "SRC_EREDES_DMAC52140",
        "high",
        "direct_portugal",
        "Actual tap position/control setpoint remains missing.",
        "OLTC: 23 positions, Un +/- 11 x 1.5%, acting on primary.",
    )
    add(
        "60/MT",
        "transformer",
        "tap_step",
        1.5,
        "%",
        "SRC_EREDES_DMAC52140",
        "high",
        "direct_portugal",
        "Actual tap position/control setpoint remains missing.",
        "OLTC step size from R56.",
    )

    for parameter in ["x_ohm_per_km", "b_siemens_per_km", "rated_current_a"]:
        add(
            60,
            "overhead",
            parameter,
            "NEEDS_LOOKUP_SOURCE",
            "",
            "",
            "red",
            "missing",
            "Need E-REDES/REN standard conductor family or 60 kV overhead line design table.",
            "No direct Portugal-specific 60 kV overhead value found.",
        )
    for parameter in ["r_ohm_per_km", "x_ohm_per_km", "b_siemens_per_km"]:
        add(
            60,
            "cable",
            parameter,
            "NEEDS_MANUFACTURER_FICHA_OR_LOOKUP",
            "",
            "SRC_EREDES_DMAC33281",
            "red",
            "missing_from_public_spec",
            "E-REDES cable specification requires manufacturer to declare these values but does not publish numeric values.",
            "The source is directly relevant but incomplete for R/X/B.",
        )
    for parameter in ["transformer_r_pu", "transformer_x_pu", "tap_control_setpoint"]:
        add(
            "60/MT",
            "transformer",
            parameter,
            "NEEDS_LOOKUP_OR_OPERATOR_DATA",
            "",
            "SRC_EREDES_DMAC52140",
            "red",
            "missing_from_public_spec",
            "Need load-loss/resistance data or approved R/X split and operational voltage-control policy.",
            "uk% and tap range are available; R/X split and actual control are not.",
        )
    return rows


def gap_analysis() -> list[dict[str, Any]]:
    return [
        {
            "category": "overhead_line",
            "gap": "60 kV AT conductor family per branch",
            "current_evidence": "RND topology has overhead/cable/mixed asset type but no conductor or cross-section.",
            "impact": "Cannot select overhead R/X/B/current rows defensibly.",
            "required_action": "Request standard E-REDES 60 kV overhead conductor families or branch-level conductor/cross-section.",
            "priority": "critical",
            "status": "RED",
        },
        {
            "category": "overhead_line",
            "gap": "60 kV overhead X/B and rated current",
            "current_evidence": "E-REDES public copper conductor document gives resistance for bare copper only; no 60 kV line geometry assumptions, X/B, or ampacity.",
            "impact": "No complete overhead LUT.",
            "required_action": "Request E-REDES/REN standard 60 kV line parameter table or cite a Portuguese engineering standard.",
            "priority": "critical",
            "status": "RED",
        },
        {
            "category": "underground_cable",
            "gap": "Branch-level cable section and installation condition",
            "current_evidence": "E-REDES AT cable spec has 400/630/1000 mm2 and indicative ampacity by installation condition.",
            "impact": "Can build sensitivity bands, but cannot select one branch value.",
            "required_action": "Request cable section/installation records or define sensitivity scenarios.",
            "priority": "high",
            "status": "YELLOW",
        },
        {
            "category": "underground_cable",
            "gap": "Cable R/X/capacitance numeric values",
            "current_evidence": "E-REDES cable spec asks manufacturers to provide capacitance and reactance but does not publish filled values.",
            "impact": "Cable impedance/admittance remains missing.",
            "required_action": "Request manufacturer fichas for E-REDES-approved 36/60 kV cable products.",
            "priority": "critical",
            "status": "RED",
        },
        {
            "category": "transformer",
            "gap": "Transformer R/X split",
            "current_evidence": "E-REDES 60 kV/MT spec gives uk%, but not winding resistance or load losses by unit.",
            "impact": "Short-circuit impedance magnitude is available, but PF modelling still needs R/X split.",
            "required_action": "Request typical transformer load losses or approved R/X split by rating.",
            "priority": "high",
            "status": "YELLOW",
        },
        {
            "category": "transformer",
            "gap": "Transformer unit count and branch-to-transformer assignment",
            "current_evidence": "RARI characteristics have installed power/ratio for many substations, but not unit count.",
            "impact": "Cannot create exact parallel transformer objects.",
            "required_action": "Request typical unit counts or station transformer inventory.",
            "priority": "high",
            "status": "YELLOW",
        },
        {
            "category": "transformer",
            "gap": "Actual tap position and control mode",
            "current_evidence": "E-REDES spec gives OLTC range/step and command modes, but not operating setpoints.",
            "impact": "OPF/voltage-control model cannot be claimed.",
            "required_action": "Request public tap-control assumptions or keep taps neutral in sensitivity-only dry runs.",
            "priority": "high",
            "status": "RED",
        },
        {
            "category": "network_calibration",
            "gap": "Circuit count and circuit-km by voltage/asset type",
            "current_evidence": "Topology has route geometry and parallel candidate branches; no official circuit counts.",
            "impact": "Topology/capacity factors would be assumptions.",
            "required_action": "Request aggregate circuit-km and standard rating statistics by 60 kV overhead/cable.",
            "priority": "critical",
            "status": "RED",
        },
        {
            "category": "citation_permission",
            "gap": "Permission to cite typical ranges from operator/manufacturer",
            "current_evidence": "Public documents are citable; non-public standard ranges would need permission.",
            "impact": "Limits reproducibility and publication strength.",
            "required_action": "Ask E-REDES/REN/professor contact for citable non-confidential typical ranges.",
            "priority": "medium",
            "status": "YELLOW",
        },
    ]


def markdown_table(df: pd.DataFrame, columns: list[str], max_rows: int | None = None) -> str:
    view = df.loc[:, columns].copy()
    if max_rows is not None:
        view = view.head(max_rows)
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join(["---"] * len(columns)) + " |"
    rows = []
    for _, row in view.iterrows():
        cells = []
        for col in columns:
            value = row[col]
            if pd.isna(value):
                value = ""
            text = str(value).replace("\n", " ").replace("|", "\\|")
            cells.append(text)
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join([header, separator, *rows])


def write_request() -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    text = f"""# Request for Non-Confidential 60 kV AT Parameters

Date: {ACCESS_DATE}

Subject: Academic request for typical 60 kV AT and AT/MT transformer parameter ranges

Dear E-REDES / REN technical team,

I am working on an academic research project to build a reproducible Portuguese open-data-based distribution-grid modelling pipeline. The project does not require confidential operational models or exact asset-by-asset records. We are only seeking typical, non-confidential parameter ranges or standard values that can be cited for transparent sensitivity analysis.

Could you share, or point us to public documents containing, the following typical values?

1. 60 kV overhead lines:
- typical conductor families and cross-sections used in the RND 60 kV network;
- typical positive-sequence R, X, and B/capacitance per km;
- typical continuous current ratings or thermal rating ranges;
- typical number of circuits/conductors per route, if available as aggregate statistics.

2. 60 kV underground cables:
- standard 36/60 (72.5) kV cable types and cross-sections used in the RND;
- typical R, X, capacitance/B per km and current ratings by installation condition;
- whether the public DMA-C33-281/N values can be cited as indicative current-rating assumptions.

3. AT/MT transformers:
- typical 60/30 kV, 60/15 kV, and 60/10 kV transformer ratings;
- typical short-circuit impedance ranges and, if public, R/X split or load-loss values;
- tap range, tap step, and any public voltage-control assumptions;
- typical transformer unit count per substation class, if aggregate and non-sensitive.

4. Calibration data:
- aggregate 60 kV circuit-km by overhead/cable and voltage level;
- aggregate current-rating or thermal-capacity ranges;
- guidance on using short-circuit power/current data for public-model validation.

We would appreciate permission to cite any shared values as approximate non-confidential ranges for academic research. If exact values cannot be shared, ranges or representative standard asset classes would still be useful.

Best regards,

[Your name]
"""
    (REPORTS / "10_request_to_eredes_ren_for_parameters.md").write_text(text, encoding="utf-8")


def write_report(
    source_df: pd.DataFrame,
    overhead_df: pd.DataFrame,
    cable_df: pd.DataFrame,
    transformer_df: pd.DataFrame,
    lut_df: pd.DataFrame,
    gap_df: pd.DataFrame,
) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    high_lut = lut_df[lut_df["confidence"].astype(str).str.contains("high", case=False, na=False)]
    red_gaps = gap_df[gap_df["status"] == "RED"]
    cable_direct = cable_df[cable_df["source_id"] == "SRC_EREDES_DMAC33281"]
    transformer_direct = transformer_df[transformer_df["source_id"] == "SRC_EREDES_DMAC52140"]

    report = f"""# 10 Portuguese / European Parameter Sources

Generated: {ACCESS_DATE}

Scope: Step 3B only. This report sources candidate values and source evidence for future Portuguese 60 kV lookup tables. It does not run power flow, OPF, ML/GNN training, or assign final branch parameters.

## Executive Summary

The search found high-confidence Portuguese public sources for two important pieces of the future LUT:

- E-REDES 36/60 (72.5) kV cable specification: standard cable sections and indicative current ratings by installation condition.
- E-REDES 60 kV/MT transformer specification: rated powers, 60 kV primary, MT secondary options, short-circuit impedance uk%, and OLTC tap range/step.

It did not find a complete Portugal-specific 60 kV overhead-line LUT. Public sources still lack confirmed 60 kV AT conductor family per branch, overhead X/B/current ratings, and cable R/X/capacitance numeric values. Open-model values from pandapower are retained only as low-confidence sensitivity/benchmark candidates.

## Key Citable Sources

- [E-REDES Documentos Normativos](https://www.e-redes.pt/pt-pt/clientes-e-parceiros/profissionais/documentos-normativos)
- [E-REDES DMA-C52-140/N, transformadores trifasicos de 60 kV/MT](https://www.e-redes.pt/sites/eredes/files/2020-06/DMA-C52-140_0.pdf)
- [E-REDES DMA-C33-281/N, cabos isolados AT 36/60 (72.5) kV](https://www.e-redes.pt/sites/eredes/files/2020-05/DMA%20C33%20281.pdf)
- [E-REDES DMA-C34-110/N, cabos nus de cobre](https://www.e-redes.pt/sites/eredes/files/normative_docs/DMA-C34-110.pdf)
- [E-REDES DMA-C67-020/N, postes de aco reticulados para linhas AT 60 kV](https://www.e-redes.pt/sites/eredes/files/normative_docs/DMA-C67-020N.pdf)
- [Cabelte product portfolio](https://www.cabelte.pt/os-nossos-produtos/)
- [Efacec transformers portfolio](https://www.efacec.com/pt/negocio/transformadores/)
- [pandapower standard types](https://pandapower.readthedocs.io/en/latest/std_types/basic.html)
- [Reference paper on arXiv](https://arxiv.org/abs/2605.04289)

## Source Inventory

Total candidate sources recorded: {len(source_df)}

{markdown_table(source_df, ["source_id", "source_type", "publisher", "voltage_level", "asset_type", "values_extractable", "confidence"], max_rows=20)}

## High-Confidence Extracted Values

### E-REDES 36/60 kV Cable Current Candidates

Rows extracted: {len(cable_direct)}. These are current-rating candidates only; R/X/capacitance are not provided in the public specification.

{markdown_table(cable_direct, ["cross_section_mm2", "installation_type", "rated_current_a", "derived_thermal_limit_mva_at_60kv", "confidence"], max_rows=12)}

### E-REDES 60 kV/MT Transformer Candidates

Rows extracted: {len(transformer_direct)}. The source gives short-circuit impedance magnitude uk%, not R/X split.

{markdown_table(transformer_direct, ["rated_mva", "short_circuit_impedance_percent", "tap_range", "tap_step", "confidence"], max_rows=10)}

## Overhead Line Sources

Portugal-specific 60 kV overhead support structures were found in E-REDES DMA-C67-020/N, but this document does not provide conductor impedance or ratings. E-REDES DMA-C34-110/N provides bare copper conductor resistance values, but it is not voltage-specific and the current topology does not identify whether 60 kV AT branches use those conductors.

{markdown_table(overhead_df, ["source_id", "voltage_kv", "conductor_type", "cross_section_mm2", "r_ohm_per_km", "x_ohm_per_km", "rated_current_a", "confidence"], max_rows=15)}

## Cable Sources

The E-REDES cable specification is directly applicable to Portuguese 60 kV underground AT cable current-rating scenarios, but it explicitly leaves manufacturer-specific electrical data to proposal/ficha fields. The pandapower 64/110 kV cable rows are retained only as low-confidence software benchmark rows.

{markdown_table(cable_df, ["source_id", "voltage_kv", "cable_voltage_rating", "cross_section_mm2", "installation_type", "r_ohm_per_km", "x_ohm_per_km", "capacitance_per_km", "rated_current_a", "confidence"], max_rows=16)}

## Candidate LUT Status

The candidate LUT is not final. It separates direct Portuguese values, derived scenario values, weak proxies, benchmark-only values, and explicit missing markers.

- Candidate LUT rows: {len(lut_df)}
- High-confidence rows: {len(high_lut)}
- RED/missing gap rows: {len(red_gaps)}

{markdown_table(lut_df, ["voltage_kv", "asset_type", "parameter", "candidate_value", "unit", "source_id", "confidence", "applicability"], max_rows=25)}

## Gap Analysis

{markdown_table(gap_df, ["category", "gap", "impact", "required_action", "status"], max_rows=None)}

## Answers

1. Were Portugal-specific 60 kV overhead line parameters found?

Partially. E-REDES publishes 60 kV overhead-line support specifications and a bare-copper overhead conductor resistance table, but no complete 60 kV AT overhead R/X/B/current LUT was found. The copper resistance rows are conditional and cannot be applied to AT branches without conductor confirmation.

2. Were Portugal-specific 60 kV cable parameters found?

Yes for current-rating candidates, not for impedance/admittance. E-REDES DMA-C33-281/N is directly applicable to 36/60 (72.5) kV AT cables and gives indicative ampacities for 400, 630, and 1000 mm2 aluminium cables under specified installation conditions. It does not publish R/X/capacitance values.

3. Were E-REDES / REN / ERSE documents found that support parameterization?

E-REDES public normative documents support cable-current and transformer-uk/tap parameterization. No REN/ERSE public source with extractable 60 kV branch R/X/B/current tables was found in this Step 3B pass.

4. Were European sources found that can be adapted?

Yes, but only with low confidence for numeric values. IEC/EN standards are referenced by E-REDES documents as calculation/test standards, and pandapower provides inspectable European 50 Hz standard types. These are not Portugal-specific 60 kV LUTs.

5. Were manufacturer catalogs found with usable values?

Cabelte and Efacec public pages confirm relevant Portuguese cable/conductor and transformer product scope, but accessible pages did not expose numeric R/X/B/current or transformer impedance tables. Manufacturer fichas are still needed.

6. Were transformer impedance references found?

Yes. E-REDES DMA-C52-140/N gives direct 60 kV/MT short-circuit impedance values by lower-power winding rating: 3.15 MVA = 6.25%, 6.3 MVA = 7.15%, 10 MVA = 8.35%, 12.5 MVA = 8.35%, 20 MVA = 10.0%, 31.5 MVA = 12.5%, 40 MVA = 15.0%. R/X split remains missing.

7. Which values are high-confidence?

High-confidence: E-REDES 60 kV/MT transformer uk%, transformer rated voltage options, transformer OLTC range/step, E-REDES 36/60 kV cable sections, and E-REDES indicative cable ampacity scenarios.

8. Which values are medium-confidence?

Medium-confidence: cable thermal MVA derived from E-REDES ampacity and nominal 60 kV, provided the selected cable section and installation condition are stated. Manufacturer product-scope pages are medium-confidence evidence of availability, not numeric LUT values.

9. Which values should only be used for sensitivity analysis?

Pandapower 110 kV overhead and 64/110 kV cable standard types, and any E-REDES copper overhead resistance rows applied without confirmed conductor identity, should be sensitivity-only.

10. Can we now build a candidate 60 kV LUT?

Yes, but only as a candidate/sensitivity LUT. Transformer uk% and cable current bands can be source-backed. Complete branch R/X/B and overhead thermal ratings remain missing.

11. Can we run a parameterization dry run after this?

Yes, a non-solver dry run can be run to test data plumbing and sensitivity bands. It must mark overhead R/X/B and cable R/X/B as sourced/benchmark/missing according to row-level confidence and must not claim power-flow readiness.

12. What assumptions would still need to be stated in the paper?

Any dry run must state conductor/cable section selection, installation condition, cable season/current scenario, overhead conductor assumption, use of benchmark values, transformer R/X split method, transformer unit count, parallel-circuit handling, and tap setpoint/control assumption.

13. What should be requested from E-REDES / REN?

Request non-confidential standard 60 kV overhead conductor families and R/X/B/current ratings; approved 36/60 kV cable fichas with R/X/capacitance/current; transformer load-loss or R/X split by rating; tap-control assumptions; aggregate circuit-km and circuit-count/rating statistics.

## Final Conclusion

### GREEN

- E-REDES 60 kV/MT transformer uk% values and tap range/step are directly usable as source-backed candidates.
- E-REDES 36/60 (72.5) kV cable sections and indicative current-rating scenarios are directly usable for sensitivity bands.
- E-REDES public normative documents are citable and technically interpretable.

### YELLOW

- A candidate 60 kV LUT can now be drafted for transformer uk% and cable thermal-current scenarios, but it still needs branch-level asset selection.
- Cable thermal limits can be derived from source current ratings and 60 kV only as scenario values.
- Open-model/pandapower values can support code dry-runs and sensitivity tests, not final Portuguese parameters.

### RED

- Complete Portugal-specific 60 kV overhead R/X/B/current lookup rows are still missing.
- Public 36/60 kV cable R/X/capacitance values are still missing.
- Transformer R/X split, unit count, actual tap position/control, and circuit-count/circuit-km calibration remain unresolved.
- The model is still not ready for AC power flow, OPF, or electrical cascading simulation.

## Deliverables

- `data/processed/step3b_parameter_source_inventory.csv`
- `data/processed/step3b_overhead_line_parameter_candidates.csv`
- `data/processed/step3b_cable_parameter_candidates.csv`
- `data/processed/step3b_transformer_parameter_candidates.csv`
- `data/processed/step3b_candidate_portugal_60kv_lut.csv`
- `data/processed/step3b_parameter_gap_analysis.csv`
- `reports/10_request_to_eredes_ren_for_parameters.md`
"""
    (REPORTS / "10_portuguese_european_parameter_sources.md").write_text(report, encoding="utf-8")


def main() -> None:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)

    sources = source_inventory()
    overhead = overhead_candidates()
    cables = cable_candidates()
    transformers = transformer_candidates()
    lut = candidate_lut(overhead, cables, transformers)
    gaps = gap_analysis()
    multilingual = normalize_multilingual_candidates()
    best_available = best_available_parameter_table(multilingual)
    multilingual_gaps = multilingual_gap_analysis(best_available)
    cable_diagnostic = best_available_cable_diagnostic_table(best_available)

    source_df = write_csv(PROCESSED / "step3b_parameter_source_inventory.csv", sources)
    overhead_df = write_csv(PROCESSED / "step3b_overhead_line_parameter_candidates.csv", overhead)
    cable_df = write_csv(PROCESSED / "step3b_cable_parameter_candidates.csv", cables)
    transformer_df = write_csv(PROCESSED / "step3b_transformer_parameter_candidates.csv", transformers)
    lut_df = write_csv(PROCESSED / "step3b_candidate_portugal_60kv_lut.csv", lut)
    gap_df = write_csv(PROCESSED / "step3b_parameter_gap_analysis.csv", gaps)
    multilingual_df = write_csv(PROCESSED / "step3b_multilingual_parameter_evidence_inventory.csv", multilingual)
    best_df = write_csv(PROCESSED / "step3b_best_available_parameter_table.csv", best_available)
    multilingual_gap_df = write_csv(PROCESSED / "step3b_best_available_gap_analysis.csv", multilingual_gaps)
    cable_diagnostic_df = write_csv(PROCESSED / "step3b_best_available_cable_diagnostic_table.csv", cable_diagnostic)

    multilingual_summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evidence_rows": int(len(multilingual_df)),
        "best_available_rows": int(len(best_df)),
        "evidence_status_counts": multilingual_df["evidence_status"].value_counts(dropna=False).to_dict() if len(multilingual_df) else {},
        "selection_status_counts": best_df["selection_status"].value_counts(dropna=False).to_dict() if len(best_df) else {},
        "diagnostic_allowed_best_rows": int(best_df[best_df["selection_status"] == "BEST_AVAILABLE"]["diagnostic_allowed"].fillna(False).sum()) if len(best_df) else 0,
        "best_available_cable_diagnostic_rows": int(len(cable_diagnostic_df)),
        "status": "BEST_AVAILABLE_MULTILINGUAL_DIAGNOSTIC_ONLY",
        "publication_allowed": False,
    }
    (PROCESSED / "step3b_multilingual_parameter_evidence_summary.json").write_text(json.dumps(multilingual_summary, indent=2, ensure_ascii=False), encoding="utf-8")

    write_request()
    write_report(source_df, overhead_df, cable_df, transformer_df, lut_df, gap_df)
    write_multilingual_report(multilingual_df, best_df, multilingual_gap_df, multilingual_summary)

    print("Step 3B source inventory complete")
    print(f"sources={len(source_df)}")
    print(f"overhead_candidates={len(overhead_df)}")
    print(f"cable_candidates={len(cable_df)}")
    print(f"transformer_candidates={len(transformer_df)}")
    print(f"candidate_lut_rows={len(lut_df)}")
    print(f"gap_rows={len(gap_df)}")
    print(f"multilingual_evidence_rows={len(multilingual_df)}")
    print(f"best_available_rows={len(best_df)}")
    print(f"best_available_cable_diagnostic_rows={len(cable_diagnostic_df)}")


if __name__ == "__main__":
    main()
