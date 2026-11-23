"""FastAPI route handlers for the multi-agent assistant."""

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.agents.analyst import AnalystAgent
from app.agents.execution import ExecutionAgent
from app.agents.orchestrator import OrchestratorAgent
from app.agents.research import ResearchAgent
from app.agents.reviewer import ReviewAgent
from app.config import get_settings
from app.memory.store import MemoryStore
from app.monitoring.audit import audit_record
from app.monitoring.tracing import TaskTrace, get_trace
from app.security.auth import Role, require_admin, require_role, require_user, require_viewer
from app.api.schemas import (
    AgentResult,
    ChatRequest,
    ChatResponse,
    ChunkResult,
    HealthResponse,
    IngestRequest,
    IngestResponse,
    SearchRequest,
    SearchResponse,
)
from app.rag.ingest import ingest_directory, ingest_file, ingest_text
from app.rag.store import get_vector_store

logger = logging.getLogger(__name__)

router = APIRouter()
settings = get_settings()

EmitFn = Callable[[str, Dict[str, Any]], Awaitable[None]]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _run_agent_pipeline(
    task_id: str,
    session_id: str,
    message: str,
    auto_approve: bool,
    emit: Optional[EmitFn] = None,
) -> Dict[str, Any]:
    """Run the full multi-agent delegation pipeline.

    When ``emit`` is supplied, progress events are pushed as each stage runs so
    the UI can show live agent activity instead of a silent wait.
    """
    memory = MemoryStore()
    memory.append_message(session_id, "user", message)

    async def send(event: str, payload: Dict[str, Any]) -> None:
        if emit is not None:
            await emit(event, payload)

    # 1. Orchestrator
    trace = TaskTrace(task_id, session_id, enabled=settings.tracing_enabled)
    trace.start("orchestrator", "planning")
    await send("agent_start", {"agent": "orchestrator", "label": "Planning"})
    orchestrator = OrchestratorAgent()
    orch_result = await orchestrator.run(
        task_id=task_id,
        session_id=session_id,
        input_data={"message": message},
    )
    plan = orch_result.get("plan", {})
    agents = plan.get("agents", ["research"])
    await send("plan", {"plan": plan})
    dur = trace.end("orchestrator", "ok", {"intent": plan.get("intent")})
    await send(
        "agent_done",
        {"agent": "orchestrator", "intent": plan.get("intent"), "duration_ms": dur},
    )

    agent_results: List[AgentResult] = []
    current_input: Dict[str, Any] = {"query": message, "message": message}
    requires_approval = plan.get("requires_approval", False)

    # 2. Run agents in delegation order
    for agent_name in agents:
        if agent_name == "research":
            trace.start("research", "searching knowledge base")
            await send("agent_start", {"agent": "research", "label": "Searching knowledge base"})
            agent = ResearchAgent()
            res = await agent.run(
                task_id=task_id,
                session_id=session_id,
                input_data=current_input,
            )
            agent_results.append(
                AgentResult(agent=agent.name, task_id=task_id, data=res)
            )
            current_input["research_results"] = {
                "chunks": res.get("chunks", []),
                "answer": res.get("answer", ""),
                "chunk_count": res.get("chunk_count", 0),
            }
            sources = _unique_sources(res.get("chunks", []))
            dur = trace.end("research", "ok", {"chunks": res.get("chunk_count", 0)})
            await send(
                "agent_done",
                {
                    "agent": "research",
                    "chunk_count": res.get("chunk_count", 0),
                    "sources": sources,
                    "duration_ms": dur,
                },
            )

        elif agent_name == "analyst":
            trace.start("analyst", "analyzing findings")
            await send("agent_start", {"agent": "analyst", "label": "Analyzing findings"})
            agent = AnalystAgent()
            res = await agent.run(
                task_id=task_id,
                session_id=session_id,
                input_data=current_input,
            )
            agent_results.append(
                AgentResult(agent=agent.name, task_id=task_id, data=res)
            )
            current_input["analysis_results"] = {
                "analysis": res.get("analysis", ""),
            }
            dur = trace.end("analyst", "ok")
            await send("agent_done", {"agent": "analyst", "duration_ms": dur})

        elif agent_name == "execution":
            trace.start("execution", "executing action")
            await send("agent_start", {"agent": "execution", "label": "Executing action"})
            agent = ExecutionAgent()
            exec_input = dict(current_input)
            exec_input["auto_approve"] = auto_approve
            res = await agent.run(
                task_id=task_id,
                session_id=session_id,
                input_data=exec_input,
            )
            agent_results.append(
                AgentResult(agent=agent.name, task_id=task_id, data=res)
            )
            if res.get("requires_approval"):
                requires_approval = True
            dur = trace.end(
                "execution",
                res.get("status", "ok"),
                {"tool": res.get("tool"), "risk": res.get("risk")},
            )
            audit_record(
                "execution_stage",
                task_id=task_id,
                session_id=session_id,
                tool=res.get("tool"),
                risk=res.get("risk"),
                status=res.get("status"),
            )
            await send(
                "agent_done",
                {
                    "agent": "execution",
                    "status": res.get("status", "unknown"),
                    "duration_ms": dur,
                },
            )

    # 3. Synthesize final response
    trace.start("synthesis", "composing answer")
    await send("agent_start", {"agent": "synthesis", "label": "Composing answer"})
    final_response = await _synthesize_final_response(
        message, agent_results, plan
    )
    dur = trace.end("synthesis", "ok")
    await send("agent_done", {"agent": "synthesis", "duration_ms": dur})

    # 4. Review — verify the draft against retrieved evidence before delivery
    review_result: Optional[Dict[str, Any]] = None
    if settings.review_enabled and plan.get("intent") != "chitchat":
        trace.start("review", "reviewing answer")
        await send("agent_start", {"agent": "review", "label": "Reviewing answer"})
        chunks_for_review: List[Dict[str, Any]] = []
        for r in agent_results:
            if r.agent == "research":
                chunks_for_review = r.data.get("chunks", []) or []
                break
        reviewer = ReviewAgent()
        review_result = await reviewer.run(
            task_id=task_id,
            session_id=session_id,
            input_data={
                "question": message,
                "answer": final_response,
                "chunks": chunks_for_review,
            },
        )
        # Apply the revision when the reviewer produced one.
        if review_result.get("verdict") == "revise" and review_result.get("revised_answer"):
            final_response = review_result["revised_answer"]
        agent_results.append(
            AgentResult(agent="review", task_id=task_id, data=review_result)
        )
        dur = trace.end("review", "ok", {"verdict": review_result.get("verdict", "pass")})
        audit_record(
            "review_completed",
            task_id=task_id,
            session_id=session_id,
            verdict=review_result.get("verdict", "pass"),
            issue_count=len(review_result.get("issues") or []),
        )
        await send(
            "agent_done",
            {
                "agent": "review",
                "verdict": review_result.get("verdict", "pass"),
                "issues": len(review_result.get("issues") or []),
                "duration_ms": dur,
            },
        )

    memory.append_message(session_id, "assistant", final_response)

    # Pending approval (tool or HTTP) surfaced for the UI's approval card.
    task_ctx = memory.get_task_context(task_id) or {}
    pending_action = None
    if task_ctx.get("pending_tool_call"):
        pending_action = {
            "kind": "tool",
            **task_ctx["pending_tool_call"],
        }
    elif task_ctx.get("pending_execution"):
        pending_action = {
            "kind": "http",
            **task_ctx["pending_execution"],
        }

    trace_dict = trace.to_dict()
    await trace.persist()

    result = {
        "task_id": task_id,
        "session_id": session_id,
        "orchestrator_plan": plan,
        "agent_results": agent_results,
        "final_response": final_response,
        "requires_approval": requires_approval or pending_action is not None,
        "pending_action": pending_action,
        "review": review_result,
        "trace": trace_dict,
        "status": "completed",
    }
    # Emit plain dicts: AgentResult models are not JSON-serialisable directly.
    await send(
        "final",
        {**result, "agent_results": [r.model_dump() for r in agent_results]},
    )
    return result


