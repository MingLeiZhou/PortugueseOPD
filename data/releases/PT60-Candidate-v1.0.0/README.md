# PT60-Candidate v1.0.0

PT60-Candidate is a provenance-tracked candidate dataset and fail-closed pipeline output for Portuguese 60 kV topology reconstruction from public E-REDES Open Data.

This archive is a candidate-topology dataset, not an operator-validated or operational grid model.

## Main contents

- `core_topology/`: retained candidate branches, full circuit ledger, GraphML export, sensitivity sweep and reconstruction summaries.
- `provenance/`: source manifest, release metadata, licensing and responsible-release boundary.
- `validation/`: public-source triangulation, negative controls, OSM/OpenInfraMap independence audit and internal validation outputs.
- `optional_interfaces/`: Stage 1-5 derivative consumer interfaces.
- `optional_diagnostic/`: non-operational electrical-readiness interface files, where included.
- `manuscript/`: current Scientific Data draft route and generated figures.

## Claim boundary

Use this archive for reconstruction research, geospatial data integration, provenance-aware dataset engineering, graph/tabular interface testing and sensitivity analysis.

Do not use it for operational switching, protection studies, security analysis, contingency analysis, congestion analysis, infrastructure targeting, emergency operations, asset-condition assessment or regulatory/commercial capacity claims.

## Licensing

Repository code is MIT licensed. E-REDES-derived data use the E-REDES Open Data Portal terms recorded in `DATA_LICENSE.md`, `ATTRIBUTION.md` and `provenance/reproduction_source_manifest.json`. Reuse must retain E-REDES attribution, link CC BY 4.0, identify source datasets/access dates and indicate transformations.

## Status

Dataset DOI, code DOI, final schemas/data dictionary and clean-room reproduction are still pending. See `manifest.json`, `checksums.sha256` and `excluded_artifacts.json` for archive contents and exclusions.
