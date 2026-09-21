#!/usr/bin/env python3
"""Validate the synchronized final manuscript, references, links, and archived claims."""
from pathlib import Path
import hashlib
import json
import re
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
PAPER = ROOT / "paper"
source = (PAPER / "PT60_Sep16.MD").read_text()
edited = (PAPER / "paper_final_edited.md").read_text()
supp = (PAPER / "PT60_Sep16_SUPPLEMENTARY_TABLES.md").read_text()
body = edited.split("# References", 1)[0]
results = []


def check(name, ok, detail=None):
    results.append({"check": name, "passed": bool(ok), "detail": detail})


def prose_reference(text, label):
    pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(label)}(?![A-Za-z0-9])")
    for line in text.splitlines():
        stripped = line.strip()
        if not pattern.search(line):
            continue
        if stripped.startswith("![") or stripped.startswith(f"**{label}——"):
            continue
        return True
    return False


check("Canonical and mirror manuscripts are byte-identical", source == edited)
check("Exactly one References section", len(re.findall(r"^# References$", edited, re.M)) == 1)
check("No legacy display-math delimiters", "$$" not in body)
check("Keywords use an ASCII colon", "**Keywords:**" in body and "**Keywords：**" not in body)

for kind, count in [("Figure", 12), ("Table", 17)]:
    captions = list(map(int, re.findall(r"\*\*" + kind + r" (\d+)——", body)))
    check(kind + " captions sequential", captions == list(range(1, count + 1)), captions)
    refs = list(map(int, re.findall(rf"(?<![A-Za-z]){kind} (\d+)(?!\d)", body)))
    check(kind + " references resolve", set(refs) <= set(captions), sorted(set(refs)))
    missing = [n for n in range(1, count + 1) if not prose_reference(body, f"{kind} {n}")]
    check(kind + " captions have prose introductions", not missing, missing)

images = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", body)
check("Twelve main figure assets are linked", len(images) == 12, images)
expected_images = [
    f"figures_final/fig{i:02d}_{name}.png"
    for i, name in enumerate([
        "network_reconstruction", "geographic_network_state", "temporal_coverage",
        "network_parameter_evidence", "parameter_spatial_sensitivity",
        "hotspot_persistence", "temporal_validation", "spatial_validation",
        "generation_balance_scope", "proxy_provenance", "grid_stress",
        "annual_nminus1",
    ], start=1)
]
check("Main figure paths follow current figure numbering", images == expected_images, images)
for link in images:
    check("Image exists: " + link, (PAPER / link).is_file())

supp_tables = list(map(int, re.findall(r"\*\*Table S(\d+)——", supp)))
supp_figures = list(map(int, re.findall(r"\*\*Figure S(\d+)——", supp)))
check("Supplementary table captions S1–S11 are sequential", supp_tables == list(range(1, 12)), supp_tables)
check("Supplementary figure captions S1–S3 are sequential", supp_figures == list(range(1, 4)), supp_figures)
combined = body + "\n" + supp
missing_s_tables = [n for n in range(1, 12) if not prose_reference(combined, f"Table S{n}")]
missing_s_figures = [n for n in range(1, 4) if not prose_reference(combined, f"Figure S{n}")]
check("Supplementary tables have prose references", not missing_s_tables, missing_s_tables)
check("Supplementary figures have prose references", not missing_s_figures, missing_s_figures)

supp_anchors = set(re.findall(r'<a id="([^"]+)"></a>', supp))
linked_anchors = re.findall(r"PT60_Sep16_SUPPLEMENTARY_TABLES\.md#([^)]+)", body)
check("All linked supplementary anchors resolve", set(linked_anchors) <= supp_anchors, sorted(set(linked_anchors) - supp_anchors))
check("Supplementary Note anchors S1–S5 exist", all(f"supplementary-note-s{i}" in supp_anchors for i in range(1, 6)))
check("Supplementary table anchors S1–S11 exist", all(f"supplementary-table-s{i}" in supp_anchors for i in range(1, 12)))

sections = set(re.findall(r"^#+ (\d+(?:\.\d+)*)[. ]", body, re.M))
section_refs = re.findall(r"第 (\d+(?:\.\d+)*) 节", body)
check("Explicit single-section references resolve", set(section_refs) <= sections, sorted(set(section_refs) - sections))
check("Main section structure is 1–6", re.findall(r"^# (\d+)\.", body, re.M) == ["1", "2", "3", "4", "5", "6"])

refs = json.loads((PAPER / "references_final.json").read_text())
keys = [r["id"] for r in refs]
cited = set(re.findall(r"\]\(#ref-([^)]+)\)", body))
md_keys = re.findall(r'<a id="ref-([^"]+)">', edited)
bib_keys = re.findall(r"^@\w+\{([^,]+)", (PAPER / "references_final.bib").read_text(), re.M)
check("All citations and reference anchors agree", cited == set(keys) == set(md_keys), {"cited": len(cited), "json": len(keys), "markdown": len(md_keys)})
check("Reference order agrees across Markdown, JSON, and BibTeX", md_keys == keys == bib_keys, {"markdown": md_keys, "json": keys, "bib": bib_keys})
check("No duplicate final reference keys", len(keys) == len(set(keys)))
dois = [r["DOI"].lower() for r in refs if r.get("DOI")]
check("No duplicate final DOIs", len(dois) == len(set(dois)))
check("No unresolved citation markers", "[CITATION NEEDED]" not in body)

