"""Orchestrator Agent: classifies intent and delegates tasks to other agents."""

import json
import logging
from typing import Any, Dict, List, Optional

from app.agents.base import BaseAgent
from app.memory.store import MemoryStore

logger = logging.getLogger(__name__)


class OrchestratorAgent(BaseAgent):
    name = "orchestrator"
    description = "Understands user requests and delegates tasks to specialist agents."

    SYSTEM_PROMPT = """You are the Orchestrator Agent for a multi-agent enterprise assistant.

Your job is to:
1. Understand the user's request
2. Classify the intent into one of: research, analysis, execution, itsm, chitchat
3. Decide which specialist agents to invoke and in what order
4. Provide a delegation plan as JSON

Agent capabilities:
- research: Answers questions, finds facts, looks up information from documents (RAG). Use this for most questions.
- analyst: Deep analysis of retrieved information, comparisons, recommendations, summaries.
- execution: Uses enterprise tools and APIs to perform actions: look up employees (HR directory), list/create IT support tickets, run read-only SQL queries, generate CSV reports. Use when the user asks to CREATE, OPEN, LOG, GENERATE or LOOK UP a specific record — e.g. "create a ticket", "find employee X", "run a query", "generate a report".
- itsm: Full IT service desk workflow — classify an IT problem, search IT documentation for troubleshooting steps, create a service request, route it to admin approval, and track tickets. Use when the user REPORTS A PROBLEM ("my laptop won't start", "VPN keeps disconnecting", "printer is jammed") or asks to TRACK a ticket ("status of my request", "track SR-00001").
- chitchat: Greetings, small talk, thanks, or general conversation with no data need.

Classification rules:
- General questions, facts, math, definitions, "what/why/how/who/when" -> research
- "analyze", "compare", "recommend", "insights", "pros and cons" -> analysis (agents: research then analyst)
- Creating/opening tickets, looking up a specific employee, running SQL, generating reports -> execution (add research first if company documents are also needed)
- Reporting an IT problem, asking for IT help, troubleshooting, or tracking service requests -> itsm
- Greetings and pleasantries -> chitchat
- If unsure, default to research.

Respond ONLY with valid JSON in this format:
{
  "intent": "research|analysis|execution|itsm|chitchat|multi_step",
  "agents": ["research", "analyst", "execution"],
  "reasoning": "brief explanation of why",
  "requires_approval": false,
  "summary_for_user": "one-sentence summary of what will happen"
}

If the intent is chitchat, set agents to [] and provide a friendly response in summary_for_user.
If multiple agents are needed, list them in execution order.
If execution is involved and the action is a write (POST/PUT/DELETE), set requires_approval to true.
Return ONLY the JSON object, no other text.
"""

    # Obvious small talk: skip the LLM round-trip so greetings feel instant.
    GREETINGS = {
        "hi", "hello", "hey", "yo", "hiya", "howdy",
        "good morning", "good afternoon", "good evening",
        "thanks", "thank you", "thx", "bye", "goodbye",
        "how are you", "how are you doing", "whats up", "what's up",
    }

    GREETING_REPLIES = {
        "thanks": "You're welcome! Let me know if you need anything else.",
        "thank you": "You're welcome! Let me know if you need anything else.",
        "thx": "You're welcome! Let me know if you need anything else.",
        "bye": "Goodbye! Feel free to come back anytime.",
        "goodbye": "Goodbye! Feel free to come back anytime.",
        "how are you": "I'm doing well, thanks for asking! How can I help you today?",
        "how are you doing": "I'm doing well, thanks for asking! How can I help you today?",
    }

    @classmethod
    def _fast_chitchat(cls, message: str) -> Optional[Dict[str, Any]]:
        normalized = message.strip().lower().rstrip("!.?")
        normalized = " ".join(normalized.split())
        if normalized not in cls.GREETINGS:
            return None
        reply = cls.GREETING_REPLIES.get(
            normalized, "Hello! How can I help you today?"
        )
        return {
            "intent": "chitchat",
            "agents": [],
            "reasoning": "Simple greeting handled without an LLM call",
            "requires_approval": False,
            "summary_for_user": reply,
        }

    async def run(
        self,
        task_id: str,
        session_id: str,
        input_data: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        user_message = input_data.get("message", "")
        history = self.memory.get_history(session_id, limit=20)

        plan = self._fast_chitchat(user_message)
        if plan is None:
            messages = self._build_messages(
                self.SYSTEM_PROMPT,
                user_message,
                history,
            )

            response = await self._llm_complete(messages, temperature=0.2, max_tokens=500)

            try:
                plan = json.loads(response)
            except json.JSONDecodeError:
                logger.warning("Orchestrator returned non-JSON: %s", response)
                plan = {
                    "intent": "research",
                    "agents": ["research"],
                    "reasoning": "Fallback: treat as research query",
                    "requires_approval": False,
                    "summary_for_user": "I will search the knowledge base for information related to your request.",
                }

        # Store delegation plan in task context
        self.memory.set_task_context(
            task_id,
            {
                "orchestrator_plan": plan,
                "session_id": session_id,
                "user_message": user_message,
            },
        )

        return {
            "agent": self.name,
            "plan": plan,
            "task_id": task_id,
        }