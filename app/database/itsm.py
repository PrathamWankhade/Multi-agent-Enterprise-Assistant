"""ITSM persistence layer.

Deliberately uses the existing free SQLite backend (same file as the mock ERP)
so the project stays free and offline-capable. Schema and transitions model a
real ITSM lifecycle; PostgreSQL can replace the connection layer later without
changing workflow code.

Lifecycle:
    pending_approval -> approved | rejected
    approved         -> assigned | in_progress
    assigned         -> in_progress | resolved
    in_progress      -> resolved | assigned
    resolved         -> closed | in_progress
    closed / rejected -> (terminal)
"""

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.config import get_settings

logger = logging.getLogger(__name__)

_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY = False

VALID_TRANSITIONS: Dict[str, List[str]] = {
    "pending_approval": ["approved", "rejected"],
    "approved": ["assigned", "in_progress"],
    "assigned": ["in_progress", "resolved"],
    "in_progress": ["resolved", "assigned"],
    "resolved": ["closed", "in_progress"],
    "closed": [],
    "rejected": [],
}

DEFAULT_SLA_HOURS = {"critical": 2, "high": 8, "medium": 24, "low": 72}


# ---------------------------------------------------------------------------
# Connection / schema
# ---------------------------------------------------------------------------

def _connect() -> sqlite3.Connection:
    settings = get_settings()
    path = settings.sqlite_path
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_itsm_schema() -> None:
    """Create ITSM tables and seed technicians exactly once per process."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        conn = _connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS technicians (
                    tech_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL,
                    skills TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS service_requests (
                    ticket_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    category TEXT NOT NULL,
                    issue_type TEXT NOT NULL DEFAULT '',
                    suggested_priority TEXT NOT NULL DEFAULT 'medium',
                    priority TEXT NOT NULL DEFAULT 'medium',
                    status TEXT NOT NULL DEFAULT 'pending_approval',
                    requester TEXT NOT NULL,
                    requester_department TEXT NOT NULL DEFAULT '',
                    confidence REAL,
                    classification_reasoning TEXT NOT NULL DEFAULT '',
                    approved_by TEXT,
                    approved_at TEXT,
                    rejected_by TEXT,
                    rejected_at TEXT,
                    rejection_reason TEXT,
                    technician TEXT,
                    assigned_at TEXT,
                    resolution TEXT,
                    resolved_at TEXT,
                    sla_due_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ticket_status_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    actor_role TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (ticket_id) REFERENCES service_requests(ticket_id)
                );

                CREATE INDEX IF NOT EXISTS idx_sr_status ON service_requests(status);
                CREATE INDEX IF NOT EXISTS idx_sr_requester ON service_requests(requester);
                CREATE INDEX IF NOT EXISTS idx_hist_ticket ON ticket_status_history(ticket_id);
                """
            )
            count = conn.execute("SELECT COUNT(*) AS c FROM technicians").fetchone()["c"]
            if count == 0:
                conn.executemany(
                    "INSERT INTO technicians (tech_id, name, email, skills, active) "
                    "VALUES (?,?,?,?,1)",
                    [
                        ("T-01", "Noah Kim", "noah.kim@company.com", "hardware,access"),
                        ("T-02", "Liam O'Connor", "liam.oconnor@company.com", "network,hardware"),
                        ("T-03", "Sofia Ramirez", "sofia.ramirez@company.com", "software,access"),
                        ("T-04", "Raj Malhotra", "raj.malhotra@company.com", "network,software"),
                    ],
                )
            conn.commit()
            _SCHEMA_READY = True
        finally:
            conn.close()


def _sla_hours(priority: str) -> int:
    settings = get_settings()
    hours = dict(DEFAULT_SLA_HOURS)
    for part in (settings.itsm_sla_hours or "").split(","):
        if ":" in part:
            key, value = part.split(":", 1)
            try:
                hours[key.strip()] = int(value.strip())
            except ValueError:  # pragma: no cover - ignore malformed config
                continue
    return int(hours.get(priority, hours["medium"]))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return dict(row)


# ---------------------------------------------------------------------------
# Ticket creation and queries
# ---------------------------------------------------------------------------

