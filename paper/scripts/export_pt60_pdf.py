#!/usr/bin/env python3
"""Export PT60_Sep16.MD to a publication-quality PDF using Pandoc and XeLaTeX.

Handles:
- Extracting title, abstract, and keywords into standard LaTeX front matter
- Removing internal outline/draft notes
- Fixing markdown heading indentation and newline separation
- Stripping image alt-texts to prevent duplicate captions and unwanted floats
- Typesetting with Songti SC, Menlo, booktabs, fancyhdr, and full math formatting
- Embedding vector figures, keeping captions with figures, and appending supplement tables
"""

import os
import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAPER_DIR = ROOT / "paper"
SOURCE_MD = PAPER_DIR / "PT60_Sep16.MD"
TARGET_PDF = PAPER_DIR / "PT60_Sep16.pdf"
TARGET_TEX = None
SUPPLEMENT_MD = PAPER_DIR / "PT60_Sep16_SUPPLEMENTARY_TABLES.md"
INCLUDE_SUPPLEMENT = True
PANDOC_BIN = "/opt/homebrew/bin/pandoc"
XELATEX_BIN = "/usr/local/texlive/2025basic/bin/universal-darwin/xelatex"


def preprocess_markdown(content: str) -> str:
    # 1. Strip internal drafting outline note at top if present
    content = re.sub(r"^\s*五段式主线：\s*\n\s*>.*?\n\s*", "", content, flags=re.DOTALL)

    # 2. Extract title
    title_match = re.search(r"^#\s+(SimPT60(?::|—).*)$", content, re.MULTILINE)
    title = title_match.group(1).replace("—", ": ", 1).strip() if title_match else "SimPT60"
    content = re.sub(r"^#\s+SimPT60(?::|—).*?\n+", "", content, flags=re.MULTILINE)

    # 3. Extract Abstract and Keywords
    abstract_pattern = r"^#\s+Abstract\s*\n+(.*?)(?=\n+------|\n+#\s+1\.|\n+1\.)"
    abstract_match = re.search(abstract_pattern, content, flags=re.DOTALL | re.MULTILINE)
    if abstract_match:
        abstract_text = abstract_match.group(1).strip()
        content = content[: abstract_match.start()] + content[abstract_match.end() :]
    else:
        abstract_text = ""

    # Clean up divider if present
    content = re.sub(r"^\s*------\s*\n*", "\n\n", content, flags=re.MULTILINE)

    # 4. Fix indented headings (e.g. '   # 1. Introduction')
    content = re.sub(r"^[ \t]+(#{1,6}\s+)", r"\1", content, flags=re.MULTILINE)

    # 5. Ensure headings always have an empty line before them
    content = re.sub(r"([^\n])\n(#{1,6}\s+)", r"\1\n\n\2", content)

    # 6. Strip image alt-text: ![Figure X...](path) -> ![](path)
    # This prevents Pandoc from generating a floating \begin{figure} with duplicate \caption{Figure X: Figure X...}
    # allowing the author's rich bold caption paragraph directly beneath the image to act as the primary caption.
    content = re.sub(r"!\[.*?\]\((.*?)\)", r"![](\1)", content)

    # 7. Build YAML header
    safe_title = title.replace('"', '\\"')
    yaml_abstract = "\n".join("  " + line for line in abstract_text.splitlines())

    yaml_header = f"""---
title: "{safe_title}"
date: "September 2026"
abstract: |
{yaml_abstract}
---

"""
    return yaml_header + content


def build_latex_header() -> str:
    return r"""
\usepackage{fancyhdr}
\pagestyle{fancy}
\fancyhf{}
\fancyhead[L]{\small\textcolor[gray]{0.4}{SimPT60: Time-Series Power-Flow Dataset}}
\fancyhead[R]{\small\textcolor[gray]{0.4}{September 2026}}
\fancyfoot[C]{\small\thepage}
\renewcommand{\headrulewidth}{0.4pt}
\renewcommand{\footrulewidth}{0pt}

\usepackage{setspace}
\setstretch{1.16}
\setlength{\emergencystretch}{3em}
\setlength{\parskip}{0.4em plus 0.1em minus 0.1em}
\setlength{\parindent}{0pt}
% Native XeTeX line breaking works without a separate xeCJK installation.
\XeTeXlinebreaklocale "zh"
\XeTeXlinebreakskip = 0pt plus 1pt
% Use the page-builder-aware package, including at a natural page break.
\usepackage{needspace}
\newcommand{\PTneedspace}[1]{\Needspace{#1}}

\usepackage{titlesec}
\titleformat{\section}{\Large\bfseries}{\thesection}{0.8em}{}
\titleformat{\subsection}{\large\bfseries}{\thesubsection}{0.8em}{}
\titleformat{\subsubsection}{\normalsize\bfseries}{\thesubsubsection}{0.8em}{}
\titlespacing*{\section}{0pt}{14pt plus 2pt minus 2pt}{6pt plus 1pt minus 1pt}
\titlespacing*{\subsection}{0pt}{10pt plus 2pt minus 2pt}{4pt plus 1pt minus 1pt}
\titlespacing*{\subsubsection}{0pt}{8pt plus 1pt minus 1pt}{3pt plus 1pt minus 1pt}

\usepackage{booktabs}
\usepackage{array}
\usepackage{colortbl}

% Ensure inline graphics are centered
\let\origpandocbounded\pandocbounded
\renewcommand*\pandocbounded[1]{%
  \begin{center}%
  \origpandocbounded{#1}%
  \end{center}%
}
"""


