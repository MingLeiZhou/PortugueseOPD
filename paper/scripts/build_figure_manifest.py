#!/usr/bin/env python3
"""Build the paper figure manifest with source hashes and output provenance."""

from __future__ import annotations

import argparse
import csv
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "paper" / "figure_manifest.csv"


FIGURES = [
    {
        "figure_id": "fig1",
        "paper_section": "Fail-Closed Dataset Construction",
        "purpose": "Summarize the fail-closed reconstruction, validation, and deposited release layers.",
        "claim": "The public release preserves both retained candidates and downgraded/rejected records with provenance, validation, and schema metadata.",
        "sources": ["paper/figures/source/fig1_pipeline_overview.drawio"],
        "generator": "paper/scripts/export_conceptual_figures.py",
        "stem": "fig1_pipeline_overview",
        "width": "double-column",
        "caption": "Fail-closed reconstruction and release workflow. Blue boxes are transformations, the yellow diamond is the retention gate, green boxes are deposited release layers, and the red box is the complete downgrade/rejection ledger. The diagram is conceptual and does not encode measured performance.",
        "uncertainty": "Conceptual diagram; no measured values.",
        "provenance_notes": "Authored as draw.io XML and exported without manual post-processing.",
        "license_status": "code_and_conceptual_only",
        "notes": "The manuscript uses the validated vector PDF. The draw.io SVG contains embedded rendering elements and is retained only as an alternate export; the editable source is the .drawio file.",
    },
    {
        "figure_id": "fig2",
        "paper_section": "Dataset Composition",
        "purpose": "Show the reconstruction funnel and complete selected-strategy circuit disposition.",
        "claim": "The selected strategy converts 5,334 raw line features into 1,342 circuits and retains 358 inter-facility candidates while preserving rejected classes.",
        "sources": ["data/releases/PT60-Candidate-v1.0.1/core_topology/at_paper_logic_summary.json"],
        "generator": "paper/scripts/generate_quantitative_figures.py",
        "stem": "fig2_reconstruction_funnel",
        "width": "double-column",
        "caption": "Reconstruction funnel and circuit disposition for the selected node-set B, 100 m facility-buffer, 0.5 m endpoint-snap, voltage-status-aware strategy. Counts describe pipeline outputs from the current E-REDES snapshot; retained means passing declared geometric rules, not operator validation.",
        "uncertainty": "Counts are deterministic for the snapshot but classifications depend on facility coverage and thresholds.",
        "provenance_notes": "Generated directly from the topology summary; no geographic identifiers are plotted.",
        "license_status": "cc_by_4_0_eredes_attribution_required",
    },
    {
        "figure_id": "fig3",
        "paper_section": "Dataset Composition",
        "purpose": "Show the spatial footprint of released facilities and candidate branches together with graph fragmentation.",
        "claim": "The 358 candidates span mainland Portugal while the all-facility candidate graph remains fragmented; the largest component has 53 facilities.",
        "sources": ["data/releases/PT60-Candidate-v1.0.1/core_topology/at_paper_logic_graph.graphml", "data/releases/PT60-Candidate-v1.0.1/core_topology/at_interfacility_candidate_branches.csv"],
        "generator": "paper/scripts/generate_quantitative_figures.py",
        "stem": "fig3_topology_quality",
        "width": "double-column",
        "caption": "Geographic distribution and aggregate structure of the selected candidate graph. The map uses released EPSG:4326 coordinates and labels no facilities; blue lines belong to the largest component and gray lines to other components. Component sizes use a logarithmic vertical scale. The graph contains 484 facilities, 358 branch records, and 109 isolated facilities.",
        "uncertainty": "Graph metrics are exact for the inferred candidate, but physical correctness lacks operator ground truth.",
        "provenance_notes": "Computed from the released GraphML and retained-branch geometry using NetworkX and Matplotlib; no basemap is used.",
        "license_status": "cc_by_4_0_eredes_attribution_required",
    },
    {
        "figure_id": "fig4",
        "paper_section": "Quality Assurance and Validation",
        "purpose": "Expose threshold sensitivity and the trade-off used by the internal selection heuristic.",
        "claim": "Retained branch yield, ambiguity, and connected-component size vary materially with facility buffers and endpoint snapping.",
        "sources": ["data/releases/PT60-Candidate-v1.0.1/core_topology/at_paper_logic_parameter_sweep.csv"],
        "generator": "paper/scripts/generate_quantitative_figures.py",
        "stem": "fig4_sensitivity_analysis",
        "width": "double-column",
        "caption": "Topology-reconstruction sensitivity. Panels a-c hold node set B and voltage-status-aware merging fixed while varying facility buffer and endpoint snap; the outlined star marks the selected 100 m/0.5 m cell. Panel d shows all 216 strategies and marks the internal recommendation. Selection balances retained branches, ambiguity, and connectivity; it is not a comparison against operator ground truth.",
        "uncertainty": "Discrete parameter sweep; no statistical error bars because each configuration is deterministic.",
        "provenance_notes": "Generated from all 216 sweep rows with untruncated axes.",
        "license_status": "cc_by_4_0_eredes_attribution_required",
    },
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", choices=["generated", "validated"], default="generated")
    args = parser.parse_args()

    fields = [
        "figure_id", "panel_id", "paper_section", "claim", "purpose",
        "source_data", "source_artifacts", "source_sha256", "generator",
        "generator_script", "output_file", "output_pdf", "output_svg",
        "preview_png", "width", "caption", "uncertainty", "provenance_notes",
        "license_status", "status", "notes",
    ]
    rows = []
    for spec in FIGURES:
        sources = spec["sources"]
        hashes = [sha256(ROOT / source) for source in sources]
        stem = spec["stem"]
        pdf = f"paper/figures/generated/{stem}.pdf"
        svg = f"paper/figures/generated/{stem}.svg"
        png = f"paper/figures/generated/{stem}.png"
        rows.append(
            {
                "figure_id": spec["figure_id"],
                "panel_id": "all",
                "paper_section": spec["paper_section"],
                "claim": spec["claim"],
                "purpose": spec["purpose"],
                "source_data": ";".join(sources),
                "source_artifacts": ";".join(sources),
                "source_sha256": ";".join(hashes),
                "generator": spec["generator"],
                "generator_script": spec["generator"],
                "output_file": ";".join([pdf, svg, png]),
                "output_pdf": pdf,
                "output_svg": svg,
                "preview_png": png,
                "width": spec["width"],
                "caption": spec["caption"],
                "uncertainty": spec["uncertainty"],
                "provenance_notes": spec["provenance_notes"],
                "license_status": spec["license_status"],
                "status": args.status,
                "notes": spec.get("notes", "No manual edits to data-bearing outputs."),
            }
        )

    with MANIFEST.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {MANIFEST}")


if __name__ == "__main__":
    main()