conclusion_tail = edited.split("# 6. Conclusions\n", 1)[1].split("# References", 1)[0]
check("Conclusion separator occurs only after conclusion prose", conclusion_tail.strip().endswith("------") and conclusion_tail.count("------") == 1)
conclusion = conclusion_tail.rsplit("------", 1)[0].strip()
check("Conclusions contain no orphan bullets", not re.search(r"^\s*(?:[-*]|\d+\.) ", conclusion, re.M))
check("Conclusions retain core dataset and validation values", all(v in conclusion for v in ["60–400 kV", "31,492", "3,783", "4,943", "228", "31,388", "0.997", "0.9998", "9,894", "9,888"]))

OUTPUTS = ROOT / "portuguese_hv_network/outputs"
v = OUTPUTS / "validation_experiments"
series = pd.read_csv(v / "scope_matched_validation_timeseries.csv")
load = series[["ren_consumption_mw", "external_load_mw"]].dropna()
wind = series[["ren_wind_mw", "external_wind_mw"]].dropna()
check("Archived AC cases: 31,492, all converged", len(series) == 31492 and series.converged.eq(True).all())
check("Archived time window", series.local_date.min() == "2025-05-01" and series.local_date.max() == "2026-03-24")
load_r = load.corr().iloc[0, 1]
wind_r = wind.corr().iloc[0, 1]
check("Load: 31,388 pairs and rounded r=0.997", len(load) == 31388 and round(load_r, 3) == .997, {"pairs": len(load), "r": load_r})
check("Wind: 31,484 pairs and rounded r=0.9998", len(wind) == 31484 and round(wind_r, 4) == .9998, {"pairs": len(wind), "r": wind_r})
closure = series.model_ac_balance_closure_mw.abs().mean()
check("AC closure MAE rounds to 1.77e-6 MW", f"{closure:.2e}" == "1.77e-06", closure)
topology = json.loads((v / "topology_summary.json").read_text())
coverage = pd.read_csv(v / "topology_coverage_by_voltage.csv")
check("Archived topology: 3,783 buses and 228 transformers", topology["buses"] == 3783 and topology["transformers"] == 228)
line_column = next(c for c in coverage if c in ("lines", "line_count", "model_lines"))
check("Archived voltage coverage: 4,943 lines", coverage[line_column].sum() == 4943)
panel = pd.read_csv(OUTPUTS / "annual_nminus1_panel_v2/annual_nminus1_panel.csv", low_memory=False)
metrics = {"cases": len(panel), "primary": int(panel.primary_converged.sum()), "absolute": int(panel.passes_screen.sum()), "incremental": int(panel.passes_incremental_contingency_screen.sum())}
check("Annual N−1: 9,894 / 9,888 / 6,048 / 9,064", metrics == {"cases": 9894, "primary": 9888, "absolute": 6048, "incremental": 9064}, metrics)
check("Annual N−1: six states × 1,649", len(panel.representative_role.unique()) == 6 and panel.groupby("representative_role").size().eq(1649).all())
net_export = panel.loc[panel.representative_role.eq("MAX_NET_EXPORT")]
net_export_other = net_export.loc[~net_export.passes_incremental_contingency_screen & ~net_export.material_islanding]
check(
    "Maximum-net-export residual eight cases are seven thermal and one voltage",
    len(net_export_other) == 8
    and net_export_other.outcome.value_counts().to_dict() == {"THERMAL": 7, "VOLTAGE": 1},
    net_export_other.outcome.value_counts().to_dict(),
)
for label, filename, left, right, expected_n, expected_r in [
    ("Municipality", "municipality_comparison.csv", "pt60_share_within_matched", "billed_share_within_matched", 2183, .881),
    ("Substation", "substation_comparison.csv", "pt60_observed_peak_mw", "reference_natural_load_mw", 792, .957),
]:
    frame = pd.read_csv(OUTPUTS / "external_evidence_validation" / filename)
    paired = frame.loc[frame.matched.eq(True), [left, right]].dropna()
    rho = paired.corr(method="spearman").iloc[0, 1]
    check(f"{label}: archived sample and Spearman", len(paired) == expected_n and round(rho, 3) == expected_r, {"pairs": len(paired), "rho": rho})
lock = json.loads((PAPER / "scripts/figures/source_lock.json").read_text())
for path, sha in lock.items():
    check("Archived figure input SHA-256: " + path, hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == sha)

report = {
    "passed": sum(r["passed"] for r in results),
    "total": len(results),
    "checks": results,
    "unresolved": [],
}
(PAPER / "final_edit_validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
summary = {"passed": report["passed"], "total": report["total"], "failures": [r for r in results if not r["passed"]]}
print(json.dumps(summary, ensure_ascii=False, indent=2))
assert all(r["passed"] for r in results), "Editorial checks failed; see final_edit_validation.json"
