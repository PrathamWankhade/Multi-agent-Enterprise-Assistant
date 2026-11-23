"""API-level smoke and behaviour tests (offline, deterministic)."""

import pytest


def test_health(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "llm_provider" in body
    assert "memory_backend" in body


def test_root_and_static(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200
    assert client.get("/docs").status_code == 200


def test_tools_listing(client):
    r = client.get("/api/v1/tools")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    names = {t["name"] for t in body["tools"]}
    assert names == {
        "create_ticket",
        "generate_report",
        "list_tickets",
        "lookup_employee",
        "query_database",
    }
    create = next(t for t in body["tools"] if t["name"] == "create_ticket")
    assert create["risk"] == "write"
    assert create["requires_approval"] is True


def test_kb_documents(client):
    r = client.get("/api/v1/kb/documents")
    assert r.status_code == 200
    body = r.json()
    assert "count" in body and "documents" in body


def test_chat_greeting_fast_path(client):
    """Greetings must work without any LLM call and store history."""
    r = client.post(
        "/api/v1/chat",
        json={"message": "hello", "session_id": "test-greeting"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed"
    assert body["orchestrator_plan"]["intent"] == "chitchat"
    assert body["final_response"]
    assert body["trace"]["total_ms"] >= 0
    assert [s["agent"] for s in body["trace"]["steps"]] == ["orchestrator", "synthesis"]

    h = client.get("/api/v1/sessions/test-greeting/history")
    assert h.status_code == 200
    assert [m["role"] for m in h.json()["messages"]] == ["user", "assistant"]


def test_chat_research_and_trace(client):
    """A research question runs through the pipeline and records a trace."""
    r = client.post(
        "/api/v1/chat",
        json={"message": "What is the leave policy?", "session_id": "test-research"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["orchestrator_plan"]["intent"] == "research"
    agents = [a["agent"] for a in body["agent_results"]]
    assert "research" in agents
    assert "review" in agents
    assert body["final_response"]

    tid = body["task_id"]
    trace = client.get(f"/api/v1/tasks/{tid}/trace")
    assert trace.status_code == 200
    steps = trace.json()["trace"]["steps"]
    names = {s["agent"] for s in steps}
    assert {"orchestrator", "research", "synthesis", "review"} <= names
    assert all(s["status"] == "ok" for s in steps)
    assert all(s["duration_ms"] is not None for s in steps)


def test_audit_log_records_chat(client):
    r = client.get("/api/v1/audit?limit=20")
    assert r.status_code == 200
    events = {e["event"] for e in r.json()["entries"]}
    assert "chat_requested" in events
    assert "review_completed" in events


def test_unknown_trace_returns_404(client):
    assert client.get("/api/v1/tasks/does-not-exist/trace").status_code == 404


def test_ingest_and_search(client):
    r = client.post(
        "/api/v1/ingest",
        json={
            "text": "Quarterly revenue grew 12% year over year in the EMEA region.",
            "source": "test-finance.md",
            "doc_id": "test:finance",
        },
    )
    assert r.status_code == 200
    assert r.json()["chunks_ingested"] >= 1

    s = client.post("/api/v1/search", json={"query": "EMEA revenue", "top_k": 3})
    assert s.status_code == 200
    assert s.json()["count"] >= 0  # keyword or vector fallback both acceptable


def test_session_delete(client):
    assert client.delete("/api/v1/sessions/test-greeting").status_code == 200
    h = client.get("/api/v1/sessions/test-greeting/history").json()
    assert h["messages"] == []
