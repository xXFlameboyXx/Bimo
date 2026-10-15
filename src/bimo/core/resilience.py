"""Offline Resilience and Capability Matrix for Bimo.

Provides:
- Capability: REMOTE_LLM, REMOTE_IMAGE, PC_CONTROL, LOCAL_TTS, LOCAL_STT, LOCAL_ARTIFACT, ROBOT_LOCAL, SMART_HOME
- SystemAvailability: Evaluates internet, Windows agent, and local engines.
- CapabilityMatrix: Queries whether a desired capability is currently available or degraded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import logging
import socket
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class Capability(str, Enum):
    """Categorized Bimo capabilities."""
    REMOTE_LLM = "REMOTE_LLM"
    REMOTE_IMAGE = "REMOTE_IMAGE"
    PC_CONTROL = "PC_CONTROL"
    LOCAL_TTS = "LOCAL_TTS"
    LOCAL_STT = "LOCAL_STT"
    LOCAL_ARTIFACT = "LOCAL_ARTIFACT"
    ROBOT_LOCAL = "ROBOT_LOCAL"
    SMART_HOME = "SMART_HOME"


@dataclass
class SystemAvailability:
    """Current availability state of environment dependencies."""
    internet_available: bool = True
    windows_agent_connected: bool = False
    local_tts_available: bool = True
    local_stt_available: bool = True
    smart_home_hub_connected: bool = True

    @classmethod
    def check_internet(cls, timeout: float = 1.0) -> bool:
        """Quick check for outbound internet connectivity."""
        try:
            # Connect to 1.1.1.1:53 or 8.8.8.8:53
            socket.create_connection(("1.1.1.1", 53), timeout=timeout)
            return True
        except (OSError, socket.timeout):
            return False


class CapabilityMatrix:
    """Manages system capabilities and provides graceful degradation paths."""

    def __init__(self, availability: Optional[SystemAvailability] = None) -> None:
        self.availability = availability or SystemAvailability()

    def is_available(self, capability: Capability) -> bool:
        """Check if a capability is currently functional."""
        avail = self.availability
        match capability:
            case Capability.REMOTE_LLM | Capability.REMOTE_IMAGE:
                return avail.internet_available
            case Capability.PC_CONTROL:
                return avail.windows_agent_connected
            case Capability.LOCAL_TTS:
                return avail.local_tts_available
            case Capability.LOCAL_STT:
                return avail.local_stt_available
            case Capability.LOCAL_ARTIFACT | Capability.ROBOT_LOCAL:
                return True
            case Capability.SMART_HOME:
                return avail.smart_home_hub_connected
        return False

    def get_degradation_message(self, capability: Capability) -> str:
        """Provide a clear, human-friendly explanation when a capability is unavailable."""
        match capability:
            case Capability.REMOTE_LLM:
                return "I am currently offline and cannot reach my cloud brain. Local voice and robot commands are still available."
            case Capability.REMOTE_IMAGE:
                return "Internet connection is unavailable. I will use local diagram and chart rendering instead."
            case Capability.PC_CONTROL:
                return "Windows PC Agent is not connected. PC computer-use actions are unavailable."
            case Capability.SMART_HOME:
                return "Smart-home hub is currently unreachable."
            case _:
                return f"Capability {capability.value} is currently unavailable."
