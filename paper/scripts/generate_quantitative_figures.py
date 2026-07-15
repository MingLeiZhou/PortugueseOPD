#!/usr/bin/env python3
"""Generate the canonical data-bearing paper figures from a frozen release.

Every plotted value is read from the versioned public archive.  The geographic
overview uses released coordinates but prints no facility identifiers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "paper" / "figures" / "generated"
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache" / "matplotlib"))
os.environ.setdefault("XDG_CACHE_HOME", str(ROOT / ".cache"))

import matplotlib as mpl
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import pandas as pd


COLORS = {
    "blue": "#0072B2",
    "orange": "#E69F00",
    "green": "#009E73",
    "red": "#D55E00",
    "purple": "#CC79A7",
    "sky": "#56B4E9",
    "gray": "#6B7280",
    "light_gray": "#D1D5DB",
}


def configure_style() -> None:
    mpl.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 8.5,
            "axes.titlesize": 9.5,
            "axes.labelsize": 8.5,
            "xtick.labelsize": 7.5,
            "ytick.labelsize": 7.5,
            "legend.fontsize": 7.5,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": False,
            "savefig.bbox": "tight",
            "svg.fonttype": "none",
            "pdf.fonttype": 42,
        }
    )


def panel_label(ax: plt.Axes, label: str) -> None:
    ax.text(-0.13, 1.09, label, transform=ax.transAxes, fontweight="bold", fontsize=10, va="top")


def save(fig: plt.Figure, stem: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / f"{stem}.pdf")
    svg_path = OUT / f"{stem}.svg"
    fig.savefig(svg_path)
    svg_path.write_text("\n".join(line.rstrip() for line in svg_path.read_text(encoding="utf-8").splitlines()) + "\n", encoding="utf-8")
    fig.savefig(OUT / f"{stem}.png", dpi=300)
    plt.close(fig)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def figure2_reconstruction_funnel(core: Path) -> None:
    summary = json.loads((core / "at_paper_logic_summary.json").read_text())
    raw = int(summary["validation"]["raw_feature_count"])
    merged = int(summary["selected_strategy"]["merged_circuits"])
    retained = int(summary["selected_strategy"]["inter_facility_circuits"])
    classes = {row["classification"]: row for row in summary["classification_summary"]}

    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.0), gridspec_kw={"width_ratios": [1.05, 1.45]})

    labels = ["Raw line features", "Merged circuits", "Retained branches"]
    values = [raw, merged, retained]
    colors = [COLORS["gray"], COLORS["sky"], COLORS["green"]]
    y = np.arange(len(labels))
    axes[0].barh(y, values, color=colors, height=0.62)
    axes[0].set_yticks(y, labels)
    axes[0].invert_yaxis()
    axes[0].set_xlabel("Count")
    axes[0].set_xlim(0, raw * 1.14)
    axes[0].set_title("Reconstruction funnel")
    for i, value in enumerate(values):
        axes[0].text(value + raw * 0.015, i, f"{value:,}", va="center", fontsize=8)
    axes[0].text(merged, 0.55, f"{100 * (1 - merged / raw):.1f}% reduction", ha="right", color=COLORS["blue"])
    axes[0].text(retained, 1.55, f"{100 * retained / merged:.1f}% retained", ha="left", color=COLORS["green"])
    panel_label(axes[0], "a")

    order = [
        "inter-facility",
        "single-facility",
        "isolated",
        "tap / multi-terminal",
        "self-loop",
        "ambiguous",
        "loop",
    ]
    counts = [int(classes[k]["circuit_count"]) for k in order]
    display = [x.replace("inter-facility", "inter-facility (retained)") for x in order]
    bar_colors = [COLORS["green"], COLORS["orange"], COLORS["gray"], COLORS["sky"], COLORS["purple"], COLORS["red"], COLORS["light_gray"]]
    y = np.arange(len(order))
    axes[1].barh(y, counts, color=bar_colors, height=0.62)
    axes[1].set_yticks(y, display)
    axes[1].invert_yaxis()
    axes[1].set_xlabel("Merged circuits")
    axes[1].set_xlim(0, max(counts) * 1.18)
    axes[1].set_title("Selected-strategy circuit classification")
    for i, value in enumerate(counts):
        axes[1].text(value + max(counts) * 0.015, i, f"{value:,}", va="center", fontsize=8)
    panel_label(axes[1], "b")

    fig.subplots_adjust(wspace=0.52)
    save(fig, "fig2_reconstruction_funnel")


def geometry_parts(value: str) -> list[list[list[float]]]:
    geometry = json.loads(value)
    if geometry["type"] == "LineString":
        return [geometry["coordinates"]]
    if geometry["type"] == "MultiLineString":
        return geometry["coordinates"]
    raise ValueError(f"Unsupported geometry type: {geometry['type']}")


def figure3_topology_quality(core: Path) -> None:
    graph = nx.read_graphml(core / "at_paper_logic_graph.graphml")
    simple = nx.Graph(graph)
    component_sets = sorted(nx.connected_components(simple), key=len, reverse=True)
    components = [len(component) for component in component_sets]
    largest = component_sets[0]
    edge_component = {}
    for rank, component in enumerate(component_sets, start=1):
        for node in component:
            edge_component[node] = rank
    branch_endpoints = {}
    for u, v, data in graph.edges(data=True):
        branch_endpoints[str(data["branch_id"])] = (u, v)
    branches = pd.read_csv(core / "at_interfacility_candidate_branches.csv")

    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.35), gridspec_kw={"width_ratios": [1.45, 1.0]})
    ax = axes[0]
    for row in branches.itertuples(index=False):
        u, _ = branch_endpoints[str(row.branch_id)]
        color = COLORS["blue"] if u in largest else COLORS["light_gray"]
        width = 0.75 if u in largest else 0.42
        for part in geometry_parts(row.geometry):
            ax.plot([point[0] for point in part], [point[1] for point in part], color=color, linewidth=width, zorder=1)
    connected_x, connected_y, isolate_x, isolate_y = [], [], [], []
    for node, data in graph.nodes(data=True):
        target_x, target_y = (isolate_x, isolate_y) if simple.degree(node) == 0 else (connected_x, connected_y)
        target_x.append(float(data["lon"]))
        target_y.append(float(data["lat"]))
    ax.scatter(
        connected_x,
        connected_y,
        s=5,
        alpha=0.60,
        color=COLORS["green"],
        edgecolors="none",
        zorder=2,
        label="Connected facility",
    )
    ax.scatter(isolate_x, isolate_y, s=9, marker="x", linewidths=0.55, color=COLORS["orange"], zorder=2, label="Isolated facility")
    ax.set_xlabel("Longitude (°E; EPSG:4326)")
    ax.set_ylabel("Latitude (°N; EPSG:4326)")
    ax.set_aspect(1 / np.cos(np.deg2rad(np.mean(connected_y + isolate_y))))
    ax.set_title(r"$\bf{a}$  Released candidate geography", loc="left")
    ax.legend(
        frameon=False,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.16),
        ncol=2,
        columnspacing=1.0,
        handletextpad=0.4,
    )

    ranks = np.arange(1, len(components) + 1)
    axes[1].plot(ranks, components, color=COLORS["blue"], linewidth=1.5)
    axes[1].scatter(ranks[:10], components[:10], color=COLORS["blue"], s=12, zorder=3)
    axes[1].axhline(1, color=COLORS["gray"], linewidth=0.8, linestyle="--")
    axes[1].set_xlabel("Connected-component rank")
    axes[1].set_ylabel("Facilities in component")
    axes[1].set_yscale("log")
    axes[1].set_yticks([1, 10, 100], ["1", "10", "100"])
    axes[1].set_title(rf"$\bf{{b}}$  Component sizes (n={len(components)})", loc="left")
    axes[1].text(0.98, 0.95, f"Largest = {components[0]}\nIsolates = {components.count(1)}", transform=axes[1].transAxes, ha="right", va="top")

    fig.subplots_adjust(wspace=0.34, bottom=0.19)
    save(fig, "fig3_topology_quality")


def draw_heatmap(ax: plt.Axes, table: pd.DataFrame, title: str, cmap: str) -> None:
    x_edges = np.arange(table.shape[1] + 1) - 0.5
    y_edges = np.arange(table.shape[0] + 1) - 0.5
    image = ax.pcolormesh(x_edges, y_edges, table.values, cmap=cmap, shading="flat")
    ax.set_xticks(np.arange(len(table.columns)), [f"{float(x):g}" for x in table.columns])
    ax.set_yticks(np.arange(len(table.index)), [f"{int(x)}" for x in table.index])
    ax.set_xlabel("Endpoint snap (m)")
    ax.set_ylabel("Facility buffer (m)")
    ax.set_title(title)
    ax.set_xlim(-0.5, table.shape[1] - 0.5)
    ax.set_ylim(table.shape[0] - 0.5, -0.5)
    midpoint = (np.nanmin(table.values) + np.nanmax(table.values)) / 2
    for i in range(table.shape[0]):
        for j in range(table.shape[1]):
            value = int(table.iloc[i, j])
            ax.text(j, i, str(value), ha="center", va="center", fontsize=7, color="white" if value > midpoint else "black")


def figure4_sensitivity(core: Path) -> None:
    sweep = pd.read_csv(core / "at_paper_logic_parameter_sweep.csv")
    selected = sweep[(sweep["facility_node_set"] == "B") & (sweep["merge_mode"] == "voltage-status-aware")]
    selected = selected.sort_values(["facility_buffer_m", "endpoint_snap_threshold_m"])

    fig, axes = plt.subplots(2, 2, figsize=(7.2, 6.0))
    metrics = [
        ("inter_facility_circuits", "Inter-facility circuits", "Blues"),
        ("largest_component_size", "Largest component", "Greens"),
        ("ambiguous", "Ambiguous circuits", "Oranges"),
    ]
    for ax, (column, title, cmap), label in zip(axes.flat[:3], metrics, ["a", "b", "c"]):
        table = selected.pivot(index="facility_buffer_m", columns="endpoint_snap_threshold_m", values=column)
        draw_heatmap(ax, table, title, cmap)
        ax.scatter([0], [1], s=70, marker="*", facecolors="none", edgecolors=COLORS["red"], linewidths=1.2)
        panel_label(ax, label)

    ax = axes[1, 1]
    bins = [0, 10, 20, 30, 40, 55]
    bin_labels = ["1-10", "11-20", "21-30", "31-40", "41-55"]
    bin_ids = pd.cut(sweep["largest_component_size"], bins=bins, labels=bin_labels, include_lowest=True)
    bin_colors = plt.cm.viridis(np.linspace(0.10, 0.90, len(bin_labels)))
    bin_markers = ["o", "s", "^", "D", "P"]
    for label, color, marker in zip(bin_labels, bin_colors, bin_markers):
        mask = bin_ids.eq(label)
        ax.scatter(
            sweep.loc[mask, "ambiguous"],
            sweep.loc[mask, "clean_branch_count"],
            color=color,
            marker=marker,
            s=18,
            alpha=0.68,
            edgecolors="none",
            label=f"LCC {label}",
        )
    chosen = sweep[sweep["recommended_yes_no"].astype(str).str.lower().eq("yes")].iloc[0]
    ax.scatter([chosen["ambiguous"]], [chosen["clean_branch_count"]], marker="*", s=120, color=COLORS["red"], label="Selected")
    ax.set_xlabel("Ambiguous circuits")
    ax.set_ylabel("Retained branches")
    ax.set_title("All 216 strategies")
    ax.legend(frameon=False, loc="lower right", fontsize=7.0, labelspacing=0.25)
    panel_label(ax, "d")

    fig.subplots_adjust(wspace=0.34, hspace=0.44)
    save(fig, "fig4_sensitivity_analysis")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--release-root",
        type=Path,
        default=ROOT / "data" / "releases" / "PT60-Candidate-v1.0.1",
        help="Extracted frozen release directory.",
    )
    args = parser.parse_args()
    release_root = args.release_root.resolve()
    core = release_root / "core_topology"
    inputs = [
        core / "at_paper_logic_summary.json",
        core / "at_paper_logic_graph.graphml",
        core / "at_interfacility_candidate_branches.csv",
        core / "at_paper_logic_parameter_sweep.csv",
    ]
    missing = [str(path) for path in inputs if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Frozen figure inputs are missing: {missing}")

    configure_style()
    figure2_reconstruction_funnel(core)
    figure3_topology_quality(core)
    figure4_sensitivity(core)

    outputs = [
        OUT / f"{stem}.{suffix}"
        for stem in ["fig2_reconstruction_funnel", "fig3_topology_quality", "fig4_sensitivity_analysis"]
        for suffix in ["pdf", "svg", "png"]
    ]
    report = {
        "release_root": str(release_root.relative_to(ROOT)),
        "inputs": [{"path": str(path.relative_to(ROOT)), "sha256": sha256(path)} for path in inputs],
        "outputs": [{"path": str(path.relative_to(ROOT)), "sha256": sha256(path)} for path in outputs],
        "figures": ["fig2_reconstruction_funnel", "fig3_topology_quality", "fig4_sensitivity_analysis"],
        "excluded_from_canonical_build": ["fig5_parameter_coverage", "fig6_dataset_architecture"],
    }
    report_path = OUT / "main_quantitative_figure_build.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Generated 3 quantitative figures from {release_root}")
    print(f"Wrote provenance report to {report_path}")


if __name__ == "__main__":
    main()
