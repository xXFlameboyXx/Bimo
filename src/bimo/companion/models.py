"""Data models for the Bimo Companion App API."""

from __future__ import annotations

import time
from typing import Any, List, Optional
from pydantic import BaseModel, Field


class AttachmentMeta(BaseModel):
    id: str
    filename: str
    mime_type: str
    size_bytes: int
    path: str


class ChatRequest(BaseModel):
    message: str = Field(..., description="User message text")
    attachments: List[AttachmentMeta] = Field(default_factory=list, description="List of attached files")
    conversation_id: Optional[str] = Field(default=None, description="Optional conversation context identifier")


class ChatResponse(BaseModel):
    task_id: str
    status: str
    response: str
    state: str
    artifacts: List[dict[str, Any]] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)


class UploadResponse(BaseModel):
    id: str
    filename: str
    mime_type: str
    size_bytes: int
    path: str
    message: str = "Upload successful"


class StatusResponse(BaseModel):
    status: str = "online"
    state: str = "idle"
    status_text: str = "Hey! I'm ready ♡"
    bimo_version: str = "0.1.0"
    companion_api_version: str = "1.0.0"
    voice: dict[str, Any] = Field(default_factory=dict)
    active_task: Optional[str] = None
    pc_connected: bool = False
    timestamp: float = Field(default_factory=time.time)


class PermissionPrompt(BaseModel):
    request_id: str
    tool_name: str
    title: str
    description: str
    scope: str = "pc_control"
    created_at: float = Field(default_factory=time.time)


class PermissionDecision(BaseModel):
    request_id: str
    allowed: bool
    scope: str = "pc_control"
    duration_seconds: float = 300.0


class VoicePreviewRequest(BaseModel):
    text: Optional[str] = "Hello! I am Bimo, your cozy AI companion."
    voice: Optional[str] = "en_GB-semaine-medium"
    speech_rate: Optional[int] = 175
    volume: Optional[float] = 1.0


class TaskCancelRequest(BaseModel):
    task_id: Optional[str] = None
    reason: str = "User requested cancellation from companion app"
