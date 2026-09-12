#!/usr/bin/env python3
"""Deterministic end-to-end mock agent pipeline demonstration.

Demonstrates:
"Hello Bimo"
    ↓
AgentInputInterface
    ↓
MockLLMProvider
    ↓
assistant response
    ↓
MockTTS
    ↓
SPEAKING
    ↓
IDLE
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

# Ensure src/ is on sys.path
SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from bimo.agent import BimoAgent, ConversationContext, MockLLMProvider
from bimo.core.events import Event, EventBus, EventType
from bimo.core.state import RobotState, RobotStateMachine
from bimo.voice import MockTTS


def run_mock_agent_demo(query: str = "Hello Bimo, how are you today?") -> bool:
    print("\n" + "=" * 68)
    print("      BIMO PHASE 5: END-TO-END DETERMINISTIC AGENT DEMO")
    print("=" * 68)

    event_bus = EventBus()
    state_machine = RobotStateMachine(
        initial_state=RobotState.IDLE,
        event_bus=event_bus,
        auto_subscribe_events=True,
    )

    tts = MockTTS(event_bus=event_bus, simulated_duration=0.5)
    llm_provider = MockLLMProvider(
        default_response="Hello! I am Bimo. I am functioning perfectly on my desktop hub."
    )
    context = ConversationContext(max_history=10)

    agent = BimoAgent(
        llm_provider=llm_provider,
        tts=tts,
        context=context,
        event_bus=event_bus,
        state_machine=state_machine,
        auto_speak=True,
    )

    # Event observers
    def on_event(event: Event) -> None:
        if event.type == EventType.AI_STARTED:
            print(f">>> [AI_STARTED] Reasoning on query: \"{event.data.get('query')}\"")
            print(f"    State Transition: -> {state_machine.current_state.value.upper()} (Thinking face)")
        elif event.type == EventType.AI_FINISHED:
            print(f">>> [AI_FINISHED] Model generated {len(event.data.get('content', ''))} characters.")
        elif event.type == EventType.SPEECH_OUTPUT_STARTED:
            print(f">>> [SPEECH_OUTPUT_STARTED] Speaking: \"{event.data.get('text')}\"")
            print(f"    State Transition: -> {state_machine.current_state.value.upper()} (Speaking mouth active)")
        elif event.type == EventType.SPEECH_OUTPUT_FINISHED:
            print(f"*** [SPEECH_OUTPUT_FINISHED] Speech completed.")
            print(f"    State Transition: -> {state_machine.current_state.value.upper()} (Idle face active)")
        elif event.type == EventType.AI_ERROR:
            print(f"!!! [AI_ERROR] {event.data.get('error')}")

    event_bus.subscribe(None, on_event)

    print(f"  Initial State      : {state_machine.current_state.value.upper()}")
    print(f"  Input Query        : \"{query}\"")
    print(f"  LLM Provider       : {llm_provider.provider_name}")
    print(f"  TTS Provider       : {tts.name}")
    print("=" * 68 + "\n")

    print("[Step 1] Delivering user text to AgentInputInterface...")
    agent.receive_user_input(query)

    # Wait for TTS worker thread to finish playback
    for _ in range(30):
        if not tts.is_speaking and state_machine.current_state == RobotState.IDLE:
            break
        time.sleep(0.05)

    time.sleep(0.1)

    print("\n" + "=" * 68)
    print("                DEMONSTRATION SUMMARY")
    print("=" * 68)
    print(f"  Conversation Turns : {context.message_count}")
    print(f"  Last User Input    : \"{context.last_user_message}\"")
    print(f"  Last Assistant Msg : \"{context.last_assistant_message}\"")
    print(f"  Final Robot State  : {state_machine.current_state.value.upper()}")
    success = state_machine.current_state == RobotState.IDLE and context.message_count == 2
    print(f"  Pipeline Result    : {'SUCCESS' if success else 'FAILURE'}")
    print("=" * 68 + "\n")

    return success


def main() -> None:
    parser = argparse.ArgumentParser(description="Bimo Phase 5 End-To-End Mock Agent Demo")
    parser.add_argument(
        "--query",
        default="Hello Bimo, how are you today?",
        help="Test query text",
    )
    args = parser.parse_args()
    success = run_mock_agent_demo(query=args.query)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
