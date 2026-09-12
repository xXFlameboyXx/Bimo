"""Standard safe robot tools for Bimo Phase 6.

Implements:
1. robot.speak (SAFE): Speaks natural language through BaseTextToSpeech abstraction.
2. robot.set_face (SAFE): Changes facial expression via RobotStateMachine / EventBus.
3. robot.get_status (SAFE): Queries non-sensitive robot state and subsystem health.

Strictly adheres to:
- No direct hardware / LCD / GPIO imports.
- Zero computer control, shell execution, or smart-device control.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from bimo.core.events import Event, EventBus, EventType
from bimo.core.state import RobotState, RobotStateMachine
from bimo.interfaces.voice import BaseTextToSpeech
from bimo.tools.base import BaseTool, ToolParameter, ToolResult
from bimo.tools.permissions import ToolPermission

logger = logging.getLogger(__name__)

FACE_TO_STATE_MAP: dict[str, RobotState] = {
    "idle": RobotState.IDLE,
    "listening": RobotState.LISTENING,
    "thinking": RobotState.THINKING,
    "executing": RobotState.EXECUTING,
    "speaking": RobotState.SPEAKING,
    "success": RobotState.SUCCESS,
    "happy": RobotState.SUCCESS,
    "error": RobotState.ERROR,
    "sleeping": RobotState.SLEEPING,
}


class RobotSpeakTool(BaseTool):
    """Tool allowing Bimo to vocalize arbitrary text through the TTS subsystem."""

    def __init__(self, tts: BaseTextToSpeech | None = None) -> None:
        super().__init__(
            name="robot.speak",
            description="Speaks natural language text out loud through Bimo's audio speaker.",
            parameters=[
                ToolParameter(
                    name="text",
                    type="string",
                    description="The natural language utterance for Bimo to speak aloud.",
                    required=True,
                ),
            ],
            permission=ToolPermission.SAFE,
            timeout=15.0,
            strict_parameters=True,
        )
        self.tts = tts

    def execute(self, **kwargs: Any) -> ToolResult:
        text = kwargs.get("text")
        if not isinstance(text, str) or not text.strip():
            return ToolResult(
                success=False,
                error="Parameter 'text' must be a non-empty string.",
                tool_name=self.name,
            )

        clean_text = text.strip()
        if self.tts is None:
            logger.info("[robot.speak] Mock output: '%s'", clean_text)
            return ToolResult(
                success=True,
                output=f"Spoke {len(clean_text)} characters (TTS unattached)",
                tool_name=self.name,
                metadata={"spoken_text": clean_text, "char_count": len(clean_text)},
            )

        try:
            self.tts.speak(clean_text, block=True)
            return ToolResult(
                success=True,
                output=f"Spoke {len(clean_text)} characters successfully",
                tool_name=self.name,
                metadata={"spoken_text": clean_text, "char_count": len(clean_text)},
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error=f"TTS playback failed: {exc}",
                tool_name=self.name,
            )


class RobotSetFaceTool(BaseTool):
    """Tool allowing Bimo to update its facial expression safely via the state machine."""

    def __init__(
        self,
        state_machine: RobotStateMachine | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        super().__init__(
            name="robot.set_face",
            description=(
                "Changes Bimo's animated facial expression. Allowed faces: "
                "idle, listening, thinking, executing, speaking, success, happy, error, sleeping."
            ),
            parameters=[
                ToolParameter(
                    name="face",
                    type="string",
                    description="The facial expression to display.",
                    required=True,
                    enum_values=list(FACE_TO_STATE_MAP.keys()),
                ),
            ],
            permission=ToolPermission.SAFE,
            timeout=5.0,
            strict_parameters=True,
        )
        self.state_machine = state_machine
        self.event_bus = event_bus

    def execute(self, **kwargs: Any) -> ToolResult:
        face = kwargs.get("face")
        if not isinstance(face, str):
            return ToolResult(
                success=False,
                error="Parameter 'face' must be a string.",
                tool_name=self.name,
            )

        clean_face = face.strip().lower()
        if clean_face not in FACE_TO_STATE_MAP:
            return ToolResult(
                success=False,
                error=(
                    f"Unknown face '{face}'. Allowed choices: {sorted(FACE_TO_STATE_MAP.keys())}"
                ),
                tool_name=self.name,
            )

        target_state = FACE_TO_STATE_MAP[clean_face]

        if self.state_machine:
            try:
                self.state_machine.transition_to(
                    target_state, reason=f"Tool robot.set_face({clean_face})", force=True
                )
            except Exception as exc:
                return ToolResult(
                    success=False,
                    error=f"State transition failed: {exc}",
                    tool_name=self.name,
                )

        if self.event_bus:
            self.event_bus.publish(
                Event(
                    type=EventType.STATE_CHANGED,
                    data={"face": clean_face, "state": target_state.value},
                    source="robot.set_face",
                )
            )

        return ToolResult(
            success=True,
            output=f"Face updated to '{clean_face}' ({target_state.value})",
            tool_name=self.name,
            metadata={"face": clean_face, "state": target_state.value},
        )


class RobotGetStatusTool(BaseTool):
    """Tool allowing Bimo to inspect its current state and operational health."""

    def __init__(
        self,
        state_machine: RobotStateMachine | None = None,
        tts: BaseTextToSpeech | None = None,
    ) -> None:
        super().__init__(
            name="robot.get_status",
            description="Returns the current operating status and active subsystems of the Bimo robot.",
            parameters=[],
            permission=ToolPermission.SAFE,
            timeout=5.0,
            strict_parameters=True,
        )
        self.state_machine = state_machine
        self.tts = tts
        self._boot_time = time.time()

    def execute(self, **kwargs: Any) -> ToolResult:
        # Strict validation already caught any unexpected kwargs
        current_state = (
            self.state_machine.current_state.value if self.state_machine else "UNKNOWN"
        )
        tts_active = bool(self.tts.is_speaking) if self.tts else False
        uptime_seconds = round(time.time() - self._boot_time, 1)

        status_data = {
            "robot_name": "Bimo",
            "current_state": current_state,
            "uptime_seconds": uptime_seconds,
            "subsystems": {
                "state_machine": self.state_machine is not None,
                "tts": {
                    "available": self.tts is not None,
                    "is_speaking": tts_active,
                },
                "tools": {
                    "phase": 6,
                    "mode": "secure_sandbox",
                    "registered_safe_tools": ["robot.speak", "robot.set_face", "robot.get_status"],
                },
            },
        }

        return ToolResult(
            success=True,
            output=status_data,
            tool_name=self.name,
            metadata={"uptime_seconds": uptime_seconds},
        )
