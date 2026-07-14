# PT60-Candidate Scientific Data Submission Tasklist

Updated: 2026-07-14  
Target article type: **Scientific Data — Data Descriptor**  
Canonical draft route: `paper/main_scidata_public_validation.tex`  
Current status: **author-review draft; not submission-ready**

## Locked submission strategy

This version follows the **public-source validation route without dual independent human adjudication**.

Supported claims:

- reproducible, fail-closed dataset construction;
- internal schema, geometry, key, referential and graph consistency;
- sensitivity across 216 reconstruction configurations;
- branch-level concordance with OSM/OpenInfraMap public evidence;
- explicit provenance, rejection reasons and reuse boundaries.

Prohibited claims:

- operator-validated or accurate physical topology;
- independently estimated branch precision;
- national-grid or real-network recall/completeness;
- an operational Portuguese grid model;
- AC power-flow, OPF, protection or security-analysis readiness.

Dual independent review is **not a blocker for this route**. If precision or branch-accuracy claims are added later, the paper must switch to a separately designed evidence-backed review protocol. OSM evidence-category counts cannot be converted into precision or recall.

## Definition of submission-ready

The paper is ready for first-round submission only when:

- every **P0** item below is complete;
- editors and reviewers can download the frozen dataset without revealing reviewer identity;
- manuscript, dataset archive and code release identify the same version;
- the PDF contains no placeholders, development-only paths, unsupported claims or unresolved references;
- Technical Validation contains actual negative-control results, not only a planned protocol.

Scientific Data currently requires reviewer access to the dataset at first submission and expects a formal repository for later review/publication. A DOI-backed repository is the preferred route. The journal does not require or encourage a special manuscript template, but it requires the Data Descriptor headings and Data/Code Availability statements. Re-check these official pages immediately before submission:

