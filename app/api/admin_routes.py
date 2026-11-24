"""Admin endpoints: user-aware task assignment, priority control and routing rules."""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.database import itsm as db
from app.database import users as user_db
from app.monitoring.audit import audit_record
from app.security.common import _forbidden
from app.security.deps import Actor, get_actor

logger = logging.getLogger(__name__)
router = APIRouter()

# Assignment rules are persisted in a small JSON file alongside the SQLite DB.
_RULES_FILENAME = "assignment_rules.json"


def _rules_path() -> str:
    import os

    from app.config import get_settings

    path = get_settings().sqlite_path
    return os.path.join(os.path.dirname(path) or ".", _RULES_FILENAME)


def _load_rules() -> Dict[str, Any]:
    try:
        with open(_rules_path(), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"by_user": {}, "by_department": {}, "defaults": {}}


def _save_rules(rules: Dict[str, Any]) -> None:
    with open(_rules_path(), "w", encoding="utf-8") as fh:
        json.dump(rules, fh, indent=2)


def _admin(actor) -> Actor:
    from app.config import get_settings

    if actor is None:
        if not get_settings().auth_enabled:
            return Actor(username="open-mode", role="admin", source="open")
        raise _forbidden("Admin role required")
    if actor.role != "admin":
        raise _forbidden("Admin role required")
    return actor


class AssignRequest(BaseModel):
    technician: Optional[str] = Field(default=None, description="Technician name (auto-assigned if omitted)")
    priority: Optional[str] = Field(default=None, description="Admin priority override")
    note: str = ""
    status: str = Field(default="assigned", description="assigned | in_progress | resolved")


class RuleRequest(BaseModel):
    username: Optional[str] = None
    department: Optional[str] = None
    priority: str
    technician: Optional[str] = None
    category: Optional[str] = None


def apply_user_rules(ticket: Dict[str, Any]) -> Dict[str, Any]:
    """Apply stored per-user/department routing rules to a new ticket."""
    rules = _load_rules()
    requester = (ticket.get("requester") or "").lower()
    department = (ticket.get("requester_department") or "").lower()
    override: Dict[str, Any] = {}
    if requester in rules.get("by_user", {}):
        override.update(rules["by_user"][requester])
    elif department in rules.get("by_department", {}):
        override.update(rules["by_department"][department])
    return override


@router.get("/admin/tickets", tags=["admin"])
async def admin_tickets(
    status: str = "all", limit: int = 100, actor: Optional[Actor] = Depends(get_actor)
):
    """All service requests with requester identity for user-aware decisions."""
    _admin(actor)
    tickets = db.list_tickets(status=status, limit=limit)
    enriched = []
    for t in tickets:
        u = user_db.get_by_username(t.get("requester", ""))
        enriched.append(
            {
                **t,
                "requester_role": u.get("role") if u else "external",
                "requester_department": u.get("department") or t.get("requester_department", ""),
                "rules": apply_user_rules(t),
            }
        )
    return {"count": len(enriched), "tickets": enriched}


@router.post("/admin/tickets/{ticket_id}/assign", tags=["admin"])
async def admin_assign(
    ticket_id: str, request: AssignRequest, actor: Optional[Actor] = Depends(get_actor)
):
    """Admin assigns a task and sets priority according to the requesting user."""
    admin = _admin(actor)
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if ticket["status"] not in {"pending_approval", "approved", "assigned", "in_progress"}:
        raise HTTPException(status_code=409, detail="Ticket cannot be reassigned in this status")

    overrides: Dict[str, Any] = {}
    if request.priority:
        from app.database.itsm import DEFAULT_SLA_HOURS

        if request.priority not in DEFAULT_SLA_HOURS:
            raise HTTPException(status_code=400, detail="Invalid priority")
        overrides["priority"] = request.priority

    current = ticket["status"]
    if current == "pending_approval":
        overrides.update(
            {
                "approved_by": admin.username,
                "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )
        db.transition_ticket(ticket_id, "approved", admin.username, "admin",
                             request.note or "Approved with admin assignment", **overrides)
        current = "approved"

    notes = request.note or f"Assigned by admin {admin.username}"
    if request.technician:
        overrides = {**overrides, "technician": request.technician,
                     "assigned_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        db.transition_ticket(ticket_id, "assigned", admin.username, "admin", notes, **overrides)
    elif current == "approved":
        db.auto_assign(ticket_id, actor=admin.username)

    if request.status == "in_progress":
        db.transition_ticket(ticket_id, "in_progress", admin.username, "admin", notes)
    elif request.status == "resolved":
        db.transition_ticket(
            ticket_id, "resolved", admin.username, "admin", notes,
            resolution=request.note or "Resolved by administrator",
            resolved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    audit_record(
        "admin_task_assigned",
        ticket_id=ticket_id,
        actor=admin.username,
        requester=ticket.get("requester"),
        technician=request.technician,
        priority=request.priority,
    )
    return {
        "status": "assigned",
        "ticket": db.get_ticket(ticket_id),
        "history": db.get_history(ticket_id),
    }


@router.get("/admin/rules", tags=["admin"])
async def get_rules(actor: Optional[Actor] = Depends(get_actor)):
    _admin(actor)
    return _load_rules()


@router.post("/admin/rules", tags=["admin"])
async def set_rule(request: RuleRequest, actor: Optional[Actor] = Depends(get_actor)):
    """Define default priority/technician for a user or department."""
    admin = _admin(actor)
    if not request.username and not request.department:
        raise HTTPException(status_code=400, detail="Provide username or department")
    rules = _load_rules()
    entry = {"priority": request.priority}
    if request.technician:
        entry["technician"] = request.technician
    if request.category:
        entry["category"] = request.category
    if request.username:
        rules.setdefault("by_user", {})[request.username.lower()] = entry
    else:
        rules.setdefault("by_department", {})[request.department.lower()] = entry
    _save_rules(rules)
    audit_record("admin_rule_set", actor=admin.username,
                 username=request.username, department=request.department,
                 priority=request.priority)
    return rules


@router.delete("/admin/rules", tags=["admin"])
async def clear_rules(actor: Optional[Actor] = Depends(get_actor)):
    _admin(actor)
    _save_rules({"by_user": {}, "by_department": {}, "defaults": {}})
    return {"status": "cleared"}
