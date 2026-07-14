"""Run endpoint-name negative controls for PT60 public-source matching.

The control keeps each retained branch geometry fixed but replaces endpoint
names with endpoint names from another retained branch. This tests whether
name-based OSM/OpenInfraMap evidence declines when endpoint identity is broken.
It is a matcher-selectivity check, not a topology-accuracy estimate.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

import config
from cross_validate_pt60_topology_external_sources import (
    build_osm_evidence,
    cross_validate,
)
from utils import utc_now, write_json, write_text


OUT = config.PROCESSED_DIR / "topology_validation"
BRANCHES = config.PROCESSED_DIR / "at_interfacility_candidate_branches.csv"
OSM_CACHE = OUT / "pt_osm_openinframap_60kv_power_ways.json"
REAL_MATCHES = OUT / "pt_topology_cross_validation_osm_matches.csv"
CONTROL_MATCHES = OUT / "matcher_negative_control_names.csv"
SUMMARY_JSON = OUT / "matcher_negative_control_names_summary.json"
REPORT = config.REPORTS_DIR / "103_pt60_endpoint_name_negative_control.md"

DEFAULT_SEED = 20260714
STRONG_NAME_STATUSES = {"OSM_NAME_OPERATOR_STRONG", "OSM_NAME_STRONG"}
PARTIAL_NAME_STATUSES = {"OSM_PARTIAL_NAME_NEARBY"}


def status_summary(matches: pd.DataFrame) -> dict[str, object]:
    counts = matches["external_evidence_status"].value_counts().sort_index()
    total = int(len(matches))
    strong_name = int(matches["external_evidence_status"].isin(STRONG_NAME_STATUSES).sum())
    partial_name = int(matches["external_evidence_status"].isin(PARTIAL_NAME_STATUSES).sum())
    any_name = strong_name + partial_name
    return {
        "branches": total,
        "status_counts": {str(k): int(v) for k, v in counts.items()},
        "strong_name_evidence_branches": strong_name,
        "partial_name_nearby_branches": partial_name,
        "any_name_evidence_branches": any_name,
        "strong_name_evidence_rate": strong_name / total if total else None,
        "any_name_evidence_rate": any_name / total if total else None,
    }


def assign_length_strata(branches: pd.DataFrame) -> pd.Series:
    lengths = pd.to_numeric(branches["total_length_km"], errors="coerce")
    ranked = lengths.rank(method="first")
    return pd.qcut(ranked, q=4, labels=["q1_short", "q2", "q3", "q4_long"])


def shifted_indices(group: pd.DataFrame, seed: int) -> list[int]:
    ordered = group.sort_values(["total_length_km", "branch_id"]).index.tolist()
    if len(ordered) < 2:
        return ordered
    offset = seed % (len(ordered) - 1) + 1
    return ordered[offset:] + ordered[:offset]


def corrupt_endpoint_names(branches: pd.DataFrame, seed: int) -> pd.DataFrame:
    controlled = branches.copy()
    controlled["negative_control_seed"] = seed
    controlled["negative_control_type"] = "length_stratified_endpoint_pair_shift"
    controlled["length_stratum"] = assign_length_strata(controlled).astype(str)
    controlled["original_from_facility_name"] = controlled["from_facility_name"]
    controlled["original_to_facility_name"] = controlled["to_facility_name"]
    controlled["original_from_facility_code"] = controlled["from_facility_code"]
    controlled["original_to_facility_code"] = controlled["to_facility_code"]
    controlled["permuted_source_branch_id"] = ""

    for _, group in controlled.groupby("length_stratum", sort=True, observed=False):
        shifted = shifted_indices(group, seed)
        for target_index, source_index in zip(group.sort_values(["total_length_km", "branch_id"]).index.tolist(), shifted):
            source = controlled.loc[source_index]
            controlled.at[target_index, "from_facility_name"] = source["from_facility_name"]
            controlled.at[target_index, "to_facility_name"] = source["to_facility_name"]
            controlled.at[target_index, "from_facility_code"] = source["from_facility_code"]
            controlled.at[target_index, "to_facility_code"] = source["to_facility_code"]
            controlled.at[target_index, "permuted_source_branch_id"] = source["branch_id"]

    unchanged = controlled["branch_id"].eq(controlled["permuted_source_branch_id"])
    if bool(unchanged.any()):
        raise RuntimeError(
            "Endpoint-name negative control failed: at least one branch kept its own endpoint names."
        )
    return controlled


def load_osm_from_cache(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise RuntimeError(f"Missing OSM/OpenInfraMap cache: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return build_osm_evidence(data)


def write_report(summary: dict[str, object]) -> None:
    real_counts = summary["real"]["status_counts"]
    control_counts = summary["negative_control"]["status_counts"]
    statuses = sorted(set(real_counts) | set(control_counts))
    lines = [
        "# 103 PT60 Endpoint-Name Negative Control",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "## Purpose",
        "",
        "This negative control keeps retained branch geometries fixed and replaces endpoint names with endpoint pairs from other retained branches in the same length stratum. The test checks whether name-based OSM/OpenInfraMap evidence declines when endpoint identity is deliberately broken.",
        "",
        "## Design",
        "",
        f"- branches tested: {summary['branches_tested']}",
        f"- seed: {summary['seed']}",
        "- corruption: deterministic length-stratified cyclic shift of endpoint-name pairs",
        "- geometry, voltage, status, length and branch identifiers are retained",
        "- interpretation: matcher selectivity only; this is not an accuracy, precision or recall estimate",
        "",
        "## Results",
        "",
        f"- real strong-name evidence: {summary['real']['strong_name_evidence_branches']} / {summary['branches_tested']} ({summary['real']['strong_name_evidence_rate']:.4f})",
        f"- corrupted-name strong-name evidence: {summary['negative_control']['strong_name_evidence_branches']} / {summary['branches_tested']} ({summary['negative_control']['strong_name_evidence_rate']:.4f})",
        f"- absolute strong-name rate drop: {summary['strong_name_rate_drop']:.4f}",
        f"- relative strong-name reduction: {summary['strong_name_relative_reduction']:.4f}",
        "",
        "| external_evidence_status | real branches | corrupted-name branches |",
        "|---|---:|---:|",
    ]
    for status in statuses:
        lines.append(f"| {status} | {int(real_counts.get(status, 0))} | {int(control_counts.get(status, 0))} |")
    lines.extend(
        [
            "",
            "## Paper Interpretation",
            "",
            "A decline in strong-name evidence after endpoint-name corruption supports using endpoint names as a non-trivial public-source concordance signal. Remaining geometry-based matches should be described as corridor concordance, not branch truth.",
        ]
    )
    write_text(REPORT, "\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()

    if not BRANCHES.exists():
        raise RuntimeError(f"Missing retained branch table: {BRANCHES}")
    OUT.mkdir(parents=True, exist_ok=True)
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    branches = pd.read_csv(BRANCHES)
    osm = load_osm_from_cache(OSM_CACHE)

    if REAL_MATCHES.exists():
        real_matches = pd.read_csv(REAL_MATCHES)
    else:
        real_matches = cross_validate(branches, osm)

    controlled_branches = corrupt_endpoint_names(branches, args.seed)
    control_matches = cross_validate(controlled_branches, osm)

    identity_cols = [
        "branch_id",
        "negative_control_seed",
        "negative_control_type",
        "length_stratum",
        "permuted_source_branch_id",
        "original_from_facility_code",
        "original_from_facility_name",
        "original_to_facility_code",
        "original_to_facility_name",
    ]
    control_matches = control_matches.merge(
        controlled_branches[identity_cols],
        on="branch_id",
        how="left",
        validate="one_to_one",
    )
    control_matches.to_csv(CONTROL_MATCHES, index=False)

    real = status_summary(real_matches)
    control = status_summary(control_matches)
    real_rate = float(real["strong_name_evidence_rate"] or 0.0)
    control_rate = float(control["strong_name_evidence_rate"] or 0.0)
    summary = {
        "generated_at": utc_now(),
        "seed": args.seed,
        "branches_tested": int(len(branches)),
        "control_design": "length_stratified_endpoint_pair_shift",
        "preserved_fields": [
            "branch_id",
            "geometry",
            "voltage",
            "status",
            "total_length_km",
            "confidence_score",
            "number_of_original_segments",
        ],
        "corrupted_fields": [
            "from_facility_name",
            "to_facility_name",
            "from_facility_code",
            "to_facility_code"
        ],
        "real": real,
        "negative_control": control,
        "strong_name_rate_drop": real_rate - control_rate,
        "strong_name_relative_reduction": (real_rate - control_rate) / real_rate if real_rate else None,
        "outputs": {
            "negative_control_matches": str(CONTROL_MATCHES.relative_to(config.ROOT_DIR)),
            "summary": str(SUMMARY_JSON.relative_to(config.ROOT_DIR)),
            "report": str(REPORT.relative_to(config.ROOT_DIR)),
        },
        "claim_boundary": "This negative control tests name-evidence selectivity only. It does not estimate topology precision, recall, operator validation, or real-grid completeness.",
    }
    write_json(SUMMARY_JSON, summary)
    write_report(summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
