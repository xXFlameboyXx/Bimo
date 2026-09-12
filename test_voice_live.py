#!/usr/bin/env python3
"""Live microphone voice input, openWakeWord detection, and Whisper transcription demonstration.

Pipeline flow:
IDLE
  ↓
Wake-word detector listens (openWakeWord / MockWakeWord)
  ↓
Wake word detected ("hey_jarvis", "alexa", etc.)
  ↓
emit WAKE_WORD_DETECTED & LISTENING_STARTED
  ↓
robot state = LISTENING
  ↓
capture user speech (PCMicrophone via SoundDevice or MockMicrophone)
  ↓
detect end of speech (EnergyVAD)
  ↓
emit SPEECH_CAPTURE_STOPPED & LISTENING_STOPPED
  ↓
Whisper transcription (WhisperSTT local CTranslate2)
  ↓
deliver transcribed text to AgentInputInterface
  ↓
emit SPEECH_RECEIVED
  ↓
robot state = THINKING (strictly NO LLM call)
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
import sys
import threading
import time

# Ensure src/ is on sys.path
SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from bimo.core.config import AudioConfig, Config
from bimo.core.events import Event, EventBus, EventType
from bimo.core.state import RobotState, RobotStateMachine
from bimo.interfaces.voice import AgentInputInterface
from bimo.voice import (
    DefaultAgentInput,
    EnergyVAD,
    MockMicrophone,
    MockSpeechToText,
    MockTTS,
    MockWakeWordDetector,
    OpenWakeWordDetector,
    PCMicrophone,
    PiperTTS,
    VoiceService,
    WhisperSTT,
    calculate_rms,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s",
)
logger = logging.getLogger("bimo.voice_pipeline_demo")


class TrackingAgentInput(DefaultAgentInput):
    """Custom AgentInput tracking received inputs to verify delivery without LLM call."""

    def __init__(self) -> None:
        super().__init__()
        self.delivered_inputs: list[tuple[str, dict]] = []
        self.input_received_event = threading.Event()

    def receive_user_input(self, text: str, metadata: dict | None = None) -> None:
        super().receive_user_input(text, metadata)
        self.delivered_inputs.append((text, metadata or {}))
        self.input_received_event.set()


def run_pipeline_demo(
    mock: bool = False,
    wake_word_models: str | list[str] = "hey_jarvis,alexa,hey_mycroft",
    wake_word_threshold: float = 0.5,
    whisper_model: str = "tiny.en",
    enable_tts: bool = True,
    tts_provider: str = "piper",
    continuous: bool = False,
    speech_timeout: float = 6.0,
    max_duration: float = 10.0,
    listen_timeout: float = 30.0,
) -> bool:
    """Run voice pipeline demonstration."""
    print("\n" + "=" * 68)
    print("  BIMO PHASE 4: MULTI-WAKE-WORD, WHISPER STT & PIPER TTS PIPELINE")
    print("=" * 68)

    if isinstance(wake_word_models, str):
        models_list = [m.strip() for m in wake_word_models.split(",") if m.strip()]
    else:
        models_list = list(wake_word_models)

    event_bus = EventBus()
    state_machine = RobotStateMachine(
        initial_state=RobotState.IDLE,
        event_bus=event_bus,
        auto_subscribe_events=True,
    )
    agent_input = TrackingAgentInput()

    tts = None
    if mock:
        print("  Mode               : MOCK / SIMULATED PIPELINE")
        mic = MockMicrophone(sample_rate=16000, chunk_size=1024)
        wake_word = MockWakeWordDetector(
            model_names=models_list,
            threshold=wake_word_threshold,
            event_bus=event_bus,
        )
        stt = MockSpeechToText(default_text="What time is it?")
        if enable_tts:
            tts = MockTTS(event_bus=event_bus)
    else:
        print("  Mode               : LIVE LOCAL AUDIO (PC Microphone & Speakers)")
        mic = PCMicrophone(sample_rate=16000, channels=1, chunk_size=1024)
        wake_word = OpenWakeWordDetector(
            model_names=models_list,
            threshold=wake_word_threshold,
            event_bus=event_bus,
        )
        stt = WhisperSTT(model_size=whisper_model, compute_type="int8")
        if enable_tts:
            model_path = Path("models/piper/en_GB-semaine-medium.onnx")
            config_path = Path("models/piper/en_GB-semaine-medium.onnx.json")
            if model_path.exists():
                tts = PiperTTS(
                    model_path=str(model_path),
                    config_path=str(config_path),
                    event_bus=event_bus,
                )
            else:
                print("  [Notice] Piper model not found in models/piper/, using MockTTS")
                tts = MockTTS(event_bus=event_bus)

    vad = EnergyVAD(
        energy_threshold=110.0,
        speech_time_threshold=0.08,
        silence_time_threshold=1.35,
        sample_rate=mic.sample_rate,
        chunk_size=mic.chunk_size,
    )

    service = VoiceService(
        microphone=mic,
        stt=stt,
        wake_word_detector=wake_word,
        event_bus=event_bus,
        agent_input=agent_input,
        vad=vad,
        speech_timeout=speech_timeout,
        max_speech_duration=max_duration,
        min_speech_bytes=1600,
    )

    print(f"  Microphone         : {mic.name} (16 kHz 16-bit PCM)")
    print(f"  Wake Word Models   : {wake_word.model_names} [Threshold: {wake_word_threshold:.2f}]")
    print(f"  Speech-To-Text     : {stt.provider_name}")
    print(f"  TTS Output         : {tts.name if tts else 'Disabled'}")
    print(f"  Initial State      : {state_machine.current_state.value.upper()}")
    print("=" * 68)

    # Event handlers for rich visual output
    def on_wake_word(event: Event) -> None:
        model = event.data.get("model", "wake_word")
        score = event.data.get("score", 1.0)
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()
        print(f"\n>>> [WAKE WORD DETECTED] Model: '{model}' (Confidence: {score:.2f})")
        print(f"    State Transition: -> {state_machine.current_state.value.upper()} (Listening face active)")

    def on_listening_started(event: Event) -> None:
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()
        print(">>> [LISTENING STARTED] Robot is now actively capturing speech...")

    def on_user_speaking(event: Event) -> None:
        level = event.data.get("audio_level", 0.0)
        bars = min(30, int(level / 80.0))
        bar_str = "#" * bars
        sys.stdout.write(f"\r\033[K    [AUDIO IN] RMS: {level:6.1f} |{bar_str:<30}|")
        sys.stdout.flush()

    def on_speech_capture_stopped(event: Event) -> None:
        reason = event.data.get("reason", "unknown")
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()
        print(f"<<< [SPEECH CAPTURE STOPPED] Reason: {reason}. User finished speaking.")

    def on_transcription_started(event: Event) -> None:
        bytes_len = event.data.get("audio_bytes_length", 0)
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()
        print(f"... [WHISPER TRANSCRIBING] Processing {bytes_len} audio bytes locally on CPU...")

    def on_transcription_completed(event: Event) -> None:
        transcript = event.data.get("transcript", "")
        conf = event.data.get("confidence", 1.0)
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()
        print(f"*** [WHISPER RESULT] \"{transcript}\" (Confidence: {conf:.2f})")

    def on_speech_received(event: Event) -> None:
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()
        print(f">>> [SPEECH RECEIVED] Delivery to AgentInputInterface complete.")
        print(f"    State Transition: -> {state_machine.current_state.value.upper()} (Thinking face active)")

    def on_error(event: Event) -> None:
        err = event.data.get("error", "Unknown error")
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()
        print(f"\n!!! [ERROR EVENT] {err}")
        print(f"    State Transition: -> {state_machine.current_state.value.upper()}")

    event_bus.subscribe(EventType.WAKE_WORD_DETECTED, on_wake_word)
    event_bus.subscribe(EventType.LISTENING_STARTED, on_listening_started)
    event_bus.subscribe(EventType.USER_SPEAKING, on_user_speaking)
    event_bus.subscribe(EventType.SPEECH_CAPTURE_STOPPED, on_speech_capture_stopped)
    event_bus.subscribe(EventType.SPEECH_TRANSCRIPTION_STARTED, on_transcription_started)
    event_bus.subscribe(EventType.SPEECH_TRANSCRIPTION_COMPLETED, on_transcription_completed)
    event_bus.subscribe(EventType.SPEECH_RECEIVED, on_speech_received)
    event_bus.subscribe(EventType.SPEECH_ERROR, on_error)
    event_bus.subscribe(EventType.SPEECH_TRANSCRIPTION_ERROR, on_error)

    service.start()

    if mock:
        print("\n[MOCK MODE] Simulating wake-word utterance...")
        time.sleep(0.3)
        wake_word.set_next_detection(True, score=0.92)
        mic.feed_audio(b"\x00\x20" * 1024)
        time.sleep(0.2)

        print("\n[MOCK MODE] Simulating user speech: 'What time is it?'...")
        for _ in range(6):
            mic.feed_audio(b"\x00\x35" * 1024)
            time.sleep(0.04)

        for _ in range(25):
            mic.feed_audio(bytes(2048))
            time.sleep(0.04)

        agent_input.input_received_event.wait(timeout=3.0)
    elif continuous:
        print("\n=================================================================")
        print("  CONTINUOUS CONVERSATION MODE ACTIVE:")
        print(f"  1. Say any configured wake word: {wake_word_models}")
        print("  2. Wait for [WAKE WORD DETECTED] -> LISTENING")
        print("  3. Speak your command naturally")
        print("  4. Robot will transcribe and respond via Piper TTS")
        print("  (Press Ctrl+C at any time to exit)")
        print("=================================================================\n")

        try:
            while True:
                agent_input.input_received_event.clear()
                if not agent_input.input_received_event.wait(timeout=listen_timeout):
                    continue

                if agent_input.delivered_inputs:
                    text, meta = agent_input.delivered_inputs[-1]
                    if tts:
                        response_text = f"I heard you say: {text}."
                        print(f"\n[PHASE 4 TTS] Speaking: \"{response_text}\"")
                        tts.speak(response_text)
                        for _ in range(100):
                            if not tts.is_speaking:
                                break
                            time.sleep(0.1)
                        # Flush mic buffer so speaker audio is not processed
                        mic.flush()
                        vad.reset()
                        wake_word.reset()
                        print(f"[PHASE 4 TTS] Finished speaking. Returning to wake-word detection...\n")
        except KeyboardInterrupt:
            print("\nStopping continuous voice service...")
    else:
        print("\n=================================================================")
        print("  INSTRUCTIONS:")
        print(f"  1. Say the wake word: '{wake_word_models}'")
        print("  2. Wait for [WAKE WORD DETECTED] -> LISTENING")
        print("  3. Ask your question (e.g. 'What time is it?')")
        print("  4. Wait for Whisper transcription -> THINKING")
        print("=================================================================\n")

        agent_input.input_received_event.wait(timeout=listen_timeout)

    time.sleep(0.5)
    service.stop()

    # Verify deliveries and state
    print("\n" + "=" * 68)
    print("                DEMONSTRATION RESULTS")
    print("=" * 68)
    delivered = len(agent_input.delivered_inputs) > 0
    if delivered:
        text, meta = agent_input.delivered_inputs[-1]
        print(f"  Transcribed Query  : \"{text}\"")
        print(f"  Delivered to Agent : YES (via AgentInputInterface)")
        print(f"  LLM Called         : NO (Strictly deferred to future phases)")
        print(f"  Final Robot State  : {state_machine.current_state.value.upper()}")
        print("  Result             : SUCCESS (Voice pipeline verified end-to-end)")

        # Phase 4 TTS synthesis verification (for single-run mode)
        if tts and not continuous:
            response_text = f"I heard you say: {text}. Phase 4 text to speech is functioning cleanly."
            print(f"\n[PHASE 4 TTS] Synthesizing speech via {tts.name}...")
            print(f"              \"{response_text}\"")
            tts.speak(response_text)
            for _ in range(50):
                if not tts.is_speaking:
                    break
                time.sleep(0.1)
            mic.flush()
            vad.reset()
            wake_word.reset()
            print(f"[PHASE 4 TTS] Playback finished. Robot state: {state_machine.current_state.value.upper()}")
    else:
        print("  Transcribed Query  : NONE (Timed out or no speech detected)")
        print("  Final Robot State  : " + state_machine.current_state.value.upper())
        print("  Result             : INCOMPLETE / TIMEOUT")
    print("=" * 68 + "\n")

    return delivered


def main() -> None:
    parser = argparse.ArgumentParser(description="Bimo Phase 4 Voice Pipeline Demo")
    parser.add_argument("--mock", action="store_true", help="Run deterministic mock test")
    parser.add_argument("--live", action="store_true", help="Run with live PC microphone and speakers")
    parser.add_argument(
        "--wake-word-models",
        default="hey_jarvis,alexa,hey_mycroft",
        help="Comma-separated wake-word models (default: hey_jarvis,alexa,hey_mycroft)",
    )
    parser.add_argument("--wake-word-threshold", type=float, default=0.5, help="Wake-word score threshold (0.0-1.0)")
    parser.add_argument("--whisper-model", default="base.en", help="Whisper model (default: base.en)")
    parser.add_argument("--no-tts", action="store_true", help="Disable TTS output")
    parser.add_argument("--continuous", action="store_true", help="Run in continuous multi-turn mode")
    parser.add_argument("--speech-timeout", type=float, default=6.0, help="Speech start timeout seconds")
    parser.add_argument("--max-duration", type=float, default=10.0, help="Max speech capture seconds")
    parser.add_argument("--timeout", type=float, default=30.0, help="Total demo wait timeout in seconds")

    args = parser.parse_args()
    mock_mode = args.mock or (not args.live and False)

    success = run_pipeline_demo(
        mock=mock_mode,
        wake_word_models=args.wake_word_models,
        wake_word_threshold=args.wake_word_threshold,
        whisper_model=args.whisper_model,
        enable_tts=not args.no_tts,
        continuous=args.continuous,
        speech_timeout=args.speech_timeout,
        max_duration=args.max_duration,
        listen_timeout=args.timeout,
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
