# PT60-Candidate Claim–Evidence Matrix

Updated: 2026-07-14
Dataset version: PT60-Candidate v1.0.0
Dataset DOI: `10.6084/m9.figshare.32984021`

## Contribution framing

**Contribution.** PT60-Candidate releases a provenance-tracked candidate dataset and fail-closed pipeline output for Portuguese E-REDES 60 kV topology reconstruction while preserving retained and non-retained circuit dispositions.

**Evidence.** The evidence consists of the 63-file DOI archive, 358 retained candidate branches, complete 1,342-row circuit ledger, 484-node/358-edge GraphML, 216-setting sensitivity sweep, internal checks, two deterministic negative controls, OSM/OpenInfraMap public-source concordance, independence-risk records, schemas, and checksums.

**Importance.** The release makes topology reconstruction decisions, exclusions, provenance, sensitivity, and reuse limitations inspectable without presenting inferred connectivity as an operator-validated grid model.

## Claims used in the canonical manuscript

| Claim | Deposited evidence | Status | Required wording boundary |
|---|---|---|---|
| The source snapshot contains 5,334 valid AT line features and zero geometry-loader drops. | `core_topology/at_paper_logic_summary.json`; `validation/internal_validation_summary.json` | Supported for v1.0.0 | Feature count, not physical-circuit count. |
| The selected setting is node set B, 100 m facility buffer, 0.5 m endpoint snap, and voltage-plus-status-aware merging. | `core_topology/at_paper_logic_summary.json`; `core_topology/at_paper_logic_parameter_sweep.csv` | Supported | Documented engineering choice, not an externally optimal setting. |
| The sweep contains 216 configurations. | `core_topology/at_paper_logic_parameter_sweep.csv` | Supported | Sensitivity coverage does not establish topology accuracy. |
| The pipeline produces 1,342 circuit candidates, retaining 358 and preserving 984 downgrade/rejection records. | `core_topology/at_circuit_classification.csv`; `inventory/headline_counts.json` | Supported | “Retained” means rule satisfaction, not true positive. |
| The selected GraphML has 484 nodes and 358 edges and preserves isolates and parallel edges. | `core_topology/at_paper_logic_graph.graphml`; `core_topology/at_paper_logic_summary.json` | Supported | Candidate multigraph, not a physical-network census. |
| Internal validation records 23 PASS and 2 WARN results across 25 checks. | `validation/internal_validation_checks.csv`; `validation/internal_validation_summary.json` | Supported | Internal consistency only. |
| Endpoint-name corruption reduces strong name evidence from 183/358 to 9/358. | `validation/matcher_negative_control_names.csv`; summary JSON | Supported | Matcher-selectivity control, not precision. |
| Spatial displacement reduces corridor evidence from 290/358 to 1/358. | `validation/matcher_negative_control_geometry.csv`; summary JSON | Supported | Matcher-selectivity control, not topology accuracy. |
| Public-source concordance categories contain 247 strong, 104 medium, and 7 weak retained branches. | `validation/pt_topology_cross_validation_osm_matches.csv`; summary JSON | Supported | Public-source concordance, not operator validation or ground truth. |
| Independence-risk categories contain 266 more-independent-public-evidence, 87 unknown, and 5 possibly-same-source branches. | `validation/pt_topology_cross_validation_osm_matches_independence_audit.csv`; summary JSON | Supported | Provenance-risk categorization, not statistical independence proof. |
| All 358 retained records lack a complete branch-specific source-backed electrical parameter set. | `core_topology/at_parameter_feasibility_summary.json`; data dictionary | Supported | Dataset is not ACPF/OPF ready. |
| The archive contains 63 files with complete manifest and checksum reconciliation. | `manifest.json`; `checksums.sha256`; `archive_validation_summary.json` | Supported | Package integrity, not scientific truth. |
| A clean checkout of tag `pt60-candidate-v1.0.0` rebuilds the archive byte-for-byte from frozen derived inputs. | `reports/108_pt60_clean_room_archive_validation.md`; archive SHA-256 | Supported with limitation | Does not establish raw/API-to-release reproduction. |
| E-REDES-derived records use the recorded portal-level CC BY 4.0 basis with attribution and indication of modification. | `DATA_LICENSE.md`; `ATTRIBUTION.md`; `provenance/reproduction_source_manifest.json` | Supported for recorded release review | Licence permission is not topology validation. |

## Claims prohibited for v1.0.0

- operator-validated or physically accurate topology;
- independently estimated branch precision, recall, F1, or confidence intervals;
- complete Portuguese national grid coverage;
- operational switching, protection, security, contingency, or congestion readiness;
- AC power-flow or OPF readiness;
- verified branch-specific electrical parameters or operating conditions;
- full raw/API-to-release byte reproducibility.

## Remaining submission blockers

- Final author names, affiliations, ORCIDs, contribution statement, funding, ethics/responsible-release determination, and competing-interests declaration.
- Reviewer-accessible archived code repository and preferably a code DOI.
- Logged-out verification of the confidential Figshare and code links immediately before submission.