- [Submission Guidelines](https://www.nature.com/sdata/submission-guidelines)
- [Data Policies](https://www.nature.com/sdata/policies/data-policies)
- [Repository Guidance](https://www.nature.com/sdata/policies/repositories)
- [Editorial and Code Availability Policies](https://www.nature.com/sdata/policies/editorial-and-publishing-policies)

## Already completed

- [x] Reframed the contribution as a provenance-tracked **candidate dataset** rather than a validated grid model.
- [x] Created and compiled `paper/main_scidata_public_validation.pdf`.
- [x] Preserved all 1,342 dispositions: 358 retained and 984 downgraded/rejected.
- [x] Generated the 216-configuration sensitivity sweep.
- [x] Generated the selected 484-node, 358-edge candidate multigraph and graph summaries.
- [x] Generated OSM/OpenInfraMap triangulation for 358 retained branches: 247 strong, 104 medium and 7 weak public-evidence categories.
- [x] Recorded a four-source role audit and the limitation that OSM/OpenInfraMap is not ground truth.
- [x] Generated a 13-source provenance/source manifest.
- [x] Separated MIT software licensing from E-REDES-derived data attribution requirements in repository documentation.
- [x] Compiled the current 14-page draft with no undefined citations/references, LaTeX errors or overfull boxes.

## P0 — Blocking tasks before initial submission

Complete these in order. Do not prioritize language polishing until P0.1–P0.8 are closed.

### P0.1 Freeze one canonical release frame

- [x] Assign an explicit release version: `PT60-Candidate v1.0.0`.
- [x] Freeze the E-REDES snapshot date, exact source dataset IDs and access dates.
- [x] Freeze node set B, 100 m facility buffer, 0.5 m endpoint snapping and voltage-plus-status-aware merging.
- [x] Freeze 5,334 source features, 1,342 circuit candidates, 358 retained branches, 984 downgraded records, 484 nodes, 216 sweep rows and the OSM cache.
- [x] Record the generator commit, Python version and dependency environment.
- [x] Prevent post-validation threshold tuning in v1.0.0; any change creates a new version and reruns validation.
- [x] Replace the current dirty generator state with an immutable clean commit or release tag before final deposit.

**Current status:** `release_metadata.json` has been created as a machine-readable freeze draft and the local release candidate has been committed/tagged as `pt60-candidate-v1.0.0-rc1` at commit `bc3ea8e45297b0f11204e137fb055acfbbad85f6`. It is not yet the final archival release record because dataset DOI, code DOI and formal repository deposit identifiers are still pending.

**Done when:** one machine-readable release record identifies all frozen inputs, counts, settings, timestamps and generator commit.

**Artifacts:** `release_metadata.json`, frozen source manifest and release tag/commit reference.

### P0.2 Run endpoint-name negative controls

- [x] Implement deterministic endpoint-name permutation or corruption while retaining candidate geometries.
- [x] Record the random seed and preserve relevant distance/component/length strata where applicable.
- [x] Run on all 358 retained branches or justify a pre-specified sample.
- [x] Report the full evidence-category distribution and denominators.
- [x] Compare real versus corrupted-name strong-name match rates.

**Current result:** deterministic length-stratified endpoint-pair shift with seed `20260714`; real strong-name evidence was 183/358 and corrupted-name strong evidence was 9/358, a 95.1% relative reduction. This supports matcher selectivity for endpoint-name evidence only; it is not a topology-accuracy, precision, recall or operator-validation estimate.

**Done when:** a reproducible table shows whether strong name evidence declines after endpoint identity is broken.

**Artifacts:** `matcher_negative_control_names.csv`, summary JSON, command and manuscript result.

### P0.3 Run spatial-alignment negative controls

- [x] Define a deterministic displacement or geometry-permutation control that breaks candidate–OSM alignment without changing record count.
- [x] Pre-specify displacement/permutation rules before examining outcomes.
- [x] Check transformed geometry validity and record boundary exclusions.
- [x] Report strong, medium, weak and no-match counts for real and transformed candidates.
- [x] Interpret the result only as matcher selectivity, not topology accuracy.

**Current result:** fixed spatial translation with seed `20260714`, `+1.75` degrees longitude and `+0.85` degrees latitude; 358/358 transformed geometries remained valid and 0 records were excluded after transformation. Real corridor evidence was 290/358 and displaced-geometry corridor evidence was 1/358, a 99.7% relative reduction. The transformed evidence distribution was 337 no-match, 20 weak nearby and 1 geometry-medium record.

**Done when:** archived results show how often corridor evidence survives deliberately broken alignment.

**Artifacts:** `matcher_negative_control_geometry.csv`, summary JSON, command and manuscript result.

### P0.4 Audit OSM/OpenInfraMap evidence independence

- [x] Inspect OSM source tags, object histories, timestamps and edit provenance for possible derivation from E-REDES.
- [x] Separate name, geometry and operator-tag evidence; `operator=E-REDES` is not operator confirmation.
- [x] Add a controlled independence field such as `unknown`, `possibly_same_source`, or `more_independent_public_evidence`.
- [x] Report match counts by independence category.
- [x] Prefer “public-source triangulation/concordance” wherever independence cannot be established.

**Current result:** audited 5,551 OSM/OpenInfraMap 60 kV records with current metadata and 271 unique matched OSM way histories covering 2,326 historical versions. All 358 matched retained branches have timestamped OSM metadata. Branch-level independence-risk categories are 266 `more_independent_public_evidence`, 87 `unknown`, and 5 `possibly_same_source`. One matched branch has historical source/note fields compatible with EDP/E-REDES-derived public documentation; four weak matches are operator-tag-only. These categories support provenance-risk reporting only, not operator validation.

**Done when:** users can distinguish public concordance from reasonably independent corroboration.

### P0.5 Publish measured internal-validation results

- [x] Export geometry parse/validity, CRS, unique-key, required-null, endpoint and terminal-count results.
- [x] Automatically verify `358 + 984 = 1,342` and reconcile every downgrade class.
- [x] Verify GraphML/CSV counts, identifiers, isolates, parallel edges and orphan references.
- [x] Test deterministic rerun equality or document narrowly defined non-deterministic fields.
- [x] Report missingness by semantic status for critical fields.

**Current result:** `internal_validation_summary.json` reports `PASS_WITH_WARNINGS`: 25 checks total, 23 pass and 2 warn. The warnings are not row/graph consistency failures: exact source/release CRS still needs final Data Records wording, and the current artifact hashes are recorded as a deterministic baseline while clean-room rerun equality remains P0.12. Measured counts include 5,334/5,334 valid source geometries, 358 retained branches, 984 downgraded/rejected records, 1,342 ledger rows, 216 sensitivity rows, 484 GraphML nodes, 358 GraphML edges, 109 isolates and 28 extra parallel-edge records.

**Done when:** Technical Validation reports actual counts and pass/fail outcomes linked to a versioned validation file.

**Artifacts:** `internal_validation_summary.json`, detailed CSV and manuscript validation table.

### P0.6 Decide the responsible public-release boundary

- [x] Decide whether exact geometries, facility names and evidence URLs can be included under applicable terms and the responsible-release assessment.
- [x] Document aggregation risks and mitigation for public infrastructure geography.
- [x] Prohibit operational switching, protection, security, congestion, contingency and infrastructure-targeting uses.
- [x] List every withheld/generalized artifact, the reason and the effect on reproducibility.
- [x] Confirm E-REDES attribution and indication-of-modification requirements for all derivatives.

**Current result:** release-boundary decision is `completed_with_conditions`. Core derived retained-branch geometries, facility names/codes, GraphML, circuit ledger, sensitivity sweep, source manifest, OSM evidence URLs, negative controls and validation outputs can be included in the public archive with attribution, modification notices and responsible-use language. Raw E-REDES downloads are excluded from the default public archive in favour of source IDs/URLs/access dates; non-open third-party documents are excluded; ACPF/DCOPF diagnostics are excluded from the core archive or must be labelled optional diagnostic. The archive must prohibit operational switching, protection, security, contingency, congestion, infrastructure-targeting, emergency-operation, asset-condition and regulatory-capacity uses.

**Done when:** manuscript claims match a public/restricted/excluded decision for every artifact class.

### P0.7 Build the versioned dataset archive

- [x] Create a clean public release directory.
- [x] Include `README.md`, `CITATION.cff`, `DATA_LICENSE.md`, `ATTRIBUTION.md`, `CHANGELOG.md`, `manifest.json` and `checksums.sha256`.
- [x] Include retained branches, full circuit ledger, GraphML, 216-sweep table, provenance/source manifests and validation outputs.
- [x] Place ACPF and Stage 1–5 materials in clearly labelled optional directories or exclude them with documented reasons.
- [x] Ensure every manuscript file exists in the archive and every archive file is documented.

**Current result:** built `data/releases/PT60-Candidate-v1.0.0/` and `data/releases/PT60-Candidate-v1.0.0.tar.gz` with `src/build_pt60_release_archive.py`. Archive validation reports `PASS`: 183 files, 183 manifest records, no missing documentation, no documented-missing files, no missing required files, no checksum mismatches and no missing checksum paths. The tarball SHA-256 is `0ca66cd5b0ec7e1a6747a8cf20e79edd0497acb0e15b611a2dfa7d0198fe31e1`; uncompressed size is 42M and compressed size is 9.7M. Core files, provenance, validation outputs, reports, schema/data dictionary files, manuscript support files, generated figures and `inventory/headline_counts.json` are included. Stage 1–5 derivative interfaces are included under `optional_interfaces/`; non-operational diagnostic files are included under `optional_diagnostic/`; raw E-REDES downloads, third-party parameter documents, ACPF/DCOPF operational diagnostics and dormant dual-review outputs are excluded or documented in `excluded_artifacts.json`.

**Done when:** one downloadable archive contains no undocumented files, broken paths or development-only artifacts.

**Artifacts:** `data/releases/PT60-Candidate-v1.0.0/`, `data/releases/PT60-Candidate-v1.0.0.tar.gz`, `manifest.json`, `checksums.sha256`, `archive_validation_summary.json`, `excluded_artifacts.json`.

### P0.8 Complete schemas, dictionary, CRS, units and missing-value semantics

- [x] Produce a field dictionary for every public CSV/JSON field and GraphML node/edge attribute.
- [x] Record description, type, nullability, unit, allowed values/range, missing representation, semantic status, derivation/source and key role.
- [x] State exact source, processing and release CRS with EPSG identifiers.
- [x] State axis order, geometry encoding and distance/geometry units.
- [x] Document joins among retained branches, ledger, GraphML, manifests and validation records.
- [x] Add machine-readable schemas for principal core files.

**Current result:** generated `data/schema/PT60-Candidate-v1.0.0/` with `src/build_release_schema_dictionary.py` and embedded it into the versioned release archive under `schema/`. Coverage check passes: all 143 public CSV/JSON/GraphML paths in the release have data-dictionary records, with 0 missing and 0 extra documented paths. The dictionary contains 7,601 field, JSON-pointer and GraphML-attribute records across core topology, validation, provenance, optional interfaces, optional diagnostics, release-control, inventory and schema documentation files. Sixteen principal machine-readable schemas were generated for core topology, provenance, validation, archive-control and GraphML files. CRS/geometry documentation states release geometries as WGS84/EPSG:4326 GeoJSON-style longitude-latitude coordinates, the processing metric workspace as a custom local equirectangular Portugal CRS with explicit `lon0=-8.532604`, `lat0=39.567953`, and distance fields in metres or kilometres. The processing metric workspace has no EPSG code because it is a pipeline-defined local projection.

**Done when:** a user can interpret and join the release without reading Python source.

**Artifacts:** `data/releases/PT60-Candidate-v1.0.0/schema/data_dictionary.csv`, `file_schema_summary.csv`, `join_relationships.csv`, `crs_and_geometry.md`, `crs_and_geometry.json`, `json_schemas/`, `schema_build_summary.json`.

### P0.9 Generate a checksum-linked inventory

- [x] Record relative path, purpose, media type, bytes, SHA-256, row/items, schema version, dataset version and generator commit.
- [x] Record upstream source IDs, semantic role, license class and public/restricted status.
- [x] Validate all hashes after the final archive is built.
- [x] Generate manuscript headline counts from frozen inventory/upstream summaries.

**Current result:** upgraded `manifest.json` into the canonical checksum-linked release inventory. Each manifest record now includes `path`, `purpose`, `semantic_role`, `source_path`, `media_type`, `bytes`, `sha256`, `row_or_item_count`, `dataset_version`, `schema_version`, `generator_commit`, `upstream_source_ids`, `license_class`, `public_release_status` and `artifact_access_class`. `checksums.sha256` reconciles with the archive: 0 checksum mismatches and 0 missing checksum paths. `inventory/headline_counts.json` records frozen manuscript counts, source manifest summaries, validation counts, schema counts and claim boundaries for Data Records and Technical Validation. Archive validation remains `PASS` with 183 files and 183 manifest records.

**Done when:** `manifest.json` and `checksums.sha256` reconcile with the downloadable archive.

**Artifacts:** `manifest.json`, `checksums.sha256`, `inventory/headline_counts.json`, `archive_validation_summary.json`.

### P0.10 Deposit data and mint persistent identifiers

- [ ] Select a formal repository with stable download and confidential reviewer-access support.
- [ ] Upload the frozen archive.
- [ ] Provide anonymous first-round reviewer access if the final record is not yet public.
- [ ] Mint a version-specific dataset DOI no later than the journal-required stage; preferably make it live before submission.
- [ ] Add a formal dataset citation to References, Data Records and Data Availability.

**Done when:** reviewers can download the exact dataset without contacting authors or exposing reviewer identity.

### P0.11 Create an immutable code release

- [x] Select the exact generating code commit.
- [x] Create a versioned release tag.
- [ ] Archive the code in a DOI-providing repository where feasible.
- [x] Include environment lock, Python version and shortest core reproduction chain.
- [x] Keep MIT code licensing separate from E-REDES-derived data terms.

**Current result:** local immutable code release candidate created. Exact commit: `bc3ea8e45297b0f11204e137fb055acfbbad85f6`. Annotated local tag: `pt60-candidate-v1.0.0-rc1`. The tagged tree includes the release archive, schema package, validation reports, paper support files and generating/validation scripts. Environment record uses Python `3.12.9` and `requirements.txt` SHA-256 `3041f3f6e70697cdcf6a462c9613368079ce2344b694230f78e8184ad5e23cef`. Code DOI remains pending until formal external archiving.

**Done when:** Code Availability identifies an immutable release capable of regenerating the core archive.

### P0.12 Run clean-room reproduction

- [ ] Rebuild the core release from a clean checkout and environment.
- [ ] Verify downloaded source IDs, retrieval dates and expected input hashes.
- [x] Compare regenerated outputs with frozen hashes.
- [ ] Regenerate every quantitative paper figure/table from frozen artifacts.
- [x] Record runtime, software versions and network/manual prerequisites.

**Current result:** package-level clean-room validation completed with `src/validate_release_archive_package.py`. The validator extracts only `data/releases/PT60-Candidate-v1.0.0.tar.gz` into a temporary directory and checks manifest records, required files, manifest SHA-256 values, `checksums.sha256`, schema/data-dictionary coverage and frozen headline counts without relying on the development release directory. Result is `PASS`: archive SHA-256 `0ca66cd5b0ec7e1a6747a8cf20e79edd0497acb0e15b611a2dfa7d0198fe31e1`, 183 manifest records, 183 extracted files, 143 machine-readable paths, 143 dictionary paths, 7,601 dictionary records, 0 checksum mismatches, 0 missing checksum paths and all headline count checks passed. Full source-to-archive regeneration from raw/API inputs remains pending and must be run from the immutable tag before final DOI deposit.

**Done when:** the clean run produces identical hashes or documents narrowly defined acceptable differences.

**Artifacts:** `data/metadata/clean_room_archive_validation_summary.json`, `reports/108_pt60_clean_room_archive_validation.md`, `src/validate_release_archive_package.py`.

### P0.13 Make the public-source manuscript route canonical

- [ ] Promote the public-source route to the sole submission entry point.
- [ ] Remove dormant dual-review text and `[ADJUDICATED VALIDATION RESULTS NEEDED]` from the submission source.
- [ ] Update `submission_readiness.md` and `claim_evidence_matrix.md` so dual review is not listed as mandatory for this route.
- [ ] Replace or remove every `[... NEEDED]` placeholder.
- [ ] Replace local `data/processed/...` paths with stable archive paths.
- [ ] Align title, abstract, Methods, Data Records, Technical Validation and Usage Notes to the same claim boundary.

**Done when:** no manuscript/readiness file implies that precision review is part of the submitted evidence.

### P0.14 Finalize Data Records and availability statements

- [ ] Rewrite Data Records against the deposited archive rather than the development tree.
- [ ] State repository, DOI, version, layout, formats, keys, CRS, units, missing values, licenses and relationships.
- [ ] Cite the dataset using the journal's data-citation format.
- [ ] Make Data Availability repeat the essential DOI/access information from Data Records.
- [ ] Place Code Availability at the required final-manuscript location immediately before References.

**Done when:** a reader can find, download, interpret and cite the exact dataset using the paper alone.

### P0.15 Complete authorship and governance metadata

- [ ] Add author names, affiliations, countries, corresponding email and ORCIDs.
- [ ] Add CRediT-style Author Contributions.
- [ ] Add funding, applicable acknowledgements and final Competing Interests.
- [ ] Record the actual institutional ethics determination; do not invent approval/exemption language.
- [ ] Add maintainer/contact, versioning policy and errata channel.
- [ ] Determine whether substantial AI-assisted writing/research use requires disclosure under the current Springer Nature policy.

**Done when:** all author/declaration placeholders are resolved and match submission-system metadata.

## P1 — High-value improvements

### P1.1 Strengthen Technical Validation presentation

- [ ] Replace `[NEGATIVE-CONTROL RESULTS NEEDED]` with real results and denominators.
- [ ] Combine internal checks, 216-sweep sensitivity, negative controls and public-source concordance in one claim-bounded table.
- [ ] Include null/failure cases and interpret the 358/358 OSM match rate cautiously.
- [ ] Keep precision, recall, accuracy and confidence intervals absent without independent truth labels.
- [ ] Explain that ACPF non-convergence is an electrical-readiness boundary, not topology validation.

### P1.2 Reframe figures around the released dataset

- [ ] Keep the fail-closed pipeline as Figure 1.
- [ ] Keep the reconstruction funnel with all totals reconciled.
- [ ] Redraw release architecture around core CSV, ledger, GraphML, schemas, provenance and validation; make Stage 1–5 optional.
- [ ] Keep the 216-sweep figure and label the selected setting as documented, not optimal.
- [ ] Add one negative-control/public-evidence figure only if it is clearer than a table.
- [ ] Check final-size legibility, grayscale and colour-blind accessibility.

### P1.3 Remove out-of-scope research-article material

- [ ] Remove/move detailed ACPF 343-run localization from the main Data Descriptor narrative.
- [ ] Move Ridge/GBDT metrics, Stage 1–5 implementation details and proxy DCOPF scenarios to repository notes or narrowly justified SI.
- [ ] Do not use SI to retain results/discussion outside Data Descriptor scope.
- [ ] Keep only short readiness/interface boundary statements in the main text.

### P1.4 Complete reuse-oriented Usage Notes

- [ ] Provide concise CSV and GraphML loading instructions.
- [ ] Explain isolate and parallel-edge handling.
- [ ] Explain joins among retained branches, complete ledger and validation records.
- [ ] Show semantic-status filtering and missing/proxy/scenario-field handling.
- [ ] State software/environment requirements.
- [ ] List intended/prohibited uses without writing a conclusion or promotional section.

### P1.5 Verify citations and positioning

- [ ] Verify every bibliography record, DOI and dataset citation.
- [ ] Confirm every citation supports its surrounding claim.
- [ ] Compare PT60-Candidate with nearest open-grid datasets on provenance, rejection ledgers, sensitivity records and reuse scope.
- [ ] Remove subjective novelty, importance and utility claims from Background & Summary.
- [ ] Disclose and cite any previously released or overlapping PT60 artifacts.

### P1.6 Optional single-reviewer evidence audit

- [ ] If resources permit, audit a pre-specified stratified sample with one evidence-backed reviewer.
- [ ] Publish evidence references, instructions, exclusions and uncertain cases.
- [ ] Call it a `single-reviewer evidence audit` or `sample confirmation rate`, not independent precision.
- [ ] Do not block submission on this item if P0 is complete and claims remain bounded.

## P2 — Final submission audit

### P2.1 Re-check current Scientific Data requirements

- [ ] Re-open official submission, data, repository and code policies.
- [ ] Confirm mandatory headings and declaration order.
- [ ] Confirm first-round data access and later-round repository requirements.
- [ ] Confirm current data citation, figure/file and AI-disclosure requirements.
- [ ] Do not adapt to an unofficial template; Scientific Data currently does not require one.

### P2.2 Build and inspect the final manuscript package

- [ ] Compile LaTeX/BibTeX from a clean paper-source directory.
- [ ] Confirm zero undefined citations/references, LaTeX errors and clipped/overfull content.
- [ ] Inspect every page for float order, table width, figure text and blank pages.
- [ ] Remove author-review annotations and verify PDF metadata.
- [ ] Test every public/reviewer URL from a logged-out browser.

### P2.3 Run final claim–evidence audit

- [ ] Trace every headline number to a frozen artifact and command.
- [ ] Search for `validated`, `accurate`, `precision`, `recall`, `ground truth`, `operational` and `power-flow-ready`.
- [ ] Confirm each occurrence is negated, bounded or supported.
- [ ] Check captions against body-text claim boundaries.
- [ ] Confirm no mock, simulated or planned result is presented as evidence.

### P2.4 Obtain external readability/reproducibility checks

- [ ] Ask one power-systems reader to assess terminology and operational boundaries.
- [ ] Ask one data/reproducibility reader to download and inspect the archive using only the manuscript.
- [ ] Resolve blocking misunderstandings and archive failures.
- [ ] Record remaining comments and their disposition.

### P2.5 Prepare submission files

- [ ] Final manuscript source and PDF.
- [ ] Formal dataset citation and accessible repository record.
- [ ] Code release citation and repository record.
- [ ] SI PDF only if genuinely necessary; prefer repository-hosted dictionaries/technical notes.
- [ ] Cover letter summarizing dataset, reuse value, validation boundaries and data/code access.
- [ ] Complete author, funding, competing-interest and submission-system metadata.

## Deferred and explicitly non-blocking

- [ ] Operator confirmation of individual branches.
- [ ] Dual independent retained-branch review and adjudicated precision.
- [ ] National-grid recall estimation.
- [ ] Complete branch-specific R/X/B/current and verified transformer controls.
- [ ] AC power-flow or OPF-ready Portuguese operating model.
- [ ] Expanded ML benchmark claims from scenario-derived labels.

These tasks require new evidence and materially broader claims. They are not required for the current candidate-dataset route and must not be implied by v1.0.0.

## Recommended next 10 actions

1. Run full source-to-archive clean-room reproduction from `pt60-candidate-v1.0.0-rc1`.
2. Deposit data/code and obtain persistent version identifiers.
3. Replace all manuscript placeholders and run the final claim-evidence audit.
4. Rebuild the public-validation manuscript from frozen release artifacts.
5. Re-run both negative controls and the OSM independence audit from the final clean release tag if any release artifact changes.
6. Confirm Data Records, Data Availability and Code Availability all cite the same frozen version.
7. Re-run `internal_validation_summary.json` after final archive packaging and confirm zero FAIL checks.
8. Ask one data/reproducibility reader to inspect the archive using only the manuscript and schema package.
9. Re-check E-REDES/OpenStreetMap/OpenInfraMap attribution and release language immediately before DOI deposit.
10. Mint dataset/code DOI records and update `CITATION.cff`, Data Availability and Code Availability.
