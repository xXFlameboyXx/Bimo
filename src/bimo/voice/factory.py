"""Factory functions for instantiating voice and speech recognition components."""

from __future__ import annotations

import logging
from typing import Any

from bimo.core.config import AudioConfig, Config
from bimo.core.events import EventBus
from bimo.interfaces.voice import (
    AgentInputInterface,
    BaseMicrophone,
    BaseSpeechToText,
    BaseTextToSpeech,
    BaseWakeWordDetector,
)
from bimo.voice.agent_input import DefaultAgentInput
from bimo.voice.microphone import (
    MockMicrophone,
    PCMicrophone,
    RaspberryPiMicrophone,
)
from bimo.voice.service import VoiceService
from bimo.voice.stt import GoogleWebSTT, MockSpeechToText, WhisperSTT
from bimo.voice.tts import MockTTS, PiperTTS, RaspberryPiTTS, WindowsTTS
from bimo.voice.vad import EnergyVAD
from bimo.voice.wakeword import MockWakeWordDetector, OpenWakeWordDetector

logger = logging.getLogger(__name__)


def create_microphone(
    config: AudioConfig | Config | None = None,
    mock: bool = False,
) -> BaseMicrophone:
    """Create the configured BaseMicrophone implementation.

    Selects PCMicrophone (development PC), RaspberryPiMicrophone (future Pi hardware),
    or MockMicrophone (automated tests).
    """
    if config is None:
        cfg = Config.from_env().audio
    elif isinstance(config, Config):
        cfg = config.audio
    else:
        cfg = config

    if mock or cfg.audio_input.lower() == "mock":
        logger.info("Instantiating MockMicrophone for testing")
        return MockMicrophone(
            sample_rate=cfg.sample_rate,
            channels=cfg.channels,
            chunk_size=cfg.chunk_size,
        )
    elif cfg.audio_input.lower() in ("pi", "rpi", "raspberry_pi"):
        logger.info("Instantiating RaspberryPiMicrophone stub")
        return RaspberryPiMicrophone(
            sample_rate=cfg.sample_rate,
            channels=cfg.channels,
            chunk_size=cfg.chunk_size,
        )
    else:
        logger.info("Instantiating PCMicrophone (live PC microphone input)")
        return PCMicrophone(
            sample_rate=cfg.sample_rate,
            channels=cfg.channels,
            chunk_size=cfg.chunk_size,
            device_index=cfg.device_index,
        )


def create_stt_provider(
    config: AudioConfig | Config | None = None,
    mock: bool = False,
) -> BaseSpeechToText:
    """Create the configured BaseSpeechToText implementation.

    Selects WhisperSTT (default local on-device), GoogleWebSTT (legacy fallback),
    or MockSpeechToText (automated tests).
    """
    if config is None:
        cfg = Config.from_env().audio
    elif isinstance(config, Config):
        cfg = config.audio
    else:
        cfg = config

    if mock or cfg.stt_provider.lower() == "mock":
        logger.info("Instantiating MockSpeechToText")
        return MockSpeechToText()
    elif cfg.stt_provider.lower() == "google":
        logger.info("Instantiating legacy GoogleWebSTT provider")
        return GoogleWebSTT()
    else:
        logger.info("Instantiating WhisperSTT provider (model: %s)", cfg.whisper_model)
        return WhisperSTT(
            model_size=cfg.whisper_model,
            model_path=cfg.whisper_model_path,
        )


def create_wake_word_detector(
    config: AudioConfig | Config | None = None,
    event_bus: EventBus | None = None,
    mock: bool = False,
) -> BaseWakeWordDetector:
    """Create the configured BaseWakeWordDetector implementation.

    Selects OpenWakeWordDetector (local on-device ONNX) or MockWakeWordDetector (automated tests).
    Supports multiple concurrent wake-word models.
    """
    if config is None:
        cfg = Config.from_env().audio
    elif isinstance(config, Config):
        cfg = config.audio
    else:
        cfg = config

    models = getattr(cfg, "wake_word_models", (cfg.wake_word_model,))

    if mock or cfg.wake_word_provider.lower() == "mock":
        logger.info("Instantiating MockWakeWordDetector for testing (models: %s)", list(models))
        return MockWakeWordDetector(
            model_names=models,
            model_name=cfg.wake_word_model,
            threshold=cfg.wake_word_threshold,
            cooldown_seconds=cfg.wake_word_cooldown,
            event_bus=event_bus,
        )
    else:
        logger.info("Instantiating OpenWakeWordDetector (models: %s)", list(models))
        return OpenWakeWordDetector(
            model_names=models,
            model_name=cfg.wake_word_model,
            threshold=cfg.wake_word_threshold,
            cooldown_seconds=cfg.wake_word_cooldown,
            event_bus=event_bus,
        )


