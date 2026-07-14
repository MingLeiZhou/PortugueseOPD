# CRS, Geometry Encoding, Units and Missing-Value Semantics

Dataset version: PT60-Candidate v1.0.0

## Coordinate reference systems

- Release geometry fields are encoded as GeoJSON-style longitude/latitude coordinate arrays and should be treated as WGS 84 / EPSG:4326 decimal degrees.
- Axis order in CSV geometry cells is longitude, latitude.
- The reconstruction and validation pipeline also uses a local equirectangular metric workspace centred on Portugal (`lon0=-8.532604`, `lat0=39.567953`, metres). This local CRS is used for endpoint clustering, facility-distance checks, corridor-distance checks and length/coverage diagnostics.
- The exact upstream portal CRS wording should be re-checked immediately before DOI deposit. This is still tracked as a release-review item, not as a row-level validation failure.

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
