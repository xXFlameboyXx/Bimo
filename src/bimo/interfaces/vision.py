"""Vision Abstraction and Safety Layer for Bimo.

Provides:
- VisionProvider: Abstract interface for multi-modal visual analysis.
- VisionRequest & VisionResult: Structured query and bounded interpretation.
- VisionSafetyValidator: Validates detected UI coordinates against screen dimensions and target window bounds.
- Prevents blind execution of low-confidence model coordinates.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MIN_CLICK_CONFIDENCE = 0.70


class VisionConfidence(str, Enum):
    """Confidence level of vision identification."""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNCERTAIN = "UNCERTAIN"


@dataclass
class UIElementLocation:
    """Bounded coordinates of an identified UI control on screen."""
    element_type: str  # "button", "input", "link", "text", "icon"
    label: str
    x: int
    y: int
    width: int
    height: int
    confidence: float

    @property
    def center(self) -> Tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "element_type": self.element_type,
            "label": self.label,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "confidence": self.confidence,
            "center": list(self.center),
        }


@dataclass
class VisionRequest:
    """Visual query on an image artifact or screenshot."""
    image_bytes: bytes
    prompt: str
    target_element: Optional[str] = None
    screen_size: Tuple[int, int] = (1920, 1080)
    target_window_bounds: Optional[Tuple[int, int, int, int]] = None  # (left, top, right, bottom)


@dataclass
class VisionResult:
    """Structured interpretation of visual content."""
    description: str
    identified_elements: List[UIElementLocation] = field(default_factory=list)
    confidence: VisionConfidence = VisionConfidence.UNCERTAIN
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "description": self.description,
            "identified_elements": [e.to_dict() for e in self.identified_elements],
            "confidence": self.confidence.value,
            "error_message": self.error_message,
        }


class VisionProvider(abc.ABC):
    """Interface for multi-modal vision models."""

    @abc.abstractmethod
    def analyze_image(self, request: VisionRequest) -> VisionResult:
        """Analyze image bytes and return structured interpretation."""
        pass


class MockVisionProvider(VisionProvider):
    """Deterministic mock provider for testing vision integration."""

    def __init__(self, default_elements: Optional[List[UIElementLocation]] = None) -> None:
        self.default_elements = default_elements or []

    def analyze_image(self, request: VisionRequest) -> VisionResult:
        if not request.image_bytes:
            return VisionResult(
                description="Empty image provided.",
                confidence=VisionConfidence.UNCERTAIN,
                error_message="Image bytes are empty.",
            )

        elements = list(self.default_elements)
        if request.target_element and not elements:
            # Generate simulated mock match
            elements.append(
                UIElementLocation(
                    element_type="button",
                    label=request.target_element,
                    x=500,
                    y=300,
                    width=120,
                    height=40,
                    confidence=0.92,
                )
            )

        return VisionResult(
            description=f"Analyzed visual content for: {request.prompt}",
            identified_elements=elements,
            confidence=VisionConfidence.HIGH if elements else VisionConfidence.MEDIUM,
        )


class VisionSafetyValidator:
    """Validates vision-derived interaction coordinates before executing input."""

    @classmethod
    def validate_click_target(
        cls,
        element: UIElementLocation,
        screen_size: Tuple[int, int],
        target_window_bounds: Optional[Tuple[int, int, int, int]] = None,
        min_confidence: float = MIN_CLICK_CONFIDENCE,
    ) -> Tuple[bool, Optional[str]]:
        """Verify element coordinates are inside bounds and meet confidence threshold."""
        # 1. Confidence check
        if element.confidence < min_confidence:
            return False, f"Vision confidence ({element.confidence:.2f}) is below safe threshold ({min_confidence:.2f})."

        cx, cy = element.center
        sw, sh = screen_size

        # 2. Screen bounds check
        if cx < 0 or cx >= sw or cy < 0 or cy >= sh:
            return False, f"Coordinates ({cx}, {cy}) are outside screen bounds ({sw}x{sh})."

        # 3. Target window containment check
        if target_window_bounds:
            left, top, right, bottom = target_window_bounds
            if cx < left or cx > right or cy < top or cy > bottom:
                return False, f"Coordinates ({cx}, {cy}) fall outside target window bounds ({left}, {top}, {right}, {bottom})."

        return True, None
