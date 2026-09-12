"""Type-safe Device interfaces and Device Registry for Bimo.

Enables uniform discovery, lifecycle management, and capability querying for
all internal and external hardware peripherals (LCD face, RGB desk lights,
smart bulb, audio hardware, and optional external laptop control node).
"""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from enum import Enum

from bimo.core.events import Event, EventBus, EventType

logger = logging.getLogger(__name__)


class DeviceStatus(str, Enum):
    """Lifecycle and connectivity status for a registered device."""

    OFFLINE = "OFFLINE"
    CONNECTING = "CONNECTING"
    ONLINE = "ONLINE"
    BUSY = "BUSY"
    ERROR = "ERROR"


class DeviceCapability(str, Enum):
    """Capabilities that hardware peripherals or remote nodes can expose."""

    DISPLAY = "DISPLAY"  # 3.5" Face LCD or visual rendering
    AUDIO_INPUT = "AUDIO_INPUT"  # Microphone / wake word stream
    AUDIO_OUTPUT = "AUDIO_OUTPUT"  # Speaker / TTS audio
    LIGHTING = "LIGHTING"  # RGB lights, desk strip, smart bulb
    LAPTOP_CONTROL = "LAPTOP_CONTROL"  # Remote control of paired laptop
    CAMERA = "CAMERA"  # Visual sensor / camera module
    SENSOR = "SENSOR"  # Distance, environmental, or touch sensors


class BaseDevice(ABC):
    """Abstract base class for all internal and external devices."""

    def __init__(
        self,
        id: str,
        name: str,
        capabilities: set[DeviceCapability] | None = None,
    ) -> None:
        self.id = id
        self.name = name
        self.capabilities = capabilities or set()
        self._status = DeviceStatus.OFFLINE

    @property
    def status(self) -> DeviceStatus:
        """Return the current device status."""
        return self._status

    def set_status(self, status: DeviceStatus) -> None:
        """Update the device status."""
        self._status = status

    def has_capability(self, capability: DeviceCapability) -> bool:
        """Check if this device provides a specific capability."""
        return capability in self.capabilities

    @abstractmethod
    def connect(self) -> bool:
        """Establish connection or initialize hardware communication."""

    @abstractmethod
    def disconnect(self) -> None:
        """Safely disconnect or shut down device communication."""

    @abstractmethod
    def health_check(self) -> bool:
        """Perform a liveness and responsiveness check on the device."""


class DeviceRegistry:
    """Central registry tracking all connected devices and their capabilities."""

    def __init__(self, event_bus: EventBus | None = None) -> None:
        self._devices: dict[str, BaseDevice] = {}
        self._lock = threading.RLock()
        self._event_bus = event_bus

    def register(self, device: BaseDevice) -> None:
        """Register a device in the registry."""
        with self._lock:
            self._devices[device.id] = device
            logger.info("Registered device: %s (%s) with capabilities: %s",
                        device.name, device.id, [c.value for c in device.capabilities])

    def unregister(self, device_id: str) -> bool:
        """Unregister a device by its identifier."""
        with self._lock:
            if device_id in self._devices:
                device = self._devices.pop(device_id)
                logger.info("Unregistered device: %s (%s)", device.name, device_id)
                return True
            return False

    def get(self, device_id: str) -> BaseDevice | None:
        """Retrieve a device by its ID."""
        with self._lock:
            return self._devices.get(device_id)

    def list_devices(self) -> list[BaseDevice]:
        """Return all registered devices."""
        with self._lock:
            return list(self._devices.values())

    def get_by_capability(self, capability: DeviceCapability) -> list[BaseDevice]:
        """Retrieve all registered devices providing a given capability."""
        with self._lock:
            return [d for d in self._devices.values() if d.has_capability(capability)]

    def update_status(self, device_id: str, new_status: DeviceStatus) -> bool:
        """Update a device's status and optionally publish a status event."""
        with self._lock:
            device = self._devices.get(device_id)
            if not device:
                return False

            old_status = device.status
            device.set_status(new_status)
            logger.info("Device %s status changed: %s -> %s",
                        device.name, old_status.value, new_status.value)

        # Notify via EventBus if this device is the laptop node
        if self._event_bus:
            if device.has_capability(DeviceCapability.LAPTOP_CONTROL):
                event_type = (
                    EventType.LAPTOP_CONNECTED
                    if new_status == DeviceStatus.ONLINE
                    else EventType.LAPTOP_DISCONNECTED
                )
                self._event_bus.publish(
                    Event(
                        type=event_type,
                        data={
                            "device_id": device.id,
                            "name": device.name,
                            "status": new_status.value,
                        },
                        source="device_registry",
                    )
                )

        return True
