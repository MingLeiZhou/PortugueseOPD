"""Assign initial Portuguese generator candidates to buses, prioritizing the S16 backbone."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "data" / "processed" / "generator_candidates"
BACKBONE = ROOT / "data" / "processed" / "acpf_s16_backbone_core_depth6"
RAW = ROOT / "data" / "raw"
REPORTS = ROOT / "reports"
OUT = ROOT / "data" / "processed" / "generator_assignment"


def read_csv(path: Path, sep: str = ",") -> pd.DataFrame:
    return pd.read_csv(path, sep=sep) if path.exists() else pd.DataFrame()


def coords_split(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    text = series.fillna("").astype(str).str.split(",", n=1, expand=True)
    lat = pd.to_numeric(text[0].str.strip(), errors="coerce") if 0 in text.columns else pd.Series(dtype=float)
    lon = pd.to_numeric(text[1].str.strip(), errors="coerce") if 1 in text.columns else pd.Series(dtype=float)
    return lat, lon


def build_lookup() -> pd.DataFrame:
    se = read_csv(RAW / "se-at_2025.csv", sep=";")
    pc = read_csv(RAW / "pc-at_2025.csv", sep=";")
    frames = []
    for df, facility_type in [(se, "SE_AT"), (pc, "PC_AT")]:
        if df.empty:
            continue
        lat, lon = coords_split(df.get("coordenadas", pd.Series(dtype=str)))
        tmp = pd.DataFrame(
            {
                "installation_code": df.get("codigo", pd.Series(dtype=str)).astype(str),
                "installation_name": df.get("instalacao", pd.Series(dtype=str)).astype(str),
                "district": df.get("distrito", pd.Series(dtype=str)).astype(str),
                "municipality": df.get("concelho", pd.Series(dtype=str)).astype(str),
                "nut3": df.get("nut3", pd.Series(dtype=str)).astype(str),
                "lat": lat,
                "lon": lon,
                "facility_type": facility_type,
            }
        )
        frames.append(tmp)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def assign() -> tuple[pd.DataFrame, dict[str, Any]]:
    candidates = read_csv(GEN / "pt_generator_candidates.csv")
    buses = read_csv(BACKBONE / "s16_backbone_buses.csv")
    lookup = build_lookup()
    if candidates.empty or buses.empty:
        return pd.DataFrame(), {"candidate_count": int(len(candidates)), "assigned_count": 0, "backbone_assigned_count": 0}

    bus_map = buses.copy()
    bus_map["installation_code"] = bus_map["name"].astype(str).str.replace(r"_(60|30|15|10)$", "", regex=True)
    bus_map["bus_voltage_kv"] = pd.to_numeric(bus_map["vn_kv"], errors="coerce")

    merged = candidates.merge(lookup, on=["installation_code", "district", "municipality"], how="left", suffixes=("", "_lookup"))
    merged = merged.merge(bus_map[["bus_index", "name", "installation_code", "bus_voltage_kv"]], on="installation_code", how="left", suffixes=("", "_bus"))

    rows = []
    for _, row in merged.iterrows():
        assigned = pd.notna(row.get("bus_index"))
        rows.append(
            {
                "candidate_id": row.get("candidate_id"),
                "source_dataset": row.get("source_dataset"),
                "installation_code": row.get("installation_code"),
                "installation_name": row.get("installation_name"),
                "district": row.get("district"),
                "municipality": row.get("municipality"),
                "generation_type": row.get("generation_type"),
                "capacity_mva_or_mw": row.get("capacity_mva_or_mw"),
                "capacity_field": row.get("capacity_field"),
                "facility_type": row.get("facility_type", ""),
                "lat": row.get("lat", ""),
                "lon": row.get("lon", ""),
                "assigned_bus_index": int(row.get("bus_index")) if assigned else "",
                "assigned_bus_name": row.get("name", "") if assigned else "",
                "assigned_bus_voltage_kv": row.get("bus_voltage_kv", "") if assigned else "",
                "assignment_status": "ASSIGNED_TO_BACKBONE_BUS" if assigned else "UNASSIGNED_NOT_IN_BACKBONE",
                "assignment_confidence": "high_installation_code_match" if assigned else "missing_backbone_match",
                "publication_allowed": False,
                "notes": "Installation-code-based assignment to S16 backbone candidate bus." if assigned else "No matching installation code in current S16 backbone core.",
            }
        )

    out = pd.DataFrame(rows)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "candidate_count": int(len(out)),
        "assigned_count": int((out["assignment_status"] == "ASSIGNED_TO_BACKBONE_BUS").sum()),
        "backbone_assigned_count": int((out["assignment_status"] == "ASSIGNED_TO_BACKBONE_BUS").sum()),
        "unassigned_count": int((out["assignment_status"] != "ASSIGNED_TO_BACKBONE_BUS").sum()),
        "source_dataset_counts": out["source_dataset"].value_counts().to_dict() if len(out) else {},
        "publication_allowed": False,
        "status": "BACKBONE_ASSIGNMENT_ONLY",
    }
    return out, summary


def markdown_table(df: pd.DataFrame, max_rows: int = 60) -> str:
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
    assignment, summary = assign()
    assignment.to_csv(OUT / "pt_generator_bus_assignment.csv", index=False)
    (OUT / "pt_generator_bus_assignment_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    assigned = assignment[assignment["assignment_status"] == "ASSIGNED_TO_BACKBONE_BUS"].copy() if len(assignment) else pd.DataFrame()
    text = [
        "# 47 Portuguese Generator Bus Assignment",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "Scope: first installation-code-based bus assignment of generator/capacity candidates to the S16 backbone core. This is not yet an OPF-ready generator layer.",
        "",
        "## Summary",
        "",
        markdown_table(pd.DataFrame([summary]).drop(columns=["generated_at"])),
        "",
        "## Assigned Candidates",
        "",
        markdown_table(assigned, 80),
        "",
        "## Interpretation",
        "",
        "This pass only maps installation-code-based candidates onto backbone buses. The next step is semantic classification: decide which assigned rows are actual dispatchable generator proxies, which are import/slack candidates, and which are only network capacity context.",
    ]
    (REPORTS / "47_portuguese_generator_bus_assignment.md").write_text("\n".join(text), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
