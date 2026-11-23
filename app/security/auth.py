"""Authentication and role-based access control (Phase 5).

**Default is OFF** — with ``AUTH_ENABLED=false`` every route is open, exactly
like before, so nothing breaks for local/demo use.

When enabled, clients send an API key either in the ``X-API-Key`` header or as
``Authorization: Bearer <key>``. Keys map to roles via ``API_KEYS``:

    API_KEYS="secret-admin:admin,secret-user:user,secret-view:viewer"

Roles (lowest to highest)::

    viewer   read-only: health, tools list, kb list, search, history, audit(?)
    user     viewer + chat, ingest
    admin    user + approve/reject actions, delete documents, read audit log

Audit every failure so brute-force attempts leave a trail.
"""

import logging
from enum import IntEnum
from typing import Any, Dict, Optional

from fastapi import Depends, Header, Request
from fastapi.security.utils import get_authorization_scheme_param

from app.monitoring.audit import audit_record

logger = logging.getLogger(__name__)


class Role(IntEnum):
    """Role ranks; higher value = more privileges."""

    VIEWER = 1
    USER = 2
    ADMIN = 3


def _parse_keys(raw: str) -> Dict[str, Role]:
    mapping: Dict[str, Role] = {}
    for part in (raw or "").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        key, role = part.split(":", 1)
        key = key.strip()
        role = role.strip().lower()
        if not key:
            continue
        try:
            mapping[key] = Role[role.upper()]
        except KeyError:
            logger.warning("Unknown role in API_KEYS: %s (ignored)", role)
    return mapping


def lookup_role(key: str) -> Optional[Role]:
    """Return the role bound to an API key, or None when unknown."""
    from app.config import get_settings

    settings = get_settings()
    return _parse_keys(settings.api_keys).get(key)


def extract_key(x_api_key: Optional[str], authorization: Optional[str]) -> Optional[str]:
    if x_api_key and x_api_key.strip():
        return x_api_key.strip()
    scheme, param = get_authorization_scheme_param(authorization)
    if scheme.lower() == "bearer" and param:
        return param.strip()
    return None


def require_role(minimum: Role):
    """FastAPI dependency enforcing at least ``minimum`` role.

    When auth is disabled the dependency is a no-op returning ADMIN so
    existing callers keep working unchanged.
    """

    async def dependency(
        request: Request,
        x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
        authorization: Optional[str] = Header(default=None),
    ) -> Optional[Role]:
        from app.config import get_settings

        settings = get_settings()
        if not settings.auth_enabled:
            return Role.ADMIN  # auth off -> unrestricted (dev/demo mode)

        path = request.url.path
        method = request.method
        key = extract_key(x_api_key, authorization)

        if not key:
            audit_record(
                "auth_failed",
                reason="missing_key",
                path=path,
                method=method,
                required=minimum.name,
            )
            raise _unauthorized("Missing API key. Provide X-API-Key or Bearer token.")

        role = lookup_role(key)
        if role is None:
            audit_record(
                "auth_failed",
                reason="invalid_key",
                path=path,
                method=method,
                required=minimum.name,
            )
            raise _unauthorized("Invalid API key.")

        if role < minimum:
            audit_record(
                "auth_failed",
                reason="insufficient_role",
                path=path,
                method=method,
                role=role.name,
                required=minimum.name,
            )
            raise _forbidden(f"Role '{role.name}' lacks permission for {path}.")

        return role

    return dependency


def _unauthorized(detail: str):
    from fastapi import HTTPException

    return HTTPException(
        status_code=401,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _forbidden(detail: str):
    from fastapi import HTTPException

    return HTTPException(status_code=403, detail=detail)


# Convenient dependency singletons -------------------------------
require_viewer = Depends(require_role(Role.VIEWER))
require_user = Depends(require_role(Role.USER))
require_admin = Depends(require_role(Role.ADMIN))


def actor_summary(role: Optional[Any]) -> Dict[str, Any]:
    """Small dict for audit entries."""
    return {"role": role.name} if role else {"role": None}