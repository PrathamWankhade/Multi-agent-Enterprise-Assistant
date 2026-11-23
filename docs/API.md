# API Reference

Base URL: `http://127.0.0.1:8000/api/v1` · Interactive Swagger: [`http://127.0.0.1:8000/docs`](http://127.0.0.1:8000/docs)

All write endpoints may require the `X-API-Key` header when `AUTH_ENABLED=true`. Roles: `viewer` < `user` < `admin`.

---

## Chat

### `POST /chat` · role: user

Run the full pipeline and return one JSON response.

**Request**
```json
{
  "message": "What is the company leave policy?",
  "session_id": "user-123",
  "auto_approve": false
}
```

**Response** (abbreviated)
```json
{
  "task_id": "uuid",
  "session_id": "user-123",
  "orchestrator_plan": {
    "intent": "research",
    "agents": ["research"],
    "requires_approval": false
  },
  "agent_results": [
    { "agent": "research", "task_id": "uuid", "data": { "chunk_count": 4 } },
    { "agent": "review", "task_id": "uuid", "data": { "verdict": "pass" } }
  ],
  "final_response": "Based on your handbook… [source: company_handbook.md]",
  "requires_approval": false,
  "pending_action": null,
  "review": { "verdict": "pass", "issues": [], "llm_checked": true },
  "trace": { "total_ms": 4821, "steps": [] },
  "status": "completed"
}
```

### `POST /chat/stream` · role: user

Same pipeline as Server-Sent Events. See [Architecture](ARCHITECTURE.md#2-request-lifecycle) for the event list.

```bash
curl -N -X POST http://127.0.0.1:8000/api/v1/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "Compare our products", "session_id": "s1"}'
```

---

## Knowledge base

### `POST /ingest` · role: user

```json
{ "text": "Our refund window is 30 days.", "source": "refund.md" }
```

Also accepts `file_path` or `directory`. Ingestion is idempotent (deterministic chunk IDs; old chunks replaced).

### `POST /search` · role: viewer

```json
{ "query": "remote work", "top_k": 5, "min_score": 0.0 }
```

Returns `{ query, results: [{chunk_id, text, source, score, ...}], count }`.

### `GET /kb/documents` · role: viewer

Documents with chunk counts, character counts and previews. Includes `"available": false` when the vector store is offline.

### `DELETE /kb/documents/{doc_id}` · role: admin

Removes a document and all its chunks. Audited.

---

## Tools

### `GET /tools` · role: viewer

```json
{
  "enabled": true,
  "count": 5,
  "tools": [
    { "name": "create_ticket", "risk": "write", "requires_approval": true,
      "parameters": [] }
  ]
}
```

### `POST /tasks/{task_id}/approve` · role: admin

Executes the pending action stored for the task.

```json
{
  "status": "approved",
  "task_id": "uuid",
  "result": { "status": "ok", "tool": "create_ticket", "ticket_id": 24 }
}
```

Returns `409` when there is no pending action, `404` for an unknown task.

### `POST /tasks/{task_id}/reject` · role: admin

Rejects the pending action. Nothing is executed.

---

## Sessions

### `GET /sessions/{id}/history` · role: viewer

```json
{ "session_id": "s1", "messages": [{ "role": "user", "content": "…" }] }
```

### `DELETE /sessions/{id}` · role: user

Clears conversation history.

---

## Monitoring

### `GET /tasks/{id}/trace` · role: viewer

```json
{
  "task_id": "uuid",
  "trace": {
    "total_ms": 4821,
    "steps": [
      { "agent": "orchestrator", "label": "planning", "duration_ms": 187, "status": "ok" },
      { "agent": "research", "label": "searching knowledge base", "duration_ms": 2110, "status": "ok" }
    ]
  }
}
```

### `GET /audit?limit=100&event=approval_granted` · role: admin

Recent audit entries from the in-memory ring buffer (newest last).

### `GET /audit/tail?lines=50` · role: admin

Raw tail of today's audit log file.

---

## Health

### `GET /health` · public

```json
{
  "status": "ok",
  "llm_provider": "ollama",
  "vector_store": "qdrant",
  "memory_backend": "redis"
}
```

Provider values: `ollama`, `openai`, `azure_openai`, `mock` · Store: `qdrant`, `azure_ai_search` · Memory: `redis`, `in-memory`.

---

## Error responses

| Status | Meaning |
|---|---|
| `400` | Invalid request (e.g. ingest with no content) |
| `401` | Missing or invalid API key |
| `403` | Role lacks permission for the endpoint |
| `404` | Unknown task / trace not found |
| `409` | No pending action for that task |
| `500` | Unhandled server error (logged with full traceback) |

Every error body contains a `detail` field describing the problem.
