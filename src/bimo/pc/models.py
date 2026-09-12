"""Phase 7 Windows PC Agent protocol models.

Defines the JSON request, response, error payloads, and error codes for
authenticated communication between Bimo and the Windows PC Agent.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class PCErrorCode(str, Enum):
    """Standard error codes for PC Agent protocol responses."""
    AUTH_FAILED = "AUTH_FAILED"
    EXPIRED_TIMESTAMP = "EXPIRED_TIMESTAMP"
    REPLAY_DETECTED = "REPLAY_DETECTED"
    INVALID_PAYLOAD = "INVALID_PAYLOAD"
    UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    UNKNOWN_COMMAND = "UNKNOWN_COMMAND"
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    COMMAND_FAILED = "COMMAND_FAILED"
    APP_NOT_ALLOWED = "APP_NOT_ALLOWED"
    KEY_NOT_ALLOWED = "KEY_NOT_ALLOWED"
    UNAVAILABLE = "UNAVAILABLE"
    TIMEOUT = "TIMEOUT"


@dataclass(frozen=True)
class PCError:
    """Error object contained in an unsuccessful PCResponse."""
    code: str
    message: str
    details: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.details:
            data["details"] = self.details
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PCError:
        return cls(
            code=str(data.get("code", PCErrorCode.COMMAND_FAILED.value)),
            message=str(data.get("message", "Unknown error")),
            details=data.get("details"),
        )


@dataclass(frozen=True)
class PCRequest:
    """Canonical request payload sent from Bimo to the Windows PC Agent."""
    version: int
    request_id: str
    timestamp: float
    nonce: str
    command: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    signature: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "request_id": self.request_id,
            "timestamp": self.timestamp,
            "nonce": self.nonce,
            "command": self.command,
            "arguments": self.arguments,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PCRequest:
        return cls(
            version=int(data.get("version", 1)),
            request_id=str(data.get("request_id", "")),
            timestamp=float(data.get("timestamp", 0.0)),
            nonce=str(data.get("nonce", "")),
            command=str(data.get("command", "")),
            arguments=data.get("arguments") if isinstance(data.get("arguments"), dict) else {},
            signature=str(data.get("signature", "")),
        )


@dataclass(frozen=True)
class PCResponse:
    """Structured response payload returned by the Windows PC Agent."""
    version: int
    request_id: str
    success: bool
    command: str
    output: Optional[Dict[str, Any]] = None
    error: Optional[PCError] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "request_id": self.request_id,
            "success": self.success,
            "command": self.command,
            "output": self.output,
            "error": self.error.to_dict() if self.error else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PCResponse:
        err_data = data.get("error")
        error_obj = PCError.from_dict(err_data) if isinstance(err_data, dict) else None
        return cls(
            version=int(data.get("version", 1)),
            request_id=str(data.get("request_id", "")),
            success=bool(data.get("success", False)),
            command=str(data.get("command", "")),
            output=data.get("output") if isinstance(data.get("output"), dict) else None,
            error=error_obj,
        )
