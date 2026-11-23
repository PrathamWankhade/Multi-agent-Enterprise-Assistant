# Multi-Agent Enterprise Assistant

A fully functional multi-agent system with RAG, memory, tool calling, a
human-approval workflow, and Azure deployment support.

## Architecture

- **Orchestrator Agent** - Classifies intent and delegates to specialist agents
- **Research Agent** - RAG-based document retrieval via Qdrant / Azure AI Search
- **Analyst Agent** - Generates insights, summaries, and recommendations
- **Execution Agent** - Picks enterprise tools (HR directory, IT tickets, SQL, reports) and runs them behind an approval gate
- **Review Agent** - Verifies the drafted answer against retrieved evidence, flags unsupported claims, and rewrites when needed
- **Tool framework** - Schema-validated registry with `read` / `write` risk levels (`app/tools/`)
- **Human approval** - Write tools pause until approved or rejected through the UI or API
- **Security** - Optional API-key auth with viewer/user/admin roles (OFF by default)
- **Monitoring** - Per-agent execution traces + structured JSON-lines audit trail
- **Memory Layer** - Redis-backed conversation and task context (in-memory fallback)
- **Backend** - Python + FastAPI
- **Cloud** - Azure AI Foundry + Azure Functions ready

### Enterprise tools

| Tool | Risk | What it does |
|---|---|---|
| `lookup_employee` | read | Search the mock HR directory (name, ID, department, role) |
| `list_tickets` | read | List IT support tickets filtered by status/category |
| `query_database` | read | Read-only SQL (`SELECT`/`WITH` only — mutations rejected) |
| `create_ticket` | **write** | Open an IT support ticket — requires human approval |
| `generate_report` | **write** | Save query results as a CSV in `reports/` — requires human approval |

The mock SQLite database (`data/enterprise.db`) is created and seeded with
10 employees and 22 IT tickets on first start.

## Quick Start

### 1. Install dependencies

```bash
cd multi_agent_assistant
pip install -r requirements.txt
```

### 2. Configure environment

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

The app runs in one of three modes, chosen automatically from `.env`:

| Mode | What you need | Behaviour |
|---|---|---|
| **Local (Ollama)** | Ollama running + `OPENAI_BASE_URL=http://localhost:11434/v1` | Real local LLM + real embeddings |
| **Cloud** | Real `AZURE_OPENAI_*` or `OPENAI_API_KEY` values | Real hosted LLM + embeddings |
| **Mock** | Nothing | Deterministic offline responses, keyword retrieval |

Placeholder values such as `your-resource.openai.azure.com` are detected and ignored,
so an unfinished `.env` never breaks the app — it falls back instead.

#### Local mode with Ollama (no cloud credentials needed)

```bash
ollama pull qwen3.5:4b        # chat model (any model you have works)
ollama pull nomic-embed-text  # embedding model
```

`.env`:

```env
OPENAI_API_KEY=ollama
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_MODEL=qwen3.5:4b
OPENAI_EMBEDDING_MODEL=nomic-embed-text
```

Reasoning models are called with thinking disabled so they return direct answers.
The collection's vector size is matched to the embedding model automatically
(768 for `nomic-embed-text`, 1536 for `text-embedding-3-small`).

### 3. Start infrastructure (optional)

```bash
docker compose up -d   # Qdrant + Redis
```

Both are optional:

- **Qdrant** — a Qdrant Cloud URL works too (`QDRANT_HOST=https://xxx.cloud.qdrant.io` + `QDRANT_API_KEY`).
  If no vector store is reachable, retrieval falls back to keyword search over `data/`.
- **Redis** — if unreachable or the password is wrong, memory falls back to a
  shared in-process store. It is probed once, not on every request.

### 4. Run the server

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Then open **http://127.0.0.1:8000/** for the chat UI.

> On Windows use `python -m uvicorn` (not bare `uvicorn`) and run it from the
> `multi_agent_assistant/` folder, not from `app/`.

### 5. Knowledge base

The bundled documents in `data/` are ingested automatically on first startup
(only when the collection is empty). To rebuild after editing them:

```bash
python scripts/seed_kb.py --force
```

Or ingest your own content:

```bash
curl -X POST http://localhost:8000/api/v1/ingest \
  -H "Content-Type: application/json" \
  -d '{"directory": "./data"}'
```

Ingestion is idempotent — re-ingesting a document replaces its previous chunks.

## Web UI

Open **http://127.0.0.1:8000/** — a single-page workspace, no build step required.

- **Live agent pipeline** — answers stream over Server-Sent Events, so you watch
  Orchestrator → Research → Analyst → Compose → Review progress with a running
  timer instead of staring at a blank screen for a minute.
- **Human approval cards** — when a write tool is selected, the answer includes
  an Approve/Reject card showing the exact tool and arguments. Approving runs it
  live; rejecting records the decision. Nothing executes without you.
