# Data licensing and redistribution status

The MIT license in this repository applies to source code only. It does not apply to
downloaded E-REDES data, third-party technical documents, or processed datasets derived
from those sources.

## Current status

Many E-REDES Open Data datasets state a CC BY 4.0 license and require attribution to:

> E-REDES - Distribuicao de Eletricidade, "E-REDES Open Data Portal".

However, the metadata retrieved for several topology-critical inputs, including the
working datasets named `rede-at-teste`, `se-at_2025`, and `pc-at_2025`, did not expose an
explicit dataset-level license in the retrieved catalog response.

For that reason:

- raw E-REDES files are not distributed in this repository;
- processed topology and benchmark files are excluded from Git by default;
- public redistribution of the candidate dataset remains pending written license
  clarification for the topology-critical inputs;
- users must obtain source data from E-REDES and comply with the terms shown by the
  portal at download time.

## Intended public release

After license clarification, a versioned dataset release should include:

- source dataset identifiers, URLs, access dates, and licenses;
- attribution and indication of modifications required by CC BY 4.0;
- a diagnostic-only and non-operator-grade disclaimer;
- a separate DOI and data license declaration;
- no downloaded manufacturer catalogs or standards unless redistribution is permitted.

The generated labels are scenario-derived diagnostic targets. They are not measured
failures, observed congestion events, or verified operating records from E-REDES.