def _unique_sources(chunks: List[Dict[str, Any]]) -> List[str]:
    seen: List[str] = []
    for chunk in chunks or []:
        source = chunk.get("source")
        if source and source not in seen:
            seen.append(source)
    return seen


async def _synthesize_final_response(
    user_message: str,
    agent_results: List[AgentResult],
    plan: Dict[str, Any],
) -> str:
    """Combine agent outputs into a final user-facing response."""
    parts: List[str] = []

    intent = plan.get("intent", "research")
    summary = plan.get("summary_for_user", "")

    if intent == "chitchat" or not agent_results:
        return summary or "I'm here to help. How can I assist you today?"

    for res in agent_results:
        agent = res.agent
        data = res.data

        if agent == "research":
            answer = data.get("answer", "")
            chunk_count = data.get("chunk_count", 0)
            if answer:
                parts.append(answer)
            if chunk_count == 0:
                parts.append("No relevant documents were found in the knowledge base.")

        elif agent == "analyst":
            analysis = data.get("analysis", "")
            if analysis:
                parts.append("\n--- Analysis ---\n" + analysis)

        elif agent == "execution":
            status = data.get("status", "unknown")
            tool = data.get("tool")
            if status == "no_action":
                parts.append(
                    "\n" + data.get(
                        "message",
                        "No specific API action was requested.",
                    )
                )
            elif status == "pending_approval":
                tool = data.get("tool", "action")
                parts.append(
                    f"\n⚠️ **Approval required** — `{tool}` is a write action. "
                    "Use the Approve button above to run it."
                )
            elif status in {"ok", "completed"}:
                if tool:
                    detail = data.get("message") or data.get("note")
                    if not detail:
                        # Concise human-readable summary of common tool shapes.
                        if "employees" in data:
                            names = ", ".join(
                                e.get("name", "?") for e in (data.get("employees") or [])[:5]
                            )
                            detail = f"{data.get('count', 0)} match(es): {names}" if names else "No matches."
                        elif "tickets" in data:
                            detail = (
                                f"{data.get('count', 0)} ticket(s) shown "
                                f"of {data.get('total_in_database', '?')} total."
                            )
                        elif "rows" in data:
                            detail = f"{data.get('row_count', len(data['rows']))} row(s) returned."
                        elif "filename" in data:
                            detail = data.get("message", f"Saved {data['filename']}.")
                        else:
                            detail = "Completed successfully."
                    parts.append(f"\n✅ Tool `{tool}` executed — {detail}")
            elif status == "blocked":
                parts.append(
                    f"\n🚫 Action blocked: {data.get('error', 'Domain not allowed')}"
                )
            elif status == "error":
                parts.append(
                    f"\n❌ Action failed: {data.get('error', 'Unknown error')}"
                )
            elif status in {"disabled", "no_tools"}:
                parts.append("\n" + data.get("message", "Tool execution unavailable."))

    if not parts:
        parts.append(summary or "I processed your request but did not generate a specific response.")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, _role=require_user):
    """Main chat endpoint: orchestrates agents and returns a response."""
    session_id = request.session_id or MemoryStore.generate_id()
    task_id = MemoryStore.generate_id()
    audit_record(
        "chat_requested",
        task_id=task_id,
        session_id=session_id,
        message_length=len(request.message),
        auto_approve=request.auto_approve,
    )

    try:
        result = await _run_agent_pipeline(
            task_id=task_id,
            session_id=session_id,
            message=request.message,
            auto_approve=request.auto_approve,
        )
        return ChatResponse(**result)
    except Exception as exc:
        logger.exception("Chat pipeline failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/chat/stream", tags=["chat"])
