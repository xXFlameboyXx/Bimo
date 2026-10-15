"""Artifact Intent and Visual-First Planning Layer for Bimo.

Provides:
- Intent classification: TEXT_DOCUMENT, STRUCTURED_DOCUMENT, VISUAL_GUIDE, PRESENTATION, INFOGRAPHIC, IMAGE, SPREADSHEET, DATA_EXPORT
- Visual composition planning (hero images, layout, sections, typography style)
- Bounded image count heuristics (default 1 hero image)
- Structured image prompt synthesis from architectural concepts
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


class ArtifactIntent(str, Enum):
    """Categorization of user artifact requests."""

    TEXT_DOCUMENT = "TEXT_DOCUMENT"
    STRUCTURED_DOCUMENT = "STRUCTURED_DOCUMENT"
    VISUAL_GUIDE = "VISUAL_GUIDE"
    PRESENTATION = "PRESENTATION"
    INFOGRAPHIC = "INFOGRAPHIC"
    IMAGE = "IMAGE"
    SPREADSHEET = "SPREADSHEET"
    DATA_EXPORT = "DATA_EXPORT"


@dataclass(frozen=True)
class VisualAssetPlan:
    """Specification of an image or visual asset required for the document."""

    asset_type: str  # "hero_image", "diagram", "chart", "illustration"
    purpose: str
    prompt: str
    aspect_ratio: str = "16:9"
    count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ArtifactPlan:
    """Structured architectural plan for generating a user artifact."""

    intent: ArtifactIntent
    artifact_type: str  # "pdf", "docx", "xlsx", "png", "txt", "md"
    style: str  # "modern", "minecraft", "clean", "tech", "medieval", "fantasy", "minimal", "professional"
    title: str
    summary: str
    image_count: int = 0
    visual_assets: list[VisualAssetPlan] = field(default_factory=list)
    sections: list[dict[str, Any]] = field(default_factory=list)
    callouts: list[str] = field(default_factory=list)
    tips: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.value,
            "artifact_type": self.artifact_type,
            "style": self.style,
            "title": self.title,
            "summary": self.summary,
            "image_count": self.image_count,
            "visual_assets": [v.to_dict() for v in self.visual_assets],
            "sections": list(self.sections),
            "callouts": list(self.callouts),
            "tips": list(self.tips),
        }


# Keywords that signal specific intents
VISUAL_GUIDE_KEYWORDS = (
    "visual example",
    "graphical example",
    "graphical guide",
    "visual guide",
    "illustrated guide",
    "visual tutorial",
    "visual walkthrough",
    "graphical tutorial",
    "visual build",
    "visual plan",
    "showcase",
    "design concept",
    "look like",
    "graphic guide",
)

PRESENTATION_KEYWORDS = (
    "presentation",
    "pitch deck",
    "slides",
    "slide deck",
)

INFOGRAPHIC_KEYWORDS = (
    "infographic",
    "diagram",
    "flowchart",
    "architecture diagram",
    "system diagram",
    "poster",
)

IMAGE_ONLY_KEYWORDS = (
    "create an image",
    "generate an image",
    "make an image",
    "draw an image",
    "picture of",
    "photo of",
    "illustration of",
    "logo of",
    "render of",
)

SPREADSHEET_KEYWORDS = (
    "excel",
    "spreadsheet",
    "sheet",
    "workbook",
    "csv",
    ".xlsx",
    "budget table",
    "tabular tracker",
)

DATA_KEYWORDS = (
    "json",
    "data export",
    "export data",
    "raw data",
)

TEXT_DOC_KEYWORDS = (
    "plain text",
    "notes",
    ".txt",
    "quick text",
)


class ArtifactPlanner:
    """Plans artifact composition based on user intent and visual-first principles."""

    @classmethod
    def detect_intent(cls, prompt: str) -> ArtifactIntent:
        """Deterministically determine the artifact intent from the user's prompt."""
        p_lower = prompt.lower().strip()

        # Check pure image requests first if prompt specifically asks for an image/picture only
        if any(k in p_lower for k in IMAGE_ONLY_KEYWORDS) and not any(
            v in p_lower for v in ("pdf", "guide", "document", "booklet", "walkthrough", "tutorial")
        ):
            return ArtifactIntent.IMAGE

        # Check spreadsheet / data
        if any(k in p_lower for k in SPREADSHEET_KEYWORDS):
            return ArtifactIntent.SPREADSHEET

        if any(k in p_lower for k in DATA_KEYWORDS):
            return ArtifactIntent.DATA_EXPORT

        # Check visual guide keywords
        if any(k in p_lower for k in VISUAL_GUIDE_KEYWORDS):
            return ArtifactIntent.VISUAL_GUIDE

        # Check presentation
        if any(k in p_lower for k in PRESENTATION_KEYWORDS):
            return ArtifactIntent.PRESENTATION

        # Check infographic
        if any(k in p_lower for k in INFOGRAPHIC_KEYWORDS):
            return ArtifactIntent.INFOGRAPHIC

        # Check plain text
        if any(k in p_lower for k in TEXT_DOC_KEYWORDS):
            return ArtifactIntent.TEXT_DOCUMENT

        # General report / document
        if any(k in p_lower for k in ("pdf", "docx", "report", "document", "specification", "paper")):
            # Check if user mentioned "graphical" or "visual" anywhere in request
            if "visual" in p_lower or "graphical" in p_lower or "illustrated" in p_lower:
                return ArtifactIntent.VISUAL_GUIDE
            return ArtifactIntent.STRUCTURED_DOCUMENT

        return ArtifactIntent.STRUCTURED_DOCUMENT

    @classmethod
    def detect_style(cls, prompt: str) -> str:
        """Infer an appropriate aesthetic style from the subject matter."""
        p_lower = prompt.lower()
        if "minecraft" in p_lower or "voxel" in p_lower:
            return "minecraft"
        if any(k in p_lower for k in ("medieval", "castle", "knight", "tavern", "fantasy")):
            return "medieval"
        if any(k in p_lower for k in ("tech", "robot", "cyber", "ai", "hardware", "software", "system")):
            return "tech"
        if any(k in p_lower for k in ("minimal", "clean", "simple")):
            return "minimal"
        return "modern"

    @classmethod
    def determine_image_count(cls, prompt: str, intent: ArtifactIntent) -> int:
        """Determine bounded image count.

        Default rule:
        - Exactly 1 strong hero image for visual guides, infographics, or presentations.
        - Exactly 1 image for image intent.
        - 0 images for text/data/spreadsheet.
        - Multiple images only if user explicitly asked for 'multiple', 'several', 'each step', or 'step-by-step illustrations'.
        """
        if intent in (ArtifactIntent.TEXT_DOCUMENT, ArtifactIntent.SPREADSHEET, ArtifactIntent.DATA_EXPORT):
            return 0

        p_lower = prompt.lower()

        # Check explicit request for single image
        if any(k in p_lower for k in ("one image", "1 image", "single image", "one beautiful", "a hero image")):
            return 1

        # Check explicit request for multiple images
        if any(k in p_lower for k in ("step-by-step images", "step-by-step illustrations", "multiple images", "series of images")):
            return 3

        # Default for visual guides & presentations: 1 strong hero image
        if intent in (ArtifactIntent.VISUAL_GUIDE, ArtifactIntent.PRESENTATION, ArtifactIntent.INFOGRAPHIC, ArtifactIntent.IMAGE):
            return 1

        return 0

    @classmethod
    def construct_image_prompt(
        cls,
        subject: str,
        style: str,
        materials: list[str] | None = None,
        features: list[str] | None = None,
        composition: str = "Isometric 3D perspective, three-quarter exterior view, centered, dramatic warm lighting, clean background, highly detailed 8k render",
    ) -> str:
        """Synthesize a high-quality, document-serving image generation prompt."""
        parts = [f"Subject: {subject}"]

        if style.lower() == "minecraft":
            parts.append("Style: Authentic Minecraft voxel architecture, cinematic shader lighting, raytraced ambient occlusion, vibrant textures")
        elif style.lower() in ("medieval", "fantasy"):
            parts.append("Style: Stylized medieval fantasy architecture, warm glowing lanterns, hand-crafted timber and stonework")
        elif style.lower() == "tech":
            parts.append("Style: Sleek high-tech modern industrial design, clean bevels, illuminated accents, technical schematic aesthetic")
        else:
            parts.append(f"Style: Polished {style} aesthetic, high aesthetic quality, clean lighting")

        if materials:
            parts.append(f"Materials & Palette: {', '.join(materials)}")

        if features:
            parts.append(f"Key Architectural Features: {', '.join(features)}")

        parts.append(f"Composition: {composition}")

        return ". ".join(parts)

    @classmethod
    def create_plan(
        cls,
        prompt: str,
        intent: ArtifactIntent | None = None,
    ) -> ArtifactPlan:
        """Generate a complete structured plan for producing a professional artifact."""
        resolved_intent = intent or cls.detect_intent(prompt)
        style = cls.detect_style(prompt)
        image_count = cls.determine_image_count(prompt, resolved_intent)

        p_lower = prompt.lower()

        # Check if subject is Minecraft Base
        if "minecraft" in p_lower and any(k in p_lower for k in ("base", "cottage", "house", "build", "home")):
            title = "Cozy Woodland Cottage — Minecraft Build Guide"
            summary = "A charming, survival-friendly woodland cottage featuring rich natural textures, steep pitched rooflines, and inviting lantern ambiance."
            materials = [
                ["Oak Logs", "Frame & Structural Columns", "32-48"],
                ["Dark Oak Planks", "Exterior Walls & Flooring", "64+"],
                ["Spruce Stairs & Slabs", "Steep Pitched Roofline", "64+"],
                ["Cobblestone", "Foundation, Fireplace & Chimney", "32"],
                ["Glass Panes", "Window Glazing", "16"],
                ["Lanterns & Flowers", "Exterior Accents & Planter Boxes", "8"],
            ]
            features = [
                "Steep A-frame pitched spruce roof with overhang",
                "Warm glowing lanterns suspended under eaves",
                "Exterior flower boxes with blooming poppies",
                "Cobblestone chimney with campfire smoke",
                "Rich woodland forest backdrop",
            ]
            callouts = [
                "Steep A-frame roof creates the iconic cozy silhouette",
                "Cobblestone foundation prevents flat wall monotony",
                "Suspended lanterns provide warm, spawn-proof illumination",
                "Flower planter boxes add organic color to the timber facade",
            ]
            tips = [
                "Strip alternating oak logs to add subtle weathered texture.",
                "Place a campfire inside the chimney top with hay beneath for continuous thick smoke.",
                "Mix cobblestone with mossy cobblestone and stone bricks near the foundation for organic depth.",
                "Use spruce trapdoors as decorative window shutters.",
            ]
            sections = [
                {
                    "heading": "1. Architectural Concept",
                    "level": 1,
                    "paragraph": "This build balances compact survival utility with warm rustic aesthetics. The foundation anchors into terrain using cobblestone, transitioning into warm timber walls capped by an aggressive pitch roof that sheds snow and rain.",
                },
                {
                    "heading": "2. Required Materials",
                    "level": 2,
                    "table": {
                        "headers": ["Material", "Primary Use", "Estimated Quantity"],
                        "rows": materials,
                    },
                },
                {
                    "heading": "3. Step-by-Step Build Walkthrough",
                    "level": 1,
                    "numbered": [
                        "Lay a 7x9 cobblestone foundation outline with a 1-block recessed entrance.",
                        "Erect 4-block high stripped oak log pillars at each corner, connected by horizontal log beams.",
                        "Fill exterior wall panels with dark oak planks, leaving 1x2 and 2x2 openings for glass pane windows.",
                        "Construct the steep pitched spruce roof using stairs and slabs, extending a 1-block overhang past the front and back gables.",
                        "Build the stone chimney along the north wall, integrating a campfire at the crown for authentic chimney smoke.",
                        "Add detailing: install spruce trapdoor shutters, hanging lanterns, and flower boxes with poppies under every window.",
                    ],
                },
            ]

            visual_assets = []
            if image_count > 0:
                img_prompt = cls.construct_image_prompt(
                    subject="Cozy woodland Minecraft cottage",
                    style="minecraft",
                    materials=["Oak logs", "Dark oak planks", "Spruce stairs", "Cobblestone"],
                    features=features,
                    composition="Three-quarter isometric exterior view, cottage centered in an autumn spruce forest, blooming flower boxes under windows, warm lantern glow on timber walls, smoking cobblestone chimney, raytraced Minecraft shader aesthetic",
                )
                visual_assets.append(
                    VisualAssetPlan(
                        asset_type="hero_image",
                        purpose="Hero visual showing finished cottage exterior and architectural silhouette",
                        prompt=img_prompt,
                        aspect_ratio="16:9",
                        count=1,
                    )
                )

            return ArtifactPlan(
                intent=resolved_intent,
                artifact_type="pdf",
                style="minecraft",
                title=title,
                summary=summary,
                image_count=image_count,
                visual_assets=visual_assets,
                sections=sections,
                callouts=callouts,
                tips=tips,
            )

        # Default generic visual / document plan
        clean_title = re.sub(r'^(create|make|write|generate|give me|a|an)\s+', '', prompt, flags=re.IGNORECASE).strip().title()
        if len(clean_title) > 50:
            clean_title = clean_title[:47] + "..."

        visual_assets = []
        if image_count > 0:
            img_prompt = cls.construct_image_prompt(
                subject=clean_title,
                style=style,
            )
            visual_assets.append(
                VisualAssetPlan(
                    asset_type="hero_image",
                    purpose="Hero visual illustration representing the core concept",
                    prompt=img_prompt,
                    aspect_ratio="16:9",
                    count=1,
                )
            )

        return ArtifactPlan(
            intent=resolved_intent,
            artifact_type="pdf",
            style=style,
            title=clean_title or "Bimo Generated Guide",
            summary=f"Visual guide and documentation for {clean_title or prompt}.",
            image_count=image_count,
            visual_assets=visual_assets,
            sections=[
                {
                    "heading": "Overview",
                    "level": 1,
                    "paragraph": f"Overview and key specifications for {clean_title or prompt}.",
                }
            ],
            callouts=[],
            tips=[],
        )
