"""Unit tests for the Bimo robot state machine."""

import unittest

from bimo.core.events import Event, EventBus, EventType
from bimo.core.state import RobotState, RobotStateMachine


class TestRobotStateMachine(unittest.TestCase):
    """Test suite for RobotStateMachine transitions and event handling."""

    def setUp(self) -> None:
        self.event_bus = EventBus()
        self.sm = RobotStateMachine(
            initial_state=RobotState.IDLE,
            event_bus=self.event_bus,
            auto_subscribe_events=True,
        )

    def test_all_eight_states_exist(self) -> None:
        expected_states = {
            "IDLE",
            "LISTENING",
            "THINKING",
            "EXECUTING",
            "SPEAKING",
            "SUCCESS",
            "ERROR",
            "SLEEPING",
        }
        actual_states = {s.value for s in RobotState}
        self.assertEqual(actual_states, expected_states)

    def test_initial_state_defaults_to_idle(self) -> None:
        self.assertEqual(self.sm.current_state, RobotState.IDLE)
        self.assertEqual(self.sm.previous_state, RobotState.IDLE)

    def test_valid_manual_transitions(self) -> None:
        # IDLE -> LISTENING -> THINKING -> EXECUTING -> SUCCESS -> IDLE
        self.assertTrue(self.sm.transition_to(RobotState.LISTENING, "User spoke"))
        self.assertEqual(self.sm.current_state, RobotState.LISTENING)
        self.assertEqual(self.sm.previous_state, RobotState.IDLE)

        self.assertTrue(self.sm.transition_to(RobotState.THINKING, "AI inference"))
        self.assertEqual(self.sm.current_state, RobotState.THINKING)

        self.assertTrue(self.sm.transition_to(RobotState.EXECUTING, "Tool execution"))
        self.assertEqual(self.sm.current_state, RobotState.EXECUTING)

        self.assertTrue(self.sm.transition_to(RobotState.SUCCESS, "Task completed"))
        self.assertEqual(self.sm.current_state, RobotState.SUCCESS)

        self.assertTrue(self.sm.transition_to(RobotState.IDLE, "Back to rest"))
        self.assertEqual(self.sm.current_state, RobotState.IDLE)

    def test_invalid_transition_rejected_unless_forced(self) -> None:
        import logging

        # SLEEPING directly to EXECUTING is invalid
        self.sm.transition_to(RobotState.SLEEPING, force=True)
        self.assertEqual(self.sm.current_state, RobotState.SLEEPING)

        # Natural transition should be rejected
        logging.disable(logging.CRITICAL)
        try:
            rejected = self.sm.transition_to(RobotState.EXECUTING)
        finally:
            logging.disable(logging.NOTSET)

        self.assertFalse(rejected)
        self.assertEqual(self.sm.current_state, RobotState.SLEEPING)

        # Forced transition should succeed
        forced = self.sm.transition_to(RobotState.EXECUTING, force=True)
        self.assertTrue(forced)
        self.assertEqual(self.sm.current_state, RobotState.EXECUTING)

    def test_listener_callback_triggered(self) -> None:
        transitions: list[tuple[RobotState, RobotState, str]] = []

        def on_transition(old: RobotState, new: RobotState, reason: str) -> None:
            transitions.append((old, new, reason))

        self.sm.add_listener(on_transition)
        self.sm.transition_to(RobotState.LISTENING, "Test reason")

        self.assertEqual(len(transitions), 1)
        self.assertEqual(
            transitions[0], (RobotState.IDLE, RobotState.LISTENING, "Test reason")
        )

        # Remove listener
        self.sm.remove_listener(on_transition)
        self.sm.transition_to(RobotState.THINKING)
        self.assertEqual(len(transitions), 1)

    def test_event_driven_wake_word(self) -> None:
        self.event_bus.publish(
            Event(
                type=EventType.WAKE_WORD_DETECTED,
                data={"keyword": "bimo"},
            )
        )
        self.assertEqual(self.sm.current_state, RobotState.LISTENING)

    def test_event_driven_speech_received(self) -> None:
        self.sm.transition_to(RobotState.LISTENING)
        self.event_bus.publish(
            Event(
                type=EventType.SPEECH_RECEIVED,
                data={"transcript": "hello"},
            )
        )
        self.assertEqual(self.sm.current_state, RobotState.THINKING)

    def test_event_driven_ai_finished_speech_and_tool(self) -> None:
        self.sm.transition_to(RobotState.THINKING)
        self.event_bus.publish(
            Event(
                type=EventType.AI_FINISHED,
                data={"action": "speak"},
            )
        )
        self.assertEqual(self.sm.current_state, RobotState.SPEAKING)

        self.sm.transition_to(RobotState.THINKING, force=True)
        self.event_bus.publish(
            Event(
                type=EventType.AI_FINISHED,
                data={"action": "tool"},
            )
        )
        self.assertEqual(self.sm.current_state, RobotState.EXECUTING)

    def test_event_driven_task_completion_and_failure(self) -> None:
        self.sm.transition_to(RobotState.EXECUTING)
        self.event_bus.publish(
            Event(
                type=EventType.TOOL_COMPLETED,
                data={"name": "test_tool"},
            )
        )
        self.assertEqual(self.sm.current_state, RobotState.SUCCESS)

        self.sm.transition_to(RobotState.EXECUTING, force=True)
        self.event_bus.publish(
            Event(
                type=EventType.TASK_FAILED,
                data={"error": "failed"},
            )
        )
        self.assertEqual(self.sm.current_state, RobotState.ERROR)

    def test_laptop_events_do_not_mutate_state(self) -> None:
        initial = self.sm.current_state
        self.event_bus.publish(
            Event(
                type=EventType.LAPTOP_CONNECTED,
                data={"name": "Laptop"},
            )
        )
        self.assertEqual(self.sm.current_state, initial)

        self.event_bus.publish(
            Event(
                type=EventType.LAPTOP_DISCONNECTED,
                data={"name": "Laptop"},
            )
        )
        self.assertEqual(self.sm.current_state, initial)


if __name__ == "__main__":
    unittest.main()