async def chat_stream(request: ChatRequest, _role=require_user):
    """Server-sent events version of /chat.

    Emits `agent_start`, `plan`, `agent_done` and `final` events so the UI can
    render live pipeline progress during long local-model runs.
    """
    session_id = request.session_id or MemoryStore.generate_id()
    task_id = MemoryStore.generate_id()
    audit_record(
        "chat_stream_requested",
        task_id=task_id,
        session_id=session_id,
        message_length=len(request.message),
        auto_approve=request.auto_approve,
    )
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(event: str, payload: Dict[str, Any]) -> None:
        await queue.put((event, payload))

    async def runner() -> None:
        try:
            await _run_agent_pipeline(
                task_id=task_id,
                session_id=session_id,
                message=request.message,
                auto_approve=request.auto_approve,
                emit=emit,
            )
        except Exception as exc:  # pragma: no cover
            logger.exception("Streaming chat pipeline failed")
            await queue.put(("error", {"message": str(exc)}))
        finally:
            await queue.put(("__end__", {}))

    async def event_source():
        task = asyncio.create_task(runner())
        try:
            yield f"event: session\ndata: {json.dumps({'session_id': session_id, 'task_id': task_id})}\n\n"
            while True:
                event, payload = await queue.get()
                if event == "__end__":
                    break
                data = json.dumps(payload, default=str)
                yield f"event: {event}\ndata: {data}\n\n"
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/kb/documents", tags=["knowledge-base"])
async def kb_documents(_role=require_viewer):
    """List documents currently in the knowledge base."""
    store = get_vector_store()
    await store.initialize()
    docs = await store.list_documents()
    return {
        "available": store.available,
        "count": len(docs),
        "documents": docs,
    }


