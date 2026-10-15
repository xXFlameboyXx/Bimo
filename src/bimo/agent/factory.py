"""Factory methods for instantiating LLM providers, BimoAgent, and AssistantOrchestrator."""

from __future__ import annotations

import logging
from typing import Any

from bimo.agent.bimo_agent import BimoAgent
from bimo.agent.context import ConversationContext
from bimo.agent.memory import MemoryStore
from bimo.agent.mock_provider import MockLLMProvider
from bimo.agent.omniroute import OmniRouteLLM
from bimo.agent.orchestrator import AssistantOrchestrator
from bimo.agent.prompts import DEFAULT_BIMO_SYSTEM_PROMPT
from bimo.artifacts.service import ArtifactService
from bimo.artifacts.tools import register_artifact_tools
from bimo.core.config import Config, LLMConfig
from bimo.core.events import EventBus
from bimo.core.resilience import CapabilityMatrix
from bimo.core.state import RobotStateMachine
from bimo.images.local_renderer import DeterministicLocalImageRenderer
from bimo.images.provider import (
    ImageGenerationProvider,
    OmniRouteImageProvider,
    UnavailableImageProvider,
)
from bimo.interfaces.devices import DeviceRegistry
from bimo.interfaces.llm import LLMProvider
from bimo.interfaces.vision import VisionProvider
from bimo.interfaces.voice import BaseTextToSpeech
from bimo.tools.permissions import PermissionPolicy
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
    policy: PermissionPolicy | None = None,
    artifact_service: ArtifactService | None = None,
    image_provider: ImageGenerationProvider | None = None,
    config: Config | None = None,
    pc_client: Any | None = None,
) -> ToolRegistry:
    """Create a ToolRegistry populated with the standard safe robot, web, artifact, and PC tools."""
    registry = ToolRegistry(policy=policy)
    registry.register(RobotSpeakTool(tts=tts))
    registry.register(RobotSetFaceTool(state_machine=state_machine, event_bus=event_bus))
    registry.register(RobotGetStatusTool(state_machine=state_machine, tts=tts))
    registry.register(WebSearchTool())
    registry.register(WebFetchTool())

    # Initialize Artifact Service
    art_svc = artifact_service
    if art_svc is None:
        if config and hasattr(config, "artifacts"):
            art_svc = ArtifactService(
                workspace_dir=config.artifacts.workspace_dir,
                max_artifact_size_mb=config.artifacts.max_artifact_size_mb,
                max_text_size_mb=config.artifacts.max_text_size_mb,
                max_image_size_mb=config.artifacts.max_image_size_mb,
            )
        else:
            art_svc = ArtifactService()

    # Initialize Image Generation Provider
    img_prov = image_provider
    if img_prov is None:
        if config and hasattr(config, "image") and config.image.enabled:
            prov_type = config.image.provider.lower()
            if prov_type == "omniroute":
                img_prov = OmniRouteImageProvider(
                    base_url=config.image.base_url,
                    model=config.image.model,
                    api_key=config.image.api_key,
                    timeout_seconds=config.image.timeout_seconds,
                )
            elif prov_type in ("none", "disabled"):
                img_prov = UnavailableImageProvider("Image generation is disabled by configuration.")
            else:
                img_prov = UnavailableImageProvider(f"Unknown image provider '{config.image.provider}'.")
        else:
            img_prov = OmniRouteImageProvider()

    register_artifact_tools(
        registry=registry,
        artifact_service=art_svc,
        image_provider=img_prov,
        local_renderer=DeterministicLocalImageRenderer(),
    )

    # Register PC tools if client provided or configured
    client_to_use = pc_client
    if client_to_use is None:
        cfg = config or Config.from_env()
        if hasattr(cfg, "pc_agent"):
            try:
                from bimo.pc.client import PCAgentClient
                client_to_use = PCAgentClient(cfg.pc_agent, event_bus=event_bus)
            except Exception as pc_err:
                logger.debug("PCAgentClient creation skipped: %s", pc_err)

    if client_to_use is not None:
        try:
            from bimo.pc.tools import register_pc_tools
            register_pc_tools(registry, client_to_use)
            logger.info("PC Agent tools registered into ToolRegistry.")
        except Exception as pc_reg_err:
            logger.warning("Could not register PC agent tools: %s", pc_reg_err)

    return registry


