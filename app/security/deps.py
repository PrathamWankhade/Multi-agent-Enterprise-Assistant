"""Unified auth dependencies: JWT bearer tokens OR static API keys."""

from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header, Request
from fastapi.security.utils import get_authorization_scheme_param

from app.config import get_settings
from app.database.users import ROLE_RANK
from app.monitoring.audit import audit_record
from app.security.common import Role, _forbidden, _unauthorized, extract_key, lookup_role
from app.security.jwt_auth import user_from_token


@dataclass
class Actor:
    username: str
    role: str
    department: str = ""
    source: str = "jwt"
    user_id: str = ""

    @property
    def rank(self) -> int:
        from app.database.users import ROLE_RANK

        return ROLE_RANK.get(self.role, -1)

    def to_dict(self):
        return {
            "username": self.username,
            "role": self.role,
            "department": self.department,
            "user_id": self.user_id,
            "source": self.source,
        }


async def get_actor(
    request: Request,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None),
) -> Optional[Actor]:
    """Resolve the caller from JWT or API key. None when auth is disabled."""
    settings = get_settings()
    scheme, param = get_authorization_scheme_param(authorization)
    if scheme.lower() == "bearer" and param:
        user = user_from_token(param)
        if user:
            return Actor(
                username=user["username"],
                role=user["role"],
                department=user.get("department", ""),
                user_id=user.get("user_id", ""),
                source="jwt",
            )
        # Not a JWT — try treating it as a static API key.
        role = lookup_role(param)
        if role:
            return Actor(username="api-key", role=role.name.lower(), source="api_key")

    key = extract_key(x_api_key, authorization)
    if key:
        role = lookup_role(key)
        if role:
            return Actor(username="api-key", role=role.name.lower(), source="api_key")

    if not settings.auth_enabled:
        # Open mode: still resolve credentials if provided (so the UI can show
        # the signed-in user), but never block anonymous callers.
        if not x_api_key and not authorization:
            return None

    audit_record(
        "auth_failed",
        reason="missing_or_invalid_token",
        path=request.url.path,
        method=request.method,
    )
    raise _unauthorized("Authentication required (JWT bearer or X-API-Key).")


def require_role(minimum: str):
    """Enforce a minimum role: user < technician < admin."""

    async def dependency(actor: Optional[Actor] = Depends(get_actor)) -> Optional[Actor]:
        if actor is None:
            from app.config import get_settings

            if get_settings().auth_enabled:
                raise _unauthorized("Authentication required.")
            return None  # open mode
        required_rank = ROLE_RANK.get(minimum, 99)
        if actor.rank < required_rank:
            raise _forbidden(f"Role '{actor.role}' lacks permission; requires '{minimum}'.")
        return actor

    return dependency


require_user_role = Depends(require_role("user"))
require_tech_role = Depends(require_role("technician"))
require_admin_role = Depends(require_role("admin"))
require_viewer_role = Depends(require_role("viewer"))
