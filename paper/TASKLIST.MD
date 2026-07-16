# PT60-Candidate Scientific Data Submission Tasklist

Updated: 2026-07-15
Target article type: **Scientific Data — Data Descriptor**  
Canonical draft route: `paper/main_scidata.tex`
Current status: **final author-review draft; author declarations and reviewer data access remain blocking**

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
- [x] Created and compiled the canonical `paper/main_scidata.pdf`.
- [x] Preserved all 1,342 dispositions: 358 retained and 984 downgraded/rejected.
- [x] Generated the 216-configuration sensitivity sweep.
- [x] Generated the selected 484-node, 358-edge candidate multigraph and graph summaries.
- [x] Generated OSM-derived triangulation for 358 retained branches: 247 strong, 104 medium and 7 weak public-evidence categories; OpenInfraMap is treated only as a visualization of OSM-derived data.
- [x] Recorded a source-role audit and the limitation that OSM-derived evidence is neither proven independent nor ground truth.
- [x] Generated a 13-source provenance/source manifest.
- [x] Separated MIT software licensing from E-REDES-derived data attribution requirements in repository documentation.
- [x] Compiled the current 15-page draft with no undefined citations/references, LaTeX errors or overfull boxes.

## Final consistency revision — 2026-07-15

- [x] Remove the internal LLM-disclosure reminder while retaining the factual author-responsibility disclosure.
- [x] Reconcile facility counts: one exact duplicate among 410 AT-substation source records leaves 409; one exact duplicate among 76 switching-facility records leaves 75; node set B is therefore 484.
- [x] Define archived `confidence_score` as a rule-based endpoint-proximity score with theoretical range 0.65--1.00, observed range 0.6771--0.9691, and an uncalibrated fixed scale factor of 0.35.
- [x] Re-run the selected topology under EPSG:3763. Both projections yield 358 retained branches, but one retained source-line group is exchanged; 357/358 retained groups and their facility assignments are unchanged. Do not claim projection invariance.
- [x] Record fresh topology-critical export SHA-256 hashes, byte sizes, retrieval time, and record counts; confirm that the reacquired inputs reproduce all deposited legacy classification records.
- [x] State that the public acquisition procedure is currently repeatable, while exact deposited-release reconstruction uses frozen derived inputs because upstream API responses may change.
- [x] Regenerate Figure 3 with a complete four-class legend and reduced connected-facility opacity.
- [ ] Verify anonymous reviewer download of the private Figshare record immediately before submission. **Deferred by author on 15 July 2026 because the item is not yet public; supply the confidential review link to the journal.**

## v1.0.1 corrective release status

- [x] Replace substring-based schema inference with token-aware coordinate, geometry, boolean, voltage, score, and count rules.
- [x] Add regression tests for the known v1.0.0 misclassifications and fail-closed semantic validation of the generated dictionary.
- [x] Rebuild a 63-file v1.0.1 archive with 54/54 machine-readable paths covered by 1,090 dictionary records.
- [x] Pass archive extraction, manifest, checksum, schema-coverage, and headline-count validation with no failures.
- [x] Regenerate all four manuscript figures and three generated tables from the v1.0.1 release inputs.
- [x] Update the canonical Scientific Data manuscript to v1.0.1 and disclose the corrected schema logic.
- [x] Replace the Figshare file with the final tagged-build `PT60-Candidate-v1.0.1.tar.gz` and verify the downloaded SHA-256 `3240cd741e6af985c2d7e631856cc3482443ab56d0e4a687cdb3b63c6c957271`.
- [x] Create the immutable annotated `pt60-candidate-v1.0.1` source tag at commit `ab6cf36` and verify a detached clean-worktree rebuild from frozen derived inputs. A separate software DOI is not claimed.

Tagged v1.0.1 source commit: `ab6cf36`. The final stabilized tagged-build archive SHA-256 is `3240cd741e6af985c2d7e631856cc3482443ab56d0e4a687cdb3b63c6c957271`; the author confirmed that the Figshare replacement and downloaded archive hash match. Logged-out reviewer-link verification remains manual.

