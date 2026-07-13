from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import networkx as nx
import pandas as pd

import config
from utils import ensure_directories, markdown_table, utc_now, write_json, write_text

ROOT = config.ROOT_DIR
PROCESSED = config.PROCESSED_DIR
REPORTS = config.REPORTS_DIR
STAGE2 = PROCESSED / "dataset_release_stage2"
OUT = PROCESSED / "dataset_release_stage3"
RELEASE_REPORT = REPORTS / "92_stage3_graph_export_release_note.md"

REQUIRED_INPUTS = [
    STAGE2 / "pt_stage2_bus_features.csv",
    STAGE2 / "pt_stage2_line_features.csv",
    STAGE2 / "pt_stage2_generator_features.csv",
    STAGE2 / "pt_stage2_line_risk_targets.csv",
    STAGE2 / "pt_stage2_generator_risk_targets.csv",
    STAGE2 / "pt_stage2_provenance_flags.csv",
    STAGE2 / "pt_stage2_manifest.json",
]

DATASET_ID = "pt_grid_benchmark_stage3_graph"
RELEASE_ID = "pt_grid_benchmark_stage3_graph_v1"
SCHEMA_VERSION = "1.0"


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def require_inputs() -> None:
    missing = [str(path) for path in REQUIRED_INPUTS if not path.exists()]
    if missing:
        raise RuntimeError("Fail-closed: required stage-3 inputs are missing:\n- " + "\n- ".join(missing))


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if pd.isna(value):
        return False
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def load_stage2() -> dict[str, Any]:
    return {
        "bus_features": read_csv(STAGE2 / "pt_stage2_bus_features.csv"),
        "line_features": read_csv(STAGE2 / "pt_stage2_line_features.csv"),
        "generator_features": read_csv(STAGE2 / "pt_stage2_generator_features.csv"),
        "line_targets": read_csv(STAGE2 / "pt_stage2_line_risk_targets.csv"),
        "generator_targets": read_csv(STAGE2 / "pt_stage2_generator_risk_targets.csv"),
        "provenance": read_csv(STAGE2 / "pt_stage2_provenance_flags.csv"),
        "manifest": read_json(STAGE2 / "pt_stage2_manifest.json"),
    }


def prepare_frames(stage2: dict[str, Any]) -> dict[str, Any]:
    bus_features = stage2["bus_features"].copy()
    line_features = stage2["line_features"].copy()
    generator_features = stage2["generator_features"].copy()
    line_targets = stage2["line_targets"].copy()
    generator_targets = stage2["generator_targets"].copy()
    provenance = stage2["provenance"].copy()

    for frame, columns in [
        (bus_features, ["bus_id"]),
        (line_features, ["line_id", "line_name", "from_bus", "to_bus"]),
        (generator_features, ["generator_id", "bus_id"]),
        (line_targets, ["line_id", "line_name", "from_bus", "to_bus"]),
        (generator_targets, ["generator_id", "bus_id"]),
        (provenance, ["entity_type", "entity_id"]),
    ]:
        for column in columns:
            frame[column] = frame[column].astype(str)

    return {
        **stage2,
        "bus_features": bus_features,
        "line_features": line_features,
        "generator_features": generator_features,
        "line_targets": line_targets,
        "generator_targets": generator_targets,
        "provenance": provenance,
    }


def build_graph_nodes(stage2: dict[str, Any]) -> pd.DataFrame:
    buses = stage2["bus_features"].copy()
    bus_prov = stage2["provenance"][stage2["provenance"]["entity_type"] == "bus"].copy()
    bus_prov = bus_prov.rename(columns={
        "entity_id": "bus_id",
        "publication_allowed": "prov_publication_allowed",
        "diagnostic_only": "prov_diagnostic_only",
        "source_scenario_id": "prov_source_scenario_id",
        "trace_source_ids": "prov_trace_source_ids",
        "assumption_ids": "prov_assumption_ids",
    })
    out = buses.merge(bus_prov[["bus_id", "prov_publication_allowed", "prov_diagnostic_only", "prov_source_scenario_id", "prov_trace_source_ids", "prov_assumption_ids"]], on="bus_id", how="left")
    out.insert(0, "node_id", out["bus_id"].astype(str))
    out.insert(1, "node_type", "bus")
    for column in ["in_service", "has_policy_governed_incident_line", "has_parallel_equivalent_required_incident_line", "prov_publication_allowed", "prov_diagnostic_only"]:
        if column in out.columns:
            out[column] = out[column].apply(as_bool)
    return out


