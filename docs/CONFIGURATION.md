# Configuration

All settings are environment variables read by `app/config.py` (pydantic-settings). Copy `.env.example` to `.env` and edit. **Every section is optional** — placeholder or empty values are detected and ignored.

---

## LLM providers

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | – | OpenAI-compatible key. Use `ollama` for local Ollama |
| `OPENAI_BASE_URL` | – | e.g. `http://localhost:11434/v1` for Ollama |
| `OPENAI_MODEL` | `gpt-4o-mini` | Chat model |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model (768-dim for `nomic-embed-text`) |
| `AZURE_OPENAI_ENDPOINT` | – | Azure OpenAI resource endpoint |
| `AZURE_OPENAI_API_KEY` | – | Azure key (optional with managed identity) |
| `AZURE_OPENAI_API_VERSION` | `2024-02-15-preview` | API version |
| `AZURE_OPENAI_DEPLOYMENT_NAME` | – | Chat deployment |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | – | Embedding deployment |
| `AZURE_USE_MANAGED_IDENTITY` | `false` | Use `DefaultAzureCredential` instead of keys |

Provider selection order: Azure OpenAI → OpenAI-compatible → deterministic mock.

---

## Vector store

| Variable | Default | Description |
|---|---|---|
| `QDRANT_HOST` | `localhost` | Host or full Cloud URL |
| `QDRANT_PORT` | `6333` | Port |
| `QDRANT_API_KEY` | – | Cloud API key |
| `QDRANT_COLLECTION` | `enterprise_kb` | Collection name |
| `AZURE_AI_SEARCH_ENDPOINT` | – | Search service endpoint |
| `AZURE_AI_SEARCH_API_KEY` | – | Admin key |
| `AZURE_AI_SEARCH_INDEX_NAME` | `enterprise-kb` | Index name |
| `AZURE_AI_SEARCH_API_VERSION` | `2024-07-01` | REST API version |

Azure AI Search is selected automatically when its endpoint + key (or managed identity) are present. Collection dimension is matched to the embedding model; populated collections are never deleted on a dimension mismatch.

---

## Memory

| Variable | Default | Description |
|---|---|---|
| `REDIS_HOST` | `localhost` | Redis host |
| `REDIS_PORT` | `6379` | Port |
| `REDIS_DB` | `0` | Database index |
| `REDIS_PASSWORD` | – | Password |
| `MEMORY_TTL_SECONDS` | `86400` | Key expiry (24h) |

If Redis is unreachable, a shared in-process store is used. The connection is probed once per process.

---

## Agents & tools

| Variable | Default | Description |
|---|---|---|
| `REVIEW_ENABLED` | `true` | Run the Review Agent after synthesis |
| `REVIEW_LLM_ENABLED` | `true` | LLM grounding pass (adds one model call; disable on slow CPUs) |
| `TOOLS_ENABLED` | `true` | Enable the enterprise tool framework |
| `EXECUTION_REQUIRE_APPROVAL` | `true` | Require approval for write actions |
| `ALLOWED_EXECUTION_DOMAINS` | – | Comma-separated domains for direct HTTP calls |
| `SQLITE_PATH` | `data/enterprise.db` | Mock ERP database |
| `REPORTS_DIR` | `reports` | CSV report output directory |

---

## Security & monitoring

| Variable | Default | Description |
|---|---|---|
| `AUTH_ENABLED` | `false` | Enable API-key authentication |
| `API_KEYS` | – | `key1:admin,key2:user,key3:viewer` |
| `AUDIT_DIR` | `logs` | Audit log directory |
| `TRACING_ENABLED` | `true` | Record per-agent execution timings |
| `SECRET_KEY` | `change-this-in-production` | App secret |
| `ALLOWED_ORIGINS` | `http://localhost:3000` | CORS origins (comma-separated) |
| `LOG_LEVEL` | `INFO` | Python log level |

---

## Application

| Variable | Default | Description |
|---|---|---|
| `APP_HOST` | `0.0.0.0` | Bind host |
| `APP_PORT` | `8000` | Bind port |
| `APP_DEBUG` | `true` | Uvicorn reload (disable in production) |

---

## Example: local Ollama

```env
OPENAI_API_KEY=ollama
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_MODEL=qwen3.5:4b
OPENAI_EMBEDDING_MODEL=nomic-embed-text
REVIEW_LLM_ENABLED=false   # optional: faster answers on CPU
```

## Example: Azure with managed identity

```env
AZURE_USE_MANAGED_IDENTITY=true
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-small
AZURE_AI_SEARCH_ENDPOINT=https://<search>.search.windows.net
AZURE_AI_SEARCH_INDEX_NAME=enterprise-kb
AUTH_ENABLED=true
API_KEYS=long-random-key:admin
```
