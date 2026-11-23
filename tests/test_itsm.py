"""ITSM ticket lifecycle tests (offline, isolated SQLite)."""

import pytest

from app.database import itsm as db


@pytest.fixture()
def clean_tickets():
    """Remove any tickets created by a test after it finishes."""
    created = []
    yield created
    try:
        with db._connect() as conn:
            for ticket_id in created:
                conn.execute("DELETE FROM ticket_status_history WHERE ticket_id = ?", (ticket_id,))
                conn.execute("DELETE FROM service_requests WHERE ticket_id = ?", (ticket_id,))
    except Exception:  # pragma: no cover
        pass


def make_ticket(**kwargs):
    ticket = db.create_ticket(
        title=kwargs.get("title", "Laptop will not boot"),
        description=kwargs.get("description", "Blue screen after update, urgent"),
        category=kwargs.get("category", "hardware"),
        requester=kwargs.get("requester", "Ava Patel"),
        issue_type=kwargs.get("issue_type", "Laptop boot failure"),
        suggested_priority=kwargs.get("priority", "high"),
    )
    return ticket["ticket_id"]


def test_classification_agent_fallback():
    import asyncio
    from app.agents.classifier import ClassificationAgent

    result = asyncio.run(
        ClassificationAgent().run(
            task_id="cls-test", session_id="s",
            input_data={"message": "My VPN keeps disconnecting and I cannot work"},
        )
    )
    # Mock LLM returns JSON only for "Respond ONLY with valid JSON" prompts;
    # either path must yield a valid category/priority.
    assert result["category"] in {"network", "other", "hardware"}
    assert result["suggested_priority"] in {"critical", "high", "medium", "low"}
    assert result["approval_required"] is True


def test_ticket_creation_starts_pending_approval(clean_tickets):
    tid = make_ticket()
    clean_tickets.append(tid)
    ticket = db.get_ticket(tid)
    assert ticket["status"] == "pending_approval"
    assert ticket["priority"] == "high"
    assert ticket["sla_due_at"]
    history = db.get_history(tid)
    assert len(history) == 1
    assert history[0]["to_status"] == "pending_approval"


