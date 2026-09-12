"""Central deterministic Tool Registry and executor for Bimo.

Ensures strict registration integrity, deterministic schema export,
centralized permission checks, argument validation, and safe execution.
"""

from __future__ import annotations

import concurrent.futures
import logging
import re
import threading
from typing import Any

from bimo.tools.base import BaseTool, ToolResult
from bimo.tools.permissions import PermissionPolicy, ToolPermission

logger = logging.getLogger(__name__)

# Pattern for valid tool names: alphanumeric, dots, and underscores only
TOOL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_.]+$")


class ToolRegistry:
    """Central deterministic registry of tools available to Bimo and the LLM."""

    def __init__(self, policy: PermissionPolicy | None = None) -> None:
        self._tools: dict[str, BaseTool] = {}
        self._policy = policy or PermissionPolicy()
        self._lock = threading.RLock()

    @property
    def policy(self) -> PermissionPolicy:
        """Return the attached permission policy engine."""
        return self._policy

    def register(self, tool: BaseTool, overwrite: bool = False) -> None:
        """Register a tool instance.

        Validates:
        - tool must be an instance of BaseTool.
        - tool name must be a valid identifier.
        - tool name must be unique unless overwrite is explicitly True.
        """
        if not isinstance(tool, BaseTool):
            raise TypeError(f"Expected BaseTool instance, got {type(tool).__name__}.")

        name = tool.name.strip()
        if not name:
            raise ValueError("Tool name cannot be empty.")

        if not TOOL_NAME_PATTERN.match(name):
            raise ValueError(
                f"Invalid tool name '{name}'. Names must contain only letters, numbers, dots, and underscores."
            )

        with self._lock:
            if name in self._tools and not overwrite:
                raise ValueError(f"Tool '{name}' is already registered. Set overwrite=True to replace.")

            self._tools[name] = tool
            logger.debug("Registered tool: %s (permission: %s)", name, tool.permission.value)

    def unregister(self, tool_name: str) -> bool:
        """Unregister a tool by name. Returns True if removed."""
        with self._lock:
            if tool_name in self._tools:
                del self._tools[tool_name]
                logger.debug("Unregistered tool: %s", tool_name)
                return True
            return False

    def get(self, tool_name: str) -> BaseTool | None:
        """Retrieve a tool by name, supporting dot/underscore normalization."""
        with self._lock:
            if tool_name in self._tools:
                return self._tools[tool_name]
            dotted = tool_name.replace("_", ".")
            if dotted in self._tools:
                return self._tools[dotted]
            underscored = tool_name.replace(".", "_")
            for name, tool in self._tools.items():
                if name.replace(".", "_") == underscored:
                    return tool
            return None

    def has(self, tool_name: str) -> bool:
        """Check whether a tool is registered."""
        return self.get(tool_name) is not None

    def list_tools(self) -> list[BaseTool]:
        """Return all registered tools in deterministic alphabetical order by name."""
        with self._lock:
            return sorted(self._tools.values(), key=lambda t: t.name)

    def list_names(self) -> list[str]:
        """Return all registered tool names in deterministic alphabetical order."""
        with self._lock:
            return sorted(self._tools.keys())

    def get_schemas(self) -> list[dict[str, Any]]:
        """Export all registered tools as function schemas in deterministic order."""
        with self._lock:
            return [tool.get_schema() for tool in self.list_tools()]

    def clear(self) -> None:
        """Clear all registered tools from the registry."""
        with self._lock:
            self._tools.clear()
            logger.debug("ToolRegistry cleared.")

    def execute(
        self,
        tool_name: str,
        permission_policy: PermissionPolicy | None = None,
        context: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> ToolResult:
        """Safely validate and execute a tool.

        Execution pipeline:
        1. Find tool in registry (reject unknown tools).
        2. Validate arguments against parameter schema.
        3. Check permission policy (centralized policy).
        4. Execute tool action with timeout protection.
        5. Catch and structure any exceptions.
        """
        tool = self.get(tool_name)
        if not tool:
            err_msg = f"Unknown tool: '{tool_name}' is not registered in the system."
            logger.error(err_msg)
            return ToolResult(
                success=False,
                error=err_msg,
                tool_name=tool_name,
            )

        # 1. Argument validation
        is_valid, val_error = tool.validate_arguments(kwargs)
        if not is_valid:
            err_msg = f"Argument validation failed for '{tool_name}': {val_error}"
            logger.warning(err_msg)
            return ToolResult(
                success=False,
                error=err_msg,
                tool_name=tool_name,
            )

        # 2. Permission check
        policy = permission_policy or self._policy
        allowed, perm_error = policy.check_permission(tool, context)
        if not allowed:
            logger.warning("Permission denied for tool '%s': %s", tool_name, perm_error)
            return ToolResult(
                success=False,
                error=perm_error,
                tool_name=tool_name,
                metadata={"permission": tool.permission.value, "denied": True},
            )

        # 3. Execution with timeout protection
        logger.info("Executing tool '%s' with arguments: %s", tool_name, kwargs)

        timeout = getattr(tool, "timeout", 10.0)
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(tool.execute, **kwargs)
                result = future.result(timeout=timeout)
                if not isinstance(result, ToolResult):
                    return ToolResult(
                        success=True,
                        output=result,
                        tool_name=tool_name,
                    )
                # Ensure tool_name is set
                if not result.tool_name:
                    return ToolResult(
                        success=result.success,
                        output=result.output,
                        error=result.error,
                        tool_name=tool_name,
                        metadata=result.metadata,
                    )
                return result
        except concurrent.futures.TimeoutError:
            err_msg = f"Tool '{tool_name}' execution timed out after {timeout:.1f}s."
            logger.error(err_msg)
            return ToolResult(
                success=False,
                error=err_msg,
                tool_name=tool_name,
            )
        except Exception as exc:
            err_msg = f"Exception during execution of tool '{tool_name}': {exc}"
            logger.exception(err_msg)
            return ToolResult(
                success=False,
                error=err_msg,
                tool_name=tool_name,
            )
