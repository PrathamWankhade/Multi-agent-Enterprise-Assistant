# Testing & Development

---

## Test suite

```bash
python -m pytest tests/ -q
# 52 passed in ~20s
```

100% offline. `tests/conftest.py` sets mock-mode environment variables **before** importing the app, so no Ollama, Qdrant, Redis or Azure account is needed.

| File | Covers |
|---|---|
| `test_api.py` | Health, static routes, chat, traces, audit, ingest/search, sessions |
| `test_tools.py` | Registry, validation, read-only SQL, approve/reject |
| `test_review.py` | Citation/hallucination checks, verdicts, persistence |
| `test_auth.py` | Roles, 401/403, Bearer tokens, auth audit events |
| `test_azure.py` | Provider detection, managed identity, mocked Search index/upload/search/delete |
| `test_itsm.py` | Classification, ticket lifecycle, approvals, technician updates, tracking, RBAC |
| `test_auth_jwt.py` | JWT login/refresh, user/admin roles, admin task and priority assignment, per-user rules |

**Writing tests:** use the session-scoped `client` fixture for HTTP calls, and `fresh_settings(**kwargs)` to temporarily change configuration for one test.

---

## Local development

```bash
# install
pip install -r requirements.txt

# run with auto-reload
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# rebuild the knowledge base after editing data/*.md
python scripts/seed_kb.py --force
```

Static frontend files are served with `Cache-Control: no-store`, so browser edits appear without a hard refresh.

---

## Adding an agent

1. Create `app/agents/<name>.py` inheriting `BaseAgent`; implement `run(task_id, session_id, input_data)` returning a dict.
2. Add it to the orchestrator's prompt and the delegation loop in `app/api/routes.py`.
3. Add a label to `AGENT_INFO` in `frontend/app.js` so the live stepper renders it.
4. Emit `agent_start` / `agent_done` events via `send(...)` for progress UI, and wrap with `trace.start()` / `trace.end()` for timings.
5. Add a test asserting its result appears in `agent_results`.

---

## Adding a tool

1. Write a handler in `app/tools/enterprise.py`.
2. Register a `ToolSpec` in `register_tools()` with accurate `risk` and `parameters`.
3. Add a mocked test in `tests/test_tools.py`.
4. Write tools automatically gain an approval card in the UI — no frontend change needed.

---

## Adding an API endpoint

1. Add the route in `app/api/routes.py` with the appropriate dependency:
   - `_role=require_viewer` for reads
   - `_role=require_user` for writes
   - `_role=require_admin` for approvals/audit
2. Add a Pydantic schema in `app/api/schemas.py` if needed.
3. Call `audit_record(...)` for security-relevant actions.
4. Document it in `docs/API.md`.

---

## Code style

- Type hints on public functions.
- Async throughout the request path; sync handlers are allowed in the tool registry (awaited if needed).
- Log warnings on degraded paths; never crash the app because an optional service is down.
- Keep prompts in the agent classes, not scattered in routes.

---

## Documentation

This documentation lives in `docs/` and renders directly on GitHub (including the Mermaid diagrams). Update the relevant `docs/*.md` file when behavior changes and keep the README's feature/API tables in sync.
