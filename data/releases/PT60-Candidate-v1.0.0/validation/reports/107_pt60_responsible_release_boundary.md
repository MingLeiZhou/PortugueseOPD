# 107 PT60 Responsible Release Boundary

Generated: 2026-07-14T13:52:00Z

## Decision

PT60-Candidate v1.0.0 should be released as a public derived candidate-topology dataset with attribution and responsible-use limits.

The core public archive should include exact retained-branch geometries, endpoint facility names/codes, the full circuit ledger, GraphML, the 216-row sensitivity sweep, source manifests, OSM/OpenInfraMap public-source evidence, negative-control outputs, independence-audit outputs, and internal-validation outputs.

This decision is conditional on preserving E-REDES attribution, source dataset identifiers, access dates, CC BY 4.0 license links, and transformation notices in the final archive.

## Include Public

- Retained branch table with exact candidate geometries.
- Full retained/downgraded/rejected circuit ledger with exact derived geometries.
- GraphML candidate multigraph.
- Reconstruction sensitivity sweep.
- Source manifest and license metadata.
- OSM/OpenInfraMap match table, object IDs/URLs, independence-risk categories, and audit summaries.
- Negative-control and internal-validation outputs.
- Optional Stage 4/5 consumer interfaces, if clearly labelled as derivative/diagnostic interfaces.

## Exclude or Keep Outside the Core Archive

- Raw E-REDES downloads by default. Publish source IDs, URLs, checked-at timestamps and acquisition scripts; only deposit raw snapshots if the final repository/license review explicitly approves it.
- Manufacturer catalogs, standards excerpts and non-open third-party documents.
- ACPF/DCOPF operational diagnostics from the core archive. If retained, place them under an optional diagnostic directory and label them non-operational.

## Responsible-Use Boundary

Allowed uses:

- topology reconstruction research;
- geospatial data integration;
- provenance-aware dataset engineering;
- graph and tabular consumer-interface testing;
- uncertainty and sensitivity analysis.

Not supported uses:

- operational switching;
- protection studies;
- security analysis;
- contingency analysis;
- congestion analysis;
- infrastructure targeting;
- emergency operations;
- asset condition assessment;
- commercial or regulatory statements about actual network capacity.

Required manuscript/archive language:

- candidate topology;
- public-source concordance;
- not operator-validated;
- not an operational grid model;
- electrical parameters incomplete or diagnostic.

Prohibited language:

- operator validated;
- ground truth;
- precision;
- recall;
- operational model;
- AC power-flow ready;
- OPF ready.

## License and Attribution Notes

The frozen source manifest records E-REDES Open Data Portal CC BY 4.0 terms, required attribution, source dataset IDs and access dates. The final archive must retain:

- E-REDES attribution;
- CC BY 4.0 link;
- source dataset IDs and URLs;
- checked-at/access dates;
- indication that PT60-Candidate records are transformed/derived products.

The MIT license applies only to repository code. It does not replace the E-REDES-derived data terms.

## Remaining Submission Checks

- Re-check E-REDES Open Data portal license and dataset catalog metadata immediately before DOI deposit.
- Add `ATTRIBUTION.md` and modification notice to the versioned archive.
- State exact source/release CRS and geometry encoding in Data Records and the data dictionary.
- Mark every archive artifact as core, optional diagnostic, excluded, or source-manifest-only.
- Do not include third-party catalogs or standards unless redistribution permission is documented.
