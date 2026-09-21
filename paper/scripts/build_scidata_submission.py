#!/usr/bin/env python3
"""Build the Scientific Data manuscript and supplementary PDF from Markdown.

The generated main TeX contains the numbered Nature-style bibliography directly,
so it has no runtime dependency on a separate BibTeX/Biber file.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path


PAPER = Path(__file__).resolve().parents[1]
MAIN_MD = PAPER / "SimPT60_submission_en.md"
SUPP_MD = PAPER / "SimPT60_supplement_en.md"
BIB = PAPER / "references_final.bib"
CSL = PAPER / "nature.csl"
MAIN_TEX = PAPER / "SimPT60_submission_en.tex"
MAIN_PDF = PAPER / "SimPT60_submission_en.pdf"
SUPP_TEX = PAPER / "SimPT60_supplement_en.tex"
SUPP_PDF = PAPER / "SimPT60_supplement_en.pdf"
PANDOC = shutil.which("pandoc") or "/opt/homebrew/bin/pandoc"
XELATEX = shutil.which("xelatex") or "/usr/local/texlive/2025basic/bin/universal-darwin/xelatex"


def run(args: list[str], *, cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True)


def vectorize_images(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        path = match.group(1)
        vector = Path(path).with_suffix(".pdf")
        if (PAPER / vector).exists():
            path = vector.as_posix()
        return f"![]({path})"

    return re.sub(r"!\[[^\]]*\]\(([^)]+\.png)\)", replace, text)


def prepare_main() -> str:
    text = MAIN_MD.read_text(encoding="utf-8")
    lines = text.splitlines()
    title = lines[0].removeprefix("# ").strip()
    match = re.search(r"^# Abstract\n+(.*?)(?=^# )", text, re.MULTILINE | re.DOTALL)
    if not match:
        raise RuntimeError("Abstract section not found")
    abstract_block = match.group(1).strip()
    keyword_match = re.search(r"^\*\*Keywords:\*\*\s*(.+)$", abstract_block, re.MULTILINE)
    keywords = keyword_match.group(1).strip() if keyword_match else ""
    abstract = re.sub(r"\n*\*\*Keywords:\*\*.*$", "", abstract_block, flags=re.MULTILINE).strip()
    body = text[match.end() :].lstrip()
    body = vectorize_images(body)
    yaml = (
        "---\n"
        f'title: "{title.replace(chr(34), chr(92) + chr(34))}"\n'
        "abstract: |\n"
        + "\n".join(f"  {line}" for line in abstract.splitlines())
        + "\n"
        f'keywords: "{keywords}"\n'
        "reference-section-title: References\n"
        "link-citations: false\n"
        "---\n\n"
    )
    return yaml + body


def prepare_supplement() -> str:
    text = SUPP_MD.read_text(encoding="utf-8")
    title = text.splitlines()[0].removeprefix("# ").strip()
    body = re.sub(r"^# .*\n+", "", text, count=1)
    body = vectorize_images(body)
    return f'---\ntitle: "{title}"\n---\n\n{body}'


def pandoc_tex(source: Path, target: Path, *, citations: bool) -> None:
    args = [
        PANDOC,
        str(source),
        "--from=markdown+tex_math_single_backslash+tex_math_dollars",
        "--to=latex",
        "--standalone",
        "--resource-path",
        str(PAPER),
        "--metadata=date:",
        "-V",
        "documentclass=article",
        "-V",
        "classoption=10pt",
        "-V",
        "geometry:margin=19mm",
        "-V",
        "mainfont=TeX Gyre Termes",
        "-V",
        "colorlinks=false",
        "--include-in-header",
        str(PAPER / "scidata_header.tex"),
        "-o",
        str(target),
    ]
    if citations:
        args[1:1] = ["--citeproc", "--bibliography", str(BIB), "--csl", str(CSL)]
    run(args, cwd=PAPER)


def compile_tex(tex: Path, pdf: Path, build_dir: Path) -> None:
    build_dir.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        run(
            [
                XELATEX,
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-output-directory",
                str(build_dir),
                tex.name,
            ],
            cwd=PAPER,
        )
    shutil.copy2(build_dir / f"{tex.stem}.pdf", pdf)


def main() -> None:
    header = PAPER / "scidata_header.tex"
    header.write_text(
        r"""
\usepackage{booktabs}
\usepackage{longtable}
\usepackage{array}
\usepackage{microtype}
\usepackage{setspace}
\setstretch{1.12}
\setlength{\emergencystretch}{3em}
\setlength{\parindent}{0pt}
\setlength{\parskip}{0.45em}
\usepackage{caption}
\captionsetup{font=small,labelfont=bf}
\usepackage{needspace}
\usepackage{etoolbox}
\AtBeginEnvironment{longtable}{\small}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    with tempfile.TemporaryDirectory(prefix="simpt60-scidata-") as tmp:
        tmpdir = Path(tmp)
        main_prepared = tmpdir / "main.md"
        supp_prepared = tmpdir / "supp.md"
        main_prepared.write_text(prepare_main(), encoding="utf-8")
        supp_prepared.write_text(prepare_supplement(), encoding="utf-8")
        pandoc_tex(main_prepared, MAIN_TEX, citations=True)
        pandoc_tex(supp_prepared, SUPP_TEX, citations=False)
        compile_tex(MAIN_TEX, MAIN_PDF, tmpdir / "main-build")
        compile_tex(SUPP_TEX, SUPP_PDF, tmpdir / "supp-build")
    print(MAIN_TEX)
    print(MAIN_PDF)
    print(SUPP_TEX)
    print(SUPP_PDF)


if __name__ == "__main__":
    main()
