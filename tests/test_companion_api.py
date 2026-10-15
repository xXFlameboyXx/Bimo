"""Tests for Bimo Companion Subsystem API, authentication, WebSocket, and state synchronization."""

from __future__ import annotations

import io
import time
import pytest
from starlette.testclient import TestClient

from bimo.companion.auth import COMPANION_TOKEN_ENV, get_expected_token
from bimo.companion.server import create_companion_app
from bimo.companion.service import CompanionService
from bimo.core.events import Event, EventBus, EventType
from bimo.core.state import RobotState, RobotStateMachine
from bimo.artifacts.service import ArtifactService


@pytest.fixture
def companion_setup():
    event_bus = EventBus()
    state_machine = RobotStateMachine(event_bus=event_bus)
    artifact_service = ArtifactService()
    service = CompanionService(
        event_bus=event_bus,
        state_machine=state_machine,
        artifact_service=artifact_service,
    )
    app = create_companion_app(service)
    client = TestClient(app)
    token = get_expected_token()
    headers = {"Authorization": f"Bearer {token}"}
    return {
        "service": service,
        "event_bus": event_bus,
        "state_machine": state_machine,
        "artifact_service": artifact_service,
        "app": app,
        "client": client,
        "token": token,
        "headers": headers,
    }


def test_health_check(companion_setup):
    client = companion_setup["client"]
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_unauthenticated_request_rejected(companion_setup):
    client = companion_setup["client"]
    resp = client.get("/api/v1/status")
    assert resp.status_code == 401


def test_authenticated_status(companion_setup):
    client = companion_setup["client"]
    headers = companion_setup["headers"]
    resp = client.get("/api/v1/status", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "online"
    assert data["state"] == "idle"
    assert "ready" in data["status_text"].lower()


def test_state_endpoint(companion_setup):
    client = companion_setup["client"]
    headers = companion_setup["headers"]
    state_machine = companion_setup["state_machine"]

    state_machine.transition_to(RobotState.THINKING, reason="Testing")
    resp = client.get("/api/v1/state", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["state"] == "thinking"


def test_chat_turn_processing(companion_setup):
    client = companion_setup["client"]
    headers = companion_setup["headers"]

    payload = {
        "message": "Hello Bimo!",
        "attachments": [],
    }
    resp = client.post("/api/v1/chat", json=payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert "task_id" in data
    assert len(data["response"]) > 0


def test_file_upload_and_artifact_registration(companion_setup):
    client = companion_setup["client"]
    headers = companion_setup["headers"]

    content = b"Minecraft Castle Design Notes: stone, obsidian, glass."
    fname = f"notes_{int(time.time()*1000)}.md"
    files = {"file": (fname, io.BytesIO(content), "text/markdown")}

    resp = client.post("/api/v1/upload", files=files, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["filename"] == fname
    assert data["size_bytes"] == len(content)
    assert "id" in data

    # Verify listed in artifacts
    list_resp = client.get("/api/v1/artifacts", headers=headers)
    assert list_resp.status_code == 200
    artifacts = list_resp.json()
    assert any(a["id"] == data["id"] for a in artifacts)


def test_file_upload_oversized_rejected(companion_setup):
    client = companion_setup["client"]
    headers = companion_setup["headers"]

    # Text limit is 10 MB, generate larger dummy payload
    huge_data = b"X" * (11 * 1024 * 1024)
    files = {"file": ("huge.txt", io.BytesIO(huge_data), "text/plain")}

    resp = client.post("/api/v1/upload", files=files, headers=headers)
    assert resp.status_code in (400, 413)


def test_task_cancellation(companion_setup):
    client = companion_setup["client"]
    headers = companion_setup["headers"]
    service = companion_setup["service"]

    # Set mock active task
    service._active_task_id = "task-test-cancel"
    resp = client.post("/api/v1/tasks/task-test-cancel/cancel", json={"reason": "User stop"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


def test_permission_flow(companion_setup):
    client = companion_setup["client"]
    headers = companion_setup["headers"]
    service = companion_setup["service"]

    # Simulate pending permission
    service._permission_events["req-123"] = pytest.importorskip("threading").Event()
    resp = client.post(
        "/api/v1/permission/respond",
        json={"request_id": "req-123", "allowed": True, "scope": "pc_control"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["recorded"] is True
    assert service.permission_policy.is_session_active("pc_control") is True


def test_voice_preview(companion_setup):
    client = companion_setup["client"]
    headers = companion_setup["headers"]
    resp = client.post(
        "/api/v1/voice/preview",
        json={"text": "Testing voice preview", "voice": "en_GB-semaine-medium"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] in ("playing", "mock_preview")


def test_websocket_authentication_and_initial_state(companion_setup):
    client = companion_setup["client"]
    token = companion_setup["token"]

    # Without token -> rejected
    with pytest.raises(Exception):
        with client.websocket_connect("/api/v1/events"):
            pass

    # With valid token -> receives initial state
    with client.websocket_connect(f"/api/v1/events?token={token}") as ws:
        init_data = ws.receive_json()
        assert init_data["type"] == "initial_state"
        assert init_data["state"] == "idle"

        # Test ping-pong
        ws.send_json({"type": "ping"})
        pong = ws.receive_json()
        assert pong["type"] == "pong"
