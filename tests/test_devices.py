"""Unit tests for Device interfaces and Device Registry."""

import unittest

from bimo.core.events import Event, EventBus, EventType
from bimo.interfaces.devices import (
    BaseDevice,
    DeviceCapability,
    DeviceRegistry,
    DeviceStatus,
)


class MockLaptopNode(BaseDevice):
    """Mock external laptop device node."""

    def __init__(self, id: str = "laptop_node", name: str = "Workstation Laptop") -> None:
        super().__init__(
            id=id,
            name=name,
            capabilities={DeviceCapability.LAPTOP_CONTROL},
        )

    def connect(self) -> bool:
        self.set_status(DeviceStatus.ONLINE)
        return True

    def disconnect(self) -> None:
        self.set_status(DeviceStatus.OFFLINE)

    def health_check(self) -> bool:
        return self.status == DeviceStatus.ONLINE


class MockSmartBulb(BaseDevice):
    """Mock smart bulb peripheral."""

    def __init__(self, id: str = "smart_bulb_1", name: str = "Desk Smart Bulb") -> None:
        super().__init__(
            id=id,
            name=name,
            capabilities={DeviceCapability.LIGHTING},
        )

    def connect(self) -> bool:
        self.set_status(DeviceStatus.ONLINE)
        return True

    def disconnect(self) -> None:
        self.set_status(DeviceStatus.OFFLINE)

    def health_check(self) -> bool:
        return True


class TestDeviceRegistry(unittest.TestCase):
    """Test suite for BaseDevice and DeviceRegistry."""

    def setUp(self) -> None:
        self.event_bus = EventBus()
        self.registry = DeviceRegistry(event_bus=self.event_bus)

    def test_abstract_device_cannot_be_instantiated(self) -> None:
        with self.assertRaises(TypeError):
            BaseDevice("id", "name")  # type: ignore

    def test_register_and_get_devices(self) -> None:
        laptop = MockLaptopNode()
        bulb = MockSmartBulb()

        self.registry.register(laptop)
        self.registry.register(bulb)

        self.assertEqual(len(self.registry.list_devices()), 2)
        self.assertEqual(self.registry.get("laptop_node"), laptop)
        self.assertEqual(self.registry.get("smart_bulb_1"), bulb)

    def test_query_by_capability(self) -> None:
        laptop = MockLaptopNode()
        bulb = MockSmartBulb()
        self.registry.register(laptop)
        self.registry.register(bulb)

        lighting_devices = self.registry.get_by_capability(DeviceCapability.LIGHTING)
        self.assertEqual(len(lighting_devices), 1)
        self.assertEqual(lighting_devices[0].id, "smart_bulb_1")

        laptop_devices = self.registry.get_by_capability(DeviceCapability.LAPTOP_CONTROL)
        self.assertEqual(len(laptop_devices), 1)
        self.assertEqual(laptop_devices[0].id, "laptop_node")

    def test_update_status_and_event_publishing(self) -> None:
        laptop = MockLaptopNode()
        self.registry.register(laptop)

        dispatched_events: list[Event] = []

        def on_event(event: Event) -> None:
            dispatched_events.append(event)

        self.event_bus.subscribe(EventType.LAPTOP_CONNECTED, on_event)
        self.event_bus.subscribe(EventType.LAPTOP_DISCONNECTED, on_event)

        # 1. Connect laptop
        self.registry.update_status("laptop_node", DeviceStatus.ONLINE)
        self.assertEqual(laptop.status, DeviceStatus.ONLINE)
        self.assertEqual(len(dispatched_events), 1)
        self.assertEqual(dispatched_events[0].type, EventType.LAPTOP_CONNECTED)

        # 2. Disconnect laptop
        self.registry.update_status("laptop_node", DeviceStatus.OFFLINE)
        self.assertEqual(laptop.status, DeviceStatus.OFFLINE)
        self.assertEqual(len(dispatched_events), 2)
        self.assertEqual(dispatched_events[1].type, EventType.LAPTOP_DISCONNECTED)

    def test_unregister_device(self) -> None:
        bulb = MockSmartBulb()
        self.registry.register(bulb)
        self.assertIsNotNone(self.registry.get("smart_bulb_1"))

        unregistered = self.registry.unregister("smart_bulb_1")
        self.assertTrue(unregistered)
        self.assertIsNone(self.registry.get("smart_bulb_1"))


if __name__ == "__main__":
    unittest.main()
