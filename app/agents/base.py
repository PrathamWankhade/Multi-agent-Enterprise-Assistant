"""Base agent class with common interface and LLM/memory access."""

import logging
from typing import Any, Dict, List, Optional

from app.memory.store import MemoryStore
from app.services.llm import LLMService

logger = logging.getLogger(__name__)


class BaseAgent:
    name: str = "base"
    description: str = "Base agent"

    def __init__(self) -> None:
        self.llm = LLMService()
        self.memory = MemoryStore()

    async def run(
        self,
        task_id: str,
        session_id: str,
        input_data: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def _build_messages(
        self,
        system_prompt: str,
        user_message: str,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> List[Dict[str, str]]:
        messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_message})
        return messages

    async def _llm_complete(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 1000,
    ) -> str:
        return await self.llm.chat_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )