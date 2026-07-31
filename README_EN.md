# Supply Chain QA

[中文](README.md) | English

Supply Chain QA is an **RAG + Multi-Agent intelligent Q&A system** for manufacturing supply chain scenarios. It is not a simple document Q&A demo, but a runnable, readable, and extensible enterprise-grade AI application: unified orchestration of intent routing, multi-way hybrid retrieval, agent tool calling, knowledge graph, row-level permissions, concurrency safety, conversation memory, and full-chain observability.

The core of this project is **dual-pathway routing**: employees ask questions in natural language, and the system routes automatically — knowledge questions go through the RAG pathway to retrieve and generate answers from the knowledge base, while business questions go through the Agent pathway to call tools for real-time data (inventory / orders / suppliers / tickets) — no need to switch between multiple backend systems. It is suitable both for learning the complete engineering practice of enterprise RAG systems and as a foundation for building vertical-domain intelligent Q&A systems.

**Tech Stack**: FastAPI + LangGraph + LangChain + Milvus + Neo4j + Redis + PostgreSQL + Vue3 | llama.cpp (local deployment)

## Highlights

- **Three-tier cascading intent routing**: rule matching <1ms (entity-code-first rules + command keywords, zero tokens) → semantic routing <10ms (embedding cosine similarity with a top1-top2 margin check, zero tokens) → LLM classification ~2.5s as fallback. The vast majority of requests consume no LLM calls. Routing keywords and semantic utterances are externalized to `intent_routes.json` with hot reload — adding a tool only requires a config change, not code.

- **Multi-way hybrid retrieval + adaptive RRF fusion**: Milvus vector + BM25 keyword two-way recall fused via RRF, adaptively weighted by query type (exact code queries → BM25 ×1.5, semantic questions → vector ×1.5); Neo4j graph entity hits are binary signals overlaid onto RRF scores with α/β weighting (final = α·RRF + β·graph), followed by four-layer post-processing and BGE-Reranker re-ranking.

- **Agent tool calling**: LangChain `bind_tools` + LangGraph `StateGraph`, 6 business tools, agent↔tools convergence within 5 rounds, with a built-in ReAct infinite-loop circuit breaker.

- **Concurrency-safe write operations**: Redis three-state idempotency (atomic SET NX) + token-based distributed locks (atomic Lua release) + frontend approval confirmation, preventing duplicate ticket creation.

- **Row-level RBAC**: 7 department roles, row-level data isolation via the Milvus ARRAY column `security_group` + `array_contains` filtering — no separate permission tables.

- **Graph RAG knowledge graph**: Neo4j stores supplier→material→order→warehouse entity relations with templated Cypher generation (LLM never writes Cypher directly), supporting multi-hop relational queries.

- **Three-layer cache system**: L1 in-process MD5 exact match (0ms) → L2 Redis semantic cache (embedding similarity >0.92 reuse) → L3 data query result cache (read-through for Text-to-SQL / read-only tools), saving 90%+ of API costs. On knowledge-base changes, the semantic layer is actively invalidated via an O(1) version-counter INCR (epoch invalidation), avoiding stale cache entries and full SCAN sweeps.

- **Faithfulness guardrails and conflict detection**: post-generation verification that answers are faithful to the retrieved context; proactive conflict alerts when documents from different departments define the same metric differently.

- **Streaming SSE + conversation memory**: token-level streaming output with 7 SSE event types; Redis sliding-window memory + background LLM summarization. When Redis fails, the system degrades gracefully without interrupting conversations.

- **Full-chain observability with Langfuse**: span-level tracing of routing decisions, RAG retrieval, tool calls, and LLM generation.

## Architecture

Core execution chain:

