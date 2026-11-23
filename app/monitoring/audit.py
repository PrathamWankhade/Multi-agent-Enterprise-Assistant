"""Audit trail: structured, append-only record of security-relevant events.

Writes one JSON object per line to ``{audit_dir}/audit-YYYY-MM-DD.log`` and
keeps a bounded in-memory ring buffer so ``GET /api/v1/audit`` can show
recent activity without reading files.
"""

import json
import logging
import os
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("audit")

_RING: deque = deque(maxlen=500)
_LOCK = threading.Lock()
_CONFIGURED = False


def _ensure_dir(audit_dir: str) -> str:
    try:
        os.makedirs(audit_dir, exist_ok=True)
        return audit_dir
    except OSError as exc:  # pragma: no cover - read-only fs etc.
        logger.warning("Cannot create audit directory %s: %s", audit_dir, exc)
        return audit_dir


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def audit_record(event: str, **fields: Any) -> Dict[str, Any]:
    """Record one audit event. Never raises — auditing must not break a request."""
    entry: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": event,
    }
    entry.update({k: v for k, v in fields.items() if v is not None})

    with _LOCK:
        _RING.append(entry)
        try:
            from app.config import get_settings

            settings = get_settings()
            path = os.path.join(settings.audit_dir, f"audit-{_today()}.log")
            _ensure_dir(settings.audit_dir)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, default=str) + "\n")
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Audit file write failed: %s", exc)

    logger.info("AUDIT %s %s", event, json.dumps(entry.get("actor") or "", default=str))
    return entry


def read_audit(limit: int = 100, event_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return the most recent audit entries, newest last."""
    with _LOCK:
        items = list(_RING)
    if event_filter:
        items = [e for e in items if e.get("event") == event_filter]
    return items[-limit:] if limit else items


def reset_audit() -> None:
    """Testing helper: clear the ring buffer."""
    with _LOCK:
        _RING.clear()