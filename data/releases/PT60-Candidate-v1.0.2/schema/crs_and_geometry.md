# CRS, Geometry Encoding, Units and Missing-Value Semantics

Dataset version: PT60-Candidate v1.0.2

## Coordinate reference systems

- Release geometry fields are encoded as GeoJSON-style longitude/latitude coordinate arrays in WGS 84 / EPSG:4326 decimal degrees. The frozen Opendatasoft v2.1 export URLs contain no `epsg` override, and the documented default for geometry-capable exports is EPSG:4326.
- Axis order in CSV geometry cells is longitude, latitude.
- The v1.0.2 reconstruction and validation pipeline uses ETRS89 / Portugal TM06 (EPSG:3763; Transverse Mercator, metres) for endpoint clustering, facility buffering, facility-distance checks, corridor-distance checks and length/coverage diagnostics.
- The native CRS before Opendatasoft portal ingestion, if different, is not recorded. It was not used by the reconstruction pipeline; the relevant input CRS is the EPSG:4326 portal export.

## Units

- Fields ending in `_m` are metres.
- Fields ending in `_km` are kilometres.
- Coverage, score, confidence, rate and percentage fields are unitless.
- Voltage fields preserve the file-specific source encoding. Core topology uses strings such as `60kv`; OSM-derived fields may use numeric strings such as `60000`.
- Electrical parameter fields (`r`, `x`, `b`, `thermal_limit`, `transformer_impedance`, `tap_settings`) are not estimated in the core candidate-topology release when encoded as `MISSING_NOT_ESTIMATED`.

## Missing values

The data dictionary records observed missing tokens per field. Common missing encodings are empty string, JSON null, `NaN`, `MISSING_NOT_ESTIMATED`, `pending` and absent optional JSON keys.

## Claim boundary

These CRS and unit statements support reproducible candidate-topology reconstruction, provenance tracking and public-source validation. They do not convert PT60-Candidate into an operator-validated or AC-power-flow-ready grid model.
