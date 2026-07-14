# PT60-Candidate v1.0.0

PT60-Candidate is a provenance-tracked candidate dataset and fail-closed pipeline output for Portuguese 60 kV topology reconstruction from public E-REDES Open Data.

This archive is a candidate-topology dataset, not an operator-validated or operational grid model.

## Main contents

- `core_topology/`: retained candidate branches, full circuit ledger, GraphML export, sensitivity sweep and reconstruction summaries.
- `provenance/`: source manifest, licensing and responsible-release boundary.
- `validation/`: public-source triangulation, negative controls, OSM/OpenInfraMap independence audit and internal validation outputs.

## Claim boundary

Use this archive for reconstruction research, geospatial data integration, provenance-aware dataset engineering, graph/tabular interface testing and sensitivity analysis.

Do not use it for operational switching, protection studies, security analysis, contingency analysis, congestion analysis, infrastructure targeting, emergency operations, asset-condition assessment or regulatory/commercial capacity claims.

## Licensing

Repository code is MIT licensed. E-REDES-derived data use the E-REDES Open Data Portal terms recorded in `DATA_LICENSE.md`, `ATTRIBUTION.md` and `provenance/reproduction_source_manifest.json`. Reuse must retain E-REDES attribution, link CC BY 4.0, identify source datasets/access dates and indicate transformations.

## Repository and status

Reserved dataset DOI: `10.6084/m9.figshare.32984021`.

Dataset DOI URL: `https://doi.org/10.6084/m9.figshare.32984021`.

Code DOI remains pending; the frozen local code tag is `pt60-candidate-v1.0.0`. See `manifest.json`, `checksums.sha256`, `schema/`, `inventory/headline_counts.json` and `excluded_artifacts.json` for archive contents, schemas, checksums and exclusions.
