# Azure deployment (optional, Phase 6)

The app runs fully local with **Ollama + Qdrant Cloud + Redis Cloud**. This guide
is only for deploying to Azure — none of it is required for local use, and the
core app never hard-depends on any Azure SDK.

## What was added

| Component | Implementation | Required? |
|---|---|---|
| Azure OpenAI (chat + embeddings) | OpenAI SDK, API key **or** managed identity | Optional |
| Azure AI Search (RAG) | REST client in `app/rag/azure_search.py` (index create, upsert, vector search, list, delete) | Optional |
| Managed identity | `app/services/azure_identity.py` using optional `azure-identity` | Optional |
| Auth / RBAC / audit / tracing | Built in Phases 3–5, works in Azure too | Built in |
| Infrastructure | `azure/main.bicep` (Container Apps, AI Search, Redis, Key Vault, Log Analytics) | Optional |
| azd config | `azure.yaml` | Optional |

## 1. Local Azure-development setup

```bash
pip install -r requirements-azure.txt   # adds azure-identity
az login

# .env — API key mode
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/
AZURE_OPENAI_API_KEY=<key>
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o-mini
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-small

AZURE_AI_SEARCH_ENDPOINT=https://<search>.search.windows.net
AZURE_AI_SEARCH_API_KEY=<admin-key>
AZURE_AI_SEARCH_INDEX_NAME=enterprise-kb
```

The app detects these automatically (`use_azure_openai` / `use_azure_ai_search`).
`/api/v1/health` then reports `llm_provider: azure_openai`,
`vector_store: azure_ai_search`.

## 2. Passwordless (managed identity)

```env
AZURE_USE_MANAGED_IDENTITY=true
```

With this flag, endpoints alone are enough — API keys become optional. The app
uses `DefaultAzureCredential` to fetch AAD tokens for
`https://cognitiveservices.azure.com/.default` (OpenAI) and
`https://search.azure.com/.default` (AI Search). Assign the identity these RBAC
roles:

- **Cognitive Services OpenAI User** on the OpenAI resource
- **Search Index Data Contributor** on the AI Search service

If `azure-identity` is not installed or no identity exists in the environment,
the app logs a warning and falls back to keys or mock mode — it never crashes.

## 3. One-command deploy with azd

```bash
azd login
azd up
```

`azure/main.bicep` provisions:

- Azure Container Apps environment + app (with `/api/v1/health` liveness probe)
- Azure AI Search (basic, vector-capable)
- Azure Redis Cache
- Azure Key Vault with RBAC, plus a role assignment for the app identity
- Log Analytics workspace for container logs

After deployment, set the OpenAI endpoint/deployment parameters and push a
container image (e.g. to ACR) via `azd deploy`. The bicep template accepts
`containerImage`, `azureOpenAIEndpoint`, `appApiKey` and other parameters — see
the `param` declarations at the top.

## 4. Azure Functions for event-driven execution (optional)

The Execution Agent already supports **allow-listed HTTP calls** to external
endpoints. To run an action as an Azure Function instead:

1. Create an HTTP-trigger Function that validates the `X-API-Key` header.
2. Add its hostname to `ALLOWED_EXECUTION_DOMAINS` in the app's environment.
3. Ask the assistant to call it, or POST directly to `/api/v1/chat` with an
   explicit `url`/`method` in the request.

The same approval gate applies: write-style actions (`POST`/`PUT`/`DELETE`)
pause for `/api/v1/tasks/{id}/approve` unless `auto_approve=true`.

## 5. Monitoring in Azure

- Container logs (including structured audit JSON lines) go to Log Analytics.
- `GET /api/v1/audit` and `GET /api/v1/tasks/{id}/trace` work unchanged.
- Application Insights can be wired by adding its connection string to the
  Container App env in `azure/main.bicep`.

## Design guarantees

- **No hard Azure dependency** — `openai` handles Azure OpenAI; AI Search uses
  REST; `azure-identity` is optional.
- **Graceful degradation** — unreachable Azure services fall back to keyword
  retrieval and mock responses, exactly like local mode.
- **Same security model** — API-key RBAC, approval gates, audit trail and
  tracing are identical in Azure and local deployments.
