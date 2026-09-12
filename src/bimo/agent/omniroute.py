"""OmniRoute LLM provider implementation for Bimo.

Connects to the local or remote OmniRoute API gateway using OpenAI-compatible
chat completion contracts. Implements robust connection handling, structured error
reporting, and zero-crash failure modes.
"""

from __future__ import annotations

from collections.abc import Iterator
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from bimo.interfaces.llm import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    Message,
    TokenUsage,
    ToolCall,
)

logger = logging.getLogger(__name__)


class OmniRouteLLM(LLMProvider):
    """LLM provider communicating with the local OmniRoute AI gateway.

    Features:
    - Zero external HTTP dependencies: uses Python standard library `urllib`.
    - Fully model-agnostic: model name is injected via configuration (e.g. `gemini-3.8-flash`).
    - Standardized OpenAI `/chat/completions` endpoint convention.
    - Graceful error containment (never raises unhandled exceptions to crash the robot).
    - Safely parses and exposes `tool_calls` for Phase 6 readiness without executing them.
    """

    def __init__(
        self,
        model_name: str,
        api_key: str = "",
        base_url: str = "http://localhost:20128",
        timeout_seconds: float = 30.0,
    ) -> None:
        super().__init__(model_name=model_name, api_key=api_key, base_url=base_url)
        self.timeout_seconds = timeout_seconds
        self._endpoint_url = self._resolve_endpoint_url(base_url)

    @property
    def provider_name(self) -> str:
        return "OmniRoute"

    @property
    def endpoint_url(self) -> str:
        """Resolved HTTP chat completions target endpoint."""
        return self._endpoint_url

    @staticmethod
    def _resolve_endpoint_url(base: str) -> str:
        """Derive standard chat completions URL from base gateway URL."""
        cleaned = base.strip().rstrip("/")
        if cleaned.endswith("/chat/completions"):
            return cleaned
        if cleaned.endswith("/v1"):
            return f"{cleaned}/chat/completions"
        return f"{cleaned}/v1/chat/completions"

    def generate(self, request: LLMRequest) -> LLMResponse:
        """Generate a complete completion response from OmniRoute."""
        messages_payload: list[dict[str, Any]] = []
        for msg in request.messages:
            item: dict[str, Any] = {
                "role": msg.role.value,
                "content": msg.content,
            }
            if msg.name:
                item["name"] = msg.name
            if msg.tool_call_id:
                item["tool_call_id"] = msg.tool_call_id
            if msg.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments)
                            if isinstance(tc.arguments, dict)
                            else str(tc.arguments),
                        },
                    }
                    for tc in msg.tool_calls
                ]
            messages_payload.append(item)

        body_dict: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages_payload,
            "temperature": request.temperature,
            "stream": False,
        }

        if request.max_tokens is not None:
            body_dict["max_tokens"] = request.max_tokens

        # Pass along tools if provided (for future Phase 6 tool contracts)
        if request.tools:
            body_dict["tools"] = request.tools

        if request.extra_params:
            body_dict.update(request.extra_params)

        payload_bytes = json.dumps(body_dict).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Bimo-Robot/1.0",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = urllib.request.Request(
            url=self._endpoint_url,
            data=payload_bytes,
            headers=headers,
            method="POST",
        )

        logger.debug(
            "Dispatching LLMRequest to OmniRoute (%s, model=%s, turns=%d)",
            self._endpoint_url,
            self.model_name,
            len(request.messages),
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                resp_bytes = resp.read()
                return self._parse_success_response(resp_bytes)
        except urllib.error.HTTPError as http_err:
            error_body = ""
            try:
                error_body = http_err.read().decode("utf-8")
            except Exception:
                pass

            err_msg = f"OmniRoute HTTP {http_err.code}: {http_err.reason}"
            if error_body:
                try:
                    err_json = json.loads(error_body)
                    if "error" in err_json:
                        err_msg = f"OmniRoute HTTP {http_err.code}: {err_json['error']}"
                except Exception:
                    err_msg = f"{err_msg} ({error_body[:100]})"

            logger.error("OmniRoute HTTP error: %s", err_msg)
            return LLMResponse(
                content="",
                finish_reason="error",
                error=err_msg,
                model=self.model_name,
            )
        except urllib.error.URLError as url_err:
            err_reason = str(url_err.reason)
            logger.error("OmniRoute network connection failed: %s", err_reason)
            return LLMResponse(
                content="",
                finish_reason="error",
                error=f"OmniRoute connection failed: {err_reason}",
                model=self.model_name,
            )
        except TimeoutError:
            logger.error("OmniRoute request timed out after %.1fs", self.timeout_seconds)
            return LLMResponse(
                content="",
                finish_reason="error",
                error=f"OmniRoute request timed out after {self.timeout_seconds:.1f}s",
                model=self.model_name,
            )
        except Exception as exc:
            logger.error("Unexpected error during OmniRoute inference: %s", exc)
            return LLMResponse(
                content="",
                finish_reason="error",
                error=f"Unexpected error: {exc}",
                model=self.model_name,
            )

    def _parse_success_response(self, raw_bytes: bytes) -> LLMResponse:
        """Parse raw response bytes into a structured typed LLMResponse.

        Handles standard OpenAI JSON responses as well as Server-Sent Events (SSE)
        stream data chunks if the gateway returns streamed output.
        """
        raw_str = raw_bytes.decode("utf-8").strip()

        # Handle SSE formatted stream response fallback (e.g. data: {...}\n\ndata: [DONE])
        if raw_str.startswith("data: ") or "\ndata: " in raw_str:
            return self._parse_sse_stream(raw_str)

        try:
            data = json.loads(raw_str)
        except Exception as json_err:
            logger.error("Failed to decode JSON from OmniRoute: %s (Body: %s)", json_err, repr(raw_str[:150]))
            return LLMResponse(
                content="",
                finish_reason="error",
                error=f"Malformed JSON response: {json_err}",
                model=self.model_name,
            )

        choices = data.get("choices", [])
        if not choices:
            logger.warning("OmniRoute returned 200 OK but empty choices list.")
            return LLMResponse(
                content="",
                finish_reason="error",
                error="Empty choices returned by OmniRoute",
                model=data.get("model", self.model_name),
                raw_response=data,
            )

        choice = choices[0]
        message_data = choice.get("message", {})
        content = message_data.get("content") or ""
        finish_reason = choice.get("finish_reason", "stop")

        # Parse tool calls safely for Phase 6 readiness
        tool_calls: list[ToolCall] = []
        raw_tool_calls = message_data.get("tool_calls", [])
        if isinstance(raw_tool_calls, list):
            for tc in raw_tool_calls:
                fn = tc.get("function", {})
                args_raw = fn.get("arguments", "{}")
                if isinstance(args_raw, str):
                    try:
                        args = json.loads(args_raw)
                    except Exception:
                        args = {"raw": args_raw}
                elif isinstance(args_raw, dict):
                    args = args_raw
                else:
                    args = {}

                tool_calls.append(
                    ToolCall(
                        id=str(tc.get("id", "")),
                        name=str(fn.get("name", "")),
                        arguments=args,
                    )
                )

        # Parse token usage
        usage: TokenUsage | None = None
        raw_usage = data.get("usage")
        if isinstance(raw_usage, dict):
            usage = TokenUsage(
                prompt_tokens=raw_usage.get("prompt_tokens", 0),
                completion_tokens=raw_usage.get("completion_tokens", 0),
                total_tokens=raw_usage.get("total_tokens", 0),
            )

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=finish_reason,
            model=data.get("model", self.model_name),
            raw_response=data,
        )

    def _parse_sse_stream(self, sse_text: str) -> LLMResponse:
        """Accumulate delta content chunks from Server-Sent Events stream."""
        content_parts: list[str] = []
        finish_reason = "stop"
        model_name = self.model_name

        for line in sse_text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data_str = line[len("data:"):].strip()
            if not data_str or data_str == "[DONE]":
                continue

            try:
                chunk = json.loads(data_str)
                if "model" in chunk:
                    model_name = chunk["model"]
                choices = chunk.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    if "content" in delta and delta["content"]:
                        content_parts.append(delta["content"])
                    if choices[0].get("finish_reason"):
                        finish_reason = choices[0]["finish_reason"]
            except Exception:
                continue

        full_content = "".join(content_parts)
        return LLMResponse(
            content=full_content,
            finish_reason=finish_reason,
            model=model_name,
        )

    def generate_stream(self, request: LLMRequest) -> Iterator[str]:
        """Stream tokens (yields complete content as single chunk in unary mode)."""
        resp = self.generate(request)
        if resp.content:
            yield resp.content

    def validate_connection(self) -> bool:
        """Attempt health check against base URL."""
        health_url = self.base_url.rstrip("/")
        try:
            req = urllib.request.Request(
                health_url,
                headers={"User-Agent": "Bimo-Robot/1.0"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                return resp.status < 400
        except Exception:
            return False