def export_pdf():
    print(f"Reading source manuscript from {SOURCE_MD}...")
    source_content = SOURCE_MD.read_text(encoding="utf-8")

    clean_content = preprocess_markdown(source_content)
    supplement = SUPPLEMENT_MD
    if INCLUDE_SUPPLEMENT and supplement.exists():
        clean_content += '\n\n\\clearpage\n\n' + supplement.read_text(encoding='utf-8')
    # Explicit Markdown anchors must survive the LaTeX writer, including
    # author-year bibliography links and the appended supplementary tables.
    clean_content = re.sub(r'<a id="([^"]+)"></a>',
        lambda m: '\\phantomsection\\label{' + m.group(1) + '}', clean_content)
    if INCLUDE_SUPPLEMENT:
        clean_content = clean_content.replace(supplement.name + '#', '#')
    else:
        clean_content = re.sub(r'\[([^\]]+)\]\([^)]*\.md#[^)]+\)', r'\1', clean_content)
    clean_content = re.sub(r'\[([^\]]+)\]\([^)]*\.md\)', r'\1', clean_content)
    clean_content = clean_content.replace('1.77 × 10⁻⁶ MW', '$1.77\\times10^{-6}$ MW')
    clean_content = clean_content.replace('10⁻⁶', '$10^{-6}$')
    clean_content = re.sub(r'match_distance_m；\s*capacity_calibration_factor',
        lambda _: r'match_distance_m；\newline{}capacity_calibration_factor', clean_content)
    def reserve_table(match):
        rows = sum(line.startswith('|') for line in match.group(0).splitlines())
        needed = min(620, 45 + rows * 27)
        return f'\\PTneedspace{{{needed}pt}}\n\n' + match.group(0)
    clean_content = re.sub(r'^\*\*Table [^\n]*\n\n(?:\|[^\n]*\n)+', reserve_table,
                           clean_content, flags=re.MULTILINE)
    clean_content = clean_content.replace('| 变压器电压组合（kV） |',
        '\\PTneedspace{300pt}\n\n**Table S2（续）——变压器默认参数。**\n\n| 变压器电压组合（kV） |')
    # Long database identifiers must wrap at their own punctuation.
    def breakable_code(match):
        value = match.group(1)
        if re.fullmatch(r'[0-9a-f]{40,64}', value):
            return r'\texttt{\small ' + r'\allowbreak{}'.join(
                value[i:i + 8] for i in range(0, len(value), 8)) + '}'
        escaped = ''.join({'_': r'\_\allowbreak{}', '.': r'.\allowbreak{}',
                          '%': r'\%', '#': r'\#', '&': r'\&',
                          '{': r'\{', '}': r'\}'}.get(c, c) for c in value)
        return r'\texttt{\small ' + escaped + '}'
    # Some schema tables use bare identifiers rather than Markdown code.
    # Give long identifiers the same punctuation breakpoints, avoiding
    # overflow into the neighbouring column.
    def mark_long_identifiers(line):
        if not line.startswith('|'):
            return line
        pieces = line.split('`')
        for index in range(0, len(pieces), 2):
            pieces[index] = re.sub(r'\b[A-Za-z][A-Za-z0-9_]*(?:[._][A-Za-z0-9_*]+)+',
                lambda m: '`' + m.group(0) + '`' if len(m.group(0)) >= 18 else m.group(0),
                pieces[index])
        return '`'.join(pieces)
    clean_content = '\n'.join(mark_long_identifiers(line) for line in clean_content.split('\n'))
    clean_content = re.sub(r'`([^`\n]+)`', breakable_code, clean_content)
    # Use vector counterparts when available; keep the Markdown PNG previews.
    def vector_image(match):
        path = match.group(1)
        vector = Path(path).with_suffix('.pdf')
        return f'![]({vector})' if (PAPER_DIR / vector).exists() else match.group(0)
    clean_content = re.sub(r'!\[\]\(([^)]+\.png)\)', vector_image, clean_content)
    # Reserve the rendered figure plus its caption so neither is orphaned.
    from pypdf import PdfReader
    def reserve_figure(match):
        path = PAPER_DIR / match.group(2)
        if path.suffix != '.pdf':
            return match.group(0)
        figure_pdf = PdfReader(path)
        box = figure_pdf.pages[0].mediabox
        height = 482 * float(box.height) / float(box.width)
        caption_lines = max(2, (len(match.group(3)) + 74) // 75)
        needed = min(680, height + caption_lines * 16 + 62)
        return f'\\PTneedspace{{{needed:.1f}pt}}\n\n' + match.group(0)
    clean_content = re.sub(r'(?m)^([^\n]*Figure \d+[^\n]*\n\n)?!\[\]\(([^)]+)\)\n\n(\*\*Figure [^\n]+)',
                           reserve_figure, clean_content)

    scratch = ROOT / 'tmp' / 'pdfs' / TARGET_PDF.stem
    scratch.mkdir(parents=True, exist_ok=True)
    TARGET_PDF.parent.mkdir(parents=True, exist_ok=True)
    tmp_md = scratch / "prepared.md"
    tmp_tex_header = scratch / "custom_header.tex"

    tmp_md.write_text(clean_content, encoding="utf-8")
    tmp_tex_header.write_text(build_latex_header(), encoding="utf-8")

    main_font = "Songti SC" if re.search(r'[\u3400-\u9fff]', source_content) else "Times New Roman"
    common_args = [
        PANDOC_BIN,
        str(tmp_md),
        "-f",
        "markdown+tex_math_single_backslash+tex_math_dollars",
        "-V",
        "geometry:margin=20mm",
        "-V",
        f"mainfont={main_font}",
        "-V",
        "fontsize=10pt",
        "-V",
        "monofont=Menlo",
        "-V",
        "colorlinks=true",
        "-V",
        "linkcolor=blue",
        "-V",
        "urlcolor=blue",
        "--include-in-header",
        str(tmp_tex_header),
        "--resource-path",
        f"{PAPER_DIR}:{ROOT}",
    ]

    if TARGET_TEX is not None:
        TARGET_TEX.parent.mkdir(parents=True, exist_ok=True)
        tex_cmd = common_args + ["--standalone", "-t", "latex", "-o", str(TARGET_TEX)]
        tex_result = subprocess.run(tex_cmd, capture_output=True, text=True)
        (scratch / 'tex_export.log').write_text(tex_result.stdout + '\n' + tex_result.stderr)
        if tex_result.returncode != 0:
            print("Standalone TeX export failed!")
            print("STDOUT:", tex_result.stdout)
            print("STDERR:", tex_result.stderr)
            sys.exit(1)
        print(f"Standalone TeX generated at {TARGET_TEX}!")

    print("Compiling PDF with Pandoc + XeLaTeX...")
    cmd = common_args + [
        f"--pdf-engine={XELATEX_BIN}",
        "-o",
        str(TARGET_PDF),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    (scratch / 'export.log').write_text(result.stdout + '\n' + result.stderr)
    if result.returncode != 0:
        print("Pandoc failed!")
        print("STDOUT:", result.stdout)
        print("STDERR:", result.stderr)
        sys.exit(1)

    print(f"PDF generated successfully at {TARGET_PDF}!")

    from pypdf import PdfReader

    doc = PdfReader(str(TARGET_PDF))
    print(f"Total pages: {len(doc.pages)}")
    print(f"File size: {TARGET_PDF.stat().st_size / (1024 * 1024):.2f} MB")

    caption_count = sum(
        len(re.findall(r'Figure \d+(?:——|—)', page.extract_text() or ''))
        for page in doc.pages
    )
    print(f"Figure captions verified in PDF: {caption_count} (vector figures may contain raster sublayers)")

    if tmp_md.exists():
        tmp_md.unlink()
    if tmp_tex_header.exists():
        tmp_tex_header.unlink()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE_MD)
    parser.add_argument('--output', type=Path, default=TARGET_PDF)
    parser.add_argument('--supplement', type=Path, default=SUPPLEMENT_MD)
    parser.add_argument('--tex-output', type=Path,
                        help='Also write a standalone LaTeX manuscript generated from the prepared Markdown')
    parser.add_argument('--no-supplement', action='store_true',
                        help='Export the main manuscript and references without appended supplementary material')
    args = parser.parse_args()
    SOURCE_MD = args.source.resolve()
    TARGET_PDF = args.output.resolve()
    TARGET_TEX = args.tex_output.resolve() if args.tex_output else None
    SUPPLEMENT_MD = args.supplement.resolve()
    INCLUDE_SUPPLEMENT = not args.no_supplement
    export_pdf()
