"""Execution Agent: selects enterprise tools, applies the approval gate, executes.

Flow:
1. The agent receives the user request plus the list of registered tools.
2. It picks a tool and builds arguments (LLM-assisted; rule-based fallback
   when the model is unavailable or returns nothing usable).
3. Read tools run immediately. Write tools pause at a **human approval gate**
   unless ``auto_approve`` is set — the pending action is stored in task
   context so an approval endpoint can execute it later.
4. Direct allow-listed HTTP calls (explicit URL in input) still work as
   before, keeping backwards compatibility.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

import httpx

from app.agents.base import BaseAgent
from app.config import get_settings
from app.tools.registry import ToolSpec, get_registry

logger = logging.getLogger(__name__)


class ExecutionAgent(BaseAgent):
    name = "execution"
    description = "Uses approved enterprise tools and APIs to perform actions."

    SYSTEM_PROMPT = """You are the Execution Agent for a multi-agent enterprise assistant.

You receive a USER REQUEST and a list of AVAILABLE TOOLS with their parameters.

Your job:
1. Decide whether an action is actually needed. If the request is purely informational, reply with "tool": null.
2. Otherwise pick the single best tool and fill in its parameters.
3. Never invent tool names or parameters — only use what is listed.

Respond ONLY with valid JSON:
{
  "tool": "tool_name_or_null",
  "args": {"param": "value"},
  "reasoning": "one short sentence"
}

