# nanobot-stack

Personal AI assistant infrastructure with vector memory, intelligent routing, tool execution, and full observability.

Deploys **nanobot** + **Qdrant** (hybrid search) + a **FastAPI RAG bridge** + **Langfuse** on Ubuntu 24.04 bare metal, behind **Traefik** + **Authentik** ForwardAuth.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Traefik  (reverse proxy · TLS · Authentik ForwardAuth)         │
│  ai.DOMAIN   rag.DOMAIN   observability.DOMAIN   chat.DOMAIN   │
└────┬──────────────┬──────────────────┬───────────────┬──────────┘
     │              │                  │               │
     ▼              ▼                  ▼               ▼
 ┌────────┐  ┌──────────────────┐  ┌──────────┐  ┌─────────┐
 │nanobot │  │  RAG bridge v7   │  │ Langfuse │  │  WebUI  │
 │gateway │◄─│  (FastAPI)       │  │ (Docker) │  │  (opt.) │
 │:18790  │  │  :8089           │  │ :3300    │  │  :18800 │
 └───┬────┘  └──┬──┬──┬────────┘  └──────────┘  └─────────┘
     │   MCP    │  │  │
     └──────────┘  │  ├──► Shell (read-only)
                   │  ├──► Web fetch
                   │  └──► Notifications (webhook)
                   ▼
            ┌──────────────┐     ┌────────────┐
            │   Qdrant     │     │  Ollama    │
            │ dense+sparse │     │  (offline  │
            │ :6333/:6334  │     │  fallback) │
            └──────────────┘     └────────────┘
```

## Quick start

```bash
git clone https://github.com/Draeyo/nanobot-stack.git
cd nanobot-stack

# 1. Configure
cp stack.env.example stack.env
nano stack.env  # set DOMAIN at minimum

# 2. Deploy
sudo ./deploy.sh

# 3. Add your API keys
sudo nano /opt/nanobot-stack/rag-bridge/.env
sudo nano /opt/nanobot-stack/nanobot/config/.env

# 4. Restart and verify
sudo systemctl restart nanobot-rag nanobot
nanobot-stack-selftest
```

## Configuration

All environment-specific values live in `stack.env` (git-ignored). The only **required** setting is `DOMAIN`.

```bash
# Minimal
DOMAIN="mydomain.com"

