"""Type-safe Device interfaces and Device Registry for Bimo.

Enables uniform discovery, lifecycle management, and capability querying for
all internal and external hardware peripherals (LCD face, RGB desk lights,
smart bulb, audio hardware, and optional external laptop control node) as well
as Refinement 12 Smart-Home and RGB device control.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum
import logging
import threading
from typing import Any, Dict, List, Optional, Set

from bimo.core.events import Event, EventBus, EventType

logger = logging.getLogger(__name__)


class DeviceStatus(str, Enum):
    """Lifecycle and connectivity status for a registered device."""

    OFFLINE = "OFFLINE"
    CONNECTING = "CONNECTING"
    ONLINE = "ONLINE"
    BUSY = "BUSY"
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"


class DeviceCapability(str, Enum):
    """Capabilities that hardware peripherals or smart devices expose."""

    DISPLAY = "DISPLAY"  # 3.5" Face LCD or visual rendering
    AUDIO_INPUT = "AUDIO_INPUT"  # Microphone / wake word stream
    AUDIO_OUTPUT = "AUDIO_OUTPUT"  # Speaker / TTS audio
    LIGHTING = "LIGHTING"  # RGB lights, desk strip, smart bulb
    LAPTOP_CONTROL = "LAPTOP_CONTROL"  # Remote control of paired laptop
    CAMERA = "CAMERA"  # Visual sensor / camera module
    SENSOR = "SENSOR"  # Distance, environmental, or touch sensors

    # Smart-home control capabilities
    POWER = "POWER"
    BRIGHTNESS = "BRIGHTNESS"
    COLOR = "COLOR"
    COLOR_TEMPERATURE = "COLOR_TEMPERATURE"
    SCENE = "SCENE"


class DeviceType(str, Enum):
    """Categorization of connected physical and smart-home devices."""
    LIGHT = "LIGHT"
    RGB_STRIP = "RGB_STRIP"
    SWITCH = "SWITCH"
    SENSOR = "SENSOR"
    ROBOT_HARDWARE = "ROBOT_HARDWARE"


class BaseDevice(abc.ABC):
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
    def device_id(self) -> str:
        return self.id

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

    @abc.abstractmethod
    def connect(self) -> bool:
        """Establish connection or initialize hardware communication."""

    @abc.abstractmethod
    def disconnect(self) -> None:
        """Safely disconnect or shut down device communication."""

    @abc.abstractmethod
    def health_check(self) -> bool:
        """Perform a liveness and responsiveness check on the device."""


@dataclass
class DeviceState:
    """Current operational state of a smart device."""
    power: bool = False
    brightness: int = 100  # 0 - 100
    color_rgb: Optional[tuple[int, int, int]] = None
    color_temp_k: Optional[int] = None
    active_scene: Optional[str] = None
    online: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "power": self.power,
            "brightness": self.brightness,
            "color_rgb": list(self.color_rgb) if self.color_rgb else None,
            "color_temp_k": self.color_temp_k,
            "active_scene": self.active_scene,
            "online": self.online,
        }


@dataclass
class Device:
    """An inspectable, controllable smart device entity (Refinement 12)."""
    device_id: str
    name: str
    device_type: DeviceType = DeviceType.LIGHT
    capabilities: List[DeviceCapability] = field(default_factory=list)
    state: DeviceState = field(default_factory=DeviceState)
    room: Optional[str] = None

    @property
    def id(self) -> str:
        return self.device_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_id": self.device_id,
            "name": self.name,
            "device_type": self.device_type.value,
            "capabilities": [c.value for c in self.capabilities],
            "state": self.state.to_dict(),
            "room": self.room,
        }


class DeviceAdapter(abc.ABC):
    """Abstract driver interface for smart devices."""

    @abc.abstractmethod
    def set_power(self, device_id: str, power: bool) -> bool:
        pass

    @abc.abstractmethod
    def set_brightness(self, device_id: str, brightness: int) -> bool:
        pass

    @abc.abstractmethod
    def set_color(self, device_id: str, r: int, g: int, b: int) -> bool:
        pass


class MockDeviceAdapter(DeviceAdapter):
    """Deterministic mock adapter for testing smart device control."""

    def __init__(self, registry: Optional[DeviceRegistry] = None) -> None:
        self.registry = registry

    def set_power(self, device_id: str, power: bool) -> bool:
        if self.registry:
            dev = self.registry.get_smart_device(device_id)
            if dev:
                dev.state.power = power
                return True
        return True

    def set_brightness(self, device_id: str, brightness: int) -> bool:
        if self.registry:
            dev = self.registry.get_smart_device(device_id)
            if dev:
                dev.state.brightness = max(0, min(100, brightness))
                return True
        return True

    def set_color(self, device_id: str, r: int, g: int, b: int) -> bool:
        if self.registry:
            dev = self.registry.get_smart_device(device_id)
            if dev:
                dev.state.color_rgb = (r, g, b)
                return True
        return True


class DeviceRegistry:
    """Central registry tracking all connected devices and smart home peripherals."""

    def __init__(self, event_bus: EventBus | None = None) -> None:
        self._devices: dict[str, BaseDevice] = {}
        self._smart_devices: dict[str, Device] = {}
        self._lock = threading.RLock()
        self._event_bus = event_bus
        self._adapter: DeviceAdapter = MockDeviceAdapter(self)

    def set_adapter(self, adapter: DeviceAdapter) -> None:
        self._adapter = adapter

    # ---- BaseDevice API (Phases 0-8 compatibility) ----
    def register(self, device: BaseDevice) -> None:
        """Register a hardware device in the registry."""
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
        """Return all registered hardware devices."""
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

    # ---- Smart Device API (Refinement 12) ----
    def register_device(self, device: Device) -> None:
        """Register a smart-home device."""
        with self._lock:
            self._smart_devices[device.device_id] = device
            logger.info("Registered smart device: %s (%s)", device.name, device.device_id)

    def get_smart_device(self, device_id: str) -> Optional[Device]:
        with self._lock:
            return self._smart_devices.get(device_id)

    def get_device(self, device_id: str) -> Optional[Device]:
        """Alias for get_smart_device."""
        return self.get_smart_device(device_id)

    def list_smart_devices(self) -> List[Device]:
        with self._lock:
            return list(self._smart_devices.values())

    def find_devices_by_name(self, query: str) -> List[Device]:
        clean = query.strip().lower()
        with self._lock:
            return [d for d in self._smart_devices.values() if clean in d.name.lower() or (d.room and clean in d.room.lower())]

    def set_power(self, device_id: str, power: bool) -> bool:
        dev = self.get_smart_device(device_id)
        if not dev or DeviceCapability.POWER not in dev.capabilities:
            return False
        return self._adapter.set_power(device_id, power)

    def set_brightness(self, device_id: str, brightness: int) -> bool:
        dev = self.get_smart_device(device_id)
        if not dev or DeviceCapability.BRIGHTNESS not in dev.capabilities:
            return False
        return self._adapter.set_brightness(device_id, brightness)

    def set_color(self, device_id: str, r: int, g: int, b: int) -> bool:
        dev = self.get_smart_device(device_id)
        if not dev or DeviceCapability.COLOR not in dev.capabilities:
            return False
        return self._adapter.set_color(device_id, r, g, b)

    def activate_scene(self, scene_name: str) -> Dict[str, bool]:
        """Activate a named scene across applicable devices."""
        results: Dict[str, bool] = {}
        clean_scene = scene_name.strip().lower()

        with self._lock:
            devices = list(self._smart_devices.values())

        if clean_scene in ("warm", "study focus", "study_focus"):
            for dev in devices:
                if DeviceCapability.COLOR in dev.capabilities:
                    results[dev.device_id] = self.set_color(dev.device_id, 255, 180, 100)
                if DeviceCapability.BRIGHTNESS in dev.capabilities:
                    self.set_brightness(dev.device_id, 80)
                dev.state.active_scene = scene_name

        elif clean_scene in ("movie", "dim"):
            for dev in devices:
                if DeviceCapability.BRIGHTNESS in dev.capabilities:
                    results[dev.device_id] = self.set_brightness(dev.device_id, 20)
                if DeviceCapability.COLOR in dev.capabilities:
                    self.set_color(dev.device_id, 100, 50, 150)
                dev.state.active_scene = scene_name

        elif clean_scene in ("night", "off"):
            for dev in devices:
                results[dev.device_id] = self.set_power(dev.device_id, False)
                dev.state.active_scene = scene_name

        return results
