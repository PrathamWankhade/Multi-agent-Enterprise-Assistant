# Troubleshooting

Solutions to the most common issues. When anything external fails, the app logs a warning and degrades instead of crashing.

---

## The UI shows "Backend unreachable"

1. Confirm the server is running: `curl http://127.0.0.1:8000/api/v1/health`.
2. On Windows, start it from the repository root with `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`.
3. Check for port conflicts (another process on 8000).
4. Inspect `srv_err.log` for startup errors.

---

## Answers are slow (30–200 seconds)

This is expected with a small local model on CPU. Each agent makes its own LLM call.

- Greetings are instant (LLM-free fast path).
- Research answers: ~60–100 s; full analysis: ~150–200 s.
- **Speed it up:**
  - `REVIEW_LLM_ENABLED=false` — removes one model call, keeps deterministic checks.
  - Use a smaller model, e.g. `ollama pull qwen2.5:1.5b` and set `OPENAI_MODEL`.
  - Reduce retrieved chunks in `app/agents/research.py`.
- The live pipeline stepper shows exactly where time is spent (per-agent durations).

---

## KB badge shows 0 or `–`

- The badge retries automatically while the store warms up.
- Check `GET /api/v1/kb/documents`: `"available": true` and `"count": 4` expected.
- If count is 0, reseed: `python scripts/seed_kb.py --force`.

---

## Search returns no results

- If the vector store is offline, keyword search over `data/*.md` is used — queries must share words with the documents.
- Confirm documents exist: `data/company_handbook.md`, `products.md`, `refund_policy.md`.
- Re-ingest if needed (see above).

---

## "Domain not in allow-list"

Direct HTTP execution requires the target domain in `ALLOWED_EXECUTION_DOMAINS`, e.g.:

```env
ALLOWED_EXECUTION_DOMAINS=api.example.com,hooks.example.org
```

The built-in tools (tickets, HR, SQL) do not need this.

---

## Approval card does not appear

- Only `write`/`external` tools create approvals; `read` tools run immediately.
- `EXECUTION_REQUIRE_APPROVAL` must be `true`.
- `auto_approve: true` in the chat request skips the gate.
- After approval, the task cannot be re-approved (`409`) — start a new chat.

---

## 401 / 403 errors

- `401` — missing or invalid `X-API-Key` (or Bearer token). Set it via the 🔑 button in the UI.
- `403` — your role is too low. `viewer` can read, `user` can chat, `admin` can approve and read audit logs.
- Auth is OFF by default; if you did not enable it, check that `AUTH_ENABLED=false`.

---

## Redis / Qdrant connection warnings

Harmless — the app falls back to in-process memory and keyword retrieval. To use real services:

- Redis: verify `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`.
- Qdrant Cloud: use a full URL in `QDRANT_HOST` plus `QDRANT_API_KEY`.
- Local Docker: `docker compose up -d` and keep `QDRANT_HOST=localhost`.

---

## Azure integration issues

- **Not detected** — placeholder values like `your-resource` are intentionally ignored. Use real endpoints.
- **Managed identity fails** — install `pip install -r requirements-azure.txt` and assign RBAC roles (see [Azure guide](../AZURE_DEPLOYMENT.md)).
- **Wrong embedding dimension** — Azure OpenAI `text-embedding-3-small` produces 1536 dims; Ollama `nomic-embed-text` produces 768. A populated collection is preserved; a suffixed one is created instead.

---

## Tests fail

```bash
python -m pytest tests/ -q
```

The suite forces mock mode in `tests/conftest.py` and needs no network. If you added a test that touches a real service, mock it with `monkeypatch` or `pytest.fixture`.

---

## Still stuck?

Check `srv_err.log`, `GET /api/v1/health`, `GET /api/v1/audit`, and the browser console (F12). Every failure is logged with a full traceback.
