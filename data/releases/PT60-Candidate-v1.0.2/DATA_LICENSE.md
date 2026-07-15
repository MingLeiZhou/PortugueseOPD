# Data licensing and redistribution status

The MIT license in this repository applies to source code only. It does not apply to
downloaded E-REDES data, third-party technical documents, or processed datasets derived
from those sources.

## Current status

The E-REDES Open Data Portal states that data supplied through the portal are covered by
open licenses (CC BY 4.0), that access is unrestricted provided users cite the publisher,
and recommends attribution to:

> E-REDES - Distribuicao de Eletricidade, "E-REDES Open Data Portal".

Portal license statement:

- <https://e-redes.opendatasoft.com/pages/homepage/>
- CC BY 4.0: <https://creativecommons.org/licenses/by/4.0/>

The metadata retrieved for several topology-critical inputs, including the working
datasets named `rede-at-teste`, `se-at_2025`, and `pc-at_2025`, did not duplicate an
explicit license field in their catalog API responses. This is a metadata completeness
issue; the release should archive the portal-wide statement alongside the individual
dataset identifiers and retrieval dates.

Current repository policy:

- raw E-REDES files remain excluded from Git;
- processed data remain excluded from ordinary code commits until a versioned data
  release is prepared;
- a public PT60-Candidate release must carry CC BY 4.0 attribution, source dataset IDs,
  retrieval dates, a link to the license, and an indication of modifications;
- users should consult the portal for current records and terms because the network
  data are dynamic and may be corrected or updated.

## Intended public release

A versioned dataset release should include:

- source dataset identifiers, URLs, access dates, and licenses;
- attribution and indication of modifications required by CC BY 4.0;
- a diagnostic-only and non-operator-grade disclaimer;
- a separate DOI and data license declaration;
- no downloaded manufacturer catalogs or standards unless redistribution is permitted.

The current responsible-release decision is recorded in
`data/metadata/responsible_release_boundary.json` and
`reports/107_pt60_responsible_release_boundary.md`. Under that decision, the core
public archive may include exact derived candidate geometries, facility names/codes,
the circuit ledger, GraphML, sensitivity outputs, public-source OSM evidence URLs, and
validation outputs. Raw E-REDES downloads are excluded from the default public archive in
favour of source identifiers, URLs, access dates and acquisition scripts unless a final
repository/license review explicitly approves raw snapshot deposition.

The generated labels are scenario-derived diagnostic targets. They are not measured
failures, observed congestion events, or verified operating records from E-REDES.
