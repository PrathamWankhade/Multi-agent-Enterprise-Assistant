"""JWT login, RBAC and admin task/priority assignment tests."""

import pytest

from app.database import users as user_db


@pytest.fixture()
def jwt_client(client, fresh_settings):
    fresh_settings(
        auth_enabled=True,
        secret_key="test-jwt-secret-key-that-is-long-enough-for-hs256",
        api_keys="test-admin-key:admin",
    )
    yield client
    # Clean up any assignment rules created by tests.
    try:
        client.delete(
            "/api/v1/admin/rules",
            headers={"Authorization": "Bearer " + client.post(
                "/api/v1/auth/login",
                json={"username": "admin", "password": "Admin123!"},
            ).json()["access_token"]},
        )
    except Exception:  # pragma: no cover - cleanup best effort
        pass


def test_default_users_seeded():
    assert user_db.get_by_username("admin")["role"] == "admin"
    assert user_db.get_by_username("employee")["role"] == "user"
    assert user_db.get_by_username("technician")["role"] == "technician"


def test_password_hashing():
    hashed = user_db.hash_password("Secret123!")
    assert hashed != "Secret123!"
    assert user_db.verify_password("Secret123!", hashed)
    assert not user_db.verify_password("wrong", hashed)


def test_login_success(jwt_client):
    r = jwt_client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "Admin123!"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["role"] == "admin"
    assert body["access_token"] and body["refresh_token"]


def test_login_failure(jwt_client):
    r = jwt_client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "wrong"},
    )
    assert r.status_code == 401


def test_me_with_jwt(jwt_client):
    token = jwt_client.post(
        "/api/v1/auth/login",
        json={"username": "employee", "password": "User123!"},
    ).json()["access_token"]
    r = jwt_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["user"]["role"] == "user"


def test_refresh_token(jwt_client):
    tokens = jwt_client.post(
        "/api/v1/auth/login",
        json={"username": "employee", "password": "User123!"},
    ).json()
    r = jwt_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert r.status_code == 200
    assert r.json()["access_token"]


def test_jwt_on_existing_protected_route(jwt_client):
    """Existing API-key routes must accept JWT too."""
    token = jwt_client.post(
        "/api/v1/auth/login",
        json={"username": "employee", "password": "User123!"},
    ).json()["access_token"]
    assert jwt_client.get("/api/v1/tools", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_admin_user_management(jwt_client):
    token = jwt_client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "Admin123!"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    r = jwt_client.post(
        "/api/v1/auth/users",
        json={
            "username": "testuser_jwt",
            "password": "Pass123!",
            "role": "user",
            "department": "Finance",
            "full_name": "Test User",
        },
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["role"] == "user"

    r = jwt_client.patch(
        "/api/v1/auth/users/testuser_jwt/role",
        json={"role": "technician"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["role"] == "technician"

    # Non-admin cannot manage users
    user_token = jwt_client.post(
        "/api/v1/auth/login",
        json={"username": "employee", "password": "User123!"},
    ).json()["access_token"]
    assert jwt_client.get(
        "/api/v1/auth/users",
        headers={"Authorization": f"Bearer {user_token}"},
    ).status_code == 403


def test_admin_assigns_task_and_priority(jwt_client):
    """Admin sets priority and technician for a specific user's ticket."""
    from app.database import itsm as db

    token = jwt_client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "Admin123!"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Create a ticket as the employee
    user_token = jwt_client.post(
        "/api/v1/auth/login",
        json={"username": "employee", "password": "User123!"},
    ).json()["access_token"]
    r = jwt_client.post(
        "/api/v1/itsm/requests",
        json={"message": "Laptop will not boot", "requester": "employee"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert r.status_code == 200
    tid = r.json()["ticket"]["ticket_id"]

    # Admin overrides priority and assigns by user
    r = jwt_client.post(
        f"/api/v1/admin/tickets/{tid}/assign",
        json={
            "priority": "critical",
            "technician": "Liam O'Connor",
            "note": "Priority raised: employee has a deadline",
        },
        headers=headers,
    )
    assert r.status_code == 200
    ticket = r.json()["ticket"]
    assert ticket["priority"] == "critical"
    assert ticket["technician"] == "Liam O'Connor"
    assert ticket["approved_by"] == "admin"
    assert ticket["status"] == "assigned"

    # Clean up
    with db._connect() as conn:
        conn.execute("DELETE FROM ticket_status_history WHERE ticket_id=?", (tid,))
        conn.execute("DELETE FROM service_requests WHERE ticket_id=?", (tid,))


def test_admin_routing_rule_applied(jwt_client):
    """Per-user priority rules override the AI suggestion for that user."""
    from app.database import itsm as db

    admin_token = jwt_client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "Admin123!"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {admin_token}"}

    r = jwt_client.post(
        "/api/v1/admin/rules",
        json={"username": "employee", "priority": "high", "technician": "Sofia Ramirez"},
        headers=headers,
    )
    assert r.status_code == 200

    user_token = jwt_client.post(
        "/api/v1/auth/login",
        json={"username": "employee", "password": "User123!"},
    ).json()["access_token"]
    r = jwt_client.post(
        "/api/v1/itsm/requests",
        json={"message": "I need a software install", "requester": "employee"},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ticket"]["priority"] == "high"
    assert body["classification"].get("priority_source") == "admin_rule"
    tid = body["ticket"]["ticket_id"]

    with db._connect() as conn:
        conn.execute("DELETE FROM ticket_status_history WHERE ticket_id=?", (tid,))
        conn.execute("DELETE FROM service_requests WHERE ticket_id=?", (tid,))
    jwt_client.delete("/api/v1/admin/rules", headers=headers)
