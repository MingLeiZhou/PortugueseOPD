"""Build initial Portuguese generator candidate layer for future DC OPF diagnostics."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed" / "generator_candidates"
REPORTS = ROOT / "reports"


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, sep=";", encoding="utf-8") if path.exists() else pd.DataFrame()


def normalize_capacity_row(row: pd.Series, dataset_id: str, field: str, generation_type: str) -> dict[str, Any]:
    installation_code = str(row.get("codigo_da_instalacao", "") or row.get("codigo", ""))
    name = str(row.get("nome", "") or row.get("instalacao", ""))
    district = str(row.get("distrito", ""))
    municipality = str(row.get("concelho", ""))
    mva = pd.to_numeric(pd.Series([row.get(field)]), errors="coerce").iloc[0]
    return {
        "candidate_id": f"GENCAND_{dataset_id}_{installation_code}_{field}",
        "source_dataset": dataset_id,
        "installation_code": installation_code,
        "installation_name": name,
        "district": district,
        "municipality": municipality,
        "generation_type": generation_type,
        "capacity_mva_or_mw": float(mva) if pd.notna(mva) else "",
        "capacity_field": field,
        "bus_assignment_status": "UNASSIGNED",
        "cost_status": "MISSING",
        "publication_allowed": False,
        "notes": "Candidate built from RND reception/capacity-style public data; not yet a confirmed generator dispatch row.",
    }


def rows() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    cap = read_csv(RAW / "capacidade-rececao-rnd.csv")
    for field in [
        "potencia_de_ligacao_ligado_mva_rari",
        "potencia_de_ligacao_comprometido_mva_rari",
        "potencia_de_ligacao_em_confirmacao_mva_rari",
    ]:
        if field in cap.columns:
            subset = cap[pd.to_numeric(cap[field], errors="coerce").fillna(0) > 0].copy()
            for _, row in subset.iterrows():
                out.append(normalize_capacity_row(row, "capacidade-rececao-rnd", field, "grid_connection_capacity_proxy"))

    carga = read_csv(RAW / "carga-na-subestacao.csv")
    if len(carga):
        subset = carga[[c for c in ["codigo_da_instalacao", "nome", "distrito", "potencia_instalada"] if c in carga.columns]].copy()
        if "potencia_instalada" in subset.columns:
            subset = subset[pd.to_numeric(subset["potencia_instalada"], errors="coerce").fillna(0) > 0]
            for _, row in subset.iterrows():
                out.append(
                    {
                        "candidate_id": f"GENCAND_carga-na-subestacao_{row.get('codigo_da_instalacao','')}_installed_power",
                        "source_dataset": "carga-na-subestacao",
                        "installation_code": str(row.get("codigo_da_instalacao", "")),
                        "installation_name": str(row.get("nome", "")),
                        "district": str(row.get("distrito", "")),
                        "municipality": "",
                        "generation_type": "substation_installed_power_context_only",
                        "capacity_mva_or_mw": float(pd.to_numeric(pd.Series([row.get("potencia_instalada")]), errors="coerce").iloc[0]),
                        "capacity_field": "potencia_instalada",
                        "bus_assignment_status": "UNASSIGNED",
                        "cost_status": "MISSING",
                        "publication_allowed": False,
                        "notes": "Installed power from substation load/short-circuit context; useful as system capacity context but not a confirmed generation asset.",
                    }
                )

    # Deduplicate by source/install/field/capacity.
    dedup = pd.DataFrame(out)
    if dedup.empty:
        return []
    dedup = dedup.drop_duplicates(subset=["source_dataset", "installation_code", "capacity_field", "capacity_mva_or_mw"])
    return dedup.to_dict(orient="records")


def markdown_table(df: pd.DataFrame, max_rows: int = 40) -> str:
    if df.empty:
        return "_No rows._\n"
    view = df.head(max_rows)
    cols = list(view.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in view.iterrows():
        lines.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|") for c in cols) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows())
    df.to_csv(PROCESSED / "pt_generator_candidates.csv", index=False)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidate_count": int(len(df)),
        "source_dataset_counts": df["source_dataset"].value_counts().to_dict() if len(df) else {},
        "bus_assignment_ready_count": int((df.get("bus_assignment_status", pd.Series(dtype=str)) == "READY").sum()) if len(df) else 0,
        "cost_ready_count": int((df.get("cost_status", pd.Series(dtype=str)) == "READY").sum()) if len(df) else 0,
        "publication_allowed": False,
        "status": "DIAGNOSTIC_CANDIDATE_LAYER_ONLY",
    }
    (PROCESSED / "pt_generator_candidates_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    text = [
        "# 46 Portuguese Generator Candidate Layer",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "Scope: first generator/capacity candidate layer for future DC OPF diagnostics. These rows are not yet assigned to buses and do not yet form an OPF-ready generator table.",
        "",
        "## Summary",
        "",
        markdown_table(pd.DataFrame([summary]).drop(columns=["generated_at"])),
        "",
        "## Example Candidate Rows",
        "",
        markdown_table(df, 40),
        "",
        "## Interpretation",
        "",
        "This first pass identifies public capacity-style candidates from E-REDES RND-facing datasets, but they are not confirmed dispatchable generators. The next required step is bus assignment and source classification into actual generator, import, or capacity-proxy categories before any DC OPF can be considered meaningful.",
    ]
    (REPORTS / "46_portuguese_generator_candidate_layer.md").write_text("\n".join(text), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