```Plain Text
User question (Vue3 frontend)
  -> POST /api/v1/chat/ask (SSE streaming)
  -> Rate-limit middleware (Redis sliding window, degrades to in-memory)
  -> Three-tier intent routing (rules -> semantic -> LLM fallback)
  -> RAG pathway:
       Query understanding -> two-way recall (vector + BM25) -> adaptive RRF fusion
       -> graph entity α/β weighted overlay -> four-layer post-processing -> reranker
       -> Self-RAG relevance filtering (query rewrite & re-retrieval on medium confidence)
       -> LLM streaming generation -> faithfulness guardrails / conflict detection / query-cache writeback
  -> Agent pathway:
       Clarification check -> RBAC permission -> write-operation approval
       -> Redis idempotency claim + distributed lock
       -> LangGraph ReAct (agent <-> tools, up to 5 rounds)
  -> SSE event stream (content / sources / tool_call / dag_progress ...)
  -> Conversation memory written to Redis (pipelined, background summarization)
```

## Directory Structure

```Plain Text
supply-chain-qa/
├── backend/
│   ├── app/
│   │   ├── agents/            # Agent orchestration (ToolAgent / LangGraphAgent / intent router)
│   │   ├── api/               # FastAPI routes (chat / knowledge / tool / feedback / evaluate / auth)
│   │   │   └── handlers/      # Intent handlers (rag_answer / tool_call / graph_query)
│   │   ├── core/              # Core engines (rag_engine / milvus / redis / neo4j / rate_limiter)
│   │   └── models/            # SQLAlchemy data models
│   ├── tests/                 # Unit tests + integration tests (@pytest.mark.integration)
│   ├── scripts/               # Knowledge ingestion, benchmark, data init scripts
│   ├── eval/                  # RAG retrieval quality evaluation
│   └── requirements.txt
├── frontend/                  # Vue3 + Element Plus + Pinia (JavaScript, not TS)
│   └── src/                   # views / stores / api / router
├── knowledge/                 # Knowledge base docs (7 departments, 90+ policies & business data)
├── models/                    # Local GGUF models (Qwen3-14B)
├── llama.cpp-cuda13/          # llama.cpp CUDA 13 runtime (llama-server.exe)
├── docs/                      # Design, verification, onboarding docs
├── docker-compose.yml         # Infrastructure container orchestration
├── start.ps1 / start-dev.ps1  # One-click startup scripts
└── AGENTS.md                  # AI coding rules (architecture constraints & pitfalls)
```

## Quick Start

### Prerequisites

Recommended environment:

- Python 3.11

- Node 18+

- Docker Desktop (Milvus / Redis / PostgreSQL / Neo4j)

- An LLM endpoint: local llama.cpp (OpenAI-compatible, port 18080, project default), DeepSeek API, or Ollama

### Configure .env

Copy the template and fill in real values:

```Plain Text
cd backend && cp ../.env.example .env
```

Minimal working configuration (local llama.cpp, project default):

```Plain Text
LLM_PROVIDER=local
LOCAL_LLM_BASE_URL=http://localhost:18080/v1
LOCAL_LLM_MODEL=Qwen3-14B
JWT_SECRET=change-me-in-production
```

Start the local model server with `backend\scripts\start-llama-server.bat` (loads `models\Qwen3-14B-Q4_K_M.gguf` on CUDA, port 18080).

Using a cloud API (DeepSeek example):

```Plain Text
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-your-api-key
```

Ollama is also supported (`LLM_PROVIDER=ollama`, default `http://localhost:11434`).

See [.env.example](.env.example) for the full list of options (RAG parameters, feature flags, Langfuse, etc.). Tuning parameters treat `backend/app/config.py` as the single source of truth.

### One-Click Startup

```Plain Text
.\start.ps1
```

Automatically starts: Docker infrastructure → backend → frontend. Use `.\start-dev.ps1` for development mode.

### Manual Startup

```Plain Text
# 1. Infrastructure
docker-compose up -d etcd minio milvus redis postgres neo4j

# 2. Backend
cd backend
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8001

# 3. Frontend
cd frontend && npm install && npm run dev
# -> http://localhost:5173 (proxied to backend on 8001)
```

### Knowledge Ingestion

```Plain Text
cd backend && python scripts/upload_knowledge.py
```

Chunks, vectorizes, and writes documents under `knowledge/` into Milvus (automatically tagged with `security_group` permissions). For batch PDF ingestion use `scripts/ingest_pdfs.py`.

### Default Accounts

> Controlled by `DEMO_SEED_USERS=true`, for demo environments only.

