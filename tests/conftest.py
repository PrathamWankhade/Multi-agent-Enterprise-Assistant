"""Test configuration.

Forces mock/offline mode BEFORE any app module is imported so tests never
depend on Ollama, Qdrant Cloud, Redis Cloud, or any network access. Every
external system degrades exactly as documented in the README.
"""

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="eaa-tests-")

# These must be set before `app.config.get_settings()` is first called.
os.environ.update(
    {
        "OPENAI_API_KEY": "",
        "OPENAI_BASE_URL": "",
        "AZURE_OPENAI_ENDPOINT": "",
        "AZURE_OPENAI_API_KEY": "",
        "QDRANT_HOST": "localhost",  # unreachable -> keyword fallback
        "QDRANT_PORT": "6333",
        "QDRANT_API_KEY": "",
        "REDIS_HOST": "localhost",  # unreachable -> in-memory fallback
        "REDIS_PASSWORD": "",
        "REVIEW_ENABLED": "true",
        "REVIEW_LLM_ENABLED": "false",  # no extra LLM call in tests
        "TRACING_ENABLED": "true",
        "TOOLS_ENABLED": "true",
        "SQLITE_PATH": os.path.join(_TMP, "enterprise.db"),
        "REPORTS_DIR": os.path.join(_TMP, "reports"),
        "AUDIT_DIR": os.path.join(_TMP, "audit"),
        "AUTH_ENABLED": "false",
        "API_KEYS": "",
    }
)

from app.config import get_settings

get_settings.cache_clear()  # discard any cached settings from an earlier import

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def client():
    """TestClient with full lifespan (tool registration + KB seeding)."""
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="function")
def fresh_settings():
    """Yield a helper to temporarily change settings for one test."""
    from app.config import get_settings

    original = get_settings().model_dump()

    def set_env(**kwargs):
        for k, v in kwargs.items():
            os.environ[k.upper()] = str(v)
        get_settings.cache_clear()
        return get_settings()

    yield set_env

    # Restore original environment for every key we may have touched.
    for key in original:
        if key.upper() in os.environ:
            os.environ.pop(key.upper(), None)
    for k, v in original.items():
        if v is not None:
            os.environ[k.upper()] = str(v)
        else:
            os.environ.pop(k.upper(), None)
    get_settings.cache_clear()
