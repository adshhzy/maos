from __future__ import annotations

import html
from pathlib import Path

from docx import Document as DocxDocument
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph as DocxParagraph
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


DESKTOP = Path.home() / "Desktop"
DOCX_PATH = DESKTOP / "持久化多Agent编排内核_API总结.docx"
PDF_PATH = DESKTOP / "持久化多Agent编排内核_API总结.pdf"


BLUE = colors.HexColor("#2E74B5")
DARK_BLUE = colors.HexColor("#1F4D78")
INK = colors.HexColor("#1F2937")
MUTED = colors.HexColor("#5F6B7A")
HEADER_FILL = colors.HexColor("#E8EEF5")
CALLOUT_FILL = colors.HexColor("#F4F6F9")
CODE_FILL = colors.HexColor("#F8FAFC")
BORDER = colors.HexColor("#B8C2CF")


def main() -> None:
    register_fonts()
    docx = DocxDocument(str(DOCX_PATH))
    styles = build_styles()
    flowables = docx_to_flowables(docx, styles)

    pdf = SimpleDocTemplate(
        str(PDF_PATH),
        pagesize=letter,
        leftMargin=inch,
        rightMargin=inch,
        topMargin=0.85 * inch,
        bottomMargin=0.8 * inch,
        title="持久化多 Agent 编排内核微服务 API 总结",
        author="MAOS Sandbox Runtime",
    )
    pdf.build(flowables, onFirstPage=footer, onLaterPages=footer)
    print(PDF_PATH)


def register_fonts() -> None:
    pdfmetrics.registerFont(TTFont("NotoSC", r"C:\Windows\Fonts\NotoSansSC-VF.ttf"))
    pdfmetrics.registerFont(TTFont("SimHei", r"C:\Windows\Fonts\simhei.ttf"))
    pdfmetrics.registerFont(TTFont("Arial", r"C:\Windows\Fonts\arial.ttf"))


def build_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "body": ParagraphStyle(
            "body",
            parent=base["BodyText"],
            fontName="NotoSC",
            fontSize=9.6,
            leading=13.2,
            textColor=INK,
            spaceAfter=5,
            wordWrap="CJK",
            alignment=TA_LEFT,
        ),
        "title": ParagraphStyle(
            "title",
            parent=base["Title"],
            fontName="SimHei",
            fontSize=19,
            leading=24,
            textColor=DARK_BLUE,
            spaceAfter=7,
            wordWrap="CJK",
            alignment=TA_LEFT,
        ),
        "subtitle": ParagraphStyle(
            "subtitle",
            parent=base["BodyText"],
            fontName="NotoSC",
            fontSize=10,
            leading=14,
            textColor=MUTED,
            spaceAfter=12,
            wordWrap="CJK",
        ),
        "h1": ParagraphStyle(
            "h1",
            parent=base["Heading1"],
            fontName="SimHei",
            fontSize=14.5,
            leading=18,
            textColor=BLUE,
            spaceBefore=13,
            spaceAfter=7,
            keepWithNext=True,
            wordWrap="CJK",
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=base["Heading2"],
            fontName="SimHei",
            fontSize=11.5,
            leading=15,
            textColor=BLUE,
            spaceBefore=9,
            spaceAfter=5,
            keepWithNext=True,
            wordWrap="CJK",
        ),
        "h3": ParagraphStyle(
            "h3",
            parent=base["Heading3"],
            fontName="SimHei",
            fontSize=10.5,
            leading=14,
            textColor=DARK_BLUE,
            spaceBefore=7,
            spaceAfter=4,
            keepWithNext=True,
            wordWrap="CJK",
        ),
        "cell": ParagraphStyle(
            "cell",
            parent=base["BodyText"],
            fontName="NotoSC",
            fontSize=8,
            leading=10.5,
            textColor=INK,
            wordWrap="CJK",
        ),
        "cell_header": ParagraphStyle(
            "cell_header",
            parent=base["BodyText"],
            fontName="SimHei",
            fontSize=8.2,
            leading=10.5,
            textColor=DARK_BLUE,
            wordWrap="CJK",
        ),
        "code": ParagraphStyle(
            "code",
            parent=base["Code"],
            fontName="NotoSC",
            fontSize=7.4,
            leading=9.6,
            textColor=colors.HexColor("#1E293B"),
            wordWrap="CJK",
        ),
        "footer": ParagraphStyle(
            "footer",
            parent=base["BodyText"],
            fontName="NotoSC",
            fontSize=8,
            textColor=MUTED,
            alignment=TA_RIGHT,
        ),
    }


