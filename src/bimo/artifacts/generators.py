"""Generators for Bimo artifacts (Text, Markdown, JSON, CSV, DOCX, PDF, XLSX, Images).

Executes deterministic, clean file serialization without invoking arbitrary subprocesses or GUI editors.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def generate_text(content: str) -> bytes:
    """Serialize plain text content as UTF-8 bytes."""
    if not isinstance(content, str):
        content = str(content)
    return content.encode("utf-8")


def generate_markdown(content: str) -> bytes:
    """Serialize markdown content as UTF-8 bytes."""
    if not isinstance(content, str):
        content = str(content)
    return content.encode("utf-8")


def generate_json(data: Any, indent: int = 2) -> bytes:
    """Serialize structured data into formatted, validated JSON bytes."""
    try:
        serialized = json.dumps(data, indent=indent, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid JSON data structure: {exc}") from exc
    return serialized.encode("utf-8")


def generate_csv(columns: list[str], rows: list[list[Any]]) -> bytes:
    """Serialize structured columns and rows into RFC 4180 CSV bytes.

    Validates:
    - columns must be a non-empty list of string names.
    - each row must match the length of columns.
    """
    if not isinstance(columns, (list, tuple)) or not columns:
        raise ValueError("CSV 'columns' must be a non-empty list of column headers.")

    if not isinstance(rows, (list, tuple)):
        raise ValueError("CSV 'rows' must be a list of row lists.")

    col_count = len(columns)
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow([str(c) for c in columns])

    for i, row in enumerate(rows):
        if not isinstance(row, (list, tuple)):
            raise ValueError(f"CSV row {i} is not a valid list: {row}")
        if len(row) != col_count:
            raise ValueError(
                f"CSV row {i} has {len(row)} elements, expected {col_count} columns."
            )
        writer.writerow(row)

    return output.getvalue().encode("utf-8")


def generate_docx(
    title: str,
    sections: list[dict[str, Any]] | None = None,
    author: str = "Bimo",
) -> bytes:
    """Generate a valid, styled Microsoft Word (.docx) document.

    Sections can contain:
    - 'heading': str (optional 'level': 1, 2, or 3)
    - 'paragraph': str
    - 'bullets': list[str]
    - 'numbered': list[str]
    - 'table': {'headers': list[str], 'rows': list[list[Any]]}
    """
    try:
        import docx
        from docx.shared import Inches, Pt, RGBColor
    except ImportError as exc:
        raise RuntimeError(
            "python-docx is required for DOCX generation. Install via 'pip install python-docx'."
        ) from exc

    doc = docx.Document()

    # Document properties
    core_props = doc.core_properties
    core_props.title = title
    core_props.author = author

    # Title
    if title:
        title_para = doc.add_paragraph()
        title_run = title_para.add_run(title)
        title_run.font.size = Pt(24)
        title_run.font.bold = True
        title_run.font.color.rgb = RGBColor(24, 75, 120)
        title_para.paragraph_format.space_after = Pt(14)

    if sections:
        for sec in sections:
            if not isinstance(sec, dict):
                continue

            # Headings
            if "heading" in sec:
                level = int(sec.get("level", 1))
                level = max(1, min(level, 3))
                h_para = doc.add_heading(str(sec["heading"]), level=level)
                h_para.paragraph_format.space_before = Pt(10)
                h_para.paragraph_format.space_after = Pt(4)

            # Paragraphs
            if "paragraph" in sec:
                p_text = str(sec["paragraph"])
                p = doc.add_paragraph(p_text)
                p.paragraph_format.space_after = Pt(6)

            # Bullet points
            if "bullets" in sec and isinstance(sec["bullets"], (list, tuple)):
                for item in sec["bullets"]:
                    doc.add_paragraph(str(item), style="List Bullet")

            # Numbered list
            if "numbered" in sec and isinstance(sec["numbered"], (list, tuple)):
                for item in sec["numbered"]:
                    doc.add_paragraph(str(item), style="List Number")

            # Tables
            if "table" in sec and isinstance(sec["table"], dict):
                tbl_data = sec["table"]
                headers = tbl_data.get("headers", [])
                rows = tbl_data.get("rows", [])
                if headers:
                    table = doc.add_table(rows=1, cols=len(headers))
                    table.style = "Table Grid"
                    hdr_cells = table.rows[0].cells
                    for idx, header in enumerate(headers):
                        hdr_cells[idx].text = str(header)
                        # Bold headers
                        for p in hdr_cells[idx].paragraphs:
                            for r in p.runs:
                                r.font.bold = True

                    for row_data in rows:
                        row_cells = table.add_row().cells
                        for c_idx, val in enumerate(row_data):
                            if c_idx < len(row_cells):
                                row_cells[c_idx].text = str(val)
                    doc.add_paragraph()  # spacing after table

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def generate_pdf(
    title: str,
    sections: list[dict[str, Any]] | None = None,
    author: str = "Bimo",
    style: str = "clean",
    hero_image_bytes: bytes | None = None,
    hero_image_caption: str = "",
    summary: str = "",
    callouts: list[str] | None = None,
    tips: list[str] | None = None,
    materials: list[list[Any]] | None = None,
) -> bytes:
    """Generate a clean or visual-first styled PDF document using ReportLab.

    Supports:
    - Dedicated visual themes: 'minecraft', 'modern', 'tech', 'medieval', 'clean'
    - Scaled hero image insertion and captions
    - Architectural callout cards & highlights
    - Materials and specifications tables
    - Numbered build steps and bullet lists
    - Pro tips boxes
    """
    if style.lower() != "clean" or hero_image_bytes or callouts or tips or materials or summary:
        from bimo.artifacts.composer import VisualDocumentComposer
        composer = VisualDocumentComposer(style=style)
        return composer.compose_pdf(
            title=title,
            summary=summary,
            hero_image_bytes=hero_image_bytes,
            hero_image_caption=hero_image_caption,
            materials=materials,
            sections=sections,
            callouts=callouts,
            tips=tips,
            author=author,
        )
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.platypus import (
            HRFlowable,
            PageBreak,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as exc:
        raise RuntimeError(
            "reportlab is required for PDF generation. Install via 'pip install reportlab'."
        ) from exc

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=54,
        bottomMargin=54,
        title=title,
        author=author,
    )

    styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        "BimoDocTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=22,
        leading=26,
        textColor=colors.HexColor("#1A365D"),
        spaceAfter=12,
    )
    h1_style = ParagraphStyle(
        "BimoH1",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=19,
        textColor=colors.HexColor("#2B6CB0"),
        spaceBefore=12,
        spaceAfter=6,
    )
    h2_style = ParagraphStyle(
        "BimoH2",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=16,
        textColor=colors.HexColor("#2D3748"),
        spaceBefore=8,
        spaceAfter=4,
    )
    body_style = ParagraphStyle(
        "BimoBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#2D3748"),
        spaceAfter=6,
    )
    bullet_style = ParagraphStyle(
        "BimoBullet",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#2D3748"),
        leftIndent=15,
        spaceAfter=3,
    )

    story = []

    if title:
        story.append(Paragraph(title, title_style))
        story.append(
            HRFlowable(
                width="100%",
                thickness=1.5,
                color=colors.HexColor("#3182CE"),
                spaceAfter=12,
                spaceBefore=0,
            )
        )

    if sections:
        for sec in sections:
            if not isinstance(sec, dict):
                continue

            if sec.get("page_break"):
                story.append(PageBreak())
                continue

            if "heading" in sec:
                level = int(sec.get("level", 1))
                h_style = h1_style if level == 1 else h2_style
                story.append(Paragraph(str(sec["heading"]), h_style))

            if "paragraph" in sec:
                # Sanitize HTML tags for ReportLab Paragraph
                text = (
                    str(sec["paragraph"])
                    .replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                )
                story.append(Paragraph(text, body_style))

            if "bullets" in sec and isinstance(sec["bullets"], (list, tuple)):
                for b in sec["bullets"]:
                    safe_b = (
                        str(b)
                        .replace("&", "&amp;")
                        .replace("<", "&lt;")
                        .replace(">", "&gt;")
                    )
                    story.append(Paragraph(f"&bull; {safe_b}", bullet_style))
                story.append(Spacer(1, 4))

            if "numbered" in sec and isinstance(sec["numbered"], (list, tuple)):
                for idx, n in enumerate(sec["numbered"], start=1):
                    safe_n = (
                        str(n)
                        .replace("&", "&amp;")
                        .replace("<", "&lt;")
                        .replace(">", "&gt;")
                    )
                    story.append(Paragraph(f"{idx}. {safe_n}", bullet_style))
                story.append(Spacer(1, 4))

            if "table" in sec and isinstance(sec["table"], dict):
                tbl_data = sec["table"]
                headers = tbl_data.get("headers", [])
                rows = tbl_data.get("rows", [])
                if headers:
                    table_matrix = [[str(h) for h in headers]]
                    for r in rows:
                        table_matrix.append([str(c) for c in r])

                    t = Table(table_matrix, repeatRows=1)
                    t.setStyle(
                        TableStyle([
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EBF8FF")),
                            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#2B6CB0")),
                            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                            ("FONTSIZE", (0, 0), (-1, -1), 9),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                            ("TOPPADDING", (0, 0), (-1, -1), 5),
                            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
                        ])
                    )
                    story.append(t)
                    story.append(Spacer(1, 8))

    doc.build(story)
    return buf.getvalue()


def generate_xlsx(sheets: list[dict[str, Any]]) -> bytes:
    """Generate a clean Microsoft Excel (.xlsx) workbook using OpenPyXL.

    Arguments structure:
    [
      {
        "name": "Sheet1",
        "columns": ["Item", "Cost", "Quantity"],
        "rows": [["Widget", 10.5, 4], ["Gear", 5.0, 10]]
      }
    ]
    """
    try:
        import openpyxl
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter
    except ImportError as exc:
        raise RuntimeError(
            "openpyxl is required for XLSX generation. Install via 'pip install openpyxl'."
        ) from exc

    if not sheets or not isinstance(sheets, (list, tuple)):
        raise ValueError("XLSX requires at least one sheet specification.")

    wb = openpyxl.Workbook()
    # Remove default sheet
    default_sheet = wb.active

    header_fill = PatternFill(start_color="1A365D", end_color="1A365D", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    data_font = Font(name="Calibri", size=11)
    thin_border = Border(
        left=Side(style="thin", color="D1D5DB"),
        right=Side(style="thin", color="D1D5DB"),
        top=Side(style="thin", color="D1D5DB"),
        bottom=Side(style="thin", color="D1D5DB"),
    )

    for i, s_spec in enumerate(sheets):
        if not isinstance(s_spec, dict):
            continue
        s_name = s_spec.get("name", f"Sheet{i+1}").strip()
        # Excel sheet name limits: max 31 chars, no : \ / ? * [ ]
        s_name = s_name.replace(":", "_").replace("\\", "_").replace("/", "_")[:31] or f"Sheet{i+1}"
        ws = wb.create_sheet(title=s_name)

        columns = s_spec.get("columns", [])
        rows = s_spec.get("rows", [])

        if columns:
            ws.append([str(c) for c in columns])
            for col_idx in range(1, len(columns) + 1):
                cell = ws.cell(row=1, column=col_idx)
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = Alignment(horizontal="center", vertical="center")
                cell.border = thin_border

        for r_idx, row in enumerate(rows, start=2):
            ws.append(list(row))
            for c_idx in range(1, len(row) + 1):
                cell = ws.cell(row=r_idx, column=c_idx)
                cell.font = data_font
                cell.border = thin_border

        # Auto-adjust column widths
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                val_str = str(cell.value or "")
                if len(val_str) > max_len:
                    max_len = len(val_str)
            ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

    # Clean up empty default sheet
    if default_sheet and default_sheet in wb.worksheets and len(wb.worksheets) > 1:
        wb.remove(default_sheet)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def validate_and_process_image(data: bytes, expected_format: str = "png") -> bytes:
    """Validate image bytes using Pillow, confirming format and bounding integrity."""
    from PIL import Image

    fmt = expected_format.lower().lstrip(".")
    if fmt in ("svg", "svg+xml"):
        # SVG validation: check for valid XML / <svg tag
        text = data.decode("utf-8", errors="ignore")
        if "<svg" not in text.lower():
            raise ValueError("Data is not a valid SVG document.")
        return data

    try:
        buf = io.BytesIO(data)
        img = Image.open(buf)
        img.verify()
    except Exception as exc:
        raise ValueError(f"Invalid image binary data: {exc}") from exc

    return data
