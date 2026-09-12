"""Factory methods for instantiating LLM providers and BimoAgent."""

from __future__ import annotations

import logging
from typing import Any

from bimo.agent.bimo_agent import BimoAgent
from bimo.agent.context import ConversationContext
from bimo.agent.mock_provider import MockLLMProvider
from bimo.agent.omniroute import OmniRouteLLM
from bimo.agent.prompts import DEFAULT_BIMO_SYSTEM_PROMPT
from bimo.core.config import Config, LLMConfig
from bimo.core.events import EventBus
from bimo.core.state import RobotStateMachine
from bimo.interfaces.llm import LLMProvider
from bimo.interfaces.voice import BaseTextToSpeech

from bimo.tools.registry import ToolRegistry
from bimo.tools.robot_tools import (
    RobotGetStatusTool,
    RobotSetFaceTool,
    RobotSpeakTool,
)
from bimo.tools.web_tools import WebFetchTool, WebSearchTool

logger = logging.getLogger(__name__)


def create_default_tool_registry(
    tts: BaseTextToSpeech | None = None,
    state_machine: RobotStateMachine | None = None,
    event_bus: EventBus | None = None,
) -> ToolRegistry:
    """Create a ToolRegistry populated with the standard safe robot and web tools."""
    registry = ToolRegistry()
    registry.register(RobotSpeakTool(tts=tts))
    registry.register(RobotSetFaceTool(state_machine=state_machine, event_bus=event_bus))
    registry.register(RobotGetStatusTool(state_machine=state_machine, tts=tts))
    registry.register(WebSearchTool())
    registry.register(WebFetchTool())
    return registry


def create_llm_provider(
    config: LLMConfig | Config | None = None,
    mock: bool = False,
) -> LLMProvider:
    """Instantiate the configured LLMProvider.

    Supports:
    - OmniRouteLLM: HTTP client targeting the configured local/remote OmniRoute gateway.
    - MockLLMProvider: In-memory mock for automated unit testing and offline development.
    """
    if config is None:
        cfg = Config.from_env().llm
    elif isinstance(config, Config):
        cfg = config.llm
    else:
        cfg = config

    if mock or cfg.provider.lower() == "mock":
        logger.info("Creating MockLLMProvider (model: %s)", cfg.model)
        return MockLLMProvider(
            model_name=cfg.model,
            api_key=cfg.api_key,
            base_url=cfg.base_url,
        )

    if cfg.provider.lower() in ("local", "offline", "conversational", "interactive"):
        from bimo.agent.local_provider import LocalConversationalLLM

        logger.info("Creating LocalConversationalLLM (offline interactive mode)")
        return LocalConversationalLLM(model_name=cfg.model)

    logger.info(
        "Creating OmniRouteLLM provider (model: %s, endpoint: %s)",
        cfg.model,
        cfg.base_url,
    )
    return OmniRouteLLM(
        model_name=cfg.model,
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        timeout_seconds=cfg.timeout_seconds,
    )


def create_bimo_agent(
    config: Config | None = None,
    llm_provider: LLMProvider | None = None,
    tts: BaseTextToSpeech | None = None,
    event_bus: EventBus | None = None,
    state_machine: RobotStateMachine | None = None,
    tool_registry: ToolRegistry | None = None,
    system_prompt: str = DEFAULT_BIMO_SYSTEM_PROMPT,
    mock: bool = False,
    auto_speak: bool = True,
    enable_default_tools: bool = False,
) -> BimoAgent:
    """Construct a fully wired BimoAgent."""
    cfg = config or Config.from_env()

    provider = llm_provider or create_llm_provider(config=cfg.llm, mock=mock)
    context = ConversationContext(
        max_history=cfg.llm.max_history,
        system_prompt=system_prompt,
    )

    registry = tool_registry
    if registry is None and enable_default_tools and cfg.llm.tools_enabled:
        registry = create_default_tool_registry(
            tts=tts, state_machine=state_machine, event_bus=event_bus
        )

    return BimoAgent(
        llm_provider=provider,
        tts=tts,
        context=context,
        event_bus=event_bus,
        state_machine=state_machine,
        tool_registry=registry,
        tools_enabled=cfg.llm.tools_enabled,
        max_tool_iterations=cfg.llm.max_tool_iterations,
        auto_speak=auto_speak,
    )
