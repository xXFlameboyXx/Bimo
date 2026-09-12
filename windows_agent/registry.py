"""Command registry for the Windows PC Agent.

Provides strict command registration, deterministic listing, parameter schema validation,
and execution boundaries. Strictly prohibits eval, exec, and arbitrary shell access.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from bimo.pc.models import PCError, PCErrorCode, PCResponse

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PCCommandSpec:
    """Specification of an explicitly registered PC command."""
    name: str
    description: str
    handler: Callable[[Dict[str, Any]], Dict[str, Any]]
    required_args: List[str] = field(default_factory=list)
    optional_args: List[str] = field(default_factory=list)


class PCCommandRegistry:
    """Registry managing allowed executable commands on the Windows PC Agent."""

    def __init__(self) -> None:
        self._commands: Dict[str, PCCommandSpec] = {}

    def register(self, spec: PCCommandSpec) -> None:
        """Register a command specification."""
        if spec.name in self._commands:
            raise ValueError(f"Command '{spec.name}' is already registered.")
        self._commands[spec.name] = spec
        logger.debug("Registered PC command '%s'", spec.name)

    def get(self, name: str) -> Optional[PCCommandSpec]:
        """Retrieve command specification by name."""
        return self._commands.get(name)

    def has(self, name: str) -> bool:
        """Check if command is registered."""
        return name in self._commands

    def list_commands(self) -> List[str]:
        """Return deterministically sorted list of command names."""
        return sorted(list(self._commands.keys()))

    def execute(self, request_id: str, command_name: str, arguments: Dict[str, Any]) -> PCResponse:
        """Execute a registered command with argument checking."""
        spec = self._commands.get(command_name)
        if not spec:
            return PCResponse(
                version=1,
                request_id=request_id,
                success=False,
                command=command_name,
                error=PCError(
                    code=PCErrorCode.UNKNOWN_COMMAND.value,
                    message=f"Command '{command_name}' is not registered or supported on this PC Agent.",
                ),
            )

        # Validate arguments structure
        if not isinstance(arguments, dict):
            return PCResponse(
                version=1,
                request_id=request_id,
                success=False,
                command=command_name,
                error=PCError(
                    code=PCErrorCode.INVALID_ARGUMENTS.value,
                    message="Arguments must be a JSON object/dictionary.",
                ),
            )

        # Check required arguments
        for req in spec.required_args:
            if req not in arguments:
                return PCResponse(
                    version=1,
                    request_id=request_id,
                    success=False,
                    command=command_name,
                    error=PCError(
                        code=PCErrorCode.INVALID_ARGUMENTS.value,
                        message=f"Missing required argument '{req}' for command '{command_name}'.",
                    ),
                )

        # Check for unallowed arbitrary arguments
        allowed = set(spec.required_args).union(spec.optional_args)
        for provided in arguments.keys():
            if provided not in allowed:
                return PCResponse(
                    version=1,
                    request_id=request_id,
                    success=False,
                    command=command_name,
                    error=PCError(
                        code=PCErrorCode.INVALID_ARGUMENTS.value,
                        message=f"Unrecognized argument '{provided}' for command '{command_name}'.",
                    ),
                )

        # Execute handler safely
        try:
            output = spec.handler(arguments)
            return PCResponse(
                version=1,
                request_id=request_id,
                success=True,
                command=command_name,
                output=output,
            )
        except ValueError as val_err:
            msg = str(val_err)
            err_code = PCErrorCode.INVALID_ARGUMENTS.value
            if "not in the allowed applications list" in msg:
                err_code = PCErrorCode.APP_NOT_ALLOWED.value
            elif "not in the allowed keys list" in msg:
                err_code = PCErrorCode.KEY_NOT_ALLOWED.value

            return PCResponse(
                version=1,
                request_id=request_id,
                success=False,
                command=command_name,
                error=PCError(code=err_code, message=msg),
            )
        except Exception as exc:
            logger.error("Error executing PC command '%s': %s", command_name, exc, exc_info=True)
            return PCResponse(
                version=1,
                request_id=request_id,
                success=False,
                command=command_name,
                error=PCError(
                    code=PCErrorCode.COMMAND_FAILED.value,
                    message=f"Command execution error: {exc}",
                ),
            )
