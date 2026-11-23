"""Review Agent: verifies answers against retrieved evidence before delivery.

Two-stage review, designed to stay fast on local hardware:

1. **Deterministic checks** (always, no LLM): empty answers, missing
   citations when evidence existed, sources that do not appear in the
   retrieved chunks, suspiciously short answers.
2. **LLM groundedness pass** (optional, config ``review_llm_enabled``):
   asks the model to compare the draft answer against the evidence and
   flag unsupported claims or numeric mismatches.

Output includes a verdict (``pass`` | ``revise``), the list of issues and,
when revision is requested, a corrected answer the pipeline can use instead
of the draft.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

from app.agents.base import BaseAgent
from app.config import get_settings

logger = logging.getLogger(__name__)


class ReviewAgent(BaseAgent):
    name = "review"
    description = "Checks answers for grounding, citations and policy compliance."

    SYSTEM_PROMPT = """You are the Review Agent for a multi-agent enterprise assistant.

You receive a QUESTION, a DRAFT ANSWER and the EVIDENCE retrieved from the company knowledge base.

Your job:
1. Check that every factual claim in the draft is supported by the evidence.
2. Check numbers/quotes match the evidence exactly.
3. Check the draft does not invent sources or employees or policies.
4. Check the draft actually answers the question.

Respond ONLY with valid JSON:
{
  "verdict": "pass" | "revise",
  "issues": ["short issue descriptions"],
  "revised_answer": "only when verdict is revise — the corrected answer, keeping the same style and citations"
}

Rules:
- Use the evidence as your source of truth. If a claim is not in the evidence, it is unsupported.
- If the evidence does not cover the question at all, verdict is "pass" only if the draft honestly says information is unavailable; otherwise "revise".
- Keep the revised answer concise and preserve [source: ...] citations.
- Return ONLY the JSON object.
"""

    def _deterministic_checks(
        self,
        answer: str,
        chunks: List[Dict[str, Any]],
        question: str,
    ) -> List[str]:
        issues: List[str] = []
        cleaned = (answer or "").strip()

        if not cleaned:
            return ["The answer is empty."]

        if len(cleaned) < 20 and chunks:
            issues.append("The answer is too short to be useful given the retrieved evidence.")

        if chunks:
            # Citations must point at sources we actually retrieved.
            cited = set(re.findall(r"\[source:\s*([^\]]+)\]", cleaned, flags=re.I))
            available = {c.get("source") for c in chunks if c.get("source")}
            for src in cited - available:
                issues.append(f"Citation '{src}' was not among the retrieved sources.")
            if not cited and len(cleaned) > 120:
                issues.append("The answer does not cite any source despite evidence being available.")

            # Every retrieved source should ideally be reflected somewhere.
            # (Only a soft signal — not every chunk is relevant.)
        return issues

    async def run(
        self,
        task_id: str,
        session_id: str,
        input_data: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        settings = get_settings()
        question = input_data.get("question", "")
        draft = input_data.get("answer", "")
        chunks: List[Dict[str, Any]] = input_data.get("chunks") or []

        issues = self._deterministic_checks(draft, chunks, question)
        verdict = "pass" if not issues else "revise"
        revised_answer: Optional[str] = None
        llm_checked = False

        # LLM groundedness pass (skipped for short/empty contexts and when disabled)
        if settings.review_llm_enabled and draft and len(draft) > 40:
            try:
                evidence_parts = []
                for c in chunks[:6]:
                    src = c.get("source", "unknown")
                    text = (c.get("text") or "")[:900]
                    evidence_parts.append(f"[source: {src}] {text}")
                evidence = "\n\n".join(evidence_parts) or "(no evidence retrieved)"

                user_message = (
                    f"QUESTION:\n{question}\n\n"
                    f"DRAFT ANSWER:\n{draft}\n\n"
                    f"EVIDENCE:\n{evidence}"
                )
                messages = self._build_messages(self.SYSTEM_PROMPT, user_message)
                response = await self._llm_complete(
                    messages, temperature=0.0, max_tokens=1400
                )
                llm_checked = True
                parsed = self._parse_json(response)
                if parsed:
                    llm_verdict = str(parsed.get("verdict", "")).lower()
                    llm_issues = [
                        str(i) for i in (parsed.get("issues") or []) if i
                    ]
                    if llm_verdict == "revise":
                        verdict = "revise"
                        issues = issues + llm_issues
                        revised = parsed.get("revised_answer")
                        if isinstance(revised, str) and revised.strip():
                            revised_answer = revised.strip()
                    elif llm_verdict == "pass":
                        # Deterministic issues still stand (e.g. missing citation).
                        if not issues:
                            verdict = "pass"
                else:
                    logger.warning("Review agent returned non-JSON; keeping draft")
            except Exception as exc:  # pragma: no cover
                logger.warning("Review LLM pass failed: %s", exc)

        # Never block delivery on review alone — the caller decides. We report.
        result = {
            "agent": self.name,
            "task_id": task_id,
            "verdict": verdict,
            "issues": issues[:8],
            "llm_checked": llm_checked,
            "revised_answer": revised_answer,
            "checks": {
                "deterministic": len(self._deterministic_checks(draft, chunks, question)),
                "groundedness": llm_checked,
            },
        }

        self.memory.update_task_context(
            task_id, {"review": {k: v for k, v in result.items() if k != "agent"}}
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
        # Extract first {...} block
        match = re.search(r"\{.*\}", text, flags=re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
        return None
