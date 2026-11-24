"""Knowledge-aware ITSM workflow for chat requests.

1. Classify the issue (Category, Priority, Department).
2. Search approved IT documentation for troubleshooting steps.
3. Create a service request that enters the admin approval queue.
4. Return a clear response with ticket ID, suggested steps and SLA.
"""

import logging
import re
from typing import Any, Dict, List, Optional

from app.agents.classifier import ClassificationAgent
from app.database import itsm as db
from app.database import users as user_db

logger = logging.getLogger(__name__)

_TRACK_PATTERNS = re.compile(
    r"\b(track|status of|check my|my tickets|my requests|ticket status|update on)\b", re.I
)


async def troubleshooting_hints(message: str, top_k: int = 2) -> List[Dict[str, Any]]:
    """Retrieve IT documentation snippets without an extra LLM call."""
    try:
        from app.rag.store import get_vector_store

        store = get_vector_store()
        if not store.available:
            await store.initialize()
        if store.available:
            results = await store.search(message, top_k=top_k)
            return [
                {"source": r.get("source", ""), "text": (r.get("text") or "")[:500]}
                for r in results
                if r.get("text")
            ]
    except Exception as exc:  # pragma: no cover - optional retrieval
        logger.debug("Vector troubleshooting lookup skipped: %s", exc)

    try:
        from app.rag.keyword import keyword_search

        return [
            {"source": r.get("source", ""), "text": (r.get("text") or "")[:500]}
            for r in keyword_search(message, top_k=top_k)
        ]
    except Exception:  # pragma: no cover
        return []


async def run_itsm_workflow(
    message: str,
    session_id: str,
    task_id: str,
    requester: str = "employee",
) -> Dict[str, Any]:
    """Run classification + knowledge lookup + ticket creation or tracking."""
    # --- Tracking path (read-only) -------------------------------------
    if _TRACK_PATTERNS.search(message):
        tickets = db.list_tickets(requester=requester, limit=10)
        if not tickets:
            tickets = db.list_tickets(limit=10)
        return {
            "mode": "tracking",
            "tickets": tickets,
            "message": (
                f"Found {len(tickets)} service request(s)." if tickets
                else "No service requests found yet."
            ),
        }

    # --- Create path ---------------------------------------------------
    classification = await ClassificationAgent().run(
        task_id=task_id, session_id=session_id, input_data={"message": message}
    )
    hints = await troubleshooting_hints(message)
    ticket_data = dict(
        title=classification.get("issue_type") or message[:80],
        description=message,
        category=classification.get("category", "other"),
        issue_type=classification.get("issue_type", ""),
        suggested_priority=classification.get("suggested_priority", "medium"),
        requester=requester,
        requester_department="",
        confidence=classification.get("confidence"),
        classification_reasoning=classification.get("reasoning", ""),
    )
    try:
        from app.api.admin_routes import apply_user_rules

        user = user_db.get_by_username(requester) if requester != "employee" else None
        if user:
            ticket_data["requester_department"] = user.get("department", "")
        rules = apply_user_rules(ticket_data)
        if rules.get("priority"):
            ticket_data["suggested_priority"] = rules["priority"]
            classification["priority_source"] = "admin_rule"
    except Exception:  # pragma: no cover - rules are optional
        pass

    ticket = db.create_ticket(**ticket_data)
    return {
        "mode": "create",
        "ticket": ticket,
        "classification": classification,
        "hints": hints,
        "message": (
            f"I classified this as **{classification.get('category')}** with "
            f"suggested priority **{ticket.get('priority')}** and created "
            f"**{ticket.get('ticket_id')}**. It is now in the administrator "
            "approval queue. Suggested troubleshooting steps are below while you wait."
        ),
    }
