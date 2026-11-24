"""Authentication dependencies.

Backwards-compatible exports (``require_user``, ``require_admin`` …) now
resolve JWT bearer tokens AND static API keys through the unified actor
dependency, so every existing route gains JWT support automatically.
"""

import logging
from typing import Optional

from fastapi import Depends, Request

from app.monitoring.audit import audit_record
from app.security.common import Role, _forbidden, _unauthorized, extract_key, lookup_role
from app.security.deps import Actor, get_actor

logger = logging.getLogger(__name__)


def require_role(minimum: Role):
    """Enforce at least ``minimum`` role; no-op when auth is disabled."""

    async def dependency(
        request: Request, actor: Optional[Actor] = Depends(get_actor)
    ) -> Optional[Actor]:
        if actor is None:
            from app.config import get_settings

            if get_settings().auth_enabled:
                raise _unauthorized("Authentication required.")
            return None  # open mode
        minimum_name = minimum.name.lower()
        required = {
            "viewer": 0,
            "user": 1,
            "technician": 2,
            "admin": 3,
        }.get(minimum_name, 99)
        if actor.rank < required:
            audit_record(
                "auth_failed",
                reason="insufficient_role",
                path=request.url.path,
                method=request.method,
                role=actor.role,
                required=minimum_name,
            )
            raise _forbidden(f"Role '{actor.role}' lacks permission for {request.url.path}.")
        return actor

    return dependency


require_viewer = Depends(require_role(Role.VIEWER))
require_user = Depends(require_role(Role.USER))
require_tech = Depends(require_role(Role.TECHNICIAN))
require_admin = Depends(require_role(Role.ADMIN))


def actor_summary(role=None) -> dict:
    return {"role": getattr(role, "role", None) or getattr(role, "name", None)}
