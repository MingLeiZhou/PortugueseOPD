"""Summarize Portuguese model gap-resolution workstreams into one table."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
REPORTS = ROOT / "reports"
OUT_CSV = PROCESSED / "portuguese_model_gap_resolution_table.csv"


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def row(workstream: str, step: str, problem: str, method: str, script: str, result: str, status: str, next_action: str) -> dict[str, str]:
    return {
        "workstream": workstream,
        "step": step,
        "problem": problem,
        "method_attempted": method,
        "script_or_output": script,
        "result": result,
        "status": status,
        "next_required_data_or_action": next_action,
    }


def build_rows() -> list[dict[str, str]]:
    topo = read_json(PROCESSED / "topology_repair" / "at_topology_repair_summary.json")
    lut = read_json(PROCESSED / "lut_scenarios" / "pt_lut_scenarios_summary.json")
    load = read_json(PROCESSED / "load_validation" / "pt_load_validation_summary.json")
    prog = read_json(PROCESSED / "acpf_progressive" / "pt_acpf_progressive_summary.json")
    base_pf = read_json(PROCESSED / "acpf_results" / "pt_acpf_summary.json")
    s8 = read_json(PROCESSED / "acpf_s8_pathology_exclusion" / "s8_acpf_frontier_summary.json")
    s11 = read_json(PROCESSED / "acpf_s11_alt_boundary" / "s11_alt_boundary_summary.json")
    s12 = read_json(PROCESSED / "acpf_s12_alt_slack_542_load_reallocation" / "s12_acpf_frontier_summary.json")
    s13 = read_json(PROCESSED / "acpf_s13_alt_slack_542_tap_sensitivity" / "s13_tap_sensitivity_summary.json")
    s14 = read_json(PROCESSED / "acpf_s14_critical_corridor_strengthening" / "s14_corridor_strengthening_summary.json")
    s15 = read_json(PROCESSED / "acpf_s15_active_subnetwork_frontier" / "s15_active_subnetwork_frontier_summary.json")
    s16 = read_json(PROCESSED / "acpf_s16_backbone_core_depth6" / "s16_backbone_summary.json")
    s17 = read_json(PROCESSED / "acpf_s17_reparameterize_atpl_00075" / "s17_acpf_frontier_summary.json")
    s18 = read_json(PROCESSED / "dcopf_s18_reduced_balanced" / "s18_dcopf_summary.json")
    s19 = read_json(PROCESSED / "dcopf_s19_model_sanity" / "s19_dcopf_summary.json")
    s20 = read_json(PROCESSED / "dcopf_s20_strict_internal" / "s20_strict_internal_dcopf_summary.json")
    s21 = read_json(PROCESSED / "dcopf_s21_no_extgrid" / "s21_no_extgrid_dcopf_summary.json")
    s22 = read_json(PROCESSED / "s22_mixed_corridor_remediation" / "s22_dcopf_summary.json")
    s23 = read_json(PROCESSED / "dcopf_s23_dispatch_path" / "s23_dispatch_path_summary.json")
    s24 = read_json(PROCESSED / "dcopf_s24_atpl_00304_remediation" / "s24_summary.json")
    s25 = read_json(PROCESSED / "dcopf_s25_dispatch_concentration" / "s25_dispatch_concentration_summary.json")
    s26 = read_json(PROCESSED / "dcopf_s26_supply_diversification" / "s26_summary.json")
    s27 = read_json(PROCESSED / "dcopf_s27_primary_transfer_path" / "s27_summary.json")

    rows = [
        row(
            "Topology repair",
            "1.1 Endpoint-to-line repair candidates",
            "AT topology is fragmented; many non-clean circuits are single-facility, isolated, tap/multi-terminal, or ambiguous.",
            "Screen dangling endpoints against existing inter-facility line segments using 25/50/100 m distance buckets; keep candidates diagnostic-only.",
            "src/repair_at_topology.py; reports/15_at_topology_repair.md",
            f"Examined {topo.get('dangling_endpoints_examined', 'not run')} dangling endpoints; found {topo.get('high_confidence_candidates_le_50m', 'not run')} high-confidence <=50 m candidates.",
            topo.get("status", "NOT_RUN"),
            "Manually validate candidates on maps or with E-REDES/REN topology before accepting terminal changes.",
        ),
        row(
            "Portuguese LUT",
            "2.1 Scenario LUT organization",
            "Line R/X/B/current and transformer completion remain mixed source-backed, scenario, benchmark, and missing values.",
            "Create explicit line/transformer LUT scenario tables with SOURCE_BACKED_PARTIAL, SCENARIO_ASSUMED, BENCHMARK_ONLY, and MISSING_REQUIRED statuses.",
            "src/build_portuguese_lut_scenarios.py; reports/16_portuguese_lut_scenarios.md",
            f"Created {lut.get('line_lut_rows', 'not run')} line LUT rows and {lut.get('transformer_lut_rows', 'not run')} transformer rows; source-backed PF ready={lut.get('source_backed_pf_ready', False)}.",
            lut.get("status", "NOT_RUN"),
            "Obtain Portuguese 60 kV overhead R/X/B/current, cable R/X/C, branch circuit counts, transformer R/X split, unit counts, and tap-control data.",
        ),
        row(
            "Load validation",
            "3.1 Load placement and transformer loading diagnostics",
            "Base ACPF load allocation may overload local transformers and place load on deep weak paths.",
            "Compute load-to-transformer-capacity, distance-to-slack, component membership, risk flags, and diagnostic reallocation suggestions.",
            "src/validate_load_allocation.py; reports/17_load_allocation_validation.md",
            f"Validated {load.get('active_load_count', 'not run')} active loads; flagged {load.get('flagged_load_count', 'not run')} loads; produced {load.get('suggestion_count', 'not run')} suggestions.",
            load.get("status", "NOT_RUN"),
            "Validate hourly/substation load joins, measured Q or PF assumptions, and transformer unit capacities before using suggestions as a scenario.",
        ),
        row(
            "Progressive ACPF",
            "4.1 Convergence frontier",
            "Single strict NR run fails, and prior ablations were not organized as a reusable progressive frontier.",
            "Run fail-closed diagnostic ladder across algorithms, load scaling, voltage bounds, Q/PF, transformer assumptions, and simple shunt proxy.",
            "src/run_portuguese_acpf_progressive.py; reports/18_progressive_acpf.md",
            f"Ran {prog.get('attempt_count', 'not run')} attempts; converged {prog.get('converged_count', 'not run')}; first convergence={prog.get('first_converged_attempt', '')} at {prog.get('first_converged_effective_p_mw', '')} MW.",
            prog.get("status", "NOT_RUN"),
            "Treat any convergence as diagnostic only until topology, line/trafo parameters, load, slack, and controls are source-backed or validated.",
        ),
        row(
            "Baseline ACPF",
            "5.1 Existing fail-closed base run",
            "Base model reached only benchmark-plumbing readiness and did not converge.",
            "Retain fail-closed gate and do not promote benchmark-only results.",
            "src/run_portuguese_acpf_if_ready.py; reports/13_portuguese_acpf_readiness_completion.md",
            f"Readiness={base_pf.get('readiness_status', 'unknown')}; converged={base_pf.get('converged', False)}; error={base_pf.get('error_type', '')}.",
            "NOT_READY_FOR_PUBLICATION",
            "Continue with diagnostic plumbing; obtain external E-REDES/REN data before making Portuguese PF/OPF claims.",
        ),
        row(
            "Pathology isolation",
            "6.1 Exclude ATPL_00003 diagnostic",
            "S5 non-convergence was dominated by a slack-adjacent pathological mixed branch (`ATPL_00003`).",
            "Exclude `ATPL_00003` only in a controlled diagnostic scenario and rerun the load frontier.",
            "src/run_s8_exclude_pathological_line_diagnostic.py; reports/28_s8_pathological_line_exclusion_diagnostic.md",
            f"Excluded {s8.get('excluded_count', 'not run')} line; converged {s8.get('converged_count', 'not run')}/{s8.get('attempt_count', 'not run')} attempts; max converged load scale={s8.get('max_converged_load_scale', '')}.",
            s8.get('status', 'NOT_RUN'),
            "Manually validate ATPL_00003 topology/electrical representation before treating exclusion as anything other than diagnostic-only.",
        ),
        row(
            "Boundary sensitivity",
            "6.2 Keep ATPL_00003 and vary slack/boundary",
            "The main failure might come from ATPL_00003 interacting with the selected slack-side boundary representation rather than from the line alone.",
            "Keep `ATPL_00003` in service and test alternate slack buses/boundary representations.",
            "src/run_s11_alt_boundary_with_atpl_00003.py; reports/34_s11_alt_boundary_with_atpl_00003.md",
            f"Converged {s11.get('converged_count', 'not run')}/{s11.get('attempt_count', 'not run')} attempts; best slack={s11.get('best_case_slack_bus', '')} ({s11.get('best_case_slack_bus_name', '')}) at load scale={s11.get('best_case_load_scale', '')}.",
            s11.get('status', 'NOT_RUN'),
            "Adopt a diagnostic slack/boundary policy separate from publication topology claims; compare candidate boundary choices before further expansion.",
        ),
        row(
            "Load remediation",
            "6.3 Slack 542 plus load reallocation",
            "Even with a better boundary, deep-path and transformer-capacity-mismatched loads still limited convergence.",
            "Move slack to bus 542 and apply diagnostic load-reallocation suggestions from load validation.",
            "src/run_s12_alt_slack_542_with_load_reallocation.py; reports/35_s12_alt_slack_542_with_load_reallocation.md",
            f"Modified {s12.get('modified_load_count', 'not run')} loads; total P reduced from {s12.get('total_p_before', '')} to {s12.get('total_p_after', '')} MW; max converged load scale={s12.get('max_converged_load_scale', '')}.",
            s12.get('status', 'NOT_RUN'),
            "Treat this as diagnostic-only load screening; still need measured or defensible load placement to raise voltage quality beyond the 10% frontier.",
        ),
        row(
            "Voltage-control sensitivity",
            "6.4 Transformer tap/XR/Q support tests",
            "Low-voltage behavior persisted under S12, suggesting possible transformer or reactive-support limitations.",
            "Test transformer tap biases, X/R sensitivity, and simple shunt/Q support on top of the S12 setup.",
            "src/run_s13_alt_slack_542_load_reallocation_tap_sensitivity.py; reports/36_s13_alt_slack_542_load_reallocation_tap_sensitivity.md",
            f"Ran {s13.get('attempt_count', 'not run')} attempts across {s13.get('variant_count', 'not run')} variants; best variant={s13.get('best_variant_id', '')} at load scale={s13.get('best_variant_load_scale', '')}, but no variant extended the frontier beyond S12.",
            s13.get('status', 'NOT_RUN'),
            "Do not prioritize more tap/XR/shunt tuning until structural bottlenecks are addressed; these knobs provided only marginal benefit.",
        ),
        row(
            "Critical corridor sensitivity",
            "6.5 Strengthen ATPL_00075 and ATPL_00147",
            "After boundary and load screening, the bottleneck migrated to a small set of backbone corridors, especially `ATPL_00075`.",
            "Increase `max_i` and reduce `x` only on the identified critical corridors to test whether local transfer capability is the limiting factor.",
            "src/run_s14_critical_corridor_strengthening.py; reports/37_s14_critical_corridor_strengthening.md",
            f"Ran {s14.get('attempt_count', 'not run')} attempts across {s14.get('variant_count', 'not run')} variants; best variant={s14.get('best_variant_id', '')} at load scale={s14.get('best_variant_load_scale', '')}, but still no convergence at 20% load.",
            s14.get('status', 'NOT_RUN'),
            "Stop patching single corridors globally; the active subnetwork appears structurally weak and needs backbone-focused diagnosis instead.",
        ),
        row(
            "Backbone frontier",
            "6.6 Active-subnetwork reduction frontier",
            "The active network may simply be too deep/weak to support higher load even after local fixes.",
            "Reduce the active subnetwork by graph depth from slack 542 and rerun a load frontier to identify the strongest solvable core.",
            "src/run_s15_active_subnetwork_reduction_frontier.py; reports/38_s15_active_subnetwork_reduction_frontier.md",
            f"Converged {s15.get('converged_count', 'not run')}/{s15.get('attempt_count', 'not run')} attempts; best core depth={s15.get('best_max_depth', '')} with {s15.get('best_kept_bus_count', '')} buses at load scale={s15.get('best_load_scale', '')}.",
            s15.get('status', 'NOT_RUN'),
            "Use the depth-6 backbone as the current core diagnostic network; trace its dominant bottlenecks before expanding again.",
        ),
        row(
            "Backbone core",
            "6.7 Formalize depth-6 core and trace bottleneck",
            "A backbone diagnostic core was needed so downstream diagnostics stop oscillating between full-network pathologies.",
            "Freeze the slack-542, load-reallocated, depth-6 subnetwork as `S16_BACKBONE_DIAGNOSTIC_CORE_DEPTH6` and trace `ATPL_00075` lineage inside it.",
            "src/create_s16_backbone_diagnostic_core_depth6.py; src/trace_atpl_00075_lineage.py; reports/39_s16_backbone_diagnostic_core_depth6.md; reports/40_atpl_00075_bottleneck_trace.md",
            f"Created a 54-bus / 36-line / 25-load backbone core; reference 50% run converged with min_vm_pu={s16.get('reference_min_vm_pu', '')} and worst line={s16.get('reference_worst_line_name', '')}. ATPL_00075 remains a mixed 50/50 proxy bottleneck.",
            s16.get('status', 'NOT_RUN'),
            "Manually validate ATPL_00075 and then implement `S17_SPLIT_ATPL_00075_DIAGNOSTIC` or `S17_REPARAMETERIZE_ATPL_00075_DIAGNOSTIC` before widening the backbone.",
        ),
        row(
            "ATPL_00075 remediation",
            "6.8 Reparameterize ATPL_00075",
            "ATPL_00075 remained the dominant backbone bottleneck after the depth-6 core was fixed, suggesting its mixed 50/50 proxy needed manual-validation-backed correction.",
            "Apply a 74.2% overhead / 25.8% cable weighted reparameterization with conservative and weighted current variants.",
            "src/run_s17_reparameterize_atpl_00075_diagnostic.py; reports/43_s17_reparameterize_atpl_00075_diagnostic.md",
            f"Ran {s17.get('attempt_count', 'not run')} attempts across {s17.get('variant_count', 'not run')} variants; best load scale={s17.get('best_load_scale', '')}, but no variant improved the 10% frontier or displaced ATPL_00147 as the next bottleneck.",
            s17.get('status', 'NOT_RUN'),
            "Treat ATPL_00075 as a real but not singly decisive bottleneck; shift attention to OPF-side supply/path structure and other binding corridors.",
        ),
        row(
            "DC PF baseline",
            "7.1 Diagnostic DC power flow",
            "A linearized active-power-only baseline was needed before diagnostic OPF work.",
            "Run fail-closed diagnostic DCPF on the best-available AC-ready network to inspect coarse flow patterns and dominant overloads.",
            "src/run_portuguese_dcpf_if_ready.py; reports/44_portuguese_dcpf_diagnostic.md",
            f"DCPF converged=True on scenario {s18.get('scenario_id', 'S5_BEST_AVAILABLE_MULTILINGUAL_DIAGNOSTIC')}; worst line family remained pathology-dominated before OPF-side model alignment.",
            "DIAGNOSTIC_DONE",
            "Use DCPF only as a linear benchmark; do not treat it as a credible dispatch or benchmark result until OPF-side semantics are fixed.",
        ),
        row(
            "DC OPF scaffolding",
            "7.2 Reduced balanced DC OPF baseline",
            "The first DC OPF needed a reduced load level and proxy generation/cost layer just to become numerically meaningful.",
            "Scale the S16 backbone load to the initial proxy supply envelope and run a first reduced balanced DC OPF (`S18`).",
            "src/run_s18_dcopf_reduced_balanced_case.py; reports/52_s18_reduced_balanced_dcopf.md",
            f"Converged reduced DC OPF at load scale={s18.get('applied_load_scale', '')} with total load ≈ {s18.get('total_load_p_mw_effective', '')} MW, but generator dispatch still behaved pathologically.",
            s18.get('status', 'NOT_RUN'),
            "Do not treat S18 as a final OPF baseline; ext_grid/import semantics and generator roles still required cleanup.",
        ),
        row(
            "DC OPF model sanity",
            "7.3 Align generator vs ext_grid roles",
            "The first DC OPF converged but the supply interpretation was meaningless because the edge-supply model dominated the dispatch.",
            "Separate dispatchable proxy generation from import/interface proxy generation, set controllability explicitly, and diagnose how much of load is really served by internal generation (`S19`).",
            "src/run_s19_dcpf_dcopf_model_sanity_alignment.py; reports/53_s19_dcpf_dcopf_model_sanity_alignment.md",
            f"Model sanity alignment converged with generator dispatch ratio ≈ {s19.get('dispatch_ratio_gen_to_load', '')} and ext-grid/import ratio ≈ {s19.get('dispatch_ratio_ext_to_load', '')}; import role still dominated.",
            s19.get('status', 'NOT_RUN'),
            "Remove ext_grid from the supply role to test whether the proxy generator fleet can actually sustain a meaningful internal dispatch problem.",
        ),
        row(
            "Internal DC OPF",
            "7.4 Remove ext_grid supply role",
            "The project needed to prove whether internal proxy generators could sustain dispatch without ext_grid implicitly balancing the system.",
            "Run `S21_NO_EXTGRID_GEN_ONLY_DCOPF` with internal proxy generators and import-interface rows represented as ordinary generation rather than ext_grid.",
            "src/run_s21_no_extgrid_gen_only_dcopf.py; reports/54_s21_no_extgrid_gen_only_dcopf.md",
            f"Converged {s21.get('converged_count', 'not run')}/{s21.get('attempt_count', 'not run')} strict-internal attempts; best load scale={s21.get('best_load_scale', '')}; internal dispatch became non-zero and interpretable.",
            s21.get('status', 'NOT_RUN'),
            "Use this as the first meaningful internal DC OPF reference and diagnose dispatch concentration plus transfer bottlenecks.",
        ),
        row(
            "Mixed corridor family",
            "7.5 Family-level mixed-corridor remediation",
            "Several key PF/OPF bottlenecks (`ATPL_00003`, `ATPL_00075`, `ATPL_00304`) shared the same unsplit 50/50 mixed proxy family, so a family-level fix had to be tested.",
            "Apply a unified weighted remediation across the three key mixed corridors and rerun internal DC OPF (`S22`).",
            "src/run_s22_mixed_corridor_targeted_remediation.py; reports/57_s22_mixed_corridor_targeted_remediation.md",
            f"Remediated {s22.get('remediated_line_count', 'not run')} mixed corridors; converged {s22.get('converged_count', 'not run')}/{s22.get('attempt_count', 'not run')} attempts, but the main OPF bottleneck persisted on {s22.get('best_max_line_name', '')}.",
            s22.get('status', 'NOT_RUN'),
            "Conclude that family-level mixed-proxy cleanup alone is insufficient; dispatch-transfer structure is now the dominant issue.",
        ),
        row(
            "Dispatch structure",
            "7.6 Dispatch path concentration diagnosis",
            "Once internal dispatch became real, the next question was which generators and corridors carried most of the power.",
            "Diagnose generator-to-load path skeletons and identify the dominant dispatch source and the line that binds first in internal DC OPF (`S23`).",
            "src/diagnose_s23_dispatch_path_structure.py; reports/58_s23_dispatch_path_structure_diagnosis.md",
            f"Total internal dispatch ≈ {s23.get('total_gen_dispatch_mw', 'not run')} MW at load scale={s23.get('load_scale', '')}; top dispatch source={s23.get('top_generator_name', '')}; worst line={s23.get('worst_line_name', '')}.",
            s23.get('status', 'NOT_RUN'),
            "Target the primary supply path rather than individual PF pathologies; the OPF bottleneck had migrated to ATPL_00304 and its corridor.",
        ),
        row(
            "ATPL_00304 remediation",
            "7.7 Reparameterize OPF binding line",
            "ATPL_00304 emerged as the dominant binding line in the internal DC OPF path, so its mixed-proxy assumptions were tested directly.",
            "Apply an overhead-dominant weighted reparameterization to ATPL_00304 and compare baseline vs remediated OPF behavior (`S24`).",
            "src/run_s24_reparameterize_atpl_00304_dcopf.py; reports/59_s24_reparameterize_atpl_00304_dcopf.md",
            f"Reparameterization left the best max loading essentially unchanged ({s24.get('baseline_max_line_loading_percent', '')} → {s24.get('remediated_max_line_loading_percent', '')}); dispatch structure did not materially improve.",
            s24.get('status', 'NOT_RUN'),
            "Stop treating ATPL_00304 as an isolated parameter bug; investigate supply concentration and path redundancy instead.",
        ),
        row(
            "Dispatch concentration",
            "7.8 Top-source dependency test",
            "The internal DC OPF appeared too dependent on one source (`FANHÕES (PS)`), so the next step was to test how fragile the dispatch is when that source is penalized or removed.",
            "Run `S25_DISPATCH_CONCENTRATION_SENSITIVITY` with capped, removed, and penalized top-source variants.",
            "src/run_s25_dispatch_concentration_sensitivity.py; reports/60_s25_dispatch_concentration_sensitivity.md",
            f"Baseline and penalized cases converged, but capping/removing the top source made OPF infeasible; the system is strongly dependent on a single dispatch hub.",
            "DIAGNOSTIC_DONE",
            "Diversify the internal dispatchable fleet and retest whether supply concentration or path capacity is the primary limitation.",
        ),
        row(
            "Supply diversification",
            "7.9 Expand internal dispatchable proxy fleet",
            "To separate supply insufficiency from path insufficiency, the internal dispatchable proxy fleet needed to be expanded beyond the initial 18-row scaffold.",
            "Promote selected 60 kV backbone-assigned capacity-context rows into a diagnostic diversified dispatchable fleet and rerun internal DC OPF (`S26`).",
            "src/run_s26_supply_diversification_diagnostic.py; reports/61_s26_supply_diversification_diagnostic.md",
            f"Expanded the generator fleet to {s26.get('added_gen_count', 'not run')} rows with total pmax ≈ {s26.get('total_pmax_mw', '')} MW; dispatch still concentrated on {s26.get('top_dispatch_generator', '')} and the worst line remained {s26.get('max_line_name', '')}.",
            "DIAGNOSTIC_DONE",
            "Conclude that supply quantity is no longer the main blocker; primary transfer-path capacity is now the dominant issue.",
        ),
        row(
            "Primary transfer path",
            "7.10 Remediate FANHÕES-to-load corridor",
            "After diversification, the dominant OPF limitation was the main transfer path from the dispatch hub toward the load backbone (`ATPL_00304`, `ATPL_00147`, `ATPL_00075`).",
            "Strengthen the three-line primary transfer path via higher `max_i` and lower `x` and compare OPF congestion before and after remediation (`S27`).",
            "src/run_s27_primary_transfer_path_remediation.py; reports/62_s27_primary_transfer_path_remediation.md; reports/63_current_best_pf_and_dcopf_scenarios.md",
            f"Best variant={s27.get('best_variant_id', 'max_i_5x_x_0p4')} reduced the worst loading from ATPL_00304≈148% to a new bottleneck around ATPL_00244≈65%, while keeping total internal dispatch ≈ {s27.get('best_total_gen_dispatch_mw', '')} MW.",
            "DIAGNOSTIC_DONE",
            "Freeze S16 as the current best PF backbone and `S27 max_i_5x_x_0p4` as the current best diagnostic DC OPF scenario; next trace ATPL_00244 or formalize this as the best current dispatch baseline.",
        ),
    ]
    return rows


def markdown_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|").replace("\n", " ") for c in cols) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    rows = build_rows()
    df = pd.DataFrame(rows)
    df.to_csv(OUT_CSV, index=False)
    text = [
        "# 19 Portuguese Model Gap Resolution Table",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "This table summarizes each attempted workstream, the method used, current status, and what remains required. Diagnostic convergence or repair candidates are not publication-grade results.",
        "",
        markdown_table(df),
    ]
    (REPORTS / "19_portuguese_model_gap_resolution_table.md").write_text("\n".join(text), encoding="utf-8")
    print(json.dumps({"rows": len(df), "report": str(REPORTS / "19_portuguese_model_gap_resolution_table.md"), "csv": str(OUT_CSV)}, indent=2))


if __name__ == "__main__":
    main()
