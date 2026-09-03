#!/usr/bin/env python3
"""Write an auditable before/after summary for the corrected 60 kV corridors."""
from __future__ import annotations

import pandas as pd

from common import POWER_FLOW, PROJECT, TABLES, VALIDATION, ensure_dirs, read_json


TARGETS = {
    "0101L5124200": {"corridor": "Águeda–Barrô", "expected_parallel": 3, "expected_max_i_ka": 0.489},
    "1317L5113200": {"corridor": "Vilar do Paraíso–Pedroso", "expected_parallel": 4, "expected_max_i_ka": 0.384},
    "1805L5119300": {"corridor": "Ribabelide–Valdigem", "expected_parallel": 2, "expected_max_i_ka": 0.934},
}


def main() -> None:
    ensure_dirs()
    config = read_json(PROJECT / "config" / "model_config.json")
    seasonal_factor = float(config.get("seasonal_rating_factor", 1.0)) if config.get("seasonal_rating_mode") == "WINTER" else 1.0
    overrides = pd.read_csv(TABLES / "corridor_rating_overrides.csv")
    results = pd.read_csv(POWER_FLOW / "line_operating_results.csv", low_memory=False)
    rows: list[dict[str, object]] = []
    for name, expected in TARGETS.items():
        selected = results[results["name"].astype(str).eq(name)]
        if selected.empty:
            raise RuntimeError(f"Missing corrected corridor rows for {name}")
        override = overrides[overrides["line_name"].astype(str).eq(name)]
        for _, row in selected.iterrows():
            expected_current = expected["expected_max_i_ka"] * seasonal_factor
            rows.append({
                "corridor": expected["corridor"], "line_id": row["line_id"], "line_name": name,
                "previous_parallel": int(override.loc[override.line_id.eq(row.line_id), "previous_parallel"].iloc[0]),
                "released_parallel": int(row["parallel"]), "previous_max_i_ka": float(override.loc[override.line_id.eq(row.line_id), "previous_max_i_ka"].iloc[0]),
                "released_max_i_ka": float(row["max_i_ka"]), "model_loading_percent": float(row["loading_percent"]),
                "expected_parallel": expected["expected_parallel"], "expected_max_i_ka": expected_current,
                "status": "PASS" if int(row["parallel"]) == expected["expected_parallel"] and abs(float(row["max_i_ka"]) - expected_current) < 1e-4 else "FAIL",
            })
    output = pd.DataFrame(rows)
    output.to_csv(VALIDATION / "corridor_rating_correction_results.csv", index=False)
    print(output.to_string(index=False))


if __name__ == "__main__":
    main()
