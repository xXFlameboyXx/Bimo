"""Unit tests for the FaceRenderer interface.

Validates that BaseFaceRenderer enforces an abstract contract so any future
physical LCD driver on the Raspberry Pi can replace the Windows simulator
without modifying the state machine.
"""

import unittest

from bimo.core.events import Event, EventBus, EventType
from bimo.core.state import RobotState, RobotStateMachine
from bimo.rendering.base import BaseFaceRenderer


class MockPhysicalLcdRenderer(BaseFaceRenderer):
    """Simulated hardware driver for a 3.5\" SPI LCD (e.g., ST7789/ILI9486)."""

    def __init__(self, width: int = 480, height: int = 320) -> None:
        super().__init__(width, height)
        self.frames_rendered = 0
        self.status_history: list[str] = []
        self.is_closed = False

    def initialize(self) -> None:
        self._is_running = True

    def set_state(self, state: RobotState) -> None:
        self._current_state = state

    def render_frame(self) -> None:
        if self._is_running:
            self.frames_rendered += 1

    def display_status(self, text: str) -> None:
        self.status_history.append(text)

    def close(self) -> None:
        self._is_running = False
        self.is_closed = True


class TestFaceRenderingInterface(unittest.TestCase):
    """Test suite for the renderer abstraction contract."""

    def test_abstract_base_cannot_be_instantiated(self) -> None:
        with self.assertRaises(TypeError):
            # BaseFaceRenderer has abstract methods
            BaseFaceRenderer()  # type: ignore

    def test_mock_physical_lcd_implements_contract(self) -> None:
        lcd = MockPhysicalLcdRenderer(480, 320)
        self.assertEqual(lcd.width, 480)
        self.assertEqual(lcd.height, 320)
        self.assertFalse(lcd.is_running)

        lcd.initialize()
        self.assertTrue(lcd.is_running)

        # Test state setting
        lcd.set_state(RobotState.THINKING)
        self.assertEqual(lcd.current_state, RobotState.THINKING)

        # Test frame rendering
        lcd.render_frame()
        lcd.render_frame()
        self.assertEqual(lcd.frames_rendered, 2)

        # Test status overlay
        lcd.display_status("Hardware online")
        self.assertEqual(lcd.status_history, ["Hardware online"])

        lcd.close()
        self.assertTrue(lcd.is_closed)
        self.assertFalse(lcd.is_running)

    def test_state_machine_drives_physical_renderer_seamlessly(self) -> None:
        """Verify that state changes drive the renderer via EventBus without coupling."""
        event_bus = EventBus()
        state_machine = RobotStateMachine(
            initial_state=RobotState.IDLE,
            event_bus=event_bus,
            auto_subscribe_events=True,
        )

        lcd = MockPhysicalLcdRenderer(480, 320)
        lcd.initialize()

        # Connect renderer to state changes via EventBus
        def on_state_changed(event: Event) -> None:
            new_state = RobotState(event.data["to_state"])
            lcd.set_state(new_state)

        event_bus.subscribe(EventType.STATE_CHANGED, on_state_changed)

        # 1. Trigger Wake Word
        event_bus.publish(Event(type=EventType.WAKE_WORD_DETECTED))
        self.assertEqual(state_machine.current_state, RobotState.LISTENING)
        self.assertEqual(lcd.current_state, RobotState.LISTENING)

        # 2. Trigger Speech Received -> Thinking
        event_bus.publish(Event(type=EventType.SPEECH_RECEIVED))
        self.assertEqual(state_machine.current_state, RobotState.THINKING)
        self.assertEqual(lcd.current_state, RobotState.THINKING)

        # 3. Trigger Tool Started -> Executing
        event_bus.publish(Event(type=EventType.TOOL_STARTED, data={"name": "scan"}))
        self.assertEqual(state_machine.current_state, RobotState.EXECUTING)
        self.assertEqual(lcd.current_state, RobotState.EXECUTING)

        # 4. Trigger Tool Completed -> Success
        event_bus.publish(Event(type=EventType.TOOL_COMPLETED, data={"name": "scan"}))
        self.assertEqual(state_machine.current_state, RobotState.SUCCESS)
        self.assertEqual(lcd.current_state, RobotState.SUCCESS)

    def test_face_simulator_renders_all_states_without_error(self) -> None:
        """Verify that Tkinter FaceSimulator can render frames for all 8 states."""
        from bimo.rendering.face_simulator import FaceSimulator

        simulator = FaceSimulator(width=800, height=480)
        try:
            simulator.initialize()
        except Exception as e:
            if "no display name" in str(e) or "$DISPLAY" in str(e):
                self.skipTest("Tkinter display not available in headless environment")
            raise
        self.assertTrue(simulator.is_running)

        for state in RobotState:
            simulator.set_state(state)
            self.assertEqual(simulator.current_state, state)
            simulator.display_status(f"Testing state {state.value}")
            # Render multiple animation ticks for this state
            for _ in range(5):
                simulator.render_frame()

        simulator.close()
        self.assertFalse(simulator.is_running)

    def test_face_simulator_idle_categories_rotation(self) -> None:
        """Verify that IDLE sub-categories are discovered and rotate properly."""
        from bimo.rendering.face_simulator import FaceSimulator, IdleSubCategoryConfig

        custom_configs = {
            "01_blink": IdleSubCategoryConfig(duration=0.05, frame_interval=0.02),
            "02_look": IdleSubCategoryConfig(duration=0.05, frame_interval=0.02),
        }
        simulator = FaceSimulator(
            width=800,
            height=480,
            idle_rotation_time=0.05,
            idle_category_configs=custom_configs,
        )
        try:
            simulator.initialize()
        except Exception as e:
            if "no display name" in str(e) or "$DISPLAY" in str(e):
                self.skipTest("Tkinter display not available in headless environment")
            raise
        simulator.set_state(RobotState.IDLE)

        # Ensure categories were loaded
        self.assertGreaterEqual(simulator.idle_categories_count, 1)
        self.assertEqual(simulator.get_idle_category_duration("01_blink"), 0.05)
        self.assertEqual(simulator.get_idle_category_frame_interval("01_blink"), 0.02)

        initial_cat = simulator.current_idle_category
        # Render multiple frames
        for _ in range(3):
            simulator.render_frame()

        # Test manual rotation
        if simulator.idle_categories_count > 1:
            rotated_cat = simulator.rotate_idle_category(forward=True)
            self.assertNotEqual(initial_cat, rotated_cat)

        simulator.close()


if __name__ == "__main__":
    unittest.main()
