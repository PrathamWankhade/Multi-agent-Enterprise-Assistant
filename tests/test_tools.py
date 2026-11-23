"""Tool registry, validation, read-only SQL and approval workflow tests."""

import asyncio
import sqlite3

import pytest

from app.tools.enterprise import register_tools
from app.tools.registry import get_registry


@pytest.fixture(scope="module", autouse=True)
def _tools():
    register_tools()


def invoke(name, args):
    return asyncio.run(get_registry().invoke(name, args))


def test_registry_has_five_tools():
    names = [t.name for t in get_registry().list()]
    assert len(names) == 5
    assert "create_ticket" in names


def test_lookup_employee():
    r = invoke("lookup_employee", {"query": "Support"})
    assert r["status"] == "ok"
    assert r["count"] >= 1


def test_argument_validation():
    r = invoke("create_ticket", {})
    assert r["status"] == "error"
    assert "title" in r["error"]


def test_unknown_tool():
    r = invoke("no_such_tool", {})
    assert r["status"] == "error"


def test_sql_is_read_only():
    r = invoke("query_database", {"sql": "DELETE FROM employees"})
    assert r["status"] == "error"
    assert "read-only" in r["error"]
    # A legitimate SELECT works
    ok = invoke("query_database", {"sql": "SELECT COUNT(*) AS n FROM employees"})
    assert ok["status"] == "ok"
    assert ok["rows"][0]["n"] >= 10


def test_list_tickets():
    r = invoke("list_tickets", {"status": "all", "limit": 5})
    assert r["status"] == "ok"
    assert r["count"] <= 5
    assert r["total_in_database"] >= 22


def test_approval_gate_and_execute(client):
    """Write tools pause for approval, then execute on `/approve`."""
    from app.memory.store import MemoryStore
    from app.agents.execution import ExecutionAgent

    mem = MemoryStore()
    tid = MemoryStore.generate_id()
    mem.set_task_context(tid, {"session_id": "test-approval"})
    agent = ExecutionAgent()

    res = asyncio.run(
        agent.run(
            tid,
            "test-approval",
            {"query": "create a ticket for a broken keyboard", "auto_approve": False},
        )
    )
    assert res["status"] == "pending_approval"
    assert res["requires_approval"] is True
    assert res["tool"] == "create_ticket"

    r = client.post(f"/api/v1/tasks/{tid}/approve")
    assert r.status_code == 200
    assert r.json()["status"] == "approved"
    assert r.json()["result"]["status"] == "ok"
    assert r.json()["result"]["ticket_id"]

    # A second approval must be rejected (no pending action left).
    assert client.post(f"/api/v1/tasks/{tid}/approve").status_code == 409


def test_reject_does_not_execute(client):
    from app.memory.store import MemoryStore
    from app.agents.execution import ExecutionAgent

    mem = MemoryStore()
    tid = MemoryStore.generate_id()
    mem.set_task_context(tid, {"session_id": "test-reject"})
    agent = ExecutionAgent()
    res = asyncio.run(
        agent.run(
            tid,
            "test-reject",
            {"query": "create a support ticket for broken monitor", "auto_approve": False},
        )
    )
    assert res["status"] == "pending_approval"

    r = client.post(f"/api/v1/tasks/{tid}/reject")
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    assert client.post(f"/api/v1/tasks/{tid}/reject").status_code == 409

    # Ensure the rejected action did not leak into the database.
    from app.config import get_settings

    conn = sqlite3.connect(get_settings().sqlite_path)
    n = conn.execute(
        "SELECT COUNT(*) FROM it_tickets WHERE title LIKE '%broken monitor%'"
    ).fetchone()[0]
    conn.close()
    assert n == 0
