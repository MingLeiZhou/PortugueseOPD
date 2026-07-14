# PT60-Candidate Submission Readiness Review

Updated: 2026-07-14
Target: Scientific Data — Data Descriptor
Canonical manuscript: `paper/main_scidata_public_validation.tex`

## Current verdict

PT60-Candidate is now framed consistently as a provenance-tracked candidate-topology dataset using a claim-bounded public-source validation route. The dataset archive is built, schema-covered, checksum-linked, deposited in a private Figshare record, and cited using reserved DOI `10.6084/m9.figshare.32984021`. The manuscript no longer treats dual independent human adjudication as required evidence for this route and does not report precision, recall, operator validation, or operational-grid readiness.

The code-access blocker is resolved through the public GitHub repository and immutable release tags. The paper is not yet submission-ready because author/governance metadata remain unresolved. The Figshare item must also remain accessible to reviewers through a confidential link supplied outside the public repository until the DOI becomes public.

## Supported contribution

**Contribution.** PT60-Candidate v1.0.0 releases a fail-closed reconstruction of a Portuguese E-REDES 60 kV candidate topology together with the complete disposition ledger, sensitivity sweep, provenance, validation records, schemas, and checksums.

**Evidence.** The deposited archive contains 63 documented files; 358 retained candidate branches; all 1,342 retained/downgraded/rejected circuit records; a 484-node, 358-edge GraphML multigraph; 216 reconstruction configurations; 25 internal checks; two deterministic negative controls; and branch-level OSM/OpenInfraMap concordance and independence-risk records.

**Boundary.** The evidence supports internal consistency, deterministic packaging, matcher selectivity, sensitivity transparency, and public-source concordance. It does not support physical-topology accuracy, operator confirmation, national-grid recall, AC power-flow readiness, or OPF readiness.

## Blocking before submission

### B1. Preserve and cite the public code release

- The public repository is `https://github.com/MingLeiZhou/PortugueseOPD`.
- The deterministic dataset builder is frozen at commit `1eb690302f35f7c0090c14c994d073539cbb5335` and annotated tag `pt60-candidate-v1.0.0`.
- The complete paper/code snapshot is frozen as `pt60-candidate-code-v1.0.0`.
- A DOI-providing software archive is strongly preferred but is no longer a reviewer-access blocker.

### B2. Complete author and governance metadata

- Add author names, affiliations, countries, corresponding email, and ORCIDs.
- Replace the provisional Author Contributions, Funding, Acknowledgements, and Competing Interests text.
- Record the actual ethics/responsible-release determination and maintainer/errata contact.
- Apply the current Springer Nature AI-use disclosure policy to the actual use made in preparing the work.

### B3. Confirm repository access at submission

- The confidential Figshare link must allow logged-out reviewers to download `PT60-Candidate-v1.0.0.tar.gz`.
- The downloaded SHA-256 must equal `b4a6c370fbf15c078e15b80a3bbc7f517750fdb763d59147b24f59cd160358d7`.
- When the Figshare item is published, confirm that `https://doi.org/10.6084/m9.figshare.32984021` resolves and replace review-only wording where appropriate.

## Major limitations to retain

### M1. No operator or truth-label validation

The manuscript must retain the statement that OSM/OpenInfraMap categories are public-source concordance variables, not truth labels. Dual review is optional future evidence and is not a blocker while the paper avoids accuracy, precision, and recall claims.

### M2. Raw/API reproduction is incomplete

The final tag rebuilds the archive byte-for-byte from the frozen derived input set. Raw E-REDES snapshots were not deposited, and the recorded v2.1 export URLs for the three topology-critical datasets returned HTTP 404 during the final reacquisition audit. The DOI archive is therefore the preserved versioned derived dataset; raw/API-to-release byte reproduction is not claimed.

### M3. Electrical readiness is outside the core release

Branch-specific R/X/B/current limits, transformer controls, measured injections, and verified operating boundaries remain incomplete or absent. ACPF/DCOPF diagnostics and Stage 1–5 learning interfaces are excluded from the main public archive and should not re-enter the Data Descriptor narrative as released products.

## Validation status

| Check | Current result |
|---|---|
| Archive inventory | PASS: 63 files and 63 manifest records |
| Checksums | PASS: zero mismatches or missing paths |
| Schema coverage | PASS: 54/54 machine-readable paths, 1,090 dictionary records |
| Internal validation | PASS_WITH_WARNINGS: 23 pass, 2 documented warnings |
| Endpoint-name negative control | 183/358 real strong-name evidence vs 9/358 corrupted |
| Spatial negative control | 290/358 real corridor evidence vs 1/358 displaced |
| Public-source concordance | 247 strong, 104 medium, 7 weak |
| Independence-risk audit | 266 more independent public evidence, 87 unknown, 5 possibly same-source |
| Deterministic tagged rebuild | PASS: clean-tag and main-worktree archive hashes identical |
| Dataset DOI/reference | Present: `10.6084/m9.figshare.32984021` |
| Code release | Public GitHub repository with immutable dataset-builder and complete-code tags |
| Code DOI | Recommended: not yet minted |
| Author declarations | Blocking: author input required |

## Next actions

1. Supply final author, affiliation, ORCID, contribution, funding, ethics, and conflict metadata.
2. Optionally archive the code tag in Zenodo or another DOI-providing repository.
3. Compile and visually inspect the revised canonical manuscript.
4. Test the Figshare confidential link and public code link from a logged-out browser.
5. Run the final claim–evidence and citation audit.
