"""Type-safe LLM Provider interfaces and data structures for Bimo.

Provides vendor-neutral abstractions for language model interactions,
enabling swappable backends (OmniRoute, Gemini Flash, OpenAI, Local models)
without altering robot decision-making logic.
Zero API calls implemented in this phase.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Role(str, Enum):
    """Conversation message roles."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True)
class ToolCall:
    """Represents a tool execution request emitted by the language model."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Message:
    """Single turn in a conversation."""

    role: Role
    content: str
    name: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None


@dataclass(frozen=True)
class TokenUsage:
    """Token consumption statistics for an inference request."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class LLMRequest:
    """Standardized request envelope for language models."""

    messages: list[Message]
    tools: list[dict[str, Any]] = field(default_factory=list)
    temperature: float = 0.7
    max_tokens: int | None = None
    extra_params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMResponse:
    """Standardized response from an LLM provider."""

    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: TokenUsage | None = None
    finish_reason: str = "stop"  # "stop", "tool_calls", "length", "error"
    error: str | None = None
    model: str | None = None
    raw_response: dict[str, Any] | None = None

    @property
    def has_tool_calls(self) -> bool:
        """Return True if the model requested one or more tool calls."""
        return bool(self.tool_calls)

    @property
    def is_error(self) -> bool:
        """Return True if an error occurred during inference or generation."""
        return self.error is not None or self.finish_reason == "error"


class LLMProvider(ABC):
    """Abstract contract for all AI/LLM providers in Bimo.

    Any provider (e.g., OmniRoute, Google Gemini, Anthropic, or local Ollama)
    must implement this interface.
    """

    def __init__(self, model_name: str, api_key: str = "", base_url: str = "") -> None:
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Name of the provider service (e.g. 'OmniRoute', 'GeminiDirect')."""

    @abstractmethod
    def generate(self, request: LLMRequest) -> LLMResponse:
        """Generate a complete response for the given request.

        Synchronous unary inference contract.
        """

    @abstractmethod
    def generate_stream(self, request: LLMRequest) -> Iterator[str]:
        """Stream response tokens as they are produced."""

    def validate_connection(self) -> bool:
        """Check if provider credentials and network endpoint are reachable."""
        return bool(self.api_key or self.base_url)
