"""Unit tests for the LLM Provider interface."""

import unittest
from collections.abc import Iterator

from bimo.interfaces.llm import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    Message,
    Role,
    TokenUsage,
    ToolCall,
)


class MockLLMProvider(LLMProvider):
    """Mock implementation of LLMProvider for interface contract validation."""

    @property
    def provider_name(self) -> str:
        return "MockOmniRoute"

    def generate(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            content=f"Echo: {request.messages[-1].content}",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            finish_reason="stop",
        )

    def generate_stream(self, request: LLMRequest) -> Iterator[str]:
        yield "Echo: "
        yield request.messages[-1].content


class TestLLMInterface(unittest.TestCase):
    """Test suite for LLMProvider and conversation data structures."""

    def test_abstract_class_cannot_be_instantiated(self) -> None:
        with self.assertRaises(TypeError):
            LLMProvider("model")  # type: ignore

    def test_mock_provider_implementation(self) -> None:
        provider = MockLLMProvider("gemini-3.8-flash", api_key="dummy_key")
        self.assertEqual(provider.provider_name, "MockOmniRoute")
        self.assertEqual(provider.model_name, "gemini-3.8-flash")
        self.assertTrue(provider.validate_connection())

        # Construct request
        req = LLMRequest(
            messages=[
                Message(role=Role.SYSTEM, content="You are Bimo."),
                Message(role=Role.USER, content="Hello!"),
            ],
            temperature=0.5,
        )

        resp = provider.generate(req)
        self.assertEqual(resp.content, "Echo: Hello!")
        self.assertIsNotNone(resp.usage)
        assert resp.usage is not None
        self.assertEqual(resp.usage.total_tokens, 15)

        stream_tokens = list(provider.generate_stream(req))
        self.assertEqual("".join(stream_tokens), "Echo: Hello!")

    def test_tool_call_data_structures(self) -> None:
        call = ToolCall(
            id="call_123",
            name="set_lighting",
            arguments={"color": "#00E5FF", "brightness": 100},
        )
        msg = Message(
            role=Role.ASSISTANT,
            content="",
            tool_calls=[call],
        )
        self.assertEqual(len(msg.tool_calls), 1)
        self.assertEqual(msg.tool_calls[0].name, "set_lighting")
        self.assertEqual(msg.tool_calls[0].arguments["color"], "#00E5FF")


if __name__ == "__main__":
    unittest.main()
