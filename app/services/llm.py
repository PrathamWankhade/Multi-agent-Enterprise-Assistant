"""LLM service supporting Azure OpenAI and OpenAI with a mock fallback."""

import json
import logging
from typing import Any, Dict, List, Optional

from openai import AsyncAzureOpenAI, AsyncOpenAI

from app.config import get_settings

logger = logging.getLogger(__name__)


class LLMService:
    """Unified LLM client. Falls back to a deterministic mock when no credentials are configured."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self._client: Optional[Any] = None
        self._embedding_client: Optional[Any] = None
        # Set to False once a real (non-mock) embedding succeeds.
        self.using_mock_embeddings: bool = True

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = self._build_client()
        return self._client

    @property
    def embedding_client(self) -> Any:
        if self._embedding_client is None:
            self._embedding_client = self._build_embedding_client()
        return self._embedding_client

    def _build_client(self) -> Any:
        settings = self.settings
        if settings.use_azure_openai:
            return AsyncAzureOpenAI(
                azure_endpoint=settings.azure_openai_endpoint,
                api_key=settings.azure_openai_api_key,
                api_version=settings.azure_openai_api_version,
            )
        if settings.use_openai:
            return AsyncOpenAI(
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url or None,
            )
        return None  # mock mode

    def _build_embedding_client(self) -> Any:
        settings = self.settings
        if settings.use_azure_openai:
            return AsyncAzureOpenAI(
                azure_endpoint=settings.azure_openai_endpoint,
                api_key=settings.azure_openai_api_key,
                api_version=settings.azure_openai_api_version,
            )
        if settings.use_openai:
            return AsyncOpenAI(
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url or None,
            )
        return None

    def _deployment(self) -> Optional[str]:
        # Only use the Azure deployment name when Azure OpenAI is actually active.
        if self.settings.use_azure_openai:
            return self.settings.azure_openai_deployment_name or self.settings.openai_model
        return self.settings.openai_model

    def _embedding_deployment(self) -> Optional[str]:
        if self.settings.use_azure_openai:
            return (
                self.settings.azure_openai_embedding_deployment
                or self.settings.openai_embedding_model
            )
        return self.settings.openai_embedding_model

    def _is_ollama(self) -> bool:
        base = (self.settings.openai_base_url or "").lower()
        return "11434" in base or "ollama" in base

    def _ollama_base(self) -> str:
        base = (self.settings.openai_base_url or "http://localhost:11434").rstrip("/")
        # strip the /v1 suffix to reach native API root
        if base.endswith("/v1"):
            base = base[:-3]
        return base.rstrip("/")

    async def _ollama_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> Optional[str]:
        """Call Ollama's native /api/chat with thinking disabled."""
        import httpx

        url = self._ollama_base() + "/api/chat"
        payload = {
            "model": self._deployment(),
            "messages": messages,
            "stream": False,
            "think": False,
            # Keep the model resident so consecutive agent calls don't pay
            # the (slow) model reload cost.
            "keep_alive": "30m",
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        message = data.get("message", {}) or {}
        return message.get("content") or ""

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 1000,
        **kwargs: Any,
    ) -> str:
        """Return a text completion from the LLM, or a deterministic mock response."""
        client = self.client
        if client is None:
            return self._mock_response(messages)

        deployment = self._deployment()

        # Ollama: use native API so reasoning models can disable "thinking".
        if self._is_ollama():
            try:
                content = await self._ollama_chat(messages, temperature, max_tokens)
                if content and content.strip():
                    return content
                logger.warning("Ollama returned empty content; using mock response")
                return self._mock_response(messages)
            except Exception as exc:  # pragma: no cover
                logger.warning("Ollama call failed (%s); using mock response", exc)
                return self._mock_response(messages)

        try:
            response = await client.chat.completions.create(
                model=deployment,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )
            content = response.choices[0].message.content or ""
            if not content.strip():
                logger.warning("LLM returned empty content; using mock response")
                return self._mock_response(messages)
            return content
        except Exception as exc:  # pragma: no cover - fallback path
            # Azure/OpenAI unreachable or credentials invalid: degrade to mock gracefully
            logger.warning("LLM call failed (%s); using mock response", exc)
            return self._mock_response(messages)

    async def _ollama_embed(self, text: str) -> List[float]:
        """Call Ollama's native /api/embed endpoint."""
        import httpx

        url = self._ollama_base() + "/api/embed"
        payload = {"model": self._embedding_deployment(), "input": text, "keep_alive": "30m"}
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        embeddings = data.get("embeddings") or []
        if embeddings:
            return embeddings[0]
        # older endpoint shape
        return data.get("embedding") or []

    async def embed_text(self, text: str) -> List[float]:
        """Return an embedding vector, or a deterministic mock vector."""
        if self._is_ollama():
            try:
                vec = await self._ollama_embed(text)
                if vec:
                    self.using_mock_embeddings = False
                    return vec
                logger.warning("Ollama embedding empty; using mock embedding")
                self.using_mock_embeddings = True
                return self._mock_embedding(text)
            except Exception as exc:  # pragma: no cover
                logger.warning("Ollama embedding failed (%s); using mock embedding", exc)
                self.using_mock_embeddings = True
                return self._mock_embedding(text)

        client = self.embedding_client
        if client is None:
            self.using_mock_embeddings = True
            return self._mock_embedding(text)

        deployment = self._embedding_deployment()
        try:
            response = await client.embeddings.create(
                model=deployment,
                input=text,
            )
            self.using_mock_embeddings = False
            return response.data[0].embedding
        except Exception as exc:  # pragma: no cover - fallback path
            self.using_mock_embeddings = True
            return self._mock_embedding(text)

    # ------------------------------------------------------------------
    # Fallbacks for offline / demo mode
    # ------------------------------------------------------------------

    @staticmethod
    def _mock_response(messages: List[Dict[str, str]]) -> str:
        # Detect whether JSON is expected (e.g., orchestrator delegation)
        system_prompt = ""
        user_message = ""
        for m in messages:
            if m.get("role") == "system":
                system_prompt = m.get("content", "")
            if m.get("role") == "user":
                user_message = m.get("content", "")

        if "Respond ONLY with valid JSON" in system_prompt:
            lower = user_message.lower()
            if any(w in lower for w in ("hello", "hi ", "hi!", "how are", "good morning", "good afternoon")):
                return json.dumps(
                    {
                        "intent": "chitchat",
                        "agents": [],
                        "reasoning": "Greeting or general conversation, no specialist needed",
                        "requires_approval": False,
                        "summary_for_user": "Hello! How can I help you today?",
                    }
                )
            if any(w in lower for w in ("api", "call", "execute", "action", "send", "post", "webhook")):
                return json.dumps(
                    {
                        "intent": "execution",
                        "agents": ["execution"],
                        "reasoning": "User requests an API action or execution",
                        "requires_approval": True,
                        "summary_for_user": "I will prepare the API action for your review and approval.",
                    }
                )
            if any(w in lower for w in ("analyze", "insight", "summary", "recommend", "compare")):
                return json.dumps(
                    {
                        "intent": "analysis",
                        "agents": ["research", "analyst"],
                        "reasoning": "User wants analysis of information",
                        "requires_approval": False,
                        "summary_for_user": "I will research and analyze to give you insights.",
                    }
                )
            # Default: research
            return json.dumps(
                {
                    "intent": "research",
                    "agents": ["research"],
                    "reasoning": "User is asking for information from documents",
                    "requires_approval": False,
                    "summary_for_user": "I will search the knowledge base for relevant information.",
                }
            )

        user_message = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_message = m.get("content", "")
                break
        lower = user_message.lower()
        if any(w in lower for w in ("research", "document", "file", "find")):
            return (
                "[MOCK] I would search the knowledge base for documents related to your request. "
                "Configure Azure OpenAI or OpenAI credentials to enable real LLM responses."
            )
        if any(w in lower for w in ("analyze", "insight", "summary")):
            return (
                "[MOCK] I would analyze the retrieved information and generate insights. "
                "Configure LLM credentials to enable real responses."
            )
        if any(w in lower for w in ("api", "call", "execute", "action")):
            return (
                "[MOCK] I would execute the permitted API action. "
                "Configure LLM credentials to enable real responses."
            )
        return (
            "[MOCK] I received your request but no LLM provider is configured. "
            "Set AZURE_OPENAI_* or OPENAI_API_KEY environment variables to enable full functionality."
        )

    @staticmethod
    def _mock_embedding(text: str) -> List[float]:
        """Deterministic pseudo-embedding based on character hash (for demo only)."""
        import hashlib

        digest = hashlib.sha256(text.encode("utf-8")).digest()
        # Produce a 1536-dim vector normalized to unit length
        values = [b / 255.0 for b in digest]
        # Repeat to reach 1536 dims
        while len(values) < 1536:
            values.extend(values[: min(len(values), 1536 - len(values))])
        values = values[:1536]
        norm = sum(v * v for v in values) ** 0.5 or 1.0
        return [v / norm for v in values]