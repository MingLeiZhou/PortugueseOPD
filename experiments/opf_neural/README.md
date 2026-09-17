# Archived exploratory AC-OPF and neural work

This experiment is outside PT60's four core deliverables and is paused.
It is not installed by `pip install pt60-tools` and is not required to build the website.

Retained evidence: `output/opf/`, `docs/PT60_ACOPF_NEURAL_VALIDATION.json`,
`docs/PT60_ACOPF_NEURAL_GUIDE_CN.md`. Seven OPF pilot cases passed the independent
physics audit. The neural pilot remains infeasible and has no demonstrated
end-to-end speedup. These results are not main-paper contributions.

Run from the repository root after installing core solver dependencies:

```bash
python -m experiments.opf_neural.cli --help
python -m pytest tests/test_pt60_opf.py
```

Historical guide commands `pt60 opf`, `predict`, `finetune`, `graph-export`,
`graph-check` and `opf-policy` now use the prefix
`python -m experiments.opf_neural.cli`. Neural experiments additionally require
the pinned GridSFM source at commit
`1ca775fd436d7ce013a1c0ab946e61ac7ef59ad6` (`model/` subdirectory).
The benchmark scripts import this experimental namespace explicitly.
Existing `/optimization` links remain an archived view, without primary navigation.
