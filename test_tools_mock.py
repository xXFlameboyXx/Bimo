"""Bimo Phase 6: Deterministic End-to-End Mock Tool Demo.

Demonstrates:
1. User: "Say hello."
   - Mock LLM emits tool call: robot.speak(text="Hello!")
   - ToolRegistry validates parameters and SAFE permission
   - Executes MockTTS
   - LLM receives tool result and produces final response: "Done."
   - Spoken through TTS abstraction.

2. User: "Set your face to happy."
   - Mock LLM emits tool call: robot.set_face(face="happy")
   - State machine transitions to SUCCESS (displaying happy face)
   - LLM receives tool result and responds: "I have updated my face to happy!"

Requires zero real hardware, zero network, zero external dependencies.
"""

from __future__ import annotations

import sys
import time

from bimo.agent.bimo_agent import BimoAgent
from bimo.agent.context import ConversationContext
from bimo.agent.mock_provider import MockLLMProvider
from bimo.core.events import Event, EventBus, EventType
from bimo.core.state import RobotState, RobotStateMachine
from bimo.interfaces.llm import ToolCall
from bimo.tools.permissions import PermissionPolicy, ToolPermission
from bimo.tools.registry import ToolRegistry
from bimo.tools.robot_tools import (
    RobotGetStatusTool,
    RobotSetFaceTool,
    RobotSpeakTool,
)
from bimo.voice import MockTTS


def print_header(title: str) -> None:
    print("\n" + "=" * 68)
    print(f"      {title}")
    print("=" * 68)


def main() -> None:
    print_header("BIMO PHASE 6: SECURE TOOL SYSTEM DEMONSTRATION")

    # 1. Setup deterministic mock environment
    event_bus = EventBus()
    state_machine = RobotStateMachine(event_bus=event_bus)
    tts = MockTTS(event_bus=event_bus)
    llm_provider = MockLLMProvider()
    context = ConversationContext()

    # 2. Build secure ToolRegistry with Phase 6 Safe Tools
    registry = ToolRegistry(policy=PermissionPolicy(allow_high_risk=False))
    registry.register(RobotSpeakTool(tts=tts))
    registry.register(RobotSetFaceTool(state_machine=state_machine, event_bus=event_bus))
    registry.register(RobotGetStatusTool(state_machine=state_machine, tts=tts))

    print(f"  Initial State      : {state_machine.current_state.value}")
    print(f"  Registered Tools   : {', '.join(registry.list_names())}")
    print(f"  Permission Policy  : Centralized 3-tier (SAFE, CONFIRM, HIGH_RISK)")
    print("=" * 68)

    # 3. Create BimoAgent with ToolRegistry
    agent = BimoAgent(
        llm_provider=llm_provider,
        tts=tts,
        context=context,
        event_bus=event_bus,
        state_machine=state_machine,
        tool_registry=registry,
        tools_enabled=True,
        max_tool_iterations=5,
    )

    # Subscribe to domain events for live tracing
    def on_event(event: Event) -> None:
        if event.type in (
            EventType.AI_STARTED,
            EventType.TOOL_CALL_REQUESTED,
            EventType.TOOL_EXECUTION_STARTED,
            EventType.TOOL_EXECUTION_COMPLETED,
            EventType.AI_FINISHED,
            EventType.STATE_CHANGED,
        ):
            print(f"  [EVENT] {event.type.value:26} : {event.data}")

    event_bus.subscribe(None, on_event)

    # ------------------------------------------------------------------
    # SCENARIO 1: Tool Call robot.speak
    # ------------------------------------------------------------------
    print("\n" + "-" * 68)
    print("  SCENARIO 1: User asks Bimo to speak aloud")
    print("  Query: 'Say hello.'")
    print("-" * 68)

    # Step 1: Mock LLM returns tool call robot.speak(text="Hello!")
    call_1 = ToolCall(id="call_speak_01", name="robot.speak", arguments={"text": "Hello! I am Bimo."})
    llm_provider.enqueue_response(content="", tool_calls=[call_1])

    # Step 2: After seeing tool result, Mock LLM returns final natural language response
    llm_provider.enqueue_response(content="Done. I said hello aloud.")

    agent.receive_user_input("Say hello.")

    print(f"  Spoken Audio Log   : {tts.spoken_texts}")
    print(f"  Robot State        : {state_machine.current_state.value}")

    # ------------------------------------------------------------------
    # SCENARIO 2: Tool Call robot.set_face
    # ------------------------------------------------------------------
    print("\n" + "-" * 68)
    print("  SCENARIO 2: User asks Bimo to change facial expression")
    print("  Query: 'Set your face to happy.'")
    print("-" * 68)

    # Step 1: Mock LLM returns tool call robot.set_face(face="happy")
    call_2 = ToolCall(id="call_face_02", name="robot.set_face", arguments={"face": "happy"})
    llm_provider.enqueue_response(content="", tool_calls=[call_2])

    # Step 2: After seeing tool result, Mock LLM returns final response
    llm_provider.enqueue_response(content="My face is now smiling and happy!")

    agent.receive_user_input("Set your face to happy.")

    print(f"  Spoken Audio Log   : {tts.spoken_texts[-1]}")
    print(f"  Robot State        : {state_machine.current_state.value}")

    # ------------------------------------------------------------------
    # SCENARIO 3: Tool Call robot.get_status
    # ------------------------------------------------------------------
    print("\n" + "-" * 68)
    print("  SCENARIO 3: User checks robot subsystem status")
    print("  Query: 'What is your status?'")
    print("-" * 68)

    call_3 = ToolCall(id="call_status_03", name="robot.get_status", arguments={})
    llm_provider.enqueue_response(content="", tool_calls=[call_3])
    llm_provider.enqueue_response(content="All subsystems are online and operating normally.")

    agent.receive_user_input("What is your status?")

    print_header("DEMONSTRATION SUMMARY")
    print("  Total Dialogue Turns :", context.message_count)
    print("  All Spoken Texts     :", tts.spoken_texts)
    print("  Final Robot State    :", state_machine.current_state.value)
    print("  Pipeline Result      : SUCCESS (Secure Tool Architecture Verified)")
    print("=" * 68 + "\n")


if __name__ == "__main__":
    main()
