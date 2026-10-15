"""Dynamic Windows Application Discovery and Resolution Service.

Discovers installed applications across Start Menu shortcuts, Windows App Paths,
Registry registrations, and known system utilities without executing anything.
Provides deterministic resolution from friendly user names to safe application identities.
Prohibits arbitrary model-supplied executable paths or shell execution.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
import logging
import os
import platform
import re
import shutil
import time
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ApplicationInfo:
    """Safe, immutable metadata describing an installed Windows application."""
    name: str  # Normalized lowercase identifier, e.g. "discord"
    display_name: str  # User-facing display name, e.g. "Discord"
    executable_name: str  # e.g. "Discord.exe"
    executable_path: str  # Absolute path to verified binary
    launch_target: str  # Path to execute (executable or verified shortcut)
    source: str  # e.g. "start_menu_user", "start_menu_common", "app_paths", "system_builtin"
    app_type: str = "desktop"  # "desktop", "uwp", "system", "web_app"
    arguments: Optional[List[str]] = None

    def to_safe_dict(self) -> Dict[str, Any]:
        """Return safe metadata for presentation to LLM/user without exposing filesystem details."""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "executable_name": self.executable_name,
            "app_type": self.app_type,
            "source": self.source,
        }

    def to_dict(self) -> Dict[str, Any]:
        """Full internal dictionary representation."""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "executable_name": self.executable_name,
            "executable_path": self.executable_path,
            "launch_target": self.launch_target,
            "source": self.source,
            "app_type": self.app_type,
            "arguments": self.arguments or [],
        }


@dataclass(frozen=True)
class ResolutionResult:
    """Result of resolving a friendly application name to an installed application."""
    status: str  # "SUCCESS", "AMBIGUOUS", "NOT_FOUND", "ARBITRARY_PATH_REJECTED"
    application: Optional[ApplicationInfo] = None
    candidates: List[str] = field(default_factory=list)
    error_message: Optional[str] = None


class ApplicationDiscoveryService:
    """Discovers and caches installed Windows applications safely."""

    KNOWN_SYSTEM_APPS: Dict[str, Dict[str, Any]] = {
        "notepad": {
            "display_name": "Notepad",
            "executable_name": "notepad.exe",
            "launch_target": "notepad.exe",
            "app_type": "system",
            "source": "system_builtin",
        },
        "calculator": {
            "display_name": "Calculator",
            "executable_name": "calc.exe",
            "launch_target": "calc.exe",
            "app_type": "system",
            "source": "system_builtin",
        },
        "explorer": {
            "display_name": "File Explorer",
            "executable_name": "explorer.exe",
            "launch_target": "explorer.exe",
            "app_type": "system",
            "source": "system_builtin",
        },
        "paint": {
            "display_name": "Paint",
            "executable_name": "mspaint.exe",
            "launch_target": "mspaint.exe",
            "app_type": "system",
            "source": "system_builtin",
        },
        "taskmgr": {
            "display_name": "Task Manager",
            "executable_name": "taskmgr.exe",
            "launch_target": "taskmgr.exe",
            "app_type": "system",
            "source": "system_builtin",
        },
        "youtube": {
            "display_name": "YouTube",
            "executable_name": "chrome.exe",
            "launch_target": "https://www.youtube.com",
            "app_type": "web_app",
            "source": "system_builtin",
        },
    }

    def __init__(self, cache_ttl_seconds: float = 300.0) -> None:
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cache: Dict[str, ApplicationInfo] = {}
        self._last_scan_time: float = 0.0

    def discover(self, force_refresh: bool = False) -> Dict[str, ApplicationInfo]:
        """Discover installed applications, returning a map of normalized_name -> ApplicationInfo."""
        now = time.time()
        if not force_refresh and self._cache and (now - self._last_scan_time < self.cache_ttl_seconds):
            return self._cache

        discovered: Dict[str, ApplicationInfo] = {}

        # 1. Add known system applications first
        for name, spec in self.KNOWN_SYSTEM_APPS.items():
            exe_path = shutil.which(spec["executable_name"]) or spec["launch_target"]
            app = ApplicationInfo(
                name=name,
                display_name=spec["display_name"],
                executable_name=spec["executable_name"],
                executable_path=exe_path,
                launch_target=spec["launch_target"],
                source=spec["source"],
                app_type=spec["app_type"],
            )
            discovered[name] = app

        # If not running on Windows, return builtins for mock/cross-platform testing
        if platform.system() != "Windows":
            self._cache = discovered
            self._last_scan_time = now
            return discovered

        # 2. Scan User Start Menu shortcuts
        user_start = os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs")
        self._scan_start_menu(user_start, "start_menu_user", discovered)

        # 3. Scan Common Start Menu shortcuts
        common_start = os.path.expandvars(r"%PROGRAMDATA%\Microsoft\Windows\Start Menu\Programs")
        self._scan_start_menu(common_start, "start_menu_common", discovered)

        # 4. Scan App Paths registry
        self._scan_app_paths(discovered)

        self._cache = discovered
        self._last_scan_time = now
        logger.info("ApplicationDiscoveryService: Discovered %d unique applications.", len(discovered))
        return discovered

    def _scan_start_menu(self, folder_path: str, source: str, out: Dict[str, ApplicationInfo]) -> None:
        """Safely inspect .lnk shortcuts in Start Menu folders using WScript.Shell without execution."""
        if not os.path.exists(folder_path):
            return

        wscript = None
        try:
            import win32com.client
            wscript = win32com.client.Dispatch("WScript.Shell")
        except Exception as e:
            logger.debug("win32com WScript.Shell not available for shortcut resolution: %s", e)

        for root, _, files in os.walk(folder_path):
            for f in files:
                if not f.lower().endswith(".lnk"):
                    continue

                display_name = f[:-4].strip()
                # Skip uninstallers, help files, and website links
                lower_disp = display_name.lower()
                if any(k in lower_disp for k in ("uninstall", "help", "documentation", "readme", "website", "manual")):
                    continue

                shortcut_path = os.path.join(root, f)
                target_exe = ""

                if wscript is not None:
                    try:
                        shortcut = wscript.CreateShortcut(shortcut_path)
                        target_exe = str(shortcut.TargetPath or "").strip()
                    except Exception:
                        pass

                # If shortcut resolution failed or target is empty, use the shortcut itself
                if not target_exe:
                    target_exe = shortcut_path

                exe_name = os.path.basename(target_exe) if target_exe else f"{display_name}.exe"
                if not exe_name:
                    exe_name = f"{display_name}.exe"

                norm_name = self._normalize_key(display_name)
                if not norm_name:
                    continue

                # Don't overwrite higher priority system builtins or already found apps with better paths
                if norm_name in out and out[norm_name].source == "system_builtin":
                    continue

                out[norm_name] = ApplicationInfo(
                    name=norm_name,
                    display_name=display_name,
                    executable_name=exe_name,
                    executable_path=target_exe,
                    launch_target=shortcut_path if shortcut_path.endswith(".lnk") else target_exe,
                    source=source,
                    app_type="desktop",
                )

    def _scan_app_paths(self, out: Dict[str, ApplicationInfo]) -> None:
        """Scan Windows App Paths in HKLM and HKCU for registered application executables."""
        try:
            import winreg
        except ImportError:
            return

        hives = [
            (winreg.HKEY_CURRENT_USER, "app_paths_hkcu"),
            (winreg.HKEY_LOCAL_MACHINE, "app_paths_hklm"),
        ]

        for hive, source in hives:
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths") as key:
                    subkeys_count, _, _ = winreg.QueryInfoKey(key)
                    for i in range(subkeys_count):
                        try:
                            app_key_name = winreg.EnumKey(key, i)
                            with winreg.OpenKey(key, app_key_name) as subkey:
                                raw_val, _ = winreg.QueryValueEx(subkey, "")
                                exe_path = os.path.expandvars(str(raw_val or "").strip().strip('"'))
                                if not exe_path:
                                    continue

                                exe_name = os.path.basename(exe_path)
                                display_name = app_key_name[:-4] if app_key_name.lower().endswith(".exe") else app_key_name
                                norm_name = self._normalize_key(display_name)

                                if not norm_name or norm_name in out:
                                    continue

                                out[norm_name] = ApplicationInfo(
                                    name=norm_name,
                                    display_name=display_name,
                                    executable_name=exe_name,
                                    executable_path=exe_path,
                                    launch_target=exe_path,
                                    source=source,
                                    app_type="desktop",
                                )
                        except Exception:
                            continue
            except Exception:
                continue

    def search(self, query: str, limit: int = 10) -> List[ApplicationInfo]:
        """Search installed applications by query string, sorted deterministically."""
        apps = self.discover()
        if not query or not query.strip():
            return sorted(apps.values(), key=lambda a: a.display_name.lower())[:limit]

        clean_query = query.strip().lower()
        norm_query = self._normalize_key(clean_query)

        scored: List[Tuple[int, ApplicationInfo]] = []
        for app in apps.values():
            disp_lower = app.display_name.lower()
            name_lower = app.name.lower()
            exe_lower = app.executable_name.lower()

            if clean_query == disp_lower or clean_query == name_lower:
                scored.append((100, app))
            elif norm_query == self._normalize_key(disp_lower):
                scored.append((90, app))
            elif disp_lower.startswith(clean_query) or name_lower.startswith(clean_query):
                scored.append((70, app))
            elif clean_query in disp_lower or clean_query in exe_lower or norm_query in self._normalize_key(disp_lower):
                scored.append((50, app))

        scored.sort(key=lambda item: (-item[0], item[1].display_name.lower(), item[1].source))
        return [item[1] for item in scored[:limit]]

    def list_apps(self, limit: int = 50, query: Optional[str] = None) -> List[ApplicationInfo]:
        """List discovered installed applications with safe metadata and deterministic sorting."""
        if query and query.strip():
            return self.search(query, limit=limit)
        apps = list(self.discover().values())
        apps.sort(key=lambda a: (a.display_name.lower(), a.source))
        return apps[:limit]

    @staticmethod
    def _normalize_key(text: str) -> str:
        """Normalize application name to a clean alphanumeric lowercase key."""
        return re.sub(r"[^a-z0-9]", "", text.lower())


class ApplicationResolver:
    """Resolves friendly application queries to concrete, verified ApplicationInfo instances."""

    COMMON_ALIASES: Dict[str, str] = {
        "discord": "discord",
        "chrome": "google chrome",
        "google chrome": "google chrome",
        "edge": "microsoft edge",
        "msedge": "microsoft edge",
        "microsoft edge": "microsoft edge",
        "code": "visual studio code",
        "vscode": "visual studio code",
        "vs code": "visual studio code",
        "visual studio code": "visual studio code",
        "notepad": "notepad",
        "calculator": "calculator",
        "calc": "calculator",
        "explorer": "file explorer",
        "file explorer": "file explorer",
        "paint": "paint",
        "mspaint": "paint",
        "taskmgr": "task manager",
        "task manager": "task manager",
        "youtube": "youtube",
        "word": "word",
        "excel": "excel",
        "powerpoint": "powerpoint",
        "outlook": "outlook",
        "access": "access",
        "onenote": "onenote",
    }

    def __init__(self, discovery_service: Optional[ApplicationDiscoveryService] = None) -> None:
        self.discovery_service = discovery_service or ApplicationDiscoveryService()

    def resolve(self, app_name: str) -> ResolutionResult:
        """Deterministically resolve an application name into a single verified ApplicationInfo."""
        if not app_name or not isinstance(app_name, str) or not app_name.strip():
            return ResolutionResult(
                status="NOT_FOUND",
                error_message="Application name cannot be empty.",
            )

        clean_name = app_name.strip()

        # Security check: Prohibit arbitrary paths, traversal, direct executable paths, and shell metacharacters
        if any(sep in clean_name for sep in ("\\", "/", ":", "..", ";", "&", "|", "`", "$", "<", ">")) or clean_name.startswith("."):
            logger.warning("Rejected arbitrary path or traversal attempt in application resolver: '%s'", clean_name)
            return ResolutionResult(
                status="ARBITRARY_PATH_REJECTED",
                error_message=f"Arbitrary path execution is prohibited. '{clean_name}' must be an application name, not a file path or command.",
            )

        # Remove trailing .exe if provided by user e.g. "discord.exe" -> "discord"
        if clean_name.lower().endswith(".exe"):
            clean_name = clean_name[:-4].strip()

        apps = self.discovery_service.discover()
        lower_name = clean_name.lower()
        norm_key = ApplicationDiscoveryService._normalize_key(clean_name)

        # 1. Check exact key match in discovered apps
        if lower_name in apps:
            return ResolutionResult(status="SUCCESS", application=apps[lower_name])

        if norm_key in apps:
            return ResolutionResult(status="SUCCESS", application=apps[norm_key])

        # 2. Check alias mapping
        if lower_name in self.COMMON_ALIASES:
            target_alias = self.COMMON_ALIASES[lower_name]
            alias_norm = ApplicationDiscoveryService._normalize_key(target_alias)
            if alias_norm in apps:
                return ResolutionResult(status="SUCCESS", application=apps[alias_norm])
            if target_alias in apps:
                return ResolutionResult(status="SUCCESS", application=apps[target_alias])

        # 3. Match against display_name exactly (case-insensitive)
        exact_matches = [
            app for app in apps.values()
            if app.display_name.lower() == lower_name or ApplicationDiscoveryService._normalize_key(app.display_name) == norm_key
        ]
        if len(exact_matches) == 1:
            return ResolutionResult(status="SUCCESS", application=exact_matches[0])
        elif len(exact_matches) > 1:
            # Prefer system_builtin if exists
            for m in exact_matches:
                if m.source == "system_builtin":
                    return ResolutionResult(status="SUCCESS", application=m)
            return ResolutionResult(
                status="AMBIGUOUS",
                candidates=[m.display_name for m in exact_matches],
                error_message=f"Multiple applications match '{clean_name}': {', '.join(m.display_name for m in exact_matches)}",
            )

        # 4. Match against prefix or whole-word boundary
        word_matches: List[ApplicationInfo] = []
        pattern = rf"\b{re.escape(clean_name)}\b"
        for app in apps.values():
            if re.search(pattern, app.display_name, re.IGNORECASE) or re.search(pattern, app.executable_name, re.IGNORECASE):
                word_matches.append(app)

        if len(word_matches) == 1:
            return ResolutionResult(status="SUCCESS", application=word_matches[0])
        elif len(word_matches) > 1:
            return ResolutionResult(
                status="AMBIGUOUS",
                candidates=[m.display_name for m in word_matches],
                error_message=f"Ambiguous application name '{clean_name}'. Did you mean: {', '.join(m.display_name for m in word_matches)}?",
            )

        # 5. Search for partial matches
        search_results = self.discovery_service.search(clean_name, limit=5)
        if len(search_results) == 1:
            return ResolutionResult(status="SUCCESS", application=search_results[0])
        elif len(search_results) > 1:
            return ResolutionResult(
                status="AMBIGUOUS",
                candidates=[m.display_name for m in search_results],
                error_message=f"Ambiguous application name '{clean_name}'. Found: {', '.join(m.display_name for m in search_results)}",
            )

        return ResolutionResult(
            status="NOT_FOUND",
            error_message=f"Installed application '{clean_name}' could not be found.",
        )