## Submission-review revision batch — 2026-07-14

This batch records the latest manuscript-level review. Items are ordered by their effect on initial submission. A checked item must be reflected in the canonical LaTeX source, regenerated PDF/DOCX, and—where relevant—a reproducible supporting artifact. Items that require facts only the authors can supply remain explicitly blocked rather than being filled with invented text.

### P0 — Direct submission blockers

- [x] Keep the abstract within the current Scientific Data recommendation (approximately 170 words), remove DOI/download details, and retain only compact negative-control results. **Verified current state:** approximately 146 whitespace-delimited words and no DOI or download URL.
- [x] Convert every in-text citation to sequential numerical Nature style and embed the numbered reference list in the LaTeX submission source.
- [x] Publish the exact parameter-selection rule: formula, term definitions, weights, absence of normalization, and deterministic tie handling; state explicitly that the result is a pipeline design choice rather than externally validated optimality.
- [x] Distinguish repeatable public acquisition from exact reconstruction of the deposited release: the API procedure currently succeeds, while the frozen derived inputs and tagged build fix the exact released bytes.
- [x] Re-run the topology-critical public acquisition with the required public widget keys and record HTTP success, record counts, byte sizes, SHA-256 digests, and acquisition timestamps in a machine-readable audit.
- [x] Confirm that the reacquired inputs reproduce every deposited classification record under the v1.0.1 equirectangular build. Do not promise that future dynamic API responses will remain byte-identical.
- [x] Add a compact Methods table for the topology-critical E-REDES inputs with portal identifier, snapshot evidence, access date, exact retrieval route/status, record count, and role; point to the 13-source machine-readable manifest for the full inventory.

### P1 — Likely reviewer questions

- [x] Replace language that presents OSM and OpenInfraMap as independent sources with “OSM-derived public evidence, visualized through OpenInfraMap where applicable”; state that OpenInfraMap is a visualization of OSM-derived data and that source independence cannot be proven.
- [x] Remove “separately maintained public geospatial layer”; explain that 358/358 non-empty OSM-derived evidence categories are concordance coverage, not external validation.
- [x] Audit the custom local equirectangular workspace against spherical great-circle distance and re-run the selected configuration under EPSG:3763. The retained count remains 358, but one branch is exchanged; 357/358 retained source-line groups and their facility assignments are unchanged.
- [x] Define `confidence_score`: exact formula, theoretical $[0.65,1.00]$ retained-branch range, observed 0.6771--0.9691 range, fixed uncalibrated 0.35 scaling convention, four-decimal rounding, rule-score status, and non-use in retention or validation claims.
- [x] Replace the degree-distribution overview with a publication-ready geographic distribution figure showing 484 facilities, 358 candidate branches, isolates, and largest-component membership without facility labels or a misleading basemap.
- [x] Reconcile all 11 source records labelled 130 kV: they merge into five ledger candidates (three self-loops, one single-facility, one isolated), with none retained and no mixed-voltage circuit.

### P2 — Language, structure, and presentation

- [x] Reduce repeated limitation wording across the abstract, Technical Validation, and Usage Notes while preserving every distinct claim boundary.
- [x] Expand Background & Summary with verified European/open-grid, OSM reconstruction, dataset-documentation, and FAIR literature; DOI metadata for the two new references was independently retrieved.
- [x] Simplify Figure 1 box text, increase box typography to 13--14 pt in the source canvas, re-export, and inspect the rendered figure.
- [x] Replace machine-only evidence-category labels in Table 4 with concise human-readable labels; retain traceability to machine codes in the caption and deposited table.
- [x] Remove the front-page line “Scientific Data draft; public-source validation route”.
- [ ] Complete author names, affiliations, Author Contributions, Funding, Competing Interests, and applicable Acknowledgements. **Blocked:** requires author-supplied facts and declarations.
- [x] Keep the terminal section order aligned with the current Scientific Data guidance: Data Availability, Code Availability, References, Author Contributions, Competing Interests, Acknowledgements, and Funding.
- [x] Regenerate and inspect the canonical PDF and DOCX; confirm sequential numeric citations, figure legibility, table labels, section order, abstract word count, and absence of unresolved LaTeX references. Author/declaration placeholders remain only under the explicitly blocked author-information item above.

