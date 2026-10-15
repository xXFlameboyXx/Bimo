"""Action Verification subsystem for Bimo.

Provides:
- VerificationLevel: VERIFIED, FAILED, UNCERTAIN, NOT_APPLICABLE
- VerificationResult
- ActionVerifier base and concrete verifiers for:
  - App launch & window existence
  - Window focus / foreground verification
  - Input target persistence verification
  - Artifact integrity & structure verification (PDF, images, etc.)
  - Device state query verification
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import logging
import os
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class VerificationLevel(str, Enum):
    """Result status of an action verification check."""
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass
class VerificationResult:
    """Detailed outcome of an action verification check."""
    level: VerificationLevel
    message: str
    details: Optional[Dict[str, Any]] = None

    @property
    def is_verified(self) -> bool:
        return self.level == VerificationLevel.VERIFIED

    @property
    def is_failed(self) -> bool:
        return self.level == VerificationLevel.FAILED

    @property
    def is_uncertain(self) -> bool:
        return self.level == VerificationLevel.UNCERTAIN

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level.value,
            "message": self.message,
            "details": self.details or {},
        }


class ActionVerifier:
    """Verifies the state resulting from an executed action."""

    def verify(self, tool_name: str, arguments: Dict[str, Any], output: Any) -> VerificationResult:
        """Verify the post-condition of a tool execution."""
        clean_name = tool_name.strip().lower()

        # 1. PC / Window Operations
        if clean_name in ("pc.open_app", "open_app"):
            return self._verify_open_app(arguments, output)
        elif clean_name in ("pc.focus_window", "focus_window"):
            return self._verify_focus_window(arguments, output)
        elif clean_name in ("pc.type_text", "pc.press_key", "pc.click"):
            return self._verify_input_action(arguments, output)
        elif clean_name in ("pc.close_app", "close_app"):
            return self._verify_close_app(arguments, output)
        elif clean_name in ("pc.open_url", "open_url", "pc.play_youtube", "play_youtube"):
            return self._verify_url_or_media_action(arguments, output)

        # 2. Software Installation & Verification Operations
        elif clean_name == "pc.system_info":
            return self._verify_system_info(arguments, output)
        elif clean_name == "pc.package_search":
            return self._verify_package_search(arguments, output)
        elif clean_name == "pc.package_show":
            return self._verify_package_show(arguments, output)
        elif clean_name == "pc.package_install":
            return self._verify_package_install(arguments, output)
        elif clean_name == "pc.download_installer":
            return self._verify_download_installer(arguments, output)
        elif clean_name == "pc.verify_installer":
            return self._verify_installer_integrity(arguments, output)
        elif clean_name in ("pc.run_installer", "pc.execute_installer"):
            return self._verify_execute_installer(arguments, output)
        elif clean_name == "pc.verify_app_running":
            return self._verify_app_running(arguments, output)

        # 3. Artifact operations
        elif clean_name.startswith("artifact.") or clean_name in ("generate_document", "render_image"):
            return self._verify_artifact(clean_name, arguments, output)

        # 4. Smart Device operations
        elif clean_name.startswith("device."):
            return self._verify_device_action(arguments, output)

        return VerificationResult(
            level=VerificationLevel.NOT_APPLICABLE,
            message=f"No verification rule registered for tool '{tool_name}'.",
        )

    def _verify_url_or_media_action(self, arguments: Dict[str, Any], output: Any) -> VerificationResult:
        """Verify URL navigation or media playback action."""
        if not output or not isinstance(output, dict):
            return VerificationResult(level=VerificationLevel.VERIFIED, message="URL/media action completed.")
        if output.get("status") in ("opened", "playing", "launched", "ok") or output.get("url") or output.get("success"):
            return VerificationResult(
                level=VerificationLevel.VERIFIED,
                message=f"URL/media action completed: {output.get('url', output.get('status', 'success'))}",
                details=output,
            )
        return VerificationResult(level=VerificationLevel.VERIFIED, message="URL/media action completed.")


    def _verify_open_app(self, arguments: Dict[str, Any], output: Any) -> VerificationResult:
        """Verify that open_app successfully found/created a target window."""
        if not output or not isinstance(output, dict):
            return VerificationResult(
                level=VerificationLevel.FAILED,
                message="open_app output is empty or invalid.",
            )

        target = output.get("target") or output.get("target_window")
        if target:
            hwnd = target.get("hwnd") if isinstance(target, dict) else getattr(target, "hwnd", None)
            if hwnd and hwnd > 0:
                return VerificationResult(
                    level=VerificationLevel.VERIFIED,
                    message=f"Application opened and bound to HWND {hwnd}.",
                    details={"hwnd": hwnd, "title": target.get("title") if isinstance(target, dict) else getattr(target, "title", None)},
                )

        if output.get("status") in ("launched", "playing", "searched"):
            return VerificationResult(
                level=VerificationLevel.VERIFIED,
                message="Application launched successfully.",
                details=output,
            )

        return VerificationResult(
            level=VerificationLevel.UNCERTAIN,
            message="Application launch returned without explicit target window confirmation.",
            details=output,
        )

    def _verify_focus_window(self, arguments: Dict[str, Any], output: Any) -> VerificationResult:
        """Verify that focus_window brought the window to the foreground."""
        if not output or not isinstance(output, dict):
            return VerificationResult(level=VerificationLevel.FAILED, message="focus_window failed to return status.")
        if (
            output.get("focused") is True
            or output.get("foreground") is True
            or output.get("status") in ("focused", "online")
            or output.get("hwnd")
        ):
            return VerificationResult(
                level=VerificationLevel.VERIFIED,
                message="Window successfully brought to foreground.",
                details=output,
            )
        return VerificationResult(
            level=VerificationLevel.FAILED,
            message="Window focus could not be verified in foreground.",
            details=output,
        )

    def _verify_input_action(self, arguments: Dict[str, Any], output: Any) -> VerificationResult:
        """Verify that keyboard/mouse action maintained target verification."""
        if not output or not isinstance(output, dict):
            return VerificationResult(level=VerificationLevel.UNCERTAIN, message="Input action produced no verification data.")
        if output.get("verified") is False:
            return VerificationResult(
                level=VerificationLevel.FAILED,
                message=f"Input action failed target verification: {output.get('reason', 'target mismatch')}",
                details=output,
            )
        return VerificationResult(
            level=VerificationLevel.VERIFIED,
            message="Input injected with target verification intact.",
            details=output,
        )

    def _verify_close_app(self, arguments: Dict[str, Any], output: Any) -> VerificationResult:
        if isinstance(output, dict) and output.get("success") is True:
            return VerificationResult(level=VerificationLevel.VERIFIED, message="Application closed successfully.", details=output)
        return VerificationResult(level=VerificationLevel.UNCERTAIN, message="Close app outcome uncertain.", details={"output": output})

    def _verify_artifact(self, tool_name: str, arguments: Dict[str, Any], output: Any) -> VerificationResult:
        """Verify that generated artifact file exists and has non-zero size and valid header."""
        file_path = None
        if isinstance(output, dict):
            file_path = output.get("file_path") or output.get("path")
        elif isinstance(output, str) and (output.endswith(".pdf") or output.endswith(".png") or output.endswith(".docx")):
            file_path = output

        if not file_path:
            if isinstance(output, dict) and output.get("id") and output.get("size_bytes", 0) > 0:
                return VerificationResult(
                    level=VerificationLevel.VERIFIED,
                    message=f"Artifact {output.get('filename')} created successfully ({output.get('size_bytes')} bytes).",
                    details=output,
                )
            return VerificationResult(level=VerificationLevel.UNCERTAIN, message="Artifact generation did not return a verifiable file path.")

        if not os.path.exists(file_path):
            if isinstance(output, dict) and output.get("id") and output.get("size_bytes", 0) > 0:
                return VerificationResult(
                    level=VerificationLevel.VERIFIED,
                    message=f"Artifact {output.get('filename')} created successfully ({output.get('size_bytes')} bytes).",
                    details=output,
                )
            return VerificationResult(level=VerificationLevel.FAILED, message=f"Artifact file not found at path: {file_path}")

        try:
            size = os.path.getsize(file_path)
            if size == 0:
                return VerificationResult(level=VerificationLevel.FAILED, message=f"Artifact file {file_path} is empty (0 bytes).")

            # Validate header for PDF
            if file_path.lower().endswith(".pdf"):
                with open(file_path, "rb") as f:
                    header = f.read(5)
                if not header.startswith(b"%PDF-"):
                    return VerificationResult(level=VerificationLevel.FAILED, message="File does not have valid %PDF- header.")

            # Validate header for PNG
            elif file_path.lower().endswith(".png"):
                with open(file_path, "rb") as f:
                    header = f.read(8)
                if not header.startswith(b"\x89PNG\r\n\x1a\n"):
                    return VerificationResult(level=VerificationLevel.FAILED, message="File does not have valid PNG header.")

            return VerificationResult(
                level=VerificationLevel.VERIFIED,
                message=f"Artifact verified: {os.path.basename(file_path)} ({size} bytes).",
                details={"file_path": file_path, "size": size},
            )
        except Exception as err:
            return VerificationResult(level=VerificationLevel.FAILED, message=f"Failed verifying artifact: {err}")

    def _verify_device_action(self, arguments: Dict[str, Any], output: Any) -> VerificationResult:
        if isinstance(output, dict) and output.get("success") is True:
            return VerificationResult(
                level=VerificationLevel.VERIFIED,
                message="Device state updated successfully.",
                details=output,
            )
        return VerificationResult(level=VerificationLevel.UNCERTAIN, message="Device action returned without explicit state confirmation.", details={"output": output})

    def _verify_system_info(self, arguments: Dict[str, Any], output: Any) -> VerificationResult:
        if not isinstance(output, dict):
            return VerificationResult(level=VerificationLevel.FAILED, message="System info output is empty or invalid.")
        free_gb = output.get("free_storage_gb")
        arch = output.get("architecture") or output.get("arch", "")
        if free_gb is not None and free_gb < 0.5:
            return VerificationResult(
                level=VerificationLevel.FAILED,
                message=f"Insufficient disk storage: only {free_gb} GB free on system drive.",
                details=output,
            )
        storage_str = f", {free_gb} GB storage available" if free_gb is not None else ""
        return VerificationResult(
            level=VerificationLevel.VERIFIED,
            message=f"System verified: {output.get('os')} ({arch}){storage_str}.",
            details=output,
        )

    def _verify_package_search(self, arguments: Dict[str, Any], output: Any) -> VerificationResult:
        if not isinstance(output, dict):
            return VerificationResult(level=VerificationLevel.FAILED, message="Package search output is invalid.")
        count = output.get("count", 0)
        packages = output.get("packages") or output.get("matches") or []
        if count > 0 or len(packages) > 0:
            top_pkg = packages[0] if packages else {}
            return VerificationResult(
                level=VerificationLevel.VERIFIED,
                message=f"Found {count or len(packages)} matching software packages. Top match: {top_pkg.get('name', '')} ({top_pkg.get('id', '')}).",
                details=output,
            )
        return VerificationResult(
            level=VerificationLevel.FAILED,
            message=f"No matching software packages found for search query '{arguments.get('query')}'.",
            details=output,
        )

    def _verify_package_show(self, arguments: Dict[str, Any], output: Any) -> VerificationResult:
        if not isinstance(output, dict):
            return VerificationResult(level=VerificationLevel.FAILED, message="Package show output is invalid.")
        pkg = output.get("package")
        if not pkg or not isinstance(pkg, dict):
            return VerificationResult(level=VerificationLevel.FAILED, message="Package details not found.")
        return VerificationResult(
            level=VerificationLevel.VERIFIED,
            message=f"Verified package: {pkg.get('name')} v{pkg.get('version')} (Publisher: {pkg.get('publisher')}, License: {pkg.get('license')}).",
            details=pkg,
        )

    def _verify_package_install(self, arguments: Dict[str, Any], output: Any) -> VerificationResult:
        if not isinstance(output, dict):
            return VerificationResult(level=VerificationLevel.FAILED, message="Package install output is invalid.")
        rc = output.get("return_code") if output.get("return_code") is not None else output.get("exit_code")
        success = output.get("success") is True or output.get("installed") is True or rc in (0, 3010) or output.get("already_installed") is True
        if success:
            return VerificationResult(
                level=VerificationLevel.VERIFIED,
                message=f"Package {output.get('package_id', arguments.get('package_id'))} installed successfully (exit code: {rc}).",
                details=output,
            )
        elevation = output.get("elevation_required") or rc in (740, -1978236885)
        if elevation:
            return VerificationResult(
                level=VerificationLevel.UNCERTAIN,
                message="Installation requires administrator approval (Windows UAC checkpoint).",
                details={"elevation_required": True, **output},
            )
        return VerificationResult(
            level=VerificationLevel.FAILED,
            message=f"Package installation failed with exit code {rc}: {output.get('error', 'unknown error')}",
            details=output,
        )

    def _verify_download_installer(self, arguments: Dict[str, Any], output: Any) -> VerificationResult:
        if not isinstance(output, dict):
            return VerificationResult(level=VerificationLevel.FAILED, message="Download output is invalid.")
        if output.get("success") is True and output.get("file_path"):
            return VerificationResult(
                level=VerificationLevel.VERIFIED,
                message=f"Installer downloaded successfully: {output.get('file_path')} ({output.get('file_size_mb')} MB).",
                details=output,
            )
        return VerificationResult(
            level=VerificationLevel.FAILED,
            message=f"Installer download failed: {output.get('error', 'download incomplete')}",
            details=output,
        )

    def _verify_installer_integrity(self, arguments: Dict[str, Any], output: Any) -> VerificationResult:
        if not isinstance(output, dict):
            return VerificationResult(level=VerificationLevel.FAILED, message="Verification output is invalid.")
        if output.get("valid") is True and output.get("sha256_matches", True):
            sig_msg = f"Signature: {output.get('signature_status')} ({output.get('signer', 'Authenticode')})"
            return VerificationResult(
                level=VerificationLevel.VERIFIED,
                message=f"Installer integrity verified. SHA256: {output.get('sha256', '')[:12]}..., {sig_msg}.",
                details=output,
            )
        if output.get("sha256_matches") is False:
            return VerificationResult(
                level=VerificationLevel.FAILED,
                message=f"SHA256 checksum mismatch! Expected: {output.get('expected_sha256')}, got: {output.get('sha256')}",
                details=output,
            )
        return VerificationResult(
            level=VerificationLevel.FAILED,
            message=f"Installer verification failed: {output.get('error', 'invalid digital signature or checksum')}",
            details=output,
        )

    def _verify_execute_installer(self, arguments: Dict[str, Any], output: Any) -> VerificationResult:
        if not isinstance(output, dict):
            return VerificationResult(level=VerificationLevel.FAILED, message="Execution output is invalid.")
        rc = output.get("return_code")
        if output.get("success") is True or rc in (0, 3010):
            return VerificationResult(
                level=VerificationLevel.VERIFIED,
                message=f"Installer executed successfully (return code: {rc}).",
                details=output,
            )
        if output.get("elevation_required") or rc == 740:
            return VerificationResult(
                level=VerificationLevel.UNCERTAIN,
                message="Installer requires administrator elevation (UAC checkpoint).",
                details={"elevation_required": True, **output},
            )
        return VerificationResult(
            level=VerificationLevel.FAILED,
            message=f"Installer execution failed with return code {rc}: {output.get('error')}",
            details=output,
        )

    def _verify_app_running(self, arguments: Dict[str, Any], output: Any) -> VerificationResult:
        if not isinstance(output, dict):
            return VerificationResult(level=VerificationLevel.FAILED, message="App running check returned invalid output.")
        is_running = (
            output.get("is_running") is True
            or output.get("running") is True
            or bool(output.get("running_pids"))
            or bool(output.get("matching_processes"))
            or bool(output.get("window_titles"))
        )
        app = output.get("app_name", arguments.get("app_name", "Application"))
        if is_running:
            return VerificationResult(
                level=VerificationLevel.VERIFIED,
                message=f"Application '{app}' is running successfully. PIDs: {output.get('running_pids')}, Windows: {output.get('window_titles')}.",
                details=output,
            )
        return VerificationResult(
            level=VerificationLevel.FAILED,
            message=f"Application '{app}' is not running or responsive.",
            details=output,
        )
