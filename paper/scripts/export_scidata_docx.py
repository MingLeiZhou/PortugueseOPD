#!/usr/bin/env python3
"""Export the canonical Scientific Data LaTeX manuscript to editable DOCX."""

from __future__ import annotations

import argparse
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path


PAPER = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PAPER / "PT60-Candidate_Scientific_Data_draft.docx"
TITLE = "A provenance-tracked candidate dataset for Portuguese 60 kV topology reconstruction"


def escape_texttt(value: str) -> str:
    return value.replace("_", r"\_").replace("#", r"\#")


def prepare_latex(source: str) -> str:
    """Replace PDF-only display helpers with constructs Pandoc preserves."""

    source = re.sub(r"\\begin\{thebibliography\}.*?\\end\{thebibliography\}", "", source, flags=re.DOTALL)
    for macro in ("path", "hashvalue"):
        pattern = re.compile(rf"\\{macro}\{{([^{{}}]+)\}}")
        source = pattern.sub(lambda match: rf"\texttt{{{escape_texttt(match.group(1))}}}", source)
    return source


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=PAPER / "main_scidata.tex")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    source_path = args.source.resolve()
    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    prepared = prepare_latex(source_path.read_text(encoding="utf-8"))
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".tex", prefix="pt60_docx_", dir=PAPER, encoding="utf-8", delete=False
    ) as handle:
        handle.write(prepared)
        temporary_source = Path(handle.name)

    command = [
        "pandoc",
        temporary_source.name,
        "--from=latex",
        "--to=docx",
        "--citeproc",
        "--bibliography=references.bib",
        "--csl=../nature.csl",
        "--resource-path=.:figures/generated:generated_tables",
        "--lua-filter=scripts/docx_image_filter.lua",
        f"--metadata=title:{TITLE}",
        "--metadata=author:Author details to be finalized before submission",
        "--metadata=date:July 2026",
        "--metadata=reference-section-title:References",
        f"--output={output_path}",
    ]
    try:
        subprocess.run(command, cwd=PAPER, check=True)
    finally:
        temporary_source.unlink(missing_ok=True)

    with zipfile.ZipFile(output_path) as archive:
        media = [name for name in archive.namelist() if name.startswith("word/media/")]
        document_xml = archive.read("word/document.xml")
    table_count = document_xml.count(b"<w:tbl>")
    print(f"Wrote {output_path}")
    print(f"Embedded media: {len(media)} ({', '.join(Path(name).suffix for name in media)})")
    print(f"Word tables: {table_count}")


if __name__ == "__main__":
    main()
