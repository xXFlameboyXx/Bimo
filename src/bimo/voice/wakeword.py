"""Wake-word detection subsystem for Bimo.

Provides:
1. BaseWakeWordDetector: Abstract base interface for hardware/provider independent detection.
2. OpenWakeWordDetector: Local, on-device wake-word detection using openWakeWord (ONNX),
   supporting multiple concurrent wake-word models.
3. MockWakeWordDetector: Deterministic wake-word detector for unit testing and CI pipelines.
"""

from __future__ import annotations

from collections.abc import Sequence
import logging
import os
import time
from typing import Any

from bimo.core.events import EventBus, EventType
from bimo.interfaces.voice import BaseWakeWordDetector, WakeWordResult

logger = logging.getLogger(__name__)

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore

try:
    import openwakeword
    from openwakeword.model import Model as OWWModel
    HAS_OPENWAKEWORD = True
except ImportError:
    openwakeword = None  # type: ignore
    OWWModel = None  # type: ignore
    HAS_OPENWAKEWORD = False


class OpenWakeWordDetector(BaseWakeWordDetector):
    """Local, hardware-independent wake-word detector powered by openWakeWord.

    Runs entirely on-device using ONNX Runtime. Supports evaluating multiple wake-word
    models concurrently against every incoming audio frame.
    Does not transmit raw audio or transcriptions to cloud servers.
    """

    def __init__(
        self,
        model_name: str | None = None,
        model_names: Sequence[str] | None = None,
        model_path: str | None = None,
        model_paths: Sequence[str] | None = None,
        threshold: float = 0.5,
        cooldown_seconds: float = 1.5,
        event_bus: EventBus | None = None,
    ) -> None:
        super().__init__(
            model_name=model_name,
            model_names=model_names,
            threshold=threshold,
            cooldown_seconds=cooldown_seconds,
            event_bus=event_bus,
        )
        self.model_path = model_path
        if model_paths:
            self.model_paths: tuple[str, ...] = tuple(str(p).strip() for p in model_paths if str(p).strip())
        elif model_path:
            self.model_paths = (str(model_path).strip(),)
        else:
            self.model_paths = ()

        self._model: Any = None
        self._is_active = False
        self._initialize_detector()

    @property
    def provider_name(self) -> str:
        return "openwakeword"

    @property
    def is_active(self) -> bool:
        return self._is_active

    def _initialize_detector(self) -> None:
        """Initialize openWakeWord model instances for all configured models."""
        if not HAS_OPENWAKEWORD or np is None:
            logger.warning(
                "openwakeword package is not installed. OpenWakeWordDetector will remain inactive."
            )
            self._is_active = False
            self._publish_event(
                EventType.WAKE_WORD_ERROR,
                {"error": "openwakeword package not available in environment"},
            )
            return

        try:
            models_to_load: list[str] = []

            # 1. Custom model paths if provided
            if self.model_paths:
                for path in self.model_paths:
                    if not os.path.exists(path):
                        raise FileNotFoundError(f"Wake word model path does not exist: {path}")
                    models_to_load.append(path)

            # 2. Standard named models from self.model_names
            # Only download if not a file path and needed
            download_candidates: list[str] = []
            for m in self.model_names:
                if os.path.exists(m) or os.path.isabs(m):
                    if m not in models_to_load:
                        models_to_load.append(m)
                else:
                    download_candidates.append(m)
                    if m not in models_to_load:
                        models_to_load.append(m)

            if download_candidates:
                try:
                    openwakeword.utils.download_models(model_names=download_candidates)
                except Exception as dl_err:
                    logger.debug("Model download check completed: %s", dl_err)

            logger.info("Loading openWakeWord models: %s", models_to_load)
            self._model = OWWModel(wakeword_models=models_to_load)
            self._is_active = True
            logger.info(
                "OpenWakeWordDetector initialized successfully (models: %s, threshold: %.2f)",
                list(self.model_names),
                self.threshold,
            )
        except Exception as exc:
            logger.error("Failed to initialize OpenWakeWordDetector (%s): %s", self.model_names, exc)
            self._is_active = False
            self._publish_event(
                EventType.WAKE_WORD_ERROR,
                {"error": f"Failed to initialize openWakeWord models {self.model_names}: {exc}"},
            )

    def process_audio(self, audio_chunk: bytes) -> WakeWordResult:
        """Analyze a 16-bit 16kHz mono PCM chunk for wake word presence across all models."""
        if not self._is_active or self._model is None:
            return WakeWordResult(
                detected=False,
                model_name=self.model_name,
                model=self.model_name,
                score=0.0,
                confidence=0.0,
                error="Detector not active or model uninitialized",
            )

        if not audio_chunk:
            return WakeWordResult(
                detected=False,
                model_name=self.model_name,
                model=self.model_name,
                score=0.0,
                confidence=0.0,
            )

        try:
            # Convert 16-bit PCM bytes to numpy int16 array
            audio_array = np.frombuffer(audio_chunk, dtype=np.int16)

            # openWakeWord predict takes audio frames (int16 array)
            # predictions is a dict mapping model identifier to confidence score
            predictions = self._model.predict(audio_array)

            best_score = 0.0
            detected_model = self.model_name

            # Check all candidate detections
            candidates: list[tuple[float, int, str]] = []

            if isinstance(predictions, dict):
                for m_idx, target_model in enumerate(self.model_names):
                    # In openWakeWord, keys can be 'hey_jarvis_v0.1' or 'hey_jarvis'
                    for p_key, score in predictions.items():
                        score_float = float(score)
                        # Match exact or prefix (e.g. 'hey_jarvis' matches 'hey_jarvis_v0.1')
                        if target_model == p_key or p_key.startswith(f"{target_model}_") or target_model in p_key:
                            if score_float >= self.threshold:
                                candidates.append((score_float, m_idx, target_model))
                            if score_float > best_score:
                                best_score = score_float
                                detected_model = target_model

            # Multi-model selection rule:
            # If multiple models produce detections crossing threshold on the same frame:
            # 1. Primary: Highest score (confidence) descending
            # 2. Secondary: Earliest index in configured model list (tie-breaker)
            if candidates:
                candidates.sort(key=lambda item: (-item[0], item[1]))
                best_candidate_score, _, best_candidate_model = candidates[0]

                now = time.time()
                # Cooldown / debounce check to prevent repeated triggers
                if now - self._last_detection_time >= self.cooldown_seconds:
                    self._last_detection_time = now
                    logger.info(
                        "Wake word detected! Model: '%s', Score: %.3f (Threshold: %.2f)",
                        best_candidate_model,
                        best_candidate_score,
                        self.threshold,
                    )
                    self._publish_event(
                        EventType.WAKE_WORD_DETECTED,
                        {
                            "model": best_candidate_model,
                            "score": best_candidate_score,
                            "threshold": self.threshold,
                        },
                    )
                    return WakeWordResult(
                        detected=True,
                        model_name=best_candidate_model,
                        model=best_candidate_model,
                        score=best_candidate_score,
                        confidence=best_candidate_score,
                    )
                else:
                    logger.debug(
                        "Wake word score %.3f for '%s' suppressed by debounce cooldown (%.2fs remaining)",
                        best_candidate_score,
                        best_candidate_model,
                        self.cooldown_seconds - (now - self._last_detection_time),
                    )

            return WakeWordResult(
                detected=False,
                model_name=detected_model,
                model=detected_model,
                score=best_score,
                confidence=best_score,
            )
        except Exception as exc:
            logger.error("Error during openWakeWord inference: %s", exc)
            self._publish_event(
                EventType.WAKE_WORD_ERROR,
                {"error": f"Inference failure: {exc}"},
            )
            return WakeWordResult(
                detected=False,
                model_name=self.model_name,
                model=self.model_name,
                score=0.0,
                confidence=0.0,
                error=str(exc),
            )

    def reset(self) -> None:
        """Reset openWakeWord internal activation buffers."""
        if self._model is not None:
            try:
                self._model.reset()
            except Exception as e:
                logger.debug("Error resetting openWakeWord model: %s", e)


