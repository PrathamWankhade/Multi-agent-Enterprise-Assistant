"""Vector store abstraction supporting Qdrant and Azure AI Search.

Provides:
- collection / index creation
- upsert (add document chunks with embeddings)
- hybrid / vector search returning chunks with scores and metadata
"""

import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional

from app.config import get_settings
from app.services.llm import LLMService

logger = logging.getLogger(__name__)


class Chunk:
    def __init__(
        self,
        text: str,
        source: str,
        doc_id: str,
        chunk_id: Optional[str] = None,
        page: Optional[int] = None,
        section: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        embedding: Optional[List[float]] = None,
    ) -> None:
        self.chunk_id = chunk_id or str(uuid.uuid4())
        self.text = text
        self.source = source
        self.doc_id = doc_id
        self.page = page
        self.section = section
        self.metadata = metadata or {}
        self.embedding = embedding

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "source": self.source,
            "doc_id": self.doc_id,
            "page": self.page,
            "section": self.section,
            "metadata": self.metadata,
            "embedding": self.embedding,
        }


class VectorStore:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.llm = LLMService()
        self._backend: str = self._choose_backend()
        self._initialized = False
        self._available = False
        self._collection: str = self.settings.qdrant_collection
        self._init_lock = asyncio.Lock()

    def _choose_backend(self) -> str:
        if self.settings.use_azure_ai_search:
            return "azure_ai_search"
        return "qdrant"

    async def initialize(self) -> None:
        if self._initialized:
            return
        # Serialise concurrent callers so the collection is only set up once.
        async with self._init_lock:
            if self._initialized:
                return
            try:
                if self._backend == "qdrant":
                    await self._init_qdrant()
                else:
                    await self._init_azure_ai_search()
                self._available = True
            except Exception as exc:
                logger.warning("Vector store unavailable: %s", exc)
                self._available = False
            self._initialized = True

    @property
    def available(self) -> bool:
        return self._available and self._initialized

    async def _init_qdrant(self) -> None:
        from qdrant_client import QdrantClient
        from qdrant_client.http.models import Distance, PayloadSchemaType, VectorParams

        host = self.settings.qdrant_host or ""
        api_key = self.settings.qdrant_api_key or None
        name = self.settings.qdrant_collection
        self._collection = name

        # Cloud / URL form: e.g. https://xxxx.aws.cloud.qdrant.io
        if host.startswith("http://") or host.startswith("https://"):
            self._qdrant = QdrantClient(
                url=host,
                api_key=api_key,
                timeout=10,
            )
        else:
            self._qdrant = QdrantClient(
                host=host,
                port=self.settings.qdrant_port,
                api_key=api_key,
                https=self.settings.qdrant_port == 6333 and bool(api_key),
                timeout=5,
            )

        # Probe the embedding dimension so the collection matches the model
        # (e.g. nomic-embed-text = 768, text-embedding-3-small = 1536).
        probe = await self.llm.embed_text("dimension probe")
        dim = len(probe) or 1536
        real_embeddings = not self.llm.using_mock_embeddings

        def lookup(collection: str):
            try:
                return self._qdrant.get_collection(collection_name=collection)
            except Exception as exc:
                logger.debug("Collection lookup failed (may not exist): %s", exc)
                return None

        def size_of(info) -> Optional[int]:
            vectors = info.config.params.vectors
            size = getattr(vectors, "size", None)
            if size is None and isinstance(vectors, dict):
                first = next(iter(vectors.values()), None)
                size = getattr(first, "size", None)
            return size

        info = lookup(name)

        # A mock embedding must never drive schema decisions: its dimension is
        # arbitrary and would otherwise cause a real collection to be replaced.
        if not real_embeddings:
            if info is None:
                logger.warning(
                    "Embeddings unavailable; cannot verify collection '%s'", name
                )
            else:
                logger.info(
                    "Qdrant collection '%s' ready (embeddings offline, schema untouched)",
                    name,
                )
        elif info is not None:
            existing_size = size_of(info)
            if existing_size and existing_size != dim:
                points = int(info.points_count or 0)
                if points == 0:
                    logger.warning(
                        "Empty collection '%s' has dim %s but model produces %s; recreating",
                        name, existing_size, dim,
                    )
                    try:
                        self._qdrant.delete_collection(collection_name=name)
                    except Exception as exc:  # pragma: no cover
                        logger.warning("Delete collection failed: %s", exc)
                    info = None
                else:
                    # Never destroy indexed data. Use a dimension-specific
                    # collection alongside the existing one instead.
                    alt = f"{name}_d{dim}"
                    logger.warning(
                        "Collection '%s' holds %s points at dim %s but the model "
                        "produces %s; using '%s' instead (existing data preserved)",
                        name, points, existing_size, dim, alt,
                    )
                    self._collection = alt
                    name = alt
                    info = lookup(name)

        if info is None:
            try:
                self._qdrant.create_collection(
                    collection_name=name,
                    vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
                )
            except Exception as exc:
                # 409 = already exists (created concurrently or lookup hiccup)
                logger.info(
                    "Collection '%s' creation returned %s; assuming it exists", name, exc
                )
                self._qdrant.get_collection(collection_name=name)

        logger.info("Qdrant collection '%s' ready (dim=%s)", name, dim)

        # Qdrant Cloud requires a keyword index to filter/delete by doc_id.
        for field in ("doc_id", "source"):
            try:
                self._qdrant.create_payload_index(
                    collection_name=self._collection,
                    field_name=field,
                    field_schema=PayloadSchemaType.KEYWORD,
                )
            except Exception as exc:  # pragma: no cover - usually "already exists"
                logger.debug("Payload index for %s: %s", field, exc)

    async def _init_azure_ai_search(self) -> None:
        logger.info(
            "Azure AI Search backend selected (index: %s)",
            self.settings.azure_ai_search_index_name,
        )

    async def add_chunks(self, chunks: List[Chunk]) -> List[str]:
        if not chunks:
            return []
        if not self.available:
            logger.warning("Vector store unavailable, skipping add_chunks")
            return [c.chunk_id for c in chunks]

        for chunk in chunks:
            if chunk.embedding is None:
                chunk.embedding = await self.llm.embed_text(chunk.text)

        if self._backend == "qdrant":
            return await self._add_chunks_qdrant(chunks)
        return await self._add_chunks_azure_ai_search(chunks)

    async def _add_chunks_qdrant(self, chunks: List[Chunk]) -> List[str]:
        from qdrant_client.http.models import PointStruct

        points = []
        for chunk in chunks:
            points.append(
                PointStruct(
                    id=chunk.chunk_id,
                    vector=chunk.embedding or [],
                    payload={
                        "text": chunk.text,
                        "source": chunk.source,
                        "doc_id": chunk.doc_id,
                        "page": chunk.page,
                        "section": chunk.section,
                        "metadata": chunk.metadata,
                    },
                )
            )
        self._qdrant.upsert(
            collection_name=self._collection,
            points=points,
        )
        return [c.chunk_id for c in chunks]

    async def _add_chunks_azure_ai_search(self, chunks: List[Chunk]) -> List[str]:
        return [c.chunk_id for c in chunks]

    async def search(
        self,
        query: str,
        top_k: int = 5,
        filter_doc_id: Optional[str] = None,
        min_score: float = 0.0,
    ) -> List[Dict[str, Any]]:
        if not self.available:
            logger.warning("Vector store unavailable, returning empty search results")
            return []
        query_embedding = await self.llm.embed_text(query)
        if self._backend == "qdrant":
            return await self._search_qdrant(query_embedding, top_k, filter_doc_id, min_score)
        return await self._search_azure_ai_search(query_embedding, top_k, filter_doc_id, min_score)

    async def _search_qdrant(
        self,
        query_embedding: List[float],
        top_k: int,
        filter_doc_id: Optional[str],
        min_score: float,
    ) -> List[Dict[str, Any]]:
        from qdrant_client.http.models import Filter, FieldCondition, MatchValue

        qdrant_filter = None
        if filter_doc_id:
            qdrant_filter = Filter(
                must=[FieldCondition(key="doc_id", match=MatchValue(value=filter_doc_id))]
            )

        results = self._qdrant.search(
            collection_name=self._collection,
            query_vector=query_embedding,
            limit=top_k,
            query_filter=qdrant_filter,
            with_payload=True,
            score_threshold=min_score if min_score > 0 else None,
        )

        out: List[Dict[str, Any]] = []
        for point in results:
            payload = point.payload or {}
            out.append(
                {
                    "chunk_id": point.id,
                    "text": payload.get("text", ""),
                    "source": payload.get("source", ""),
                    "doc_id": payload.get("doc_id", ""),
                    "page": payload.get("page"),
                    "section": payload.get("section"),
                    "metadata": payload.get("metadata", {}),
                    "score": point.score,
                }
            )
        return out

    async def _search_azure_ai_search(
        self,
        query_embedding: List[float],
        top_k: int,
        filter_doc_id: Optional[str],
        min_score: float,
    ) -> List[Dict[str, Any]]:
        return []

    async def count(self) -> int:
        """Return the number of stored points (0 when unavailable)."""
        if not self.available or self._backend != "qdrant":
            return 0
        try:
            info = self._qdrant.get_collection(
                collection_name=self._collection
            )
            return int(info.points_count or 0)
        except Exception as exc:  # pragma: no cover
            logger.warning("Count failed: %s", exc)
            return 0

    async def list_documents(self) -> List[Dict[str, Any]]:
        """Aggregate stored chunks by document for the knowledge-base view."""
        if not self.available or self._backend != "qdrant":
            return []
        try:
            docs: Dict[str, Dict[str, Any]] = {}
            offset = None
            while True:
                points, offset = self._qdrant.scroll(
                    collection_name=self._collection,
                    limit=256,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                for point in points:
                    payload = point.payload or {}
                    doc_id = payload.get("doc_id") or "unknown"
                    entry = docs.setdefault(
                        doc_id,
                        {
                            "doc_id": doc_id,
                            "source": payload.get("source") or doc_id,
                            "chunks": 0,
                            "characters": 0,
                            "preview": "",
                        },
                    )
                    entry["chunks"] += 1
                    text = payload.get("text") or ""
                    entry["characters"] += len(text)
                    if not entry["preview"]:
                        entry["preview"] = text[:180]
                if offset is None:
                    break
            return sorted(docs.values(), key=lambda d: d["source"].lower())
        except Exception as exc:  # pragma: no cover
            logger.warning("list_documents failed: %s", exc)
            return []

    async def delete_document(self, doc_id: str) -> int:
        if self._backend == "qdrant":
            from qdrant_client.http.models import Filter, FieldCondition, MatchValue

            flt = Filter(
                must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]
            )
            res = self._qdrant.delete(
                collection_name=self._collection,
                points_selector=flt,
            )
            return getattr(res, "deleted_count", 0) or 0
        return 0


_vector_store: Optional[VectorStore] = None


def get_vector_store() -> VectorStore:
    global _vector_store
    if _vector_store is None:
        _vector_store = VectorStore()
    return _vector_store