**Batch completion rule:** P0 is not complete while raw/API-to-release reproducibility is overstated, the selection rule is undisclosed, or citations remain author–year. The manuscript may explicitly document an unavailable historical raw snapshot, but it must not imply that this limitation has been technically solved.

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

**Current status:** the minimized deterministic release builder is frozen at commit `1eb690302f35f7c0090c14c994d073539cbb5335` and annotated tag `pt60-candidate-v1.0.0`. The deterministic final-tag tarball has been uploaded to Figshare under DOI `10.6084/m9.figshare.32984021`; the user verified that the downloaded archive SHA-256 matches the local frozen archive (`b4a6c370fbf15c078e15b80a3bbc7f517750fdb763d59147b24f59cd160358d7`). The code DOI remains pending under P0.11.

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

**Current result:** the deposited `internal_validation_summary.json` records 25 checks, with 24 passes and one release-review warning. The warning concerned tagged clean-room equality before the tag existed and was subsequently closed by two identical stabilized rebuilds from annotated tag `pt60-candidate-v1.0.1` at commit `ab6cf36`, using the frozen derived inputs. The final tagged-build SHA-256 is `3240cd741e6af985c2d7e631856cc3482443ab56d0e4a687cdb3b63c6c957271`. Measured counts remain 5,334/5,334 valid source geometries, 358 retained branches, 984 downgraded/rejected records, 1,342 ledger rows, 216 sensitivity rows, 484 GraphML nodes, 358 GraphML edges, 109 isolates and 28 extra parallel-edge records.

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
- [x] Ensure every release documentation file exists in the archive and every archive file is documented.

**Current result:** built a minimized Figshare-ready main public archive at `data/releases/PT60-Candidate-v1.0.0/` and `data/releases/PT60-Candidate-v1.0.0.tar.gz` with `src/build_pt60_release_archive.py`. Archive validation reports `PASS`: 63 files, 63 manifest records, no missing documentation, no documented-missing files, no missing required files, no checksum mismatches and no missing checksum paths. The deterministic final-tag tarball SHA-256 is `b4a6c370fbf15c078e15b80a3bbc7f517750fdb763d59147b24f59cd160358d7`; uncompressed size is 19M and compressed size is 5.1M. The main archive includes core topology files, provenance, source/release boundary metadata, table-level validation evidence, sanitized OSM/OpenInfraMap branch-level independence audit fields, schema/data dictionary files and `inventory/headline_counts.json`. It excludes private/reviewer/account Figshare links, manuscript source/PDFs, `TASKLIST.MD`, generated paper figures, root release bookkeeping metadata, development-machine paths, raw OSM/OpenInfraMap cache/history blobs, OSM user/change-set history fields, Stage 1–5 optional interfaces and non-operational diagnostics. Optional interfaces/diagnostics should be released only as a separate clearly labelled supplementary archive if needed.

**Done when:** one downloadable archive contains no undocumented files, broken paths or development-only artifacts.

**Artifacts:** `data/releases/PT60-Candidate-v1.0.0/`, `data/releases/PT60-Candidate-v1.0.0.tar.gz`, `manifest.json`, `checksums.sha256`, `archive_validation_summary.json`, `excluded_artifacts.json`.

### P0.8 Complete schemas, dictionary, CRS, units and missing-value semantics

- [x] Produce a field dictionary for every public CSV/JSON field and GraphML node/edge attribute.
- [x] Record description, type, nullability, unit, allowed values/range, missing representation, semantic status, derivation/source and key role.
- [x] State exact source, processing and release CRS with EPSG identifiers.
- [x] State axis order, geometry encoding and distance/geometry units.
- [x] Document joins among retained branches, ledger, GraphML, manifests and validation records.
- [x] Add machine-readable schemas for principal core files.

**Current result:** generated `data/schema/PT60-Candidate-v1.0.1/` with `src/build_release_schema_dictionary.py` and embedded it into the versioned release archive under `schema/`. Coverage check passes: all 54 public CSV/JSON/GraphML paths in the minimized main archive have data-dictionary records, with 0 missing and 0 extra documented paths. The dictionary contains 1,090 field, JSON-pointer and GraphML-attribute records across core topology, validation, provenance, release-control, inventory and schema documentation files. Fifteen principal machine-readable schemas were generated for core topology, provenance, validation, archive-control and GraphML files. CRS/geometry documentation confirms that the portal exports and release geometries use WGS84/EPSG:4326 GeoJSON-style longitude-latitude coordinates based on the Opendatasoft v2.1 default and the recorded export URLs, and documents the processing metric workspace as a custom local equirectangular Portugal CRS with explicit `lon0=-8.532604`, `lat0=39.567953`, and distance fields in metres or kilometres. The processing metric workspace has no EPSG code because it is a pipeline-defined local projection.

**Done when:** a user can interpret and join the release without reading Python source.

**Artifacts:** `data/releases/PT60-Candidate-v1.0.0/schema/data_dictionary.csv`, `file_schema_summary.csv`, `join_relationships.csv`, `crs_and_geometry.md`, `crs_and_geometry.json`, `json_schemas/`, `schema_build_summary.json`.

### P0.9 Generate a checksum-linked inventory

- [x] Record relative path, purpose, media type, bytes, SHA-256, row/items, schema version, dataset version and generator commit.
- [x] Record upstream source IDs, semantic role, license class and public/restricted status.
- [x] Validate all hashes after the final archive is built.
- [x] Generate article headline counts from frozen inventory/upstream summaries.

**Current result:** upgraded `manifest.json` into the canonical checksum-linked release inventory. Each manifest record now includes `path`, `purpose`, `semantic_role`, `source_path`, `media_type`, `bytes`, `sha256`, `row_or_item_count`, `dataset_version`, `schema_version`, `generator_commit`, `upstream_source_ids`, `license_class`, `public_release_status` and `artifact_access_class`. `checksums.sha256` reconciles with the archive: 0 checksum mismatches and 0 missing checksum paths. `inventory/headline_counts.json` records frozen article counts, source manifest summaries, validation counts, schema counts and claim boundaries for the Data Record and Technical Validation. Archive validation remains `PASS` with 63 files and 63 manifest records.

**Done when:** `manifest.json` and `checksums.sha256` reconcile with the downloadable archive.

**Artifacts:** `manifest.json`, `checksums.sha256`, `inventory/headline_counts.json`, `archive_validation_summary.json`.

### P0.10 Deposit data and mint persistent identifiers

- [x] Select a formal repository with stable download and confidential reviewer-access support.
- [x] Upload the frozen archive.
- [x] Provide anonymous first-round reviewer access if the final record is not yet public.
- [x] Mint a version-specific dataset DOI no later than the journal-required stage; preferably make it live before submission.
- [x] Add a formal dataset citation to References, Data Record and Data Availability.

**Current result:** Figshare hosts the deterministic final-tag archive under DOI `10.6084/m9.figshare.32984021`; confidential reviewer access is available separately and private reviewer/account URLs are intentionally absent from the public archive. The user downloaded the deposited `PT60-Candidate-v1.0.0.tar.gz` and verified that its SHA-256 matches the frozen local archive: `b4a6c370fbf15c078e15b80a3bbc7f517750fdb763d59147b24f59cd160358d7`. The formal dataset citation now appears in References, Data Record and Data Availability.

**Done when:** reviewers can download the exact dataset without contacting authors or exposing reviewer identity.

### P0.11 Create an immutable code release

- [x] Select the exact generating code commit.
- [x] Create a versioned release tag.
- [ ] Archive the code in a DOI-providing repository where feasible.
- [x] Include environment lock, Python version and shortest core reproduction chain.
- [x] Keep MIT code licensing separate from E-REDES-derived data terms.