def create_ticket(
    *,
    title: str,
    description: str,
    category: str,
    requester: str,
    issue_type: str = "",
    suggested_priority: str = "medium",
    requester_department: str = "",
    confidence: Optional[float] = None,
    classification_reasoning: str = "",
) -> Dict[str, Any]:
    """Create a service request in `pending_approval` state."""
    ensure_itsm_schema()
    priority = suggested_priority if suggested_priority in DEFAULT_SLA_HOURS else "medium"
    now = _now()
    with _connect() as conn:
        next_num = conn.execute(
            "SELECT COUNT(*) AS c FROM service_requests"
        ).fetchone()["c"] + 1
        ticket_id = f"SR-{next_num:05d}"
        due = (datetime.now(timezone.utc) + timedelta(hours=_sla_hours(priority))).isoformat(timespec="seconds")
        conn.execute(
            """INSERT INTO service_requests (
                ticket_id, title, description, category, issue_type,
                suggested_priority, priority, status, requester,
                requester_department, confidence, classification_reasoning,
                sla_due_at, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                ticket_id, title[:200], description[:4000], category, issue_type,
                priority, priority, "pending_approval", requester,
                requester_department, confidence, classification_reasoning[:500],
                due, now, now,
            ),
        )
        conn.execute(
            """INSERT INTO ticket_status_history
               (ticket_id, from_status, to_status, actor, actor_role, note, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (ticket_id, None, "pending_approval", requester, "employee",
             "Service request submitted", now),
        )
    return get_ticket(ticket_id) or {}  # type: ignore[return-value]


def get_ticket(ticket_id: str) -> Optional[Dict[str, Any]]:
    ensure_itsm_schema()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM service_requests WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def list_tickets(
    status: Optional[str] = None,
    requester: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    ensure_itsm_schema()
    query = "SELECT * FROM service_requests WHERE 1=1"
    params: List[Any] = []
    if status and status != "all":
        query += " AND status = ?"
        params.append(status)
    if requester:
        query += " AND requester = ?"
        params.append(requester)
    if category:
        query += " AND category = ?"
        params.append(category)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(int(limit), 200)))
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_history(ticket_id: str) -> List[Dict[str, Any]]:
    ensure_itsm_schema()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM ticket_status_history WHERE ticket_id = ? ORDER BY id",
            (ticket_id,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def approval_queue(limit: int = 100) -> List[Dict[str, Any]]:
    return list_tickets(status="pending_approval", limit=limit)


def technician_queue(technician: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    ensure_itsm_schema()
    query = (
        "SELECT * FROM service_requests WHERE status IN "
        "('approved','assigned','in_progress')"
    )
    params: List[Any] = []
    if technician:
        query += " AND technician = ?"
        params.append(technician)
    query += " ORDER BY priority DESC, created_at ASC LIMIT ?"
    params.append(max(1, min(int(limit), 200)))
    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_technicians(skill: Optional[str] = None) -> List[Dict[str, Any]]:
    ensure_itsm_schema()
    with _connect() as conn:
        if skill:
            rows = conn.execute(
                "SELECT * FROM technicians WHERE active = 1 AND skills LIKE ?",
                (f"%{skill}%",),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM technicians WHERE active = 1").fetchall()
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Lifecycle transitions
# ---------------------------------------------------------------------------

def transition_ticket(
    ticket_id: str,
    to_status: str,
    actor: str,
    actor_role: str = "",
    note: str = "",
    **updates: Any,
) -> Dict[str, Any]:
    """Validate and apply a status transition, recording it in history."""
    ticket = get_ticket(ticket_id)
    if ticket is None:
        raise ValueError(f"Unknown ticket: {ticket_id}")
    current = ticket["status"]
    if to_status not in VALID_TRANSITIONS.get(current, []):
        raise ValueError(
            f"Invalid transition {current!r} -> {to_status!r}. "
            f"Allowed: {VALID_TRANSITIONS.get(current, [])}"
        )

    allowed_updates = {
        "priority", "technician", "resolution", "approved_by", "approved_at",
        "rejected_by", "rejected_at", "rejection_reason", "assigned_at", "resolved_at",
    }
    clean_updates = {k: v for k, v in updates.items() if k in allowed_updates and v is not None}
    now = _now()
    set_clause = ", ".join([f"{k} = ?" for k in clean_updates] + ["status = ?", "updated_at = ?"])
    values = list(clean_updates.values()) + [to_status, now, ticket_id]

    with _connect() as conn:
        conn.execute(f"UPDATE service_requests SET {set_clause} WHERE ticket_id = ?", values)
        conn.execute(
            """INSERT INTO ticket_status_history
               (ticket_id, from_status, to_status, actor, actor_role, note, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (ticket_id, current, to_status, actor, actor_role, note[:500], now),
        )
    return get_ticket(ticket_id) or {}  # type: ignore[return-value]


def auto_assign(ticket_id: str, actor: str = "system") -> Optional[str]:
    """Assign the first available technician with a matching skill."""
    ticket = get_ticket(ticket_id)
    if not ticket:
        return None
    candidates = list_technicians(ticket.get("category"))
    if not candidates:
        candidates = list_technicians()
    if not candidates:
        return None
    tech = candidates[0]["name"]
    transition_ticket(
        ticket_id, "assigned", actor, "system",
        f"Auto-assigned by skill: {ticket.get('category')}",
        technician=tech, assigned_at=_now(),
    )
    return tech
