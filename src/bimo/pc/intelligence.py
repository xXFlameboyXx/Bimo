"""Application Intelligence and Adaptive Interaction Strategies for Bimo PC Control.

Provides:
- ApplicationCategory: BROWSER, EDITOR, COMMUNICATION, MEDIA, PRODUCTIVITY, DEVELOPMENT, GRAPHICS, SYSTEM, UNKNOWN
- InteractionStrategy: APPLICATION_API, UI_AUTOMATION, TARGETED_WINDOW, FOREGROUND_INPUT
- ApplicationProfile: Metadata profile classifying application category, capabilities, and preferred strategy
- ApplicationIntelligenceRegistry: Automatically profiles discovered Windows applications
- ApplicationAdapter: Base adapter and concrete adapters (e.g. BrowserAdapter, TextEditorAdapter)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ApplicationCategory(str, Enum):
    """Categorization of desktop applications."""
    BROWSER = "BROWSER"
    EDITOR = "EDITOR"
    COMMUNICATION = "COMMUNICATION"
    MEDIA = "MEDIA"
    PRODUCTIVITY = "PRODUCTIVITY"
    DEVELOPMENT = "DEVELOPMENT"
    GRAPHICS = "GRAPHICS"
    SYSTEM = "SYSTEM"
    UNKNOWN = "UNKNOWN"


class InteractionStrategy(str, Enum):
    """Preferred interaction strategy hierarchy."""
    APPLICATION_API = "APPLICATION_API"
    UI_AUTOMATION = "UI_AUTOMATION"
    TARGETED_WINDOW = "TARGETED_WINDOW"
    FOREGROUND_INPUT = "FOREGROUND_INPUT"


@dataclass
class ApplicationProfile:
    """Rich profile for an application to guide intelligent, targeted automation."""
    name: str
    display_name: str
    executable_name: str
    category: ApplicationCategory = ApplicationCategory.UNKNOWN
    preferred_strategy: InteractionStrategy = InteractionStrategy.TARGETED_WINDOW
    supports_tabs: bool = False
    supports_shortcuts: bool = True
    supports_direct_search: bool = False
    capabilities: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "executable_name": self.executable_name,
            "category": self.category.value,
            "preferred_strategy": self.preferred_strategy.value,
            "supports_tabs": self.supports_tabs,
            "supports_shortcuts": self.supports_shortcuts,
            "supports_direct_search": self.supports_direct_search,
            "capabilities": list(self.capabilities),
        }


class ApplicationIntelligenceRegistry:
    """Profiles applications based on executable signatures, names, and known behaviors."""

    KNOWN_PATTERNS = [
        # Browsers
        (r"(chrome|msedge|firefox|brave|opera)", ApplicationCategory.BROWSER, InteractionStrategy.TARGETED_WINDOW, {
            "supports_tabs": True,
            "supports_direct_search": True,
            "capabilities": ["navigate", "search", "tab_management", "zoom"],
        }),
        # Editors / IDEs
        (r"(code|devenv|pycharm|idea|sublime|notepad\+\+|atom)", ApplicationCategory.DEVELOPMENT, InteractionStrategy.TARGETED_WINDOW, {
            "supports_tabs": True,
            "supports_shortcuts": True,
            "capabilities": ["file_open", "syntax_highlighting", "terminal_integration"],
        }),
        # Simple Text Editors
        (r"(notepad|wordpad)", ApplicationCategory.EDITOR, InteractionStrategy.TARGETED_WINDOW, {
            "supports_tabs": False,
            "supports_shortcuts": True,
            "capabilities": ["plain_text", "save", "find"],
        }),
        # Communication
        (r"(discord|slack|teams|zoom|telegram|whatsapp|skype)", ApplicationCategory.COMMUNICATION, InteractionStrategy.TARGETED_WINDOW, {
            "supports_tabs": False,
            "supports_shortcuts": True,
            "capabilities": ["chat", "voice_channel", "notifications"],
        }),
        # Productivity / Office
        (r"(excel|word|powerpnt|onenote|outlook)", ApplicationCategory.PRODUCTIVITY, InteractionStrategy.TARGETED_WINDOW, {
            "supports_tabs": True,
            "supports_shortcuts": True,
            "capabilities": ["rich_document", "formatting", "export"],
        }),
        # Media
        (r"(spotify|vlc|wmplayer|music)", ApplicationCategory.MEDIA, InteractionStrategy.TARGETED_WINDOW, {
            "supports_tabs": False,
            "supports_shortcuts": True,
            "capabilities": ["playback_control", "playlist", "volume"],
        }),
        # System
        (r"(calc|explorer|taskmgr|regedit|mspaint)", ApplicationCategory.SYSTEM, InteractionStrategy.TARGETED_WINDOW, {
            "supports_tabs": False,
            "supports_shortcuts": True,
            "capabilities": ["system_utility"],
        }),
    ]

    @classmethod
    def profile_application(cls, name: str, display_name: str, executable_name: str) -> ApplicationProfile:
        """Derive an ApplicationProfile from application attributes."""
        combined = f"{name} {display_name} {executable_name}".lower()

        for pattern, category, strategy, extra in cls.KNOWN_PATTERNS:
            if re.search(pattern, combined):
                return ApplicationProfile(
                    name=name,
                    display_name=display_name,
                    executable_name=executable_name,
                    category=category,
                    preferred_strategy=strategy,
                    supports_tabs=extra.get("supports_tabs", False),
                    supports_shortcuts=extra.get("supports_shortcuts", True),
                    supports_direct_search=extra.get("supports_direct_search", False),
                    capabilities=extra.get("capabilities", []),
                )

        return ApplicationProfile(
            name=name,
            display_name=display_name,
            executable_name=executable_name,
            category=ApplicationCategory.UNKNOWN,
            preferred_strategy=InteractionStrategy.TARGETED_WINDOW,
            capabilities=["generic_window_control"],
        )


class ApplicationAdapter:
    """Base adapter for specialized application interaction workflows."""

    def __init__(self, profile: ApplicationProfile) -> None:
        self.profile = profile

    def get_search_keystroke(self) -> Optional[str]:
        """Return the keyboard shortcut to initiate search in this application, if any."""
        if self.profile.category == ApplicationCategory.BROWSER:
            return "ctrl+l"  # Focus address / search bar
        elif self.profile.category in (ApplicationCategory.DEVELOPMENT, ApplicationCategory.EDITOR):
            return "ctrl+f"  # Find in document
        return None

    def get_new_tab_keystroke(self) -> Optional[str]:
        if self.profile.supports_tabs:
            return "ctrl+t"
        return None
