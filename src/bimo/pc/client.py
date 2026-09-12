"""Bimo-side HTTP client for communicating with the Windows PC Agent.

Provides authenticated JSON-over-HTTP requests, HMAC signing, timeout handling,
connection state tracking, event dispatching, and graceful fallbacks when offline.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, Optional

from bimo.core.config import PCAgentConfig
from bimo.core.events import Event, EventBus, EventType
from bimo.pc.auth import sign_request
from bimo.pc.models import PCError, PCErrorCode, PCRequest, PCResponse

logger = logging.getLogger(__name__)


class PCAgentClient:
    """Authenticated HTTP client that dispatches requests to the Windows PC Agent."""

    def __init__(
        self,
        config: PCAgentConfig,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self.config = config
        self.event_bus = event_bus
        self._connected: bool = False
        self._base_url = f"http://{config.host}:{config.port}"

    @property
    def is_connected(self) -> bool:
        """Return cached connection status."""
        return self._connected

    @property
    def base_url(self) -> str:
        return self._base_url

    def _publish_event(self, event_type: EventType, data: Optional[Dict[str, Any]] = None) -> None:
        if self.event_bus:
            try:
                self.event_bus.publish(Event(type=event_type, data=data or {}, source="pc_client"))
            except Exception as e:
                logger.warning("Failed to publish PC event %s: %s", event_type.name, e)

    def check_health(self) -> bool:
        """Quick health probe to check if the PC Agent endpoint is reachable."""
        if not self.config.enabled:
            return False

        url = f"{self._base_url}/api/v1/health"
        req = urllib.request.Request(url, headers={"User-Agent": "Bimo-PCAgentClient/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=self.config.connect_timeout) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    was_connected = self._connected
                    self._connected = bool(data.get("status") == "ok")
                    if self._connected and not was_connected:
                        self._publish_event(EventType.PC_CONNECTED, {"host": self.config.host, "port": self.config.port})
                    return self._connected
        except Exception as err:
            logger.debug("PC Agent health check failed at %s: %s", url, err)

        if self._connected:
            self._connected = False
            self._publish_event(EventType.PC_DISCONNECTED, {"host": self.config.host, "port": self.config.port})
        return False

    def execute(
        self,
        command: str,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> PCResponse:
        """Send an authenticated command execution request to the Windows PC Agent."""
        req_id = str(uuid.uuid4())
        args = arguments if arguments is not None else {}
        ts = time.time()
        nonce = secrets.token_hex(16)

        if not self.config.enabled:
            logger.info("PC Agent is disabled in configuration. Command '%s' rejected.", command)
            return PCResponse(
                version=1,
                request_id=req_id,
                success=False,
                command=command,
                error=PCError(
                    code=PCErrorCode.UNAVAILABLE.value,
                    message="PC Agent is disabled in Bimo configuration.",
                ),
            )

        # Generate HMAC-SHA256 signature
        signature = sign_request(
            shared_secret=self.config.shared_secret,
            version=1,
            request_id=req_id,
            timestamp=ts,
            nonce=nonce,
            command=command,
            arguments=args,
        )

        request_model = PCRequest(
            version=1,
            request_id=req_id,
            timestamp=ts,
            nonce=nonce,
            command=command,
            arguments=args,
            signature=signature,
        )

        url = f"{self._base_url}/api/v1/command"
        payload_bytes = json.dumps(request_model.to_dict()).encode("utf-8")

        self._publish_event(
            EventType.PC_REQUEST_STARTED,
            {"command": command, "request_id": req_id},
        )

        http_req = urllib.request.Request(
            url,
            data=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Bimo-PCAgentClient/1.0",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(http_req, timeout=self.config.request_timeout) as resp:
                resp_bytes = resp.read()
                data = json.loads(resp_bytes.decode("utf-8"))
                response = PCResponse.from_dict(data)

                if not self._connected:
                    self._connected = True
                    self._publish_event(EventType.PC_CONNECTED, {"host": self.config.host, "port": self.config.port})

                if response.success:
                    self._publish_event(
                        EventType.PC_REQUEST_COMPLETED,
                        {"command": command, "request_id": req_id, "success": True},
                    )
                else:
                    self._publish_event(
                        EventType.PC_REQUEST_FAILED,
                        {
                            "command": command,
                            "request_id": req_id,
                            "error": response.error.code if response.error else "UNKNOWN",
                        },
                    )
                return response

        except urllib.error.HTTPError as http_err:
            raw_body = http_err.read().decode("utf-8") if http_err.fp else ""
            error_code = PCErrorCode.COMMAND_FAILED.value
            error_msg = f"HTTP {http_err.code}: {http_err.reason}"

            # Try parsing structured error from server response
            try:
                err_json = json.loads(raw_body)
                if isinstance(err_json, dict) and "error" in err_json:
                    err_data = err_json["error"]
                    if isinstance(err_data, dict):
                        error_code = err_data.get("code", error_code)
                        error_msg = err_data.get("message", error_msg)
            except Exception:
                pass

            if http_err.code in (401, 403) or error_code in (
                PCErrorCode.AUTH_FAILED.value,
                PCErrorCode.REPLAY_DETECTED.value,
                PCErrorCode.EXPIRED_TIMESTAMP.value,
            ):
                self._publish_event(
                    EventType.PC_AUTH_FAILED,
                    {"command": command, "request_id": req_id, "code": error_code, "reason": error_msg},
                )
            else:
                self._publish_event(
                    EventType.PC_REQUEST_FAILED,
                    {"command": command, "request_id": req_id, "error": error_code},
                )

            return PCResponse(
                version=1,
                request_id=req_id,
                success=False,
                command=command,
                error=PCError(code=error_code, message=error_msg),
            )

        except (urllib.error.URLError, TimeoutError, OSError) as net_err:
            if self._connected:
                self._connected = False
                self._publish_event(EventType.PC_DISCONNECTED, {"host": self.config.host, "port": self.config.port})

            is_timeout = isinstance(net_err, TimeoutError) or "timed out" in str(net_err).lower()
            code = PCErrorCode.TIMEOUT.value if is_timeout else PCErrorCode.UNAVAILABLE.value
            msg = "Request timed out" if is_timeout else f"Cannot reach Windows PC Agent: {net_err}"

            self._publish_event(
                EventType.PC_REQUEST_FAILED,
                {"command": command, "request_id": req_id, "error": code},
            )

            return PCResponse(
                version=1,
                request_id=req_id,
                success=False,
                command=command,
                error=PCError(code=code, message=msg),
            )

        except Exception as ex:
            logger.error("Unexpected error in PCAgentClient.execute: %s", ex, exc_info=True)
            self._publish_event(
                EventType.PC_REQUEST_FAILED,
                {"command": command, "request_id": req_id, "error": "CLIENT_EXCEPTION"},
            )
            return PCResponse(
                version=1,
                request_id=req_id,
                success=False,
                command=command,
                error=PCError(code=PCErrorCode.COMMAND_FAILED.value, message=str(ex)),
            )
