"""Analyst Agent: processes information and generates insights."""

import json
import logging
from typing import Any, Dict, List, Optional

from app.agents.base import BaseAgent
from app.memory.store import MemoryStore

logger = logging.getLogger(__name__)


class AnalystAgent(BaseAgent):
    name = "analyst"
    description = "Processes information, generates insights, summaries, and comparisons."

    SYSTEM_PROMPT = """You are the Analyst Agent. Your job is to process information and generate insights.

You will receive:
- Retrieved information from the Research Agent
- The original user query
- Any prior conversation context

Your tasks:
1. Analyze the information for key insights, patterns, and implications
2. Generate a summary, comparison, or recommendation as appropriate
3. Identify gaps, contradictions, or areas needing more information
4. Provide actionable conclusions

Structure your response with:
- Key Findings
- Insights / Implications
- Recommendations
- Confidence Level
- Sources (if applicable)

Be objective, evidence-based, and clear.
"""

    async def run(
        self,
        task_id: str,
        session_id: str,
        input_data: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        query = input_data.get("query", input_data.get("message", ""))
        research_results = input_data.get("research_results", {})

        # Pull research results from task context if not provided directly
        if not research_results:
            task_ctx = self.memory.get_task_context(task_id) or {}
            research_results = task_ctx.get("research_results", {})

        chunks = research_results.get("chunks", [])
        prior_answer = research_results.get("answer", "")

        history = self.memory.get_history(session_id, limit=10)

        # Build context string from chunks
        context_parts = []
        for c in chunks:
            source = c.get("source", "unknown")
            text = c.get("text", "")
            context_parts.append(f"[source: {source}] {text}")
        context_str = "\n\n".join(context_parts) if context_parts else "No context available."

        user_message = (
            f"Original query: {query}\n\n"
            f"Research answer: {prior_answer}\n\n"
            f"Source chunks:\n{context_str}"
        )

        messages = self._build_messages(
            self.SYSTEM_PROMPT,
            user_message,
            history,
        )

        analysis = await self._llm_complete(messages, temperature=0.3, max_tokens=1200)

        task_ctx = self.memory.get_task_context(task_id) or {}
        task_ctx["analysis_results"] = {
            "query": query,
            "analysis": analysis,
            "based_on_chunks": len(chunks),
        }
        self.memory.set_task_context(task_id, task_ctx)

        return {
            "agent": self.name,
            "query": query,
            "analysis": analysis,
            "based_on_chunks": len(chunks),
            "task_id": task_id,
        }