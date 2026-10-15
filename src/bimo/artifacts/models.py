"""Data models for Bimo artifacts and file generation."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Artifact:
    """Represents a validated, generated file artifact within the Bimo workspace."""

    id: str
    filename: str
    mime_type: str
    size_bytes: int
    path: str  # Relative path from artifact workspace root
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert artifact metadata to clean dictionary representation."""
        return {
            "id": self.id,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "path": self.path,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }
