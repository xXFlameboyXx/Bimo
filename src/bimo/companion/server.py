"""FastAPI HTTP and WebSocket server for the Bimo Companion App."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path
import time
from typing import Any, AsyncGenerator, List, Optional

from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from bimo.companion.auth import get_expected_token, verify_companion_token
from bimo.companion.models import (
    ChatRequest,
    ChatResponse,
    PermissionDecision,
    StatusResponse,
    TaskCancelRequest,
    UploadResponse,
    VoicePreviewRequest,
)
from bimo.companion.service import CompanionService

logger = logging.getLogger(__name__)


def create_companion_app(service: CompanionService) -> FastAPI:
    """Create and configure FastAPI application for Companion clients."""
    
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        loop = asyncio.get_running_loop()
        service.set_loop(loop)
        logger.info("Bimo Companion API started on loop: %s", loop)
        yield

    app = FastAPI(
        title="Bimo Companion API",
        version="1.0.0",
        description="Authenticated API and real-time WebSocket for Bimo Companion App",
        lifespan=lifespan,
    )

    # Configure CORS for desktop app (Tauri, Vite dev, Electron)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "bimo-companion"}

    @app.get("/api/v1/status", response_model=StatusResponse)
    async def get_status(authenticated: bool = Depends(verify_companion_token)) -> Any:
        return service.get_status()

    @app.get("/api/v1/state")
    async def get_state(authenticated: bool = Depends(verify_companion_token)) -> dict[str, Any]:
        return {
            "state": service.state_machine.current_state.value.lower(),
            "status_text": service._current_status_text,
            "timestamp": time.time(),
        }

    @app.post("/api/v1/chat", response_model=ChatResponse)
    async def post_chat(
        req: ChatRequest,
        authenticated: bool = Depends(verify_companion_token),
    ) -> Any:
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                service.process_chat,
                req.message,
                [a.model_dump() for a in req.attachments],
                req.conversation_id,
            )
            return result
        except HTTPException:
            raise
        except Exception as err:
            logger.error("Failed to process chat: %s", err, exc_info=True)
            raise HTTPException(status_code=500, detail=str(err))

    @app.post("/api/v1/upload", response_model=UploadResponse)
    async def upload_file(
        file: UploadFile = File(...),
        authenticated: bool = Depends(verify_companion_token),
    ) -> Any:
        try:
            filename = file.filename or "upload.bin"
            content = await file.read()
            
            # Enforce size limits via artifact service
            ext = os.path.splitext(filename)[1].lower()
            limit = service.artifact_service.get_size_limit_for_extension(ext)
            if len(content) > limit:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail=f"File exceeds maximum allowed limit of {limit // 1024} KB for {ext}",
                )

            # Create artifact safely
            art = service.artifact_service.create_artifact(
                filename=filename,
                data=content,
                mime_type=file.content_type,
                metadata={"source": "companion_upload", "original_name": filename},
            )

            service.broadcast_message({
                "type": "artifact_created",
                "artifact": art.to_dict(),
            })

            return {
                "id": art.id,
                "filename": art.filename,
                "mime_type": art.mime_type,
                "size_bytes": art.size_bytes,
                "path": art.path,
                "message": "File attached and registered ♡",
            }
        except HTTPException:
            raise
        except ValueError as val_err:
            raise HTTPException(status_code=400, detail=str(val_err))
        except Exception as err:
            logger.error("Upload error: %s", err)
            raise HTTPException(status_code=500, detail=f"Upload failed: {err}")

    @app.get("/api/v1/artifacts")
    async def list_artifacts(authenticated: bool = Depends(verify_companion_token)) -> list[dict[str, Any]]:
        artifacts = service.artifact_service.list_artifacts()
        return [a.to_dict() for a in artifacts]

    @app.get("/api/v1/artifacts/{artifact_id}")
    async def get_artifact(
        artifact_id: str,
        authenticated: bool = Depends(verify_companion_token),
    ) -> dict[str, Any]:
        art = service.artifact_service.get_artifact(artifact_id)
        if not art:
            raise HTTPException(status_code=404, detail="Artifact not found")
        return art.to_dict()

    @app.get("/api/v1/artifacts/{artifact_id}/download")
    async def download_artifact(
        artifact_id: str,
        token: Optional[str] = Query(None),
    ) -> Any:
        expected = get_expected_token()
        if token != expected:
            raise HTTPException(status_code=401, detail="Unauthorized")

        art = service.artifact_service.get_artifact(artifact_id)
        if not art:
            raise HTTPException(status_code=404, detail="Artifact not found")
        
        file_path = service.artifact_service.workspace_dir / art.path
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="Artifact file missing on disk")
        return FileResponse(path=str(file_path), media_type=art.mime_type, filename=art.filename)

    @app.post("/api/v1/tasks/{task_id}/cancel")
    async def cancel_task(
        task_id: str,
        req: Optional[TaskCancelRequest] = None,
        authenticated: bool = Depends(verify_companion_token),
    ) -> dict[str, Any]:
        reason = req.reason if req else "Cancelled by user"
        ok = service.cancel_task(task_id, reason=reason)
        return {"status": "cancelled" if ok else "failed", "task_id": task_id}

    @app.post("/api/v1/permission/respond")
    async def respond_permission(
        dec: PermissionDecision,
        authenticated: bool = Depends(verify_companion_token),
    ) -> dict[str, Any]:
        ok = service.respond_permission(dec.request_id, dec.allowed, dec.scope)
        return {"status": "ok", "recorded": ok}

    @app.post("/api/v1/voice/preview")
    async def voice_preview(
        req: VoicePreviewRequest,
        authenticated: bool = Depends(verify_companion_token),
    ) -> dict[str, Any]:
        try:
            if service.agent and service.agent.tts:
                sample_text = req.text or "Hello! I am Bimo."
                service.agent.tts.speak(sample_text)
                return {"status": "playing", "voice": service.agent.tts.voice}
            return {"status": "mock_preview", "message": "Voice preview simulated"}
        except Exception as err:
            logger.warning("Voice preview failed: %s", err)
            return {"status": "error", "message": str(err)}

    @app.websocket("/api/v1/events")
    async def websocket_events(websocket: WebSocket, token: Optional[str] = Query(None)) -> None:
        expected = get_expected_token()
        if token != expected:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        await websocket.accept()
        service.register_client(websocket)

        # Send initial snapshot immediately
        await websocket.send_json({
            "type": "initial_state",
            "state": service.state_machine.current_state.value.lower(),
            "status_text": service._current_status_text,
            "timestamp": time.time(),
        })

        try:
            while True:
                data = await websocket.receive_json()
                msg_type = data.get("type")
                if msg_type == "ping":
                    await websocket.send_json({"type": "pong", "timestamp": time.time()})
                elif msg_type == "cancel":
                    service.cancel_task(reason="WebSocket cancel event")
        except WebSocketDisconnect:
            service.unregister_client(websocket)
        except Exception as e:
            logger.debug("WebSocket client error: %s", e)
            service.unregister_client(websocket)

    return app
