"""Mock tool implementations for unit testing and deterministic verification."""

from __future__ import annotations

import time
from typing import Any

from bimo.tools.base import BaseTool, ToolParameter, ToolResult
from bimo.tools.permissions import ToolPermission


class MockEchoTool(BaseTool):
    """Simple mock tool that echoes its input argument."""

    def __init__(self, name: str = "mock.echo", permission: ToolPermission = ToolPermission.SAFE) -> None:
        super().__init__(
            name=name,
            description="Echoes the provided message.",
            parameters=[
                ToolParameter(
                    name="message",
                    type="string",
                    description="Text to echo back.",
                    required=True,
                )
            ],
            permission=permission,
            timeout=5.0,
            strict_parameters=True,
        )
        self.call_count = 0
        self.last_kwargs: dict[str, Any] = {}

    def execute(self, **kwargs: Any) -> ToolResult:
        self.call_count += 1
        self.last_kwargs = kwargs
        msg = kwargs.get("message", "")
        return ToolResult(
            success=True,
            output=f"Echo: {msg}",
            tool_name=self.name,
            metadata={"call_count": self.call_count},
        )


class MockConfirmTool(BaseTool):
    """Mock tool requiring CONFIRM permission level."""

    def __init__(self, name: str = "mock.confirm_action") -> None:
        super().__init__(
            name=name,
            description="Action requiring user confirmation.",
            parameters=[
                ToolParameter(
                    name="action_id",
                    type="string",
                    description="Identifier of action to confirm.",
                    required=True,
                )
            ],
            permission=ToolPermission.CONFIRM,
            timeout=5.0,
            strict_parameters=True,
        )
        self.executed = False

    def execute(self, **kwargs: Any) -> ToolResult:
        self.executed = True
        return ToolResult(
            success=True,
            output=f"Confirmed action {kwargs.get('action_id')} executed.",
            tool_name=self.name,
        )


class MockHighRiskTool(BaseTool):
    """Mock tool requiring HIGH_RISK permission level."""

    def __init__(self, name: str = "mock.high_risk_action") -> None:
        super().__init__(
            name=name,
            description="Destructive action requiring elevated confirmation.",
            parameters=[],
            permission=ToolPermission.HIGH_RISK,
            timeout=5.0,
            strict_parameters=True,
        )
        self.executed = False

    def execute(self, **kwargs: Any) -> ToolResult:
        self.executed = True
        return ToolResult(
            success=True,
            output="High risk action executed.",
            tool_name=self.name,
        )


class MockTimeoutTool(BaseTool):
    """Mock tool that exceeds its execution timeout."""

    def __init__(self, sleep_seconds: float = 0.5, timeout: float = 0.1) -> None:
        super().__init__(
            name="mock.timeout",
            description="Tool that intentionally takes too long.",
            parameters=[],
            permission=ToolPermission.SAFE,
            timeout=timeout,
            strict_parameters=True,
        )
        self.sleep_seconds = sleep_seconds

    def execute(self, **kwargs: Any) -> ToolResult:
        time.sleep(self.sleep_seconds)
        return ToolResult(success=True, output="Finished too late", tool_name=self.name)


class MockFailingTool(BaseTool):
    """Mock tool that raises an unexpected exception."""

    def __init__(self, error_message: str = "Hardware communication error") -> None:
        super().__init__(
            name="mock.failing",
            description="Tool that always raises an unhandled exception.",
            parameters=[],
            permission=ToolPermission.SAFE,
            timeout=5.0,
            strict_parameters=True,
        )
        self.error_message = error_message

    def execute(self, **kwargs: Any) -> ToolResult:
        raise RuntimeError(self.error_message)


class MockMultiTypeTool(BaseTool):
    """Mock tool accepting various argument types for validation testing."""

    def __init__(self) -> None:
        super().__init__(
            name="mock.multi_type",
            description="Validates different JSON parameter types.",
            parameters=[
                ToolParameter(name="str_val", type="string", description="String", required=True),
                ToolParameter(name="int_val", type="integer", description="Integer", required=True),
                ToolParameter(name="num_val", type="number", description="Float/Int", required=False),
                ToolParameter(name="bool_val", type="boolean", description="Boolean", required=False),
                ToolParameter(name="arr_val", type="array", description="List", required=False),
                ToolParameter(name="obj_val", type="object", description="Dict", required=False),
                ToolParameter(
                    name="choice",
                    type="string",
                    description="Enum choice",
                    required=False,
                    enum_values=["alpha", "beta", "gamma"],
                ),
            ],
            permission=ToolPermission.SAFE,
            timeout=5.0,
            strict_parameters=True,
        )

    def execute(self, **kwargs: Any) -> ToolResult:
        return ToolResult(success=True, output=kwargs, tool_name=self.name)
