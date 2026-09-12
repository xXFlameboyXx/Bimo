"""Type-safe interfaces for LLM providers, tools, and devices."""

from bimo.interfaces.devices import (
    BaseDevice,
    DeviceCapability,
    DeviceRegistry,
    DeviceStatus,
)
from bimo.interfaces.llm import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    Message,
    Role,
    TokenUsage,
    ToolCall,
)
from bimo.interfaces.tools import (
    BaseTool,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)

__all__ = [
    # LLM
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "Message",
    "Role",
    "TokenUsage",
    "ToolCall",
    # Tools
    "BaseTool",
    "ToolParameter",
    "ToolRegistry",
    "ToolResult",
    # Devices
    "BaseDevice",
    "DeviceCapability",
    "DeviceRegistry",
    "DeviceStatus",
    # Voice & Audio
    "AudioChunk",
    "BaseMicrophone",
    "BaseSpeechToText",
    "BaseTextToSpeech",
    "BaseWakeWordDetector",
    "WakeWordResult",
    "STTResult",
    "AgentInputInterface",
]

from bimo.interfaces.voice import (
    AgentInputInterface,
    AudioChunk,
    BaseMicrophone,
    BaseSpeechToText,
    BaseTextToSpeech,
    BaseWakeWordDetector,
    STTResult,
    WakeWordResult,
)
