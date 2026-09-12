"""HTTP Server implementation for the Windows PC Agent.

Provides authenticated POST /api/v1/command and GET /api/v1/health endpoints.
Enforces payload limits, HMAC signature checks, clock skew tolerance, and replay protection.
"""

from __future__ import annotations

import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from typing import Optional

from bimo.pc.auth import RequestAuthenticator
from bimo.pc.models import PCError, PCErrorCode, PCRequest, PCResponse
from windows_agent.registry import PCCommandRegistry

logger = logging.getLogger(__name__)

MAX_PAYLOAD_BYTES = 64 * 1024  # 64 KB limit


class PCAgentHTTPHandler(BaseHTTPRequestHandler):
    """HTTP request handler for incoming Bimo PC Agent requests."""

    server: PCAgentServer  # Type hint for IDE

    def log_message(self, format: str, *args: object) -> None:
        logger.debug("%s - - [%s] %s", self.client_address[0], self.log_date_time_string(), format % args)

    def _send_json_response(self, status: int, data: dict) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in ("/api/v1/health", "/health"):
            self._send_json_response(HTTPStatus.OK, {"status": "ok"})
        else:
            self._send_json_response(
                HTTPStatus.NOT_FOUND,
                {"error": "Endpoint not found"},
            )

    def do_POST(self) -> None:
        if self.path != "/api/v1/command":
            self._send_json_response(
                HTTPStatus.NOT_FOUND,
                {"error": "Endpoint not found"},
            )
            return

        content_length_header = self.headers.get("Content-Length")
        if not content_length_header:
            self._send_json_response(
                HTTPStatus.LENGTH_REQUIRED,
                PCResponse(
                    version=1,
                    request_id="",
                    success=False,
                    command="",
                    error=PCError(code=PCErrorCode.INVALID_PAYLOAD.value, message="Missing Content-Length header"),
                ).to_dict(),
            )
            return

        try:
            content_length = int(content_length_header)
        except ValueError:
            self._send_json_response(
                HTTPStatus.BAD_REQUEST,
                PCResponse(
                    version=1,
                    request_id="",
                    success=False,
                    command="",
                    error=PCError(code=PCErrorCode.INVALID_PAYLOAD.value, message="Invalid Content-Length header"),
                ).to_dict(),
            )
            return

        if content_length > MAX_PAYLOAD_BYTES:
            self._send_json_response(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                PCResponse(
                    version=1,
                    request_id="",
                    success=False,
                    command="",
                    error=PCError(code=PCErrorCode.PAYLOAD_TOO_LARGE.value, message="Payload exceeds maximum limit"),
                ).to_dict(),
            )
            return

        raw_data = self.rfile.read(content_length)
        try:
            req_data = json.loads(raw_data.decode("utf-8"))
        except Exception as json_err:
            self._send_json_response(
                HTTPStatus.BAD_REQUEST,
                PCResponse(
                    version=1,
                    request_id="",
                    success=False,
                    command="",
                    error=PCError(code=PCErrorCode.INVALID_PAYLOAD.value, message=f"Malformed JSON: {json_err}"),
                ).to_dict(),
            )
            return

        if not isinstance(req_data, dict):
            self._send_json_response(
                HTTPStatus.BAD_REQUEST,
                PCResponse(
                    version=1,
                    request_id="",
                    success=False,
                    command="",
                    error=PCError(code=PCErrorCode.INVALID_PAYLOAD.value, message="Payload must be a JSON object"),
                ).to_dict(),
            )
            return

        parsed_req = PCRequest.from_dict(req_data)

        if parsed_req.version != 1:
            self._send_json_response(
                HTTPStatus.BAD_REQUEST,
                PCResponse(
                    version=1,
                    request_id=parsed_req.request_id,
                    success=False,
                    command=parsed_req.command,
                    error=PCError(code=PCErrorCode.UNSUPPORTED_VERSION.value, message=f"Unsupported protocol version {parsed_req.version}"),
                ).to_dict(),
            )
            return

        # Authenticate request
        is_auth, auth_err_code, auth_err_msg = self.server.authenticator.authenticate(
            version=parsed_req.version,
            request_id=parsed_req.request_id,
            timestamp=parsed_req.timestamp,
            nonce=parsed_req.nonce,
            command=parsed_req.command,
            arguments=parsed_req.arguments,
            signature=parsed_req.signature,
        )

        if not is_auth:
            status_code = HTTPStatus.UNAUTHORIZED if auth_err_code == PCErrorCode.AUTH_FAILED.value else HTTPStatus.FORBIDDEN
            self._send_json_response(
                status_code,
                PCResponse(
                    version=1,
                    request_id=parsed_req.request_id,
                    success=False,
                    command=parsed_req.command,
                    error=PCError(code=auth_err_code or PCErrorCode.AUTH_FAILED.value, message=auth_err_msg or "Auth failed"),
                ).to_dict(),
            )
            return

        # Execute command via registry
        response = self.server.registry.execute(
            request_id=parsed_req.request_id,
            command_name=parsed_req.command,
            arguments=parsed_req.arguments,
        )

        http_status = HTTPStatus.OK if response.success else HTTPStatus.BAD_REQUEST
        self._send_json_response(http_status, response.to_dict())


class PCAgentServer(ThreadingHTTPServer):
    """Threading HTTP server instance holding the command registry and authenticator."""

    def __init__(
        self,
        server_address: tuple[str, int],
        registry: PCCommandRegistry,
        authenticator: RequestAuthenticator,
    ) -> None:
        self.registry = registry
        self.authenticator = authenticator
        super().__init__(server_address, PCAgentHTTPHandler)


class PCAgentServerManager:
    """Helper to start and stop the Windows PC Agent in background or foreground."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8088,
        shared_secret: str = "",
        max_clock_skew: float = 30.0,
        registry: Optional[PCCommandRegistry] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.shared_secret = shared_secret
        self.authenticator = RequestAuthenticator(shared_secret=shared_secret, max_clock_skew=max_clock_skew)
        self.registry = registry or PCCommandRegistry()
        self._server: Optional[PCAgentServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self, background: bool = True) -> None:
        """Start the HTTP server."""
        self._server = PCAgentServer(
            server_address=(self.host, self.port),
            registry=self.registry,
            authenticator=self.authenticator,
        )
        logger.info("Windows PC Agent listening on %s:%d", self.host, self.port)

        if background:
            self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._thread.start()
        else:
            try:
                self._server.serve_forever()
            except KeyboardInterrupt:
                self.stop()

    def stop(self) -> None:
        """Stop the HTTP server."""
        if self._server:
            logger.info("Stopping Windows PC Agent...")
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            self._thread = None