- **Review verdict** — every answer carries a `review approved / revised` badge
  from the Review Agent's grounding check.
- **Stop / Retry** — cancel a long run mid-flight; failed answers offer a retry.
- **Citations you can open** — every `[source: file]` becomes a chip; click it to
  read the exact retrieved excerpts with their similarity scores.
- **Knowledge base manager** — browse indexed documents, paste in new ones, and
  remove documents, all from the sidebar.
- **Conversations** — multiple sessions kept in `localStorage`, with server-side
  history in Redis; titles are generated from your first question.
- **Light / dark theme** — follows your OS preference, remembered across visits.
- **Keyboard** — `Enter` send, `Shift+Enter` newline, `Ctrl/Cmd+K` new chat,
  `Esc` close dialogs.
- Responsive down to phone widths, with focus rings, `aria-live` regions and
  `prefers-reduced-motion` support.

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/api/v1/chat` | POST | Main chat - orchestrates agents, returns one JSON payload |
| `/api/v1/chat/stream` | POST | Same pipeline as SSE events (`agent_start`, `plan`, `agent_done`, `final`) |
| `/api/v1/ingest` | POST | Ingest documents into RAG |
| `/api/v1/search` | POST | Semantic search over knowledge base |
| `/api/v1/kb/documents` | GET | List indexed documents with chunk counts |
| `/api/v1/kb/documents/{doc_id}` | DELETE | Remove a document and its chunks |
| `/api/v1/sessions/{id}/history` | GET | Stored conversation history |
| `/api/v1/sessions/{id}` | DELETE | Clear a conversation |
| `/api/v1/tools` | GET | Registered tools with schemas and risk levels |
| `/api/v1/tasks/{id}/approve` | POST | Approve and execute a pending write action (admin) |
| `/api/v1/tasks/{id}/reject` | POST | Reject a pending write action (admin) |
| `/api/v1/tasks/{id}/trace` | GET | Agent execution timings for a task (viewer) |
| `/api/v1/audit` | GET | Recent audit events (admin) |
| `/api/v1/audit/tail` | GET | Raw tail of today's audit log file (admin) |
| `/api/v1/health` | GET | Health check |
| `/docs` | GET | Interactive API docs (Swagger) |

### Example: Chat

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What is the company leave policy?",
    "session_id": "user-123"
  }'
```

### Example: Ingest text

```bash
curl -X POST http://localhost:8000/api/v1/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Our refund policy allows returns within 30 days with a receipt.",
    "source": "refund-policy.txt"
  }'
```

### Example: Execution (requires allow-list domain)

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Call the weather API for New York",
    "auto_approve": true
  }'
```

### Example: Create a ticket (pauses for approval)

```bash
# 1. Ask for a write action — the response includes pending_action
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Create an IT ticket: laptop won'\''t boot, priority high"}'

# 2. Approve it (or POST /tasks/{task_id}/reject)
curl -X POST http://127.0.0.1:8000/api/v1/tasks/{task_id}/approve
```

With `auto_approve: true` the action runs immediately (for trusted callers).

## Security & governance

Authentication is **off by default**, so local/demo use needs no setup. Turn it
on when deploying:

```env
AUTH_ENABLED=true
API_KEYS="long-random-admin-key:admin,long-random-user-key:user,long-random-view-key:viewer"
```

Clients send `X-API-Key: <key>` (or `Authorization: Bearer <key>`). The UI has a
key button in the top bar that stores the key in `localStorage` only.

| Role | Allowed |
|---|---|
| `viewer` | health, KB list, search, tools list, session history, task traces |
| `user` | viewer + chat, ingest, clear sessions |
| `admin` | user + approve/reject actions, delete KB documents, read audit log |

Security-relevant events (`chat_requested`, `approval_granted`,
`approval_rejected`, `auth_failed`, `kb_document_deleted`, …) are appended to
`logs/audit-YYYY-MM-DD.log` as JSON lines and exposed through `/api/v1/audit`.
Failed auth (missing/invalid key, insufficient role) is always recorded.

## Monitoring & evaluation

Every chat response includes a **trace** with per-agent durations:

```json
"trace": {
  "task_id": "...",
  "total_ms": 4821,
  "steps": [
    {"agent": "orchestrator", "duration_ms": 312, "status": "ok"},
    {"agent": "research", "duration_ms": 2110, "status": "ok"},
    {"agent": "synthesis", "duration_ms": 5, "status": "ok"},
    {"agent": "review", "duration_ms": 2394, "status": "ok"}
  ]
}
```

Fetch it later with `GET /api/v1/tasks/{id}/trace`. The UI shows each duration
next to its pipeline step and the Review Agent's verdict in the message meta
line — evidence of real multi-agent execution, not just a chat log.

## Configuration

See `.env.example` for all environment variables. Key settings:

- `AZURE_OPENAI_*` — Azure OpenAI endpoint/key/deployment
- `OPENAI_API_KEY` — Regular OpenAI key (alternative to Azure)
- `OPENAI_BASE_URL` — OpenAI-compatible endpoint, e.g. Ollama at `http://localhost:11434/v1`
- `OPENAI_MODEL` / `OPENAI_EMBEDDING_MODEL` — model names for the OpenAI-compatible endpoint
- `QDRANT_HOST`/`QDRANT_PORT`/`QDRANT_API_KEY` — Qdrant vector store (host or full Cloud URL)
- `REDIS_HOST`/`REDIS_PORT` — Redis memory store
- `ALLOWED_EXECUTION_DOMAINS` — Comma-separated domains the Execution Agent may call
- `EXECUTION_REQUIRE_APPROVAL` — Require human approval for write operations
- `REVIEW_ENABLED` / `REVIEW_LLM_ENABLED` — Review Agent on/off; the LLM
  groundedness pass adds one model call per answer (disable on slow CPUs)