def test_admin_approval_assigns_technician(client, clean_tickets):
    tid = make_ticket()
    clean_tickets.append(tid)

    r = client.post(
        f"/api/v1/itsm/tickets/{tid}/approve",
        json={"actor": "admin", "note": "Approved, high priority"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ticket"]["status"] == "assigned"
    assert body["ticket"]["approved_by"] == "admin"
    assert body["ticket"]["technician"]  # auto-assigned by skill
    assert [h["to_status"] for h in body["history"]] == [
        "pending_approval", "approved", "assigned"
    ]


def test_admin_priority_override(client, clean_tickets):
    tid = make_ticket(priority="low")
    clean_tickets.append(tid)
    r = client.post(
        f"/api/v1/itsm/tickets/{tid}/approve",
        json={"actor": "admin", "priority": "critical", "technician": "Noah Kim"},
    )
    assert r.status_code == 200
    ticket = r.json()["ticket"]
    assert ticket["priority"] == "critical"
    assert ticket["technician"] == "Noah Kim"


def test_rejection_is_terminal(client, clean_tickets):
    tid = make_ticket()
    clean_tickets.append(tid)
    r = client.post(
        f"/api/v1/itsm/tickets/{tid}/reject",
        json={"actor": "admin", "note": "Duplicate request"},
    )
    assert r.status_code == 200
    assert r.json()["ticket"]["status"] == "rejected"

    # Cannot approve a rejected ticket.
    r2 = client.post(f"/api/v1/itsm/tickets/{tid}/approve", json={"actor": "admin"})
    assert r2.status_code == 409


def test_technician_resolution_flow(client, clean_tickets):
    tid = make_ticket()
    clean_tickets.append(tid)
    client.post(f"/api/v1/itsm/tickets/{tid}/approve", json={"actor": "admin"})
    statuses = ["in_progress", "resolved"]
    for status in statuses:
        body = {"status": status, "actor": "Noah Kim"}
        if status == "resolved":
            body["resolution"] = "Replaced faulty SSD"
        r = client.patch(f"/api/v1/itsm/tickets/{tid}", json=body)
        assert r.status_code == 200, r.text
    ticket = r.json()["ticket"]
    assert ticket["status"] == "resolved"
    assert ticket["resolution"] == "Replaced faulty SSD"
    assert ticket["resolved_at"]

    # resolved -> assigned is invalid; only closed / in_progress are allowed
    assert client.patch(
        f"/api/v1/itsm/tickets/{tid}",
        json={"status": "assigned", "actor": "Noah Kim"},
    ).status_code == 409
    # Invalid transition to pending_approval must be rejected too
    assert client.patch(
        f"/api/v1/itsm/tickets/{tid}",
        json={"status": "pending_approval", "actor": "Noah Kim"},
    ).status_code == 409


def test_tracking_and_queues(client, clean_tickets):
    tid = make_ticket()
    clean_tickets.append(tid)

    track = client.get(f"/api/v1/itsm/tickets/{tid}")
    assert track.status_code == 200
    assert len(track.json()["history"]) >= 1

    queue = client.get("/api/v1/itsm/approvals")
    assert queue.status_code == 200
    assert any(t["ticket_id"] == tid for t in queue.json()["tickets"])

    techs = client.get("/api/v1/itsm/technicians?skill=hardware")
    assert techs.status_code == 200
    assert len(techs.json()["technicians"]) >= 1


def test_create_request_endpoint(client, clean_tickets):
    r = client.post(
        "/api/v1/itsm/requests",
        json={
            "message": "My laptop will not start and I have a presentation tomorrow",
            "requester": "Test Employee",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    tid = body["ticket"]["ticket_id"]
    clean_tickets.append(tid)
    assert body["ticket"]["status"] == "pending_approval"
    assert body["classification"]["category"] in {"hardware", "other"}
    assert isinstance(body["troubleshooting_hints"], list)


def test_itsm_intent_forces_agent(client, monkeypatch, clean_tickets):
    """Even if the LLM returns agents:['research'], itsm intent must route correctly."""
    from app.agents.orchestrator import OrchestratorAgent

    async def fake_run(self, task_id, session_id, input_data, context=None):
        return {
            "agent": "orchestrator",
            "plan": {"intent": "itsm", "agents": ["research"], "reasoning": "test"},
            "task_id": task_id,
        }

    monkeypatch.setattr(OrchestratorAgent, "run", fake_run)
    r = client.post("/api/v1/chat", json={"message": "VPN is broken", "session_id": "itsm-force"})
    assert r.status_code == 200
    body = r.json()
    assert body["orchestrator_plan"]["agents"] == ["itsm"]
    assert [a["agent"] for a in body["agent_results"]] == ["itsm"]
    clean_tickets.append(body["itsm"]["ticket"]["ticket_id"])


def test_itsm_chat_skips_review(client, clean_tickets):
    """Review must not rewrite deterministic ITSM/ticket responses."""
    r = client.post(
        "/api/v1/chat",
        json={
            "message": "My laptop won't boot, blue screen, urgent presentation tomorrow",
            "session_id": "itsm-chat-test",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["orchestrator_plan"]["intent"] == "itsm"
    assert [a["agent"] for a in body["agent_results"]] == ["itsm"]
    assert "SR-" in body["final_response"]
    assert body["itsm"]["ticket"]["status"] == "pending_approval"
    tid = body["itsm"]["ticket"]["ticket_id"]
    clean_tickets.append(tid)


def test_itsm_roles(client, fresh_settings, clean_tickets):
    """Approvals require admin; ordinary users cannot approve."""
    from tests.test_auth import KEY_ADMIN, KEY_USER

    fresh_settings(auth_enabled=True, api_keys=f"{KEY_ADMIN}:admin,{KEY_USER}:user")
    tid = make_ticket()
    clean_tickets.append(tid)

    r = client.post(
        f"/api/v1/itsm/tickets/{tid}/approve",
        json={"actor": "user"},
        headers={"X-API-Key": KEY_USER},
    )
    assert r.status_code == 403

    r = client.post(
        f"/api/v1/itsm/tickets/{tid}/approve",
        json={"actor": "admin"},
        headers={"X-API-Key": KEY_ADMIN},
    )
    assert r.status_code == 200
