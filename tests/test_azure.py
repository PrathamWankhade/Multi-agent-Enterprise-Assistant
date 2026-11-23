"""Azure integration tests — all network calls mocked, no Azure account needed."""

import asyncio
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.rag.azure_search import AzureSearchClient
from app.rag.store import Chunk
from app.services import azure_identity


def test_managed_identity_disabled_by_default():
    # conftest leaves AZURE_USE_MANAGED_IDENTITY=false
    assert get_settings().azure_use_managed_identity is False
    assert azure_identity._credential() is None
    assert asyncio.run(azure_identity.get_azure_token()) is None


def test_azure_provider_detection_with_keys(fresh_settings):
    s = fresh_settings(
        azure_openai_endpoint="https://my-resource.openai.azure.com",
        azure_openai_api_key="real-key-123",
        azure_ai_search_endpoint="https://my-search.search.windows.net",
        azure_ai_search_api_key="real-key-456",
        azure_use_managed_identity=False,
    )
    assert s.use_azure_openai is True
    assert s.use_azure_ai_search is True


def test_azure_provider_detection_placeholder_ignored(fresh_settings):
    s = fresh_settings(
        azure_openai_endpoint="https://your-resource.openai.azure.com",
        azure_openai_api_key="your-key",
        azure_ai_search_endpoint="your-service.search.windows.net",
        azure_ai_search_api_key="",
    )
    assert s.use_azure_openai is False
    assert s.use_azure_ai_search is False


def test_azure_provider_with_managed_identity_no_keys(fresh_settings):
    s = fresh_settings(
        azure_openai_endpoint="https://my-resource.openai.azure.com",
        azure_openai_api_key="",
        azure_ai_search_endpoint="https://my-search.search.windows.net",
        azure_ai_search_api_key="",
        azure_use_managed_identity=True,
    )
    # With the flag on, endpoint alone is enough — keys are optional.
    assert s.use_azure_openai is True
    assert s.use_azure_ai_search is True


def test_azure_search_client_builds_correct_index(monkeypatch):
    """The index schema must contain a cosine vector field with the right dims."""
    captured = {}

    async def fake_request(self, method, path, **kwargs):
        captured["method"] = method
        captured["path"] = path
        captured["body"] = kwargs.get("json_body")
        return SimpleNamespace(is_error=False)

    monkeypatch.setattr(AzureSearchClient, "_request", fake_request)
    client = AzureSearchClient("my-search.search.windows.net", "enterprise-kb", "key")
    ok = asyncio.run(client.ensure_index(768))
    assert ok is True
    assert captured["method"] == "PUT"
    fields = {f["name"]: f for f in captured["body"]["fields"]}
    assert fields["embedding"]["dimensions"] == 768
    assert fields["embedding"]["vectorSearchProfile"] == "vector-profile"
    assert captured["body"]["vectorSearch"]["algorithms"][0]["hnswParameters"]["metric"] == "cosine"


def test_azure_search_upload_and_search(monkeypatch):
    uploaded = {}

    async def fake_request(self, method, path, **kwargs):
        if "docs/index" in path:
            uploaded["docs"] = kwargs["json_body"]["value"]
            return SimpleNamespace(is_error=False)
        if "docs/search" in path:
            return SimpleNamespace(
                is_error=False,
                json=lambda: {
                    "value": [
                        {
                            "@search.score": 0.81,
                            "chunk_id": "c1",
                            "text": "Employees get 20 days.",
                            "source": "handbook.md",
                            "doc_id": "d1",
                            "metadata": '{"x": 1}',
                        }
                    ]
                },
            )
        return SimpleNamespace(is_error=False)

    monkeypatch.setattr(AzureSearchClient, "_request", fake_request)
    client = AzureSearchClient("https://search.example.com", "kb", "key")
    chunk = Chunk(text="Employees get 20 days.", source="handbook.md", doc_id="d1", embedding=[0.1] * 768)
    ids = asyncio.run(client.upload([chunk]))
    assert ids == [chunk.chunk_id]
    assert uploaded["docs"][0]["@search.action"] == "mergeOrUpload"
    assert len(uploaded["docs"][0]["embedding"]) == 768

    results = asyncio.run(client.search([0.1] * 768, top_k=3))
    assert len(results) == 1
    assert results[0]["score"] == 0.81
    assert results[0]["metadata"] == {"x": 1}


def test_azure_search_delete(monkeypatch):
    async def fake_request(self, method, path, **kwargs):
        if "docs/search" in path:
            return SimpleNamespace(
                is_error=False,
                json=lambda: {"value": [{"chunk_id": "c1"}, {"chunk_id": "c2"}]},
            )
        return SimpleNamespace(is_error=False)

    monkeypatch.setattr(AzureSearchClient, "_request", fake_request)
    client = AzureSearchClient("https://search.example.com", "kb", "key")
    assert asyncio.run(client.delete_document("d1")) == 2


def test_azure_backend_selection(fresh_settings):
    """When Azure AI Search is configured, the store must select that backend."""
    from app.rag.store import VectorStore

    fresh_settings(
        azure_ai_search_endpoint="https://my-search.search.windows.net",
        azure_ai_search_api_key="key",
        azure_openai_endpoint="",
        azure_openai_api_key="",
    )
    store = VectorStore()
    assert store._backend == "azure_ai_search"
