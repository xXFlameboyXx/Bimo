"""Local conversational LLM provider for offline testing and hardware demonstrations.

Provides intelligent natural language responses and tool calls (Notepad, Clock, Face emotions)
without requiring an external API connection or running server.
"""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any

from bimo.interfaces.llm import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    TokenUsage,
    ToolCall,
)


class LocalConversationalLLM(LLMProvider):
    """Local offline conversational provider for realistic IRL testing without external network."""

    def __init__(
        self,
        model_name: str = "bimo-local-conversational",
        api_key: str = "",
        base_url: str = "",
    ) -> None:
        super().__init__(model_name=model_name, api_key=api_key, base_url=base_url)
        self.call_count = 0

    @property
    def provider_name(self) -> str:
        return "LocalConversationalLLM"

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.call_count += 1

        # Extract latest user message
        user_text = ""
        last_tool_result = ""
        for msg in reversed(request.messages):
            if msg.role.value == "user" and not user_text:
                user_text = msg.content.strip()
            elif msg.role.value == "tool" and not last_tool_result:
                last_tool_result = msg.content.strip()

        # If following up on a tool execution:
        if last_tool_result:
            return LLMResponse(
                content="I have executed the command for you.",
                tool_calls=[],
                finish_reason="stop",
                model=self.model_name,
                usage=TokenUsage(prompt_tokens=20, completion_tokens=10, total_tokens=30),
            )

        norm = user_text.lower()

        # Check for PC tool triggers
        if any(w in norm for w in ("open notepad", "launch notepad", "start notepad")):
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(id=f"call-{self.call_count}", name="pc.open_app", arguments={"app": "notepad"})],
                finish_reason="tool_calls",
                model=self.model_name,
            )

        if any(w in norm for w in ("close notepad", "exit notepad")):
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(id=f"call-{self.call_count}", name="pc.close_app", arguments={"app": "notepad"})],
                finish_reason="tool_calls",
                model=self.model_name,
            )

        if any(w in norm for w in ("take a screenshot", "capture screen", "screenshot")):
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(id=f"call-{self.call_count}", name="pc.screenshot", arguments={})],
                finish_reason="tool_calls",
                model=self.model_name,
            )

        # Conversational responses
        if any(w in norm for w in ("time", "what time")):
            now_str = datetime.now().strftime("%I:%M %p")
            reply = f"The current time is {now_str}."
        elif any(w in norm for w in ("hello", "hi", "hey bimo", "hey")):
            reply = "Hello! I am Bimo, your physical AI robot. How can I help you today?"
        elif any(w in norm for w in ("how are you", "how r u")):
            reply = "I am feeling wonderful! My sensors, microphone, and face animations are running smoothly."
        elif any(w in norm for w in ("who are you", "what is your name")):
            reply = "I am Bimo! An autonomous desktop companion robot built with real-time vision, voice, and computer control."
        elif any(w in norm for w in ("what can you do", "features", "capabilities")):
            reply = "I can listen to your voice, speak with neural speech, express dynamic emotions, and control your Windows PC desktop."
        elif any(w in norm for w in ("thank you", "thanks")):
            reply = "You are welcome! Always happy to help."
        elif any(w in norm for w in ("bye", "goodbye", "sleep")):
            reply = "Goodbye! Going back to rest. Wake me up anytime."
        else:
            clean_text = re.sub(r"[^\w\s]", "", user_text)
            reply = f"I heard you say: '{clean_text}'. My physical AI systems are running and ready."

        return LLMResponse(
            content=reply,
            tool_calls=[],
            finish_reason="stop",
            model=self.model_name,
            usage=TokenUsage(prompt_tokens=15, completion_tokens=15, total_tokens=30),
        )

    def generate_stream(self, request: LLMRequest):
        resp = self.generate(request)
        if resp.content:
            yield resp.content
