"""ITSM API: service requests, admin approvals and technician workflow."""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

from app.agents.classifier import ClassificationAgent
from app.api.schemas import ServiceRequestCreate, TicketDecision, TicketUpdate
from app.config import get_settings
from app.database import itsm as db
from app.memory.store import MemoryStore
from app.monitoring.audit import audit_record
from app.security.auth import require_admin, require_user, require_viewer

logger = logging.getLogger(__name__)

router = APIRouter()
settings = get_settings()


async def _troubleshooting_hints(message: str) -> List[Dict[str, Any]]:
    """Find relevant approved IT docs without an LLM round-trip."""
    try:
        from app.rag.store import get_vector_store

        store = get_vector_store()
        if not store.available:
            await store.initialize()
        if store.available:
            results = await store.search(message, top_k=2)
            return [
                {"source": r.get("source"), "text": (r.get("text") or "")[:400]}
                for r in results
            ]
    except Exception as exc:  # pragma: no cover - optional RAG
        logger.debug("Troubleshooting lookup skipped: %s", exc)

    try:
        from app.rag.keyword import keyword_search

        return [
            {"source": r.get("source"), "text": (r.get("text") or "")[:400]}
            for r in keyword_search(message, top_k=2)
        ]
    except Exception:  # pragma: no cover
        return []


@router.post("/itsm/requests", tags=["itsm"])
async def create_request(request: ServiceRequestCreate, actor=require_user):
    """Employee reports a problem: classify, find KB hints, create a pending ticket."""
    from app.api.admin_routes import apply_user_rules

    task_id = MemoryStore.generate_id()
    classification = await ClassificationAgent().run(
        task_id=task_id,
        session_id="itsm",
        input_data={"message": request.message},
    )
    hints = await _troubleshooting_hints(request.message)

    requester = request.requester
    department = request.department
    if actor is not None:
        requester = actor.username
        department = department or actor.department or ""

    ticket_data = dict(
        title=request.title or classification.get("issue_type") or request.message[:80],
        description=request.message,
        category=classification.get("category", "other"),
        issue_type=classification.get("issue_type", ""),
        suggested_priority=classification.get("suggested_priority", "medium"),
        requester=requester,
        requester_department=department,
        confidence=classification.get("confidence"),
        classification_reasoning=classification.get("reasoning", ""),
    )
    # Admin-defined per-user / per-department rules can set the initial priority.
    rules = apply_user_rules(ticket_data)
    if rules.get("priority"):
        ticket_data["suggested_priority"] = rules["priority"]
        classification["priority_source"] = "admin_rule"

    ticket = db.create_ticket(**ticket_data)
    audit_record(
        "itsm_request_created",
        ticket_id=ticket.get("ticket_id"),
        category=classification.get("category"),
        priority=ticket.get("priority"),
        requester=request.requester,
    )
    return {
        "ticket": ticket,
        "classification": classification,
        "troubleshooting_hints": hints,
        "message": (
            f"Ticket {ticket.get('ticket_id')} created and sent to admin approval. "
            "Suggested troubleshooting steps are included while you wait."
        ),
    }


@router.get("/itsm/tickets", tags=["itsm"])
async def list_service_requests(
    status: str = "all",
    category: str = "",
    requester: str = "",
    limit: int = 50,
    _role=require_viewer,
):
    tickets = db.list_tickets(
        status=status, category=category or None, requester=requester or None, limit=limit
    )
    return {"count": len(tickets), "tickets": tickets}


@router.get("/itsm/approvals", tags=["itsm"])
async def approval_queue(limit: int = 100, _role=require_admin):
    tickets = db.approval_queue(limit=limit)
    return {"count": len(tickets), "tickets": tickets}


@router.get("/itsm/technicians", tags=["itsm"])
async def technicians(skill: str = "", _role=require_viewer):
    return {"technicians": db.list_technicians(skill or None)}


@router.get("/itsm/tickets/{ticket_id}", tags=["itsm"])
async def track_ticket(ticket_id: str, _role=require_viewer):
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return {
        "ticket": ticket,
        "history": db.get_history(ticket_id),
    }


@router.get("/itsm/tickets/{ticket_id}/history", tags=["itsm"])
async def ticket_history(ticket_id: str, _role=require_viewer):
    if not db.get_ticket(ticket_id):
        raise HTTPException(status_code=404, detail="Ticket not found")
    return {"ticket_id": ticket_id, "history": db.get_history(ticket_id)}


@router.post("/itsm/tickets/{ticket_id}/approve", tags=["itsm"])
async def approve_ticket(ticket_id: str, decision: TicketDecision, role=require_admin):
    """Admin approves a request: optional priority override, then technician assignment."""
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if ticket["status"] != "pending_approval":
        raise HTTPException(status_code=409, detail="Ticket is not awaiting approval")

    overrides: Dict[str, Any] = {
        "approved_by": decision.actor,
        "approved_at": __import__("datetime").datetime.utcnow().isoformat(timespec="seconds"),
    }
    if decision.priority:
        from app.database.itsm import DEFAULT_SLA_HOURS

        if decision.priority not in DEFAULT_SLA_HOURS:
            raise HTTPException(status_code=400, detail="Invalid priority")
        overrides["priority"] = decision.priority
    db.transition_ticket(
        ticket_id, "approved", decision.actor, "admin",
        decision.note or "Approved by administrator", **overrides,
    )

    if decision.technician:
        db.transition_ticket(
            ticket_id, "assigned", decision.actor, "admin",
            f"Manually assigned to {decision.technician}",
            technician=decision.technician,
            assigned_at=__import__("datetime").datetime.utcnow().isoformat(timespec="seconds"),
        )
    else:
        db.auto_assign(ticket_id, actor=decision.actor)

    audit_record(
        "itsm_ticket_approved",
        ticket_id=ticket_id,
        actor=decision.actor,
        role=role.role if role else None,
        priority_override=decision.priority,
    )
    return {"status": "approved", "ticket": db.get_ticket(ticket_id), "history": db.get_history(ticket_id)}


@router.post("/itsm/tickets/{ticket_id}/reject", tags=["itsm"])
async def reject_ticket(ticket_id: str, decision: TicketDecision, role=require_admin):
    ticket = db.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if ticket["status"] != "pending_approval":
        raise HTTPException(status_code=409, detail="Ticket is not awaiting approval")
    db.transition_ticket(
        ticket_id, "rejected", decision.actor, "admin",
        decision.note or "Rejected by administrator",
        rejected_by=decision.actor,
        rejected_at=__import__("datetime").datetime.utcnow().isoformat(timespec="seconds"),
        rejection_reason=decision.note,
    )
    audit_record("itsm_ticket_rejected", ticket_id=ticket_id, actor=decision.actor,
                 role=role.role if role else None)
    return {"status": "rejected", "ticket": db.get_ticket(ticket_id)}


@router.patch("/itsm/tickets/{ticket_id}", tags=["itsm"])
async def update_ticket(ticket_id: str, update: TicketUpdate, role=require_user):
    """Technician/agent advances status and optionally records a resolution."""
    try:
        updates: Dict[str, Any] = {}
        if update.resolution:
            updates["resolution"] = update.resolution
        if update.status == "resolved":
            updates["resolved_at"] = __import__("datetime").datetime.utcnow().isoformat(timespec="seconds")
        ticket = db.transition_ticket(
            ticket_id, update.status, update.actor,
            role.role if role else "", update.note or "", **updates,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    audit_record("itsm_ticket_updated", ticket_id=ticket_id,
                 status=update.status, actor=update.actor)
    return {"status": "updated", "ticket": ticket, "history": db.get_history(ticket_id)}
