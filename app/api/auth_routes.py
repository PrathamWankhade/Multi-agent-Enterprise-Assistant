"""Authentication endpoints: login, current user, user management."""

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.database import users as user_db
from app.monitoring.audit import audit_record
from app.security.common import _unauthorized
from app.security.deps import Actor, get_actor
from app.security.jwt_auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
)
from fastapi import Depends

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = Field(default="user", description="user | technician | admin")
    email: str = ""
    department: str = ""
    full_name: str = ""


class RoleUpdateRequest(BaseModel):
    role: str


@router.post("/auth/login", tags=["auth"])
async def login(request: LoginRequest):
    """Exchange username/password for JWT access + refresh tokens."""
    user = user_db.authenticate(request.username, request.password)
    if not user:
        audit_record("auth_login_failed", username=request.username)
        raise _unauthorized("Invalid username or password")
    audit_record("auth_login_success", username=user["username"], role=user["role"])
    return {
        "access_token": create_access_token(user),
        "refresh_token": create_refresh_token(user),
        "token_type": "bearer",
        "user": user,
    }


@router.post("/auth/refresh", tags=["auth"])
async def refresh(request: RefreshRequest):
    payload = decode_token(request.refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise _unauthorized("Invalid refresh token")
    from app.database import users as udb

    user = udb.get_by_id(str(payload.get("sub", "")))
    if not user or not user.get("active"):
        raise _unauthorized("User no longer active")
    clean = udb._clean(user)
    return {"access_token": create_access_token(clean), "token_type": "bearer"}


@router.get("/auth/me", tags=["auth"])
async def me(actor: Actor = Depends(get_actor)):
    """Return the authenticated user (JWT or API key)."""
    from app.config import get_settings

    return {
        "auth_enabled": get_settings().auth_enabled,
        "user": actor.to_dict() if actor else None,
    }


def _is_admin(actor) -> bool:
    from app.config import get_settings

    if actor is not None:
        return actor.role == "admin"
    return not get_settings().auth_enabled  # open mode permits all


@router.get("/auth/users", tags=["auth"])
async def list_users(actor: Actor = Depends(get_actor)):
    """Admin-only user directory."""
    if not _is_admin(actor):
        from app.security.common import _forbidden

        raise _forbidden("Admin role required")
    return {"count": len(user_db.list_users()), "users": user_db.list_users()}


@router.post("/auth/users", tags=["auth"])
async def create_user(request: CreateUserRequest, actor: Actor = Depends(get_actor)):
    """Admin creates a user with an assigned role and department."""
    if not _is_admin(actor):
        from app.security.common import _forbidden

        raise _forbidden("Admin role required")
    try:
        user = user_db.create_user(
            username=request.username,
            password=request.password,
            role=request.role,
            email=request.email,
            department=request.department,
            full_name=request.full_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"Username may already exist: {exc}")
    audit_record("auth_user_created", username=request.username, role=request.role,
                 actor=actor.username)
    return user


@router.patch("/auth/users/{username}/role", tags=["auth"])
async def change_role(username: str, request: RoleUpdateRequest, actor: Actor = Depends(get_actor)):
    if not _is_admin(actor):
        from app.security.common import _forbidden

        raise _forbidden("Admin role required")
    try:
        user = user_db.update_role(username, request.role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    audit_record("auth_role_changed", username=username, role=request.role,
                 actor=actor.username)
    return user
