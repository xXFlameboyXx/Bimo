"""Bimo PC Agent integration package (Phase 7 & 8)."""

from bimo.pc.auth import RequestAuthenticator, sign_request, verify_signature
from bimo.pc.client import PCAgentClient
from bimo.pc.mock_client import MockPCAgentClient
from bimo.pc.models import PCError, PCErrorCode, PCRequest, PCResponse
from bimo.pc.tools import (
    PCClickTool,
    PCCloseAppTool,
    PCFocusWindowTool,
    PCGetActiveWindowTool,
    PCGetScreenSizeTool,
    PCGetStatusTool,
    PCMoveMouseTool,
    PCOpenAppTool,
    PCPressKeyTool,
    PCScreenshotTool,
    PCScrollTool,
    PCTypeTextTool,
    register_pc_tools,
)
from bimo.pc.vision import DesktopCapture, ScreenControllerDesktopCapture, Screenshot

__all__ = [
    "PCAgentClient",
    "MockPCAgentClient",
    "PCRequest",
    "PCResponse",
    "PCError",
    "PCErrorCode",
    "RequestAuthenticator",
    "sign_request",
    "verify_signature",
    "register_pc_tools",
    "PCGetStatusTool",
    "PCGetActiveWindowTool",
    "PCGetScreenSizeTool",
    "PCScreenshotTool",
    "PCFocusWindowTool",
    "PCOpenAppTool",
    "PCCloseAppTool",
    "PCTypeTextTool",
    "PCPressKeyTool",
    "PCMoveMouseTool",
    "PCClickTool",
    "PCScrollTool",
    "DesktopCapture",
    "ScreenControllerDesktopCapture",
    "Screenshot",
]