| Account   | Password     | Role          | Visibility                      |
| --------- | ------------ | ------------- | ------------------------------- |
| admin     | admin123     | Administrator | All                             |
| purchase  | purchase123  | Purchasing    | Purchasing / Suppliers / Public |
| warehouse | warehouse123 | Warehouse     | Inventory / Logistics / Public  |

More roles (quality / production / finance / logistics) can be created via the registration page or API.

## How the Dual Pathways Work

The core feature of Supply Chain QA is **automatic routing between RAG retrieval and Agent tool calling**.

### RAG Pathway: Knowledge Questions

```Plain Text
"What is the purchase order approval process?"
  -> Intent routed to KNOWLEDGE
  -> Two-way recall: Milvus vectors + BM25
  -> Adaptive RRF fusion (codes/IDs -> boost BM25; "how"/"what" -> boost vectors)
  -> Neo4j graph entity hits overlaid with α/β weighting and re-ranked
  -> Four-layer post-processing: low-score filter -> Jaccard dedup
     -> conflict detection -> faithfulness guardrails
  -> BGE-Reranker re-ranking -> Self-RAG relevance filter (threshold 0.15,
     falls back to top-1 on zero hits)
  -> Query rewrite & re-retrieval on medium confidence (0.3~0.6)
  -> LLM streaming generation with source citations
```

### Agent Pathway: Business Operations

```Plain Text
"Create an urgent ticket for MAT-001 material shortage"
  -> Intent routed to TOOL_CALL
  -> Clarification check (asks back proactively when parameters are missing)
  -> RBAC tool permission check
  -> Write-operation approval (executes after frontend confirmation)
  -> Redis three-state idempotency claim (acquired / pending / completed)
  -> Distributed lock (token + atomic Lua release)
  -> LangGraph ReAct executes create_ticket
  -> Ticket ID: TK-{timestamp}{7-digit random}
```

Available tools (11 in TOOL_REGISTRY): `query_inventory` / `query_order` / `query_supplier` / `track_logistics` / `create_ticket` / `calculate_reorder_point` / `get_knowledge` / `get_datetime` / `web_search` / `calculator` / `code_interpreter`

Core files:

```Plain Text
backend/app/agents/router.py           # Three-tier intent routing (entity-first rules -> semantic -> LLM)
backend/app/data/intent_routes.json    # Routing keywords / entity rules / semantic utterances (hot reload)
backend/app/core/rag_engine.py         # RRF fusion + four-layer post-processing
backend/app/api/handlers/tool_call.py  # Idempotency + lock + approval chain
backend/app/agents/tool.py             # LangGraph ToolAgent + loop breaker
backend/app/core/redis_client.py       # Connection mgmt / memory / locks / idempotency
```

### Defensive Engineering (SuperPowers)

- **ReAct loop circuit breaker**: monitors agent reasoning path signatures in real time, intercepts tool-call infinite loops and force-injects introspection instructions, guaranteeing 100% convergence.

- **Fuzzy-input self-healing normalization**: automatically corrects user typos (O/0 confusion, missing hyphens); both Graph RAG and the retrieval pathways support fuzzy matching.

- **Redis failure degradation**: when the connection drops, conversation memory / cache / rate limiting degrade automatically (the main chain is never interrupted); auto-reconnects within 10 seconds of recovery.

## Common Commands

### Startup and Testing

| Command                                                                         | Purpose                                                    |
| ------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| `.\start.ps1`                                                                   | One-click start of all services                            |
| `docker-compose up -d`                                                          | Start infrastructure containers                            |
| `cd backend && uvicorn app.main:app --reload --port 8001`                       | Backend dev server                                         |
| `cd frontend && npm run dev`                                                    | Frontend dev server (5173)                                 |
| `cd backend && venv\Scripts\python.exe -m pytest tests -q -k "not integration"` | Backend unit tests (no Docker needed)                      |
| `cd backend && venv\Scripts\python.exe -m pytest tests -q`                      | All tests (Docker services required)                       |
| `cd frontend && npm run test:unit`                                              | Frontend unit tests                                        |
| `python scripts/run_benchmark.py --mode both`                                   | Agent tool-calling benchmark (performance / quality modes) |

### Test Status

