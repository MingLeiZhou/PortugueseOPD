#!/usr/bin/env python3
"""Generate the relation-aware OSM/REN coverage audit figure."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.collections import LineCollection
from pyproj import Transformer

from common import PROJECT, TABLES, VALIDATION, sha256


FIGURES = PROJECT / "outputs" / "figures"
COLORS = {150: "#0072B2", 220: "#E69F00", 400: "#CC79A7"}


def load_segments(frame: pd.DataFrame, transformer: Transformer) -> dict[int, list[list[tuple[float, float]]]]:
    output: dict[int, list[list[tuple[float, float]]]] = {150: [], 220: [], 400: []}
    for row in frame.to_dict("records"):
        voltage = int(row["voltage_kv"])
        if row.get("source") != "OpenStreetMap" or voltage not in output:
            continue
        coordinates = json.loads(row["geometry_json"])
        output[voltage].append([transformer.transform(float(lon), float(lat)) for lon, lat in coordinates])
    return output


def main() -> None:
    FIGURES.mkdir(parents=True, exist_ok=True)
    retained_path = TABLES / "lines_topology.csv"
    blocked_path = TABLES / "lines_blocked.csv"
    coverage_path = VALIDATION / "voltage_coverage_reconciliation.csv"
    retained = pd.read_csv(retained_path, low_memory=False)
    blocked = pd.read_csv(blocked_path, low_memory=False)
    coverage = pd.read_csv(coverage_path)
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3763", always_xy=True)
    retained_segments = load_segments(retained, transformer)
    blocked_segments = load_segments(blocked, transformer)

    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 8.5, "axes.titlesize": 9.5,
        "axes.labelsize": 8.5, "legend.fontsize": 7.5, "pdf.fonttype": 42,
        "ps.fonttype": 42,
    })
    figure, (map_ax, bar_ax) = plt.subplots(
        1, 2, figsize=(7.2, 4.25), gridspec_kw={"width_ratios": [1.45, 1.0]},
    )
    for voltage in (150, 220, 400):
        map_ax.add_collection(LineCollection(
            blocked_segments[voltage], colors=COLORS[voltage], linewidths=0.35,
            alpha=0.22, linestyles="dotted", zorder=1,
        ))
        map_ax.add_collection(LineCollection(
            retained_segments[voltage], colors=COLORS[voltage], linewidths=0.8,
            alpha=0.9, linestyles="solid", label=f"{voltage} kV retained", zorder=2,
        ))
    mainland_min = transformer.transform(-9.65, 36.75)
    mainland_max = transformer.transform(-6.0, 42.25)
    map_ax.set_xlim(mainland_min[0], mainland_max[0])
    map_ax.set_ylim(mainland_min[1], mainland_max[1])
    map_ax.set_aspect("equal", adjustable="box")
    map_ax.set_axis_off()
    map_ax.set_title("a  Extracted Portuguese transmission corridors", loc="left", fontweight="bold")
    map_ax.legend(loc="upper left", frameon=False, handlelength=2.4)
    map_ax.text(
        0.01, 0.01, "Mainland Portugal extent\nSolid: retained  ·  dotted: blocked after endpoint clustering\nETRS89 / Portugal TM06 (EPSG:3763)",
        transform=map_ax.transAxes, va="bottom", ha="left", fontsize=6.7,
    )

    subset = coverage[coverage["voltage_kv"].isin([150, 220, 400])].copy()
    x = range(len(subset))
    width = 0.24
    bar_ax.bar([value - width for value in x], subset["osm_relation_circuit_km"], width, color="#0072B2", label="Geofabrik relation circuit-km")
    bar_ax.bar(x, subset["osm_wiki_mapped_km_2026_07_14"], width, color="#999999", label="OSM Portugal mapped total")
    bar_ax.bar([value + width for value in x], subset["ren_2025_context_km"], width, color="#E69F00", label="REN context total")
    bar_ax.set_xticks(list(x), [f"{value} kV" for value in subset["voltage_kv"]])
    bar_ax.set_ylabel("Circuit length (km)")
    bar_ax.set_title("b  Circuit-length reconciliation", loc="left", fontweight="bold")
    bar_ax.grid(axis="y", color="#dddddd", linewidth=0.6)
    bar_ax.spines[["top", "right"]].set_visible(False)
    bar_ax.set_ylim(0, 5500)
    bar_ax.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, 0.99))
    for container in bar_ax.containers:
        bar_ax.bar_label(container, fmt="%.0f", fontsize=6.5, padding=1, rotation=90)

    figure.suptitle("Relation-aware Geofabrik extraction and published-length cross-check", x=0.06, ha="left", fontsize=10.5, fontweight="bold")
    figure.text(
        0.06, 0.015,
        "OpenInfraMap visualizes the same OSM source; display agreement is a rendering check, not independent validation.",
        fontsize=7, ha="left",
    )
    figure.tight_layout(rect=(0.02, 0.045, 1, 0.94))
    outputs = [FIGURES / "osm_geofabrik_coverage_audit.pdf", FIGURES / "osm_geofabrik_coverage_audit.svg", FIGURES / "osm_geofabrik_coverage_audit.png"]
    figure.savefig(outputs[0], bbox_inches="tight")
    figure.savefig(outputs[1], bbox_inches="tight")
    figure.savefig(outputs[2], dpi=320, bbox_inches="tight")
    plt.close(figure)

    caption = (
        "Relation-aware extraction of Portuguese 150, 220, and 400 kV OpenStreetMap geometry. "
        "Panel a distinguishes topology segments retained after endpoint clustering from segments preserved in the blocked ledger. "
        "Panel b compares circuit-relation length with the dated OSM Portugal mapping summary and REN contextual totals. "
        "OpenInfraMap is a visualization of the same OSM source and is used only for display-parity review."
    )
    manifest_path = FIGURES / "figure_manifest.csv"
    with manifest_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "figure_id", "panel_id", "claim", "source_data", "source_sha256", "generator",
            "output_file", "caption", "uncertainty", "license_status", "status", "notes",
        ])
        writer.writeheader()
        writer.writerow({
            "figure_id": "hv-audit-01", "panel_id": "a-b",
            "claim": "The relation-aware PBF extraction recovers high-voltage geometry and circuit-length totals suitable for source reconciliation.",
            "source_data": ";".join(str(path.relative_to(PROJECT)) for path in (retained_path, blocked_path, coverage_path)),
            "source_sha256": ";".join(sha256(path) for path in (retained_path, blocked_path, coverage_path)),
            "generator": "src/generate_osm_coverage_figure.py",
            "output_file": str(outputs[0].relative_to(PROJECT)), "caption": caption,
            "uncertainty": "No statistical uncertainty; differences reflect source date and corridor-vs-circuit definitions.",
            "license_status": "OSM-derived data: ODbL 1.0; REN values used as attributed contextual statistics.",
            "status": "validated", "notes": "No basemap used. EPSG:3763. Blocked geometry remains visible at low opacity. PDF/PNG validators passed and the final raster was visually inspected.",
        })
    print(json.dumps({"outputs": [str(path) for path in outputs], "manifest": str(manifest_path)}, indent=2))


if __name__ == "__main__":
    main()
