from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import networkx as nx
import pandas as pd

import config
from utils import ensure_directories, markdown_table, utc_now, write_json, write_text

OUT = config.PROCESSED_DIR / "dataset_release_stage3"
REPORTS = config.REPORTS_DIR
VALIDATION_REPORT = REPORTS / "93_stage3_graph_export_validation.md"

REQUIRED_FILES = {
    "graph_nodes": OUT / "pt_stage3_graph_nodes.csv",
    "graph_edges": OUT / "pt_stage3_graph_edges.csv",
    "generator_nodes": OUT / "pt_stage3_generator_nodes.csv",
    "generator_bus_links": OUT / "pt_stage3_generator_bus_links.csv",
    "line_risk_samples": OUT / "pt_stage3_line_risk_samples.csv",
    "generator_risk_samples": OUT / "pt_stage3_generator_risk_samples.csv",
    "graphml": OUT / "pt_stage3_graph.graphml",
    "manifest": OUT / "pt_stage3_manifest.json",
}

REQUIRED_COLUMNS = {
    "graph_nodes": {"node_id", "node_type", "bus_id", "prov_publication_allowed", "prov_diagnostic_only"},
    "graph_edges": {"edge_id", "edge_type", "source_node_id", "target_node_id", "line_id", "line_name", "policy_class", "prov_diagnostic_only"},
    "generator_nodes": {"node_id", "node_type", "generator_id", "bus_id", "dispatch_proxy_class", "cost_class", "prov_diagnostic_only"},
    "generator_bus_links": {"edge_id", "edge_type", "source_node_id", "target_node_id", "generator_id", "bus_id", "publication_allowed", "diagnostic_only", "prov_publication_allowed", "prov_diagnostic_only", "prov_source_scenario_id"},
    "line_risk_samples": {"sample_id", "sample_type", "line_id", "line_name", "edge_id", "source_node_id", "target_node_id", "metric_max_loading_percent", "top_congested_flag", "publication_allowed", "diagnostic_only"},
    "generator_risk_samples": {"sample_id", "sample_type", "generator_id", "bus_id", "node_id", "attached_bus_node_id", "dispatch_mw", "top_dispatch_flag", "publication_allowed", "diagnostic_only"},
}


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)


def load_manifest() -> dict[str, Any]:
    with (OUT / "pt_stage3_manifest.json").open("r", encoding="utf-8") as handle:
        return json.load(handle)


def add_error(errors: list[str], message: str) -> None:
    errors.append(message)


def check_required_files(errors: list[str]) -> None:
    for name, path in REQUIRED_FILES.items():
        if not path.exists():
            add_error(errors, f"Missing required stage-3 file: {name} -> {path}")


def check_columns(name: str, df: pd.DataFrame, errors: list[str]) -> None:
    missing = sorted(REQUIRED_COLUMNS.get(name, set()) - set(df.columns))
    if missing:
        add_error(errors, f"{name} missing required columns: {', '.join(missing)}")


def check_unique(df: pd.DataFrame, column: str, label: str, errors: list[str]) -> int:
    duplicates = int(df[column].astype(str).duplicated().sum()) if column in df.columns else 0
    if duplicates:
        add_error(errors, f"{label} has {duplicates} duplicate key rows in column {column}")
    return duplicates


def write_report(summary: dict[str, Any]) -> None:
    summary_rows = [{
        "status": summary["status"],
        "error_count": len(summary["errors"]),
        "warning_count": len(summary["warnings"]),
    }]
    check_rows = [{"check": key, "value": value} for key, value in summary["check_counts"].items()]
    warning_rows = [{"warning": warning} for warning in summary["warnings"]]
    error_rows = [{"error": error} for error in summary["errors"]]
    text = [
        "# 93 Stage-3 Graph Export Validation",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "Scope: validate the packaged stage-3 graph export, graph-linked sample tables, GraphML parity, referential integrity, and boundary flags.",
        "",
        "## Summary",
        "",
        markdown_table(summary_rows, ["status", "error_count", "warning_count"]),
        "",
        "## Check counts",
        "",
        markdown_table(check_rows, ["check", "value"]),
        "",
        "## Warnings",
        "",
        markdown_table(warning_rows, ["warning"]) if warning_rows else "_No rows._\n",
        "",
        "## Errors",
        "",
        markdown_table(error_rows, ["error"]) if error_rows else "_No rows._\n",
    ]
    write_text(VALIDATION_REPORT, "\n".join(text) + "\n")


