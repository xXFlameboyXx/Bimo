"""Bimo Face Simulator Application.

Integrates the EventBus, RobotStateMachine, and FaceSimulator with keyboard
shortcuts to simulate robot events, transitions, and external device connections.
"""

from __future__ import annotations

import logging
from pathlib import Path
import sys
import threading
import time

# Ensure src/ is on sys.path if invoked directly
SRC_DIR = Path(__file__).resolve().parent.parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from bimo.core.config import Config, DisplayConfig
from bimo.core.events import Event, EventBus, EventType
from bimo.core.state import RobotState, RobotStateMachine
from bimo.rendering import (
    FaceSimulator,
    IdleSubCategoryConfig,
    PhysicalFaceRenderer,
    create_face_renderer,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s",
)
logger = logging.getLogger("bimo.simulator")


class BimoSimulatorApp:
    """Simulator coordinator connecting events, state machine, and GUI renderer."""

    def __init__(self, backend: str | None = None, enable_voice: bool = False) -> None:
        self.config = Config.from_env()
        if backend:
            is_phys = backend.lower() in ("physical_lcd", "fb", "ili9486", "spi_ili9486")
            def_w = 480 if is_phys else 800
            def_h = 320 if is_phys else 480
            w = def_w if self.config.display.width in (800, 480) else self.config.display.width
            h = def_h if self.config.display.height in (480, 320) else self.config.display.height
            self.config = Config(
                robot_name=self.config.robot_name,
                environment=self.config.environment,
                display=DisplayConfig(
                    width=w,
                    height=h,
                    backend=backend.lower(),
                    fb_device=self.config.display.fb_device,
                    idle_rotation_seconds=self.config.display.idle_rotation_seconds,
                    idle_subcategories=self.config.display.idle_subcategories,
                ),
                audio=self.config.audio,
                llm=self.config.llm,
                laptop=self.config.laptop,
                logging=self.config.logging,
            )

        self.event_bus = EventBus()
        self.state_machine = RobotStateMachine(
            initial_state=RobotState.IDLE,
            event_bus=self.event_bus,
            auto_subscribe_events=True,
        )

        self.renderer = create_face_renderer(
            config=self.config.display,
            on_key_press=self.handle_key_event,
        )

        self.laptop_connected = False
        self._demo_thread: threading.Thread | None = None
        self._setup_event_subscriptions()

        self.agent_input = None
        self.voice_service = None
        if enable_voice:
            try:
                from bimo.voice import DefaultAgentInput, create_voice_service

                self.agent_input = DefaultAgentInput()
                self.voice_service = create_voice_service(
                    config=self.config.audio,
                    event_bus=self.event_bus,
                    agent_input=self.agent_input,
                )
                logger.info(
                    "Voice service initialized with %s (STT: %s)",
                    self.voice_service.microphone.name,
                    self.voice_service.stt.provider_name,
                )
            except Exception as e:
                logger.warning("Could not initialize voice subsystem: %s", e)

    def _setup_event_subscriptions(self) -> None:
        """Subscribe renderer to state and device events."""
        # 1. Update face graphics when state changes
        def on_state_changed(event: Event) -> None:
            to_state_str = event.data.get("to_state", "")
            reason = event.data.get("reason", "")
            try:
                target_state = RobotState(to_state_str)
                self.renderer.set_state(target_state)
                self.renderer.display_status(f"{to_state_str} ({reason})")
            except ValueError:
                pass

        self.event_bus.subscribe(EventType.STATE_CHANGED, on_state_changed)

        # 2. Update laptop indicator when device status changes
        def on_laptop_connected(event: Event) -> None:
            self.laptop_connected = True
            self.renderer.laptop_connected = True
            self.renderer.display_status("Laptop Connected")

        def on_laptop_disconnected(event: Event) -> None:
            self.laptop_connected = False
            self.renderer.laptop_connected = False
            self.renderer.display_status("Laptop Disconnected")

        self.event_bus.subscribe(EventType.LAPTOP_CONNECTED, on_laptop_connected)
        self.event_bus.subscribe(EventType.LAPTOP_DISCONNECTED, on_laptop_disconnected)

        # 3. Display arbitrary events
        def on_any_event(event: Event) -> None:
            if event.type != EventType.STATE_CHANGED:
                if event.type == EventType.SPEECH_RECEIVED:
                    text = event.data.get("transcript", "")
                    self.renderer.display_status(f"Heard: '{text}'")

                    # In Phase 3 (before LLM is active), return to IDLE after 3.0s display
                    def _reset_thinking() -> None:
                        time.sleep(3.0)
                        if self.state_machine.current_state == RobotState.THINKING:
                            self.state_machine.transition_to(
                                RobotState.IDLE, reason="Awaiting next speech turn"
                            )

                    threading.Thread(target=_reset_thinking, daemon=True).start()
                elif event.type == EventType.VOICE_ACTIVITY_STARTED:
                    self.renderer.display_status("Listening to you...")
                elif event.type == EventType.VOICE_ACTIVITY_STOPPED:
                    self.renderer.display_status("Processing speech...")
                elif event.type == EventType.SPEECH_ERROR:
                    self.renderer.display_status(f"Speech error: {event.data.get('error', '')}")

                    # Recover back to IDLE after 2.5s of error display
                    def _reset_error() -> None:
                        time.sleep(2.5)
                        if self.state_machine.current_state == RobotState.ERROR:
                            self.state_machine.transition_to(
                                RobotState.IDLE, reason="Recovered from speech error"
                            )

                    threading.Thread(target=_reset_error, daemon=True).start()
                else:
                    self.renderer.display_status(f"{event.type.value}")

        self.event_bus.subscribe(None, on_any_event)

    def handle_key_event(self, key: str) -> None:
        """Map keyboard keys to events and manual state changes."""
        logger.info("Key pressed: %s", key)

        # Direct state overrides: 1 to 8
        state_map = {
            "1": RobotState.IDLE,
            "2": RobotState.LISTENING,
            "3": RobotState.THINKING,
            "4": RobotState.EXECUTING,
            "5": RobotState.SPEAKING,
            "6": RobotState.SUCCESS,
            "7": RobotState.ERROR,
            "8": RobotState.SLEEPING,
        }

        if key in state_map:
            target = state_map[key]
            self.state_machine.transition_to(
                target, reason=f"Manual key '{key}'", force=True
            )
            return

        # Domain events
        match key:
            case "w":
                self.event_bus.publish(
                    Event(
                        type=EventType.WAKE_WORD_DETECTED,
                        data={"keyword": "Hey Bimo"},
                        source="keyboard_sim",
                    )
                )

            case "u":
                self.event_bus.publish(
                    Event(
                        type=EventType.USER_SPOKE,
                        data={"audio_level": 0.85},
                        source="keyboard_sim",
                    )
                )

            case "r":
                self.event_bus.publish(
                    Event(
                        type=EventType.SPEECH_RECEIVED,
                        data={"transcript": "Turn on the desk light"},
                        source="keyboard_sim",
                    )
                )

            case "a":
                self.event_bus.publish(
                    Event(
                        type=EventType.AI_STARTED,
                        data={"provider": "OmniRoute", "model": "Gemini Flash 3.8"},
                        source="keyboard_sim",
                    )
                )

            case "f":
                self.event_bus.publish(
                    Event(
                        type=EventType.AI_FINISHED,
                        data={"action": "speak", "response": "I turned on the light."},
                        source="keyboard_sim",
                    )
                )

            case "t":
                self.event_bus.publish(
                    Event(
                        type=EventType.TASK_STARTED,
                        data={"name": "control_desk_light"},
                        source="keyboard_sim",
                    )
                )

            case "c":
                self.event_bus.publish(
                    Event(
                        type=EventType.TASK_COMPLETED,
                        data={"name": "control_desk_light", "result": "ok"},
                        source="keyboard_sim",
                    )
                )

            case "x":
                self.event_bus.publish(
                    Event(
                        type=EventType.TASK_FAILED,
                        data={"error": "Device connection timed out"},
                        source="keyboard_sim",
                    )
                )

            case "l":
                if self.laptop_connected:
                    self.event_bus.publish(
                        Event(
                            type=EventType.LAPTOP_DISCONNECTED,
                            data={"name": "User-Laptop"},
                            source="keyboard_sim",
                        )
                    )
                else:
                    self.event_bus.publish(
                        Event(
                            type=EventType.LAPTOP_CONNECTED,
                            data={"name": "User-Laptop", "ip": "192.168.1.50"},
                            source="keyboard_sim",
                        )
                    )

            case "i":
                if self.state_machine.current_state != RobotState.IDLE:
                    self.state_machine.transition_to(RobotState.IDLE, reason="Manual key 'i'", force=True)
                cat = self.renderer.rotate_idle_category()
                logger.info("Rotated to IDLE sub-category: %s", cat)

            case " " | "space":
                self.run_demo_sequence()

    def run_demo_sequence(self) -> None:
        """Trigger an automated natural conversational lifecycle demo in a background thread."""
        if self._demo_thread and self._demo_thread.is_alive():
            logger.info("Demo sequence is already running.")
            return

        def _sequence() -> None:
            logger.info("Starting automated transition demo sequence...")

            # 1. Wake up
            time.sleep(0.3)
            self.event_bus.publish(
                Event(
                    type=EventType.WAKE_WORD_DETECTED,
                    data={"keyword": "Hey Bimo"},
                    source="demo",
                )
            )

            # 2. User speaks
            time.sleep(1.8)
            self.event_bus.publish(
                Event(
                    type=EventType.SPEECH_RECEIVED,
                    data={"transcript": "Can you check my laptop notifications?"},
                    source="demo",
                )
            )

            # 3. AI Thinking & Tool invocation
            time.sleep(2.0)
            self.event_bus.publish(
                Event(
                    type=EventType.TOOL_STARTED,
                    data={"name": "fetch_notifications"},
                    source="demo",
                )
            )

            # 4. Tool Execution completes successfully
            time.sleep(2.2)
            self.event_bus.publish(
                Event(
                    type=EventType.TOOL_COMPLETED,
                    data={"name": "fetch_notifications", "count": 3},
                    source="demo",
                )
            )

            # 5. Speaking response
            time.sleep(1.8)
            self.event_bus.publish(
                Event(
                    type=EventType.AI_FINISHED,
                    data={"action": "speak", "response": "You have 3 notifications."},
                    source="demo",
                )
            )

            # 6. Return to IDLE
            time.sleep(2.8)
            self.state_machine.transition_to(
                RobotState.IDLE, reason="Demo sequence completed"
            )
            logger.info("Demo sequence finished.")

        self._demo_thread = threading.Thread(target=_sequence, daemon=True)
        self._demo_thread.start()

    def run(self) -> None:
        """Launch simulator window and main event loop."""
        logger.info("Initializing Bimo Face...")
        self.renderer.initialize()
        self.renderer.set_state(self.state_machine.current_state)

        print("\n" + "=" * 60)
        print(f"  BIMO ROBOT FACE ({self.renderer.width}x{self.renderer.height})")
        print("  Displaying ONLY the robot face on the screen.")
        print("  Active IDLE Sub-Categories:")
        for cat_name, frames in self.renderer._idle_categories:
            dur = self.renderer.get_idle_category_duration(cat_name)
            spd = self.renderer.get_idle_category_frame_interval(cat_name)
            print(f"   * {cat_name:<10}: duration={dur:5.1f}s | frame_interval={spd:4.2f}s ({len(frames)} frames)")
        print("=" * 60)
        print("  Keyboard Controls (active on face window):")
        print("   [1-8] Manual States: 1=IDLE, 2=LISTEN, 3=THINK,")
        print("         4=EXECUTE, 5=SPEAK, 6=SUCCESS, 7=ERROR, 8=SLEEP")
        print("   [I]   Rotate IDLE Sub-Category (instant cycle)")
        print("   [W]   Wake Word ('Hey Bimo')")
        print("   [U]   User Spoke")
        print("   [R]   Speech Received (starts Thinking)")
        print("   [A]   AI Started")
        print("   [F]   AI Finished (starts Speaking)")
        print("   [T]   Task / Tool Started (Capturing/Executing)")
        print("   [C]   Task Completed (Success)")
        print("   [X]   Task Failed (Error)")
        print("   [L]   Toggle Laptop Connection")
        print("   [B]   Blank / Clear Screen")
        print("   [Space] Run Full Automated Demo Sequence")
        print("   [Q]   Quit & Turn Off Display")
        print("=" * 60 + "\n")

        if self.voice_service:
            try:
                self.voice_service.start()
                logger.info(
                    "Voice input subsystem started (%s). Listening for speech...",
                    self.voice_service.microphone.name,
                )
            except Exception as e:
                logger.warning("Failed to start voice service: %s", e)

        try:
            if isinstance(self.renderer, FaceSimulator):
                self.renderer.run_loop(fps=45)
            else:
                # Register termination signal handlers for graceful display shutdown
                import signal

                def _sig_cleanup(signum: int, frame: any) -> None:
                    logger.info("Signal %s received; closing face renderer...", signum)
                    if self.voice_service:
                        self.voice_service.stop()
                    self.renderer.close()
                    sys.exit(0)

                for sig in (signal.SIGINT, signal.SIGTERM):
                    try:
                        signal.signal(sig, _sig_cleanup)
                    except (ValueError, AttributeError):
                        pass
                if hasattr(signal, "SIGHUP"):
                    try:
                        signal.signal(signal.SIGHUP, _sig_cleanup)
                    except (ValueError, AttributeError):
                        pass

                # Physical LCD background rendering with interactive terminal loop
                self.renderer.start_render_thread(fps=30)
                try:
                    while self.renderer.is_running:
                        print("Bimo (Physical LCD) > ", end="", flush=True)
                        line = sys.stdin.readline()
                        if not line:
                            break
                        cmd = line.strip().lower()
                        if cmd in ("q", "quit", "exit"):
                            break
                        elif cmd in ("b", "blank", "clear"):
                            logger.info("Screen blanked to black")
                            self.renderer.clear(0)
                        elif cmd == "space":
                            self.handle_key_event(" ")
                        elif cmd:
                            self.handle_key_event(cmd)
                except (KeyboardInterrupt, EOFError):
                    pass
        finally:
            if self.voice_service:
                self.voice_service.stop()
            logger.info("Shutting down display and clearing screen to black...")
            self.renderer.close()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Bimo Face Application")
    parser.add_argument(
        "--backend",
        choices=["simulator", "physical_lcd", "fb", "ili9486", "spi_ili9486"],
        default=None,
        help="Display backend ('physical_lcd' or 'simulator')",
    )
    parser.add_argument(
        "--physical", "--lcd",
        action="store_const",
        dest="backend",
        const="physical_lcd",
        help="Shortcut to use physical LCD backend (/dev/fb1)",
    )
    parser.add_argument(
        "--simulator",
        action="store_const",
        dest="backend",
        const="simulator",
        help="Shortcut to use desktop window simulator",
    )
    parser.add_argument(
        "--voice",
        action="store_true",
        help="Enable live voice input subsystem (microphone & speech recognition)",
    )
    parser.add_argument(
        "--blank", "--clear",
        action="store_true",
        help="Immediately blank the physical LCD to black and exit",
    )
    args, _ = parser.parse_known_args()

    # Handle immediate display blanking request
    if args.blank:
        from bimo.core.process_lock import blank_physical_display

        blank_physical_display()
        print("Physical LCD (/dev/fb1) blanked to black.")
        return

    backend = args.backend
    # Smart auto-detection: if on Raspberry Pi with /dev/fb1, default to physical_lcd
    if backend is None:
        if Path("/dev/fb1").exists():
            backend = "physical_lcd"
            logger.info("Detected /dev/fb1: auto-selecting 'physical_lcd' backend")

    app = BimoSimulatorApp(backend=backend, enable_voice=args.voice)
    app.run()


if __name__ == "__main__":
    main()
