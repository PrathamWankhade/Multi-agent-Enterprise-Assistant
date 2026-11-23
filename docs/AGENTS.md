# Agent Reference

Seven specialized agents cooperate on requests. All agents inherit `BaseAgent`, which provides LLM access, memory access, and a shared message-building helper. They use **one** configured LLM — distinct behavior comes from prompts, inputs and permissions.

---

## 1. Orchestrator (`app/agents/orchestrator.py`) — *the brain*

**Responsibility:** understand the request, classify intent, pick agents, hold task state.

- Reads recent conversation history from memory.
- Produces a JSON plan: `intent`, `agents`, `reasoning`, `requires_approval`, `summary_for_user`.
- Intent mapping:
  - `research` — general facts, policy questions (`agents: ["research"]`)
  - `analysis` — compare / recommend (`agents: ["research", "analyst"]`)
  - `execution` — tickets, lookups, SQL, reports (`agents: ["research", "execution"]` when docs are relevant)
  - `itsm` — IT problem reports and ticket tracking (`agents: ["itsm"]`, forced even if the LLM emits a different list)
  - `chitchat` — greetings (`agents: []`)
- **Fast path:** obvious greetings bypass the LLM entirely (instant response).
- Falls back to a safe `research` plan if the model returns non-JSON.

---

## Classification (`app/agents/classifier.py`) — *the IT triage specialist*

**Responsibility:** turn a free-text IT problem into structured ITSM fields.

- Proposes `category`, `issue_type`, `suggested_priority`, `confidence` and `reasoning` as JSON.
- Keyword heuristic fallback covers hardware/network/access/software and criticality terms.
- Always sets `approval_required: true` — the administrator makes the final decision.
- Used by both the ITSM chat workflow and `POST /itsm/requests`.

---

## 2. Research (`app/agents/research.py`) — *the retriever*

**Responsibility:** find relevant company knowledge and answer with citations.

- Embeds the query and searches Qdrant / Azure AI Search.
- Falls back to lexical search over `data/*.md` when the vector store is unavailable.
- Returns `chunks` (text, source, score) plus a cited `answer`.
- Explicitly reports when no relevant information exists instead of inventing it.

**Output contract:** `{ agent, answer, chunks, chunk_count, sources }`

---

## 3. Analyst (`app/agents/analyst.py`) — *the reasoner*

**Responsibility:** transform retrieved facts into insight.

- Receives the research answer and source chunks.
- Compares options, calculates, identifies inconsistencies, recommends actions.
- Temperature `0.3` for focused analytical output.

**Output contract:** `{ agent, analysis }`

---

## 4. Execution (`app/agents/execution.py`) — *the actor*

**Responsibility:** use tools and APIs safely.

- Selects a tool from the registry via LLM tool-calling prompt, with a keyword heuristic fallback for offline/mock mode.
- Validates arguments against the tool's JSON schema before running anything.
- Risk gate: `read` tools run immediately; `write`/`external` tools require human approval unless `auto_approve=true`.
- Pending actions are stored in task context for the approval endpoints.
- Legacy allow-listed HTTP calls remain supported (domain allow-list + approval gate).

**Output contract:** `{ agent, tool, risk, args, status, result }`

---

## 5. Synthesis (`routes.py::_synthesize_final_response`) — *the composer*

**Responsibility:** merge agent outputs into one coherent response.

- Combines cited research answer, analysis, execution status, and orchestrator summary.
- Renders tool outcomes as readable text (ticket IDs, report paths, match counts).
- Deterministic — no extra LLM call, which keeps latency low.

---

## 6. Review (`app/agents/reviewer.py`) — *the critic*

**Responsibility:** verify the draft before the user sees it.

**Stage 1 — deterministic checks (always, instant):**
- Empty or suspiciously short answers.
- Citations pointing at sources not present in retrieved evidence.
- Missing citations when evidence was available.

**Stage 2 — LLM groundedness pass (optional, `REVIEW_LLM_ENABLED`):**
- Compares draft against evidence excerpts.
- Flags unsupported claims and number mismatches.
- Can return a `revised_answer` which replaces the draft.

**Verdict:** `pass` or `revise`, with up to 8 issues. Recorded in the trace and the message meta line (`review approved` / `review revised`).

---

## Agent collaboration rules

| Rule | Reason |
|---|---|
| Sequential by default | One model instance; avoids GPU/RAM contention on CPU machines |
| Shared task context | Agents read prior results through `MemoryStore`, not via long prompts |
| No agent calls another directly | The pipeline in `routes.py` owns delegation and ordering |
| Review always last | It validates the final draft, not intermediate output |
| Review never blocks delivery | A missing LLM verdict falls back to deterministic checks only |

---

## Adding a new agent

1. Create `app/agents/<name>.py` inheriting `BaseAgent` and implement `run(...)`.
2. Register it in the orchestrator prompt and the delegation loop in `routes.py`.
3. Add a label/icon to `AGENT_INFO` in `frontend/app.js` so the pipeline stepper renders it.
4. Add a test in `tests/test_api.py` asserting its result appears in `agent_results`.
