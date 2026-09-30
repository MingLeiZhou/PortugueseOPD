#!/usr/bin/env python3
"""Build switch/state/contingency objects for the all-voltage equivalent graph.

Each switch is an explicit branch-gate simulation abstraction attached to the
upstream terminal of one existing branch.  It is not an inferred operator
breaker or a claim about station single-line diagrams.  A bridge-tree audit
computes the load that loses every modeled source when a switch is opened.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
import json
from pathlib import Path

import duckdb
import networkx as nx
import pyarrow as pa

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
VERSION = "ALL_VOLTAGE_SWITCHABLE_BASE_V1"
SNAPSHOT = "SWITCH_SNAPSHOT_ALL_CLOSED_V1"


class UnionFind:
    def __init__(self, values: list[str]):
        self.parent = {x: x for x in values}
        self.rank = {x: 0 for x in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, a: str, b: str) -> None:
        a, b = self.find(a), self.find(b)
        if a == b:
            return
        if self.rank[a] < self.rank[b]:
            a, b = b, a
        self.parent[b] = a
        if self.rank[a] == self.rank[b]:
            self.rank[a] += 1


def canonical(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB)
    args = parser.parse_args()

    with duckdb.connect(str(args.database), read_only=True) as con:
        buses = [r[0] for r in con.execute("SELECT bus_id FROM equivalent.buses ORDER BY bus_id").fetchall()]
        branches = con.execute("""SELECT branch_id,from_bus,to_bus,from_kv,to_kv,
            upper(asset_kind),source_ref,evidence_status
            FROM equivalent.branches ORDER BY branch_id""").fetchall()
        sources = {r[0] for r in con.execute("""SELECT DISTINCT bus_id
            FROM equivalent.base_generators WHERE bus_id IS NOT NULL
            UNION SELECT hv_bus_id FROM candidate.station_sources WHERE hv_bus_id IS NOT NULL
            UNION SELECT bus_id FROM equivalent.buses
              WHERE evidence_status='DIRECT_EREDES' AND voltage_kv>=60""").fetchall()}
        load_by_bus = dict(con.execute("""SELECT t.connectivity_node_id,count(*)
            FROM model.resource r JOIN model.terminal t ON r.resource_id=t.object_id
            WHERE r.resource_type IN ('LOAD','MV_LOAD') GROUP BY 1""").fetchall())

    pair_count = Counter(canonical(r[1], r[2]) for r in branches)
    graph = nx.Graph()
    graph.add_nodes_from(buses)
    graph.add_edges_from(pair_count)
    simple_bridges = {canonical(a, b) for a, b in nx.bridges(graph)}
    bridges = {pair for pair in simple_bridges if pair_count[pair] == 1}

    uf = UnionFind(buses)
    for a, b in pair_count:
        if canonical(a, b) not in bridges:
            uf.union(a, b)
    component_nodes: dict[str, list[str]] = defaultdict(list)
    for bus in buses:
        component_nodes[uf.find(bus)].append(bus)
    component_sources = {root: sum(bus in sources for bus in nodes)
                         for root, nodes in component_nodes.items()}
    component_loads = {root: sum(int(load_by_bus.get(bus, 0)) for bus in nodes)
                       for root, nodes in component_nodes.items()}
    tree: dict[str, list[str]] = defaultdict(list)
    bridge_components: dict[tuple[str, str], tuple[str, str]] = {}
    for a, b in bridges:
        ca, cb = uf.find(a), uf.find(b)
        tree[ca].append(cb); tree[cb].append(ca)
        bridge_components[(a, b)] = (ca, cb)

    impact_by_pair: dict[tuple[str, str], tuple[int | None, int, int, str]] = {}
    visited: set[str] = set()
    for start in component_nodes:
        if start in visited:
            continue
        parent = {start: None}
        order = []
        queue = [start]
        while queue:
            node = queue.pop()
            if node in visited:
                continue
            visited.add(node); order.append(node)
            for nxt in tree[node]:
                if nxt != parent.get(node) and nxt not in parent:
                    parent[nxt] = node; queue.append(nxt)
        subtree_sources = {node: component_sources[node] for node in order}
        subtree_loads = {node: component_loads[node] for node in order}
        for node in reversed(order):
            if parent[node] is not None:
                subtree_sources[parent[node]] += subtree_sources[node]
                subtree_loads[parent[node]] += subtree_loads[node]
        total_sources = subtree_sources[start]
        total_loads = subtree_loads[start]
        for child in order[1:]:
            par = parent[child]
            child_sources = subtree_sources[child]
            other_sources = total_sources - child_sources
            if child_sources == 0 and other_sources > 0:
                lost, status = subtree_loads[child], "SOURCE_FREE_CHILD_SIDE"
            elif other_sources == 0 and child_sources > 0:
                lost, status = total_loads - subtree_loads[child], "SOURCE_FREE_PARENT_SIDE"
            elif child_sources > 0 and other_sources > 0:
                lost, status = 0, "SOURCES_ON_BOTH_SIDES"
            else:
                lost, status = None, "BASE_COMPONENT_HAS_NO_SOURCE_CANDIDATE"
            # Find the unique physical bridge connecting these contracted nodes.
            # The total number of bridge edges is linear, so this reverse map is built below.
            impact_by_pair[(par, child)] = (lost, other_sources, child_sources, status)
            impact_by_pair[(child, par)] = (lost, child_sources, other_sources, status)

    criticality_rows = []
    for branch_id, from_bus, to_bus, from_kv, to_kv, asset_kind, source_ref, evidence in branches:
        pair = canonical(from_bus, to_bus)
        is_bridge = pair in bridges
        lost = None; side_a_sources = None; side_b_sources = None
        impact_status = "NON_BRIDGE_ALTERNATE_MODELED_PATH"
        if is_bridge:
            ca, cb = bridge_components[pair]
            lost, side_a_sources, side_b_sources, impact_status = impact_by_pair[(ca, cb)]
        switch_id = f"SWITCH:{branch_id}:UPSTREAM"
        criticality_rows.append((switch_id, branch_id, from_bus, to_bus,
                                 pair_count[pair], is_bridge, lost,
                                 side_a_sources, side_b_sources, impact_status,
                                 "GRAPH_DERIVED_FROM_ALL_CLOSED_EQUIVALENT_TOPOLOGY"))

    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS model")
        con.execute("CREATE SCHEMA IF NOT EXISTS operating")
        con.execute("CREATE SCHEMA IF NOT EXISTS scenario")
        con.execute("CREATE SCHEMA IF NOT EXISTS study")
        con.execute("""CREATE OR REPLACE TABLE model.switching_device AS
            SELECT 'SWITCH:'||branch_id||':UPSTREAM' AS switch_id,
                   'SOLVABLE_EQUIVALENT' AS network_layer,
                   CASE WHEN upper(asset_kind)='TRANSFORMER' OR from_kv>=60
                        THEN 'CIRCUIT_BREAKER' ELSE 'LOAD_BREAK_SWITCH' END AS switch_kind,
                   branch_id AS controlled_equipment_id,
                   'TERM:'||branch_id||':1' AS controlled_terminal_id,
                   from_bus AS upstream_connectivity_node_id,
                   from_kv AS rated_voltage_kv,false AS normal_open,
                   'CLOSED' AS normal_position,'BRANCH_IN_SERVICE_GATE' AS control_model,
                   source_ref,'SYNTHETIC_SWITCHING_ABSTRACTION_NOT_PUBLIC_ASSET' AS evidence_status
            FROM equivalent.branches""")
        con.execute("""CREATE OR REPLACE TABLE operating.switch_state_snapshot AS
            SELECT ? AS snapshot_id,? AS topology_version_id,switch_id,
                   normal_position AS position,NULL::VARCHAR AS effective_at_utc,
                   'SYNTHETIC_ALL_CLOSED_BASE_STATE' AS state_status,
                   false AS publicly_observed
            FROM model.switching_device""", [SNAPSHOT, VERSION])
        con.execute("""CREATE OR REPLACE TABLE scenario.contingency AS
            SELECT 'CONTINGENCY:N-1:'||switch_id AS contingency_id,
                   'N-1_BRANCH_ISOLATION' AS contingency_type,
                   controlled_equipment_id AS target_id,
                   'SYNTHETIC_ENUMERATION_NOT_HISTORICAL_EVENT' AS evidence_status
            FROM model.switching_device""")
        con.execute("""CREATE OR REPLACE TABLE scenario.contingency_action AS
            SELECT 'CONTINGENCY:N-1:'||switch_id AS contingency_id,switch_id,
                   1 AS sequence_number,'OPEN' AS target_position,
                   'BRANCH_OUT_OF_SERVICE' AS modeled_effect
            FROM model.switching_device""")
        names = ("switch_id","controlled_equipment_id","from_bus","to_bus",
                 "parallel_edge_count","is_graph_bridge","deenergized_load_count",
                 "side_a_source_count","side_b_source_count","impact_status","evidence_status")
        arrays = [pa.array([row[i] for row in criticality_rows]) for i in range(len(names))]
        criticality_arrow = pa.Table.from_arrays(arrays, names=names)
        con.register("criticality_arrow", criticality_arrow)
        con.execute("""CREATE OR REPLACE TABLE study.switching_branch_criticality AS
            SELECT * FROM criticality_arrow""")
        con.execute("DELETE FROM model.object_registry WHERE object_category='SWITCHING_DEVICE'")
        con.execute("""INSERT INTO model.object_registry
            SELECT switch_id,'SWITCHING_DEVICE',switch_kind,switch_id,
                   e.container_id,s.source_ref,s.evidence_status,s.network_layer
            FROM model.switching_device s
            JOIN model.conducting_equipment e
              ON s.controlled_equipment_id=e.equipment_id
             AND s.network_layer=e.network_layer""")
        con.execute("DELETE FROM model.topology_version WHERE topology_version_id=?", [VERSION])
        con.execute("""INSERT INTO model.topology_version VALUES (?,
            'SOLVABLE_EQUIVALENT','EQUIVALENT_BRANCH_GATE_SWITCHING_ABSTRACTION',
            ? ,NULL,'SYNTHETIC_SWITCH_STATE_NOT_OPERATOR_OBSERVATION')""",
                    [VERSION, SNAPSHOT])
        checks = {
            "equivalent_branches": con.execute("SELECT count(*) FROM equivalent.branches").fetchone()[0],
            "switching_devices": con.execute("SELECT count(*) FROM model.switching_device").fetchone()[0],
            "distinct_switching_devices": con.execute("SELECT count(DISTINCT switch_id) FROM model.switching_device").fetchone()[0],
            "switch_states": con.execute("SELECT count(*) FROM operating.switch_state_snapshot").fetchone()[0],
            "closed_base_states": con.execute("SELECT count(*) FROM operating.switch_state_snapshot WHERE position='CLOSED'").fetchone()[0],
            "contingencies": con.execute("SELECT count(*) FROM scenario.contingency").fetchone()[0],
            "contingency_actions": con.execute("SELECT count(*) FROM scenario.contingency_action").fetchone()[0],
            "object_registry_switches": con.execute("SELECT count(*) FROM model.object_registry WHERE object_category='SWITCHING_DEVICE'").fetchone()[0],
            "orphan_controlled_equipment": con.execute("""SELECT count(*) FROM model.switching_device s
                LEFT JOIN model.conducting_equipment e
                  ON s.controlled_equipment_id=e.equipment_id AND s.network_layer=e.network_layer
                WHERE e.equipment_id IS NULL""").fetchone()[0],
            "orphan_controlled_terminals": con.execute("""SELECT count(*) FROM model.switching_device s
                LEFT JOIN model.terminal t ON s.controlled_terminal_id=t.terminal_id
                WHERE t.terminal_id IS NULL""").fetchone()[0],
            "graph_bridges": con.execute("SELECT count(*) FROM study.switching_branch_criticality WHERE is_graph_bridge").fetchone()[0],
            "switches_with_deenergized_load": con.execute("""SELECT count(*) FROM study.switching_branch_criticality
                WHERE deenergized_load_count>0""").fetchone()[0],
            "mv_feeder_switches": con.execute("SELECT count(*) FROM model.switching_device WHERE controlled_equipment_id LIKE 'MVFEEDER:%'").fetchone()[0],
            "mv_feeder_bridges": con.execute("""SELECT count(*) FROM study.switching_branch_criticality
                WHERE controlled_equipment_id LIKE 'MVFEEDER:%' AND is_graph_bridge""").fetchone()[0],
            "mv_feeder_switches_deenergizing_two_loads": con.execute("""SELECT count(*) FROM study.switching_branch_criticality
                WHERE controlled_equipment_id LIKE 'MVFEEDER:%'
                  AND deenergized_load_count=2""").fetchone()[0],
            "publicly_observed_switch_devices": con.execute("""SELECT count(*) FROM model.switching_device
                WHERE evidence_status NOT LIKE 'SYNTHETIC%'""").fetchone()[0],
        }

    errors = []
    expected = checks["equivalent_branches"]
    for key in ("switching_devices", "distinct_switching_devices", "switch_states",
                "closed_base_states", "contingencies", "contingency_actions",
                "object_registry_switches"):
        if checks[key] != expected:
            errors.append(f"{key}={checks[key]} expected {expected}")
    for key in ("orphan_controlled_equipment", "orphan_controlled_terminals"):
        if checks[key]:
            errors.append(f"{key}={checks[key]}")
    if checks["mv_feeder_switches"] != 72434 or checks["mv_feeder_bridges"] != 72434:
        errors.append("Every synthetic MV feeder should be a switch-controlled graph bridge")
    report = {
        "result": "PASS" if not errors else "FAIL",
        "checks": checks,
        "topology_version_id": VERSION,
        "switch_snapshot_id": SNAPSHOT,
        "scope": "Nationwide equivalent branch-gate switches, all-closed state snapshot, N-1 actions, and source-aware bridge criticality",
        "limitations": [
            "Switches are simulation abstractions attached to existing branch terminals, not operator breaker inventory",
            "The control model gates a branch and does not create physical busbar, bay, or split-node geometry",
            "All-closed normal positions are assumptions; no time-stamped public switch states are available",
            "Deenergized load counts use modeled source candidates plus aggregate LV and inferred MV load resources, not customer interruption records",
            "Contingencies enumerate actions only; each case still requires power-flow, fault, protection, and restoration studies",
        ],
        "errors": errors,
    }
    path = args.database.parent / "switching_topology_scenario.validation.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
