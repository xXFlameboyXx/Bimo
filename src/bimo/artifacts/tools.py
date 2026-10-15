"""Bimo Tool implementations for Artifact and Image Generation (Phase 6 & Refinement).

Provides standard BaseTool instances:
- artifact.create_text (CONFIRM)
- artifact.create_markdown (CONFIRM)
- artifact.create_json (CONFIRM)
- artifact.create_csv (CONFIRM)
- artifact.create_docx (CONFIRM)
- artifact.create_pdf (CONFIRM)
- artifact.create_xlsx (CONFIRM)
- artifact.save_image (CONFIRM)
- artifact.list (SAFE)
- artifact.get_info (SAFE)
- image.generate (CONFIRM)
- image.render_local (CONFIRM)
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

from bimo.artifacts.generators import (
    generate_csv,
    generate_docx,
    generate_json,
    generate_markdown,
    generate_pdf,
    generate_text,
    generate_xlsx,
    validate_and_process_image,
)
from bimo.artifacts.planner import ArtifactIntent, ArtifactPlan, ArtifactPlanner
from bimo.artifacts.service import ArtifactService
from bimo.images.local_renderer import DeterministicLocalImageRenderer
from bimo.images.provider import ImageGenerationProvider
from bimo.tools.base import BaseTool, ToolParameter, ToolResult
from bimo.tools.permissions import ToolPermission
from bimo.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class ArtifactCreateTextTool(BaseTool):
    """Creates a UTF-8 text (.txt) artifact safely within workspace boundaries."""

    def __init__(self, service: ArtifactService) -> None:
        self.service = service
        super().__init__(
            name="artifact.create_text",
            description="Create a plain text (.txt) file in the Bimo artifacts directory.",
            permission=ToolPermission.CONFIRM,
            parameters=[
                ToolParameter(
                    name="filename",
                    type="string",
                    description="Name of the text file (e.g. 'notes.txt'). Must end with .txt.",
                    required=True,
                ),
                ToolParameter(
                    name="content",
                    type="string",
                    description="The raw text content to write into the file.",
                    required=True,
                ),
            ],
        )

    def execute(self, filename: str, content: str = "", **kwargs: Any) -> ToolResult:
        try:
            raw_bytes = generate_text(content)
            artifact = self.service.create_artifact(
                filename=filename,
                data=raw_bytes,
                mime_type="text/plain",
                metadata={"type": "text"},
            )
            return ToolResult(
                success=True,
                output=f"Created text file '{artifact.filename}' in artifacts workspace ({artifact.size_bytes} bytes).",
                tool_name=self.name,
                metadata=artifact.to_dict(),
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"Failed to create text file: {exc}",
                tool_name=self.name,
            )


class ArtifactCreateMarkdownTool(BaseTool):
    """Creates a Markdown (.md) document safely within workspace boundaries."""

    def __init__(self, service: ArtifactService) -> None:
        self.service = service
        super().__init__(
            name="artifact.create_markdown",
            description="Create a formatted Markdown (.md) document in the Bimo artifacts directory.",
            permission=ToolPermission.CONFIRM,
            parameters=[
                ToolParameter(
                    name="filename",
                    type="string",
                    description="Name of the markdown file (e.g. 'project_spec.md'). Must end with .md.",
                    required=True,
                ),
                ToolParameter(
                    name="content",
                    type="string",
                    description="The Markdown formatted content to write.",
                    required=True,
                ),
            ],
        )

    def execute(self, filename: str, content: str = "", **kwargs: Any) -> ToolResult:
        try:
            raw_bytes = generate_markdown(content)
            artifact = self.service.create_artifact(
                filename=filename,
                data=raw_bytes,
                mime_type="text/markdown",
                metadata={"type": "markdown"},
            )
            return ToolResult(
                success=True,
                output=f"Created markdown document '{artifact.filename}' ({artifact.size_bytes} bytes).",
                tool_name=self.name,
                metadata=artifact.to_dict(),
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"Failed to create markdown document: {exc}",
                tool_name=self.name,
            )


class ArtifactCreateJsonTool(BaseTool):
    """Creates a structured JSON (.json) artifact safely within workspace boundaries."""

    def __init__(self, service: ArtifactService) -> None:
        self.service = service
        super().__init__(
            name="artifact.create_json",
            description="Create a structured JSON (.json) file in the Bimo artifacts directory.",
            permission=ToolPermission.CONFIRM,
            parameters=[
                ToolParameter(
                    name="filename",
                    type="string",
                    description="Name of the JSON file (e.g. 'data.json'). Must end with .json.",
                    required=True,
                ),
                ToolParameter(
                    name="data",
                    type="object",
                    description="Dictionary or structured data to serialize as JSON.",
                    required=True,
                ),
                ToolParameter(
                    name="indent",
                    type="integer",
                    description="Formatting indentation spaces (default: 2).",
                    required=False,
                ),
            ],
        )

    def execute(self, filename: str, data: Any = None, indent: int = 2, **kwargs: Any) -> ToolResult:
        try:
            raw_bytes = generate_json(data=data if data is not None else {}, indent=indent)
            artifact = self.service.create_artifact(
                filename=filename,
                data=raw_bytes,
                mime_type="application/json",
                metadata={"type": "json"},
            )
            return ToolResult(
                success=True,
                output=f"Created JSON file '{artifact.filename}' ({artifact.size_bytes} bytes).",
                tool_name=self.name,
                metadata=artifact.to_dict(),
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"Failed to create JSON file: {exc}",
                tool_name=self.name,
            )


class ArtifactCreateCsvTool(BaseTool):
    """Creates a structured CSV (.csv) artifact safely within workspace boundaries."""

    def __init__(self, service: ArtifactService) -> None:
        self.service = service
        super().__init__(
            name="artifact.create_csv",
            description="Create a structured CSV (.csv) file with column headers and rows.",
            permission=ToolPermission.CONFIRM,
            parameters=[
                ToolParameter(
                    name="filename",
                    type="string",
                    description="Name of the CSV file (e.g. 'people.csv'). Must end with .csv.",
                    required=True,
                ),
                ToolParameter(
                    name="columns",
                    type="array",
                    description="List of column header names (e.g. ['Name', 'Role', 'Status']).",
                    required=True,
                    items={"type": "string"},
                ),
                ToolParameter(
                    name="rows",
                    type="array",
                    description="List of data rows matching the column structure.",
                    required=True,
                    items={"type": "array", "items": {"type": "string"}},
                ),
            ],
        )

    def execute(
        self,
        filename: str,
        columns: list[str] | None = None,
        rows: list[list[Any]] | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        try:
            raw_bytes = generate_csv(columns=columns or [], rows=rows or [])
            artifact = self.service.create_artifact(
                filename=filename,
                data=raw_bytes,
                mime_type="text/csv",
                metadata={"columns": columns, "row_count": len(rows or [])},
            )
            return ToolResult(
                success=True,
                output=f"Created CSV file '{artifact.filename}' with {len(rows or [])} rows.",
                tool_name=self.name,
                metadata=artifact.to_dict(),
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"Failed to create CSV file: {exc}",
                tool_name=self.name,
            )


class ArtifactCreateDocxTool(BaseTool):
    """Creates a real Microsoft Word document (.docx) without requiring MS Word to be installed."""

    def __init__(self, service: ArtifactService) -> None:
        self.service = service
        super().__init__(
            name="artifact.create_docx",
            description="Create a real Microsoft Word (.docx) document with titles, headings, bullet lists, and tables.",
            permission=ToolPermission.CONFIRM,
            parameters=[
                ToolParameter(
                    name="filename",
                    type="string",
                    description="Name of the Word file (e.g. 'project_report.docx'). Must end with .docx.",
                    required=True,
                ),
                ToolParameter(
                    name="title",
                    type="string",
                    description="Main title of the Word document.",
                    required=True,
                ),
                ToolParameter(
                    name="sections",
                    type="array",
                    description=(
                        "List of document sections. Each item can have 'heading', 'paragraph', "
                        "'bullets' (list of strings), 'numbered' (list of strings), or 'table' ({'headers': [...], 'rows': [[...]]})."
                    ),
                    required=False,
                    items={"type": "object"},
                ),
            ],
        )

    def execute(
        self,
        filename: str,
        title: str,
        sections: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        try:
            raw_bytes = generate_docx(title=title, sections=sections or [])
            artifact = self.service.create_artifact(
                filename=filename,
                data=raw_bytes,
                mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                metadata={"title": title, "sections_count": len(sections or [])},
            )
            return ToolResult(
                success=True,
                output=f"Created Word document '{artifact.filename}' ({artifact.size_bytes} bytes).",
                tool_name=self.name,
                metadata=artifact.to_dict(),
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"Failed to create Word document: {exc}",
                tool_name=self.name,
            )


class ArtifactCreatePdfTool(BaseTool):
    """Creates a formatted or visual-first PDF (.pdf) with ReportLab."""

    def __init__(self, service: ArtifactService) -> None:
        self.service = service
        super().__init__(
            name="artifact.create_pdf",
            description="Create a formatted PDF document with title, headings, paragraphs, bullet lists, tables, and optional hero image.",
            permission=ToolPermission.CONFIRM,
            parameters=[
                ToolParameter(
                    name="filename",
                    type="string",
                    description="Name of the PDF file (e.g. 'bimo_architecture.pdf'). Must end with .pdf.",
                    required=True,
                ),
                ToolParameter(
                    name="title",
                    type="string",
                    description="Title of the PDF document.",
                    required=True,
                ),
                ToolParameter(
                    name="style",
                    type="string",
                    description="Visual theme: 'minecraft', 'modern', 'clean', 'tech', 'medieval'.",
                    required=False,
                    enum_values=["minecraft", "modern", "clean", "tech", "medieval"],
                ),
                ToolParameter(
                    name="summary",
                    type="string",
                    description="Optional introductory concept summary.",
                    required=False,
                ),
                ToolParameter(
                    name="hero_image_id",
                    type="string",
                    description="Optional artifact ID or filename of an existing image to embed as hero visual.",
                    required=False,
                ),
                ToolParameter(
                    name="hero_image_caption",
                    type="string",
                    description="Caption displayed beneath hero image.",
                    required=False,
                ),
                ToolParameter(
                    name="materials",
                    type="array",
                    description="Optional materials/specifications rows: [['Item', 'Role', 'Qty']].",
                    required=False,
                    items={"type": "array", "items": {"type": "string"}},
                ),
                ToolParameter(
                    name="sections",
                    type="array",
                    description=(
                        "List of content sections. Each section can contain 'heading', 'paragraph', "
                        "'bullets' (list of strings), 'numbered' (list of strings), or 'table' ({'headers': [...], 'rows': [[...]]})."
                    ),
                    required=False,
                    items={"type": "object"},
                ),
                ToolParameter(
                    name="callouts",
                    type="array",
                    description="List of architectural detail callouts.",
                    required=False,
                    items={"type": "string"},
                ),
                ToolParameter(
                    name="tips",
                    type="array",
                    description="List of pro detailing build tips.",
                    required=False,
                    items={"type": "string"},
                ),
            ],
        )

    def execute(
        self,
        filename: str,
        title: str,
        style: str = "clean",
        summary: str = "",
        hero_image_id: str | None = None,
        hero_image_caption: str = "",
        materials: list[list[Any]] | None = None,
        sections: list[dict[str, Any]] | None = None,
        callouts: list[str] | None = None,
        tips: list[str] | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        try:
            hero_image_bytes = None
            if hero_image_id:
                art = self.service.get_artifact(hero_image_id)
                if art and Path(art.path).exists():
                    with open(art.path, "rb") as f:
                        hero_image_bytes = f.read()

            raw_bytes = generate_pdf(
                title=title,
                sections=sections or [],
                style=style,
                summary=summary,
                hero_image_bytes=hero_image_bytes,
                hero_image_caption=hero_image_caption,
                materials=materials,
                callouts=callouts,
                tips=tips,
            )
            artifact = self.service.create_artifact(
                filename=filename,
                data=raw_bytes,
                mime_type="application/pdf",
                metadata={"title": title, "style": style, "sections_count": len(sections or [])},
            )
            return ToolResult(
                success=True,
                output=f"Created PDF document '{artifact.filename}' ({artifact.size_bytes} bytes, style: {style}) in artifacts folder.",
                tool_name=self.name,
                metadata=artifact.to_dict(),
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"Failed to create PDF document: {exc}",
                tool_name=self.name,
            )


class ArtifactCreateXlsxTool(BaseTool):
    """Creates a real Microsoft Excel spreadsheet (.xlsx) using OpenPyXL."""

    def __init__(self, service: ArtifactService) -> None:
        self.service = service
        super().__init__(
            name="artifact.create_xlsx",
            description="Create an Excel spreadsheet (.xlsx) workbook with styled header rows and multiple sheets.",
            permission=ToolPermission.CONFIRM,
            parameters=[
                ToolParameter(
                    name="filename",
                    type="string",
                    description="Name of the Excel file (e.g. 'budget.xlsx'). Must end with .xlsx.",
                    required=True,
                ),
                ToolParameter(
                    name="sheets",
                    type="array",
                    description="List of sheet objects: [{'name': 'Budget', 'columns': ['Item', 'Cost'], 'rows': [['Rent', 1200]]}]",
                    required=True,
                    items={"type": "object"},
                ),
            ],
        )

    def execute(
        self,
        filename: str,
        sheets: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        try:
            raw_bytes = generate_xlsx(sheets=sheets or [])
            artifact = self.service.create_artifact(
                filename=filename,
                data=raw_bytes,
                mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                metadata={"sheets_count": len(sheets or [])},
            )
            return ToolResult(
                success=True,
                output=f"Created Excel workbook '{artifact.filename}' ({artifact.size_bytes} bytes).",
                tool_name=self.name,
                metadata=artifact.to_dict(),
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"Failed to create Excel workbook: {exc}",
                tool_name=self.name,
            )


class ArtifactSaveImageTool(BaseTool):
    """Saves raw image bytes or base64 data into an artifact file."""

    def __init__(self, service: ArtifactService) -> None:
        self.service = service
        super().__init__(
            name="artifact.save_image",
            description="Save binary or base64 image data (.png, .jpg, .svg) into the Bimo artifacts directory.",
            permission=ToolPermission.CONFIRM,
            parameters=[
                ToolParameter(
                    name="filename",
                    type="string",
                    description="Filename for the image (e.g. 'diagram.png', 'photo.jpg').",
                    required=True,
                ),
                ToolParameter(
                    name="image_base64",
                    type="string",
                    description="Base64-encoded image bytes.",
                    required=True,
                ),
            ],
        )

    def execute(self, filename: str, image_base64: str = "", **kwargs: Any) -> ToolResult:
        try:
            raw_bytes = base64.b64decode(image_base64)
            ext = "." + filename.split(".")[-1].lower() if "." in filename else ".png"
            processed_bytes = validate_and_process_image(raw_bytes, expected_format=ext)
            artifact = self.service.create_artifact(
                filename=filename,
                data=processed_bytes,
                metadata={"type": "image"},
            )
            return ToolResult(
                success=True,
                output=f"Saved image artifact '{artifact.filename}' ({artifact.size_bytes} bytes).",
                tool_name=self.name,
                metadata=artifact.to_dict(),
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"Failed to save image artifact: {exc}",
                tool_name=self.name,
            )


class ArtifactListTool(BaseTool):
    """Lists all artifacts currently present in the Bimo artifact workspace."""

    def __init__(self, service: ArtifactService) -> None:
        self.service = service
        super().__init__(
            name="artifact.list",
            description="List all generated artifacts, files, and documents in the Bimo workspace.",
            permission=ToolPermission.SAFE,
            parameters=[],
        )

    def execute(self, **kwargs: Any) -> ToolResult:
        try:
            items = self.service.list_artifacts()
            return ToolResult(
                success=True,
                output={"count": len(items), "artifacts": [a.to_dict() for a in items]},
                tool_name=self.name,
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"Failed to list artifacts: {exc}",
                tool_name=self.name,
            )


class ArtifactGetInfoTool(BaseTool):
    """Retrieves metadata about a specific artifact by name or ID."""

    def __init__(self, service: ArtifactService) -> None:
        self.service = service
        super().__init__(
            name="artifact.get_info",
            description="Get metadata, file size, and creation timestamp for a specific artifact.",
            permission=ToolPermission.SAFE,
            parameters=[
                ToolParameter(
                    name="identifier",
                    type="string",
                    description="Filename (e.g. 'report.pdf') or artifact ID.",
                    required=True,
                )
            ],
        )

    def execute(self, identifier: str, **kwargs: Any) -> ToolResult:
        item = self.service.get_artifact(identifier)
        if not item:
            return ToolResult(
                success=False,
                error=f"Artifact '{identifier}' not found in artifacts workspace.",
                tool_name=self.name,
            )
        return ToolResult(
            success=True,
            output=item.to_dict(),
            tool_name=self.name,
            metadata=item.to_dict(),
        )


class ImageGenerateTool(BaseTool):
    """Invokes the configured ImageGenerationProvider to produce real AI images and save them as artifacts."""

    def __init__(
        self,
        service: ArtifactService,
        provider: ImageGenerationProvider,
    ) -> None:
        self.service = service
        self.provider = provider
        super().__init__(
            name="image.generate",
            description="Generate an image from a natural language prompt using the configured image generation model.",
            permission=ToolPermission.CONFIRM,
            parameters=[
                ToolParameter(
                    name="prompt",
                    type="string",
                    description="Detailed text description of the image to generate.",
                    required=True,
                ),
                ToolParameter(
                    name="filename",
                    type="string",
                    description="Desired filename to save the image (e.g. 'robot_mascot.png').",
                    required=False,
                ),
                ToolParameter(
                    name="size",
                    type="string",
                    description="Resolution of the generated image (e.g. '1024x1024', '512x512').",
                    required=False,
                ),
            ],
        )

    def execute(
        self,
        prompt: str,
        filename: str = "generated_image.png",
        size: str = "1024x1024",
        **kwargs: Any,
    ) -> ToolResult:
        try:
            if not self.provider.is_available():
                return ToolResult(
                    success=False,
                    error="AI image generation backend is not configured or reachable. You can use 'image.render_local' for charts and diagrams.",
                    tool_name=self.name,
                )

            gen_img = self.provider.generate(prompt=prompt, size=size)
            ext = gen_img.format.lower()
            if not filename.lower().endswith(f".{ext}"):
                filename = f"{filename}.{ext}"

            artifact = self.service.create_artifact(
                filename=filename,
                data=gen_img.data,
                mime_type=f"image/{ext}",
                metadata={
                    "prompt": prompt,
                    "provider": gen_img.provider_name,
                    "width": gen_img.width,
                    "height": gen_img.height,
                },
            )
            return ToolResult(
                success=True,
                output=f"Successfully generated image '{artifact.filename}' ({artifact.size_bytes} bytes).",
                tool_name=self.name,
                metadata=artifact.to_dict(),
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"Image generation failed: {exc}",
                tool_name=self.name,
            )


class ImageRenderLocalTool(BaseTool):
    """Locally renders charts, badges, and diagrams using Pillow without external network or AI dependencies."""

    def __init__(
        self,
        service: ArtifactService,
        renderer: DeterministicLocalImageRenderer,
    ) -> None:
        self.service = service
        self.renderer = renderer
        super().__init__(
            name="image.render_local",
            description="Render local visual graphics (charts, diagrams, status badges) 100% offline without remote AI models.",
            permission=ToolPermission.CONFIRM,
            parameters=[
                ToolParameter(
                    name="render_type",
                    type="string",
                    description="Type of graphic: 'bar_chart', 'badge', or 'diagram'.",
                    required=True,
                    enum_values=["bar_chart", "badge", "diagram"],
                ),
                ToolParameter(
                    name="filename",
                    type="string",
                    description="Target PNG filename (e.g. 'chart.png').",
                    required=True,
                ),
                ToolParameter(
                    name="title",
                    type="string",
                    description="Title heading of the graphic.",
                    required=True,
                ),
                ToolParameter(
                    name="labels",
                    type="array",
                    description="List of labels for bar_chart or diagram steps.",
                    required=False,
                    items={"type": "string"},
                ),
                ToolParameter(
                    name="values",
                    type="array",
                    description="List of numeric values corresponding to labels (for bar_chart).",
                    required=False,
                    items={"type": "number"},
                ),
                ToolParameter(
                    name="subtitle",
                    type="string",
                    description="Subtitle or status text for badge/card render.",
                    required=False,
                ),
            ],
        )

    def execute(
        self,
        render_type: str,
        filename: str,
        title: str,
        labels: list[str] | None = None,
        values: list[float] | None = None,
        subtitle: str = "",
        **kwargs: Any,
    ) -> ToolResult:
        try:
            r_type = render_type.lower().strip()
            if r_type == "bar_chart":
                clean_values = [float(v) for v in (values or [])]
                data = self.renderer.render_bar_chart(
                    title=title,
                    labels=labels or [],
                    values=clean_values,
                )
            elif r_type == "badge":
                data = self.renderer.render_badge(
                    title=title,
                    subtitle=subtitle,
                )
            elif r_type == "diagram":
                data = self.renderer.render_diagram(
                    title=title,
                    steps=labels or [],
                )
            else:
                return ToolResult(
                    success=False,
                    error=f"Unsupported render_type '{render_type}'. Choose from 'bar_chart', 'badge', 'diagram'.",
                    tool_name=self.name,
                )

            if not filename.lower().endswith(".png"):
                filename += ".png"

            artifact = self.service.create_artifact(
                filename=filename,
                data=data,
                mime_type="image/png",
                metadata={"render_type": r_type, "title": title},
            )
            return ToolResult(
                success=True,
                output=f"Rendered offline graphic '{artifact.filename}' ({artifact.size_bytes} bytes).",
                tool_name=self.name,
                metadata=artifact.to_dict(),
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"Local image rendering failed: {exc}",
                tool_name=self.name,
            )


class ArtifactPlanTool(BaseTool):
    """Analyzes a user request and formulates a structured visual-first artifact plan."""

    def __init__(self) -> None:
        super().__init__(
            name="artifact.plan",
            description="Analyze a user request and formulate an artifact plan (intent, style, image count, sections).",
            permission=ToolPermission.SAFE,
            parameters=[
                ToolParameter(
                    name="prompt",
                    type="string",
                    description="User request or topic to analyze and plan.",
                    required=True,
                ),
                ToolParameter(
                    name="intent",
                    type="string",
                    description="Optional intent override (e.g. 'VISUAL_GUIDE', 'STRUCTURED_DOCUMENT').",
                    required=False,
                    enum_values=[
                        "TEXT_DOCUMENT",
                        "STRUCTURED_DOCUMENT",
                        "VISUAL_GUIDE",
                        "PRESENTATION",
                        "INFOGRAPHIC",
                        "IMAGE",
                        "SPREADSHEET",
                        "DATA_EXPORT",
                    ],
                ),
            ],
        )

    def execute(self, prompt: str, intent: str | None = None, **kwargs: Any) -> ToolResult:
        try:
            resolved_intent = ArtifactIntent(intent) if intent else None
            plan = ArtifactPlanner.create_plan(prompt, resolved_intent)
            return ToolResult(
                success=True,
                output=f"Formulated {plan.intent.value} plan for '{plan.title}' ({plan.style} style, {plan.image_count} image required).",
                tool_name=self.name,
                metadata=plan.to_dict(),
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"Artifact planning failed: {exc}",
                tool_name=self.name,
            )


class ArtifactCreateVisualGuideTool(BaseTool):
    """Orchestrates creation of an illustrated visual guide (PDF) with hero image, materials, callouts, and steps."""

    def __init__(
        self,
        service: ArtifactService,
        image_provider: ImageGenerationProvider | None = None,
        local_renderer: DeterministicLocalImageRenderer | None = None,
    ) -> None:
        self.service = service
        self.image_provider = image_provider
        self.local_renderer = local_renderer or DeterministicLocalImageRenderer()
        super().__init__(
            name="artifact.create_visual_guide",
            description=(
                "Create a professional, visual-first PDF guide. Includes a hero image, concept summary, "
                "materials table, step-by-step instructions, architectural callout annotations, and pro tips. "
                "Use this tool whenever the user asks for a visual guide, graphical tutorial, or illustrated build."
            ),
            permission=ToolPermission.CONFIRM,
            parameters=[
                ToolParameter(
                    name="filename",
                    type="string",
                    description="Filename for the visual guide (e.g. 'minecraft_base_guide.pdf'). Must end with .pdf.",
                    required=True,
                ),
                ToolParameter(
                    name="title",
                    type="string",
                    description="Main title of the visual guide.",
                    required=True,
                ),
                ToolParameter(
                    name="style",
                    type="string",
                    description="Aesthetic visual theme.",
                    required=False,
                    enum_values=["minecraft", "modern", "clean", "tech", "medieval"],
                ),
                ToolParameter(
                    name="summary",
                    type="string",
                    description="Executive concept summary or design overview.",
                    required=False,
                ),
                ToolParameter(
                    name="hero_image_id",
                    type="string",
                    description="Artifact ID or filename of an existing image in the workspace to embed as hero visual.",
                    required=False,
                ),
                ToolParameter(
                    name="hero_image_prompt",
                    type="string",
                    description="Prompt to automatically generate the hero image if hero_image_id is not supplied.",
                    required=False,
                ),
                ToolParameter(
                    name="hero_image_caption",
                    type="string",
                    description="Caption displayed beneath the hero image.",
                    required=False,
                ),
                ToolParameter(
                    name="materials",
                    type="array",
                    description="List of materials rows: [['Material', 'Primary Role', 'Estimated Qty']].",
                    required=False,
                    items={"type": "array", "items": {"type": "string"}},
                ),
                ToolParameter(
                    name="sections",
                    type="array",
                    description="List of content sections with 'heading', 'paragraph', 'numbered' steps, or 'bullets'.",
                    required=False,
                    items={"type": "object"},
                ),
                ToolParameter(
                    name="callouts",
                    type="array",
                    description="List of architectural detail callouts highlighting key features.",
                    required=False,
                    items={"type": "string"},
                ),
                ToolParameter(
                    name="tips",
                    type="array",
                    description="List of pro detailing build tips.",
                    required=False,
                    items={"type": "string"},
                ),
            ],
        )

    def execute(
        self,
        filename: str,
        title: str,
        style: str = "minecraft",
        summary: str = "",
        hero_image_id: str | None = None,
        hero_image_prompt: str | None = None,
        hero_image_caption: str = "",
        materials: list[list[Any]] | None = None,
        sections: list[dict[str, Any]] | None = None,
        callouts: list[str] | None = None,
        tips: list[str] | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        try:
            hero_image_bytes = None
            generated_img_artifact = None

            # 1. Resolve existing hero image if ID provided
            if hero_image_id:
                art = self.service.get_artifact(hero_image_id)
                if art and Path(art.path).exists():
                    with open(art.path, "rb") as f:
                        hero_image_bytes = f.read()

            # 2. Or generate hero image on the fly if prompt provided
            if not hero_image_bytes and hero_image_prompt:
                if self.image_provider and self.image_provider.is_available():
                    try:
                        gen_img = self.image_provider.generate(hero_image_prompt, size="1024x1024", format="png")
                        hero_image_bytes = gen_img.data
                        stem = Path(filename).stem
                        generated_img_artifact = self.service.create_artifact(
                            filename=f"{stem}_hero.png",
                            data=hero_image_bytes,
                            mime_type="image/png",
                            metadata={"prompt": hero_image_prompt, "role": "hero_image"},
                        )
                    except Exception as gen_exc:
                        logger.warning("Hero image generation via provider failed: %s", gen_exc)

                # Fallback to local graphic if provider not available
                if not hero_image_bytes and self.local_renderer:
                    try:
                        hero_image_bytes = self.local_renderer.render_diagram(
                            title=title,
                            steps=["Layout Foundation", "Wall Columns", "Pitched Roof", "Exterior Detailing"],
                        )
                    except Exception:
                        pass

            # 3. If Minecraft build guide and components are missing, populate from plan
            if style == "minecraft" and (not materials or not sections):
                plan = ArtifactPlanner.create_plan(title, ArtifactIntent.VISUAL_GUIDE)
                materials = materials or [
                    ["Oak Logs", "Structural Frame & Pillars", "48"],
                    ["Dark Oak Planks", "Exterior Walls & Flooring", "64+"],
                    ["Spruce Stairs", "Steep Pitched Roof", "64+"],
                    ["Cobblestone", "Foundation & Fireplace", "32"],
                    ["Lanterns & Flowers", "Accents & Planter Boxes", "8"],
                ]
                summary = summary or plan.summary
                callouts = callouts or plan.callouts
                tips = tips or plan.tips
                sections = sections or plan.sections

            # 4. Compose visually rich PDF bytes
            raw_pdf = generate_pdf(
                title=title,
                style=style,
                summary=summary,
                hero_image_bytes=hero_image_bytes,
                hero_image_caption=hero_image_caption,
                materials=materials,
                sections=sections,
                callouts=callouts,
                tips=tips,
            )

            # 5. Persist final PDF artifact
            pdf_artifact = self.service.create_artifact(
                filename=filename,
                data=raw_pdf,
                mime_type="application/pdf",
                metadata={
                    "title": title,
                    "style": style,
                    "has_hero_image": hero_image_bytes is not None,
                    "hero_image_artifact": generated_img_artifact.id if generated_img_artifact else hero_image_id,
                },
            )

            msg = f"Created visual guide '{pdf_artifact.filename}' ({pdf_artifact.size_bytes} bytes, style: {style})."
            if generated_img_artifact:
                msg += f" Hero image generated: '{generated_img_artifact.filename}'."

            meta = pdf_artifact.to_dict()
            if generated_img_artifact:
                meta["hero_image"] = generated_img_artifact.to_dict()

            return ToolResult(
                success=True,
                output=msg,
                tool_name=self.name,
                metadata=meta,
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"Failed to create visual guide: {exc}",
                tool_name=self.name,
            )


def register_artifact_tools(
    registry: ToolRegistry,
    artifact_service: ArtifactService,
    image_provider: ImageGenerationProvider | None = None,
    local_renderer: DeterministicLocalImageRenderer | None = None,
) -> None:
    """Populates a ToolRegistry with all Phase 9 Artifact & Image generation tools."""
    renderer = local_renderer or DeterministicLocalImageRenderer()
    registry.register(ArtifactPlanTool())
    registry.register(ArtifactCreateTextTool(artifact_service))
    registry.register(ArtifactCreateMarkdownTool(artifact_service))
    registry.register(ArtifactCreateJsonTool(artifact_service))
    registry.register(ArtifactCreateCsvTool(artifact_service))
    registry.register(ArtifactCreateDocxTool(artifact_service))
    registry.register(ArtifactCreatePdfTool(artifact_service))
    registry.register(
        ArtifactCreateVisualGuideTool(
            service=artifact_service,
            image_provider=image_provider,
            local_renderer=renderer,
        )
    )
    registry.register(ArtifactCreateXlsxTool(artifact_service))
    registry.register(ArtifactSaveImageTool(artifact_service))
    registry.register(ArtifactListTool(artifact_service))
    registry.register(ArtifactGetInfoTool(artifact_service))

    if image_provider is not None:
        registry.register(ImageGenerateTool(artifact_service, image_provider))

    registry.register(ImageRenderLocalTool(artifact_service, renderer))
    logger.info("Registered Artifact and Image tools into ToolRegistry.")