@router.delete("/kb/documents/{doc_id}", tags=["knowledge-base"])
async def kb_delete_document(doc_id: str, role=require_admin):
    """Remove a document (and all its chunks) from the knowledge base."""
    audit_record("kb_document_deleted", doc_id=doc_id, role=role.name if role else None)
    store = get_vector_store()
    await store.initialize()
    if not store.available:
        raise HTTPException(status_code=503, detail="Vector store unavailable")
    try:
        await store.delete_document(doc_id)
    except Exception as exc:
        logger.exception("Document delete failed")
        raise HTTPException(status_code=500, detail=str(exc))
    return {"status": "deleted", "doc_id": doc_id}


@router.get("/sessions/{session_id}/history", tags=["sessions"])
async def session_history(session_id: str, limit: int = 100, _role=require_viewer):
    """Return stored conversation history for a session."""
    memory = MemoryStore()
    return {
        "session_id": session_id,
        "messages": memory.get_history(session_id, limit=limit),
    }


@router.delete("/sessions/{session_id}", tags=["sessions"])
async def session_clear(session_id: str, role=require_user):
    """Clear stored conversation history for a session."""
    MemoryStore().clear_session(session_id)
    audit_record("session_cleared", session_id=session_id)
    return {"status": "cleared", "session_id": session_id}


@router.get("/tasks/{task_id}/trace", tags=["monitoring"])
async def task_trace(task_id: str, _role=require_viewer):
    """Agent execution trace: per-stage timings and outcomes for a task."""
    trace = await get_trace(task_id)
    if not trace:
        raise HTTPException(
            status_code=404, detail="No trace found for this task (expired or unknown)"
        )
    return {"task_id": task_id, "trace": trace}


@router.get("/audit", tags=["monitoring"])
async def audit_log(limit: int = 100, event: Optional[str] = None, _role=require_admin):
    """Recent audit entries (newest last)."""
    from app.monitoring.audit import read_audit

    return {
        "count": len(read_audit(limit=limit, event_filter=event)),
        "entries": read_audit(limit=limit, event_filter=event),
    }


