#!/usr/bin/env python3
"""Export the canonical Scientific Data LaTeX manuscript to editable DOCX."""

from __future__ import annotations

import argparse
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor, Twips


PAPER = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PAPER / "PT60-Candidate_Scientific_Data_latest.docx"
TITLE = "A provenance-tracked candidate dataset for Portuguese 60 kV topology reconstruction"
FONT = "Times New Roman"
CONTENT_WIDTH_DXA = 9360


def set_run_font(run, name: str, size: float, *, bold: bool | None = None) -> None:
    run.font.name = name
    run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.rFonts
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    for key in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rfonts.set(qn(key), name)


def configure_paragraph_style(
    document: Document,
    name: str,
    *,
    size: float,
    bold: bool = False,
    italic: bool = False,
    before: float = 0,
    after: float = 0,
    line_spacing: float = 1.0,
    alignment=None,
    keep_with_next: bool = False,
) -> None:
    style = next(style for style in document.styles if style.name == name)
    style.font.name = FONT
    style.font.size = Pt(size)
    style.font.bold = bold
    style.font.italic = italic
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.rFonts
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    for key in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rfonts.set(qn(key), FONT)
    style.font.color.rgb = RGBColor(0, 0, 0)
    fmt = style.paragraph_format
    fmt.space_before = Pt(before)
    fmt.space_after = Pt(after)
    fmt.line_spacing = line_spacing
    fmt.keep_with_next = keep_with_next
    fmt.widow_control = True
    if alignment is not None:
        fmt.alignment = alignment


def ensure_child(parent, tag: str):
    child = parent.find(qn(tag))
    if child is None:
        child = OxmlElement(tag)
        parent.append(child)
    return child


def set_width(parent, tag: str, width_dxa: int) -> None:
    width = ensure_child(parent, tag)
    width.set(qn("w:type"), "dxa")
    width.set(qn("w:w"), str(width_dxa))


def apply_table_geometry(table, widths_dxa: list[int]) -> None:
    """Synchronize Word table, grid, and cell widths for stable rendering."""

    if sum(widths_dxa) != CONTENT_WIDTH_DXA:
        raise ValueError(f"Table widths must sum to {CONTENT_WIDTH_DXA}: {widths_dxa}")
    table.autofit = False
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    tbl_pr = table._tbl.tblPr
    set_width(tbl_pr, "w:tblW", CONTENT_WIDTH_DXA)
    table_indent = ensure_child(tbl_pr, "w:tblInd")
    table_indent.set(qn("w:type"), "dxa")
    table_indent.set(qn("w:w"), "120")
    layout = ensure_child(tbl_pr, "w:tblLayout")
    layout.set(qn("w:type"), "fixed")

    grid = table._tbl.tblGrid
    for child in list(grid):
        grid.remove(child)
    for width in widths_dxa:
        grid_col = OxmlElement("w:gridCol")
        grid_col.set(qn("w:w"), str(width))
        grid.append(grid_col)

    for column, width in zip(table.columns, widths_dxa):
        column.width = Twips(width)
    for row in table.rows:
        row.height = None
        tr_pr = row._tr.get_or_add_trPr()
        if tr_pr.find(qn("w:cantSplit")) is None:
            tr_pr.append(OxmlElement("w:cantSplit"))
        for cell, width in zip(row.cells, widths_dxa):
            cell.width = Twips(width)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            tc_pr = cell._tc.get_or_add_tcPr()
            set_width(tc_pr, "w:tcW", width)
            tc_mar = ensure_child(tc_pr, "w:tcMar")
            for side, margin in (("top", 80), ("bottom", 80), ("start", 120), ("end", 120)):
                item = ensure_child(tc_mar, f"w:{side}")
                item.set(qn("w:w"), str(margin))
                item.set(qn("w:type"), "dxa")

    header_pr = table.rows[0]._tr.get_or_add_trPr()
    if header_pr.find(qn("w:tblHeader")) is None:
        header_pr.append(OxmlElement("w:tblHeader"))


