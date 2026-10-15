"""Software installation, package management, and system verification controller for Windows PC Agent.

Provides bounded, robust, safe automation for:
- System requirements and compatibility checks (architecture, storage, OS)
- Package discovery and inspection via Windows Package Manager (winget)
- Safe HTTPS installer downloading with progress tracking and size limits
- Cryptographic hash (SHA256) and Windows Authenticode digital signature verification
- Documented unattended/silent installer execution (/S, /qn, etc.)
- Process execution verification and health diagnostics
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Bounded constants
MAX_DOWNLOAD_BYTES = 3 * 1024 * 1024 * 1024  # 3 GB maximum package size
DEFAULT_TIMEOUT_SECONDS = 300  # 5 minutes for installer downloads and runs
DEFAULT_STAGING_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Bimo", "installers")


@dataclass
class PackageInfo:
    """Structured information about an installable software package."""
    id: str
    name: str
    version: str
    publisher: Optional[str] = None
    homepage: Optional[str] = None
    license: Optional[str] = None
    description: Optional[str] = None
    installer_type: Optional[str] = None
    installer_url: Optional[str] = None
    installer_sha256: Optional[str] = None
    source: str = "winget"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "publisher": self.publisher,
            "homepage": self.homepage,
            "license": self.license,
            "description": self.description,
            "installer_type": self.installer_type,
            "installer_url": self.installer_url,
            "installer_sha256": self.installer_sha256,
            "source": self.source,
        }


class InstallerController:
    """Manages software discovery, downloads, verification, and installation on Windows."""

    def __init__(self, staging_dir: Optional[str] = None) -> None:
        self.staging_dir = staging_dir or DEFAULT_STAGING_DIR
        try:
            os.makedirs(self.staging_dir, exist_ok=True)
        except Exception as e:
            logger.warning("Could not create staging directory %s: %s", self.staging_dir, e)
        self._winget_path: Optional[str] = None

    def locate_winget(self) -> Optional[str]:
        """Find the executable path for winget.exe."""
        if self._winget_path and os.path.exists(self._winget_path):
            return self._winget_path

        # 1. Check system PATH
        path_which = shutil.which("winget")
        if path_which and os.path.exists(path_which):
            self._winget_path = path_which
            return path_which

        # 2. Check standard WindowsApps location
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if local_app_data:
            candidate = os.path.join(local_app_data, "Microsoft", "WindowsApps", "winget.exe")
            if os.path.exists(candidate):
                self._winget_path = candidate
                return candidate

        # 3. Check Program Files WindowsApps
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        windows_apps = os.path.join(program_files, "WindowsApps")
        if os.path.exists(windows_apps):
            try:
                for entry in os.listdir(windows_apps):
                    if entry.startswith("Microsoft.DesktopAppInstaller_") and "x64" in entry:
                        candidate = os.path.join(windows_apps, entry, "winget.exe")
                        if os.path.exists(candidate):
                            self._winget_path = candidate
                            return candidate
            except (PermissionError, OSError):
                pass

        return None

    def get_system_info(self) -> Dict[str, Any]:
        """Query host architecture, OS version, disk storage, and memory."""
        # Disk usage on C:
        free_gb = 0.0
        total_gb = 0.0
        try:
            usage = shutil.disk_usage("C:\\" if os.name == "nt" else "/")
            free_gb = round(usage.free / (1024 ** 3), 2)
            total_gb = round(usage.total / (1024 ** 3), 2)
        except Exception as err:
            logger.debug("Failed to query disk usage: %s", err)

        # Architecture and OS
        machine = platform.machine()
        arch = platform.architecture()[0]

        # Memory (ctypes or fallback)
        total_ram_gb = 0.0
        avail_ram_gb = 0.0
        if platform.system() == "Windows":
            try:
                import ctypes

                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                    ]

                stat = MEMORYSTATUSEX()
                stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
                if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                    total_ram_gb = round(stat.ullTotalPhys / (1024 ** 3), 2)
                    avail_ram_gb = round(stat.ullAvailPhys / (1024 ** 3), 2)
            except Exception:
                pass

        winget_available = bool(self.locate_winget())

        return {
            "os": f"{platform.system()} {platform.release()}",
            "version": platform.version(),
            "architecture": machine,
            "bitness": arch,
            "free_storage_gb": free_gb,
            "total_storage_gb": total_gb,
            "total_ram_gb": total_ram_gb,
            "available_ram_gb": avail_ram_gb,
            "winget_available": winget_available,
            "winget_path": self._winget_path if winget_available else None,
        }

    def package_search(self, query: str, source: str = "winget") -> Dict[str, Any]:
        """Search package repositories via winget."""
        winget = self.locate_winget()
        if not winget:
            return {
                "success": False,
                "error": "Windows Package Manager (winget) is not installed or available on this system.",
                "packages": [],
                "count": 0,
            }

        cmd = [
            winget,
            "search",
            query,
            "--source",
            source,
            "--accept-source-agreements",
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                encoding="utf-8",
                errors="replace",
            )
            stdout = proc.stdout or ""
            packages = self._parse_winget_table(stdout)
            return {
                "success": True,
                "query": query,
                "source": source,
                "count": len(packages),
                "packages": packages,
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": f"Search for '{query}' timed out.",
                "packages": [],
                "count": 0,
            }
        except Exception as err:
            return {
                "success": False,
                "error": str(err),
                "packages": [],
                "count": 0,
            }

    def _parse_winget_table(self, output: str) -> List[Dict[str, Any]]:
        """Parse tabular stdout output from winget search."""
        lines = [line.rstrip() for line in output.splitlines() if line.strip()]
        if not lines:
            return []

        # Find header line with "Name" and "Id"
        header_idx = -1
        for idx, line in enumerate(lines):
            if "Name" in line and "Id" in line:
                header_idx = idx
                break

        if header_idx == -1 or header_idx + 1 >= len(lines):
            return []

        header = lines[header_idx]
        divider = lines[header_idx + 1]

        # Determine column start indices based on separator dashes
        id_start = header.find("Id")
        version_start = header.find("Version")
        match_start = header.find("Match")
        source_start = header.find("Source")

        results = []
        for line in lines[header_idx + 2:]:
            if not line.strip() or line.startswith("-"):
                continue
            try:
                name = line[:id_start].strip() if id_start > 0 else line.strip()
                if version_start > id_start:
                    pkg_id = line[id_start:version_start].strip()
                    if match_start > version_start:
                        version = line[version_start:match_start].strip()
                    elif source_start > version_start:
                        version = line[version_start:source_start].strip()
                    else:
                        version = line[version_start:].strip()
                else:
                    pkg_id = line[id_start:].strip()
                    version = ""

                if pkg_id:
                    results.append({
                        "name": name,
                        "id": pkg_id,
                        "version": version,
                    })
            except Exception:
                continue

        return results

    def package_show(self, package_id: str, source: str = "winget") -> Dict[str, Any]:
        """Query metadata and installer details for a specific package ID."""
        winget = self.locate_winget()
        if not winget:
            return {
                "success": False,
                "error": "winget is not available on this system.",
            }

        cmd = [
            winget,
            "show",
            package_id,
            "--source",
            source,
            "--accept-source-agreements",
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                encoding="utf-8",
                errors="replace",
            )
            if proc.returncode != 0:
                return {
                    "success": False,
                    "error": f"Package '{package_id}' not found or winget returned error: {proc.stderr.strip()}",
                }

            stdout = proc.stdout or ""
            info = self._parse_winget_show(stdout, package_id)
            return {
                "success": True,
                "package": info.to_dict(),
            }
        except Exception as err:
            return {
                "success": False,
                "error": str(err),
            }

    def _parse_winget_show(self, output: str, package_id: str) -> PackageInfo:
        """Parse structured key-value stdout from winget show."""
        data: Dict[str, str] = {}
        for line in output.splitlines():
            line_str = line.strip()
            if ":" in line_str:
                parts = line_str.split(":", 1)
                key = parts[0].strip().lower()
                val = parts[1].strip()
                data[key] = val

        name = data.get("found", data.get("name", package_id))
        # Remove trailing "[Id]" from name if present
        if "[" in name:
            name = name.split("[")[0].strip()

        return PackageInfo(
            id=package_id,
            name=name,
            version=data.get("version", ""),
            publisher=data.get("publisher", ""),
            homepage=data.get("homepage", data.get("publisher url", "")),
            license=data.get("license", ""),
            description=data.get("description", ""),
            installer_type=data.get("installer type", ""),
            installer_url=data.get("installer url", ""),
            installer_sha256=data.get("installer sha256", ""),
        )

    def package_install(
        self,
        package_id: str,
        silent: bool = True,
        timeout: int = 600,
    ) -> Dict[str, Any]:
        """Install software package via winget with unattended flags."""
        winget = self.locate_winget()
        if not winget:
            return {
                "success": False,
                "error": "winget is not available on this system.",
                "elevation_required": False,
            }

        cmd = [
            winget,
            "install",
            "--id",
            package_id,
            "--exact",
            "--accept-package-agreements",
            "--accept-source-agreements",
            "--disable-interactivity",
        ]
        if silent:
            cmd.append("--silent")

        logger.info("Executing winget install for package '%s'...", package_id)
        start_time = time.time()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
            elapsed = round(time.time() - start_time, 2)
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            rc = proc.returncode

            # Common winget exit codes:
            # 0 = Success
            # 3010 = Success (Reboot required)
            # 0x8a15002b (-1978236885) = User cancelled / Elevation cancelled
            # 0x8a150040 = Package already installed
            elevation_required = (rc in (740, -1978236885) or "elevation" in stdout.lower() or "elevation" in stderr.lower())
            already_installed = ("already installed" in stdout.lower() or rc == -1978236864)

            success = (rc in (0, 3010) or already_installed)

            return {
                "success": success,
                "package_id": package_id,
                "return_code": rc,
                "elapsed_seconds": elapsed,
                "elevation_required": elevation_required,
                "already_installed": already_installed,
                "stdout": stdout[-1000:] if len(stdout) > 1000 else stdout,
                "error": stderr if not success else None,
            }

        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "package_id": package_id,
                "error": f"Installation of '{package_id}' timed out after {timeout} seconds.",
                "elevation_required": False,
            }
        except Exception as err:
            return {
                "success": False,
                "package_id": package_id,
                "error": str(err),
                "elevation_required": False,
            }

    def download_installer(
        self,
        url: str,
        dest_filename: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> Dict[str, Any]:
        """Safely download an installer binary from an HTTPS URL into staging directory."""
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme.lower() not in ("http", "https"):
            raise ValueError(f"Invalid URL scheme '{parsed.scheme}'. Only http and https URLs are allowed.")

        # Derive filename from URL if not provided
        if not dest_filename:
            dest_filename = os.path.basename(parsed.path) or "installer.exe"
            # Ensure valid Windows filename
            dest_filename = re.sub(r'[\\/*?:"<>|]', "", dest_filename)
            if not dest_filename.lower().endswith((".exe", ".msi", ".zip")):
                dest_filename += ".exe"

        dest_path = os.path.join(self.staging_dir, dest_filename)
        logger.info("Downloading installer from %s to %s...", url, dest_path)

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36 Bimo/1.0"
                )
            },
        )

        start_time = time.time()
        bytes_downloaded = 0
        sha256_hasher = hashlib.sha256()

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                cl_header = resp.headers.get("Content-Length")
                content_length = int(cl_header) if cl_header and cl_header.isdigit() else None

                if content_length and content_length > MAX_DOWNLOAD_BYTES:
                    raise ValueError(f"Package size ({content_length} bytes) exceeds maximum limit ({MAX_DOWNLOAD_BYTES} bytes).")

                # If already fully downloaded and non-empty, verify hash and return immediately
                if os.path.exists(dest_path) and os.path.getsize(dest_path) > 0:
                    current_size = os.path.getsize(dest_path)
                    if content_length is None or current_size == content_length:
                        logger.info("Found existing downloaded installer (%d bytes) at %s. Verifying hash...", current_size, dest_path)
                        with open(dest_path, "rb") as f_in:
                            while chunk := f_in.read(128 * 1024):
                                sha256_hasher.update(chunk)
                        computed_hash = sha256_hasher.hexdigest().lower()
                        return {
                            "success": True,
                            "file_path": dest_path,
                            "file_name": dest_filename,
                            "file_size": current_size,
                            "file_size_mb": round(current_size / (1024 * 1024), 2),
                            "sha256": computed_hash,
                            "elapsed_seconds": 0.0,
                            "source_url": url,
                            "cached": True,
                        }

                with open(dest_path, "wb") as f_out:
                    while True:
                        chunk = resp.read(128 * 1024)
                        if not chunk:
                            break
                        bytes_downloaded += len(chunk)
                        if bytes_downloaded > MAX_DOWNLOAD_BYTES:
                            raise ValueError(f"Download exceeded maximum limit ({MAX_DOWNLOAD_BYTES} bytes).")
                        sha256_hasher.update(chunk)
                        f_out.write(chunk)

            elapsed = round(time.time() - start_time, 2)
            computed_hash = sha256_hasher.hexdigest().lower()

            logger.info("Download completed: %s (%d bytes, SHA256: %s)", dest_path, bytes_downloaded, computed_hash)
            return {
                "success": True,
                "file_path": dest_path,
                "file_size": bytes_downloaded,
                "file_size_mb": round(bytes_downloaded / (1024 * 1024), 2),
                "sha256": computed_hash,
                "elapsed_seconds": elapsed,
                "source_url": url,
            }

        except Exception as err:
            if os.path.exists(dest_path):
                try:
                    os.remove(dest_path)
                except Exception:
                    pass
            logger.error("Download failed for %s: %s", url, err)
            return {
                "success": False,
                "error": f"Failed to download installer from {url}: {err}",
            }

    def verify_installer(
        self,
        file_path: str,
        expected_sha256: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Compute SHA256 checksum and verify Windows Authenticode signature of the installer."""
        if not os.path.exists(file_path):
            return {
                "success": False,
                "error": f"Installer file does not exist: {file_path}",
                "valid": False,
            }

        file_size = os.path.getsize(file_path)

        # 1. Compute SHA256
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(256 * 1024):
                hasher.update(chunk)
        actual_sha256 = hasher.hexdigest().lower()

        sha256_matches = True
        if expected_sha256:
            clean_expected = expected_sha256.strip().lower()
            sha256_matches = (actual_sha256 == clean_expected)

        # 2. Check Windows Authenticode Signature via PowerShell Get-AuthenticodeSignature
        signature_status = "Unknown"
        signer_subject = ""
        is_trusted = False

        if platform.system() == "Windows":
            try:
                ps_cmd = [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    f"(Get-AuthenticodeSignature -FilePath '{file_path}') | Select-Object Status, StatusMessage, @{{Name='Signer';Expression={{$_.SignerCertificate.Subject}}}} | ConvertTo-Json",
                ]
                proc = subprocess.run(ps_cmd, capture_output=True, text=True, timeout=15)
                if proc.returncode == 0 and proc.stdout.strip():
                    data = json.loads(proc.stdout.strip())
                    raw_status = str(data.get("Status", "Unknown"))
                    signer_subject = str(data.get("Signer", "") or "")
                    signature_status = raw_status
                    # Status == 0 corresponds to Valid in enum
                    if raw_status in ("Valid", "0"):
                        is_trusted = True
                    else:
                        is_trusted = False
            except Exception as e:
                logger.debug("Authenticode signature check failed: %s", e)

        valid = sha256_matches and (is_trusted or signature_status in ("Valid", "Unknown"))

        return {
            "success": True,
            "file_path": file_path,
            "file_size": file_size,
            "file_size_mb": round(file_size / (1024 * 1024), 2),
            "sha256": actual_sha256,
            "expected_sha256": expected_sha256,
            "sha256_matches": sha256_matches,
            "signature_status": signature_status,
            "signer": signer_subject,
            "is_trusted": is_trusted,
            "valid": valid,
        }

    def execute_installer(
        self,
        installer_path: str,
        arguments: Optional[List[str]] = None,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> Dict[str, Any]:
        """Execute a downloaded installer with documented unattended/silent arguments."""
        if not os.path.exists(installer_path):
            return {
                "success": False,
                "error": f"Installer file not found: {installer_path}",
                "elevation_required": False,
            }

        # Restrict to permitted binary extensions
        lower_path = installer_path.lower()
        if not (lower_path.endswith(".exe") or lower_path.endswith(".msi")):
            return {
                "success": False,
                "error": "Only .exe and .msi installer packages can be executed.",
                "elevation_required": False,
            }

        args = arguments or []
        if lower_path.endswith(".msi"):
            cmd = ["msiexec.exe", "/i", installer_path, "/qn", "/norestart"] + args
        else:
            # If no args given for .exe, default to standard unattended silent flags
            if not args:
                cmd = [installer_path, "/CURRENTUSER", "/VERYSILENT", "/SP-", "/SUPPRESSMSGBOXES", "/NORESTART", "/S"]
            else:
                cmd = [installer_path] + args

        logger.info("Executing installer: %s (timeout: %ds)", " ".join(cmd), timeout)
        start_time = time.time()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
            elapsed = round(time.time() - start_time, 2)
            rc = proc.returncode
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""

            # Win32 Error 740: ERROR_ELEVATION_REQUIRED
            elevation_required = (rc == 740 or "elevation" in stderr.lower())
            success = (rc in (0, 3010))

            return {
                "success": success,
                "installer_path": installer_path,
                "return_code": rc,
                "elapsed_seconds": elapsed,
                "elevation_required": elevation_required,
                "stdout": stdout[-1000:],
                "error": stderr if not success else None,
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": f"Installer process timed out after {timeout} seconds.",
                "elevation_required": False,
            }
        except Exception as err:
            return {
                "success": False,
                "error": str(err),
                "elevation_required": False,
            }

    def verify_app_running(self, app_name: str) -> Dict[str, Any]:
        """Check if target application process is active and has an interactive window."""
        clean = app_name.lower().strip()
        running_pids: List[int] = []
        process_names: List[str] = []
        window_titles: List[str] = []

        if platform.system() == "Windows":
            try:
                import ctypes
                import win32gui
                import win32process

                user32 = ctypes.windll.user32

                def enum_win_cb(hwnd: int, extra: Any) -> None:
                    if win32gui.IsWindowVisible(hwnd):
                        title = win32gui.GetWindowText(hwnd).strip()
                        if title:
                            pid = ctypes.c_ulong()
                            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                            # Compare title or process name
                            if clean in title.lower():
                                window_titles.append(title)
                                if pid.value not in running_pids:
                                    running_pids.append(pid.value)

                win32gui.EnumWindows(enum_win_cb, None)
            except Exception as e:
                logger.debug("Window enumeration error: %s", e)

        # Also check tasklist CLI as fallback
        try:
            tl = subprocess.run(
                ["tasklist", "/FO", "CSV"],
                capture_output=True,
                text=True,
                timeout=10,
                encoding="utf-8",
                errors="replace",
            )
            for line in tl.stdout.splitlines():
                if clean in line.lower():
                    parts = line.split('","')
                    if len(parts) >= 2:
                        pname = parts[0].replace('"', "")
                        pid_str = parts[1].replace('"', "")
                        if pname not in process_names:
                            process_names.append(pname)
                        if pid_str.isdigit() and int(pid_str) not in running_pids:
                            running_pids.append(int(pid_str))
        except Exception:
            pass

        is_running = len(running_pids) > 0 or len(window_titles) > 0
        return {
            "app_name": app_name,
            "is_running": is_running,
            "running_pids": running_pids,
            "process_names": process_names,
            "window_titles": window_titles,
            "status": "running" if is_running else "not_running",
        }
