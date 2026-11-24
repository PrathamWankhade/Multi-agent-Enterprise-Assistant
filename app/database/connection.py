"""Database connection abstraction (SQLite default, optional PostgreSQL).

Set ``DATABASE_URL=postgresql://user:pass@host:5432/db`` to switch. The
``psycopg[binary]`` package is optional (requirements-postgres.txt); without
it the app keeps using SQLite and logs a warning.

All SQL in this project uses ``?`` placeholders. The returned connection
proxy rewrites them to ``%s`` for PostgreSQL, so repository code is agnostic.
"""

import logging
import os
import sqlite3
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)


def backend() -> str:
    url = (get_settings().database_url or "").strip().lower()
    return "postgresql" if url.startswith(("postgres://", "postgresql://")) else "sqlite"


def q(sql: str) -> str:
    """Translate SQL placeholders for the active backend."""
    return sql.replace("?", "%s") if backend() == "postgresql" else sql


class DBConnection:
    """Proxy that translates ``?`` placeholders and normalises rows."""

    def __init__(self, raw, is_postgres: bool):
        self._raw = raw
        self._is_postgres = is_postgres

    def execute(self, sql, params=()):
        return self._raw.execute(q(sql), params)

    def executemany(self, sql, params_seq):
        return self._raw.executemany(q(sql), params_seq)

    def executescript(self, script):
        if self._is_postgres:
            for stmt in [s.strip() for s in script.split(";") if s.strip()]:
                self._raw.execute(q(stmt))
            self._raw.commit()
        else:
            self._raw.executescript(script)

    def commit(self):
        return self._raw.commit()

    def rollback(self):
        return self._raw.rollback()

    def close(self):
        return self._raw.close()

    def __enter__(self):
        self._raw.__enter__()
        return self

    def __exit__(self, *args):
        return self._raw.__exit__(*args)


def connect() -> DBConnection:
    settings = get_settings()
    if backend() == "postgresql":
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError:
            logger.warning("psycopg not installed; falling back to SQLite")
        else:
            return DBConnection(
                psycopg.connect(settings.database_url, row_factory=dict_row), True
            )

    path = settings.sqlite_path
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    raw = sqlite3.connect(path, timeout=10)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    return DBConnection(raw, False)


def execute_script(conn, script: str) -> None:
    """Execute a multi-statement schema script (used by users.py)."""
    conn.executescript(script)


def as_dict(row: Any) -> dict:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    return dict(row)
