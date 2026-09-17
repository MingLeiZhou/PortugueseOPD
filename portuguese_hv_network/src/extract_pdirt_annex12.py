#!/usr/bin/env python3
"""Extract the 2025 PDIRT Annex 12 simultaneous-load reference table.

The source PDF has a two-column visual table.  Text extraction preserves each
visual row but interleaves the left and right entries.  Each entry contains six
decimal MW/Mvar values, which provides a deterministic split without relying
on coordinates or OCR.  Published totals are used as hard validation checks.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import requests
from pypdf import PdfReader

from common import PROJECT, RAW, TABLES, sha256, utc_now, write_json


SOURCE_URL = "https://www.erse.pt/media/lx5n5kao/pdirt-2025-2034-proposta-inicial-vol-i-anexos-1-a-16.pdf"
SOURCE_PAGE_NUMBERS = {"WINTER": 323, "SUMMER": 324}
EXPECTED_TOTALS = {"WINTER": (10541.0, 1778.0), "SUMMER": (8350.0, 1930.0)}
PDF_PATH = RAW / "erse" / "pdirt-2025-2034-annexes-1-16.pdf"
OUTPUT_PATH = TABLES / "pdirt_annex12_2025_pde_loads.csv"
NUMBER = re.compile(r"(?<![\w.])-?\d+\.\d+(?![\w.])")


def ensure_pdf() -> None:
    if PDF_PATH.exists():
        return
    response = requests.get(SOURCE_URL, headers={"User-Agent": "PT60 source extraction/0.1"}, timeout=180)
    response.raise_for_status()
    PDF_PATH.parent.mkdir(parents=True, exist_ok=True)
    PDF_PATH.write_bytes(response.content)


def parse_entry(name: str, values: list[float], season: str) -> dict[str, object]:
    return {
        "pde_name": name.strip(),
        "season": season,
        "peak_p_mw": values[0],
        "peak_q_mvar": values[1],
        "intermediate_p_mw": values[2],
        "intermediate_q_mvar": values[3],
        "valley_p_mw": values[4],
        "valley_q_mvar": values[5],
        "horizon_year": 2025,
        "source_document": "PDIRT 2025-2034 initial proposal, Volume I, Annex 12",
        "source_pdf_page": SOURCE_PAGE_NUMBERS[season],
        "source_url": SOURCE_URL,
        "extraction_status": "PDF_TEXT_PARSED_AND_TOTAL_CHECKED",
    }


def parse_page(text: str, season: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in text.splitlines():
        matches = list(NUMBER.finditer(line))
        if len(matches) not in {6, 12}:
            continue
        rows.append(parse_entry(line[: matches[0].start()], [float(item.group()) for item in matches[:6]], season))
        if len(matches) == 12:
            second_name = line[matches[5].end() : matches[6].start()]
            rows.append(parse_entry(second_name, [float(item.group()) for item in matches[6:]], season))
    return rows


def main() -> None:
    ensure_pdf()
    reader = PdfReader(PDF_PATH)
    if reader.is_encrypted and reader.decrypt("") == 0:
        raise RuntimeError("PDIRT PDF could not be opened with its public empty password")
    rows: list[dict[str, object]] = []
    audits: list[dict[str, object]] = []
    for season, page_number in SOURCE_PAGE_NUMBERS.items():
        season_rows = parse_page(reader.pages[page_number - 1].extract_text(), season)
        peak_p = sum(float(row["peak_p_mw"]) for row in season_rows)
        peak_q = sum(float(row["peak_q_mvar"]) for row in season_rows)
        expected_p, expected_q = EXPECTED_TOTALS[season]
        # Published entries are rounded to 0.1, while totals are whole units.
        if abs(peak_p - expected_p) > 1.0 or abs(peak_q - expected_q) > 1.0:
            raise AssertionError(f"{season} Annex 12 totals failed: {peak_p=}, {peak_q=}")
        rows.extend(season_rows)
        audits.append({
            "season": season,
            "entry_count": len(season_rows),
            "extracted_peak_p_mw": peak_p,
            "published_peak_p_mw": expected_p,
            "extracted_peak_q_mvar": peak_q,
            "published_peak_q_mvar": expected_q,
        })
    TABLES.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).sort_values(["season", "pde_name"]).to_csv(OUTPUT_PATH, index=False)
    write_json(TABLES / "pdirt_annex12_2025_extraction_audit.json", {
        "generated_at": utc_now(),
        "source_url": SOURCE_URL,
        "source_local_path": str(PDF_PATH.relative_to(PROJECT)),
        "source_sha256": sha256(PDF_PATH),
        "source_pages": SOURCE_PAGE_NUMBERS,
        "audits": audits,
        "output": str(OUTPUT_PATH.relative_to(PROJECT)),
    })
    print(pd.DataFrame(audits).to_string(index=False))


if __name__ == "__main__":
    main()
