"""Memory store with Redis backend and a shared in-memory fallback.

Stores:
- conversation history per session
- agent scratchpad / context per task
- workflow state

The in-memory fallback is module-level (shared across all MemoryStore
instances) so state persists across agents and requests within a process.
Redis is probed at most once per process; if unavailable, the fallback is
used silently without repeated connection attempts.
"""

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

import redis

from app.config import get_settings

logger = logging.getLogger(__name__)

# Shared across all instances so fallback state persists across agents.
_FALLBACK: Dict[str, Any] = {}

# Redis singleton state (probed once per process).
_REDIS_CLIENT: Optional[redis.Redis] = None
_REDIS_PROBED: bool = False


def _get_redis_client() -> Optional[redis.Redis]:
    global _REDIS_CLIENT, _REDIS_PROBED
    if _REDIS_PROBED:
        return _REDIS_CLIENT
    _REDIS_PROBED = True
    settings = get_settings()
    try:
        client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password or None,
            decode_responses=True,
            socket_timeout=1,
            socket_connect_timeout=1,
        )
        client.ping()
        logger.info("Connected to Redis memory store")
        _REDIS_CLIENT = client
    except Exception as exc:
        logger.warning("Redis unavailable, using in-memory fallback: %s", exc)
        _REDIS_CLIENT = None
    return _REDIS_CLIENT


class MemoryStore:
    """Memory store using Redis when available, otherwise a shared dict."""

    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def client(self) -> Optional[redis.Redis]:
        return _get_redis_client()

    # -- session conversation history -------------------------------------

    def _session_key(self, session_id: str) -> str:
        return f"session:{session_id}:history"

    def append_message(self, session_id: str, role: str, content: str) -> None:
        payload = json.dumps({"role": role, "content": content})
        key = self._session_key(session_id)
        client = self.client
        if client:
            try:
                client.rpush(key, payload)
                client.expire(key, self.settings.memory_ttl_seconds)
                return
            except Exception as exc:  # pragma: no cover
                logger.warning("Redis append failed: %s", exc)
        _FALLBACK.setdefault(key, []).append(payload)

    def get_history(self, session_id: str, limit: int = 50) -> List[Dict[str, str]]:
        key = self._session_key(session_id)
        client = self.client
        if client:
            try:
                raw = client.lrange(key, -limit, -1)
                return [json.loads(item) for item in raw]
            except Exception as exc:  # pragma: no cover
                logger.warning("Redis get history failed: %s", exc)
        return self._parse_fallback(key)

    def _parse_fallback(self, key: str) -> List[Dict[str, str]]:
        items = _FALLBACK.get(key, [])
        return [json.loads(item) for item in items]

    def clear_session(self, session_id: str) -> None:
        key = self._session_key(session_id)
        client = self.client
        if client:
            try:
                client.delete(key)
            except Exception as exc:  # pragma: no cover
                logger.warning("Redis clear failed: %s", exc)
        _FALLBACK.pop(key, None)

    # -- task / workflow context ------------------------------------------

    def _task_key(self, task_id: str) -> str:
        return f"task:{task_id}:context"

    def set_task_context(
        self, task_id: str, context: Dict[str, Any], ttl: Optional[int] = None
    ) -> None:
        payload = json.dumps(context)
        key = self._task_key(task_id)
        ttl = ttl or self.settings.memory_ttl_seconds
        client = self.client
        if client:
            try:
                client.set(key, payload, ex=ttl)
                return
            except Exception as exc:  # pragma: no cover
                logger.warning("Redis set task context failed: %s", exc)
        _FALLBACK[key] = payload

    def get_task_context(self, task_id: str) -> Optional[Dict[str, Any]]:
        key = self._task_key(task_id)
        client = self.client
        if client:
            try:
                raw = client.get(key)
                if raw is not None:
                    return json.loads(raw)
            except Exception as exc:  # pragma: no cover
                logger.warning("Redis get task context failed: %s", exc)
        return self._parse_fallback_obj(key)

    def _parse_fallback_obj(self, key: str) -> Optional[Dict[str, Any]]:
        raw = _FALLBACK.get(key)
        if raw is None:
            return None
        return json.loads(raw)

    def update_task_context(self, task_id: str, updates: Dict[str, Any]) -> None:
        current = self.get_task_context(task_id) or {}
        current.update(updates)
        self.set_task_context(task_id, current)

    # -- utilities --------------------------------------------------------

    @staticmethod
    def generate_id() -> str:
        return str(uuid.uuid4())