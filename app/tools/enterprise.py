"""Mock enterprise tools backed by a local SQLite database.

These simulate the systems an enterprise assistant would integrate with:
- HR directory lookups
- IT support ticket creation and listing
- Read-only SQL over the same database
- CSV report generation

The SQLite database is created and seeded on first use so the project works
with zero external dependencies. Write tools are registered with
``risk="write"`` which forces the human-approval gate in the Execution Agent.
"""

import csv
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.config import get_settings
from app.tools.registry import ToolParameter, ToolSpec, get_registry

logger = logging.getLogger(__name__)

_SEEDED = False


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _db_path() -> str:
    settings = get_settings()
    path = settings.sqlite_path
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return path


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def ensure_database() -> None:
    """Create tables and seed sample data exactly once per process."""
    global _SEEDED
    if _SEEDED:
        return
    conn = _connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS employees (
                employee_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                department TEXT NOT NULL,
                role TEXT NOT NULL,
                location TEXT NOT NULL,
                manager TEXT,
                leave_balance INTEGER NOT NULL DEFAULT 0,
                start_date TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS it_tickets (
                ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                priority TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                requester TEXT NOT NULL,
                assignee TEXT,
                description TEXT,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            );
            """
        )
        count = conn.execute("SELECT COUNT(*) AS c FROM employees").fetchone()["c"]
        if count == 0:
            _seed(conn)
            conn.commit()
            logger.info("Seeded mock enterprise SQLite database")
        _SEEDED = True
    finally:
        conn.close()


def _seed(conn: sqlite3.Connection) -> None:
    now = datetime.utcnow()
    employees = [
        ("E-1001", "Ava Patel", "Engineering", "Senior Engineer", "London", "Marcus Chen", 14, "2021-03-15"),
        ("E-1002", "Liam O'Connor", "Engineering", "Platform Engineer", "Dublin", "Marcus Chen", 8, "2022-07-01"),
        ("E-1003", "Sofia Ramirez", "Support", "Support Lead", "Madrid", "Priya Nair", 19, "2020-01-20"),
        ("E-1004", "Noah Kim", "Support", "Support Agent", "Seoul", "Sofia Ramirez", 5, "2023-05-09"),
        ("E-1005", "Emma Johansson", "HR", "HR Business Partner", "Stockholm", "Dana White", 22, "2019-11-02"),
        ("E-1006", "Raj Malhotra", "Finance", "Financial Analyst", "Mumbai", "Helen Zhao", 11, "2022-02-14"),
        ("E-1007", "Chloe Dubois", "Marketing", "Content Strategist", "Paris", "Tomas Vega", 16, "2023-09-18"),
        ("E-1008", "Marcus Chen", "Engineering", "Engineering Manager", "London", "Dana White", 3, "2018-06-11"),
        ("E-1009", "Priya Nair", "Support", "Support Manager", "Bangalore", "Dana White", 9, "2019-08-25"),
        ("E-1010", "Dana White", "Executive", "VP Operations", "New York", None, 27, "2017-04-03"),
    ]
    conn.executemany(
        "INSERT INTO employees VALUES (?,?,?,?,?,?,?,?)", employees
    )

    categories = [
        ("password reset", "access"),
        ("laptop won't boot", "hardware"),
        ("VPN disconnects", "network"),
        ("password reset", "access"),
        ("software install request", "software"),
        ("printer jam", "hardware"),
        ("VPN disconnects", "network"),
        ("email quota full", "access"),
        ("password reset", "access"),
        ("monitor flickering", "hardware"),
        ("VPN disconnects", "network"),
        ("password reset", "access"),
        ("shared drive access", "access"),
        ("laptop won't boot", "hardware"),
        ("password reset", "access"),
        ("VPN disconnects", "network"),
        ("software install request", "software"),
        ("password reset", "access"),
        ("email quota full", "access"),
        ("password reset", "access"),
        ("docking station faulty", "hardware"),
        ("VPN disconnects", "network"),
    ]
    requesters = [e[1] for e in employees]
    priorities = ["low", "medium", "high", "critical"]
    for i, (title, category) in enumerate(categories):
        created = now - timedelta(days=(i % 30) + 1, hours=i % 12)
        resolved = created + timedelta(hours=2 + (i % 20)) if i % 4 else None
        status = "resolved" if resolved else "open"
        conn.execute(
            "INSERT INTO it_tickets (title, category, priority, status, requester, "
            "assignee, description, created_at, resolved_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                title.title(),
                category,
                priorities[i % 4],
                status,
                requesters[i % len(requesters)],
                "Noah Kim" if status == "resolved" else None,
                f"User reported: {title}.",
                created.isoformat(timespec="seconds"),
                resolved.isoformat(timespec="seconds") if resolved else None,
            ),
        )


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def _tool_lookup_employee(query: str) -> Dict[str, Any]:
    """Search the HR directory by name, ID or department."""
    ensure_database()
    like = f"%{query}%"
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM employees WHERE employee_id LIKE ? OR name LIKE ? "
            "OR department LIKE ? OR role LIKE ? ORDER BY name LIMIT 10",
            (like, like, like, like),
        ).fetchall()
    finally:
        conn.close()
    results = [dict(r) for r in rows]
    return {
        "count": len(results),
        "employees": results,
        "note": "Read-only HR directory lookup." if results else f"No employee matched '{query}'.",
    }


def _tool_create_ticket(
    title: str,
    requester: str,
    category: str = "other",
    priority: str = "medium",
    description: str = "",
) -> Dict[str, Any]:
    """Open an IT support ticket (write action — requires approval)."""
    ensure_database()
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO it_tickets (title, category, priority, status, requester, "
            "description, created_at) VALUES (?,?,?,?,?,?,?)",
            (
                title,
                category,
                priority,
                "open",
                requester,
                description,
                datetime.utcnow().isoformat(timespec="seconds"),
            ),
        )
        conn.commit()
        ticket_id = cur.lastrowid
    finally:
        conn.close()
    return {
        "ticket_id": ticket_id,
        "message": f"Ticket #{ticket_id} created: '{title}' ({priority}).",
    }


def _tool_list_tickets(
    status: str = "all",
    category: str = "",
    limit: int = 50,
) -> Dict[str, Any]:
    """List IT support tickets with optional status/category filters."""
    ensure_database()
    query = "SELECT * FROM it_tickets WHERE 1=1"
    params: List[Any] = []
    if status and status != "all":
        query += " AND status = ?"
        params.append(status)
    if category:
        query += " AND category = ?"
        params.append(category)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(int(limit), 200)))

    conn = _connect()
    try:
        rows = conn.execute(query, params).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) AS c FROM it_tickets"
        ).fetchone()["c"]
    finally:
        conn.close()
    tickets = [dict(r) for r in rows]
    return {
        "count": len(tickets),
        "total_in_database": total,
        "tickets": tickets,
    }


def _tool_query_database(sql: str) -> Dict[str, Any]:
    """Run a READ-ONLY SQL query against the enterprise database.

    Only single SELECT/WITH statements are allowed; anything that could
    mutate data is rejected before execution.
    """
    ensure_database()
    cleaned = sql.strip().rstrip(";")
    first_word = cleaned.split(None, 1)[0].lower() if cleaned else ""
    if first_word not in {"select", "with"}:
        return {
            "error": "Only read-only SELECT queries are permitted.",
            "sql": sql,
        }
    # Belt and braces: reject mutation keywords anywhere in the statement.
    lowered = cleaned.lower()
    for banned in (" insert ", " update ", " delete ", " drop ", " alter ", " create ", " attach "):
        if banned in f" {lowered} ":
            return {"error": f"Statement contains forbidden keyword:{banned.strip()}.", "sql": sql}

    conn = _connect()
    try:
        cursor = conn.execute(cleaned)
        rows = cursor.fetchmany(200)
        columns = [d[0] for d in cursor.description or []]
    except Exception as exc:
        return {"error": f"SQL error: {exc}", "sql": sql}
    finally:
        conn.close()

    data = [dict(zip(columns, [r[c] for c in columns])) for r in rows]
    return {"columns": columns, "rows": data, "row_count": len(data), "sql": cleaned}


def _tool_generate_report(
    report_name: str,
    sql: str = "",
    format: str = "csv",
) -> Dict[str, Any]:
    """Run a read-only query and save the result as a report file (write action)."""
    settings = get_settings()
    result = _tool_query_database(sql or f"SELECT * FROM it_tickets ORDER BY created_at DESC LIMIT 100")
    if "error" in result:
        return {"error": result["error"]}

    os.makedirs(settings.reports_dir, exist_ok=True)
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in report_name)[:60]
    ext = "csv" if format == "csv" else "csv"
    filename = f"{safe_name}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.{ext}"
    path = os.path.join(settings.reports_dir, filename)

    columns = result["columns"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows(result["rows"])

    return {
        "path": path,
        "filename": filename,
        "rows_written": result["row_count"],
        "message": f"Report saved with {result['row_count']} rows.",
    }


# ---------------------------------------------------------------------------
# ITSM workflow tool handlers (Phase 7)
# ---------------------------------------------------------------------------

def _tool_create_service_request(
    title: str,
    description: str,
    requester: str,
    category: str = "other",
    issue_type: str = "",
    priority: str = "medium",
    department: str = "",
) -> Dict[str, Any]:
    """Create an ITSM service request — starts in the admin approval queue."""
    from app.database.itsm import create_ticket

    ticket = create_ticket(
        title=title,
        description=description,
        category=category,
        requester=requester,
        issue_type=issue_type,
        suggested_priority=priority,
        requester_department=department,
        confidence=0.8,
        classification_reasoning="Created through the ITSM workflow.",
    )
    return {
        "ticket_id": ticket.get("ticket_id"),
        "status": ticket.get("status"),
        "priority": ticket.get("priority"),
        "sla_due_at": ticket.get("sla_due_at"),
        "message": (
            f"Service request {ticket.get('ticket_id')} created and sent to the "
            "IT administration approval queue."
        ),
        "note": f"Pending admin approval · SLA due {ticket.get('sla_due_at')}",
    }


def _tool_track_ticket(ticket_id: str = "", requester: str = "") -> Dict[str, Any]:
    """Track service requests by ID or requester."""
    from app.database.itsm import get_history, get_ticket, list_tickets

    if ticket_id:
        ticket = get_ticket(ticket_id)
        if not ticket:
            return {"error": f"Ticket {ticket_id} not found", "ticket_id": ticket_id}
        return {
            "ticket_id": ticket_id,
            "status": ticket.get("status"),
            "priority": ticket.get("priority"),
            "technician": ticket.get("technician"),
            "resolution": ticket.get("resolution"),
            "sla_due_at": ticket.get("sla_due_at"),
            "history": get_history(ticket_id),
        }
    tickets = list_tickets(requester=requester or None, limit=20)
    return {
        "count": len(tickets),
        "tickets": [
            {
                "ticket_id": t["ticket_id"],
                "title": t["title"],
                "status": t["status"],
                "priority": t["priority"],
                "technician": t.get("technician"),
            }
            for t in tickets
        ],
    }


def _tool_list_service_requests(
    status: str = "all",
    category: str = "",
    limit: int = 50,
) -> Dict[str, Any]:
    """List service requests (admin/technician visibility controlled by API)."""
    from app.database.itsm import list_tickets

    tickets = list_tickets(status=status, category=category or None, limit=limit)
    return {
        "count": len(tickets),
        "tickets": [
            {
                "ticket_id": t["ticket_id"],
                "title": t["title"],
                "category": t["category"],
                "priority": t["priority"],
                "status": t["status"],
                "requester": t["requester"],
                "technician": t.get("technician"),
            }
            for t in tickets
        ],
    }


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_tools() -> None:
    registry = get_registry()
    if registry.get("lookup_employee"):
        return  # already registered
    ensure_database()

    registry.register(
        ToolSpec(
            name="lookup_employee",
            description="Search the HR employee directory by name, employee ID, department or role.",
            risk="read",
            parameters=[
                ToolParameter("query", "string", "Name, employee ID, department or role to search for."),
            ],
            handler=_tool_lookup_employee,
        )
    )
    registry.register(
        ToolSpec(
            name="list_tickets",
            description="List IT support tickets, optionally filtered by status (open/resolved/all) or category.",
            risk="read",
            parameters=[
                ToolParameter("status", "string", "Filter by status.", required=False, default="all",
                              enum=["all", "open", "resolved"]),
                ToolParameter("category", "string", "Filter by category.", required=False, default=""),
                ToolParameter("limit", "integer", "Maximum tickets to return.", required=False, default=50),
            ],
            handler=_tool_list_tickets,
        )
    )
    registry.register(
        ToolSpec(
            name="query_database",
            description="Run a read-only SQL SELECT query against the enterprise database "
                        "(tables: employees, it_tickets).",
            risk="read",
            parameters=[
                ToolParameter("sql", "string", "A single read-only SELECT statement."),
            ],
            handler=_tool_query_database,
        )
    )
    registry.register(
        ToolSpec(
            name="create_ticket",
            description="Open a new IT support ticket. This is a WRITE action and requires human approval.",
            risk="write",
            parameters=[
                ToolParameter("title", "string", "Short summary of the issue."),
                ToolParameter("requester", "string", "Name or employee ID of the person requesting help."),
                ToolParameter("category", "string", "Issue category.", required=False, default="other",
                              enum=["access", "hardware", "network", "software", "other"]),
                ToolParameter("priority", "string", "Ticket priority.", required=False, default="medium",
                              enum=["low", "medium", "high", "critical"]),
                ToolParameter("description", "string", "Detailed description of the issue.",
                              required=False, default=""),
            ],
            handler=_tool_create_ticket,
        )
    )
    registry.register(
        ToolSpec(
            name="generate_report",
            description="Run a read-only query and save the results as a CSV report file. "
                        "This is a WRITE action (creates a file) and requires human approval.",
            risk="write",
            parameters=[
                ToolParameter("report_name", "string", "Name for the report file."),
                ToolParameter("sql", "string", "Read-only SELECT statement producing the report rows.",
                              required=False, default=""),
                ToolParameter("format", "string", "Report format.", required=False, default="csv",
                              enum=["csv"]),
            ],
            handler=_tool_generate_report,
        )
    )

    # --- ITSM workflow tools (own approval lifecycle, no generic gate) ---
    registry.register(
        ToolSpec(
            name="create_service_request",
            description="Create an IT service request/ticket. The ticket enters the administrator "
                        "approval queue; an admin must approve it before a technician is assigned.",
            risk="workflow",
            parameters=[
                ToolParameter("title", "string", "Short summary of the issue."),
                ToolParameter("description", "string", "Full description of the problem."),
                ToolParameter("requester", "string", "Name of the employee reporting the issue."),
                ToolParameter("category", "string", "Issue category.", required=False, default="other",
                              enum=["hardware", "software", "network", "access", "other"]),
                ToolParameter("issue_type", "string", "Specific issue label.", required=False, default=""),
                ToolParameter("priority", "string", "Suggested priority (admin may change it).",
                              required=False, default="medium",
                              enum=["critical", "high", "medium", "low"]),
                ToolParameter("department", "string", "Requester department.", required=False, default=""),
            ],
            handler=_tool_create_service_request,
        )
    )
    registry.register(
        ToolSpec(
            name="track_ticket",
            description="Track a service request by ticket ID, or list requests for a requester.",
            risk="read",
            parameters=[
                ToolParameter("ticket_id", "string", "Ticket ID, e.g. SR-00001.", required=False, default=""),
                ToolParameter("requester", "string", "Employee name to list tickets for.",
                              required=False, default=""),
            ],
            handler=_tool_track_ticket,
        )
    )
    registry.register(
        ToolSpec(
            name="list_service_requests",
            description="List service requests with optional status/category filters.",
            risk="read",
            parameters=[
                ToolParameter("status", "string", "Filter by lifecycle status.",
                              required=False, default="all",
                              enum=["all", "pending_approval", "approved", "assigned",
                                    "in_progress", "resolved", "closed", "rejected"]),
                ToolParameter("category", "string", "Filter by category.", required=False, default=""),
                ToolParameter("limit", "integer", "Maximum results.", required=False, default=50),
            ],
            handler=_tool_list_service_requests,
        )
    )
    logger.info("Registered %d enterprise tools", len(registry.list()))
