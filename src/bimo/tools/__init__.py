"""Bimo Tools and Permission subsystem (Phase 6).

Exports:
- ToolPermission, PermissionPolicy
- BaseTool, ToolParameter, ToolResult
- ToolRegistry
- RobotSpeakTool, RobotSetFaceTool, RobotGetStatusTool
- Mock tools for testing
"""

from bimo.tools.base import BaseTool, ToolParameter, ToolResult
from bimo.tools.mock_tools import (
    MockConfirmTool,
    MockEchoTool,
    MockFailingTool,
    MockHighRiskTool,
    MockMultiTypeTool,
    MockTimeoutTool,
)
from bimo.tools.permissions import PermissionPolicy, ToolPermission
from bimo.tools.registry import ToolRegistry
from bimo.tools.robot_tools import (
    RobotGetStatusTool,
    RobotSetFaceTool,
    RobotSpeakTool,
)

__all__ = [
    "BaseTool",
    "ToolParameter",
    "ToolResult",
    "ToolPermission",
    "PermissionPolicy",
    "ToolRegistry",
    "RobotSpeakTool",
    "RobotSetFaceTool",
    "RobotGetStatusTool",
    "MockEchoTool",
    "MockConfirmTool",
    "MockHighRiskTool",
    "MockTimeoutTool",
    "MockFailingTool",
    "MockMultiTypeTool",
]
