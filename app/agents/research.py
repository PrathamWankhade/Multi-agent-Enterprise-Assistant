"""Research Agent: retrieves information from documents using RAG."""

import logging
from typing import Any, Dict, List, Optional

from app.agents.base import BaseAgent
from app.memory.store import MemoryStore
from app.rag.keyword import keyword_search
from app.rag.store import get_vector_store

logger = logging.getLogger(__name__)


class ResearchAgent(BaseAgent):
    name = "research"
    description = "Retrieves information from documents using RAG (vector search)."

    SYSTEM_PROMPT = """You are the Research Agent for an enterprise assistant.

You will receive:
- A user query
- Retrieved document chunks with similarity scores and source metadata

Your tasks:
1. If the retrieved chunks are relevant, synthesize a clear, well-structured answer and cite sources using [source: filename].
2. If the chunks are relevant, base factual claims only on them and do not invent details.
3. If the chunks are NOT relevant or empty, answer the question from your own general knowledge, and add a short note that it is not from the internal knowledge base.
4. Be concise and helpful. Prefer short paragraphs or bullet points.
"""

    async def run(
        self,
        task_id: str,
        session_id: str,
        input_data: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        query = input_data.get("query", input_data.get("message", ""))
        top_k = input_data.get("top_k", 5)
        min_score = input_data.get("min_score", 0.0)

        # Retrieve from vector store
        store = get_vector_store()
        await store.initialize()

        results = await store.search(
            query=query,
            top_k=top_k,
            min_score=min_score,
        )

        # Fallback: lexical retrieval when embeddings are mock/unavailable or
        # the vector store returned nothing (e.g. no documents ingested yet).
        if not results:
            try:
                results = keyword_search(query, top_k=top_k)
                if results:
                    logger.info(
                        "Vector search empty; using keyword fallback (%s hits)",
                        len(results),
                    )
            except Exception as exc:  # pragma: no cover
                logger.warning("Keyword fallback failed: %s", exc)

        # Build context for LLM synthesis
        context_parts = []
        for r in results:
            source = r.get("source", "unknown")
            score = r.get("score", 0.0)
            text = r.get("text", "")
            context_parts.append(f"[source: {source}] (score: {score:.3f})\n{text}")

        retrieved_context = "\n\n---\n\n".join(context_parts) if context_parts else "No relevant documents found."

        history = self.memory.get_history(session_id, limit=10)

        messages = self._build_messages(
            self.SYSTEM_PROMPT,
            f"User query: {query}\n\nRetrieved context:\n{retrieved_context}",
            history,
        )

        answer = await self._llm_complete(messages, temperature=0.3, max_tokens=1000)

        # Store results in task context
        task_ctx = self.memory.get_task_context(task_id) or {}
        task_ctx["research_results"] = {
            "query": query,
            "chunks": results,
            "answer": answer,
            "chunk_count": len(results),
        }
        self.memory.set_task_context(task_id, task_ctx)

        return {
            "agent": self.name,
            "query": query,
            "answer": answer,
            "chunks": results,
            "chunk_count": len(results),
            "task_id": task_id,
        }