"""Voice input, microphone, and speech recognition subsystem for Bimo."""

from bimo.interfaces.voice import (
    AgentInputInterface,
    AudioChunk,
    BaseMicrophone,
    BaseSpeechToText,
    BaseTextToSpeech,
    BaseWakeWordDetector,
    STTResult,
    WakeWordResult,
)
from bimo.voice.agent_input import DefaultAgentInput
from bimo.voice.factory import (
    create_microphone,
    create_stt_provider,
    create_tts_provider,
    create_voice_service,
    create_wake_word_detector,
)
from bimo.voice.microphone import (
    MockMicrophone,
    PCMicrophone,
    RaspberryPiMicrophone,
)
from bimo.voice.service import VoicePipelineStage, VoiceService
from bimo.voice.stt import GoogleWebSTT, LocalWhisperSTT, MockSpeechToText, WhisperSTT, normalize_pcm
from bimo.voice.tts import MockTTS, PiperTTS, RaspberryPiTTS, WindowsTTS
from bimo.voice.vad import EnergyVAD, VADResult, calculate_rms
from bimo.voice.wakeword import MockWakeWordDetector, OpenWakeWordDetector

__all__ = [
    # Interfaces
    "BaseMicrophone",
    "BaseSpeechToText",
    "BaseTextToSpeech",
    "BaseWakeWordDetector",
    "AgentInputInterface",
    "AudioChunk",
    "STTResult",
    "WakeWordResult",
    # Microphones
    "PCMicrophone",
    "MockMicrophone",
    "RaspberryPiMicrophone",
    # Wake Word Detectors
    "OpenWakeWordDetector",
    "MockWakeWordDetector",
    # STT Providers & Normalization
    "WhisperSTT",
    "LocalWhisperSTT",
    "GoogleWebSTT",
    "MockSpeechToText",
    "normalize_pcm",
    # TTS Providers
    "PiperTTS",
    "WindowsTTS",
    "MockTTS",
    "RaspberryPiTTS",
    # VAD
    "EnergyVAD",
    "VADResult",
    "calculate_rms",
    # Service & Input
    "VoiceService",
    "VoicePipelineStage",
    "DefaultAgentInput",
    # Factory
    "create_microphone",
    "create_stt_provider",
    "create_tts_provider",
    "create_voice_service",
    "create_wake_word_detector",
]
