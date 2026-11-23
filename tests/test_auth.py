"""RBAC tests: roles, 401/403 responses and audit of failed auth."""

import pytest

from app.security.auth import Role, lookup_role

KEY_ADMIN = "test-admin-key"
KEY_USER = "test-user-key"
KEY_VIEWER = "test-viewer-key"
KEY_STRING = f"{KEY_ADMIN}:admin,{KEY_USER}:user,{KEY_VIEWER}:viewer"


@pytest.fixture()
def auth_client(client, fresh_settings):
    """Enable auth with three keys for the duration of one test."""
    fresh_settings(auth_enabled=True, api_keys=KEY_STRING)
    yield client


def test_role_parsing(auth_client):
    assert lookup_role(KEY_ADMIN) == Role.ADMIN
    assert lookup_role(KEY_USER) == Role.USER
    assert lookup_role(KEY_VIEWER) == Role.VIEWER
    assert lookup_role("wrong") is None


def test_open_mode_allows_everything(client):
    # conftest leaves AUTH_ENABLED=false
    assert client.post("/api/v1/chat", json={"message": "hi"}).status_code == 200


def test_missing_key_is_401(auth_client):
    r = auth_client.get("/api/v1/tools")
    assert r.status_code == 401
    assert "X-API-Key" in r.json()["detail"]


def test_invalid_key_is_401(auth_client):
    r = auth_client.get("/api/v1/tools", headers={"X-API-Key": "wrong"})
    assert r.status_code == 401


def test_viewer_can_read(auth_client):
    r = auth_client.get("/api/v1/tools", headers={"X-API-Key": KEY_VIEWER})
    assert r.status_code == 200
    assert auth_client.get("/api/v1/health").status_code == 200  # health stays open


def test_viewer_cannot_chat(auth_client):
    r = auth_client.post(
        "/api/v1/chat",
        json={"message": "hi"},
        headers={"X-API-Key": KEY_VIEWER},
    )
    assert r.status_code == 403


def test_user_can_chat_but_not_approve(auth_client):
    r = auth_client.post(
        "/api/v1/chat",
        json={"message": "hi"},
        headers={"X-API-Key": KEY_USER},
    )
    assert r.status_code == 200
    r = auth_client.post("/api/v1/tasks/x/approve", headers={"X-API-Key": KEY_USER})
    assert r.status_code == 403


def test_admin_can_approve_and_read_audit(auth_client):
    r = auth_client.post("/api/v1/tasks/x/approve", headers={"X-API-Key": KEY_ADMIN})
    assert r.status_code == 404  # auth passes, unknown task
    assert auth_client.get("/api/v1/audit", headers={"X-API-Key": KEY_ADMIN}).status_code == 200
    assert auth_client.get("/api/v1/audit", headers={"X-API-Key": KEY_USER}).status_code == 403


def test_bearer_token_accepted(auth_client):
    r = auth_client.get(
        "/api/v1/tools", headers={"Authorization": f"Bearer {KEY_VIEWER}"}
    )
    assert r.status_code == 200


def test_auth_failures_are_audited(auth_client):
    auth_client.get("/api/v1/tools", headers={"X-API-Key": "bogus"})
    entries = auth_client.get(
        "/api/v1/audit", headers={"X-API-Key": KEY_ADMIN}
    ).json()["entries"]
    failures = [e for e in entries if e["event"] == "auth_failed"]
    assert failures
    assert failures[-1]["reason"] in {"invalid_key", "missing_key", "insufficient_role"}