Return ONLY the JSON object.
"""

    FALLBACK_HINTS = [
        (r"\b(ticket|support request|create.*issue|open.*issue|log.*problem)\b", "create_ticket"),
        (r"\b(lookup|find|search).*(employee|person|staff|who)\b", "lookup_employee"),
        (r"\b(employee|directory|headcount|department)\b", "lookup_employee"),
        (r"\b(report|export|csv)\b", "generate_report"),
        (r"\b(sql|query|select|database)\b", "query_database"),
        (r"\b(tickets?|issues?)\b", "list_tickets"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.settings = get_settings()

    # ------------------------------------------------------------------
    # Legacy direct-HTTP path (allow-listed URLs)
    # ------------------------------------------------------------------

    def _is_domain_allowed(self, url: str) -> bool:
        allowed = self.settings.allowed_execution_domain_list
        if not allowed:
            return False
        try:
            from urllib.parse import urlparse
            host = urlparse(url).hostname or ""
            return any(domain in host for domain in allowed)
        except Exception:
            return False

    def _requires_approval(self, method: str) -> bool:
        if not self.settings.execution_require_approval:
            return False
        return method.upper() in {"POST", "PUT", "PATCH", "DELETE"}

    # ------------------------------------------------------------------
    # Tool selection
    # ------------------------------------------------------------------

    async def _select_tool(
        self, request: str, registry
    ) -> Dict[str, Any]:
        """Ask the LLM to choose a tool; fall back to keyword heuristics."""
        tools_block = registry.prompt_block()
        user_message = (
            f"USER REQUEST:\n{request}\n\n"
            f"AVAILABLE TOOLS:\n{tools_block}\n\n"
            "Respond with the JSON object only."
        )
        messages = self._build_messages(self.SYSTEM_PROMPT, user_message)
        try:
            response = await self._llm_complete(
                messages, temperature=0.0, max_tokens=500
            )
            parsed = self._parse_json(response)
            if parsed and isinstance(parsed.get("tool"), (str, type(None))):
                tool = parsed.get("tool")
                if tool and registry.get(str(tool)):
                    args = parsed.get("args")
                    return {
                        "tool": str(tool),
                        "args": args if isinstance(args, dict) else {},
                        "source": "llm",
                        "reasoning": str(parsed.get("reasoning", "")),
                    }
                if tool in (None, "null", ""):
                    return {"tool": None, "args": {}, "source": "llm",
                            "reasoning": str(parsed.get("reasoning", "no tool needed"))}
        except Exception as exc:
            logger.warning("LLM tool selection failed: %s", exc)

        # Keyword fallback (also used when LLM is in mock mode)
        lowered = request.lower()
        for pattern, tool_name in self.FALLBACK_HINTS:
            if re.search(pattern, lowered) and registry.get(tool_name):
                return {
                    "tool": tool_name,
                    "args": self._guess_args(tool_name, request),
                    "source": "heuristic",
                    "reasoning": f"Matched request to {tool_name} by keyword.",
                }
        return {"tool": None, "args": {}, "source": "none",
                "reasoning": "No tool matched the request."}

    @staticmethod
    def _guess_args(tool_name: str, request: str) -> Dict[str, Any]:
        """Very small argument heuristic for the no-LLM path."""
        if tool_name == "lookup_employee":
            # Use quoted text or the words after "employee"/"find"
            quoted = re.search(r"[\"']([^\"']+)[\"']", request)
            if quoted:
                return {"query": quoted.group(1)}
            m = re.search(r"(?:employee|person|staff)\s+(?:named\s+|called\s+)?(\w[\w\s-]{1,40})",
                          request, flags=re.I)
            return {"query": (m.group(1).strip() if m else request[:60])}
        if tool_name == "create_ticket":
            return {
                "title": request[:80],
                "requester": "user",
                "description": request,
            }
        if tool_name == "generate_report":
            return {"report_name": "enterprise_report", "sql": ""}
        if tool_name == "query_database":
            m = re.search(r"SELECT\b[\s\S]+", request, flags=re.I)
            return {"sql": m.group(0) if m else "SELECT * FROM it_tickets LIMIT 10"}
        if tool_name == "list_tickets":
            return {"status": "all"}
        return {}

    # ------------------------------------------------------------------
    # Main
    # ------------------------------------------------------------------

    async def run(
        self,
        task_id: str,
        session_id: str,
        input_data: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = input_data.get("url", "")
        request = (
            input_data.get("action")
            or input_data.get("query")
            or input_data.get("message")
            or ""
        )
        auto_approve = bool(input_data.get("auto_approve", False))

        result: Dict[str, Any] = {
            "agent": self.name,
            "task_id": task_id,
            "request": request,
        }

        # --- Path A: explicit allow-listed HTTP call (backwards compatible)
        if url and str(url).startswith(("http://", "https://")):
            return await self._run_http(input_data, result, auto_approve)

        # --- Path B: enterprise tool selection
        if not self.settings.tools_enabled:
            result["status"] = "disabled"
            result["message"] = "Tool execution is disabled by configuration."
            return result

        registry = get_registry()
        if not registry.list():
            result["status"] = "no_tools"
            result["message"] = "No tools are registered."
            return result

        selection = await self._select_tool(request, registry)
        tool_name = selection.get("tool")
        result["tool_selection"] = selection

        if not tool_name:
            result["status"] = "no_action"
            result["message"] = (
                selection.get("reasoning")
                or "No tool action was needed for this request."
            )
            return result

        spec: Optional[ToolSpec] = registry.get(tool_name)
        if spec is None:  # pragma: no cover - guarded in _select_tool
            result["status"] = "error"
            result["error"] = f"Unknown tool: {tool_name}"
            return result

        args = selection.get("args") or {}
        clean_args, errors = registry.validate_args(spec, args)
        if errors:
            result["status"] = "error"
            result["error"] = f"Invalid arguments for {tool_name}: {'; '.join(errors)}"
            return result

        result["tool"] = tool_name
        result["risk"] = spec.risk
        result["args"] = clean_args

        # --- Approval gate for write/external tools
        if spec.requires_approval and self.settings.execution_require_approval and not auto_approve:
            result["status"] = "pending_approval"
            result["requires_approval"] = True
            result["message"] = (
                f"'{tool_name}' is a {spec.risk} action and needs human approval "
                "before it can run."
            )
            task_ctx = self.memory.get_task_context(task_id) or {}
            task_ctx["pending_tool_call"] = {
                "tool": tool_name,
                "args": clean_args,
                "risk": spec.risk,
                "requested_at": __import__("datetime").datetime.utcnow().isoformat(
                    timespec="seconds"
                ),
                "status": "pending",
            }
            self.memory.set_task_context(task_id, task_ctx)
            return result

        # --- Execute
        outcome = await registry.invoke(tool_name, clean_args)
        result.update(outcome)
        result["requires_approval"] = False

        task_ctx = self.memory.get_task_context(task_id) or {}
        task_ctx["last_tool_call"] = {
            "tool": tool_name,
            "args": clean_args,
            "status": outcome.get("status"),
        }
        self.memory.set_task_context(task_id, task_ctx)
        return result

    # ------------------------------------------------------------------
    # Direct HTTP execution (legacy path)
    # ------------------------------------------------------------------

    async def _run_http(
        self, input_data: Dict[str, Any], result: Dict[str, Any], auto_approve: bool
    ) -> Dict[str, Any]:
        method = input_data.get("method", "GET").upper()
        url = input_data.get("url", "")
        params = input_data.get("params", {})
        json_body = input_data.get("json_body", {})
        headers = input_data.get("headers", {})
        task_id = result.get("task_id", "")

        result.update({"action": input_data.get("action", ""), "method": method, "url": url})

        if not self._is_domain_allowed(url):
            result["status"] = "blocked"
            result["error"] = f"Domain not in allow-list: {url}"
            result["requires_approval"] = False
            return result

        needs_approval = self._requires_approval(method)
        result["requires_approval"] = needs_approval

        if needs_approval and not auto_approve:
            result["status"] = "pending_approval"
            result["message"] = (
                f"This {method} action requires human approval before execution."
            )
            task_ctx = self.memory.get_task_context(task_id) or {}
            task_ctx["pending_execution"] = {
                "action": input_data.get("action", ""),
                "method": method,
                "url": url,
                "params": params,
                "json_body": json_body,
                "headers": headers,
            }
            self.memory.set_task_context(task_id, task_ctx)
            return result

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                fn = {
                    "GET": client.get, "POST": client.post, "PUT": client.put,
                    "PATCH": client.patch, "DELETE": client.delete,
                }.get(method)
                if fn is None:
                    result["status"] = "error"
                    result["error"] = f"Unsupported HTTP method: {method}"
                    return result
                resp = await fn(url, params=params,
                                **({"json": json_body} if method != "GET" else {}),
                                headers=headers)
                result["status"] = "completed"
                result["status_code"] = resp.status_code
                try:
                    result["response_body"] = resp.json()
                except Exception:
                    result["response_body"] = resp.text
        except Exception as exc:
            logger.exception("Execution agent API call failed")
            result["status"] = "error"
            result["error"] = str(exc)
        return result

    # ------------------------------------------------------------------

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
