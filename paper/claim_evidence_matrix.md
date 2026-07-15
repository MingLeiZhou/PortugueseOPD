# PT60-Candidate Claim–Evidence Matrix

Updated: 2026-07-15
Dataset version: PT60-Candidate v1.0.2
Dataset DOI: `10.6084/m9.figshare.32984021`

## Contribution framing

**Contribution.** PT60-Candidate releases a provenance-tracked candidate dataset and fail-closed pipeline output for Portuguese E-REDES 60 kV topology reconstruction while preserving retained and non-retained circuit dispositions.

**Evidence.** The evidence consists of the 67-file candidate archive, 358 retained candidate branches, complete 1,341-row circuit ledger, 484-node/358-edge GraphML, 216-setting sensitivity sweep, internal checks, two deterministic negative controls, OSM-derived public-source concordance, independence-risk records, schemas, and checksums.

**Importance.** The release makes topology reconstruction decisions, exclusions, provenance, sensitivity, and reuse limitations inspectable without presenting inferred connectivity as an operator-validated grid model.

## Claims used in the canonical manuscript

| Claim | Deposited evidence | Status | Required wording boundary |
|---|---|---|---|
| The source snapshot contains 5,334 valid AT line features and zero geometry-loader drops. | `core_topology/at_paper_logic_summary.json`; `validation/internal_validation_summary.json` | Supported for v1.0.2 | Feature count, not physical-circuit count. |
| Exact deduplication converts 410 AT-substation records to 409 and 76 switching-facility records to 75, producing the 484-facility node set B. | `release_metadata.json`; `src/audit_projection_topology_stability.py`; reconstruction loader | Supported | Both exclusions are exact duplicates under facility type, code, and coordinates; no invalid point geometry is implicated. |
| The selected setting is node set B, 100 m facility buffer, 0.5 m endpoint snap, and voltage-plus-status-aware merging. | `core_topology/at_paper_logic_summary.json`; `core_topology/at_paper_logic_parameter_sweep.csv` | Supported | Documented engineering choice, not an externally optimal setting. |
| The sweep contains 216 configurations. | `core_topology/at_paper_logic_parameter_sweep.csv` | Supported | Sensitivity coverage does not establish topology accuracy. |
| The pipeline produces 1,341 circuit candidates, retaining 358 and preserving 983 downgrade/rejection records. | `core_topology/at_circuit_classification.csv`; `inventory/headline_counts.json` | Supported | “Retained” means rule satisfaction, not true positive. |
| The selected GraphML has 484 nodes and 358 edges and preserves isolates and parallel edges. | `core_topology/at_paper_logic_graph.graphml`; `core_topology/at_paper_logic_summary.json` | Supported | Candidate multigraph, not a physical-network census. |
| Internal validation records 25 PASS results across 25 checks. | `validation/internal_validation_checks.csv`; `validation/internal_validation_summary.json` | Supported | Internal consistency only; physical topology truth remains outside scope. |
| Endpoint-name corruption reduces strong name evidence from 182/358 to 9/358. | `validation/matcher_negative_control_names.csv`; summary JSON | Supported | Matcher-selectivity control, not precision. |
| Spatial displacement reduces corridor evidence from 290/358 to 1/358. | `validation/matcher_negative_control_geometry.csv`; summary JSON | Supported | Matcher-selectivity control, not topology accuracy. |
| Public-source concordance categories contain 245 strong, 106 medium, and 7 weak retained branches. | `validation/pt_topology_cross_validation_osm_matches.csv`; summary JSON | Supported | Public-source concordance, not operator validation or ground truth. |
| Independence-risk categories contain 266 more-independent-public-evidence, 87 unknown, and 5 possibly-same-source branches. | `validation/pt_topology_cross_validation_osm_matches_independence_audit.csv`; summary JSON | Supported | Provenance-risk categorization, not statistical independence proof. |
| All 358 retained records lack a complete branch-specific source-backed electrical parameter set. | `core_topology/at_parameter_feasibility_summary.json`; data dictionary | Supported | Dataset is not ACPF/OPF ready. |
| The archive contains 67 files with complete manifest and checksum reconciliation. | `manifest.json`; `checksums.sha256`; `archive_validation_summary.json` | Supported | Package integrity, not scientific truth. |
| The public E-REDES acquisition procedure was executable on 15 July 2026 and records input fingerprints. | `provenance/pt60_v1.0.2_source_input_manifest.json`; `release_metadata.json` | Supported | Repeatable acquisition procedure does not guarantee immutable future API responses. |
| Adopting EPSG:3763 preserves 357/358 retained source-line groups and their facility assignments but exchanges one retained branch while preserving the total of 358. | `validation/projection_release_transition.csv`; summary JSON | Supported | A measured version transition, not projection invariance. |
| E-REDES-derived records use the recorded portal-level CC BY 4.0 basis with attribution and indication of modification. | `DATA_LICENSE.md`; `ATTRIBUTION.md`; `provenance/reproduction_source_manifest.json` | Supported for recorded release review | Licence permission is not topology validation. |

## Claims prohibited for v1.0.2

- operator-validated or physically accurate topology;
- independently estimated branch precision, recall, F1, or confidence intervals;
- complete Portuguese national grid coverage;
- operational switching, protection, security, contingency, or congestion readiness;
- AC power-flow or OPF readiness;
- verified branch-specific electrical parameters or operating conditions;

## Remaining submission blockers

- Final author names, affiliations, ORCIDs, contribution statement, funding, ethics/responsible-release determination, and competing-interests declaration.
- Logged-out verification of the confidential Figshare reviewer link immediately before submission.
- Logged-out verification of the confidential Figshare and code links immediately before submission.
