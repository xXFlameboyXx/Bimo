"""Central Artifact Service for Bimo.

Enforces:
- Strict workspace boundary containment (anti-traversal, no arbitrary filesystem access)
- Supported file extension allowlist
- Configurable file size limits
- Non-destructive naming (prevents silent overwrite)
- Atomic creation via secure temporary staging
- Read-only artifact catalog inspection
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
from pathlib import Path
import re
import threading
import time
import uuid
from typing import Any

from bimo.artifacts.models import Artifact

logger = logging.getLogger(__name__)

# Supported artifact extensions mapped to default MIME types
SUPPORTED_EXTENSIONS: dict[str, str] = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".json": "application/json",
    ".csv": "text/csv",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
}

# Regex to detect prohibited filename characters across OS platforms
INVALID_FILENAME_CHARS = re.compile(r'[\0<>:"/\\|?*]')


class ArtifactSecurityError(ValueError):
    """Raised when an artifact filename or path violates security constraints."""
    pass


class ArtifactSizeLimitError(ValueError):
    """Raised when an artifact payload exceeds configured size bounds."""
    pass


class ArtifactService:
    """Manages creation, resolution, containment, and tracking of Bimo artifacts."""

    def __init__(
        self,
        workspace_dir: str | Path = "artifacts",
        max_artifact_size_mb: int = 25,
        max_text_size_mb: int = 5,
        max_image_size_mb: int = 15,
    ) -> None:
        self.workspace_dir = Path(workspace_dir).resolve()
        self.temp_dir = (self.workspace_dir / ".tmp").resolve()
        self.max_artifact_size_bytes = max_artifact_size_mb * 1024 * 1024
        self.max_text_size_bytes = max_text_size_mb * 1024 * 1024
        self.max_image_size_bytes = max_image_size_mb * 1024 * 1024
        self._lock = threading.RLock()

        self._ensure_directories()

    def _ensure_directories(self) -> None:
        """Create workspace and staging directories safely."""
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def validate_filename(self, filename: str) -> str:
        """Validate and sanitize filename, enforcing containment and extension allowlist.

        Rejects:
        - Path traversal sequences ('..', '/', '\\')
        - Windows drive letters ('C:', 'D:')
        - UNC network prefixes ('\\\\')
        - Null bytes or control characters
        - Unsupported extensions
        - Hidden files
        """
        if not filename or not isinstance(filename, str):
            raise ArtifactSecurityError("Filename must be a non-empty string.")

        cleaned = filename.strip().replace("\r", "").replace("\n", "")

        # Reject path separators and traversal attempts immediately
        if "/" in cleaned or "\\" in cleaned:
            raise ArtifactSecurityError(
                f"Path traversal or directory separators are prohibited in filename: '{filename}'"
            )

        if ".." in cleaned:
            raise ArtifactSecurityError(
                f"Path traversal sequence '..' detected in filename: '{filename}'"
            )

        if INVALID_FILENAME_CHARS.search(cleaned):
            raise ArtifactSecurityError(
                f"Prohibited characters detected in filename: '{filename}'"
            )

        # Disallow hidden files
        if cleaned.startswith("."):
            raise ArtifactSecurityError(
                f"Hidden or dotfiles are prohibited as final artifacts: '{filename}'"
            )

        # Check extension against supported list
        ext = Path(cleaned).suffix.lower()
        if not ext or ext not in SUPPORTED_EXTENSIONS:
            supported = ", ".join(sorted(SUPPORTED_EXTENSIONS.keys()))
            raise ArtifactSecurityError(
                f"Unsupported artifact extension '{ext}'. Supported formats: {supported}"
            )

        # Resolve against workspace and double-check containment
        target_path = (self.workspace_dir / cleaned).resolve()
        try:
            target_path.relative_to(self.workspace_dir)
        except ValueError as exc:
            raise ArtifactSecurityError(
                f"Target path escapes artifact workspace root: '{filename}'"
            ) from exc

        return cleaned

    def get_size_limit_for_extension(self, ext: str) -> int:
        """Return the maximum allowed byte limit for a given file extension."""
        ext_lower = ext.lower()
        if ext_lower in (".txt", ".md", ".json", ".csv"):
            return self.max_text_size_bytes
        if ext_lower in (".png", ".jpg", ".jpeg", ".svg"):
            return self.max_image_size_bytes
        return self.max_artifact_size_bytes

    def resolve_unique_filename(self, filename: str, allow_overwrite: bool = False) -> str:
        """Return a non-colliding filename if the target already exists.

        Example:
        'report.pdf' -> 'report (1).pdf' -> 'report (2).pdf'
        """
        clean_name = self.validate_filename(filename)
        if allow_overwrite:
            return clean_name

        dest = self.workspace_dir / clean_name
        if not dest.exists():
            return clean_name

        stem = dest.stem
        ext = dest.suffix
        counter = 1
        while True:
            candidate = f"{stem} ({counter}){ext}"
            cand_path = self.workspace_dir / candidate
            if not cand_path.exists():
                return candidate
            counter += 1

    def create_artifact(
        self,
        filename: str,
        data: bytes,
        mime_type: str | None = None,
        metadata: dict[str, Any] | None = None,
        allow_overwrite: bool = False,
    ) -> Artifact:
        """Atomically create and record a new artifact in the workspace.

        1. Validates filename and containment.
        2. Resolves unique non-destructive name.
        3. Enforces payload byte limits.
        4. Writes atomically via temporary staging file.
        5. Returns structured Artifact object.
        """
        if not isinstance(data, (bytes, bytearray)):
            raise ValueError("Artifact payload data must be bytes.")

        with self._lock:
            self._ensure_directories()
            target_filename = self.resolve_unique_filename(
                filename, allow_overwrite=allow_overwrite
            )
            ext = Path(target_filename).suffix.lower()
            limit = self.get_size_limit_for_extension(ext)

            size_bytes = len(data)
            if size_bytes > limit:
                limit_mb = limit / (1024 * 1024)
                actual_mb = size_bytes / (1024 * 1024)
                raise ArtifactSizeLimitError(
                    f"Artifact '{target_filename}' size ({actual_mb:.2f} MB) exceeds limit ({limit_mb:.1f} MB)."
                )

            # Atomic write via staging file
            temp_filename = f"{uuid.uuid4().hex}_{target_filename}.tmp"
            temp_path = self.temp_dir / temp_filename
            final_path = self.workspace_dir / target_filename

            try:
                with open(temp_path, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())

                # Atomic replacement
                os.replace(temp_path, final_path)
            except Exception as exc:
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except Exception:
                        pass
                logger.error("Failed to materialize artifact '%s': %s", target_filename, exc)
                raise RuntimeError(f"Failed to create artifact '{target_filename}': {exc}") from exc

            inferred_mime = mime_type or SUPPORTED_EXTENSIONS.get(ext) or "application/octet-stream"
            artifact_id = f"artifact-{hashlib.sha256(target_filename.encode('utf-8')).hexdigest()[:12]}"
            full_path = str(final_path)

            art = Artifact(
                id=artifact_id,
                filename=target_filename,
                mime_type=inferred_mime,
                size_bytes=size_bytes,
                path=full_path,
                created_at=time.time(),
                metadata=metadata or {},
            )
            logger.info(
                "Artifact created: '%s' (%d bytes, ID: %s)",
                target_filename,
                size_bytes,
                artifact_id,
            )
            return art

    def get_artifact(self, identifier: str) -> Artifact | None:
        """Find an existing artifact by exact filename or ID."""
        clean = identifier.strip()
        with self._lock:
            for item in self.list_artifacts():
                if item.id == clean or item.filename.lower() == clean.lower():
                    return item
        return None

    def list_artifacts(self) -> list[Artifact]:
        """List all materialized artifacts in the workspace sorted by creation time."""
        with self._lock:
            self._ensure_directories()
            artifacts: list[Artifact] = []
            for entry in self.workspace_dir.iterdir():
                # Skip directories and staging directory
                if not entry.is_file() or entry.name.startswith("."):
                    continue
                ext = entry.suffix.lower()
                if ext not in SUPPORTED_EXTENSIONS:
                    continue

                try:
                    stat = entry.stat()
                    # Generate deterministic ID from filename
                    art_id = f"artifact-{hashlib.sha256(entry.name.encode('utf-8')).hexdigest()[:12]}"
                    mime = SUPPORTED_EXTENSIONS.get(ext, "application/octet-stream")
                    artifacts.append(
                        Artifact(
                            id=art_id,
                            filename=entry.name,
                            mime_type=mime,
                            size_bytes=stat.st_size,
                            path=str(entry.resolve()),
                            created_at=stat.st_mtime,
                        )
                    )
                except Exception:
                    pass

            artifacts.sort(key=lambda a: a.created_at, reverse=True)
            return artifacts

    def get_artifact_info(self, identifier: str) -> dict[str, Any] | None:
        """Retrieve detailed information about an artifact by ID or filename."""
        art = self.get_artifact(identifier)
        if not art:
            return None
        return art.to_dict()

    def duplicate_artifact(self, identifier: str, new_filename: str | None = None) -> Artifact:
        """Duplicate an existing artifact safely within the workspace."""
        art = self.get_artifact(identifier)
        if not art:
            raise ValueError(f"Artifact '{identifier}' does not exist.")

        with open(art.path, "rb") as f:
            data = f.read()

        if not new_filename:
            base, ext = os.path.splitext(art.filename)
            target_name = f"{base}_copy{ext}"
        else:
            target_name = new_filename

        return self.create_artifact(
            filename=target_name,
            data=data,
            mime_type=art.mime_type,
            metadata={"parent_artifact_id": art.id, "origin": "duplicate"},
        )

    def cleanup_temp(self) -> int:
        """Purge orphaned staging files in .tmp/ directory."""
        with self._lock:
            count = 0
            if self.temp_dir.exists():
                for f in self.temp_dir.glob("*.tmp"):
                    try:
                        f.unlink()
                        count += 1
                    except Exception:
                        pass
            return count
