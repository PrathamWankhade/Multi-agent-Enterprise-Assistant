"""Optional Azure managed-identity / DefaultAzureCredential support.

This module NEVER imports `azure-identity` at module load time — the package is
optional (see `requirements-azure.txt`). When it is missing, the app silently
falls back to API keys or mock/offline mode instead of crashing.

Two helpers:

- ``get_azure_token(scope)`` — an access token string for REST calls (e.g.
  Azure AI Search at ``https://search.azure.com/.default``).
- ``get_azure_token_provider(scope)`` — an async callable returning a token,
  suitable for the OpenAI SDK's ``azure_ad_token_provider`` argument.
"""

import asyncio
import logging
from functools import lru_cache
from typing import Awaitable, Callable, Optional

from app.config import get_settings

logger = logging.getLogger(__name__)

TokenProvider = Callable[[], Awaitable[str]]

_SEARCH_SCOPE = "https://search.azure.com/.default"
_COGNITIVE_SCOPE = "https://cognitiveservices.azure.com/.default"


@lru_cache(maxsize=1)
def _credential():
    """Return a cached DefaultAzureCredential, or None if unavailable."""
    settings = get_settings()
    if not settings.azure_use_managed_identity:
        return None
    try:
        from azure.identity.aio import DefaultAzureCredential
    except ImportError:
        logger.warning(
            "azure-identity not installed; managed identity disabled. "
            "Install requirements-azure.txt to enable it."
        )
        return None
    try:
        credential = DefaultAzureCredential()
        logger.info("Azure DefaultAzureCredential initialised")
        return credential
    except Exception as exc:  # pragma: no cover - environment-specific
        logger.warning("Could not create Azure credential: %s", exc)
        return None


async def get_azure_token(scope: str = _SEARCH_SCOPE) -> Optional[str]:
    """Fetch an AAD access token for ``scope``, or None when unavailable."""
    credential = _credential()
    if credential is None:
        return None
    try:
        token = await credential.get_token(scope)
        return token.token
    except Exception as exc:  # pragma: no cover - no credentials in dev
        logger.warning("Azure token acquisition failed for %s: %s", scope, exc)
        return None


def get_azure_token_provider(scope: str = _COGNITIVE_SCOPE) -> Optional[TokenProvider]:
    """Return an async token-provider callable for the OpenAI SDK."""

    async def provider() -> str:
        token = await get_azure_token(scope)
        if not token:
            raise RuntimeError("Unable to acquire Azure AD token")
        return token

    if _credential() is None:
        return None
    return provider


def search_scope() -> str:
    return _SEARCH_SCOPE


def cognitive_scope() -> str:
    return _COGNITIVE_SCOPE
