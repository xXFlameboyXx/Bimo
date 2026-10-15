"""Bimo Agent implementation for Phase 5 & Phase 6.

Orchestrates the conversational and tool execution lifecycle:
1. Receives transcribed user speech from AgentInputInterface or SPEECH_RECEIVED.
2. Updates conversation context with user query.
3. Exposes registered tools to LLMProvider as schemas (Phase 6).
4. Emits AI_STARTED, AI_FINISHED, AI_ERROR, and TOOL_* domain events.
5. Manages multi-turn tool loops: LLM -> Tool Calls -> Validation -> Permission -> Execution -> Context -> LLM.
6. Sends natural language response to TextToSpeech (Piper / Mock).
7. Enforces strict security: only registered tools execute; zero arbitrary shell/system execution.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from bimo.agent.context import ConversationContext
from bimo.core.events import Event, EventBus, EventType
from bimo.core.state import RobotState, RobotStateMachine
from bimo.interfaces.llm import LLMProvider, LLMRequest, LLMResponse
from bimo.interfaces.voice import AgentInputInterface, BaseTextToSpeech
from bimo.tools.permissions import PermissionPolicy
from bimo.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class BimoAgent(AgentInputInterface):
    """Core Bimo AI Agent coordinating conversation context, LLM, tools, and TTS output.

    Architectural separation:
    - Independent of OmniRoute HTTP implementation (uses abstract LLMProvider).
    - Independent of speaker hardware (uses abstract BaseTextToSpeech).
    - Driven by and emits domain events via EventBus.
    - Centralized permission policy and tool sandbox (Phase 6).
    """

    DEFAULT_FALLBACK_ERROR = "I am sorry, I am having trouble connecting to my brain right now."
    TOOL_UNAVAILABLE_FALLBACK = (
        "I understand your request, but real-world tool and device control is not yet available in Phase 5."
    )

    def __init__(
        self,
        llm_provider: LLMProvider,
        tts: BaseTextToSpeech | None = None,
        context: ConversationContext | None = None,
        event_bus: EventBus | None = None,
        state_machine: RobotStateMachine | None = None,
        tool_registry: ToolRegistry | None = None,
        permission_policy: PermissionPolicy | None = None,
        tools_enabled: bool = True,
        max_tool_iterations: int = 50,
        auto_speak: bool = True,
        fallback_error_message: str = DEFAULT_FALLBACK_ERROR,
    ) -> None:
        self.llm_provider = llm_provider
        self.tts = tts
        self.context = context or ConversationContext()
        self.event_bus = event_bus
        self.state_machine = state_machine
        self.tool_registry = tool_registry
        self.permission_policy = permission_policy or (tool_registry.policy if tool_registry else PermissionPolicy())
        self.tools_enabled = tools_enabled
        self.max_tool_iterations = max_tool_iterations
        self.auto_speak = auto_speak
        self.fallback_error_message = fallback_error_message

        self._history: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self._last_processed_text: str | None = None
        self._last_processed_time: float = 0.0
        self._last_completed_time: float = 0.0
        self._is_processing: bool = False
        self._last_utterance_id: str | None = None

        # Auto-subscribe to central event bus if provided
        if self.event_bus:
            self.event_bus.subscribe(EventType.SPEECH_RECEIVED, self._on_speech_received)

    @property
    def history(self) -> list[dict[str, Any]]:
        """Return history of received user turns."""
        with self._lock:
            return list(self._history)

    @property
    def last_input(self) -> str | None:
        """Return most recently received user input text."""
        with self._lock:
            return self._last_processed_text

    def _publish_event(self, event_type: EventType, data: dict[str, Any] | None = None) -> None:
        """Publish agent domain event to attached EventBus."""
        if self.event_bus:
            self.event_bus.publish(
                Event(
                    type=event_type,
                    data=data or {},
                    source="bimo_agent",
                )
            )

    def _on_speech_received(self, event: Event) -> None:
        """Event handler for SPEECH_RECEIVED from VoiceService."""
        transcript = event.data.get("transcript", "").strip()
        if not transcript:
            return

        # Check for urgent spoken interruption regardless of whether agent is processing
        from bimo.agent.cancellation import TaskInterruptManager, InterruptCommand
        cmd = TaskInterruptManager.classify(transcript)
        if cmd in (InterruptCommand.STOP, InterruptCommand.CANCEL, InterruptCommand.ABORT):
            logger.info("BimoAgent received urgent interruption command: '%s'. Stopping TTS and resetting.", transcript)
            if self.tts:
                self.tts.stop()
            if getattr(self, "_active_cancel_token", None):
                self._active_cancel_token.cancel("User interrupted")
            if self.state_machine:
                self.state_machine.transition_to(RobotState.IDLE, reason="User interrupted", force=True)
            self._publish_event(EventType.SPEECH_OUTPUT_CANCELLED, {"reason": "User interrupted", "transcript": transcript})
            with self._lock:
                self._is_processing = False
                self._active_cancel_token = None
            return
        elif cmd == InterruptCommand.PAUSE or transcript.lower() in ("wait", "pause", "hold on", "bimo wait"):
            logger.info("BimoAgent received pause command: '%s'. Pausing task and TTS.", transcript)
            if self.tts:
                self.tts.stop()
            if getattr(self, "_active_cancel_token", None):
                self._active_cancel_token.pause()
            if self.state_machine:
                self.state_machine.transition_to(RobotState.IDLE, reason="User paused", force=True)
            self._publish_event(EventType.TASK_PAUSED, {"reason": "User paused", "transcript": transcript})
            if self.auto_speak and self.tts:
                self.tts.speak("Waiting.", block=False)
            return
        elif cmd in (InterruptCommand.RESUME, InterruptCommand.CONTINUE) or transcript.lower() in ("continue", "resume", "bimo continue"):
            logger.info("BimoAgent received resume command: '%s'. Resuming task.", transcript)
            if getattr(self, "_active_cancel_token", None):
                self._active_cancel_token.resume()
            if self.state_machine:
                self.state_machine.transition_to(RobotState.EXECUTING, reason="User resumed", force=True)
            self._publish_event(EventType.TASK_RESUMED, {"reason": "User resumed", "transcript": transcript})
            if self.auto_speak and self.tts:
                self.tts.speak("Continuing.", block=False)
            return

        now = time.time()
        utterance_id = event.data.get("utterance_id")
        with self._lock:
            # 1. Do not start a new turn if one is already actively being processed
            if self._is_processing:
                logger.debug("Dropping SPEECH_RECEIVED '%s': agent is currently busy processing", transcript)
                return
            # 2. Deduplicate if this exact utterance ID was already received
            if utterance_id and utterance_id == self._last_utterance_id:
                logger.debug("Dropping duplicate utterance ID '%s'", utterance_id)
                return
            # 3. Deduplicate if same text was completed within cooldown window (5.0s)
            if transcript == self._last_processed_text and (now - self._last_completed_time) < 5.0:
                logger.debug("Dropping duplicate SPEECH_RECEIVED '%s' received within cooldown", transcript)
                return
            # Atomically lock processing state before yielding lock
            self._is_processing = True
            self._last_processed_text = transcript
            self._last_processed_time = now
            if utterance_id:
                self._last_utterance_id = utterance_id

        logger.debug("BimoAgent received SPEECH_RECEIVED event: '%s'", transcript)
        self.process_turn(transcript, metadata=event.data)

    def receive_user_input(
        self, text: str, metadata: dict[str, Any] | None = None
    ) -> None:
        """AgentInputInterface contract implementation."""
        clean_text = text.strip()
        if not clean_text:
            return

        # Check for urgent spoken interruption regardless of whether agent is processing
        from bimo.agent.cancellation import TaskInterruptManager, InterruptCommand
        cmd = TaskInterruptManager.classify(clean_text)
        if cmd in (InterruptCommand.STOP, InterruptCommand.CANCEL, InterruptCommand.ABORT):
            logger.info("BimoAgent received urgent interruption command: '%s'. Stopping TTS and resetting.", clean_text)
            if self.tts:
                self.tts.stop()
            if getattr(self, "_active_cancel_token", None):
                self._active_cancel_token.cancel("User interrupted")
            if self.state_machine:
                self.state_machine.transition_to(RobotState.IDLE, reason="User interrupted", force=True)
            self._publish_event(EventType.SPEECH_OUTPUT_CANCELLED, {"reason": "User interrupted", "transcript": clean_text})
            with self._lock:
                self._is_processing = False
                self._active_cancel_token = None
            return
        elif cmd == InterruptCommand.PAUSE or clean_text.lower() in ("wait", "pause", "hold on", "bimo wait"):
            logger.info("BimoAgent received pause command: '%s'. Pausing task and TTS.", clean_text)
            if self.tts:
                self.tts.stop()
            if getattr(self, "_active_cancel_token", None):
                self._active_cancel_token.pause()
            if self.state_machine:
                self.state_machine.transition_to(RobotState.IDLE, reason="User paused", force=True)
            self._publish_event(EventType.TASK_PAUSED, {"reason": "User paused", "transcript": clean_text})
            if self.auto_speak and self.tts:
                self.tts.speak("Waiting.", block=False)
            return
        elif cmd in (InterruptCommand.RESUME, InterruptCommand.CONTINUE) or clean_text.lower() in ("continue", "resume", "bimo continue"):
            logger.info("BimoAgent received resume command: '%s'. Resuming task.", clean_text)
            if getattr(self, "_active_cancel_token", None):
                self._active_cancel_token.resume()
            if self.state_machine:
                self.state_machine.transition_to(RobotState.EXECUTING, reason="User resumed", force=True)
            self._publish_event(EventType.TASK_RESUMED, {"reason": "User resumed", "transcript": clean_text})
            if self.auto_speak and self.tts:
                self.tts.speak("Continuing.", block=False)
            return

        now = time.time()
        utterance_id = metadata.get("utterance_id") if metadata else None
        with self._lock:
            # 1. Do not start a new turn if one is already actively being processed
            if self._is_processing:
                logger.debug("Dropping receive_user_input '%s': agent is currently busy processing", clean_text)
                return
            # 2. Deduplicate if this exact utterance ID was already received
            if utterance_id and utterance_id == self._last_utterance_id:
                logger.debug("Dropping duplicate utterance ID '%s'", utterance_id)
                return
            # 3. Deduplicate if same text was completed within cooldown window (5.0s)
            if clean_text == self._last_processed_text and (now - self._last_completed_time) < 5.0:
                logger.debug("Dropping duplicate receive_user_input '%s' received within cooldown", clean_text)
                return
            # Atomically lock processing state before yielding lock
            self._is_processing = True
            self._last_processed_text = clean_text
            self._last_processed_time = now
            if utterance_id:
                self._last_utterance_id = utterance_id

        self.process_turn(clean_text, metadata=metadata)

    def process_turn(
        self, user_text: str, metadata: dict[str, Any] | None = None
    ) -> LLMResponse:
        """Execute a full conversational reasoning turn, including tool calls if requested.

        Steps:
        1. Record input in history and update deduplication state.
        2. Signal THINKING state and emit AI_STARTED event.
        3. Append user message to ConversationContext.
        4. Loop reasoning steps (up to max_tool_iterations):
           - Request inference from LLMProvider with available tool schemas.
           - If model requests tools: validate, authorize, execute, and append results to context.
           - Transition: THINKING -> EXECUTING -> THINKING -> ...
           - Repeat until model produces final natural language output or limit reached.
        5. Emit AI_FINISHED, speak response via TTS, and transition to IDLE.
        """
        clean_text = user_text.strip()
        now = time.time()
        utterance_id = metadata.get("utterance_id") if metadata else None
        cancel_token = metadata.get("cancel_token") if metadata else None
        with self._lock:
            self._is_processing = True
            self._active_cancel_token = cancel_token
            self._last_processed_text = clean_text
            self._last_processed_time = now
            if utterance_id:
                self._last_utterance_id = utterance_id
            self._history.append({
                "text": clean_text,
                "timestamp": now,
                "metadata": metadata or {},
            })

        logger.info("BimoAgent processing user query: '%s'", clean_text)

        from bimo.agent.cancellation import TaskCancelledException
        try:
            return self._execute_turn(clean_text, metadata=metadata)
        except TaskCancelledException as exc:
            logger.info("BimoAgent turn cancelled: %s", exc)
            if self.tts:
                self.tts.stop()
            if self.state_machine:
                self.state_machine.transition_to(RobotState.IDLE, reason="Task cancelled", force=True)
            self._publish_event(
                EventType.SPEECH_OUTPUT_CANCELLED,
                {"reason": "cancelled", "transcript": clean_text},
            )
            return LLMResponse(content="Stopped.", raw={"cancelled": True})
        finally:
            with self._lock:
                self._last_completed_time = time.time()
                self._is_processing = False
                self._active_cancel_token = None

    def _execute_turn(
        self, clean_text: str, metadata: dict[str, Any] | None = None
    ) -> LLMResponse:
        """Internal execution loop for process_turn."""
        cancel_token = metadata.get("cancel_token") if metadata else self._active_cancel_token

        # Transition state machine to THINKING
        if self.state_machine and self.state_machine.current_state != RobotState.THINKING:
            self.state_machine.transition_to(
                RobotState.THINKING, reason="Agent processing user query"
            )

        # Emit AI_STARTED event
        self._publish_event(
            EventType.AI_STARTED,
            {"query": clean_text, "model": self.llm_provider.model_name},
        )

        # Add user utterance to context
        self.context.add_user_message(clean_text)

        # Prepare tool schemas if tools are enabled
        tools_schema: list[dict[str, Any]] | None = None
        if self.tools_enabled and self.tool_registry is not None:
            tools_schema = self.tool_registry.get_schemas()
            if not tools_schema:
                tools_schema = None

        iterations = 0
        final_content = ""
        last_response: LLMResponse | None = None
        computer_use_active = False
        spoke_via_tool = False

        while iterations < self.max_tool_iterations:
            if cancel_token:
                cancel_token.check_cancelled()
                cancel_token.wait_if_paused()
            # Build standardized LLMRequest
            messages = self.context.get_messages()
            llm_request = LLMRequest(messages=messages, tools=tools_schema)

            # Invoke provider
            response = self.llm_provider.generate(llm_request)
            last_response = response

            # Check for provider or network error
            if response.is_error:
                err_msg = response.error or "Unknown LLM provider error"
                # If primary provider is unreachable in live runtime (not a mock in unit tests), fall back to local engine
                if (
                    self.llm_provider.__class__.__name__ != "MockLLMProvider"
                    and any(kw in err_msg.lower() for kw in ("unreachable", "refused", "10061", "cannot connect", "connection error"))
                ):
                    try:
                        from bimo.agent.local_provider import LocalConversationalLLM
                        logger.warning(
                            "Primary LLM provider is unreachable (%s). Falling back to local conversational engine...",
                            err_msg,
                        )
                        fallback_llm = LocalConversationalLLM()
                        fallback_resp = fallback_llm.generate(llm_request)
                        if not fallback_resp.is_error:
                            response = fallback_resp
                            last_response = response
                            if not response.has_tool_calls:
                                final_content = response.content.strip()
                                self.context.add_assistant_message(final_content)
                                break
                    except Exception as fb_err:
                        logger.debug("Local fallback failed: %s", fb_err)

            if response.is_error:
                err_msg = response.error or "Unknown LLM provider error"
                logger.error("BimoAgent LLM error: %s", err_msg)

                self._publish_event(
                    EventType.AI_ERROR,
                    {"error": err_msg, "model": response.model},
                )

                if self.state_machine:
                    self.state_machine.transition_to(RobotState.ERROR, reason=f"AI error: {err_msg}", force=True)

                if self.auto_speak and self.tts and self.fallback_error_message:
                    self.tts.speak(self.fallback_error_message, block=False)

                return response

            # If response contains no tool calls, this is the final conversational turn
            if not response.has_tool_calls:
                final_content = response.content.strip()
                # Record final assistant response in context
                self.context.add_assistant_message(final_content)
                break

            # Handle tool calls
            # Safety check: are tools enabled and registry attached?
            if not self.tools_enabled or self.tool_registry is None:
                logger.info(
                    "Model requested %d tool calls: %s (Tool execution disabled or registry unattached)",
                    len(response.tool_calls),
                    [tc.name for tc in response.tool_calls],
                )
                final_content = response.content.strip() or self.TOOL_UNAVAILABLE_FALLBACK
                self.context.add_assistant_message(
                    content=final_content,
                    tool_calls=response.tool_calls,
                )
                break

            # Tool execution flow (Phase 6 & 8)
            logger.info(
                "Model requested %d tool calls (iteration %d/%d): %s",
                len(response.tool_calls),
                iterations + 1,
                self.max_tool_iterations,
                [tc.name for tc in response.tool_calls],
            )

            # Check if computer-use is involved
            has_pc_tools = any(tc.name.startswith("pc.") for tc in response.tool_calls)
            if has_pc_tools and not computer_use_active:
                computer_use_active = True
                self._publish_event(
                    EventType.PC_COMPUTER_USE_STARTED,
                    {"iteration": iterations + 1, "initial_tools": [tc.name for tc in response.tool_calls]},
                )

            self._publish_event(
                EventType.TOOL_CALL_REQUESTED,
                {
                    "tool_calls": [
                        {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                        for tc in response.tool_calls
                    ],
                    "count": len(response.tool_calls),
                },
            )

            # Transition state to EXECUTING
            if self.state_machine and self.state_machine.current_state != RobotState.EXECUTING:
                self.state_machine.transition_to(
                    RobotState.EXECUTING, reason="Executing requested robot tools"
                )

            # Record assistant turn with tool calls in context
            self.context.add_assistant_message(
                content=response.content.strip(),
                tool_calls=response.tool_calls,
            )

            # Execute each requested tool in order
            for tc in response.tool_calls:
                if cancel_token:
                    cancel_token.check_cancelled()
                    cancel_token.wait_if_paused()

                if tc.name.startswith("pc."):
                    self._publish_event(
                        EventType.PC_COMPUTER_USE_STEP,
                        {"tool": tc.name, "iteration": iterations + 1, "arguments": tc.arguments},
                    )

                self._publish_event(
                    EventType.TOOL_EXECUTION_STARTED,
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments},
                )

                # Contextual permissions check (e.g. user confirmation flag)
                exec_context = {
                    "confirmed": metadata.get("confirmed", False) if metadata else False,
                    "elevated_confirmed": metadata.get("elevated_confirmed", False) if metadata else False,
                }

                tool_result = self.tool_registry.execute(
                    tool_name=tc.name,
                    permission_policy=self.permission_policy,
                    context=exec_context,
                    **tc.arguments,
                )

                if not tool_result.success:
                    if tool_result.metadata.get("denied"):
                        self._publish_event(
                            EventType.TOOL_EXECUTION_DENIED,
                            {"id": tc.id, "name": tc.name, "error": tool_result.error},
                        )
                    self._publish_event(
                        EventType.TOOL_EXECUTION_FAILED,
                        {"id": tc.id, "name": tc.name, "error": tool_result.error},
                    )
                    if tc.name.startswith("pc."):
                        self._publish_event(
                            EventType.PC_COMPUTER_USE_FAILED,
                            {"tool": tc.name, "error": tool_result.error},
                        )
                else:
                    if tc.name == "robot.speak":
                        spoke_via_tool = True
                    self._publish_event(
                        EventType.TOOL_EXECUTION_COMPLETED,
                        {
                            "id": tc.id,
                            "name": tc.name,
                            "output": str(tool_result.output)[:200],
                        },
                    )
                    # Emit specific Phase 8 computer-use action events
                    if tc.name == "pc.screenshot":
                        self._publish_event(
                            EventType.PC_SCREENSHOT_CAPTURED,
                            {
                                "width": tool_result.metadata.get("width"),
                                "height": tool_result.metadata.get("height"),
                                "format": tool_result.metadata.get("format"),
                            },
                        )
                    elif tc.name in ("pc.move_mouse", "pc.click", "pc.scroll"):
                        self._publish_event(
                            EventType.PC_MOUSE_ACTION,
                            {"action": tc.name, "metadata": tool_result.metadata},
                        )
                    elif tc.name in ("pc.type_text", "pc.press_key"):
                        self._publish_event(
                            EventType.PC_KEYBOARD_ACTION,
                            {"action": tc.name, "metadata": tool_result.metadata},
                        )
                    elif tc.name == "pc.focus_window":
                        self._publish_event(
                            EventType.PC_WINDOW_FOCUSED,
                            {"title": tool_result.metadata.get("title")},
                        )

                # Append tool result turn into conversation context
                self.context.add_tool_result_message(
                    tool_call_id=tc.id,
                    tool_name=tc.name,
                    content=tool_result.to_json(),
                )

            # Return to THINKING state for subsequent reasoning step
            if self.state_machine and self.state_machine.current_state != RobotState.THINKING:
                self.state_machine.transition_to(
                    RobotState.THINKING, reason="Reasoning on tool execution results"
                )

            iterations += 1

        # Check if max iterations exceeded without natural language completion
        if iterations >= self.max_tool_iterations and not final_content:
            logger.warning("BimoAgent reached maximum tool iterations (%d).", self.max_tool_iterations)
            final_content = "I reached my maximum tool execution limit."
            self.context.add_assistant_message(final_content)
            if computer_use_active:
                self._publish_event(
                    EventType.PC_COMPUTER_USE_LIMIT_REACHED,
                    {"iterations": iterations, "limit": self.max_tool_iterations},
                )

        if computer_use_active:
            self._publish_event(
                EventType.PC_COMPUTER_USE_COMPLETED,
                {"iterations": iterations, "success": bool(final_content)},
            )

        # Emit AI_FINISHED event
        self._publish_event(
            EventType.AI_FINISHED,
            {
                "content": final_content,
                "model": (last_response.model if last_response else self.llm_provider.model_name),
                "action": "speak" if final_content else "idle",
                "iterations": iterations,
            },
        )

        # Dispatch speech response through TextToSpeech abstraction
        if self.auto_speak and self.tts and final_content and not spoke_via_tool:
            logger.info("BimoAgent speaking response (%d chars)", len(final_content))
            self.tts.speak(final_content, block=False)

        # Return the final LLMResponse (or reconstructed response with final content)
        if last_response and not last_response.has_tool_calls:
            return last_response

        return LLMResponse(
            content=final_content,
            tool_calls=(last_response.tool_calls if last_response else []),
            finish_reason="stop",
            model=(last_response.model if last_response else self.llm_provider.model_name),
            usage=(last_response.usage if last_response else None),
        )

    def clear_context(self) -> None:
        """Clear conversation context and input history."""
        with self._lock:
            self._history.clear()
            self._last_processed_text = None
            self._last_processed_time = 0.0
            self._last_completed_time = 0.0
            self._last_utterance_id = None
            self._is_processing = False
            self.context.clear()
            logger.info("BimoAgent context cleared.")
