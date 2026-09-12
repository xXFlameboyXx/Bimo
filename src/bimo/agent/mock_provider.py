"""Deterministic mock LLM provider for unit tests and local simulation."""

from __future__ import annotations

from collections.abc import Iterator
import collections
import threading
from typing import Any

from bimo.interfaces.llm import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    TokenUsage,
    ToolCall,
)


class MockLLMProvider(LLMProvider):
    """Deterministic mock LLM provider for testing agent behaviors without network access.

    Features:
    - Queue of predefined responses (or a recurring default response).
    - Configurable simulated errors to test failure containment.
    - Simulated tool call generation for forward-compatibility verification.
    - Inspection of received requests.
    """

    def __init__(
        self,
        model_name: str = "mock-gemini-flash",
        api_key: str = "mock-key",
        base_url: str = "http://mock-omniroute:20128",
        default_response: str = "Hello! I am Bimo, your physical AI robot.",
    ) -> None:
        super().__init__(model_name=model_name, api_key=api_key, base_url=base_url)
        self.default_response = default_response
        self._response_queue: collections.deque[LLMResponse] = collections.deque()
        self.generate_calls: list[LLMRequest] = []
        self.simulated_error: str | None = None
        self._lock = threading.RLock()

    @property
    def provider_name(self) -> str:
        return "MockLLMProvider"

    @property
    def call_count(self) -> int:
        """Return the number of generate() invocations."""
        with self._lock:
            return len(self.generate_calls)

    def enqueue_response(
        self,
        content: str,
        tool_calls: list[ToolCall] | None = None,
        error: str | None = None,
        finish_reason: str = "stop",
    ) -> None:
        """Queue a specific response to be returned on subsequent generate() calls."""
        with self._lock:
            resp = LLMResponse(
                content=content,
                tool_calls=tool_calls or [],
                usage=TokenUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
                finish_reason="error" if error else finish_reason,
                error=error,
                model=self.model_name,
            )
            self._response_queue.append(resp)

    def set_error(self, error: str | None) -> None:
        """Configure the provider to return a simulated error on future calls."""
        with self._lock:
            self.simulated_error = error

    def clear(self) -> None:
        """Clear queued responses and call logs."""
        with self._lock:
            self._response_queue.clear()
            self.generate_calls.clear()
            self.simulated_error = None

    def generate(self, request: LLMRequest) -> LLMResponse:
        with self._lock:
            self.generate_calls.append(request)

            if self.simulated_error:
                err = self.simulated_error
                return LLMResponse(
                    content="",
                    finish_reason="error",
                    error=err,
                    model=self.model_name,
                )

            if self._response_queue:
                return self._response_queue.popleft()

            return LLMResponse(
                content=self.default_response,
                usage=TokenUsage(prompt_tokens=15, completion_tokens=12, total_tokens=27),
                finish_reason="stop",
                model=self.model_name,
            )

    def generate_stream(self, request: LLMRequest) -> Iterator[str]:
        resp = self.generate(request)
        if resp.content:
            yield resp.content

    def validate_connection(self) -> bool:
        return self.simulated_error is None