**Current result:** final local immutable v1.0.1 source release created at commit `ab6cf36` with annotated tag `pt60-candidate-v1.0.1`. The build resolves the generator commit from Git HEAD and normalizes generated timestamps plus gzip/tar metadata for byte-reproducible packaging. Environment record uses Python `3.12.9` and the versioned `requirements.txt`. Code DOI remains optional until formal external archiving.

**Public-code update:** the source repository is public at `https://github.com/MingLeiZhou/PortugueseOPD`, and the deterministic v1.0.1 dataset builder is fixed at `pt60-candidate-v1.0.1`/`ab6cf36`. A DOI-providing software archive remains recommended but is no longer an access blocker.

**Done when:** Code Availability identifies an immutable release capable of regenerating the core archive.

### P0.12 Run clean-room reproduction

- [x] Rebuild the core release from a clean tagged checkout using the frozen derived input set.
- [x] Verify recorded source IDs and catalog-check dates, and document that raw-response input hashes are unavailable.
- [x] Compare regenerated outputs with frozen hashes.
- [x] Regenerate every quantitative paper figure/table from frozen artifacts.
- [x] Record runtime, software versions and network/manual prerequisites.

**Current result:** package extraction validation and deterministic tagged rebuild both pass. A detached clean worktree of `pt60-candidate-v1.0.1` (`ab6cf36`) rebuilt the release from the frozen derived core/validation inputs and produced archive SHA-256 `3240cd741e6af985c2d7e631856cc3482443ab56d0e4a687cdb3b63c6c957271`. The archive contains 63 manifest records/files, 54 machine-readable paths, 54 dictionary paths and 1,090 dictionary records, with zero checksum/schema/headline-count failures. All three quantitative main-text figures and the quantitative release/validation tables regenerate from `data/releases/PT60-Candidate-v1.0.1`; provenance reports record input and output hashes. A 15 July 2026 repeat public acquisition also recorded raw export hashes, sizes, timestamps, and counts and reproduced all deposited legacy classification records. Exact deposited bytes remain anchored by frozen derived inputs because future upstream API responses may change.

**Done when:** the clean run produces identical hashes or documents narrowly defined acceptable differences.

**Artifacts:** `data/metadata/clean_room_archive_validation_summary.json`, `reports/108_pt60_clean_room_archive_validation.md`, `reports/109_pt60_p0_12_reproducibility_figure_docx_audit.md`, `paper/figures/generated/main_quantitative_figure_build.json`, `paper/generated_tables/table_build_provenance.json`, `src/validate_release_archive_package.py`.

### P0.13 Make the public-source manuscript route canonical

- [x] Promote the public-source route to the sole submission entry point.
- [x] Remove dormant dual-review text and `[ADJUDICATED VALIDATION RESULTS NEEDED]` from the submission source.
- [x] Update `submission_readiness.md` and `claim_evidence_matrix.md` so dual review is not listed as mandatory for this route.
- [x] Replace or remove every `[... NEEDED]` placeholder from the canonical submission source and readiness files.
- [x] Replace local `data/processed/...` paths with stable archive paths.
- [x] Align title, abstract, Methods, Data Record, Technical Validation and Usage Notes to the same claim boundary.

**Current result:** `paper/main_scidata.tex` is the sole canonical submission entry point. The manuscript contains only the public-source validation route, uses measured internal/negative-control results, and contains no dormant adjudication branch or `NEEDED` markers. `submission_readiness.md` and `claim_evidence_matrix.md` treat dual review as optional future evidence rather than a requirement for the bounded candidate-dataset claims.

**Done when:** no manuscript/readiness file implies that precision review is part of the submitted evidence.

### P0.14 Finalize Data Record and availability statements

- [x] Rewrite Data Record against the deposited archive rather than the development tree.
- [x] State repository, DOI, version, layout, formats, keys, CRS, units, missing values, licences and relationships.
- [x] Cite the dataset using the journal's data-citation format.
- [x] Make Data Availability repeat the essential DOI/access information from Data Record.
- [x] Place Code Availability at the required final-manuscript location immediately before References.