def create_voice_service(
    config: AudioConfig | Config | None = None,
    event_bus: EventBus | None = None,
    agent_input: AgentInputInterface | None = None,
    mock: bool = False,
    include_wake_word: bool = True,
) -> VoiceService:
    """Create a fully configured VoiceService instance."""
    if config is None:
        cfg = Config.from_env().audio
    elif isinstance(config, Config):
        cfg = config.audio
    else:
        cfg = config

    mic = create_microphone(cfg, mock=mock)
    stt = create_stt_provider(cfg, mock=mock)
    ww = create_wake_word_detector(cfg, event_bus=event_bus, mock=mock) if include_wake_word else None
    ai = agent_input or DefaultAgentInput()
    vad = EnergyVAD(
        energy_threshold=cfg.energy_threshold,
        speech_time_threshold=cfg.speech_threshold_seconds,
        silence_time_threshold=cfg.silence_seconds,
        sample_rate=cfg.sample_rate,
        chunk_size=cfg.chunk_size,
    )

    return VoiceService(
        microphone=mic,
        stt=stt,
        wake_word_detector=ww,
        event_bus=event_bus,
        agent_input=ai,
        vad=vad,
        speech_timeout=cfg.speech_timeout,
        max_speech_duration=cfg.max_speech_duration,
    )


def create_tts_provider(
    config: AudioConfig | Config | None = None,
    event_bus: EventBus | None = None,
    mock: bool = False,
) -> BaseTextToSpeech:
    """Create the configured BaseTextToSpeech implementation.

    Selects PiperTTS (default local neural TTS), WindowsTTS (SAPI fallback),
    RaspberryPiTTS (future Pi hardware), or MockTTS (automated tests).
    """
    if config is None:
        cfg = Config.from_env().audio
    elif isinstance(config, Config):
        cfg = config.audio
    else:
        cfg = config

    provider_choice = cfg.tts_provider.lower() if cfg.tts_provider else cfg.audio_output.lower()

    if mock or provider_choice == "mock":
        logger.info("Instantiating MockTTS for testing")
        return MockTTS(
            voice=cfg.voice,
            speech_rate=cfg.speech_rate,
            volume=cfg.volume,
            event_bus=event_bus,
        )
    elif provider_choice in ("pi", "rpi", "raspberry_pi"):
        logger.info("Instantiating RaspberryPiTTS stub")
        return RaspberryPiTTS(
            voice=cfg.voice,
            speech_rate=cfg.speech_rate,
            volume=cfg.volume,
            event_bus=event_bus,
        )
    elif provider_choice in ("windows", "sapi"):
        logger.info("Instantiating WindowsTTS (SAPI SpVoice)")
        return WindowsTTS(
            voice=cfg.voice,
            speech_rate=cfg.speech_rate,
            volume=cfg.volume,
            event_bus=event_bus,
        )
    else:
        logger.info("Instantiating PiperTTS (model: %s)", getattr(cfg, "piper_model_path", None) or "en_GB-semaine-medium")
        return PiperTTS(
            model_path=getattr(cfg, "piper_model_path", None),
            config_path=getattr(cfg, "piper_config_path", None),
            voice=cfg.voice,
            speech_rate=cfg.speech_rate,
            volume=cfg.volume,
            output_device=getattr(cfg, "tts_output_device", "default"),
            sample_rate=getattr(cfg, "tts_sample_rate", 22050),
            event_bus=event_bus,
        )

