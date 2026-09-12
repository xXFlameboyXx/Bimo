"""Unit tests for configuration management."""

import os
import tempfile
import unittest
from pathlib import Path

from bimo.core.config import Config, load_dotenv


class TestConfig(unittest.TestCase):
    """Test suite for Config loading and .env parsing."""

    def test_default_config_values(self) -> None:
        cfg = Config()
        self.assertEqual(cfg.robot_name, "Bimo")
        self.assertEqual(cfg.environment, "development")
        self.assertEqual(cfg.display.width, 800)
        self.assertEqual(cfg.display.height, 480)
        self.assertEqual(cfg.display.backend, "simulator")
        self.assertEqual(cfg.display.idle_rotation_seconds, 300.0)
        self.assertEqual(cfg.llm.provider, "omniroute")
        self.assertEqual(cfg.llm.model, "gemini-3.8-flash")
        self.assertFalse(cfg.laptop.enabled)
        self.assertEqual(cfg.laptop.port, 8088)

    def test_load_dotenv_file(self) -> None:
        content = """
        # Comment line
        ROBOT_NAME="TestBimo"
        DISPLAY_WIDTH=800
        ENABLE_LAPTOP_CONTROL=true
        LLM_TEMPERATURE=0.2
        IDLE_ROTATION_SECONDS=15.0
        """
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as f:
            f.write(content)
            temp_path = f.name

        try:
            parsed = load_dotenv(temp_path)
            self.assertEqual(parsed["ROBOT_NAME"], "TestBimo")
            self.assertEqual(parsed["DISPLAY_WIDTH"], "800")
            self.assertEqual(parsed["ENABLE_LAPTOP_CONTROL"], "true")
            self.assertEqual(parsed["LLM_TEMPERATURE"], "0.2")
            self.assertEqual(parsed["IDLE_ROTATION_SECONDS"], "15.0")

            cfg = Config.from_env(temp_path)
            self.assertEqual(cfg.robot_name, "TestBimo")
            self.assertEqual(cfg.display.width, 800)
            self.assertTrue(cfg.laptop.enabled)
            self.assertAlmostEqual(cfg.llm.temperature, 0.2)
            self.assertAlmostEqual(cfg.display.idle_rotation_seconds, 15.0)
        finally:
            os.remove(temp_path)

    def test_os_environ_precedence(self) -> None:
        os.environ["ROBOT_NAME"] = "EnvOverrideBimo"
        try:
            cfg = Config.from_env()
            self.assertEqual(cfg.robot_name, "EnvOverrideBimo")
        finally:
            del os.environ["ROBOT_NAME"]

    def test_multi_wake_word_config_parsing(self) -> None:
        content = """
        WAKE_WORD_MODELS=" hey_jarvis , alexa ,  hey_mycroft , "
        WAKE_WORD_THRESHOLD=0.65
        WAKE_WORD_COOLDOWN=2.0
        """
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as f:
            f.write(content)
            temp_path = f.name

        try:
            cfg = Config.from_env(temp_path)
            self.assertEqual(cfg.audio.wake_word_models, ("hey_jarvis", "alexa", "hey_mycroft"))
            self.assertEqual(cfg.audio.wake_word_model, "hey_jarvis")
            self.assertAlmostEqual(cfg.audio.wake_word_threshold, 0.65)
            self.assertAlmostEqual(cfg.audio.wake_word_cooldown, 2.0)
        finally:
            os.remove(temp_path)

    def test_single_wake_word_backward_compatibility(self) -> None:
        content = """
        WAKE_WORD_MODEL="alexa"
        """
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as f:
            f.write(content)
            temp_path = f.name

        try:
            cfg = Config.from_env(temp_path)
            self.assertEqual(cfg.audio.wake_word_models, ("alexa",))
            self.assertEqual(cfg.audio.wake_word_model, "alexa")
        finally:
            os.remove(temp_path)

    def test_empty_or_whitespace_wake_word_config_fallback(self) -> None:
        content = """
        WAKE_WORD_MODELS="  ,  ,  "
        """
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as f:
            f.write(content)
            temp_path = f.name

        try:
            cfg = Config.from_env(temp_path)
            self.assertEqual(cfg.audio.wake_word_models, ("hey_jarvis",))
            self.assertEqual(cfg.audio.wake_word_model, "hey_jarvis")
        finally:
            os.remove(temp_path)

    def test_piper_tts_config_parsing(self) -> None:
        content = """
        TTS_PROVIDER="piper"
        PIPER_MODEL_PATH="custom/model.onnx"
        PIPER_CONFIG_PATH="custom/model.onnx.json"
        TTS_OUTPUT_DEVICE="Speakers (Realtek)"
        TTS_SAMPLE_RATE=22050
        TTS_SPEECH_RATE=190
        TTS_VOLUME=0.85
        """
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as f:
            f.write(content)
            temp_path = f.name

        try:
            cfg = Config.from_env(temp_path)
            self.assertEqual(cfg.audio.tts_provider, "piper")
            self.assertEqual(cfg.audio.piper_model_path, "custom/model.onnx")
            self.assertEqual(cfg.audio.piper_config_path, "custom/model.onnx.json")
            self.assertEqual(cfg.audio.tts_output_device, "Speakers (Realtek)")
            self.assertEqual(cfg.audio.tts_sample_rate, 22050)
            self.assertEqual(cfg.audio.speech_rate, 190)
            self.assertAlmostEqual(cfg.audio.volume, 0.85)
        finally:
            os.remove(temp_path)


if __name__ == "__main__":
    unittest.main()

