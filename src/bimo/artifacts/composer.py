"""Visual Document Composer for Bimo.

Composes professional, visual-first documents (PDF) featuring:
- Theme-based typography & palettes (Minecraft, Modern, Tech, Medieval, Clean)
- Hero image insertion with aspect-ratio preserving scaling
- Architectural callout cards with accent borders
- Materials & specifications tables
- Concise step-by-step build walkthroughs
- Pro tips highlight boxes
- Strict visual hierarchy, whitespace discipline, and pagination
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any

from PIL import Image as PILImage

logger = logging.getLogger(__name__)

# Theme color definitions
THEME_PALETTES: dict[str, dict[str, str]] = {
    "minecraft": {
        "title": "#3C2415",        # Rich dark oak
        "subtitle": "#78350F",     # Warm cedar
        "h1": "#4A2810",           # Deep timber
        "h2": "#92400E",           # Terracotta / Amber
        "accent": "#276749",       # Spruce leaf green
        "highlight": "#D97706",    # Warm lantern gold
        "callout_bg": "#FEF3C7",   # Warm parchment fill
        "callout_border": "#B45309", # Lantern border
        "table_header": "#78350F", # Dark wood table header
        "table_header_text": "#FFFFFF",
        "table_row_alt": "#FFFBEB",
        "tip_bg": "#ECFDF5",       # Soft emerald tint
        "tip_border": "#059669",
        "rule": "#B45309",
    },
    "modern": {
        "title": "#0F172A",
        "subtitle": "#334155",
        "h1": "#1E3A8A",
        "h2": "#1D4ED8",
        "accent": "#0284C7",
        "highlight": "#2563EB",
        "callout_bg": "#EFF6FF",
        "callout_border": "#3B82F6",
        "table_header": "#1E3A8A",
        "table_header_text": "#FFFFFF",
        "table_row_alt": "#F8FAFC",
        "tip_bg": "#F0FDF4",
        "tip_border": "#16A34A",
        "rule": "#3B82F6",
    },
    "tech": {
        "title": "#090D16",
        "subtitle": "#1E293B",
        "h1": "#0369A1",
        "h2": "#0284C7",
        "accent": "#0EA5E9",
        "highlight": "#38BDF8",
        "callout_bg": "#F0F9FF",
        "callout_border": "#0284C7",
        "table_header": "#0F172A",
        "table_header_text": "#38BDF8",
        "table_row_alt": "#F8FAFC",
        "tip_bg": "#ECFEFF",
        "tip_border": "#0891B2",
        "rule": "#0EA5E9",
    },
    "medieval": {
        "title": "#26130B",
        "subtitle": "#451A03",
        "h1": "#78350F",
        "h2": "#9A3412",
        "accent": "#854D0E",
        "highlight": "#B45309",
        "callout_bg": "#FEFCE8",
        "callout_border": "#A16207",
        "table_header": "#451A03",
        "table_header_text": "#FEF08A",
        "table_row_alt": "#FFFBEB",
        "tip_bg": "#FEF2F2",
        "tip_border": "#B91C1C",
        "rule": "#A16207",
    },
    "clean": {
        "title": "#18181B",
        "subtitle": "#3F3F46",
        "h1": "#27272A",
        "h2": "#52525B",
        "accent": "#4F46E5",
        "highlight": "#6366F1",
        "callout_bg": "#F4F4F5",
        "callout_border": "#71717A",
        "table_header": "#27272A",
        "table_header_text": "#FFFFFF",
        "table_row_alt": "#FAFAFA",
        "tip_bg": "#EEF2FF",
        "tip_border": "#4F46E5",
        "rule": "#E4E4E7",
    },
}


class VisualDocumentComposer:
    """Composes structured, visual-first documents using ReportLab Platypus."""

    def __init__(self, style: str = "minecraft") -> None:
        self.style = style.lower().strip()
        self.palette = THEME_PALETTES.get(self.style, THEME_PALETTES["clean"])

    def _scale_image_for_page(
        self,
        img_bytes: bytes,
        max_width_pt: float = 460.0,
        max_height_pt: float = 240.0,
    ) -> tuple[io.BytesIO, float, float]:
        """Read dimensions from image bytes and compute proportional scaling."""
        bio = io.BytesIO(img_bytes)
        with PILImage.open(bio) as pil_img:
            orig_w, orig_h = pil_img.size

        aspect = orig_w / float(orig_h) if orig_h > 0 else 1.0

        # Fit within bounding box
        target_w = max_width_pt
        target_h = target_w / aspect

        if target_h > max_height_pt:
            target_h = max_height_pt
            target_w = target_h * aspect

        bio.seek(0)
        return bio, target_w, target_h

    def compose_pdf(
        self,
        title: str,
        summary: str = "",
        hero_image_bytes: bytes | None = None,
        hero_image_caption: str = "",
        materials: list[list[Any]] | None = None,
        sections: list[dict[str, Any]] | None = None,
        callouts: list[str] | None = None,
        tips: list[str] | None = None,
        author: str = "Bimo",
    ) -> bytes:
        """Compose and return a complete PDF byte stream with visual hierarchy."""
        try:
            from reportlab.lib import colors
            from reportlab.lib.pagesizes import letter
            from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
            from reportlab.platypus import (
                HRFlowable,
                Image as RLImage,
                KeepTogether,
                PageBreak,
                Paragraph,
                SimpleDocTemplate,
                Spacer,
                Table,
                TableStyle,
            )
        except ImportError as exc:
            raise RuntimeError(
                "reportlab is required for VisualDocumentComposer. Install via 'pip install reportlab'."
            ) from exc

        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf,
            pagesize=letter,
            leftMargin=54,
            rightMargin=54,
            topMargin=50,
            bottomMargin=50,
            title=title,
            author=author,
        )

        base_styles = getSampleStyleSheet()

        # Build Theme ParagraphStyles
        title_style = ParagraphStyle(
            "VisualTitle",
            parent=base_styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=26,
            textColor=colors.HexColor(self.palette["title"]),
            spaceAfter=4,
        )

        summary_style = ParagraphStyle(
            "VisualSummary",
            parent=base_styles["Normal"],
            fontName="Helvetica-Oblique",
            fontSize=11,
            leading=15,
            textColor=colors.HexColor(self.palette["subtitle"]),
            spaceAfter=12,
        )

        h1_style = ParagraphStyle(
            "VisualH1",
            parent=base_styles["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=14,
            leading=18,
            textColor=colors.HexColor(self.palette["h1"]),
            spaceBefore=14,
            spaceAfter=6,
            keepWithNext=True,
        )

        h2_style = ParagraphStyle(
            "VisualH2",
            parent=base_styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=15,
            textColor=colors.HexColor(self.palette["h2"]),
            spaceBefore=8,
            spaceAfter=4,
            keepWithNext=True,
        )

        body_style = ParagraphStyle(
            "VisualBody",
            parent=base_styles["Normal"],
            fontName="Helvetica",
            fontSize=10,
            leading=14,
            textColor=colors.HexColor("#27272A"),
            spaceAfter=6,
        )

        caption_style = ParagraphStyle(
            "VisualCaption",
            parent=base_styles["Normal"],
            fontName="Helvetica-Oblique",
            fontSize=8.5,
            leading=11,
            alignment=1,  # Centered
            textColor=colors.HexColor(self.palette["subtitle"]),
            spaceBefore=4,
            spaceAfter=10,
        )

        callout_title_style = ParagraphStyle(
            "VisualCalloutTitle",
            parent=base_styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9.5,
            leading=13,
            textColor=colors.HexColor(self.palette["callout_border"]),
        )

        callout_body_style = ParagraphStyle(
            "VisualCalloutBody",
            parent=base_styles["Normal"],
            fontName="Helvetica",
            fontSize=9,
            leading=12.5,
            textColor=colors.HexColor("#1F2937"),
        )

        story = []

        # 1. Title Banner
        story.append(Paragraph(title, title_style))
        story.append(
            HRFlowable(
                width="100%",
                thickness=2.0,
                color=colors.HexColor(self.palette["rule"]),
                spaceBefore=2,
                spaceAfter=8,
            )
        )

        # 2. Executive Concept Summary
        if summary:
            story.append(Paragraph(summary, summary_style))

        # 3. Hero Image (if supplied)
        if hero_image_bytes and len(hero_image_bytes) > 0:
            try:
                img_io, scaled_w, scaled_h = self._scale_image_for_page(hero_image_bytes)
                rl_img = RLImage(img_io, width=scaled_w, height=scaled_h)
                rl_img.hAlign = "CENTER"
                story.append(rl_img)

                caption_text = hero_image_caption or f"Figure 1: Finished architectural concept ({self.style.title()} Style)."
                story.append(Paragraph(caption_text, caption_style))
            except Exception as img_exc:
                logger.warning("Could not embed hero image into visual PDF: %s", img_exc)

        # 4. Architectural Callouts Card (Highlighting details of the concept)
        if callouts:
            callout_cells = []
            for item in callouts:
                safe_item = str(item).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                callout_cells.append([
                    Paragraph("&#9656;", callout_title_style),
                    Paragraph(f"<b>Key Detail:</b> {safe_item}", callout_body_style),
                ])

            callout_table = Table(callout_cells, colWidths=[20, 484])
            callout_table.setStyle(
                TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(self.palette["callout_bg"])),
                    ("BOX", (0, 0), (-1, -1), 1.0, colors.HexColor(self.palette["callout_border"])),
                    ("LINELEFT", (0, 0), (0, -1), 3.5, colors.HexColor(self.palette["callout_border"])),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ])
            )
            story.append(Spacer(1, 4))
            story.append(callout_table)
            story.append(Spacer(1, 10))

        # 5. Materials / Specifications Table
        if materials:
            story.append(Paragraph("Required Materials & Palette", h1_style))
            headers = ["Material", "Structural Role", "Quantity"]
            matrix = [headers]
            for row in materials:
                matrix.append([str(c) for c in row])

            mat_table = Table(matrix, colWidths=[160, 220, 124], repeatRows=1)
            t_style = [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(self.palette["table_header"])),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor(self.palette["table_header_text"])),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
            ]
            for r_i in range(1, len(matrix)):
                if r_i % 2 == 0:
                    t_style.append(("BACKGROUND", (0, r_i), (-1, r_i), colors.HexColor(self.palette["table_row_alt"])))

            mat_table.setStyle(TableStyle(t_style))
            story.append(mat_table)
            story.append(Spacer(1, 10))

        # 6. Structured Sections
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
                    text = str(sec["paragraph"]).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    story.append(Paragraph(text, body_style))

                if "numbered" in sec and isinstance(sec["numbered"], (list, tuple)):
                    for idx, n in enumerate(sec["numbered"], start=1):
                        safe_n = str(n).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                        step_p = Paragraph(f"<b>Step {idx}:</b> {safe_n}", body_style)
                        story.append(step_p)
                    story.append(Spacer(1, 4))

                if "bullets" in sec and isinstance(sec["bullets"], (list, tuple)):
                    for b in sec["bullets"]:
                        safe_b = str(b).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                        bullet_p = Paragraph(f"&bull; {safe_b}", body_style)
                        story.append(bullet_p)
                    story.append(Spacer(1, 4))

        # 7. Pro Detailing Tips Box
        if tips:
            story.append(Spacer(1, 6))
            story.append(Paragraph("Pro Detailing & Build Tips", h1_style))
            tip_rows = []
            for t_item in tips:
                safe_t = str(t_item).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                tip_rows.append([
                    Paragraph("&#9733;", ParagraphStyle("TipStar", parent=base_styles["Normal"], textColor=colors.HexColor(self.palette["tip_border"]), fontSize=10)),
                    Paragraph(safe_t, ParagraphStyle("TipText", parent=base_styles["Normal"], fontSize=9, leading=12.5, textColor=colors.HexColor("#064E3B"))),
                ])

            tip_table = Table(tip_rows, colWidths=[18, 486])
            tip_table.setStyle(
                TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(self.palette["tip_bg"])),
                    ("BOX", (0, 0), (-1, -1), 1.0, colors.HexColor(self.palette["tip_border"])),
                    ("LINELEFT", (0, 0), (0, -1), 3.0, colors.HexColor(self.palette["tip_border"])),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ])
            )
            story.append(tip_table)

        doc.build(story)
        return buf.getvalue()
