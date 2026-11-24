"""JWT authentication (access + refresh tokens) using PyJWT."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import jwt

from app.config import get_settings
from app.database import users as user_db

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"


def create_access_token(user: Dict[str, Any], expires_minutes: int = 60) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user["user_id"],
        "username": user["username"],
        "role": user["role"],
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
        "type": "access",
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def create_refresh_token(user: Dict[str, Any], expires_days: int = 7) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user["user_id"],
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(days=expires_days),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[Dict[str, Any]]:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        logger.debug("JWT expired")
    except jwt.InvalidTokenError:
        logger.debug("Invalid JWT")
    return None


def user_from_token(token: str) -> Optional[Dict[str, Any]]:
    """Validate a JWT and return the live user record (role may have changed)."""
    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        return None
    user = user_db.get_by_id(str(payload.get("sub", "")))
    if not user or not user.get("active"):
        return None
    return user_db._clean(user)