def docx_to_flowables(docx: DocxDocument, styles: dict[str, ParagraphStyle]) -> list:
    flowables: list = []
    for block in iter_blocks(docx):
        if isinstance(block, DocxParagraph):
            text = block.text.strip()
            if not text:
                continue
            style_name = block.style.name if block.style is not None else ""
            if not flowables:
                flowables.append(Paragraph(escape(text), styles["title"]))
                continue
            if "Heading 1" in style_name:
                flowables.append(Paragraph(escape(text), styles["h1"]))
            elif "Heading 2" in style_name:
                flowables.append(Paragraph(escape(text), styles["h2"]))
            elif "Heading 3" in style_name:
                flowables.append(Paragraph(escape(text), styles["h3"]))
            else:
                flowables.append(Paragraph(escape(text), styles["body"]))
        elif isinstance(block, DocxTable):
            table = convert_table(block, styles)
            if table is not None:
                flowables.append(table)
                flowables.append(Spacer(1, 6))
    return flowables


def iter_blocks(docx: DocxDocument):
    body = docx.element.body
    for child in body.iterchildren():
        if child.tag.endswith("}p"):
            yield DocxParagraph(child, docx)
        elif child.tag.endswith("}tbl"):
            yield DocxTable(child, docx)


def convert_table(block: DocxTable, styles: dict[str, ParagraphStyle]) -> Table | None:
    rows = []
    for row in block.rows:
        values = []
        for cell in row.cells:
            values.append(cell.text.strip())
        if any(values):
            rows.append(values)
    if not rows:
        return None

    col_count = max(len(row) for row in rows)
    for row in rows:
        while len(row) < col_count:
            row.append("")

    one_cell = col_count == 1
    code_like = one_cell and looks_like_code(rows[0][0])
    data = []
    for row_index, row in enumerate(rows):
        rendered_row = []
        for value in row:
            style = styles["code"] if code_like else (
                styles["cell_header"] if row_index == 0 and not one_cell else styles["cell"]
            )
            rendered_row.append(Paragraph(escape(value).replace("\n", "<br/>"), style))
        data.append(rendered_row)

    widths = table_widths(col_count)
    table = Table(data, colWidths=[w * inch for w in widths], repeatRows=1 if not one_cell else 0)

    base_style = [
        ("BOX", (0, 0), (-1, -1), 0.6, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if one_cell:
        base_style.append(("BACKGROUND", (0, 0), (-1, -1), CODE_FILL if code_like else CALLOUT_FILL))
    else:
        base_style.append(("BACKGROUND", (0, 0), (-1, 0), HEADER_FILL))
    table.setStyle(TableStyle(base_style))
    return table


def table_widths(col_count: int) -> list[float]:
    if col_count == 1:
        return [6.5]
    if col_count == 2:
        return [1.45, 5.05]
    if col_count == 3:
        return [1.55, 1.95, 3.0]
    if col_count == 4:
        return [0.55, 1.75, 1.35, 2.85]
    return [6.5 / col_count] * col_count


def looks_like_code(text: str) -> bool:
    stripped = text.strip()
    return (
        stripped.startswith("{")
        or stripped.startswith("[")
        or stripped.startswith("http")
        or stripped.startswith("POST ")
        or stripped.startswith("GET ")
        or "127.0.0.1" in stripped
        or "D:\\\\" in stripped
    )


def escape(text: str) -> str:
    return html.escape(text).replace("  ", "&nbsp; ")


def footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFont("NotoSC", 8)
    canvas.setFillColor(MUTED)
    canvas.drawRightString(
        doc.pagesize[0] - doc.rightMargin,
        0.45 * inch,
        f"持久化多 Agent 编排内核 API 总结 | {doc.page}",
    )
    canvas.restoreState()


if __name__ == "__main__":
    main()
