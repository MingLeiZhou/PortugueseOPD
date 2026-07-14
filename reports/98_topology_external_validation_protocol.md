# 98 External Topology Validation Protocol

Generated: 2026-07-13T15:28:04+00:00

## Status

`SAMPLE_READY_REVIEW_NOT_PERFORMED`

The sample is not ground truth. It is a proportional stratified review set that preserves confidence, asset-type, and structural-role coverage. No precision or recall claim is allowed until external evidence is recorded and independently adjudicated.

## Sample

- population: 358 candidate branches
- sample: 100 branches
- required reviews: 200 rows (two reviewers per branch)
- deterministic seed: 20260713

## Review rule

A `CONFIRMED` label requires evidence for both endpoint facilities and a continuous electrical route between them. `REJECTED` requires evidence that at least one endpoint or the route is wrong. Use `UNCERTAIN` when evidence is incomplete and `ABSTAIN` when the reviewer cannot assess the record. Internal reconstruction scores are context only and must not be used as truth.

Acceptable evidence references include operator planning documents, another independently maintained grid layer, dated aerial/satellite inspection, or other public records that identify both facilities and their connection. Record the exact URL/document/page and access date.

## Outputs

- `pt_topology_validation_sample.csv`: sampled records and sampling weights
- `pt_topology_validation_sample.geojson`: geometry review layer; redistribution remains blocked
- `pt_topology_validation_reviews.csv`: persistent two-reviewer annotation file
- `pt_topology_validation_review_template.csv`: resettable blank template

Run `python src/summarize_topology_external_validation.py` after reviews are complete. The summarizer fails closed if fewer than 50 branches are adjudicated or evidence fields are missing.
