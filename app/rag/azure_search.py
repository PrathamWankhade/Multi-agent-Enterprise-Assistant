"""Azure AI Search vector backend over the REST API.

Implemented with ``httpx`` so the core app needs **no** Azure SDK packages.
Authentication uses either an admin ``api-key`` or an AAD bearer token from
the optional managed-identity helper. All operations degrade gracefully: if a
call fails the caller logs a warning and the app falls back to keyword search.
"""

import logging
from typing import Any, Dict, List, Optional

import httpx

from app.config import get_settings
from app.rag.store import Chunk

logger = logging.getLogger(__name__)


class AzureSearchClient:
    """Minimal async client for the operations the RAG store needs."""

    def __init__(self, endpoint: str, index: str, api_key: Optional[str] = None) -> None:
        settings = get_settings()
        endpoint = endpoint.rstrip("/")
        if not endpoint.startswith("http"):
            endpoint = "https://" + endpoint
        self.base = endpoint
        self.index = index
        self.api_key = api_key
        self.version = settings.azure_ai_search_api_version
        self._available = False

    # ------------------------------------------------------------------
    # Request helper
    # ------------------------------------------------------------------

    async def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["api-key"] = self.api_key
        else:
            try:
                from app.services.azure_identity import get_azure_token, search_scope

                token = await get_azure_token(search_scope())
                if token:
                    headers["Authorization"] = f"Bearer {token}"
            except Exception:  # pragma: no cover
                pass
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[httpx.Response]:
        url = f"{self.base}{path}"
        query = {"api-version": self.version}
        if params:
            query.update(params)
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.request(
                    method, url, headers=await self._headers(), params=query, json=json_body
                )
            if response.is_error:
                logger.warning(
                    "Azure AI Search %s %s -> %s: %s",
                    method, path, response.status_code, response.text[:300],
                )
                return None
            return response
        except Exception as exc:  # pragma: no cover - network offline
            logger.warning("Azure AI Search request failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Index lifecycle
    # ------------------------------------------------------------------

    async def ensure_index(self, dimension: int) -> bool:
        """Create the vector index if it does not exist; return availability."""
        body = {
            "name": self.index,
            "fields": [
                {"name": "chunk_id", "type": "Edm.String", "key": True, "filterable": True},
                {"name": "text", "type": "Edm.String", "searchable": True},
                {"name": "source", "type": "Edm.String", "filterable": True, "searchable": True},
                {"name": "doc_id", "type": "Edm.String", "filterable": True},
                {"name": "page", "type": "Edm.Int32", "filterable": True},
                {"name": "section", "type": "Edm.String", "searchable": True},
                {"name": "metadata", "type": "Edm.String"},
                {
                    "name": "embedding",
                    "type": "Collection(Edm.Single)",
                    "searchable": True,
                    "dimensions": dimension,
                    "vectorSearchProfile": "vector-profile",
                },
            ],
            "vectorSearch": {
                "algorithms": [
                    {
                        "name": "hnsw-config",
                        "kind": "hnsw",
                        "hnswParameters": {"metric": "cosine"},
                    }
                ],
                "profiles": [
                    {"name": "vector-profile", "algorithm": "hnsw-config"}
                ],
            },
        }
        response = await self._request("PUT", f"/indexes/{self.index}", json_body=body)
        # PUT returns 201 on create, 200 on update — both mean the index is usable.
        self._available = response is not None
        if self._available:
            logger.info("Azure AI Search index '%s' ready (dim=%s)", self.index, dimension)
        return self._available

    # ------------------------------------------------------------------
    # Data operations
    # ------------------------------------------------------------------

    async def upload(self, chunks: List[Chunk]) -> List[str]:
        import json

        documents = []
        for c in chunks:
            documents.append(
                {
                    "@search.action": "mergeOrUpload",
                    "chunk_id": c.chunk_id,
                    "text": c.text,
                    "source": c.source,
                    "doc_id": c.doc_id,
                    "page": c.page,
                    "section": c.section,
                    "metadata": json.dumps(c.metadata or {}),
                    "embedding": c.embedding or [],
                }
            )
        response = await self._request(
            "POST", f"/indexes/{self.index}/docs/index",
            json_body={"value": documents},
        )
        return [c.chunk_id for c in chunks] if response is not None else []

    async def search(
        self, vector: List[float], top_k: int, filter_doc_id: Optional[str] = None,
        min_score: float = 0.0,
    ) -> List[Dict[str, Any]]:
        body: Dict[str, Any] = {
            "vectorQueries": [
                {"kind": "vector", "vector": vector, "fields": "embedding", "k": top_k}
            ],
            "select": "chunk_id,text,source,doc_id,page,section,metadata",
            "top": top_k,
        }
        if filter_doc_id:
            body["filter"] = f"doc_id eq '{filter_doc_id}'"
        response = await self._request(
            "POST", f"/indexes/{self.index}/docs/search", json_body=body
        )
        if response is None:
            return []
        payload = response.json()
        results: List[Dict[str, Any]] = []
        for hit in payload.get("value", []):
            score = float(hit.get("@search.score", 0.0))
            if score < min_score:
                continue
            import json

            try:
                metadata = json.loads(hit.get("metadata") or "{}")
            except (TypeError, ValueError):
                metadata = {}
            results.append(
                {
                    "chunk_id": hit.get("chunk_id", ""),
                    "text": hit.get("text", ""),
                    "source": hit.get("source", ""),
                    "doc_id": hit.get("doc_id", ""),
                    "page": hit.get("page"),
                    "section": hit.get("section"),
                    "metadata": metadata,
                    "score": score,
                }
            )
        return results

    async def count(self) -> int:
        response = await self._request(
            "GET", f"/indexes/{self.index}/docs/$count", params={"$select": "chunk_id"}
        )
        if response is None:
            return 0
        try:
            return int(response.text.strip() or 0)
        except ValueError:  # pragma: no cover
            return 0

    async def list_documents(self) -> List[Dict[str, Any]]:
        """Aggregate indexed chunks by document for the KB modal."""
        response = await self._request(
            "POST",
            f"/indexes/{self.index}/docs/search",
            json_body={
                "search": "*",
                "select": "chunk_id,text,source,doc_id",
                "top": 1000,
            },
        )
        if response is None:
            return []
        docs: Dict[str, Dict[str, Any]] = {}
        for hit in response.json().get("value", []):
            doc_id = hit.get("doc_id") or "unknown"
            entry = docs.setdefault(
                doc_id,
                {
                    "doc_id": doc_id,
                    "source": hit.get("source") or doc_id,
                    "chunks": 0,
                    "characters": 0,
                    "preview": "",
                },
            )
            entry["chunks"] += 1
            text = hit.get("text") or ""
            entry["characters"] += len(text)
            if not entry["preview"]:
                entry["preview"] = text[:180]
        return sorted(docs.values(), key=lambda d: d["source"].lower())

    async def delete_document(self, doc_id: str) -> int:
        """Delete every chunk for a document (lookup keys, then batch delete)."""
        lookup = await self._request(
            "POST",
            f"/indexes/{self.index}/docs/search",
            json_body={
                "filter": f"doc_id eq '{doc_id}'",
                "select": "chunk_id",
                "top": 1000,
            },
        )
        if lookup is None:
            return 0
        ids = [hit.get("chunk_id") for hit in lookup.json().get("value", []) if hit.get("chunk_id")]
        if not ids:
            return 0
        actions = [{"@search.action": "delete", "chunk_id": cid} for cid in ids]
        response = await self._request(
            "POST", f"/indexes/{self.index}/docs/index", json_body={"value": actions}
        )
        return len(ids) if response is not None else 0