# ---------------------------------------------------------------------------
# Tools & human approval (Phase 4)
# ---------------------------------------------------------------------------

@router.get("/tools", tags=["tools"])
async def list_tools(_role=require_viewer):
    """List registered enterprise tools with their schemas and risk levels."""
    from app.tools.registry import get_registry

    registry = get_registry()
    return {
        "enabled": settings.tools_enabled,
        "count": len(registry.list()),
        "tools": [
            {
                "name": t.name,
                "description": t.description,
                "risk": t.risk,
                "requires_approval": t.requires_approval,
                "parameters": [
                    {
                        "name": p.name,
                        "type": p.type,
                        "required": p.required,
                        "description": p.description,
                    }
                    for p in t.parameters
                ],
            }
            for t in registry.list()
        ],
    }


@router.post("/tasks/{task_id}/approve", tags=["approval"])
async def approve_task(task_id: str, role=require_admin):
    """Approve and execute the pending action stored for a task."""
    from app.tools.registry import get_registry

    memory = MemoryStore()
    task_ctx = memory.get_task_context(task_id)
    if not task_ctx:
        raise HTTPException(status_code=404, detail="Unknown task or approval expired")

    pending_tool = task_ctx.get("pending_tool_call")
    pending_http = task_ctx.get("pending_execution")

    if pending_tool:
        registry = get_registry()
        spec = registry.get(pending_tool.get("tool", ""))
        if spec is None:
            raise HTTPException(status_code=404, detail="Tool no longer registered")
        audit_record(
            "approval_granted",
            task_id=task_id,
            tool=pending_tool.get("tool"),
            risk=pending_tool.get("risk"),
            role=role.name if role else None,
        )
        outcome = await registry.invoke(
            pending_tool.get("tool", ""), pending_tool.get("args") or {}
        )
        pending_tool.update({"status": "approved", "result": outcome})
        task_ctx["pending_tool_call"] = None
        task_ctx["approved_action"] = pending_tool
        memory.set_task_context(task_id, task_ctx)

        # Append the outcome to the conversation so the UI can show it.
        session_id = task_ctx.get("session_id")
        if session_id and outcome.get("status") == "ok":
            summary = (
                outcome.get("message")
                or outcome.get("note")
                or f"Approved and executed '{pending_tool.get('tool')}'."
            )
            memory.append_message(session_id, "assistant", f"✅ Approved: {summary}")

        return {"status": "approved", "task_id": task_id, "result": outcome}

    if pending_http:
        # Execute the allow-listed HTTP call the same way the agent would.
        agent = ExecutionAgent()
        exec_input = dict(pending_http)
        exec_input["auto_approve"] = True
        exec_input["url"] = pending_http.get("url", "")
        outcome = await agent.run(
            task_id=task_id,
            session_id=task_ctx.get("session_id", ""),
            input_data=exec_input,
        )
        pending_http.update({"status": "approved", "result": outcome})
        task_ctx["pending_execution"] = None
        task_ctx["approved_action"] = pending_http
        memory.set_task_context(task_id, task_ctx)
        return {"status": "approved", "task_id": task_id, "result": outcome}

    raise HTTPException(status_code=409, detail="No pending action for this task")


@router.post("/tasks/{task_id}/reject", tags=["approval"])
async def reject_task(task_id: str, role=require_admin):
    """Reject the pending action stored for a task."""
    audit_record("approval_rejected", task_id=task_id)
    memory = MemoryStore()
    task_ctx = memory.get_task_context(task_id)
    if not task_ctx:
        raise HTTPException(status_code=404, detail="Unknown task or approval expired")

    pending = task_ctx.get("pending_tool_call") or task_ctx.get("pending_execution")
    if not pending:
        raise HTTPException(status_code=409, detail="No pending action for this task")

    if task_ctx.get("pending_tool_call"):
        task_ctx["pending_tool_call"]["status"] = "rejected"
        task_ctx["rejected_action"] = task_ctx.pop("pending_tool_call")
    else:
        task_ctx["pending_execution"]["status"] = "rejected"
        task_ctx["rejected_action"] = task_ctx.pop("pending_execution")
    memory.set_task_context(task_id, task_ctx)

    session_id = task_ctx.get("session_id")
    if session_id:
        memory.append_message(
            session_id, "assistant", "❎ Action rejected — nothing was executed."
        )
    return {"status": "rejected", "task_id": task_id}