class MockWakeWordDetector(BaseWakeWordDetector):
    """Deterministic wake-word detector for automated unit tests and CI."""

    def __init__(
        self,
        model_name: str | None = None,
        model_names: Sequence[str] | None = None,
        threshold: float = 0.5,
        cooldown_seconds: float = 1.0,
        event_bus: EventBus | None = None,
        should_detect: bool = False,
        fixed_score: float = 0.95,
    ) -> None:
        super().__init__(
            model_name=model_name,
            model_names=model_names,
            threshold=threshold,
            cooldown_seconds=cooldown_seconds,
            event_bus=event_bus,
        )
        self.should_detect = should_detect
        self.fixed_score = fixed_score
        self.processed_chunks: list[bytes] = []
        self._simulated_error: str | None = None
        self._detection_queue: list[tuple[bool, float | None, str | None]] = []

    @property
    def provider_name(self) -> str:
        return "MockWakeWord"

    def set_next_detection(
        self,
        detected: bool,
        score: float | None = None,
        model: str | None = None,
    ) -> None:
        """Queue a detection flag, score, and model for subsequent process_audio calls."""
        self._detection_queue.append((detected, score, model))
        if score is not None:
            self.fixed_score = score
        if model is not None:
            self.model_name = model

    def simulate_error(self, error_message: str | None) -> None:
        """Simulate a detector failure on subsequent calls."""
        self._simulated_error = error_message

    def process_audio(self, audio_chunk: bytes) -> WakeWordResult:
        self.processed_chunks.append(audio_chunk)

        if self._simulated_error:
            self._publish_event(
                EventType.WAKE_WORD_ERROR,
                {"error": self._simulated_error},
            )
            return WakeWordResult(
                detected=False,
                model_name=self.model_name,
                model=self.model_name,
                score=0.0,
                confidence=0.0,
                error=self._simulated_error,
            )

        if not audio_chunk:
            return WakeWordResult(
                detected=False,
                model_name=self.model_name,
                model=self.model_name,
                score=0.0,
                confidence=0.0,
            )

        if self._detection_queue:
            detected, q_score, q_model = self._detection_queue.pop(0)
            score = q_score if q_score is not None else self.fixed_score
            target_model = q_model or self.model_name
        else:
            detected = self.should_detect
            score = self.fixed_score if detected else 0.05
            target_model = self.model_name

        if detected and score >= self.threshold:
            now = time.time()
            if now - self._last_detection_time >= self.cooldown_seconds:
                self._last_detection_time = now
                self._publish_event(
                    EventType.WAKE_WORD_DETECTED,
                    {"model": target_model, "score": score, "threshold": self.threshold},
                )
                return WakeWordResult(
                    detected=True,
                    model_name=target_model,
                    model=target_model,
                    score=score,
                    confidence=score,
                )
            else:
                return WakeWordResult(
                    detected=False,
                    model_name=target_model,
                    model=target_model,
                    score=score,
                    confidence=score,
                )

        return WakeWordResult(
            detected=False,
            model_name=target_model,
            model=target_model,
            score=score,
            confidence=score,
        )

    def reset(self) -> None:
        self.processed_chunks.clear()
        self._detection_queue.clear()
        self._simulated_error = None