def build_graph_edges(stage2: dict[str, Any]) -> pd.DataFrame:
    lines = stage2["line_features"].copy()
    line_prov = stage2["provenance"][stage2["provenance"]["entity_type"] == "line"].copy()
    line_prov = line_prov.rename(columns={
        "entity_id": "line_id",
        "publication_allowed": "prov_publication_allowed",
        "diagnostic_only": "prov_diagnostic_only",
        "source_scenario_id": "prov_source_scenario_id",
        "trace_source_ids": "prov_trace_source_ids",
        "assumption_ids": "prov_assumption_ids",
    })
    out = lines.merge(line_prov[["line_id", "prov_publication_allowed", "prov_diagnostic_only", "prov_source_scenario_id", "prov_trace_source_ids", "prov_assumption_ids"]], on="line_id", how="left")
    out.insert(0, "edge_id", out["line_id"].astype(str))
    out.insert(1, "edge_type", "line")
    out.insert(2, "source_node_id", out["from_bus"].astype(str))
    out.insert(3, "target_node_id", out["to_bus"].astype(str))
    for column in ["in_service", "publication_allowed", "repeated_bottleneck", "is_mixed_corridor", "is_policy_weighted_mixed", "is_parallel_equivalent_required", "prov_publication_allowed", "prov_diagnostic_only"]:
        if column in out.columns:
            out[column] = out[column].apply(as_bool)
    return out


def build_generator_nodes(stage2: dict[str, Any]) -> pd.DataFrame:
    generators = stage2["generator_features"].copy()
    gen_prov = stage2["provenance"][stage2["provenance"]["entity_type"] == "generator"].copy()
    gen_prov = gen_prov.rename(columns={
        "entity_id": "generator_id",
        "publication_allowed": "prov_publication_allowed",
        "diagnostic_only": "prov_diagnostic_only",
        "source_scenario_id": "prov_source_scenario_id",
        "trace_source_ids": "prov_trace_source_ids",
        "assumption_ids": "prov_assumption_ids",
    })
    out = generators.merge(gen_prov[["generator_id", "prov_publication_allowed", "prov_diagnostic_only", "prov_source_scenario_id", "prov_trace_source_ids", "prov_assumption_ids"]], on="generator_id", how="left")
    out.insert(0, "node_id", out["generator_id"].astype(str))
    out.insert(1, "node_type", "generator")
    for column in ["must_run", "curtailable", "benchmark_usable", "publication_allowed", "is_dispatchable_proxy", "is_import_interface_proxy", "prov_publication_allowed", "prov_diagnostic_only"]:
        if column in out.columns:
            out[column] = out[column].apply(as_bool)
    return out


def build_generator_bus_links(stage2: dict[str, Any]) -> pd.DataFrame:
    generators = stage2["generator_features"].copy()
    gen_prov = stage2["provenance"][stage2["provenance"]["entity_type"] == "generator"].copy()
    gen_prov = gen_prov.rename(columns={
        "entity_id": "generator_id",
        "publication_allowed": "prov_publication_allowed",
        "diagnostic_only": "prov_diagnostic_only",
        "source_scenario_id": "prov_source_scenario_id",
        "trace_source_ids": "prov_trace_source_ids",
        "assumption_ids": "prov_assumption_ids",
    })
    out = generators.merge(
        gen_prov[[
            "generator_id",
            "prov_publication_allowed",
            "prov_diagnostic_only",
            "prov_source_scenario_id",
            "prov_trace_source_ids",
            "prov_assumption_ids",
        ]],
        on="generator_id",
        how="left",
    )
    out = pd.DataFrame({
        "edge_id": [f"GENLINK_{generator_id}" for generator_id in out["generator_id"].astype(str)],
        "edge_type": "generator_attached_to_bus",
        "source_node_id": out["generator_id"].astype(str),
        "target_node_id": out["bus_id"].astype(str),
        "generator_id": out["generator_id"].astype(str),
        "bus_id": out["bus_id"].astype(str),
        "dispatch_proxy_class": out["dispatch_proxy_class"],
        "publication_allowed": out["publication_allowed"].apply(as_bool),
        "diagnostic_only": True,
        "prov_publication_allowed": out["prov_publication_allowed"].apply(as_bool),
        "prov_diagnostic_only": out["prov_diagnostic_only"].apply(as_bool),
        "prov_source_scenario_id": out["prov_source_scenario_id"],
        "prov_trace_source_ids": out["prov_trace_source_ids"],
        "prov_assumption_ids": out["prov_assumption_ids"],
    })
    return out