def create_llm_provider(
    config: LLMConfig | Config | None = None,
    mock: bool = False,
) -> LLMProvider:
    """Instantiate the configured LLMProvider."""
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
    permission_policy: Any | None = None,
    system_prompt: str = DEFAULT_BIMO_SYSTEM_PROMPT,
    mock: bool = False,
    auto_speak: bool = True,
    enable_default_tools: bool = False,
    pc_client: Any | None = None,
) -> BimoAgent:
    """Construct a fully wired BimoAgent."""
    cfg = config or Config.from_env()

    provider = llm_provider or create_llm_provider(config=cfg.llm, mock=mock)
    context = ConversationContext(
        max_history=cfg.llm.max_history,
        system_prompt=system_prompt,
    )

    policy = permission_policy
    if policy is None and tool_registry is not None:
        policy = tool_registry.policy
    elif policy is None:
        policy = PermissionPolicy(
            allow_high_risk=True,
            auto_confirm=bool(cfg.pc_agent.auto_confirm if hasattr(cfg, "pc_agent") else False),
        )

    registry = tool_registry
    if registry is None and enable_default_tools and cfg.llm.tools_enabled:
        registry = create_default_tool_registry(
            tts=tts,
            state_machine=state_machine,
            event_bus=event_bus,
            policy=policy,
            config=cfg,
            pc_client=pc_client,
        )

    return BimoAgent(
        llm_provider=provider,
        tts=tts,
        context=context,
        event_bus=event_bus,
        state_machine=state_machine,
        tool_registry=registry,
        permission_policy=policy,
        tools_enabled=cfg.llm.tools_enabled,
        max_tool_iterations=cfg.llm.max_tool_iterations,
        auto_speak=auto_speak,
    )


def create_assistant_orchestrator(
    config: Config | None = None,
    llm_provider: LLMProvider | None = None,
    tts: BaseTextToSpeech | None = None,
    event_bus: EventBus | None = None,
    state_machine: RobotStateMachine | None = None,
    tool_registry: ToolRegistry | None = None,
    permission_policy: PermissionPolicy | None = None,
    artifact_service: ArtifactService | None = None,
    memory_store: MemoryStore | None = None,
    device_registry: DeviceRegistry | None = None,
    vision_provider: VisionProvider | None = None,
    pc_client: Any | None = None,
    mock: bool = False,
    auto_speak: bool = False,
) -> AssistantOrchestrator:
    """Canonical factory constructing the unified Bimo AssistantOrchestrator with all subsystems."""
    cfg = config or Config.from_env()
    bus = event_bus or EventBus()
    sm = state_machine or RobotStateMachine(event_bus=bus)

    # 1. Permission policy
    policy = permission_policy
    if policy is None:
        policy = PermissionPolicy(
            allow_high_risk=True,
            auto_confirm=bool(cfg.pc_agent.auto_confirm if hasattr(cfg, "pc_agent") else False),
        )

    # 2. Shared Artifact & Memory Services
    art_svc = artifact_service or ArtifactService()
    mem_store = memory_store or MemoryStore()
    dev_reg = device_registry or DeviceRegistry()

    # 3. Live ToolRegistry containing Robot, Web, Artifact, and PC tools
    registry = tool_registry
    if registry is None:
        registry = create_default_tool_registry(
            tts=tts,
            state_machine=sm,
            event_bus=bus,
            policy=policy,
            artifact_service=art_svc,
            config=cfg,
            pc_client=pc_client,
        )

    # 4. LLM Provider and BimoAgent
    provider = llm_provider or create_llm_provider(config=cfg.llm, mock=mock)
    agent = create_bimo_agent(
        config=cfg,
        llm_provider=provider,
        tts=tts,
        event_bus=bus,
        state_machine=sm,
        tool_registry=registry,
        permission_policy=policy,
        auto_speak=auto_speak,
    )

    cap_matrix = CapabilityMatrix()

    # 5. Build canonical AssistantOrchestrator
    orchestrator = AssistantOrchestrator(
        llm_provider=provider,
        tool_registry=registry,
        permission_policy=policy,
        state_machine=sm,
        event_bus=bus,
        tts=tts,
        artifact_service=art_svc,
        memory_store=mem_store,
        device_registry=dev_reg,
        vision_provider=vision_provider,
        capability_matrix=cap_matrix,
        agent=agent,
    )

    logger.info("Canonical AssistantOrchestrator initialized with unified tool registry.")
    return orchestrator
