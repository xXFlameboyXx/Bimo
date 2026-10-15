"""Deterministic local image renderer for charts, diagrams, badges, and posters.

Runs 100% offline using Pillow (PIL), ensuring Bimo can always generate visual assets
even when remote image models are unavailable.
"""

from __future__ import annotations

import io
import math
from typing import Any
from PIL import Image, ImageDraw, ImageFont


class DeterministicLocalImageRenderer:
    """Renders structured diagrams, charts, status badges, and cards into PNG/JPEG bytes."""

    # Curated modern color palettes
    PALETTE = [
        (59, 130, 246),   # Blue
        (16, 185, 129),   # Emerald
        (245, 158, 11),   # Amber
        (239, 68, 68),    # Red
        (139, 92, 246),   # Purple
        (236, 72, 153),   # Pink
        (20, 184, 166),   # Teal
        (99, 102, 241),   # Indigo
    ]

    BG_COLOR = (15, 23, 42)       # Slate 900
    CARD_BG = (30, 41, 59)        # Slate 800
    TEXT_MAIN = (248, 250, 252)   # Slate 50
    TEXT_MUTED = (148, 163, 184)  # Slate 400
    BORDER_COLOR = (51, 65, 85)   # Slate 700

    def __init__(self) -> None:
        pass

    def _get_font(self, size: int = 16) -> ImageFont.ImageFont:
        """Attempt to load a standard system font, falling back to PIL default."""
        for font_name in ("arial.ttf", "segoeui.ttf", "calibri.ttf", "DejaVuSans.ttf"):
            try:
                return ImageFont.truetype(font_name, size)
            except Exception:
                pass
        return ImageFont.load_default()

    def render_bar_chart(
        self,
        title: str,
        labels: list[str],
        values: list[float],
        width: int = 800,
        height: int = 500,
    ) -> bytes:
        """Render a clean, high-contrast bar chart."""
        img = Image.new("RGB", (width, height), color=self.BG_COLOR)
        draw = ImageDraw.Draw(img)

        title_font = self._get_font(22)
        label_font = self._get_font(13)
        val_font = self._get_font(12)

        # Title
        draw.text((40, 30), title, font=title_font, fill=self.TEXT_MAIN)

        if not labels or not values or len(labels) != len(values):
            draw.text((40, 80), "No data available", font=label_font, fill=self.TEXT_MUTED)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()

        # Chart bounds
        left, top, right, bottom = 80, 90, width - 40, height - 80
        chart_w = right - left
        chart_h = bottom - top

        # Axes
        draw.line([(left, top), (left, bottom), (right, bottom)], fill=self.BORDER_COLOR, width=2)

        max_val = max(values) if max(values) > 0 else 1.0
        n_bars = len(labels)
        bar_slot = chart_w / n_bars
        bar_w = bar_slot * 0.65
        gap = bar_slot * 0.35

        for i, (label, val) in enumerate(zip(labels, values)):
            bar_h = (val / max_val) * (chart_h - 20)
            bar_x0 = left + (i * bar_slot) + (gap / 2)
            bar_y0 = bottom - bar_h
            bar_x1 = bar_x0 + bar_w
            bar_y1 = bottom - 1

            color = self.PALETTE[i % len(self.PALETTE)]
            draw.rectangle([bar_x0, bar_y0, bar_x1, bar_y1], fill=color, outline=None)

            # Value label above bar
            val_str = f"{val:.1f}" if isinstance(val, float) and not val.is_integer() else f"{int(val)}"
            draw.text((bar_x0 + 4, bar_y0 - 18), val_str, font=val_font, fill=self.TEXT_MAIN)

            # X-axis label below bar
            short_lbl = label[:12] + ".." if len(label) > 12 else label
            draw.text((bar_x0, bottom + 10), short_lbl, font=label_font, fill=self.TEXT_MUTED)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def render_badge(
        self,
        title: str,
        subtitle: str,
        status: str = "ACTIVE",
        width: int = 600,
        height: int = 320,
    ) -> bytes:
        """Render a modern visual card / status badge."""
        img = Image.new("RGB", (width, height), color=self.BG_COLOR)
        draw = ImageDraw.Draw(img)

        # Outer card container
        pad = 20
        draw.rounded_rectangle(
            [pad, pad, width - pad, height - pad],
            radius=16,
            fill=self.CARD_BG,
            outline=self.BORDER_COLOR,
            width=2,
        )

        title_font = self._get_font(24)
        sub_font = self._get_font(14)
        status_font = self._get_font(13)

        # Status chip in upper right
        chip_w, chip_h = 100, 32
        chip_x0 = width - pad - 20 - chip_w
        chip_y0 = pad + 20
        status_color = (16, 185, 129) if "ok" in status.lower() or "active" in status.lower() else (59, 130, 246)
        draw.rounded_rectangle(
            [chip_x0, chip_y0, chip_x0 + chip_w, chip_y0 + chip_h],
            radius=8,
            fill=(status_color[0] // 4, status_color[1] // 4, status_color[2] // 4),
            outline=status_color,
            width=1,
        )
        draw.text((chip_x0 + 16, chip_y0 + 7), status.upper(), font=status_font, fill=status_color)

        # Title & Subtitle
        draw.text((pad + 30, pad + 40), title, font=title_font, fill=self.TEXT_MAIN)
        draw.text((pad + 30, pad + 90), subtitle, font=sub_font, fill=self.TEXT_MUTED)

        # Decorative line
        draw.line([(pad + 30, pad + 150), (width - pad - 30, pad + 150)], fill=self.BORDER_COLOR, width=1)

        # Watermark
        draw.text((pad + 30, height - pad - 40), "Generated locally by Bimo Robot", font=sub_font, fill=(100, 116, 139))

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def render_diagram(
        self,
        title: str,
        steps: list[str],
        width: int = 900,
        height: int = 400,
    ) -> bytes:
        """Render a horizontal step-by-step flowchart diagram."""
        img = Image.new("RGB", (width, height), color=self.BG_COLOR)
        draw = ImageDraw.Draw(img)

        title_font = self._get_font(22)
        step_font = self._get_font(13)
        num_font = self._get_font(11)

        draw.text((40, 30), title, font=title_font, fill=self.TEXT_MAIN)

        if not steps:
            steps = ["Start", "Process", "Complete"]

        n = len(steps)
        pad_x = 50
        total_w = width - (pad_x * 2)
        box_w = min(150, (total_w - (n - 1) * 40) // n)
        box_h = 90
        start_y = height // 2 - box_h // 2

        spacing = (total_w - (n * box_w)) // max(1, n - 1) if n > 1 else 0

        for i, step in enumerate(steps):
            bx0 = pad_x + i * (box_w + spacing)
            by0 = start_y
            bx1 = bx0 + box_w
            by1 = by0 + box_h

            color = self.PALETTE[i % len(self.PALETTE)]

            # Step box
            draw.rounded_rectangle([bx0, by0, bx1, by1], radius=10, fill=self.CARD_BG, outline=color, width=2)

            # Step number badge
            draw.ellipse([bx0 + 10, by0 + 10, bx0 + 30, by0 + 30], fill=color)
            draw.text((bx0 + 16, by0 + 13), str(i + 1), font=num_font, fill=(255, 255, 255))

            # Step text
            lines = [step[j:j+14] for j in range(0, len(step), 14)][:3]
            for l_idx, line in enumerate(lines):
                draw.text((bx0 + 12, by0 + 38 + l_idx * 16), line, font=step_font, fill=self.TEXT_MAIN)

            # Arrow to next box
            if i < n - 1:
                arrow_start_x = bx1 + 6
                arrow_end_x = bx1 + spacing - 6
                arrow_y = start_y + box_h // 2
                draw.line([(arrow_start_x, arrow_y), (arrow_end_x, arrow_y)], fill=(148, 163, 184), width=2)
                # Arrowhead
                draw.polygon([
                    (arrow_end_x, arrow_y),
                    (arrow_end_x - 6, arrow_y - 4),
                    (arrow_end_x - 6, arrow_y + 4),
                ], fill=(148, 163, 184))

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
