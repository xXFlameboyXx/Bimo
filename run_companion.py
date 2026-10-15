#!/usr/bin/env python3
"""Launcher for Bimo with Companion API server.

Starts:
1. EventBus
2. RobotStateMachine
3. ArtifactService
4. Live ToolRegistry and PermissionPolicy (with PC tools and artifact tools)
5. Canonical AssistantOrchestrator and BimoAgent
6. FastAPI Companion API & WebSocket server on port 8000
"""

import argparse
import logging
from pathlib import Path
import sys
import threading
import time

# Ensure src/ is on sys.path
SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import uvicorn
from bimo.agent.factory import create_assistant_orchestrator
from bimo.artifacts.service import ArtifactService
from bimo.companion import CompanionService, create_companion_app
from bimo.core.config import Config
from bimo.core.events import EventBus
from bimo.core.logging import setup_logging
from bimo.core.state import RobotState, RobotStateMachine
from bimo.tools.permissions import PermissionPolicy
from bimo.tools.registry import ToolRegistry


def main() -> None:
    parser = argparse.ArgumentParser(description="Bimo Companion Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    parser.add_argument("--mock-llm", action="store_true", help="Use local mock conversational LLM")
    parser.add_argument("--log-level", default="INFO", help="Logging level (DEBUG, INFO, WARNING)")
    args = parser.parse_args()

    setup_logging()
    logger = logging.getLogger("bimo.companion_launcher")

    print("\n" + "=" * 60)
    print("  BIMO COMPANION BACKEND SERVICE (UNIFIED ORCHESTRATION)")
    print(f"  Listening on: http://{args.host}:{args.port}")
    print(f"  WebSocket:    ws://{args.host}:{args.port}/api/v1/events")
    print("=" * 60 + "\n")

    # 1. Core bus and state machine
    event_bus = EventBus()
    state_machine = RobotStateMachine(event_bus=event_bus)

    # 2. Artifact and permission policy
    artifact_service = ArtifactService()
    permission_policy = PermissionPolicy(allow_high_risk=True, auto_confirm=False)

    # 3. Load configuration
    config = Config.from_env()
    from dataclasses import replace
    if args.mock_llm:
        config = replace(
            config,
            llm=replace(
                config.llm,
                provider="local",
                model="bimo-local-conversational",
            ),
        )

    # Try attaching Piper TTS if models exist
    tts = None
    try:
        from bimo.voice.tts import PiperTTS
        tts = PiperTTS(event_bus=event_bus)
    except Exception as err:
        logger.debug("PiperTTS optional attach skipped: %s", err)

    # 4. Canonical AssistantOrchestrator with unified ToolRegistry (including PC tools)
    orchestrator = create_assistant_orchestrator(
        config=config,
        event_bus=event_bus,
        state_machine=state_machine,
        permission_policy=permission_policy,
        artifact_service=artifact_service,
        tts=tts,
        mock=args.mock_llm,
    )

    # 5. Companion Service and FastAPI App
    companion_service = CompanionService(
        event_bus=event_bus,
        state_machine=state_machine,
        orchestrator=orchestrator,
        artifact_service=artifact_service,
        permission_policy=permission_policy,
    )

    app = create_companion_app(companion_service)

    # 6. Run uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level.lower())


if __name__ == "__main__":
    main()
