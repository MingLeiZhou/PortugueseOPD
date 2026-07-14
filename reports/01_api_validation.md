# 01 API Validation

Generated: 2026-07-13T14:16:18+00:00

Both OpenDataSoft API v2.1 and v1 record endpoints were tested. Restricted RND page layers were retried with the public API key embedded in the RND page widgets.

| Dataset | Meta | Meta no key | V2 | V1 | Records | Exports | Geom | Date | Voltage | Substation | License | Redistribution |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 12-continuidade-de-servico-indicadores-gerais-de-continuidade-de-servico | 200 | 200 | 200 | 200 | 12232 | csv,json,geojson,parquet | False | True | False | False | EXPLICIT | SOURCE_TERMS_PRESENT_REVIEW_REQUIRED |
| apoios-baixa-tensao | 200 | 200 | 200 | 200 | 2994636 | csv,json,geojson,shp,parquet | True | False | False | False | EXPLICIT | SOURCE_TERMS_PRESENT_REVIEW_REQUIRED |
| auxiliar1 | 200 | 404 | 200 | 200 | 469 | csv,json,geojson,shp,parquet | True | True | True | True | MISSING | BLOCKED_MISSING_DATASET_LICENSE |
| auxiliar2 | 200 | 404 | 200 | 200 | 3824 | csv,json,geojson,shp,parquet | True | False | False | True | MISSING | BLOCKED_MISSING_DATASET_LICENSE |
| auxiliar3 | 200 | 404 | 200 | 200 | 5352 | csv,json,geojson,shp,parquet | True | True | True | True | MISSING | BLOCKED_MISSING_DATASET_LICENSE |
| capacidade-rececao-rnd | 200 | 200 | 200 | 200 | 469 | csv,json,geojson,parquet | False | True | True | True | EXPLICIT | SOURCE_TERMS_PRESENT_REVIEW_REQUIRED |
| caracteristicas-da-rede | 200 | 200 | 200 | 200 | 437 | csv,json,geojson,parquet | False | True | True | True | EXPLICIT | SOURCE_TERMS_PRESENT_REVIEW_REQUIRED |
| carga-na-subestacao | 200 | 200 | 200 | 200 | 794 | csv,json,geojson,parquet | False | True | True | True | EXPLICIT | SOURCE_TERMS_PRESENT_REVIEW_REQUIRED |
| civil-parishes-portugal | 200 | 200 | 200 | 200 | 3259 | csv,json,geojson,shp,parquet | True | True | False | True | EXPLICIT | SOURCE_TERMS_PRESENT_REVIEW_REQUIRED |
| diagrama-de-carga-de-subestacao | 200 | 200 | 200 | 200 | 5 | csv,json,geojson,parquet | False | True | False | True | EXPLICIT | SOURCE_TERMS_PRESENT_REVIEW_REQUIRED |
| diagrama_carga_subestacao_01_a_07 | 200 | 200 | 200 | 200 | 4085298 | csv,json,geojson,parquet | False | True | False | True | EXPLICIT | SOURCE_TERMS_PRESENT_REVIEW_REQUIRED |
| diagrama_carga_subestacao_08_a_10 | 200 | 200 | 200 | 200 | 2185886 | csv,json,geojson,parquet | False | True | False | True | EXPLICIT | SOURCE_TERMS_PRESENT_REVIEW_REQUIRED |
| diagrama_carga_subestacao_11_a_12 | 200 | 200 | 200 | 200 | 2829724 | csv,json,geojson,parquet | False | True | False | True | EXPLICIT | SOURCE_TERMS_PRESENT_REVIEW_REQUIRED |
| diagrama_carga_subestacao_13_a_15 | 200 | 200 | 200 | 200 | 3548885 | csv,json,geojson,parquet | False | True | False | True | EXPLICIT | SOURCE_TERMS_PRESENT_REVIEW_REQUIRED |
| diagrama_carga_subestacao_16_a_18 | 200 | 200 | 200 | 200 | 1259343 | csv,json,geojson,parquet | False | True | False | True | EXPLICIT | SOURCE_TERMS_PRESENT_REVIEW_REQUIRED |
| districts-portugal | 200 | 200 | 200 | 200 | 20 | csv,json,geojson,shp,parquet | True | True | False | True | EXPLICIT | SOURCE_TERMS_PRESENT_REVIEW_REQUIRED |
| municipalities-portugal | 200 | 200 | 200 | 200 | 308 | csv,json,geojson,shp,parquet | True | True | False | True | EXPLICIT | SOURCE_TERMS_PRESENT_REVIEW_REQUIRED |
| pc-at_2025 | 200 | 404 | 200 | 200 | 76 | csv,json,geojson,shp,parquet | True | False | False | True | MISSING | BLOCKED_MISSING_DATASET_LICENSE |
| pc-mt_2025 | 200 | 404 | 200 | 200 | 74 | csv,json,geojson,shp,parquet | True | False | False | True | MISSING | BLOCKED_MISSING_DATASET_LICENSE |
| postos-transformacao-distribuicao | 200 | 200 | 200 | 200 | 72434 | csv,json,geojson,shp,parquet | True | False | True | True | EXPLICIT | SOURCE_TERMS_PRESENT_REVIEW_REQUIRED |
| qualidade_energia_cavas-final | 200 | 200 | 200 | 200 | 2846 | csv,json,geojson,parquet | False | True | True | True | EXPLICIT | SOURCE_TERMS_PRESENT_REVIEW_REQUIRED |
| qualidade_energia_fenomenoscontinuos-final | 200 | 200 | 200 | 200 | 5860 | csv,json,geojson,parquet | False | True | True | True | EXPLICIT | SOURCE_TERMS_PRESENT_REVIEW_REQUIRED |
| qualidade_energia_sobretensoes-final | 200 | 200 | 200 | 200 | 2922 | csv,json,geojson,parquet | False | True | True | True | EXPLICIT | SOURCE_TERMS_PRESENT_REVIEW_REQUIRED |
| rede-at-teste | 200 | 404 | 200 | 200 | 5334 | csv,json,geojson,shp,parquet | True | False | True | False | MISSING | BLOCKED_MISSING_DATASET_LICENSE |
| rede-mt-teste | 200 | 404 | 200 | 200 | 337725 | csv,json,geojson,shp,parquet | True | False | True | False | MISSING | BLOCKED_MISSING_DATASET_LICENSE |
| se-at_2025 | 200 | 404 | 200 | 200 | 410 | csv,json,geojson,shp,parquet | True | False | False | True | MISSING | BLOCKED_MISSING_DATASET_LICENSE |
| se-mt_2025 | 200 | 404 | 200 | 200 | 28 | csv,json,geojson,shp,parquet | True | False | False | True | MISSING | BLOCKED_MISSING_DATASET_LICENSE |


Reproducibility note: API URLs, timestamps, HTTP statuses, export-format probes, and detected field classes are saved in `data/metadata/api_validation.json`.