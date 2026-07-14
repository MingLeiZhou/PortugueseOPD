"""Build a source manifest and attribution-aware redistribution gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

import config
from utils import ensure_directories, utc_now, write_json, write_text


API_VALIDATION = config.METADATA_DIR / "api_validation.json"
OUT_CSV = config.METADATA_DIR / "reproduction_source_manifest.csv"
OUT_JSON = config.METADATA_DIR / "reproduction_source_manifest.json"
APPROVALS = config.METADATA_DIR / "license_approvals.csv"
REPORT = config.REPORTS_DIR / "101_reproduction_and_license_gate.md"

SOURCE_ROLES = {
    "rede-at-teste": "topology_critical_at_lines",
    "se-at_2025": "topology_critical_at_substations",
    "pc-at_2025": "topology_critical_switching_facilities",
    "se-mt_2025": "transformer_and_multivoltage_inventory",
    "caracteristicas-da-rede": "electrical_characteristics_and_short_circuit_context",
    "carga-na-subestacao": "substation_load_context",
    "capacidade-rececao-rnd": "hosting_capacity_context_not_dispatch",
    "diagrama-de-carga-de-subestacao": "hourly_load_index",
    "diagrama_carga_subestacao_01_a_07": "hourly_load_partition",
    "diagrama_carga_subestacao_08_a_10": "hourly_load_partition",
    "diagrama_carga_subestacao_11_a_12": "hourly_load_partition",
    "diagrama_carga_subestacao_13_a_15": "hourly_load_partition",
    "diagrama_carga_subestacao_16_a_18": "hourly_load_partition",
}
TOPOLOGY_CRITICAL = {"rede-at-teste", "se-at_2025", "pc-at_2025"}

PORTAL_LICENSE = "CC BY 4.0"
PORTAL_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
PORTAL_TERMS_URL = "https://e-redes.opendatasoft.com/pages/homepage/"
PORTAL_ATTRIBUTION = (
    "E-REDES - Distribuicao de Eletricidade, 'E-REDES Open Data Portal'. "
    "Accessed [date]. [Online] Available at "
    "https://e-redes.opendatasoft.com/pages/homepage/"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def local_snapshots(dataset_id: str) -> list[dict[str, Any]]:
    rows = []
    for suffix in ("geojson", "csv", "json"):
        path = config.RAW_DIR / f"{dataset_id}.{suffix}"
        if path.exists():
            rows.append(
                {
                    "format": suffix,
                    "path": str(path.relative_to(config.ROOT_DIR)),
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256(path),
                }
            )
    return rows


def main() -> None:
    ensure_directories()
    if not API_VALIDATION.exists():
        raise RuntimeError("Fail-closed: run src/validate_api.py before building the source manifest.")
    payload = json.loads(API_VALIDATION.read_text(encoding="utf-8"))
    by_id = {str(item["dataset_id"]): item for item in payload}
    approvals = pd.read_csv(APPROVALS, keep_default_na=False) if APPROVALS.exists() else pd.DataFrame()
    approval_by_id = approvals.set_index("dataset_id").to_dict("index") if len(approvals) else {}

    rows: list[dict[str, Any]] = []
    for dataset_id, role in SOURCE_ROLES.items():
        item = by_id.get(dataset_id, {})
        approval = approval_by_id.get(dataset_id, {})
        catalog_license_status = str(item.get("license_metadata_status", "MISSING"))
        catalog_license = str(item.get("license_observed", "")).strip()
        catalog_license_url = str(item.get("license_url_observed", "")).strip()
        if catalog_license_status == "EXPLICIT" and catalog_license:
            effective_license = catalog_license
            effective_license_url = catalog_license_url or PORTAL_LICENSE_URL
            license_basis = "DATASET_CATALOG_METADATA"
        else:
            effective_license = PORTAL_LICENSE
            effective_license_url = PORTAL_LICENSE_URL
            license_basis = "PORTAL_LEVEL_CC_BY_4_0"
        review_status = str(approval.get("review_status", "NOT_REVIEWED"))
        topology_critical = dataset_id in TOPOLOGY_CRITICAL
        redistribution_cleared = effective_license.upper().replace(" ", "") in {
            "CCBY4.0",
            "CC-BY-4.0",
        }
        rows.append(
            {
                "dataset_id": dataset_id,
                "source_role": role,
                "topology_critical": topology_critical,
                "metadata_status": item.get("metadata_status"),
                "records_count": item.get("v2_total_count"),
                "metadata_url": item.get("metadata_url", f"{config.BASE_URL}/api/explore/v2.1/catalog/datasets/{dataset_id}"),
                "portal_url": item.get("portal_dataset_url", f"{config.BASE_URL}/explore/dataset/{dataset_id}/"),
                "csv_export_url": item.get("export_formats", {}).get("csv", {}).get("url", ""),
                "geojson_export_url": item.get("export_formats", {}).get("geojson", {}).get("url", ""),
                "checked_at": item.get("checked_at", ""),
                "catalog_license_observed": catalog_license,
                "catalog_license_url_observed": catalog_license_url,
                "catalog_license_metadata_status": catalog_license_status,
                "license_observed": effective_license,
                "license_url_observed": effective_license_url,
                "license_basis": license_basis,
                "portal_license_statement_url": PORTAL_TERMS_URL,
                "attribution_required": True,
                "required_attribution": PORTAL_ATTRIBUTION,
                "attribution_observed": item.get("attribution_observed", ""),
                "license_metadata_status": (
                    "EXPLICIT" if license_basis == "DATASET_CATALOG_METADATA"
                    else "PORTAL_LEVEL_FALLBACK"
                ),
                "license_review_status": review_status,
                "license_review_evidence": approval.get("evidence_reference", ""),
                "redistribution_cleared": redistribution_cleared,
                "redistribution_gate": (
                    "PASS_WITH_ATTRIBUTION" if redistribution_cleared else "BLOCKED"
                ),
                "local_snapshot_count": len(local_snapshots(dataset_id)),
                "local_snapshots_json": json.dumps(local_snapshots(dataset_id), ensure_ascii=False),
                "acquisition_allowed": item.get("metadata_status") == 200,
            }
        )
    frame = pd.DataFrame(rows)
    frame.to_csv(OUT_CSV, index=False)
    if not APPROVALS.exists():
        pd.DataFrame(
            [
                {
                    "dataset_id": dataset_id,
                    "review_status": "NOT_REVIEWED",
                    "reviewer": "",
                    "review_date": "",
                    "evidence_reference": "",
                    "notes": "",
                }
                for dataset_id in sorted(SOURCE_ROLES)
            ]
        ).to_csv(APPROVALS, index=False)

    critical = frame[frame["topology_critical"]]
    public_release_allowed = bool(len(critical)) and bool(critical["redistribution_cleared"].all())
    summary = {
        "generated_at": utc_now(),
        "status": "REPRODUCTION_METADATA_READY" if len(frame) else "NOT_READY",
        "datasets": int(len(frame)),
        "topology_critical_datasets": int(len(critical)),
        "topology_critical_with_explicit_catalog_license": int(
            (critical["catalog_license_metadata_status"] == "EXPLICIT").sum()
        ),
        "topology_critical_using_portal_license_fallback": int(
            (critical["license_basis"] == "PORTAL_LEVEL_CC_BY_4_0").sum()
        ),
        "topology_critical_with_effective_cc_by_4_0": int(
            critical["redistribution_cleared"].sum()
        ),
        "topology_critical_with_approved_review": int((critical["license_review_status"] == "APPROVED").sum()),
        "public_derived_data_release_allowed": public_release_allowed,
        "public_release_conditions": [
            "retain E-REDES attribution",
            "link CC BY 4.0",
            "identify source datasets and access dates",
            "indicate transformations or modifications",
        ],
        "portal_license": PORTAL_LICENSE,
        "portal_license_url": PORTAL_LICENSE_URL,
        "portal_license_statement_url": PORTAL_TERMS_URL,
        "required_attribution": PORTAL_ATTRIBUTION,
        "code_release_allowed": True,
        "reproduction_mode": "versioned_derived_release_or_user_acquisition_under_cc_by_4_0",
        "source_rows": frame.to_dict("records"),
    }
    write_json(OUT_JSON, summary)

    text = [
        "# 101 Reproduction and License Gate",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        f"- source datasets recorded: {summary['datasets']}",
        f"- topology-critical datasets: {summary['topology_critical_datasets']}",
        f"- topology-critical explicit catalog licenses observed: {summary['topology_critical_with_explicit_catalog_license']}",
        f"- topology-critical rows using the portal-level CC BY 4.0 fallback: {summary['topology_critical_using_portal_license_fallback']}",
        f"- topology-critical rows with an effective CC BY 4.0 basis: {summary['topology_critical_with_effective_cc_by_4_0']}",
        f"- topology-critical reviews approved: {summary['topology_critical_with_approved_review']}",
        f"- public derived-data release allowed: `{summary['public_derived_data_release_allowed']}`",
        "",
        "E-REDES states that portal data are distributed under CC BY 4.0 with publisher attribution. Dataset-catalog metadata are retained separately so a missing license field in one API response is visible rather than mistaken for a different license. A public derived release must preserve E-REDES attribution, the CC BY 4.0 link, source identifiers and access dates, and an indication of transformations. The repository MIT license applies to code only.",
    ]
    write_text(REPORT, "\n".join(text) + "\n")
    print(json.dumps({key: value for key, value in summary.items() if key != "source_rows"}, indent=2))


if __name__ == "__main__":
    main()