def build_line_risk_samples(stage2: dict[str, Any], graph_edges: pd.DataFrame) -> pd.DataFrame:
    line_targets = stage2["line_targets"].copy()
    edge_lookup = graph_edges[["edge_id", "line_id", "line_name", "source_node_id", "target_node_id", "policy_class", "repeated_bottleneck", "publication_allowed", "prov_diagnostic_only"]].copy()
    out = line_targets.merge(edge_lookup, on=["line_id", "line_name"], how="left")
    out.insert(
        0,
        "sample_id",
        out.apply(
            lambda row: f"line|{row['scenario_family']}|{row['variant_id']}|{row['line_id']}|{row['scenario_value']}",
            axis=1,
        ),
    )
    out.insert(1, "sample_type", "line_risk")
    out["edge_id"] = out["edge_id"].astype(str)
    out["publication_allowed"] = out["publication_allowed"].apply(as_bool)
    out["diagnostic_only"] = out["prov_diagnostic_only"].apply(as_bool)
    out = out.rename(columns={"source_node_id": "source_node_id", "target_node_id": "target_node_id"})
    return out


def build_generator_risk_samples(stage2: dict[str, Any], generator_nodes: pd.DataFrame) -> pd.DataFrame:
    generator_targets = stage2["generator_targets"].copy()
    node_lookup = generator_nodes[["node_id", "generator_id", "bus_id", "publication_allowed", "prov_diagnostic_only", "dispatch_proxy_class", "cost_class"]].copy()
    out = generator_targets.merge(node_lookup, on=["generator_id", "bus_id", "dispatch_proxy_class", "cost_class"], how="left", suffixes=("", "_node"))
    out.insert(0, "sample_id", out.apply(lambda row: f"generator|{row['scenario_family']}|{row['variant_id']}|{row['generator_id']}", axis=1))
    out.insert(1, "sample_type", "generator_risk")
    out["attached_bus_node_id"] = out["bus_id"].astype(str)
    out["publication_allowed"] = out["publication_allowed"].apply(as_bool)
    out["diagnostic_only"] = out["prov_diagnostic_only"].apply(as_bool)
    return out


def graphml_safe(value: Any) -> Any:
    if pd.isna(value):
        return ""
    if isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def write_graphml(nodes: pd.DataFrame, edges: pd.DataFrame) -> None:
    graph = nx.MultiGraph()
    for _, row in nodes.iterrows():
        attrs = {column: graphml_safe(row[column]) for column in nodes.columns if column not in {"node_id"}}
        graph.add_node(str(row["node_id"]), **attrs)
    for _, row in edges.iterrows():
        attrs = {column: graphml_safe(row[column]) for column in edges.columns if column not in {"edge_id", "source_node_id", "target_node_id"}}
        graph.add_edge(str(row["source_node_id"]), str(row["target_node_id"]), key=str(row["edge_id"]), **attrs)
    nx.write_graphml(graph, OUT / "pt_stage3_graph.graphml")


