"""Explicit, bounded, and inspectable Memory subsystem for Bimo.

Provides:
- MemoryType: PREFERENCE, FACT, TASK_HISTORY, ARTIFACT_REFERENCE, DEVICE_PREFERENCE, APPLICATION_PREFERENCE
- MemoryItem: Bounded memory item with explicit category, content, and relevance tags.
- MemoryStore: Stores, searches, retrieves relevant memories, and strictly protects sensitive credentials/secrets.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional
import uuid

logger = logging.getLogger(__name__)

MAX_MEMORY_ITEMS = 100
SENSITIVE_PATTERNS = [
    r"password",
    r"api[_-]?key",
    r"secret",
    r"token",
    r"bearer\s+[a-zA-Z0-9_\-\.]+",
    r"credentials?",
    r"auth[_-]?code",
    r"private[_-]?key",
]


class MemoryType(str, Enum):
    """Categorization of explicitly preserved user memory."""
    PREFERENCE = "PREFERENCE"
    FACT = "FACT"
    TASK_HISTORY = "TASK_HISTORY"
    ARTIFACT_REFERENCE = "ARTIFACT_REFERENCE"
    DEVICE_PREFERENCE = "DEVICE_PREFERENCE"
    APPLICATION_PREFERENCE = "APPLICATION_PREFERENCE"


@dataclass
class MemoryItem:
    """An explicit, inspectable item of user knowledge or preference."""
    id: str
    memory_type: MemoryType
    key: str
    content: str
    tags: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "memory_type": self.memory_type.value,
            "key": self.key,
            "content": self.content,
            "tags": list(self.tags),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class MemoryStore:
    """Manages inspectable, bounded memory with strict secrets rejection."""

    def __init__(self, persistence_file: Optional[Path | str] = None, max_items: int = MAX_MEMORY_ITEMS) -> None:
        self.persistence_file = Path(persistence_file) if persistence_file else None
        self.max_items = max_items
        self._items: Dict[str, MemoryItem] = {}
        self._load()

    def _is_sensitive(self, text: str) -> bool:
        """Check if text matches sensitive credential patterns."""
        for pat in SENSITIVE_PATTERNS:
            if re.search(pat, text, re.IGNORECASE):
                return True
        return False

    def store(
        self,
        memory_type: MemoryType,
        key: str,
        content: str,
        tags: Optional[List[str]] = None,
    ) -> MemoryItem:
        """Store or update a memory item. Strictly rejects sensitive credentials."""
        combined = f"{key} {content} {' '.join(tags or [])}"
        if self._is_sensitive(combined):
            raise ValueError("Prohibited: Memory cannot store passwords, API keys, tokens, or credentials.")

        clean_key = key.strip().lower()

        # Check existing by key and type
        existing_id = None
        for item in self._items.values():
            if item.key.lower() == clean_key and item.memory_type == memory_type:
                existing_id = item.id
                break

        now = time.time()
        if existing_id:
            item = self._items[existing_id]
            item.content = content
            item.tags = list(tags or [])
            item.updated_at = now
        else:
            # Enforce max items limit: prune oldest
            if len(self._items) >= self.max_items:
                oldest_id = min(self._items.keys(), key=lambda k: self._items[k].updated_at)
                del self._items[oldest_id]

            item_id = f"mem-{uuid.uuid4().hex[:8]}"
            item = MemoryItem(
                id=item_id,
                memory_type=memory_type,
                key=key,
                content=content,
                tags=list(tags or []),
                created_at=now,
                updated_at=now,
            )
            self._items[item_id] = item

        self._save()
        logger.info("Stored memory [%s] '%s'", memory_type.value, key)
        return item

    def get(self, item_id: str) -> Optional[MemoryItem]:
        return self._items.get(item_id)

    def delete(self, item_id: str) -> bool:
        if item_id in self._items:
            del self._items[item_id]
            self._save()
            return True
        return False

    def list_all(self, memory_type: Optional[MemoryType] = None) -> List[MemoryItem]:
        items = list(self._items.values())
        if memory_type:
            items = [i for i in items if i.memory_type == memory_type]
        items.sort(key=lambda i: i.updated_at, reverse=True)
        return items

    def search(self, query: str, limit: int = 5) -> List[MemoryItem]:
        """Search memory by relevance to query without dumping entire database."""
        if not query:
            return []
        query_words = set(re.findall(r"\w+", query.lower()))
        scored: List[tuple[int, MemoryItem]] = []

        for item in self._items.values():
            score = 0
            key_words = set(re.findall(r"\w+", item.key.lower()))
            content_words = set(re.findall(r"\w+", item.content.lower()))
            tag_words = set(re.findall(r"\w+", " ".join(item.tags).lower()))

            score += len(query_words.intersection(key_words)) * 3
            score += len(query_words.intersection(content_words)) * 1
            score += len(query_words.intersection(tag_words)) * 2

            if score > 0:
                scored.append((score, item))

        scored.sort(key=lambda s: s[0], reverse=True)
        return [item for _, item in scored[:limit]]

    def _save(self) -> None:
        if not self.persistence_file:
            return
        try:
            self.persistence_file.parent.mkdir(parents=True, exist_ok=True)
            data = [item.to_dict() for item in self._items.values()]
            with open(self.persistence_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as err:
            logger.warning("Failed to persist memory: %s", err)

    def _load(self) -> None:
        if not self.persistence_file or not self.persistence_file.exists():
            return
        try:
            with open(self.persistence_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for d in data:
                item = MemoryItem(
                    id=d["id"],
                    memory_type=MemoryType(d["memory_type"]),
                    key=d["key"],
                    content=d["content"],
                    tags=d.get("tags", []),
                    created_at=d.get("created_at", time.time()),
                    updated_at=d.get("updated_at", time.time()),
                )
                self._items[item.id] = item
        except Exception as err:
            logger.warning("Failed to load memory: %s", err)
