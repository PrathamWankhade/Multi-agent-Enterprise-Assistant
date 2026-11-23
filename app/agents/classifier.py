"""Classification Agent: turns a free-text issue into structured ITSM fields.

The LLM proposes category, priority and routing. Deterministic keyword rules
act as a fallback and as a sanity net when the model is offline or returns
unusable JSON. Administrators can always override the suggestion.
"""

import json
import logging
import re
from typing import Any, Dict, Optional

from app.agents.base import BaseAgent

logger = logging.getLogger(__name__)

CATEGORIES = ["hardware", "software", "network", "access", "other"]
PRIORITIES = ["critical", "high", "medium", "low"]

CATEGORY_RULES = [
    (r"\b(vpn|wifi|wi-fi|network|ethernet|router|dns|internet|connectivity)\b", "network"),
    (r"\b(password|login|access|permission|account|locked out|authentication|sso)\b", "access"),
    (r"\b(laptop|desktop|monitor|printer|keyboard|mouse|hardware|blue screen|boot|docking|device)\b", "hardware"),
    (r"\b(software|install|license|application|excel|outlook|teams|browser|crash)\b", "software"),
]

PRIORITY_RULES = [
    (r"\b(critical|outage|production|all users|whole office|down|emergency|severe)\b", "critical"),
    (r"\b(high|urgent|asap|today|tomorrow|important|presentation|deadline|can't work|cannot work|blocked)\b", "high"),
    (r"\b(low|minor|whenever|no rush|cosmetic)\b", "low"),
]


class ClassificationAgent(BaseAgent):
    name = "classifier"
    description = "Classifies IT issues into category, priority and routing suggestions."

    SYSTEM_PROMPT = """You are the Classification Agent for an enterprise IT Service Desk.

Read the employee's issue report and respond ONLY with valid JSON:
{
  "category": "hardware|software|network|access|other",
  "issue_type": "short specific issue label",
  "suggested_priority": "critical|high|medium|low",
  "department": "IT Support",
  "approval_required": true,
  "confidence": 0.0-1.0,
  "reasoning": "one short sentence"
}

Rules:
- critical: service outage or many users affected.
- high: user cannot work or has an imminent deadline.
- medium: degraded functionality with a workaround.
- low: minor or cosmetic issue.
- Always require administrator approval for ticket creation.
Return ONLY the JSON object.
"""

    @staticmethod
    def heuristic(text: str) -> Dict[str, Any]:
        lowered = text.lower()
        category = "other"
        for pattern, cat in CATEGORY_RULES:
            if re.search(pattern, lowered):
                category = cat
                break
        priority = "medium"
        for pattern, pri in PRIORITY_RULES:
            if re.search(pattern, lowered):
                priority = pri
                break
        issue_type = "IT support request"
        for pattern, label in [
            (r"blue screen|won't boot|not starting|not booting", "Laptop boot failure"),
            (r"vpn", "VPN connectivity issue"),
            (r"password|locked out", "Password / access issue"),
            (r"printer", "Printer malfunction"),
            (r"software|install", "Software support"),
        ]:
            if re.search(pattern, lowered):
                issue_type = label
                break
        return {
            "category": category,
            "issue_type": issue_type,
            "suggested_priority": priority,
            "department": "IT Support",
            "approval_required": True,
            "confidence": 0.62,
            "reasoning": "Keyword-based classification (LLM unavailable).",
            "source": "heuristic",
        }

    async def run(
        self,
        task_id: str,
        session_id: str,
        input_data: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        report = input_data.get("message") or input_data.get("description") or ""

        result = self.heuristic(report)
        messages = self._build_messages(
            self.SYSTEM_PROMPT, f"EMPLOYEE REPORT:\n{report}"
        )
        try:
            response = await self._llm_complete(messages, temperature=0.0, max_tokens=400)
            parsed = self._parse_json(response)
            if parsed and parsed.get("category") in CATEGORIES:
                priority = parsed.get("suggested_priority")
                result.update(
                    {
                        "category": parsed["category"],
                        "issue_type": str(parsed.get("issue_type") or result["issue_type"])[:120],
                        "suggested_priority": priority if priority in PRIORITIES else result["suggested_priority"],
                        "confidence": float(parsed.get("confidence") or 0.8),
                        "reasoning": str(parsed.get("reasoning") or "")[:300],
                        "source": "llm",
                    }
                )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Classification LLM call failed: %s", exc)

        result.update(
            {
                "agent": self.name,
                "task_id": task_id,
                "report": report[:500],
                "approval_required": True,
                "department": "IT Support",
            }
        )
        self.memory.update_task_context(
            task_id, {"classification": {k: v for k, v in result.items() if k != "agent"}}
        )
        return result

    @staticmethod
    def _parse_json(text: str) -> Optional[Dict[str, Any]]:
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        match = re.search(r"\{.*\}", text, flags=re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
        return None
