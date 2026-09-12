"""Configuration management for Bimo.

Loads settings from environment variables and optional .env files into
type-safe dataclasses with validation and sensible defaults for Raspberry Pi 3A+.
Zero external dependencies.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def load_dotenv(path: Path | str = ".env") -> dict[str, str]:
    """Parse a simple .env file into a dictionary without third-party dependencies."""
    env_file = Path(path)
    loaded: dict[str, str] = {}

    if not env_file.is_file():
        return loaded

    try:
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                # Skip comments and empty lines
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip()
                    # Remove surrounding quotes if present
                    if (val.startswith('"') and val.endswith('"')) or (
                        val.startswith("'") and val.endswith("'")
                    ):
                        val = val[1:-1]
                    loaded[key] = val
    except Exception as e:
        logger.warning("Could not read .env file at %s: %s", env_file, e)

    return loaded


@dataclass(frozen=True)
class DisplayConfig:
    """Display and face rendering configuration."""

    width: int = 800
    height: int = 480
    backend: str = "simulator"  # "simulator", "physical_lcd", "fb", "spi_ili9486"
    fb_device: str = "/dev/fb1"
    idle_rotation_seconds: float = 300.0
    idle_subcategories: dict[str, dict[str, float]] = field(
        default_factory=lambda: {
            "01_blink": {"duration": 300.0, "frame_interval": 0.80},
            "02_look": {"duration": 300.0, "frame_interval": 0.80},
            "03_sleep": {"duration": 300.0, "frame_interval": 0.80},
            "04_glance": {"duration": 300.0, "frame_interval": 0.80},
        }
    )


@dataclass(frozen=True)
class LLMConfig:
    """AI / LLM routing configuration."""

    provider: str = "omniroute"
    model: str = "gemini-3.8-flash"
    api_key: str = ""
    base_url: str = "http://localhost:20128"
    timeout_seconds: float = 30.0
    temperature: float = 0.7
    max_history: int = 20
    tools_enabled: bool = True
    max_tool_iterations: int = 8


@dataclass(frozen=True)
class PCAgentConfig:
    """Windows PC Agent and LAN remote endpoint settings."""

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8088
    shared_secret: str = ""
    connect_timeout: float = 3.0
    request_timeout: float = 10.0
    max_clock_skew: float = 30.0
    allowed_apps: tuple[str, ...] = ("notepad", "calculator", "explorer")

    # Phase 8: Controlled Computer Use settings
    computer_use_enabled: bool = True
    max_computer_use_steps: int = 12
    screenshot_max_width: int = 1920
    screenshot_max_height: int = 1080
    screenshot_max_bytes: int = 1_500_000
    max_type_text_length: int = 1000
    max_scroll: int = 1000


# Backward compatibility alias
LaptopConfig = PCAgentConfig


@dataclass(frozen=True)
class LoggingConfig:
    """Logging infrastructure configuration."""

    level: str = "INFO"
    log_to_file: bool = True
    log_dir: str = "logs"
    max_bytes: int = 1_048_576  # 1 MB (keeps SD card safe on Pi)
    backup_count: int = 3


@dataclass(frozen=True)
class AudioConfig:
    """Microphone, VAD, Wake-word and STT configuration."""

    audio_input: str = "pc"  # "pc", "mock", "pi"
    audio_output: str = "windows"  # "windows", "mock", "pi"
    stt_provider: str = "whisper"  # "whisper", "google", "mock"
    tts_provider: str = "piper"  # "piper", "windows", "mock", "pi"
    whisper_model: str = "tiny.en"
    whisper_model_path: str | None = None
    wake_word_provider: str = "openwakeword"  # "openwakeword", "mock"
    wake_word_models: tuple[str, ...] = ("hey_jarvis",)
    wake_word_model: str = "hey_jarvis"
    wake_word_threshold: float = 0.5
    wake_word_cooldown: float = 1.5
    speech_timeout: float = 5.0
    max_speech_duration: float = 10.0
    piper_model_path: str | None = None
    piper_config_path: str | None = None
    tts_output_device: str = "default"
    tts_sample_rate: int = 22050
    voice: str | None = None
    speech_rate: int = 175
    volume: float = 1.0
    sample_rate: int = 16000
    channels: int = 1
    chunk_size: int = 1024
    energy_threshold: float = 110.0
    silence_seconds: float = 1.25
    speech_threshold_seconds: float = 0.04
    device_index: int | str | None = None


@dataclass(frozen=True)
class Config:
    """Master configuration for Bimo robot."""

    robot_name: str = "Bimo"
    environment: str = "development"
    display: DisplayConfig = field(default_factory=DisplayConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    laptop: PCAgentConfig = field(default_factory=PCAgentConfig)
    pc_agent: PCAgentConfig = field(default_factory=PCAgentConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    @classmethod
    def from_env(cls, env_path: Path | str | None = None) -> Config:
        """Create Config instance populated from .env file and environment variables."""
        # Load .env file first
        env_dict: dict[str, str] = {}
        if env_path is not None:
            env_dict.update(load_dotenv(env_path))
        else:
            # Default search: check cwd/.env or root directory/.env
            candidate = Path(".env")
            if candidate.exists():
                env_dict.update(load_dotenv(candidate))

        def get_val(key: str, default: Any) -> Any:
            # OS environment variables take precedence over .env file
            return os.environ.get(key, env_dict.get(key, default))

        def get_bool(key: str, default: bool) -> bool:
            v = str(get_val(key, default)).lower()
            return v in ("1", "true", "yes", "on")

        def get_int(key: str, default: int) -> int:
            try:
                return int(get_val(key, default))
            except (ValueError, TypeError):
                return default

        def get_float(key: str, default: float) -> float:
            try:
                return float(get_val(key, default))
            except (ValueError, TypeError):
                return default

        backend_val = str(get_val("DISPLAY_BACKEND", "simulator")).lower()
        # Default physical LCD resolution is 480x320; simulator is 800x480
        is_physical = backend_val in ("physical_lcd", "fb", "ili9486", "spi_ili9486")
        default_w = 480 if is_physical else 800
        default_h = 320 if is_physical else 480

        def_display = DisplayConfig()
        display = DisplayConfig(
            width=get_int("DISPLAY_WIDTH", default_w),
            height=get_int("DISPLAY_HEIGHT", default_h),
            backend=backend_val,
            fb_device=str(get_val("DISPLAY_FB_DEVICE", def_display.fb_device)),
            idle_rotation_seconds=get_float("IDLE_ROTATION_SECONDS", def_display.idle_rotation_seconds),
            idle_subcategories=def_display.idle_subcategories,
        )

        base_url = get_val("OMNIROUTE_BASE_URL", None) or get_val(
            "LLM_BASE_URL", "http://localhost:20128"
        )

        llm = LLMConfig(
            provider=str(get_val("LLM_PROVIDER", "omniroute")),
            model=str(get_val("LLM_MODEL", "gemini-3.8-flash")),
            api_key=str(get_val("LLM_API_KEY", "")),
            base_url=str(base_url),
            timeout_seconds=get_float("LLM_TIMEOUT", 30.0),
            temperature=get_float("LLM_TEMPERATURE", 0.7),
            max_history=get_int("LLM_MAX_HISTORY", 20),
            tools_enabled=get_bool("TOOLS_ENABLED", True),
            max_tool_iterations=get_int("MAX_TOOL_ITERATIONS", 8),
        )

        # Parse PC / Laptop agent settings
        pc_enabled = get_bool("PC_AGENT_ENABLED", get_bool("ENABLE_LAPTOP_CONTROL", False))
        pc_host = str(get_val("BIMO_PC_AGENT_HOST", get_val("PC_AGENT_HOST", get_val("LAPTOP_HOST", "192.168.1.50"))))

        pc_port_val = get_val("BIMO_PC_AGENT_PORT", None)
        if pc_port_val is None:
            pc_port_val = get_val("PC_AGENT_PORT", None)
        if pc_port_val is None:
            pc_port_val = get_val("LAPTOP_PORT", 8088)
        pc_port = int(pc_port_val)

        pc_secret = str(get_val("PC_AGENT_SHARED_SECRET", ""))
        pc_connect_timeout = get_float("PC_AGENT_CONNECT_TIMEOUT", 3.0)
        pc_request_timeout = get_float("PC_AGENT_REQUEST_TIMEOUT", 10.0)
        pc_max_clock_skew = get_float("PC_AGENT_MAX_CLOCK_SKEW", 30.0)

        apps_val = get_val("PC_ALLOWED_APPS", "notepad,calculator,explorer")
        allowed_apps = tuple(
            a.strip().lower() for a in str(apps_val).split(",") if a.strip()
        )

        cu_enabled = get_bool("COMPUTER_USE_ENABLED", True)
        max_cu_steps = get_int("MAX_COMPUTER_USE_STEPS", 12)
        ss_max_w = get_int("PC_SCREENSHOT_MAX_WIDTH", 1920)
        ss_max_h = get_int("PC_SCREENSHOT_MAX_HEIGHT", 1080)
        ss_max_b = get_int("PC_SCREENSHOT_MAX_BYTES", 1_500_000)
        max_type_len = get_int("PC_MAX_TYPE_TEXT_LENGTH", 1000)
        max_sc = get_int("PC_MAX_SCROLL", 1000)

        laptop = PCAgentConfig(
            enabled=pc_enabled,
            host=pc_host,
            port=pc_port,
            shared_secret=pc_secret,
            connect_timeout=pc_connect_timeout,
            request_timeout=pc_request_timeout,
            max_clock_skew=pc_max_clock_skew,
            allowed_apps=allowed_apps,
            computer_use_enabled=cu_enabled,
            max_computer_use_steps=max_cu_steps,
            screenshot_max_width=ss_max_w,
            screenshot_max_height=ss_max_h,
            screenshot_max_bytes=ss_max_b,
            max_type_text_length=max_type_len,
            max_scroll=max_sc,
        )

        logging_cfg = LoggingConfig(
            level=str(get_val("LOG_LEVEL", "INFO")).upper(),
            log_to_file=get_bool("LOG_TO_FILE", True),
            log_dir=str(get_val("LOG_DIR", "logs")),
            max_bytes=get_int("LOG_MAX_BYTES", 1_048_576),
            backup_count=get_int("LOG_BACKUP_COUNT", 3),
        )

        models_val = get_val("WAKE_WORD_MODELS", None)
        single_model_val = get_val("WAKE_WORD_MODEL", None)

        parsed_models: list[str] = []
        if models_val is not None:
            for part in str(models_val).split(","):
                clean = part.strip()
                if clean and clean not in parsed_models:
                    parsed_models.append(clean)

        if not parsed_models and single_model_val is not None:
            clean = str(single_model_val).strip()
            if clean:
                parsed_models.append(clean)

        if not parsed_models:
            parsed_models = ["hey_jarvis"]

        wake_word_models_tuple = tuple(parsed_models)
        primary_wake_word_model = wake_word_models_tuple[0]

        audio_cfg = AudioConfig(
            audio_input=str(get_val("AUDIO_INPUT", "pc")).lower(),
            audio_output=str(get_val("AUDIO_OUTPUT", "windows")).lower(),
            stt_provider=str(get_val("STT_PROVIDER", "whisper")).lower(),
            tts_provider=str(get_val("TTS_PROVIDER", "piper")).lower(),
            whisper_model=str(get_val("WHISPER_MODEL", "tiny.en")),
            whisper_model_path=get_val("WHISPER_MODEL_PATH", None),
            wake_word_provider=str(get_val("WAKE_WORD_PROVIDER", "openwakeword")).lower(),
            wake_word_models=wake_word_models_tuple,
            wake_word_model=primary_wake_word_model,
            wake_word_threshold=get_float("WAKE_WORD_THRESHOLD", 0.5),
            wake_word_cooldown=get_float("WAKE_WORD_COOLDOWN", 1.5),
            speech_timeout=get_float("SPEECH_TIMEOUT", 5.0),
            max_speech_duration=get_float("MAX_SPEECH_DURATION", 10.0),
            piper_model_path=get_val("PIPER_MODEL_PATH", None),
            piper_config_path=get_val("PIPER_CONFIG_PATH", None),
            tts_output_device=str(get_val("TTS_OUTPUT_DEVICE", "default")),
            tts_sample_rate=get_int("TTS_SAMPLE_RATE", 22050),
            voice=get_val("TTS_VOICE", None),
            speech_rate=get_int("TTS_SPEECH_RATE", 175),
            volume=get_float("TTS_VOLUME", 1.0),
            sample_rate=get_int("AUDIO_SAMPLE_RATE", 16000),
            channels=get_int("AUDIO_CHANNELS", 1),
            chunk_size=get_int("AUDIO_CHUNK_SIZE", 1024),
            energy_threshold=get_float("AUDIO_ENERGY_THRESHOLD", 110.0),
            silence_seconds=get_float("AUDIO_SILENCE_SECONDS", 1.25),
            speech_threshold_seconds=get_float("AUDIO_SPEECH_THRESHOLD", 0.04),
            device_index=get_val("AUDIO_DEVICE_INDEX", None),
        )

        return cls(
            robot_name=str(get_val("ROBOT_NAME", "Bimo")),
            environment=str(get_val("ENVIRONMENT", "development")),
            display=display,
            audio=audio_cfg,
            llm=llm,
            laptop=laptop,
            pc_agent=laptop,
            logging=logging_cfg,
        )
