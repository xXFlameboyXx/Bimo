#!/usr/bin/env python3
"""Live Text-To-Speech (TTS) demonstration and development tool for Bimo.

Demonstrates:
- Converting arbitrary text to speech.
- Non-blocking asynchronous playback.
- Robot state machine transitions: IDLE -> SPEAKING -> IDLE.
- Domain event logging: SPEECH_OUTPUT_STARTED, SPEECH_OUTPUT_FINISHED, SPEECH_OUTPUT_CANCELLED.
- Configurable voice, speech rate, and volume.

Default command simply speaks:
    "Hello, I am Bimo."
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys
import time

# Ensure src/ is on sys.path
SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from bimo.core.events import Event, EventBus, EventType
from bimo.core.state import RobotState, RobotStateMachine
from bimo.voice import (
    BaseTextToSpeech,
    MockTTS,
    PiperTTS,
    WindowsTTS,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s",
)
logger = logging.getLogger("bimo.tts_demo")


def run_tts_demo(
    text: str = "Hello, I am Bimo.",
    provider: str = "piper",
    mock: bool = False,
    voice: str | None = None,
    rate: int = 175,
    volume: float = 1.0,
    cancel_after: float | None = None,
) -> None:
    """Run live TTS demonstration with state machine and event bus observation."""
    print("\n" + "=" * 65)
    print("        BIMO PHASE 4: LOCAL PIPER TEXT-TO-SPEECH (TTS) DEMO")
    print("=" * 65)

    event_bus = EventBus()
    state_machine = RobotStateMachine(
        initial_state=RobotState.IDLE,
        event_bus=event_bus,
        auto_subscribe_events=True,
    )

    if mock or provider.lower() == "mock":
        print("  Mode           : MOCK TTS (Simulated)")
        tts = MockTTS(
            voice=voice,
            speech_rate=rate,
            volume=volume,
            event_bus=event_bus,
            simulated_duration=1.5,
        )
    elif provider.lower() == "piper":
        model_path = Path("models/piper/en_GB-semaine-medium.onnx")
        config_path = Path("models/piper/en_GB-semaine-medium.onnx.json")
        print(f"  Mode           : LOCAL PIPER NEURAL TTS (en_GB-semaine-medium)")
        tts = PiperTTS(
            model_path=str(model_path),
            config_path=str(config_path),
            voice=voice or "en_GB-semaine-medium",
            speech_rate=rate,
            volume=volume,
            event_bus=event_bus,
        )
    else:
        print("  Mode           : LIVE WINDOWS TTS (SAPI.SpVoice)")
        tts = WindowsTTS(
            voice=voice,
            speech_rate=rate,
            volume=volume,
            event_bus=event_bus,
        )

    tts.connect()

    print(f"  Device Name    : {tts.name}")
    print(f"  Initial State  : {state_machine.current_state.value.upper()}")
    print(f"  Speech Rate    : {rate} WPM")
    print(f"  Volume         : {int(volume * 100)}%")
    print(f"  Voice Config   : {voice or 'System Default'}")
    print(f"  Utterance Text : \"{text}\"")
    print("=" * 65)

    # Listen to domain events and print real-time status
    def on_event(event: Event) -> None:
        if event.type == EventType.SPEECH_OUTPUT_STARTED:
            print(f"\n>>> [SPEECH STARTED] Speaking: \"{event.data.get('text')}\"")
            print(f"    State Transition: -> {state_machine.current_state.value.upper()} (Showing speaking face)")
        elif event.type == EventType.SPEECH_OUTPUT_FINISHED:
            duration = event.data.get("duration", 0.0)
            print(f"\n*** [SPEECH FINISHED] Completed in {duration:.2f}s")
            print(f"    State Transition: -> {state_machine.current_state.value.upper()} (Showing idle face)")
        elif event.type == EventType.SPEECH_OUTPUT_CANCELLED:
            print(f"\n!!! [SPEECH CANCELLED] Reason: {event.data.get('reason')}")
            print(f"    State Transition: -> {state_machine.current_state.value.upper()}")
        elif event.type == EventType.SPEECH_OUTPUT_ERROR:
            print(f"\n[!] [SPEECH ERROR] Error: {event.data.get('error')}")
            print(f"    State Transition: -> {state_machine.current_state.value.upper()}")

    event_bus.subscribe(None, on_event)

    print("\nTriggering speech output (non-blocking)...")
    success = tts.speak(text, block=False)
    if not success:
        print("[!] Failed to initiate speech output.")
        return

    # Check for cancellation demo
    if cancel_after is not None:
        time.sleep(cancel_after)
        print(f"\n[User Interruption] Calling tts.stop() after {cancel_after}s...")
        tts.stop()

    # Wait until playback completes or terminates
    while tts.is_speaking or (tts._playback_thread and tts._playback_thread.is_alive()):
        time.sleep(0.05)

    # Small delay to let final events settle
    time.sleep(0.1)

    print(f"\nDemo completed cleanly. Final Robot State: {state_machine.current_state.value.upper()}")
    tts.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="Bimo Text-To-Speech (TTS) Demo")
    parser.add_argument(
        "--text",
        type=str,
        default="Hello, I am Bimo.",
        help="Text for Bimo to speak (default: 'Hello, I am Bimo.')",
    )
    parser.add_argument(
        "--provider",
        type=str,
        default="piper",
        choices=["piper", "windows", "mock"],
        help="TTS provider: 'piper' (default local neural), 'windows' (SAPI), or 'mock'",
    )
    parser.add_argument("--mock", action="store_true", help="Use mock TTS provider")
    parser.add_argument("--voice", type=str, default=None, help="Voice name or substring match (e.g., 'en_GB-semaine-medium')")
    parser.add_argument("--rate", type=int, default=175, help="Speech rate in WPM (default: 175)")
    parser.add_argument("--volume", type=float, default=1.0, help="Volume from 0.0 to 1.0 (default: 1.0)")
    parser.add_argument("--cancel-after", type=float, default=None, help="Simulate cancellation after N seconds")
    args = parser.parse_args()

    run_tts_demo(
        text=args.text,
        provider="mock" if args.mock else args.provider,
        mock=args.mock,
        voice=args.voice,
        rate=args.rate,
        volume=args.volume,
        cancel_after=args.cancel_after,
    )


if __name__ == "__main__":
    main()
