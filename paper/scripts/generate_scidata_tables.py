#!/usr/bin/env python3
"""Generate quantitative Scientific Data table rows from the frozen release."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import networkx as nx


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "paper" / "generated_tables"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def csv_rows(path: Path) -> int:
    with path.open(newline="", encoding="utf-8") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def write_table(name: str, column_spec: str, header: str, rows: list[str]) -> Path:
    path = OUT / name
    content = [
        "% Generated from PT60-Candidate v1.0.1; do not edit by hand.",
        f"\\begin{{tabular}}{{{column_spec}}}",
        "\\toprule",
        f"{header} \\\\",
        "\\midrule",
        *rows,
        "\\bottomrule",
        "\\end{tabular}",
    ]
    path.write_text("\n".join(content) + "\n", encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--release-root",
        type=Path,
        default=ROOT / "data" / "releases" / "PT60-Candidate-v1.0.1",
    )
    args = parser.parse_args()
    release = args.release_root.resolve()
    core = release / "core_topology"
    validation = release / "validation"
    provenance = release / "provenance"
    schema = release / "schema"
    source_audit = ROOT / "data" / "metadata" / "source_reacquisition_audit.json"
    OUT.mkdir(parents=True, exist_ok=True)

    required = [
        release / "manifest.json",
        core / "at_interfacility_candidate_branches.csv",
        core / "at_circuit_classification.csv",
        core / "at_paper_logic_graph.graphml",
        core / "at_paper_logic_parameter_sweep.csv",
        validation / "pt_topology_cross_validation_osm_matches.csv",
        validation / "pt_topology_cross_validation_summary.json",
        provenance / "reproduction_source_manifest.csv",
        provenance / "reproduction_source_manifest.json",
        schema / "data_dictionary.csv",
        source_audit,
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Frozen table inputs are missing: {missing}")

    layout_specs = [
        ("core\\_topology/", "core_topology", "Retained branches, complete circuit ledger, GraphML, sensitivity sweep, endpoint/facility summaries, voltage inventory, and parameter-readiness summary."),
        ("provenance/", "provenance", "Source manifests, API validation, licence decisions, and responsible-release boundary."),
        ("validation/", "validation", "Internal checks, missingness, two negative controls, public-source concordance, and sanitized independence-risk records."),
        ("schema/", "schema", "Field dictionary, file schemas, joins, CRS/geometry documentation, and 15 machine-readable schemas."),
        ("inventory/", "inventory", "Frozen headline counts and release-scope statements."),
    ]
    layout_rows = [
        f"\\code{{{label}}} & {sum(path.is_file() for path in (release / directory).rglob('*'))} & {purpose} \\\\"
        for label, directory, purpose in layout_specs
    ]
    root_files = sum(path.is_file() for path in release.iterdir())
    layout_rows.append(f"Archive root & {root_files} & Readme, citation, licences, attribution, changelog, manifest, checksums, exclusions, and validation summary. \\\\")

    graph = nx.read_graphml(core / "at_paper_logic_graph.graphml")
    principal_rows = [
        f"Retained candidate branches CSV & {csv_rows(core / 'at_interfacility_candidate_branches.csv'):,} & Inter-facility candidate branches with endpoint facilities, source identifiers, geometry-derived length, status, and confidence metadata. \\\\ ".rstrip(),
        f"Circuit-classification ledger CSV & {csv_rows(core / 'at_circuit_classification.csv'):,} & Full retained and downgraded circuit population with terminal structure, conflict flags, and disposition fields. \\\\ ".rstrip(),
        f"Selected GraphML multigraph & {graph.number_of_nodes():,} nodes, {graph.number_of_edges():,} edges & All-facility candidate graph including isolates under the declared release policy. \\\\ ".rstrip(),
        f"Reconstruction-sensitivity CSV & {csv_rows(core / 'at_paper_logic_parameter_sweep.csv'):,} & Parameter sweep across facility sets, buffers, snap thresholds, and merge modes. \\\\ ".rstrip(),
        f"Public-source concordance CSV/JSON & {csv_rows(validation / 'pt_topology_cross_validation_osm_matches.csv'):,} branch records & Public-evidence categories, matching variables, source roles, and supporting summaries. \\\\ ".rstrip(),
        "Negative-control CSV/JSON & 2 controls & Endpoint-name corruption and spatial-displacement matcher-selectivity results. \\\\ ".rstrip(),
        f"Source manifests CSV/JSON & {csv_rows(provenance / 'reproduction_source_manifest.csv'):,} sources & Source dataset identifiers, roles, access checks, and attribution metadata. \\\\ ".rstrip(),
        f"Data dictionary CSV & {csv_rows(schema / 'data_dictionary.csv'):,} records & Field, JSON-pointer, and GraphML-attribute definitions across 54 machine-readable paths. \\\\ ".rstrip(),
    ]

    osm = json.loads((validation / "pt_topology_cross_validation_summary.json").read_text(encoding="utf-8"))
    status = osm["status_counts"]
    osm_specs = [
        ("Strong endpoint-name and operator-tag evidence", "OSM_NAME_OPERATOR_STRONG", "Both endpoint names appear in an E-REDES-tagged OSM record."),
        ("Strong corridor and operator-tag evidence", "OSM_GEOMETRY_OPERATOR_STRONG", "Candidate corridor is close to an E-REDES-tagged OSM corridor."),
        ("Partial endpoint name with nearby corridor", "OSM_PARTIAL_NAME_NEARBY", "One endpoint name appears and a nearby 60~kV corridor is present."),
        ("Medium corridor-overlap evidence", "OSM_GEOMETRY_MEDIUM", "Candidate overlaps a 60~kV OSM corridor without strong name evidence."),
        ("Weak nearby-feature evidence", "OSM_NEARBY_WEAK", "A nearby 60~kV OSM feature is present, but evidence is incomplete."),
        ("No matched OSM-derived evidence", None, "No retained branch falls in this class for the documented snapshot."),
    ]
    osm_rows = [
        f"{label} & {int(status.get(key, osm['no_osm_match_branches'] if key is None else 0)):,} & {meaning} \\\\"
        for label, key, meaning in osm_specs
    ]

    source_manifest = json.loads((provenance / "reproduction_source_manifest.json").read_text(encoding="utf-8"))
    reacquisition = json.loads(source_audit.read_text(encoding="utf-8"))
    status_by_id = {row["dataset_id"]: row for row in reacquisition["sources"]}
    role_labels = {
        "topology_critical_at_lines": "AT line geometries",
        "topology_critical_at_substations": "AT substation facilities",
        "topology_critical_switching_facilities": "switching facilities",
    }
    source_rows = []
    for row in source_manifest["source_rows"]:
        if not row.get("topology_critical"):
            continue
        audit = status_by_id[row["dataset_id"]]
        codes = sorted(set(audit["current_http_status"].values()), key=str)
        status_text = "/".join(str(code) for code in codes)
        source_rows.append(
            f"\\code{{{row['dataset_id'].replace('_', '\\_')}}} & {int(row['records_count']):,} & {row['checked_at'][:10]} & "
            f"{role_labels[row['source_role']]} & HTTP {status_text} \\\\"
        )

    outputs = [
        write_table("archive_layout_table.tex", "p{0.20\\textwidth}rp{0.59\\textwidth}", "Path & Files & Contents", layout_rows),
        write_table("principal_artifacts_table.tex", "p{0.30\\textwidth}rp{0.42\\textwidth}", "Artifact & Rows/items & Purpose", principal_rows),
        write_table("osm_triangulation_table.tex", "p{0.46\\textwidth}rp{0.32\\textwidth}", "Evidence category & Branches & Interpretation", osm_rows),
        write_table("source_inputs_table.tex", "p{0.20\\textwidth}rp{0.14\\textwidth}p{0.25\\textwidth}p{0.11\\textwidth}", "Portal identifier & Records & Snapshot date & Role & Recheck", source_rows),
    ]
    report = {
        "release_root": str(release.relative_to(ROOT)),
        "inputs": [{"path": str(path.relative_to(ROOT)), "sha256": sha256(path)} for path in required],
        "outputs": [{"path": str(path.relative_to(ROOT)), "sha256": sha256(path)} for path in outputs],
    }
    report_path = OUT / "table_build_provenance.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Generated {len(outputs)} quantitative table fragments from {release}")
    print(f"Wrote provenance report to {report_path}")


if __name__ == "__main__":
    main()
