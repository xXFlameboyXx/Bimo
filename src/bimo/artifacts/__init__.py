"""Bimo Artifact and Document Generation Subsystem.

Provides safe, boundary-enforced artifact creation (PDF, DOCX, XLSX, JSON, CSV, Text, Markdown, Image)
with atomic generation, strict directory containment, non-destructive file naming, and metadata tracking.
"""

from __future__ import annotations

from bimo.artifacts.composer import VisualDocumentComposer
from bimo.artifacts.models import Artifact
from bimo.artifacts.planner import ArtifactIntent, ArtifactPlan, ArtifactPlanner
from bimo.artifacts.service import ArtifactService

__all__ = [
    "Artifact",
    "ArtifactIntent",
    "ArtifactPlan",
    "ArtifactPlanner",
    "ArtifactService",
    "VisualDocumentComposer",
]