# Full customisation
DOMAIN="mydomain.com"
NANOBOT_SUBDOMAIN="bot"
AUTHENTIK_OUTPOST_FQDN="sso.mydomain.com"
INSTALL_NANOBOT_WEBUI="true"
RERANKER_DEVICE="cuda"
OLLAMA_BASE_URL="http://127.0.0.1:11434/v1"
NOTIFICATION_WEBHOOK_URL="https://ntfy.sh/my-nanobot"
```

At deploy time, `lib.sh` loads `stack.env`, applies defaults, and `envsubst` renders all `.template` files. Python code reads configuration from `.env` files at runtime — no hardcoded values in the codebase.

## Features

### Intelligent routing
- **Auto query classification** — Each incoming message is classified (memory lookup, code task, incident triage, translation…) and routed to the optimal model chain automatically. Heuristic fast-path for obvious cases, LLM classifier for ambiguous ones.
- **Smart chat** (`/smart-chat`) — One endpoint that classifies, retrieves context, injects user profile, and routes — the "batteries included" chat endpoint.
- **22 model profiles** with fallback chains including **Ollama local fallback** as last resort on every chain for offline resilience.
- **Hot-reloadable router** — Edit `model_router.json` without restarting.

### Active memory
- **Conversation hook** (`/conversation-hook`) — Post-conversation pipeline that automatically extracts durable facts, stores conversation summaries, and updates the user profile. No manual `remember` calls needed.
- **Context prefetch** (`/context-prefetch`) — Retrieves relevant memories + user profile and formats them for system prompt injection before answering.
- **Memory compaction** (`/compact-memories`) — Merges redundant memories on a subject into a single consolidated entry.
- **RAG feedback loop** (`/feedback`) — Positive/negative feedback on search results adjusts future ranking via persistent boost scores.

### Retrieval quality
- **Cross-encoder reranker** — BAAI/bge-reranker-v2-m3 with graceful fallback to hybrid scoring.
- **Hybrid search** — Dense (cosine) + sparse (TF-based) vectors in Qdrant, fused with RRF.
- **Paragraph-aware chunking** with metadata enrichment (title, sections, doc_date).
- **Multi-modal ingestion** — Extracts images from PDFs/DOCX, describes them via vision model, and indexes the descriptions as searchable chunks.

### Tools
- **Restricted shell** (`/shell`) — Pre-approved read-only commands (systemctl status, journalctl, openssl, curl, dig, df, uptime).
- **Web fetch** (`/web-fetch`) — Fetch and extract text from any URL.
- **Notifications** (`/notify`) — Send alerts via webhook (ntfy, Slack, Telegram, generic JSON).
- **Multi-step planner** (`/plan` + `/execute-step`) — Decomposes complex tasks into tool-call sequences and executes them.

### Personalisation
- **User profile** (`/profile`) — Auto-maintained JSON profile capturing preferences, expertise level, communication style, topics of interest. Injected into system prompts.
- **Adaptive tone** — Profile-driven response style (brief/detailed/technical) and language preference.

### Reliability
- **Circuit breakers** per LLM provider profile with configurable threshold and cooldown.
- **Rate limiting** on `/remember` and `/ingest`.
- **Readiness probe** (`/healthz`) checks Qdrant, API keys, Langfuse, and reranker.

### Observability
- **Prometheus metrics** at `/metrics`.
- **Structured JSON logging**.
- **Audit log** — Append-only JSONL.
- **Langfuse** tracing on all LLM calls.
- **Health dashboard** at `/dashboard` — Auto-refreshing single-page view of circuit breakers, cache stats, ingestion status, and system health.

### Security
- Qdrant API key, bridge bearer token, systemd hardening.
- Secret rotation script (`nanobot-rotate-secrets`).
- All services bind 127.0.0.1 — public access only via Traefik + Authentik.

### Developer experience
- **Modular repo** — 15 Python modules, standalone configs, templates.
- **`deploy.sh` / `update.sh`** — First install vs fast iteration.
- **`update.sh --code-only`** — Deploys in seconds.
- **Preflight checks** — Disk, ports, DNS, Traefik, re-install detection.
- **Template-driven** — `envsubst` renders all paths/ports/domains from `stack.env`.

## Updating

```bash
git pull
sudo ./update.sh               # full (code + deps + restart)
sudo ./update.sh --code-only   # fast (code + templates only)
sudo ./update.sh --deps-only   # pip only
```

## MCP tools (22 total)

The nanobot agent discovers these tools automatically via the MCP server:

| Tool | Description |
|------|-------------|
| `search_memory` | Search vector memory |
| `remember_memory` | Store a durable fact |
| `ask_rag` | Retrieval-grounded Q&A |
| `smart_chat` | Classify → retrieve → profile → route (all-in-one) |
| `classify_query` | Classify a query into a task type |
| `context_prefetch` | Get relevant context for prompt injection |
| `conversation_hook` | Post-conversation fact extraction + profile update |
| `compact_memories` | Merge redundant memories |
| `plan_task` | Decompose complex tasks into steps |
| `execute_step` | Execute a plan step |
| `run_shell` | Run read-only shell commands |
| `fetch_url` | Fetch web page content |
| `notify` | Send notifications via webhook |
| `give_feedback` | Positive/negative feedback on search results |
| `get_profile` | Read user profile |
| `update_profile` | Update user profile fields |
| `route_preview` | Preview model chain for a task |
| `list_model_routes` | List all configured routes |
| `rag_health` | Health check |

## API endpoints

| Endpoint | Auth | Description |
|----------|------|-------------|
| `GET /healthz` | No | Readiness probe |
| `GET /metrics` | No | Prometheus metrics |
| `GET /dashboard` | No | Health dashboard (HTML) |
| `POST /smart-chat` | Token | Intelligent auto-routed chat |
| `POST /classify` | Token | Query classification |
| `POST /context-prefetch` | Token | Memory + profile for prompt injection |
| `POST /conversation-hook` | Token | Post-conversation extraction pipeline |
| `POST /compact-memories` | Token | Memory compaction |
| `POST /plan` | Token | Multi-step task planning |
| `POST /execute-step` | Token | Execute a plan step |
| `POST /shell` | Token | Restricted shell commands |
| `POST /web-fetch` | Token | Web page fetching |
| `POST /notify` | Token | Webhook notifications |
| `POST /feedback` | Token | Search result feedback |
| `GET /profile` | Token | User profile |
| `POST /profile` | Token | Update user profile |
| `POST /search` | Token | Vector search |
| `POST /remember` | Token | Store memory |
| `POST /ask` | Token | RAG Q&A |
| `POST /chat` | Token | Direct model call |
| `POST /ingest` | Token | Background ingestion |
| `POST /ingest-sync` | Token | Synchronous ingestion |
| `GET /ingest-status` | Token | Ingestion progress |
| `GET /routes` | Token | Model routing config |
| `POST /route-preview` | Token | Preview model chain |
| `GET /circuit-breakers` | Token | Circuit breaker states |
| `GET /rate-limits` | Token | Rate limiter states |
| `GET /cache-stats` | Token | Embedding cache stats |
| `GET /feedback-stats` | Token | Feedback statistics |
| `POST /selftest` | Token | Full system diagnostic |

## File structure

```
nanobot-stack/
├── stack.env.example              # ← cp to stack.env, set DOMAIN
├── .gitignore
├── LICENSE
├── lib.sh                         # shared functions + defaults
├── deploy.sh                      # first-time installer
├── update.sh                      # fast updater
├── rotate-secrets.sh              # secret rotation
├── src/
│   ├── bridge/                    # RAG bridge (15 Python modules)
│   │   ├── app.py                 # FastAPI main app
│   │   ├── extensions.py          # v7 endpoints (smart-chat, planner, tools…)
│   │   ├── circuit_breaker.py
│   │   ├── rate_limiter.py
│   │   ├── reranker.py            # cross-encoder
│   │   ├── embedding_cache.py     # LRU cache
│   │   ├── conversation_memory.py # fact extraction, compaction, prefetch
│   │   ├── query_classifier.py    # auto task classification
│   │   ├── planner.py             # multi-step task planner
│   │   ├── tools.py               # shell, web fetch, notifications
│   │   ├── feedback.py            # relevance feedback loop
│   │   ├── vision.py              # image extraction + vision descriptions
│   │   ├── user_profile.py        # auto-maintained user profile
│   │   ├── dashboard.py           # HTML health dashboard
│   │   ├── audit.py               # audit log middleware
│   │   └── requirements.txt
│   ├── mcp/                       # MCP server (22 tools)
│   │   ├── rag_mcp_server.py
│   │   └── requirements.txt
│   └── config/
│       ├── model_router.json      # 22 profiles, 21 routes (hot-reloadable)
│       ├── NANOBOT_POLICY_PROMPT.md
│       └── langfuse-docker-compose.yml
├── systemd/                       # .template → rendered at deploy
├── traefik/                       # .template → rendered at deploy
└── scripts/                       # .template → rendered at deploy
```

## Prerequisites

- Ubuntu 24.04 (bare metal or VM)
- Traefik with ACME/TLS
- Authentik with ForwardAuth outpost
- DNS records for subdomains
- At least one LLM API key (OpenAI, Anthropic, or OpenRouter)
- Optional: Ollama for offline fallback, GPU for faster reranking

## License

MIT
