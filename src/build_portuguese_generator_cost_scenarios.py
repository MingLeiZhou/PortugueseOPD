"""Build first diagnostic generator cost scenario table for DC OPF scaffolding."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DISPATCH = ROOT / "data" / "processed" / "generator_dispatch_proxies"
OUT = ROOT / "data" / "processed" / "generator_costs"
REPORTS = ROOT / "reports"


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def classify_cost(row: pd.Series) -> tuple[str, float, bool, bool, str]:
    dispatch_class = str(row.get("dispatch_proxy_class", ""))
    facility_type = str(row.get("facility_type", ""))
    name = str(row.get("installation_name", "")).upper()

    if dispatch_class == "import_interface_proxy" or facility_type == "PC_AT":
        return ("import_interface", 95.0, False, True, "Diagnostic import/interface penalty cost.")

    if any(token in name for token in ["CENTRAL", "TEJO"]):
        return ("thermal_proxy", 75.0, False, True, "Diagnostic thermal proxy cost for a station explicitly labeled CENTRAL/TEJO.")

    if any(token in name for token in ["AEROPORTO", "EXPO", "PARQUE", "LUMIAR", "ALTO", "NORTE", "BOAVISTA", "CANEÇAS"]):
        return ("urban_injection_proxy", 110.0, False, True, "Diagnostic expensive urban injection proxy; high cost discourages use unless needed.")

    return ("generic_dispatch_proxy", 85.0, False, True, "Diagnostic generic dispatch proxy cost for connected capacity row.")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)

    proxies = read_csv(DISPATCH / "pt_generator_dispatch_proxy_table.csv")
    usable = proxies[proxies["dispatch_proxy_class"].isin(["dispatchable_proxy", "import_interface_proxy"])].copy() if len(proxies) else pd.DataFrame()

    rows = []
    for _, row in usable.iterrows():
        cost_class, marginal_cost, must_run, curtailable, note = classify_cost(row)
        rows.append(
            {
                "candidate_id": row.get("candidate_id"),
                "assigned_bus_index": row.get("assigned_bus_index"),
                "assigned_bus_name": row.get("assigned_bus_name"),
                "installation_code": row.get("installation_code"),
                "installation_name": row.get("installation_name"),
                "dispatch_proxy_class": row.get("dispatch_proxy_class"),
                "cost_class": cost_class,
                "pmax_mw_proxy": row.get("pmax_mw_proxy"),
                "pmin_mw_proxy": row.get("pmin_mw_proxy"),
                "marginal_cost_eur_per_mwh": marginal_cost,
                "must_run": must_run,
                "curtailable": curtailable,
                "cost_status": "DIAGNOSTIC_COST_ASSUMED",
                "publication_allowed": False,
                "notes": note,
            }
        )

    df = pd.DataFrame(rows)
    df.to_csv(OUT / "pt_generator_cost_scenarios.csv", index=False)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "row_count": int(len(df)),
        "cost_class_counts": df["cost_class"].value_counts().to_dict() if len(df) else {},
        "publication_allowed": False,
        "status": "DIAGNOSTIC_COST_LAYER_ONLY",
    }
    (OUT / "pt_generator_cost_scenarios_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    def markdown_table(frame: pd.DataFrame, max_rows: int = 80) -> str:
        if frame.empty:
            return "_No rows._\n"
        view = frame.head(max_rows)
        cols = list(view.columns)
        lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
        for _, r in view.iterrows():
            lines.append("| " + " | ".join(str(r.get(c, "")).replace("|", "\\|") for c in cols) + " |")
        return "\n".join(lines) + "\n"

    text = [
        "# 49 Portuguese Generator Cost Scenarios",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "Scope: first diagnostic-only marginal cost layer for dispatchable and import/interface proxy rows. This is not a publication-grade generation cost model.",
        "",
        "## Summary",
        "",
        markdown_table(pd.DataFrame([summary]).drop(columns=["generated_at"])),
        "",
        "## Cost Scenario Rows",
        "",
        markdown_table(df, 120),
        "",
        "## Interpretation",
        "",
        "This cost layer is intentionally simple and diagnostic-only. It is sufficient to unblock a first DC OPF baseline on the backbone core, but every generator class and marginal cost remains scenario-assumed.",
    ]
    (REPORTS / "49_portuguese_generator_cost_scenarios.md").write_text("\n".join(text), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
