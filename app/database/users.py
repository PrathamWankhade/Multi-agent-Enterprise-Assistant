"""User accounts and role management for JWT authentication.

Roles: `admin`, `technician`, `user`, `viewer`. Passwords are bcrypt hashes.
SQLite is the free default; PostgreSQL works via DATABASE_URL.
"""

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import bcrypt

from app.database.connection import as_dict, connect, execute_script, q

logger = logging.getLogger(__name__)

_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY = False

ROLE_RANK = {"viewer": 0, "user": 1, "technician": 2, "admin": 3}


def _connect():
    return connect()


def ensure_users_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        conn = _connect()
        try:
            execute_script(
                conn,
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id VARCHAR(64) PRIMARY KEY,
                    username VARCHAR(64) UNIQUE NOT NULL,
                    email VARCHAR(255) NOT NULL DEFAULT '',
                    password_hash VARCHAR(255) NOT NULL,
                    role VARCHAR(32) NOT NULL DEFAULT 'user',
                    department VARCHAR(128) NOT NULL DEFAULT '',
                    full_name VARCHAR(128) NOT NULL DEFAULT '',
                    active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at VARCHAR(32) NOT NULL
                )
                """,
            )
            count = conn.execute(q("SELECT COUNT(*) AS c FROM users")).fetchone()["c"]
            if count == 0:
                now = datetime.now(timezone.utc).isoformat(timespec="seconds")
                defaults = [
                    ("U-ADMIN", "admin", "admin@company.com", "Admin123!", "admin", "IT Administration", "System Administrator"),
                    ("U-USER", "employee", "employee@company.com", "User123!", "user", "Engineering", "Sample Employee"),
                    ("U-TECH", "technician", "tech@company.com", "Tech123!", "technician", "IT Support", "Noah Kim"),
                ]
                insert = q(
                    "INSERT INTO users (user_id, username, email, password_hash, role, "
                    "department, full_name, active, created_at) VALUES (?,?,?,?,?,?,?,?,?)"
                )
                for uid, username, email, password, role, dept, name in defaults:
                    conn.execute(
                        insert,
                        (uid, username, email, hash_password(password), role, dept, name, 1, now),
                    )
                conn.commit()
                logger.info("Seeded default users (admin / employee / technician)")
            _SCHEMA_READY = True
        finally:
            conn.close()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):  # pragma: no cover
        return False


def _clean(row) -> Dict[str, Any]:
    d = as_dict(row)
    d.pop("password_hash", None)
    d["active"] = bool(d.get("active"))
    return d


def get_by_username(username: str) -> Optional[Dict[str, Any]]:
    ensure_users_schema()
    with _connect() as conn:
        row = conn.execute(q("SELECT * FROM users WHERE username = ?"), (username,)).fetchone()
    return dict(row) if row else None


def get_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    ensure_users_schema()
    with _connect() as conn:
        row = conn.execute(q("SELECT * FROM users WHERE user_id = ?"), (user_id,)).fetchone()
    return dict(row) if row else None


def authenticate(username: str, password: str) -> Optional[Dict[str, Any]]:
    user = get_by_username(username)
    if not user or not user.get("active"):
        return None
    if not verify_password(password, user["password_hash"]):
        return None
    return _clean(user)


def list_users() -> List[Dict[str, Any]]:
    ensure_users_schema()
    with _connect() as conn:
        rows = conn.execute(q("SELECT * FROM users ORDER BY role, username")).fetchall()
    return [_clean(r) for r in rows]


def update_role(username: str, role: str) -> Optional[Dict[str, Any]]:
    if role not in ROLE_RANK:
        raise ValueError(f"Unknown role: {role}")
    ensure_users_schema()
    with _connect() as conn:
        conn.execute(q("UPDATE users SET role = ? WHERE username = ?"), (role, username))
        conn.commit()
        row = conn.execute(q("SELECT * FROM users WHERE username = ?"), (username,)).fetchone()
    return _clean(row) if row else None


def create_user(
    username: str, password: str, role: str, email: str = "",
    department: str = "", full_name: str = "",
) -> Dict[str, Any]:
    if role not in ROLE_RANK:
        raise ValueError(f"Unknown role: {role}")
    ensure_users_schema()
    from app.memory.store import MemoryStore

    uid = "U-" + MemoryStore.generate_id()[:8].upper()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _connect() as conn:
        conn.execute(
            q(
                "INSERT INTO users (user_id, username, email, password_hash, role, "
                "department, full_name, active, created_at) VALUES (?,?,?,?,?,?,?,?,?)"
            ),
            (uid, username, email, hash_password(password), role, department, full_name, 1, now),
        )
        conn.commit()
    return get_by_username(username) or {}