@router.post("/ingest", response_model=IngestResponse)
async def ingest(request: IngestRequest, _role=require_user):
    """Ingest documents into the RAG knowledge base."""
    audit_record(
        "kb_ingest_requested",
        source=request.source or request.file_path or request.directory or "inline",
        doc_id=request.doc_id,
    )
    try:
        if request.text:
            ids = await ingest_text(
                text=request.text,
                source=request.source or "inline",
                doc_id=request.doc_id,
                chunk_size=request.chunk_size,
                overlap=request.overlap,
            )
            return IngestResponse(
                status="completed",
                chunks_ingested=len(ids),
                doc_id=request.doc_id or request.source or "inline",
            )

        if request.file_path:
            ids = await ingest_file(
                file_path=request.file_path,
                doc_id=request.doc_id,
                chunk_size=request.chunk_size,
                overlap=request.overlap,
            )
            return IngestResponse(
                status="completed",
                chunks_ingested=len(ids),
                doc_id=request.doc_id,
            )

        if request.directory:
            ids = await ingest_directory(
                directory=request.directory,
                doc_id_prefix=request.doc_id,
            )
            return IngestResponse(
                status="completed",
                chunks_ingested=len(ids),
                doc_id=request.doc_id,
            )

        raise HTTPException(
            status_code=400,
            detail="Provide text, file_path, or directory to ingest",
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.exception("Ingestion failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/audit/tail", tags=["monitoring"])
async def audit_tail(lines: int = 50, _role=require_admin):
    """Raw tail of today's audit log file (for ops/debugging)."""
    import os
    from datetime import datetime, timezone

    settings = get_settings()
    path = os.path.join(settings.audit_dir, f"audit-{datetime.now(timezone.utc):strftime('%Y-%m-%d')}.log")
    if not os.path.exists(path):
        return {"entries": []}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            content = fh.readlines()[-max(1, min(lines, 500)):]
        return {"entries": [l.strip() for l in content if l.strip()]}
    except OSError as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest, _role=require_viewer):
    """Semantic search over the knowledge base."""
    try:
        store = get_vector_store()
        await store.initialize()
        results = await store.search(
            query=request.query,
            top_k=request.top_k,
            min_score=request.min_score,
            filter_doc_id=request.filter_doc_id,
        )
        chunk_results = [
            ChunkResult(
                chunk_id=r.get("chunk_id", ""),
                text=r.get("text", ""),
                source=r.get("source", ""),
                doc_id=r.get("doc_id", ""),
                page=r.get("page"),
                section=r.get("section"),
                metadata=r.get("metadata", {}),
                score=r.get("score"),
            )
            for r in results
        ]
        return SearchResponse(
            query=request.query,
            results=chunk_results,
            count=len(chunk_results),
        )
    except Exception as exc:
        logger.exception("Search failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/health", response_model=HealthResponse)
async def health():
    """Health check endpoint."""
    from app.memory.store import _get_redis_client

    if settings.use_azure_openai:
        provider = "azure_openai"
    elif settings.use_openai:
        provider = "ollama" if "11434" in (settings.openai_base_url or "") else "openai"
    else:
        provider = "mock"

    return HealthResponse(
        status="ok",
        llm_provider=provider,
        vector_store="azure_ai_search" if settings.use_azure_ai_search else "qdrant",
        memory_backend="redis" if _get_redis_client() is not None else "in-memory",
    )