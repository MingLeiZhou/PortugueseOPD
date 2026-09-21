# SimPT60-2026.09.21-r1

Immutable paper-associated release: Portuguese mainland 60–400 kV public-data reconstruction, 31,492 archived 15-minute AC cases, 2025-05-01 through 2026-03-24.

## Download and verify

Download `SimPT60-2026.09.21-r1-core.tar.zst`, all eleven monthly `.duckdb.zst` assets, `MONTHLY_MANIFEST.json`, `release.json`, and `SHA256SUMS` from the same release tag. Run `shasum -a 256 -c SHA256SUMS` after downloading all named assets. Do not substitute v2.1.0-rc2 (336 seasonal examples) or a mutable work database.

Extract the core with `tar --zstd -xf SimPT60-2026.09.21-r1-core.tar.zst` (on macOS without tar zstd support: `zstd -dc ...core.tar.zst | tar -xf -`). The extraction directory is `bundle/`.

From that directory:

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements-lock.txt
.venv/bin/python replay.py
```

The replay forbids network access after loading dependencies and validates four archived scalar fields at tolerance 1e-5 in their respective units. `--case-index 0` through `21` selects 22 fixed validation states. Full environment versions are in `environment.json`; the replay was executed on the recorded macOS ARM64/Python 3.13 environment. A different platform is not asserted bitwise identical; source data and stored arrays remain byte-verifiable.

Monthly assets are original compact DuckDB files compressed without modifying their contents. `MONTHLY_MANIFEST.json` records compressed/uncompressed hashes and counts. After `zstd -d file.duckdb.zst`, query `monthly_model.cases`, `state_arrays`, `audit_bundles`, `bus_order`, and `line_order`. To rerun archive audits, place each decompressed file at `monthly_models/YYYY-MM/pt60_models_YYYY-MM.compact.duckdb` under the core bundle and use the provided analysis scripts. All archived run manifests retain their original source hashes and historical absolute paths as evidence; those paths are not required for `replay.py`.

## Deliberately separate model variants

- **CORE-3783:** 3,783 buses, 4,943 line segments, 228 transformer rows. Exact original static model and generation table hashes match every monthly run manifest. Used for the annual time series, original sensitivity experiments and new normal-state operational ablations.
- **N1-3787:** six separately frozen corrected N−0 snapshots for the published N−1 panel. Four `PI_FEITOSA` junctions split the Lanheses–Feitosa circuit from its formerly shared geometric junctions. This variant also contains rating, length, parallel-equivalent and Estoi corrections. It is not substituted into historical monthly cases.
- **Effective tap-control ablation:** optional `enable_ratio_tap_model` makes the Ratio tap model explicit. Original 228 transformer rows have an empty tap type; retaining them is necessary for historical reproduction, but the paper no longer describes their old tap-position changes as effective physical voltage control.

The complete core-versus-working field diff, N−1 member lists and exclusions, source evidence and historical availability matrices, parameter-evidence downgrade overlay, held-out station predictions, operational ablations, proxy shares and hotspot frequencies are in `paper/revision_v3/`. `REVIEW_RESPONSE.md` maps all thirteen review requests to artifacts.

The source snapshot's commit appears in `release.json` and `CODE_VERSION.json`. This is the release/reproduction implementation commit, not an invented original compute commit. The full parent baseline and modification scope are also recorded. Original historical commits were not stored in monthly run manifests; reproducibility therefore rests on archived inputs/models/results and the independently checked replay.

## Scope and attribution

Code is MIT licensed. Data retain source-specific terms in `DATA_LICENSE.md` and `ATTRIBUTION.md`: E-REDES CC BY 4.0, OpenStreetMap ODbL, and the applicable terms for other providers. Public project documents support specified fields only. Parameter transfer, unknown commissioning dates, collector equivalents, Q limits, PV targets, compensation and control settings remain engineering assumptions where indicated.

This is a research reconstruction, not operator telemetry, an operator-validated topology, or a historical switching log. The ordinary line/transformer inventory is not fully date-gated. The new Minho–Galicia connection is off throughout the case window. Existing N−1 overloads, proxy-concentrated injections and failed/invalid configurations are retained rather than hidden.
