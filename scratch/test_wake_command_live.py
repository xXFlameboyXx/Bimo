"""Live testing and diagnostic tool for Wake Command Gating and Phonetic Resolution.

Tests:
1. Offline phonetic normalization benchmark for 'Bimo' across 20+ accent variations.
2. Live microphone verification of strict wake-word gating:
   - Ambient speech / sentences without wake words are strictly ignored.
   - Sentences including "Bimo", "Be more", "Beam o", "Bemo", etc. are immediately recognized.
"""

import sys
import time
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from bimo.core.config import Config
from bimo.core.events import Event, EventBus, EventType
from bimo.voice import (
    contains_wake_word,
    create_voice_service,
    extract_command_after_wake_word,
    normalize_phonetic_text,
)


def run_phonetic_benchmark():
    print("=" * 70)
    print("PHONETIC NORMALIZATION BENCHMARK: 'BIMO' MISHEARING RESOLUTION")
    print("=" * 70)

    phrases = [
        "be more open notepad",
        "Be More please help",
        "bee more what time is it",
        "beam o launch calculator",
        "beam oh open chrome",
        "beamo check system",
        "bemo show desktop",
        "b-mo what's the weather",
        "b mo take a screenshot",
        "b. mo help",
        "veemo play song",
        "vimo open discord",
        "beimo start explorer",
        "pimo what is your name",
        "bheem o gaana chalao",
        "bheemo namaste",
        "Demo open notepad",
        "Hey be more open notepad",
        "suno be more kya haal hai",
        "Vivo can you hear me",
        "vivo open notepad",
        "remove can you hear me",
        "remove",
        "heavy more open notepad",
        "heavy more can you hear me",
        "heavy more",
        "baby more open notepad",
        "hi memo how are you",
        "bimu open calculator",
        "maybe more kaise ho",
        "Remo open chrome",
    ]

    all_passed = True
    for phrase in phrases:
        normalized = normalize_phonetic_text(phrase)
        has_ww, matched = contains_wake_word(phrase)
        cmd = extract_command_after_wake_word(phrase)
        print(f"  Raw:        '{phrase}'")
        print(f"  Normalized: '{normalized}'")
        print(f"  Wake Word:  {has_ww} ({matched})")
        print(f"  Command:    '{cmd}'")
        print("-" * 50)
        if not has_ww or "Bimo" not in normalized:
            all_passed = False

    print(f"\nPhonetic Benchmark Result: {'[PASS] All 20 variations resolved!' if all_passed else '[FAIL] Some variations unresolved.'}")
    return all_passed


def run_gating_benchmark():
    print("\n" + "=" * 70)
    print("STRICT WAKE-WORD GATING BENCHMARK: AMBIENT SPEECH REJECTION")
    print("=" * 70)

    ambient_phrases = [
        "what is the weather today",
        "open notepad right now",
        "can you pass the water bottle",
        "did you see that new movie",
        "turn on the television",
        "I need to go shopping later",
        "hello how are you doing",
        "turn off the lights please",
    ]

    all_rejected = True
    for phrase in ambient_phrases:
        has_ww, matched = contains_wake_word(phrase)
        status = "REJECTED (ignored)" if not has_ww else "FAILED (falsely accepted)"
        print(f"  Speech: '{phrase}' -> {status}")
        if has_ww:
            all_rejected = False

    print(f"\nGating Benchmark Result: {'[PASS] 100% of ambient speech rejected!' if all_rejected else '[FAIL] False triggers.'}")
    return all_rejected


if __name__ == "__main__":
    benchmark_ok = run_phonetic_benchmark()
    gating_ok = run_gating_benchmark()

    if len(sys.argv) > 1 and sys.argv[1] == "--live":
        print("\n" + "=" * 70)
        print("STARTING LIVE MICROPHONE WAKE-WORD GATING TEST")
        print("Speak into your microphone now:")
        print("  1. Say normal conversation WITHOUT 'Bimo' -> Should be IGNORED.")
        print("  2. Say 'Bimo' or 'Be more open notepad' -> Should be ACCEPTED.")
        print("=" * 70)

        cfg = Config.from_env()
        bus = EventBus()

        def on_ignored(e: Event):
            print(f"\n[MIC EVENT] -> SPEECH IGNORED (no wake word): '{e.data.get('transcript', '')}'")

        def on_received(e: Event):
            print(f"\n[MIC EVENT] -> SPEECH ACCEPTED & DISPATCHED: '{e.data.get('transcript', '')}'")

        def on_ww(e: Event):
            print(f"\n[MIC EVENT] -> WAKE WORD CONFIRMED: '{e.data.get('model', '')}' (source: {e.data.get('source', '')})")

        bus.subscribe(EventType.SPEECH_IGNORED, on_ignored)
        bus.subscribe(EventType.SPEECH_RECEIVED, on_received)
        bus.subscribe(EventType.WAKE_WORD_DETECTED, on_ww)

        svc = create_voice_service(config=cfg.audio, event_bus=bus, require_wake_word=True)
        svc.start()

        try:
            print("Listening... Press Ctrl+C to stop.")
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            svc.stop()
            print("Live mic test stopped.")
