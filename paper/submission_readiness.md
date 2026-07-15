# PT60-Candidate Submission Readiness Review

Updated: 2026-07-15
Target: Scientific Data — Data Descriptor
Canonical manuscript: `paper/main_scidata.tex`

## Current verdict

PT60-Candidate is now framed consistently as a provenance-tracked candidate-topology dataset using a claim-bounded public-source validation route. The v1.0.2 candidate archive is built, schema-covered, checksum-linked, and prepared for the private Figshare record with reserved DOI `10.6084/m9.figshare.32984021`. The manuscript no longer treats dual independent human adjudication as required evidence for this route and does not report precision, recall, operator validation, or operational-grid readiness.

The code-access blocker is resolved through the public GitHub repository and immutable release tags. The paper is not yet submission-ready because author/governance metadata remain unresolved. The Figshare item must also remain accessible to reviewers through a confidential link supplied outside the public repository until the DOI becomes public.

## Supported contribution

**Contribution.** PT60-Candidate v1.0.2 releases a fail-closed reconstruction of a Portuguese E-REDES 60 kV candidate topology using EPSG:3763, together with the complete disposition ledger, sensitivity sweep, provenance, validation records, schemas, and checksums.

**Evidence.** The candidate archive contains 67 documented files; 358 retained candidate branches; all 1,341 retained/downgraded/rejected circuit records; a 484-node, 358-edge GraphML multigraph; 216 reconstruction configurations; 25 passing internal checks; two deterministic negative controls; and branch-level OSM-derived concordance and independence-risk records.

**Boundary.** The evidence supports internal consistency, deterministic packaging, matcher selectivity, sensitivity transparency, and public-source concordance. It does not support physical-topology accuracy, operator confirmation, national-grid recall, AC power-flow readiness, or OPF readiness.

## Blocking before submission

### B1. Preserve and cite the public code release

- The public repository is `https://github.com/MingLeiZhou/PortugueseOPD`.
- The v1.0.2 source is frozen at commit `93c861c` and annotated tag `pt60-candidate-v1.0.2`.
- A DOI-providing software archive is strongly preferred but is no longer a reviewer-access blocker.

### B2. Complete author and governance metadata

- Add author names, affiliations, countries, corresponding email, and ORCIDs.
- Replace the provisional Author Contributions, Funding, Acknowledgements, and Competing Interests text.
- Record the actual ethics/responsible-release determination and maintainer/errata contact.
- Apply the current Springer Nature AI-use disclosure policy to the actual use made in preparing the work.

### B3. Confirm repository access at submission

- After replacement, the confidential Figshare link must allow logged-out reviewers to download `PT60-Candidate-v1.0.2.tar.gz`.
- The downloaded SHA-256 must equal `328e64adcfc6c7210ed9558793a14fdb661e5e3bab741f1d275a89e5593d1447`.
- When the Figshare item is published, confirm that `https://doi.org/10.6084/m9.figshare.32984021` resolves and replace review-only wording where appropriate.

## Major limitations to retain

### M1. No operator or truth-label validation

The manuscript must retain the statement that OSM/OpenInfraMap categories are public-source concordance variables, not truth labels. Dual review is optional future evidence and is not a blocker while the paper avoids accuracy, precision, and recall claims.

### Resolved with an upstream-version boundary: public API acquisition

The official RND page publicly exposes the widget API keys required by the topology-critical layers. Metadata, record, and export requests returned HTTP 200 for the required inputs. The release records file hashes, sizes, timestamps, and counts in `provenance/pt60_v1.0.2_source_input_manifest.json`. The procedure was executable on the reported date; this statement does not promise immutable future API responses.

### Projection stability

The complete 216-setting sweep was re-run under EPSG:3763 and v1.0.2 adopts that result. Relative to v1.0.1, both metric choices yield 358 retained branches and 61 ambiguous matches; 357/358 retained source-line groups preserve their facility endpoints, but one retained branch is exchanged. The paper reports the exact transition without claiming projection invariance.

### M3. Electrical readiness is outside the core release

Branch-specific R/X/B/current limits, transformer controls, measured injections, and verified operating boundaries remain incomplete or absent. ACPF/DCOPF diagnostics and Stage 1–5 learning interfaces are excluded from the main public archive and should not re-enter the Data Descriptor narrative as released products.

## Validation status

| Check | Current result |
|---|---|
| Archive inventory | PASS: 67 files and 67 manifest records |
| Checksums | PASS: zero mismatches or missing paths |
| Schema coverage | PASS: 58/58 machine-readable paths, 1,201 dictionary records |
| Internal validation | PASS: 25/25 checks |
| Endpoint-name negative control | 182/358 real strong-name evidence vs 9/358 corrupted |
| Spatial negative control | 290/358 real corridor evidence vs 1/358 displaced |
| Public-source concordance | 245 strong, 106 medium, 7 weak |
| Independence-risk audit | 266 more independent public evidence, 87 unknown, 5 possibly same-source |
| Deterministic tagged rebuild | PASS: two detached tag builds are byte-identical; SHA-256 `328e64adc...d1447` |
| Dataset DOI/reference | Reserved/private: `10.6084/m9.figshare.32984021`; logged-out reviewer access still requires manual verification |
| Code release | Public GitHub repository with immutable dataset-builder and complete-code tags |
| Code DOI | Recommended: not yet minted |
| Author declarations | Blocking: author input required |

## Next actions

1. Supply final author, affiliation, ORCID, contribution, funding, ethics, and conflict metadata.
2. Optionally archive the code tag in Zenodo or another DOI-providing repository.
3. Compile and visually inspect the revised canonical manuscript.
4. Test the Figshare confidential link and public code link from a logged-out browser.
5. Run the final claim–evidence and citation audit.
