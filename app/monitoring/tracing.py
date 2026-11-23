"""Agent execution tracing.

Records per-stage timings and outcomes for every task so the UI and the
``/api/v1/tasks/{id}/trace`` endpoint can show exactly what ran, for how
long, and with what result — the "agent execution trace" recruiters look for.

Traces live in the existing task context (Redis / shared fallback), keyed by
task id, and are also returned inline in the ``final`` SSE payload.
"""

import time
from typing import Any, Dict, List, Optional

from app.memory.store import MemoryStore

_TRACE_KEY = "trace"


class TaskTrace:
    """Collects step timings for one task run."""

    def __init__(self, task_id: str, session_id: str, enabled: bool = True) -> None:
        self.task_id = task_id
        self.session_id = session_id
        self.enabled = enabled
        self.steps: List[Dict[str, Any]] = []
        self._open: Dict[str, float] = {}

    def start(self, agent: str, label: str = "") -> None:
        if not self.enabled:
            return
        self._open[agent] = time.perf_counter()
        # Overwrite any previous entry for the same agent (retries included).
        self._pending(agent, label)

    def _pending(self, agent: str, label: str) -> None:
        for step in self.steps:
            if step["agent"] == agent and step.get("duration_ms") is None:
                step["label"] = label or step.get("label", agent)
                return
        self.steps.append(
            {"agent": agent, "label": label or agent, "duration_ms": None, "status": "running"}
        )

    def end(self, agent: str, status: str = "ok", meta: Optional[Dict[str, Any]] = None) -> None:
        if not self.enabled:
            return
        t0 = self._open.pop(agent, None)
        duration = int((time.perf_counter() - t0) * 1000) if t0 is not None else None
        step = self._find(agent)
        if step is None:
            step = {"agent": agent, "label": agent, "duration_ms": None}
            self.steps.append(step)
        step["duration_ms"] = duration
        step["status"] = status
        if meta:
            step["meta"] = meta

    def _find(self, agent: str) -> Optional[Dict[str, Any]]:
        for step in self.steps:
            if step["agent"] == agent and step.get("duration_ms") is None:
                return step
        return None

    def to_dict(self) -> Dict[str, Any]:
        total = sum(
            s.get("duration_ms") or 0 for s in self.steps
        )
        return {
            "task_id": self.task_id,
            "session_id": self.session_id,
            "steps": self.steps,
            "total_ms": total,
        }

    async def persist(self) -> None:
        """Store the trace in task context (best effort)."""
        try:
            MemoryStore().update_task_context(self.task_id, {_TRACE_KEY: self.to_dict()})
        except Exception:  # pragma: no cover - defensive
            pass


async def get_trace(task_id: str) -> Optional[Dict[str, Any]]:
    """Load a previously persisted trace."""
    ctx = MemoryStore().get_task_context(task_id) or {}
    trace = ctx.get(_TRACE_KEY)
    return trace if isinstance(trace, dict) else None