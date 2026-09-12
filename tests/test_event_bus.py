"""Unit tests for the Bimo event bus."""

import unittest

from bimo.core.events import Event, EventBus, EventType


class TestEventBus(unittest.TestCase):
    """Test suite for EventBus pub/sub messaging."""

    def setUp(self) -> None:
        self.bus = EventBus(history_size=10)

    def test_publish_and_subscribe(self) -> None:
        received: list[Event] = []

        def handler(event: Event) -> None:
            received.append(event)

        self.bus.subscribe(EventType.WAKE_WORD_DETECTED, handler)

        event = Event(
            type=EventType.WAKE_WORD_DETECTED,
            data={"keyword": "hey bimo"},
            source="mic",
        )
        self.bus.publish(event)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].type, EventType.WAKE_WORD_DETECTED)
        self.assertEqual(received[0].data["keyword"], "hey bimo")
        self.assertEqual(received[0].source, "mic")

    def test_filtering_events(self) -> None:
        received: list[Event] = []

        def handler(event: Event) -> None:
            received.append(event)

        self.bus.subscribe(EventType.TASK_STARTED, handler)

        # Publish a different event
        self.bus.publish(
            Event(type=EventType.WAKE_WORD_DETECTED, data={})
        )
        self.assertEqual(len(received), 0)

        # Publish matching event
        self.bus.publish(
            Event(type=EventType.TASK_STARTED, data={"name": "test"})
        )
        self.assertEqual(len(received), 1)

    def test_catch_all_subscriber(self) -> None:
        all_events: list[Event] = []

        def on_any(event: Event) -> None:
            all_events.append(event)

        self.bus.subscribe(None, on_any)

        self.bus.publish(Event(type=EventType.WAKE_WORD_DETECTED))
        self.bus.publish(Event(type=EventType.SPEECH_RECEIVED))
        self.bus.publish(Event(type=EventType.AI_STARTED))

        self.assertEqual(len(all_events), 3)

    def test_unsubscribe(self) -> None:
        received: list[Event] = []

        def handler(event: Event) -> None:
            received.append(event)

        self.bus.subscribe(EventType.USER_SPOKE, handler)
        self.bus.publish(Event(type=EventType.USER_SPOKE))
        self.assertEqual(len(received), 1)

        unsubscribed = self.bus.unsubscribe(EventType.USER_SPOKE, handler)
        self.assertTrue(unsubscribed)

        self.bus.publish(Event(type=EventType.USER_SPOKE))
        self.assertEqual(len(received), 1)

    def test_fault_tolerance_in_subscribers(self) -> None:
        import logging

        received: list[str] = []

        def failing_handler(event: Event) -> None:
            raise RuntimeError("Handler failure simulation")

        def normal_handler(event: Event) -> None:
            received.append("success")

        self.bus.subscribe(EventType.TASK_COMPLETED, failing_handler)
        self.bus.subscribe(EventType.TASK_COMPLETED, normal_handler)

        # Suppress expected exception log during test
        logging.disable(logging.CRITICAL)
        try:
            self.bus.publish(Event(type=EventType.TASK_COMPLETED))
        finally:
            logging.disable(logging.NOTSET)

        self.assertEqual(received, ["success"])

    def test_event_history_and_limit(self) -> None:
        for i in range(15):
            self.bus.publish(
                Event(
                    type=EventType.USER_SPOKE,
                    data={"index": i},
                )
            )

        history = self.bus.get_history()
        # Max history was set to 10
        self.assertEqual(len(history), 10)
        self.assertEqual(history[-1].data["index"], 14)


if __name__ == "__main__":
    unittest.main()
