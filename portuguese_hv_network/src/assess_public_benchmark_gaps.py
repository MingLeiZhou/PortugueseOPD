#!/usr/bin/env python3
"""Turn PT60 validation outputs into a release-scope gap ledger."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from common import PROJECT, read_json, utc_now, write_json


OUTPUT = PROJECT / "outputs" / "temporal_validation"


def main() -> None:
    scope = read_json(PROJECT / "config" / "public_benchmark_scope.json")
    findings = pd.read_csv(OUTPUT / "validation_findings.csv")
    capacities = pd.read_csv(OUTPUT / "substation_capacity_screen.csv", low_memory=False)
    inventory = pd.read_csv(OUTPUT / "installed_capacity_coverage.csv")
    hotspots = pd.read_csv(OUTPUT / "line_loading_hotspots.csv")
    comparisons = pd.read_csv(OUTPUT / "multi_snapshot_comparison.csv")
    continuous = pd.read_csv(OUTPUT / "continuous_24h_validation.csv")
    cross_border = pd.read_csv(OUTPUT / "cross_border_evidence.csv")

    primary_ids = set(comparisons["case_id"])
    primary_findings = findings[findings["case_id"].isin(primary_ids)]
    capacity_missing = int(capacities["potencia_instalada"].isna().sum())
    capacity_over = int(capacities["above_installed_capacity"].fillna(False).sum())
    violations = int(primary_findings.loc[primary_findings["check"].eq("generator_nameplate"), "value"].sum())
    coverage = pd.to_numeric(inventory["coverage_percent"], errors="coerce").dropna()
    overloaded_hours = continuous[continuous["lines_over_100_percent"].gt(0)].copy()
    proxy_overload_hours = int(overloaded_hours["maximum_loading_parameter_status"].astype(str).str.contains("PROXY").sum()) if "maximum_loading_parameter_status" in overloaded_hours else None
    independent_cases = int(cross_border[
        cross_border["independent_of_ren"].eq(True)
        & cross_border["availability_status"].eq("AVAILABLE")
    ]["case_id"].nunique())
    loss_error = pd.to_numeric(comparisons["loss_percent_error_vs_ren_rnt_monthly"], errors="coerce")
    september_proxy = comparisons["load_profile_mode"].astype(str).str.contains("PRIOR_YEAR").any()
    unmapped_pdirt = int(pd.to_numeric(comparisons["pdirt_reference_unmapped_pde_count"]).max())

    computed: dict[str, tuple[str, str, Any]] = {
        "eredes_capacity_join": ("RESOLVED" if capacity_missing == 0 and capacity_over == 0 else "OPEN", "Capacity screen after exact/curated code reconciliation", {"missing_rows": capacity_missing, "above_capacity_rows": capacity_over}),
        "generator_nameplate": ("RESOLVED" if violations == 0 else "OPEN", "Automated per-asset dispatch constraint", {"violation_count": violations}),
        "generator_inventory_coverage": ("RESOLVED_WITH_DISCLOSED_AGGREGATE_DIFFERENCE" if coverage.between(80, 120).all() else "OPEN", "Mapped nameplate versus REN monthly installed capacity", {"coverage_percent_min": float(coverage.min()), "coverage_percent_max": float(coverage.max())}),
        "hotspot_line_parameters": ("OPEN_PARAMETER_SENSITIVITY", "Continuous-panel overload hours and representative-case top-ten lines", {"overloaded_hours": len(overloaded_hours), "proxy_parameter_maximum_hours": proxy_overload_hours, "representative_hotspot_rows": len(hotspots)}),
        "storage_spatial_allocation": ("BOUNDED_PROXY", "Exact national accounting; capacity-weighted public asset allocation", "No unit telemetry"),
        "rnt_loss_scope": ("QUANTIFIED_GAP", "RNT lines plus transformers versus REN monthly RNT balance", {"error_percentage_points_min": float(loss_error.min()), "error_percentage_points_max": float(loss_error.max())}),
        "independent_cross_border_total": ("RESOLVED" if independent_cases >= len(primary_ids) else "PENDING_TOKEN_OR_PUBLIC_EXPORT", "REE/ENTSO-E independent evidence", {"covered_primary_cases": independent_cases, "required_cases": len(primary_ids)}),
        "september_synchronous_load": ("PROXY_EXPLICITLY_LABELED" if september_proxy else "RESOLVED", "Scenario load_profile_mode", {"prior_year_proxy_present": bool(september_proxy)}),
        "pdirt_unmapped_deliveries": ("BOUNDED_RESIDUAL", "PDIRT Annex 12 deterministic name mapping", {"unmapped_pde_per_season": unmapped_pdirt}),
    }
    rows: list[dict[str, Any]] = []
    for item in scope["items"]:
        status, evidence, metric = computed.get(
            item["id"],
            ("EXPLICITLY_EXCLUDED", item["criterion"], "Unavailable operator-only telemetry"),
        )
        rows.append({**item, "status": status, "evidence": evidence, "metric": json.dumps(metric, ensure_ascii=False) if not isinstance(metric, str) else metric})
    frame = pd.DataFrame(rows)
    frame.to_csv(OUTPUT / "public_benchmark_gap_status.csv", index=False)
    payload = {
        "generated_at": utc_now(),
        "benchmark_target": scope["benchmark_target"],
        "not_a_claim": scope["not_a_claim"],
        "release_blockers": frame[
            frame["release_role"].eq("REQUIRED")
            & ~frame["status"].astype(str).str.startswith("RESOLVED")
        ]["id"].tolist(),
        "items": rows,
    }
    write_json(OUTPUT / "public_benchmark_gap_status.json", payload)
    print(frame[["id", "category", "release_role", "status"]].to_string(index=False))


if __name__ == "__main__":
    main()