1069 unit test cases, all passing (plus 2 skipped). The coverage gate (70%) is enforced in CI only; local runs carry no coverage flags by default. Make sure `JWT_SECRET` is configured in `.env` before running; set `REQUIRE_AUTH_CHAT=false` for local anonymous demo.

## API

| Endpoint                 | Method | Description                                                   |
| ------------------------ | ------ | ------------------------------------------------------------- |
| /api/v1/chat/ask         | POST   | Chat (SSE streaming, 7 event types)                           |
| /api/v1/tools/call       | POST   | Agent tool calling                                            |
| /api/v1/tools/list       | GET    | Tool list                                                     |
| /api/v1/knowledge/upload | POST   | Upload document (auto chunk & ingest)                         |
| /api/v1/knowledge/list   | GET    | Document list                                                 |
| /api/v1/auth/login       | POST   | Login (JWT)                                                   |
| /health                  | GET    | Full-chain health check (Milvus / Redis / PostgreSQL / Neo4j) |

Interactive docs: http://localhost:8001/docs after startup.

## Division of Labor: RAG vs Agent

| Type          | Handles                                                        | Data Source                                                                                                                     |
| ------------- | -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| RAG pathway   | Policies, processes, and standards Q&A                         | knowledge/ document base (Milvus + BM25 + Neo4j)                                                                                |
| Agent pathway | Real-time inventory/order/supplier queries and ticket creation | PostgreSQL business database (tool calls)                                                                                       |
| Text-to-SQL   | Natural-language statistical analysis                          | PostgreSQL (six-layer safety: SELECT-only / table allowlist / keyword denylist / parameterized execution / row limit / timeout) |

## Running with Docker

Infrastructure orchestration (development):

```Plain Text
docker-compose up -d
```

Included services: Milvus (etcd + minio), Redis, PostgreSQL, Neo4j, Langfuse.

Both frontend and backend ship Dockerfiles (`backend/Dockerfile`, `frontend/Dockerfile`) for containerized builds.

## Important Data Paths

| Data                  | Path                                                                   |
| --------------------- | ---------------------------------------------------------------------- |
| Knowledge source docs | knowledge/                                                             |
| Uploaded documents    | backend/uploads/                                                       |
| Vector data           | Milvus collection `supply_chain_qa_docs` (with security_group column)  |
| Conversation memory   | Redis `scqa:chat:*` / `scqa:chat_summary:*` (sliding window + summary) |
| Idempotency & locks   | Redis `idempotent:tool:*` / `lock:tool:*`                              |
| Business data         | PostgreSQL (port 15432)                                                |
| Entity graph          | Neo4j (bolt port 17687)                                                |
| Local models          | models/*.gguf (loaded by llama.cpp)                                    |

## Key Documents

| Document                                                 | Purpose                                                               |
| -------------------------------------------------------- | --------------------------------------------------------------------- |
| [AGENTS.md](AGENTS.md)                                   | AI coding rules: architecture constraints, pitfalls, delivery process |
| [docs/DESIGN.md](docs/DESIGN.md)                         | System design document                                                |
| [docs/ONBOARDING.md](docs/ONBOARDING.md)                 | Onboarding guide for new members                                      |
| [docs/VERIFICATION_GUIDE.md](docs/VERIFICATION_GUIDE.md) | Feature verification manual                                           |
| [docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md)       | Project background and context                                        |

## Who Is This For

Supply Chain QA is suitable for:

- Learning the complete engineering chain of enterprise RAG systems: hybrid retrieval, RRF fusion, re-ranking, post-processing, guardrails.

- Learning LangGraph agent orchestration for tool calling, approval flows, and concurrency-safe design (idempotency + distributed locks).

- Learning how Milvus row-level permissions (RBAC), Graph RAG, and Text-to-SQL land in practice.

- Serving as a second-development foundation for vertical-domain (manufacturing, logistics, finance) intelligent Q&A systems.

- Practicing fully local deployment: llama.cpp + GGUF models + self-hosted infrastructure, with data never leaving the intranet.

If you remember only one thing: Supply Chain QA is a **dual-pathway supply chain Q&A system** that unifies knowledge retrieval and business operations behind a single natural-language entry point.