**Current result:** Data Record documents the 63-file Figshare v1.0.1 archive, its layout, schemas, checksums and reuse boundaries. Figure 3 places a complete four-class legend outside the geographic data region. Code Availability identifies annotated tag `pt60-candidate-v1.0.1` at commit `ab6cf36` and does not claim a separate software DOI. The final tagged-build archive passes package validation and has SHA-256 `3240cd741e6af985c2d7e631856cc3482443ab56d0e4a687cdb3b63c6c957271`; the author confirmed that the Figshare replacement has the same downloaded hash. Anonymous reviewer-link access remains to be tested before submission.

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

- [x] Replace `[NEGATIVE-CONTROL RESULTS NEEDED]` with real results and denominators.
- [ ] Combine internal checks, 216-sweep sensitivity, negative controls and public-source concordance in one claim-bounded table.
- [x] Include null/failure cases and interpret the 358/358 OSM match rate cautiously.
- [x] Keep precision, recall, accuracy and confidence intervals absent without independent truth labels.
- [x] Explain that ACPF non-convergence is an electrical-readiness boundary, not topology validation.

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

1. Replace the private Figshare file with the deterministic final-tag archive and verify SHA-256 `b4a6c370...358d7` after download.
2. Deposit data/code and obtain persistent version identifiers.
3. Replace all manuscript placeholders and run the final claim-evidence audit.
4. Rebuild the public-validation manuscript from frozen release artifacts.
5. Re-run both negative controls and the OSM independence audit from the final clean release tag if any release artifact changes.
6. Confirm Data Record, Data Availability and Code Availability all cite the same frozen version.
7. Re-run `internal_validation_summary.json` after final archive packaging and confirm zero FAIL checks.
8. Ask one data/reproducibility reader to inspect the archive using only the manuscript and schema package.
9. Re-check E-REDES/OpenStreetMap/OpenInfraMap attribution and release language immediately before DOI deposit.
10. Mint dataset/code DOI records and update `CITATION.cff`, Data Availability and Code Availability.
## v1.0.2 EPSG:3763 release transition (current priority)

- [x] Adopt ETRS89 / Portugal TM06 (EPSG:3763) for facility buffering, endpoint clustering, matching, corridor distances, and metric length calculations.
- [x] Re-run the complete 216-configuration sweep and retain the declared selection rule and selected setting.
- [x] Measure the v1.0.1-to-v1.0.2 topology transition: 357/358 retained source-line groups preserve their facility endpoint pair, with one branch removed and one added.
- [x] Publish the branch-level transition mapping and summary in the validation package instead of claiming projection invariance.
- [x] Re-run OSM-derived triangulation, name and spatial negative controls, evidence-independence audit, electrical-readiness checks, and all internal validation.
- [x] Rebuild the v1.0.2 schema package: 58/58 machine-readable paths, 1,201 dictionary records, and 16 principal schemas.
- [x] Build and extraction-validate the 67-file v1.0.2 candidate archive; all 25 internal checks and all archive checks pass.
- [x] Move repeat-acquisition and projection-comparison results from Methods to a dedicated Technical Validation subsection.
- [x] Regenerate Figure 1 with Matplotlib from frozen v1.0.2 JSON, using the same academic style and export pipeline as Figures 2–4.
- [x] Place the 216-setting parameter sweep and documented configuration selection before the retention gate; route OSM-derived public evidence only into validation.
- [x] Reconcile the Data Record archive inventory to 67 documented files and the 500 m sensitivity result to 368 retained branches.
- [x] Confirm from the v1.0.2 spatial-control summary that corridor evidence changes from 290/358 real branches to 1/358 displaced branches.
- [x] Freeze source commit `93c861c` and create annotated tag `pt60-candidate-v1.0.2`.
- [x] Perform two detached clean-worktree rebuilds from the tag and confirm byte-identical archives.
- [x] Insert final tagged-build SHA-256 `328e64adcfc6c7210ed9558793a14fdb661e5e3bab741f1d275a89e5593d1447` and commit identifier into the manuscript and release metadata.
- [ ] Replace the current Figshare file with `PT60-Candidate-v1.0.2.tar.gz`, then verify its downloaded SHA-256. Figshare access testing is intentionally deferred while the item remains private.
