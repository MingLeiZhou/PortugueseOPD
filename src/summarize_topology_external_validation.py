"""Adjudicate external topology reviews and estimate candidate precision."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

import config
from utils import utc_now, write_json, write_text


OUT = config.PROCESSED_DIR / "topology_validation"
REPORTS = config.REPORTS_DIR
SAMPLE = OUT / "pt_topology_validation_sample.csv"
REVIEWS = OUT / "pt_topology_validation_reviews.csv"
VALID_LABELS = {"CONFIRMED", "REJECTED", "UNCERTAIN", "ABSTAIN"}
MIN_ADJUDICATED = 50


def wilson(successes: float, total: float, z: float = 1.959963984540054) -> tuple[float, float]:
    if total <= 0:
        return math.nan, math.nan
    proportion = successes / total
    denominator = 1.0 + z * z / total
    centre = (proportion + z * z / (2.0 * total)) / denominator
    margin = z * math.sqrt(proportion * (1.0 - proportion) / total + z * z / (4.0 * total * total)) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def nonempty(value: object) -> bool:
    return not pd.isna(value) and bool(str(value).strip())


def main() -> None:
    if not SAMPLE.exists() or not REVIEWS.exists():
        raise RuntimeError("Fail-closed: build the topology validation sample before summarizing reviews.")
    sample = pd.read_csv(SAMPLE)
    reviews = pd.read_csv(REVIEWS, keep_default_na=False)
    reviews["review_label"] = reviews["review_label"].astype(str).str.strip().str.upper()
    invalid = reviews[(reviews["review_label"] != "") & ~reviews["review_label"].isin(VALID_LABELS)]
    if len(invalid):
        raise RuntimeError(f"Fail-closed: {len(invalid)} review rows contain invalid labels.")

    evidence_complete = reviews.apply(
        lambda row: row["review_label"] not in {"CONFIRMED", "REJECTED"}
        or (nonempty(row.get("reviewer_id")) and nonempty(row.get("evidence_reference")) and nonempty(row.get("evidence_access_date"))),
        axis=1,
    )
    reviews["evidence_complete"] = evidence_complete

    adjudicated_rows: list[dict[str, object]] = []
    for branch_id, group in reviews.groupby("branch_id", sort=True):
        definitive = group[group["review_label"].isin({"CONFIRMED", "REJECTED"}) & group["evidence_complete"]]
        labels = definitive["review_label"].tolist()
        reviewer_count = definitive["reviewer_id"].astype(str).nunique()
        if reviewer_count >= 2 and len(set(labels)) == 1:
            status = labels[0]
        elif reviewer_count >= 2 and len(set(labels)) > 1:
            status = "DISAGREEMENT"
        else:
            status = "UNRESOLVED"
        adjudicated_rows.append(
            {
                "branch_id": branch_id,
                "adjudication_status": status,
                "definitive_review_rows": int(len(definitive)),
                "independent_reviewers": int(reviewer_count),
                "evidence_complete_rows": int(group["evidence_complete"].sum()),
            }
        )
    adjudicated = pd.DataFrame(adjudicated_rows).merge(
        sample[["branch_id", "validation_stratum", "sampling_weight"]],
        on="branch_id",
        how="right",
        validate="one_to_one",
    )
    definitive = adjudicated[adjudicated["adjudication_status"].isin({"CONFIRMED", "REJECTED"})].copy()
    confirmed = definitive["adjudication_status"].eq("CONFIRMED")
    weighted_total = float(definitive["sampling_weight"].sum())
    weighted_confirmed = float(definitive.loc[confirmed, "sampling_weight"].sum())
    weighted_precision = weighted_confirmed / weighted_total if weighted_total else math.nan
    low, high = wilson(int(confirmed.sum()), len(definitive))
    status = "EVALUABLE" if len(definitive) >= MIN_ADJUDICATED else "NOT_EVALUABLE"
    precision_claim_allowed = status == "EVALUABLE" and int(adjudicated["adjudication_status"].eq("DISAGREEMENT").sum()) == 0

    metrics = {
        "generated_at": utc_now(),
        "status": status,
        "sample_branches": int(len(sample)),
        "adjudicated_branches": int(len(definitive)),
        "confirmed_branches": int(confirmed.sum()),
        "rejected_branches": int((~confirmed).sum()) if len(definitive) else 0,
        "disagreements": int(adjudicated["adjudication_status"].eq("DISAGREEMENT").sum()),
        "unresolved": int(adjudicated["adjudication_status"].eq("UNRESOLVED").sum()),
        "unweighted_precision": float(confirmed.mean()) if len(definitive) else None,
        "weighted_precision": weighted_precision if weighted_total else None,
        "wilson_95_low_unweighted": low if not math.isnan(low) else None,
        "wilson_95_high_unweighted": high if not math.isnan(high) else None,
        "minimum_adjudicated_required": MIN_ADJUDICATED,
        "precision_claim_allowed": precision_claim_allowed,
        "recall_estimable": False,
        "recall_reason": "The sample contains retained candidates only; rejected circuits need a separate sample to estimate recall.",
    }
    adjudicated.to_csv(OUT / "pt_topology_validation_adjudication.csv", index=False)
    write_json(OUT / "pt_topology_validation_metrics.json", metrics)
    text = [
        "# 98 External Topology Validation Status",
        "",
        f"Generated: {metrics['generated_at']}",
        "",
        f"Status: `{status}`",
        "",
        f"- adjudicated branches: {metrics['adjudicated_branches']} / {metrics['sample_branches']}",
        f"- confirmed: {metrics['confirmed_branches']}",
        f"- rejected: {metrics['rejected_branches']}",
        f"- disagreements: {metrics['disagreements']}",
        f"- unresolved: {metrics['unresolved']}",
        f"- weighted precision: {metrics['weighted_precision']}",
        f"- unweighted Wilson 95% interval: [{metrics['wilson_95_low_unweighted']}, {metrics['wilson_95_high_unweighted']}]",
        "",
        "Recall is not estimated because this review sample contains retained candidates only. A separate sample of downgraded/rejected circuits is required.",
    ]
    write_text(REPORTS / "98_topology_external_validation_status.md", "\n".join(text) + "\n")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