def build_manifest(row_counts: dict[str, int], stage2_manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "generated_at": utc_now(),
        "dataset_id": DATASET_ID,
        "release_id": RELEASE_ID,
        "schema_version": SCHEMA_VERSION,
        "builder_script": "src/build_portuguese_stage3_graph_dataset.py",
        "release_scope": "graph export and graph-linked sample indexing layer for later SSL and risk work; no ML included",
        "upstream_stage2_release_id": stage2_manifest.get("release_id"),
        "benchmark_freeze": stage2_manifest.get("benchmark_freeze", {}),
        "source_artifacts": [str(path.relative_to(ROOT)) for path in REQUIRED_INPUTS],
        "table_row_counts": row_counts,
        "publication_allowed": False,
        "diagnostic_only": True,
        "operator_grade_ready": False,
        "ml_ready": False,
        "downstream_intended_use": [
            "graph export for downstream adapters",
            "graph-linked line risk sample indexing",
            "graph-linked generator risk sample indexing",
        ],
        "excluded_scope": [
            "ML model training",
            "train/validation/test split generation",
            "framework-specific tensor exports",
            "graph augmentations",
            "AC OPF products",
        ],
        "caveats": [
            "Canonical GraphML export is homogeneous bus-line only.",
            "Generator graph structures are exported as sidecar tables.",
            "Scenario-conditioned supervision remains in sample tables, not duplicated as graph edges.",
        ],
    }


def write_release_report(manifest: dict[str, Any], row_counts: dict[str, int]) -> None:
    summary_rows = [{"table": key, "rows": value} for key, value in row_counts.items()]
    text = [
        "# 92 Stage-3 Graph Export Release Note",
        "",
        f"Generated: {manifest['generated_at']}",
        "",
        "Scope: package the validated stage-2 Portuguese benchmark-derived feature/target layer into a graph export and graph-linked sample-index layer for later SSL and risk work, without doing machine learning.",
        "",
        "## Release identity",
        "",
        f"- dataset id: `{manifest['dataset_id']}`",
        f"- release id: `{manifest['release_id']}`",
        f"- upstream stage-2 release id: `{manifest['upstream_stage2_release_id']}`",
        "",
        "## Included outputs",
        "",
        markdown_table(summary_rows, ["table", "rows"]),
        "",
        "## What stage-3 adds",
        "",
        "- canonical graph node and edge exports",
        "- homogeneous bus-line GraphML export",
        "- generator sidecar node/link tables for later heterogeneous graph work",
        "- graph-linked line and generator sample-index tables",
        "",
        "## Boundary",
        "",
        "- diagnostic-only",
        "- not operator-grade",
        "- no ML training or splits",
        "- no framework-specific tensor export",
    ]
    write_text(RELEASE_REPORT, "\n".join(text) + "\n")


def main() -> None:
    ensure_directories()
    require_inputs()
    OUT.mkdir(parents=True, exist_ok=True)

    stage2 = prepare_frames(load_stage2())
    graph_nodes = build_graph_nodes(stage2)
    graph_edges = build_graph_edges(stage2)
    generator_nodes = build_generator_nodes(stage2)
    generator_bus_links = build_generator_bus_links(stage2)
    line_samples = build_line_risk_samples(stage2, graph_edges)
    generator_samples = build_generator_risk_samples(stage2, generator_nodes)

    graph_nodes.to_csv(OUT / "pt_stage3_graph_nodes.csv", index=False)
    graph_edges.to_csv(OUT / "pt_stage3_graph_edges.csv", index=False)
    generator_nodes.to_csv(OUT / "pt_stage3_generator_nodes.csv", index=False)
    generator_bus_links.to_csv(OUT / "pt_stage3_generator_bus_links.csv", index=False)
    line_samples.to_csv(OUT / "pt_stage3_line_risk_samples.csv", index=False)
    generator_samples.to_csv(OUT / "pt_stage3_generator_risk_samples.csv", index=False)
    write_graphml(graph_nodes, graph_edges)

    row_counts = {
        "pt_stage3_graph_nodes.csv": int(len(graph_nodes)),
        "pt_stage3_graph_edges.csv": int(len(graph_edges)),
        "pt_stage3_generator_nodes.csv": int(len(generator_nodes)),
        "pt_stage3_generator_bus_links.csv": int(len(generator_bus_links)),
        "pt_stage3_line_risk_samples.csv": int(len(line_samples)),
        "pt_stage3_generator_risk_samples.csv": int(len(generator_samples)),
    }
    manifest = build_manifest(row_counts, stage2["manifest"])
    write_json(OUT / "pt_stage3_manifest.json", manifest)
    write_release_report(manifest, row_counts)
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
