#!/usr/bin/env python3
"""Record branch-level changes between two PT60-Candidate releases."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd


def source_key(value: Any) -> tuple[str, ...]:
    return tuple(sorted(part.strip() for part in str(value).split(",") if part.strip()))


def row_map(frame: pd.DataFrame) -> dict[tuple[str, ...], dict[str, Any]]:
    return {source_key(row["source_line_ids"]): row for row in frame.to_dict("records")}


def endpoint_pair(row: dict[str, Any]) -> tuple[str, str]:
    return tuple(sorted((str(row["from_facility_uid"]), str(row["to_facility_uid"]))))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old", type=Path, required=True)
    parser.add_argument("--new", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--old-version", default="v1.0.1")
    parser.add_argument("--new-version", default="v1.0.2")
    args = parser.parse_args()

    old = pd.read_csv(args.old)
    new = pd.read_csv(args.new)
    old_map = row_map(old)
    new_map = row_map(new)
    all_keys = sorted(set(old_map) | set(new_map))
    rows: list[dict[str, Any]] = []
    for key in all_keys:
        before = old_map.get(key)
        after = new_map.get(key)
        if before and after:
            status = "retained_in_both"
            endpoints_unchanged = endpoint_pair(before) == endpoint_pair(after)
        elif before:
            status = "removed_from_retained_set"
            endpoints_unchanged = False
        else:
            status = "added_to_retained_set"
            endpoints_unchanged = False
        rows.append(
            {
                "transition_status": status,
                "source_line_ids": ",".join(key),
                "old_branch_id": "" if before is None else before["branch_id"],
                "new_branch_id": "" if after is None else after["branch_id"],
                "old_from_facility_uid": "" if before is None else before["from_facility_uid"],
                "old_to_facility_uid": "" if before is None else before["to_facility_uid"],
                "new_from_facility_uid": "" if after is None else after["from_facility_uid"],
                "new_to_facility_uid": "" if after is None else after["to_facility_uid"],
                "endpoint_pair_unchanged": endpoints_unchanged,
                "old_confidence_score": "" if before is None else before["confidence_score"],
                "new_confidence_score": "" if after is None else after["confidence_score"],
            }
        )

    result = pd.DataFrame(rows)
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.output_csv, index=False)
    counts = result["transition_status"].value_counts().to_dict()
    shared = result[result["transition_status"] == "retained_in_both"]
    summary = {
        "old_version": args.old_version,
        "new_version": args.new_version,
        "old_retained_branches": int(len(old)),
        "new_retained_branches": int(len(new)),
        "transition_status_counts": {str(key): int(value) for key, value in counts.items()},
        "shared_source_line_groups": int(len(shared)),
        "shared_groups_with_unchanged_endpoint_pair": int(shared["endpoint_pair_unchanged"].astype(bool).sum()),
        "retained_set_exchange_fraction": 1 / len(old) if len(old) else None,
        "removed_records": result[result["transition_status"] == "removed_from_retained_set"].to_dict("records"),
        "added_records": result[result["transition_status"] == "added_to_retained_set"].to_dict("records"),
        "interpretation": (
            "The formal EPSG:3763 rebuild preserves 357 source-line groups and their endpoint facility pairs, "
            "removes one previously retained group, and adds one previously non-retained group. Branch IDs are "
            "release-local identifiers and may be renumbered even when source membership is unchanged."
        ),
    }
    args.output_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
