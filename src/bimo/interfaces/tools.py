"""Type-safe tool execution interfaces and Tool Registry for Bimo.

Provides the contract for robot actions and a central registry capable of producing
standardized JSON Schema for LLM function calling.
Re-exports from bimo.tools for full architectural continuity.
"""

from __future__ import annotations

from bimo.tools.base import BaseTool, ToolParameter, ToolResult
from bimo.tools.permissions import PermissionPolicy, ToolPermission
from bimo.tools.registry import ToolRegistry

__all__ = [
    "BaseTool",
    "ToolParameter",
    "ToolResult",
    "ToolPermission",
    "PermissionPolicy",
    "ToolRegistry",
]