def add_page_number(paragraph) -> None:
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run()
    set_run_font(run, FONT, 9)
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = " PAGE "
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    text = OxmlElement("w:t")
    text.text = "1"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for element in (begin, instr, separate, text, end):
        run._r.append(element)


def format_docx(path: Path) -> None:
    """Apply a restrained academic Word style and stable layout geometry."""

    document = Document(path)
    section = document.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = Inches(1.0)
    section.bottom_margin = Inches(1.0)
    section.left_margin = Inches(1.0)
    section.right_margin = Inches(1.0)
    section.header_distance = Inches(0.5)
    section.footer_distance = Inches(0.5)

    settings = document.settings.element
    view = ensure_child(settings, "w:view")
    view.set(qn("w:val"), "print")
    hidden_boundaries = settings.find(qn("w:doNotDisplayPageBoundaries"))
    if hidden_boundaries is not None:
        settings.remove(hidden_boundaries)

    # Narrative-proposal preset with named academic-manuscript overrides:
    # Times New Roman, black hierarchy, compact 1.12-line body, journal captions.
    for style_name in ("Normal", "Body Text", "First Paragraph", "Compact"):
        configure_paragraph_style(
            document,
            style_name,
            size=10.5,
            after=5,
            line_spacing=1.12,
            alignment=WD_ALIGN_PARAGRAPH.JUSTIFY,
        )
    configure_paragraph_style(
        document,
        "Title",
        size=18,
        bold=True,
        after=10,
        line_spacing=1.0,
        alignment=WD_ALIGN_PARAGRAPH.CENTER,
        keep_with_next=True,
    )
    configure_paragraph_style(
        document,
        "Author",
        size=11,
        after=3,
        alignment=WD_ALIGN_PARAGRAPH.CENTER,
        keep_with_next=True,
    )
    configure_paragraph_style(
        document,
        "Date",
        size=10,
        after=9,
        alignment=WD_ALIGN_PARAGRAPH.CENTER,
        keep_with_next=True,
    )
    configure_paragraph_style(
        document,
        "Abstract Title",
        size=11,
        bold=True,
        before=3,
        after=4,
        alignment=WD_ALIGN_PARAGRAPH.CENTER,
        keep_with_next=True,
    )
    configure_paragraph_style(
        document,
        "Abstract",
        size=10,
        after=7,
        line_spacing=1.08,
        alignment=WD_ALIGN_PARAGRAPH.JUSTIFY,
    )
    configure_paragraph_style(
        document,
        "Heading 1",
        size=14,
        bold=True,
        before=14,
        after=6,
        keep_with_next=True,
    )
    configure_paragraph_style(
        document,
        "Heading 2",
        size=12,
        bold=True,
        before=10,
        after=4,
        keep_with_next=True,
    )
    configure_paragraph_style(
        document,
        "Heading 3",
        size=11,
        bold=True,
        italic=True,
        before=8,
        after=3,
        keep_with_next=True,
    )
    for style_name in ("Caption", "Table Caption"):
        configure_paragraph_style(
            document,
            style_name,
            size=9,
            italic=True,
            before=6,
            after=4,
            line_spacing=1.0,
            alignment=WD_ALIGN_PARAGRAPH.LEFT,
            keep_with_next=True,
        )
    configure_paragraph_style(
        document,
        "Image Caption",
        size=9,
        italic=True,
        before=3,
        after=8,
        line_spacing=1.0,
        alignment=WD_ALIGN_PARAGRAPH.JUSTIFY,
    )
    configure_paragraph_style(
        document,
        "Captioned Figure",
        size=9,
        after=0,
        alignment=WD_ALIGN_PARAGRAPH.CENTER,
        keep_with_next=True,
    )
    configure_paragraph_style(
        document,
        "Bibliography",
        size=9,
        after=3,
        line_spacing=1.05,
        alignment=WD_ALIGN_PARAGRAPH.LEFT,
    )
    bibliography = document.styles["Bibliography"].paragraph_format
    bibliography.left_indent = Inches(0.25)
    bibliography.first_line_indent = Inches(-0.25)
    configure_paragraph_style(document, "Source Code", size=8.5, after=3, line_spacing=1.0)
    document.styles["Source Code"].font.name = "Courier New"

    title_style_sizes = {
        "Title": 18,
        "Author": 11,
        "Date": 10,
        "Abstract Title": 11,
        "Abstract": 10,
        "Heading 1": 14,
        "Heading 2": 12,
        "Heading 3": 11,
        "Caption": 9,
        "Table Caption": 9,
        "Image Caption": 9,
        "Bibliography": 9,
        "Source Code": 8.5,
    }
    for paragraph in document.paragraphs:
        style_name = paragraph.style.name
        if style_name == "Heading 4":
            # LaTeX \paragraph headings are semantic third-level headings in
            # this manuscript; avoid a Heading 2 -> Heading 4 accessibility jump.
            paragraph.style = document.styles["Heading 3"]
            style_name = "Heading 3"
        size = title_style_sizes.get(style_name, 10.5)
        font_name = "Courier New" if style_name == "Source Code" else FONT
        for run in paragraph.runs:
            set_run_font(run, font_name, size)
        paragraph.paragraph_format.widow_control = True
        if style_name.startswith("Heading"):
            paragraph.paragraph_format.keep_with_next = True
            paragraph.paragraph_format.keep_together = True
        elif style_name == "Captioned Figure":
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            paragraph.paragraph_format.keep_with_next = True
        elif style_name == "Image Caption":
            paragraph.paragraph_format.keep_together = True
        elif style_name == "Table Caption":
            paragraph.paragraph_format.keep_with_next = True
        if style_name in {"Normal", "Body Text", "First Paragraph", "Compact"} and "_" in paragraph.text:
            # Full justification produces extreme spacing around breakable file
            # paths in both Word and LibreOffice; path-heavy paragraphs remain
            # left aligned while ordinary manuscript prose stays justified.
            paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT

    table_widths = [
        [1940, 900, 1440, 3240, 1840],
        [1940, 7420],
        [2020, 820, 6520],
        [2380, 1160, 5820],
        [2920, 1100, 5340],
        [2380, 3310, 3670],
    ]
    for table, widths in zip(document.tables, table_widths):
        apply_table_geometry(table, widths)
        for row_index, row in enumerate(table.rows):
            for column_index, cell in enumerate(row.cells):
                for paragraph in cell.paragraphs:
                    paragraph.paragraph_format.space_before = Pt(0)
                    paragraph.paragraph_format.space_after = Pt(2)
                    paragraph.paragraph_format.line_spacing = 1.0
                    paragraph.paragraph_format.widow_control = True
                    if column_index == 1 and len(row.cells) >= 3:
                        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    else:
                        paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
                    for run in paragraph.runs:
                        set_run_font(run, FONT, 8.5, bold=(row_index == 0))

    image_captions = [
        paragraph.text
        for paragraph in document.paragraphs
        if paragraph.style.name == "Image Caption"
    ]
    for index, shape in enumerate(document.inline_shapes):
        aspect_ratio = shape.height / shape.width
        shape.width = Inches(6.15)
        shape.height = int(shape.width * aspect_ratio)
        doc_pr = shape._inline.docPr
        doc_pr.set("descr", image_captions[index] if index < len(image_captions) else f"Figure {index + 1}")
        doc_pr.set("title", f"Figure {index + 1}")

    footer = section.footer
    footer_paragraph = footer.paragraphs[0]
    footer_paragraph.clear()
    add_page_number(footer_paragraph)

    document.core_properties.title = TITLE
    document.core_properties.author = "PT60-Candidate authors"
    document.core_properties.last_modified_by = ""
    document.core_properties.comments = "Editable Scientific Data manuscript export"
    document.save(path)


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

    format_docx(output_path)

    with zipfile.ZipFile(output_path) as archive:
        media = [name for name in archive.namelist() if name.startswith("word/media/")]
        document_xml = archive.read("word/document.xml")
    table_count = document_xml.count(b"<w:tbl>")
    print(f"Wrote {output_path}")
    print(f"Embedded media: {len(media)} ({', '.join(Path(name).suffix for name in media)})")
    print(f"Word tables: {table_count}")


if __name__ == "__main__":
    main()
