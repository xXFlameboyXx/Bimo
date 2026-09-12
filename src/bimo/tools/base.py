"""Base tool abstractions, parameter descriptors, and structured results for Bimo.

Provides type-safe parameters, schema generation, argument validation,
and safe execution results for the robot's tool subsystem.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from bimo.tools.permissions import ToolPermission

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolResult:
    """Standardized structured output from executing a robot tool."""

    success: bool
    output: Any = None
    error: str | None = None
    tool_name: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert result to dictionary representation."""
        data: dict[str, Any] = {
            "success": self.success,
            "output": self.output,
            "error": self.error,
        }
        if self.tool_name:
            data["tool_name"] = self.tool_name
        if self.metadata:
            data["metadata"] = self.metadata
        return data

    def to_json(self, max_length: int = 4000) -> str:
        """Serialize tool result safely as JSON string with length bounding."""
        try:
            raw = json.dumps(self.to_dict(), default=str)
        except Exception as exc:
            raw = json.dumps({
                "success": False,
                "error": f"Serialization failure: {exc}",
                "tool_name": self.tool_name,
            })

        if len(raw) > max_length:
            logger.warning(
                "ToolResult for '%s' truncated from %d to %d characters.",
                self.tool_name,
                len(raw),
                max_length,
            )
            raw = raw[:max_length] + "... [output truncated]"
        return raw


@dataclass(frozen=True)
class ToolParameter:
    """Parameter definition for a tool."""

    name: str
    type: str  # "string", "number", "integer", "boolean", "array", "object"
    description: str
    required: bool = True
    enum_values: list[str] | None = None


class BaseTool(ABC):
    """Abstract base class for all tools executable by Bimo.

    Subclasses must define:
    - name: unique tool identifier (e.g. "robot.speak")
    - description: clear explanation of tool behavior for the LLM
    - parameters: typed parameter specifications
    - permission: ToolPermission level (SAFE, CONFIRM, HIGH_RISK)
    - execute: implementation logic returning ToolResult
    """

    def __init__(
        self,
        name: str,
        description: str,
        parameters: list[ToolParameter] | None = None,
        permission: ToolPermission = ToolPermission.SAFE,
        timeout: float = 10.0,
        strict_parameters: bool = True,
    ) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters or []
        self.permission = permission
        self.timeout = timeout
        self.strict_parameters = strict_parameters

    def get_schema(self) -> dict[str, Any]:
        """Generate an OpenAI / Gemini function-calling compatible JSON schema."""
        properties: dict[str, Any] = {}
        required_fields: list[str] = []

        for param in self.parameters:
            prop: dict[str, Any] = {
                "type": param.type,
                "description": param.description,
            }
            if param.enum_values:
                prop["enum"] = param.enum_values
            properties[param.name] = prop

            if param.required:
                required_fields.append(param.name)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required_fields,
                },
            },
        }

    def validate_arguments(self, kwargs: dict[str, Any]) -> tuple[bool, str | None]:
        """Validate supplied arguments against parameter schema.

        Returns (is_valid, error_reason_if_invalid).
        """
        param_map = {p.name: p for p in self.parameters}

        # Check for unexpected extra parameters if strict mode enabled
        if self.strict_parameters:
            for k in kwargs:
                if k not in param_map:
                    return False, f"Unexpected argument '{k}' provided to tool '{self.name}'."

        # Validate defined parameters
        for param in self.parameters:
            if param.name not in kwargs or kwargs[param.name] is None:
                if param.required:
                    return False, f"Missing required parameter '{param.name}' for tool '{self.name}'."
                continue

            val = kwargs[param.name]

            # Type checking
            match param.type:
                case "string":
                    if not isinstance(val, str):
                        return False, (
                            f"Argument '{param.name}' must be a string, got {type(val).__name__}."
                        )
                case "integer":
                    if not isinstance(val, int) or isinstance(val, bool):
                        return False, (
                            f"Argument '{param.name}' must be an integer, got {type(val).__name__}."
                        )
                case "number":
                    if not isinstance(val, (int, float)) or isinstance(val, bool):
                        return False, (
                            f"Argument '{param.name}' must be a number, got {type(val).__name__}."
                        )
                case "boolean":
                    if not isinstance(val, bool):
                        return False, (
                            f"Argument '{param.name}' must be a boolean, got {type(val).__name__}."
                        )
                case "array":
                    if not isinstance(val, list):
                        return False, (
                            f"Argument '{param.name}' must be a list/array, got {type(val).__name__}."
                        )
                case "object":
                    if not isinstance(val, dict):
                        return False, (
                            f"Argument '{param.name}' must be a dictionary/object, got {type(val).__name__}."
                        )

            # Enum validation
            if param.enum_values is not None and str(val).lower() not in [
                e.lower() for e in param.enum_values
            ]:
                return False, (
                    f"Argument '{param.name}' with value '{val}' is not in allowed choices: {param.enum_values}."
                )

        return True, None

    @abstractmethod
    def execute(self, **kwargs: Any) -> ToolResult:
        """Execute the tool action with the supplied keyword arguments."""