- `TOOLS_ENABLED` — Enable/disable the enterprise tool framework
- `SQLITE_PATH` / `REPORTS_DIR` — Mock database and CSV report output locations
- `AUTH_ENABLED` / `API_KEYS` — API-key auth with roles (off by default)
- `AUDIT_DIR` / `TRACING_ENABLED` — Audit log directory and agent timing traces

## Graceful degradation

Nothing external is required for the app to start and answer:

| Dependency | When unavailable |
|---|---|
| LLM endpoint | Deterministic mock responses |
| Embedding model | Mock embeddings + keyword retrieval over `data/` |
| Qdrant / Azure AI Search | Keyword retrieval, no crash |
| Redis | Shared in-process memory |
| Placeholder credentials | Ignored automatically |

Every failure is logged as a warning rather than returned to the user as an error.

Indexed data is never destroyed automatically: if the embedding model's
dimension no longer matches a **populated** collection, the app switches to a
dimension-suffixed collection (e.g. `enterprise_kb_d768`) and leaves the
original intact. Only empty collections are recreated.

## Azure deployment (optional, Phase 6)

The **local stack is the primary setup** — Azure is a fully optional future
integration. Everything needed to deploy is in [`AZURE_DEPLOYMENT.md`](./AZURE_DEPLOYMENT.md):

- **Azure OpenAI** — chat + embeddings through the existing OpenAI SDK, with
  API key **or** passwordless `DefaultAzureCredential` managed identity.
- **Azure AI Search** — a real REST backend (`app/rag/azure_search.py`) with
  index creation, vector upsert/search, document listing and deletion.
- **`azure/main.bicep`** — Container Apps + AI Search + Redis + Key Vault +
  Log Analytics, with a managed identity and RBAC role assignment.
- **`azure.yaml`** — one-command `azd up` provisioning + deployment.
- **`requirements-azure.txt`** — optional `azure-identity` for managed identity.

Switching providers needs **zero code changes** — set the `AZURE_*` variables in
`.env`. Placeholder/empty values are ignored, so an unfinished Azure config
never breaks local mode. Auth, approvals, audit and tracing work identically.

## Cloud provider readiness

The system is designed for Azure AI Foundry and Azure Functions:

- Deploy FastAPI to Azure Container Apps or App Service
- Use Azure AI Foundry for model hosting and agent definitions
- Use Azure Functions for execution actions (event-driven)
- Use Azure AI Search or Qdrant for RAG
- Use Cosmos DB or Redis Cache for memory
- Use Azure Key Vault for secrets
- Use Managed Identity for passwordless connectivity

## Testing

The suite runs 100% offline — no Ollama, Qdrant, Redis or network needed.
`tests/conftest.py` forces mock mode before importing the app.

```bash
python -m pytest tests/ -q
# 41 passed
```

Coverage highlights:

- `test_api.py` — health, static routes, chat, traces, audit, ingest/search, sessions
- `test_tools.py` — registry, argument validation, read-only SQL, approve/reject
- `test_review.py` — hallucination/citation checks, verdicts, persistence
- `test_azure.py` — managed identity, provider detection, mocked Search index/upload/search/delete
- `test_auth.py` — all three roles, 401/403 paths, Bearer tokens, auth audit events

## Docker deployment

```bash
# Full local stack: app + Qdrant + Redis (Ollama runs on the host)
docker compose up --build -d

# Health check
curl http://localhost:8000/api/v1/health
```

`docker-compose.yml` uses persistent volumes for Qdrant and Redis, mounts
`./data`, `./reports` and `./logs` for the mock database/reports/audit trail,
and configures the app to reach host Ollama at `host.docker.internal:11434`.
The image runs a non-root-friendly layout with a `HEALTHCHECK` on `/health`.

For cloud Qdrant/Redis, leave those services out and put your connection
settings in `.env` (the `environment:` block in compose overrides `.env` —
remove the overrides when using cloud endpoints).

## License

Internal use.