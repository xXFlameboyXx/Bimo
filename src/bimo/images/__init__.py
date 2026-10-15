"""Bimo Image Generation and Local Rendering Subsystem.

Provides:
- ImageGenerationProvider abstraction (remote/OmniRoute, local fallback, mock)
- DeterministicLocalImageRenderer for offline charts, badges, diagrams, and posters
"""

from __future__ import annotations

from bimo.images.local_renderer import DeterministicLocalImageRenderer
from bimo.images.provider import (
    GeneratedImage,
    ImageGenerationProvider,
    MockImageProvider,
    OmniRouteImageProvider,
    UnavailableImageProvider,
)

__all__ = [
    "GeneratedImage",
    "ImageGenerationProvider",
    "OmniRouteImageProvider",
    "UnavailableImageProvider",
    "MockImageProvider",
    "DeterministicLocalImageRenderer",
]
