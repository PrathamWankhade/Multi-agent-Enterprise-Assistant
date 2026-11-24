"""Shared auth helpers (no FastAPI dependencies) — avoids circular imports."""

import logging
from enum import IntEnum
from typing import Dict, Optional

from fastapi import HTTPException
from fastapi.security.utils import get_authorization_scheme_param

logger = logging.getLogger(__name__)


class Role(IntEnum):
    VIEWER = 1
    USER = 2
    TECHNICIAN = 3
    ADMIN = 4


def _parse_keys(raw: str) -> Dict[str, Role]:
    mapping: Dict[str, Role] = {}
    for part in (raw or "").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        key, role = part.split(":", 1)
        key, role = key.strip(), role.strip().lower()
        if not key:
            continue
        try:
            mapping[key] = Role[role.upper()]
        except KeyError:
            logger.warning("Unknown role in API_KEYS: %s (ignored)", role)
    return mapping


def lookup_role(key: str) -> Optional[Role]:
    from app.config import get_settings

    return _parse_keys(get_settings().api_keys).get(key)


def extract_key(x_api_key: Optional[str], authorization: Optional[str]) -> Optional[str]:
    if x_api_key and x_api_key.strip():
        return x_api_key.strip()
    scheme, param = get_authorization_scheme_param(authorization)
    if scheme.lower() == "bearer" and param:
        return param.strip()
    return None


def _unauthorized(detail: str):
    return HTTPException(
        status_code=401,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _forbidden(detail: str):
    return HTTPException(status_code=403, detail=detail)