def main() -> None:
    ensure_directories()
    errors: list[str] = []
    warnings: list[str] = []
    check_counts: dict[str, Any] = {}

    check_required_files(errors)
    if errors:
        summary = {
            "generated_at": utc_now(),
            "status": "FAIL",
            "errors": errors,
            "warnings": warnings,
            "check_counts": check_counts,
        }
        write_json(OUT / "pt_stage3_validation_summary.json", summary)
        write_report(summary)
        raise RuntimeError("Stage-3 graph validation failed before reading packaged files.")

    graph_nodes = read_csv(REQUIRED_FILES["graph_nodes"])
    graph_edges = read_csv(REQUIRED_FILES["graph_edges"])
    generator_nodes = read_csv(REQUIRED_FILES["generator_nodes"])
    generator_bus_links = read_csv(REQUIRED_FILES["generator_bus_links"])
    line_samples = read_csv(REQUIRED_FILES["line_risk_samples"])
    generator_samples = read_csv(REQUIRED_FILES["generator_risk_samples"])
    manifest = load_manifest()

    for name, df in {
        "graph_nodes": graph_nodes,
        "graph_edges": graph_edges,
        "generator_nodes": generator_nodes,
        "generator_bus_links": generator_bus_links,
        "line_risk_samples": line_samples,
        "generator_risk_samples": generator_samples,
    }.items():
        check_columns(name, df, errors)

    check_counts["graph_node_duplicates"] = check_unique(graph_nodes, "node_id", "graph_nodes", errors)
    check_counts["graph_edge_duplicates"] = check_unique(graph_edges, "edge_id", "graph_edges", errors)
    check_counts["generator_node_duplicates"] = check_unique(generator_nodes, "node_id", "generator_nodes", errors)
    check_counts["generator_bus_link_duplicates"] = check_unique(generator_bus_links, "edge_id", "generator_bus_links", errors)
    check_counts["line_sample_duplicates"] = check_unique(line_samples, "sample_id", "line_risk_samples", errors)
    check_counts["generator_sample_duplicates"] = check_unique(generator_samples, "sample_id", "generator_risk_samples", errors)

    graph_node_ids = set(graph_nodes["node_id"].astype(str))
    graph_edge_ids = set(graph_edges["edge_id"].astype(str))
    generator_node_ids = set(generator_nodes["node_id"].astype(str))
    generator_ids = set(generator_nodes["generator_id"].astype(str))
    bus_ids = set(graph_nodes["bus_id"].astype(str))

    edge_source_missing = int((~graph_edges["source_node_id"].astype(str).isin(graph_node_ids)).sum())
    edge_target_missing = int((~graph_edges["target_node_id"].astype(str).isin(graph_node_ids)).sum())
    if edge_source_missing:
        add_error(errors, f"graph_edges has {edge_source_missing} rows with source_node_id not present in graph_nodes.node_id")
    if edge_target_missing:
        add_error(errors, f"graph_edges has {edge_target_missing} rows with target_node_id not present in graph_nodes.node_id")
    check_counts["edge_source_node_missing"] = edge_source_missing
    check_counts["edge_target_node_missing"] = edge_target_missing

    line_sample_edge_missing = int((~line_samples["edge_id"].astype(str).isin(graph_edge_ids)).sum())
    line_sample_source_missing = int((~line_samples["source_node_id"].astype(str).isin(graph_node_ids)).sum())
    line_sample_target_missing = int((~line_samples["target_node_id"].astype(str).isin(graph_node_ids)).sum())
    if line_sample_edge_missing:
        add_error(errors, f"line_risk_samples has {line_sample_edge_missing} rows with edge_id not present in graph_edges.edge_id")
    if line_sample_source_missing:
        add_error(errors, f"line_risk_samples has {line_sample_source_missing} rows with source_node_id not present in graph_nodes.node_id")
    if line_sample_target_missing:
        add_error(errors, f"line_risk_samples has {line_sample_target_missing} rows with target_node_id not present in graph_nodes.node_id")
    check_counts["line_sample_missing_edge_refs"] = line_sample_edge_missing
    check_counts["line_sample_missing_source_refs"] = line_sample_source_missing
    check_counts["line_sample_missing_target_refs"] = line_sample_target_missing

    line_sample_multimap = int(line_samples[["line_id", "line_name", "edge_id"]].drop_duplicates().groupby(["line_id", "line_name"]).size().gt(1).sum())
    if line_sample_multimap:
        add_error(errors, f"line_risk_samples has {line_sample_multimap} line_id/line_name keys mapping to multiple edge_ids")
    check_counts["line_sample_multimap_keys"] = line_sample_multimap

    generator_sample_node_missing = int((~generator_samples["node_id"].astype(str).isin(generator_node_ids)).sum())
    generator_sample_bus_missing = int((~generator_samples["attached_bus_node_id"].astype(str).isin(graph_node_ids)).sum())
    generator_sample_generator_missing = int((~generator_samples["generator_id"].astype(str).isin(generator_ids)).sum())
    if generator_sample_node_missing:
        add_error(errors, f"generator_risk_samples has {generator_sample_node_missing} rows with node_id not present in generator_nodes.node_id")
    if generator_sample_bus_missing:
        add_error(errors, f"generator_risk_samples has {generator_sample_bus_missing} rows with attached_bus_node_id not present in graph_nodes.node_id")
    if generator_sample_generator_missing:
        add_error(errors, f"generator_risk_samples has {generator_sample_generator_missing} rows with generator_id not present in generator_nodes.generator_id")
    check_counts["generator_sample_missing_node_refs"] = generator_sample_node_missing
    check_counts["generator_sample_missing_bus_refs"] = generator_sample_bus_missing
    check_counts["generator_sample_missing_generator_refs"] = generator_sample_generator_missing

    generator_link_source_missing = int((~generator_bus_links["source_node_id"].astype(str).isin(generator_node_ids)).sum())
    generator_link_target_missing = int((~generator_bus_links["target_node_id"].astype(str).isin(graph_node_ids)).sum())
    if generator_link_source_missing:
        add_error(errors, f"generator_bus_links has {generator_link_source_missing} rows with source_node_id not present in generator_nodes.node_id")
    if generator_link_target_missing:
        add_error(errors, f"generator_bus_links has {generator_link_target_missing} rows with target_node_id not present in graph_nodes.node_id")
    check_counts["generator_link_missing_source_refs"] = generator_link_source_missing
    check_counts["generator_link_missing_target_refs"] = generator_link_target_missing

    graph_node_provenance_missing = int(graph_nodes[["prov_publication_allowed", "prov_diagnostic_only", "prov_source_scenario_id"]].isna().any(axis=1).sum())
    graph_edge_provenance_missing = int(graph_edges[["prov_publication_allowed", "prov_diagnostic_only", "prov_source_scenario_id"]].isna().any(axis=1).sum())
    generator_node_provenance_missing = int(generator_nodes[["prov_publication_allowed", "prov_diagnostic_only", "prov_source_scenario_id"]].isna().any(axis=1).sum())
    generator_link_provenance_missing = int(generator_bus_links[["prov_publication_allowed", "prov_diagnostic_only", "prov_source_scenario_id"]].isna().any(axis=1).sum())
    if graph_node_provenance_missing:
        add_error(errors, f"graph_nodes has {graph_node_provenance_missing} rows missing provenance join fields")
    if graph_edge_provenance_missing:
        add_error(errors, f"graph_edges has {graph_edge_provenance_missing} rows missing provenance join fields")
    if generator_node_provenance_missing:
        add_error(errors, f"generator_nodes has {generator_node_provenance_missing} rows missing provenance join fields")
    if generator_link_provenance_missing:
        add_error(errors, f"generator_bus_links has {generator_link_provenance_missing} rows missing provenance join fields")
    check_counts["graph_node_missing_provenance_rows"] = graph_node_provenance_missing
    check_counts["graph_edge_missing_provenance_rows"] = graph_edge_provenance_missing
    check_counts["generator_node_missing_provenance_rows"] = generator_node_provenance_missing
    check_counts["generator_link_missing_provenance_rows"] = generator_link_provenance_missing

    negative_line_loading = int((pd.to_numeric(line_samples["metric_max_loading_percent"], errors="coerce") < 0).sum())
    invalid_top_dispatch = int(
        ((generator_samples["top_dispatch_flag"].astype(str).str.lower() == "true")
         & (pd.to_numeric(generator_samples["dispatch_mw"], errors="coerce") <= 0)).sum()
    )
    if negative_line_loading:
        add_error(errors, f"line_risk_samples contains {negative_line_loading} rows with negative metric_max_loading_percent")
    if invalid_top_dispatch:
        add_error(errors, f"generator_risk_samples has {invalid_top_dispatch} top_dispatch_flag rows with non-positive dispatch_mw")
    check_counts["negative_line_loading_rows"] = negative_line_loading
    check_counts["invalid_top_dispatch_rows"] = invalid_top_dispatch

    invalid_generator_bus_alignment = int((generator_samples["attached_bus_node_id"].astype(str) != generator_samples["bus_id"].astype(str)).sum())
    invalid_graph_bus_alignment = int((~graph_nodes["bus_id"].astype(str).isin(graph_node_ids)).sum())
    if invalid_generator_bus_alignment:
        add_error(errors, f"generator_risk_samples has {invalid_generator_bus_alignment} rows where attached_bus_node_id does not match bus_id")
    if invalid_graph_bus_alignment:
        add_error(errors, f"graph_nodes has {invalid_graph_bus_alignment} rows where bus_id is not represented by node_id")
    check_counts["generator_sample_bus_alignment_mismatches"] = invalid_generator_bus_alignment
    check_counts["graph_node_bus_alignment_mismatches"] = invalid_graph_bus_alignment
    check_counts["graph_bus_id_count"] = len(bus_ids)

    graphml = nx.read_graphml(REQUIRED_FILES["graphml"])
    graphml_node_count = graphml.number_of_nodes()
    graphml_edge_count = graphml.number_of_edges()
    if graphml_node_count != len(graph_nodes):
        add_error(errors, f"GraphML node count {graphml_node_count} does not match graph_nodes row count {len(graph_nodes)}")
    if graphml_edge_count != len(graph_edges):
        add_error(errors, f"GraphML edge count {graphml_edge_count} does not match graph_edges row count {len(graph_edges)}")
    check_counts["graphml_node_count"] = graphml_node_count
    check_counts["graphml_edge_count"] = graphml_edge_count

    manifest_row_counts = manifest.get("table_row_counts", {})
    expected_row_counts = {
        "pt_stage3_graph_nodes.csv": int(len(graph_nodes)),
        "pt_stage3_graph_edges.csv": int(len(graph_edges)),
        "pt_stage3_generator_nodes.csv": int(len(generator_nodes)),
        "pt_stage3_generator_bus_links.csv": int(len(generator_bus_links)),
        "pt_stage3_line_risk_samples.csv": int(len(line_samples)),
        "pt_stage3_generator_risk_samples.csv": int(len(generator_samples)),
    }
    manifest_row_count_mismatches = 0
    for name, expected in expected_row_counts.items():
        observed = manifest_row_counts.get(name)
        if observed != expected:
            manifest_row_count_mismatches += 1
            add_error(errors, f"Manifest table_row_counts mismatch for {name}: expected {expected}, observed {observed}")
    check_counts["manifest_row_count_mismatches"] = manifest_row_count_mismatches

    if bool(manifest.get("publication_allowed", True)):
        add_error(errors, "Manifest publication_allowed should remain false")
    if not bool(manifest.get("diagnostic_only", False)):
        add_error(errors, "Manifest diagnostic_only should remain true")
    if bool(manifest.get("operator_grade_ready", True)):
        add_error(errors, "Manifest operator_grade_ready should remain false")
    if bool(manifest.get("ml_ready", True)):
        add_error(errors, "Manifest ml_ready should remain false")

    summary = {
        "generated_at": utc_now(),
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "warnings": warnings,
        "check_counts": check_counts,
    }
    write_json(OUT / "pt_stage3_validation_summary.json", summary)
    write_report(summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    if errors:
        raise RuntimeError("Stage-3 graph validation failed.")


if __name__ == "__main__":
    main()
