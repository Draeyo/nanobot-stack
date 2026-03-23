<p align="center">
  <strong>nanobot-stack</strong><br>
  <em>Self-hosted AI assistant with persistent memory, smart model routing, and tool execution</em>
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> •
  <a href="#what-is-this">What is this?</a> •
  <a href="#features">Features</a> •
  <a href="#prerequisites">Prerequisites</a> •
  <a href="#installation">Installation</a> •
  <a href="#configuration">Configuration</a> •
  <a href="#authentik-setup">Authentik Setup</a> •
  <a href="#admin-ui">Admin UI</a> •
  <a href="#usage">Usage</a> •
  <a href="#faq">FAQ</a> •
  <a href="#contributing">Contributing</a>
</p>

---

## What is this?

**nanobot-stack** is a one-command deployment script that sets up a complete, self-hosted AI assistant on your own server. Think of it as your private AI that:

- **Remembers everything** — It stores facts, decisions, and conversations in a vector database and recalls them automatically when relevant.
- **Routes to the best model** — It picks the cheapest model that can handle each task (a quick rewrite doesn't need GPT-4) and falls back to alternatives if one provider is down. An adaptive router learns from feedback to improve routing over time.
- **Uses tools** — It can run shell commands, fetch web pages, send notifications, execute code in a sandbox, and run multi-step plans with parallel execution.
- **Understands context deeply** — HyDE query rewriting, knowledge graph relationships, sentiment detection, inline citations, and self-critique produce higher-quality answers.
- **Works offline** — An optional local model (Ollama) keeps things running when your internet connection drops.
- **Stays private** — Everything runs on your hardware. PII is automatically detected and redacted. Your conversations and data never leave your server.

It bundles together several open-source projects into a cohesive stack:

| Component | Role |
|-----------|------|
| [nanobot](https://github.com/nanobot-ai/nanobot) | AI agent gateway (the "brain") |
| [Qdrant](https://qdrant.tech/) | Vector database (the "memory") |
| **RAG bridge** (included) | FastAPI service that connects everything — search, embeddings, model routing, tools |
| [Langfuse](https://langfuse.com/) | Observability and tracing (the "dashboard") |
| [Ollama](https://ollama.com/) | Local model runner for offline fallback (optional) |
| [Traefik](https://traefik.io/) | Reverse proxy with automatic TLS (you bring this) |
| [Authentik](https://goauthentik.io/) | SSO and authentication (you bring this) |

## Architecture

```
Internet
    │
    ▼
┌─────────────────────────────────────────────────┐
│  Traefik (HTTPS, TLS certificates, auth)        │
│  ai.yourdomain.com   rag.yourdomain.com   ...   │
└──────┬─────────────┬──────────────┬─────────────┘
       │             │              │
       ▼             ▼              ▼
  ┌─────────┐  ┌───────────┐  ┌──────────┐
  │ nanobot │  │ RAG bridge│  │ Langfuse │
  │ (agent) │◄─│ (FastAPI) │  │ (traces) │
  └────┬────┘  └─┬───┬───┬─┘  └──────────┘
       │  MCP    │   │   │
       └─────────┘   │   ├──► Restricted shell
                     │   ├──► Code interpreter (sandbox)
                     │   ├──► Web fetcher
                     │   ├──► Webhook notifications
                     │   └──► Plugin system
                     ▼
   ┌──────────┐ ┌────────────┐ ┌─────────┐
   │Knowledge │ │  Qdrant    │ │ Ollama  │
   │  Graph   │ │  (vector   │ │ (local  │
   │ (SQLite) │ │  database) │ │ models) │
   └──────────┘ └────────────┘ └─────────┘
```

**How a question flows through the stack:**

1. You ask a question via the web UI or API.
2. **Working memory** tracks your session context (recent queries, topics, retrieved chunks).
3. **Sentiment detection** identifies tone and urgency to adapt the response style.
4. The **RAG bridge** classifies your question (memory lookup? coding task? incident?).
5. **HyDE rewriting** generates a hypothetical answer passage for better vector retrieval.
6. Long conversations are **compressed** (summarized) to fit within context limits.
7. It searches **Qdrant** for relevant memories/documents and **deduplicates** by embedding similarity.
8. **Knowledge graph** lookups add entity relationships to the context.
9. It injects context + your **user profile** + **inline citation instructions** into the prompt.
10. The **adaptive router** picks the best model, learning from feedback over time.
11. If all cloud providers are down, it falls back to **Ollama** running locally.
12. A **self-critique** pass reviews the answer for accuracy before delivery.
13. The answer streams back in real time via **SSE**, with progress events for each step.

## Features

<details>
<summary><strong>Persistent memory</strong> — Your AI remembers across conversations</summary>

- Automatically extracts facts, decisions, and preferences from conversations
- Stores them as searchable vectors in Qdrant
- Retrieves relevant context before answering — no need to repeat yourself
- Consolidates redundant memories over time (compaction)
- Separate collections for documents, personal memories, runbooks, projects, and conversation summaries
</details>

<details>
<summary><strong>Smart model routing</strong> — Right model for the right job</summary>

- 22 model profiles across OpenAI, Anthropic, OpenRouter, and Ollama
- Automatic task classification: a quick rewrite uses gpt-4.1-mini ($), while an architecture decision uses Claude Sonnet ($$)
- **Adaptive routing**: learns from feedback to prefer higher-quality models per task type
- Fallback chains with circuit breakers: if OpenAI is down, it tries Anthropic, then OpenRouter, then local Ollama
- Hot-reloadable config: edit `model_router.json` without restarting
- Gracefully skips providers whose API keys aren't configured
</details>

<details>
<summary><strong>Tool execution</strong> — Your AI can do things, not just talk</summary>

- **Restricted shell**: pre-approved read-only commands (systemctl status, journalctl, curl, dig, df…)
- **Web fetcher**: fetch and extract text from any URL
- **Code interpreter**: sandboxed Python execution for calculations, data processing, string manipulation
- **Notifications**: send alerts via webhook (ntfy.sh, Slack, Telegram)
- **Multi-step planner**: decomposes complex tasks into steps with parallel execution of independent steps
- **Plugin system**: drop Python files into a plugins directory — hot-reloaded with `@tool` and `@hook` decorators
</details>

<details>
<summary><strong>Advanced RAG pipeline</strong> — Beyond basic search</summary>

- **HyDE query rewriting**: generates a hypothetical answer passage and uses its embedding for more precise retrieval
- **Semantic chunking**: embedding-based boundary detection for smarter document splitting
- **Knowledge graph**: SQLite-backed entity/relationship graph (people, projects, technologies, decisions)
- **Inline citations**: automatic [1], [2] references with a source list at the end
- **Self-critique**: post-generation review pass that catches errors and improves answer quality
- **Sentiment detection**: adapts response tone based on user urgency and emotion
- **Memory decay**: time and access-based scoring so recent/frequent memories rank higher
- **Working memory**: per-session context tracking (query history, seen chunks, active topics)
- **Context compression**: long conversations are summarized to fit within model context limits
</details>

<details>
<summary><strong>Offline fallback</strong> — Works without internet</summary>

- Ollama with CPU-friendly models (qwen2.5:7b for chat, nomic-embed-text for embeddings)
- No GPU required (~5GB RAM)
- Automatically activated by the circuit breaker when cloud providers are unreachable
</details>

<details>
<summary><strong>Observability</strong> — Know what's happening</summary>

- Langfuse tracing on all LLM calls (cost, latency, token usage)
- Prometheus metrics at `/metrics`
- **Admin UI** at `/admin` — unified management console with 10 sections (analytics, settings, tools, vector DB, logs, chat, channels, shell, config, advanced)
- Real-time HTML dashboard at `/dashboard`
- Structured JSON logging for SIEM integration
- Append-only audit log (who called what, when)
</details>

<details>
<summary><strong>Security</strong> — Production-grade hardening</summary>

- All services bind to localhost (127.0.0.1) — public access only through Traefik
- Authentication via Authentik ForwardAuth (SSO) + per-request bridge token
- **DM pairing gate**: channel users (Telegram, Discord, WhatsApp) must be approved by the admin before interacting
- **PII auto-detection**: emails, phones, SSNs, API keys, credit cards scanned and redacted before storage
- **Approval-gated shell**: system-modifying commands require explicit user approval before execution
- **Approval-gated config**: configuration changes are validated, staged, diffed, and reviewed before applying
- Qdrant API key protection
- systemd hardening (NoNewPrivileges, ProtectSystem=strict…)
- Secret rotation script with orderly restarts
- No secrets in the Git repository
</details>

<details>
<summary><strong>Transparency & export</strong> — Understand and archive</summary>

- **Pipeline explain mode**: see exactly how your answer was produced (classification, routing, retrieval scores, timing)
- **Conversation export**: save chats as Markdown or structured JSON
- **SSE streaming**: real-time progress events for every pipeline phase
</details>

## Prerequisites

You'll need a Linux server (physical or virtual) with:

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| **OS** | Ubuntu 24.04 LTS | Ubuntu 24.04 LTS |
| **RAM** | 4 GB (without Ollama) | 8+ GB (with Ollama) |
| **Disk** | 10 GB free on `/opt` | 30+ GB |
| **CPU** | 2 cores | 4+ cores |
| **Network** | Public IP with open ports 80/443 | Same |

You also need:

- **A domain name** with DNS pointing to your server (e.g. `yourdomain.com`)
- **Traefik** already running as a reverse proxy with automatic TLS certificates. If you don't have Traefik set up yet, see [the Traefik quick start guide](https://doc.traefik.io/traefik/getting-started/quick-start/).
- **Authentik** running as your SSO provider with a [ForwardAuth outpost](https://docs.goauthentik.io/docs/providers/proxy/forward_auth) configured. If you don't need authentication, you can remove the `authentik-forwardauth` middleware from the Traefik config after deployment.
- **At least one LLM API key** from:
  - [OpenAI](https://platform.openai.com/api-keys) (for GPT-4.1, embeddings)
  - [Anthropic](https://console.anthropic.com/) (for Claude)
  - [OpenRouter](https://openrouter.ai/) (aggregator, accesses both)

> **Don't have all of these?** That's fine — the stack gracefully skips any provider whose API key is empty. You can start with just one.

## Quick start

```bash
# 1. Clone the repository
git clone https://github.com/Draeyo/nanobot-stack.git
cd nanobot-stack

# 2. Create your configuration file
cp stack.env.example stack.env
nano stack.env
# At minimum, set: DOMAIN="yourdomain.com"

# 3. Deploy (takes 5-10 minutes on first run)
sudo ./deploy.sh

# 4. Add your LLM API keys
sudo nano /opt/nanobot-stack/rag-bridge/.env
# Set at least one of: OPENAI_API_KEY, ANTHROPIC_API_KEY, or OPENROUTER_API_KEY

sudo nano /opt/nanobot-stack/nanobot/config/.env
# Same keys here

# 5. Restart and test
sudo systemctl restart nanobot-rag nanobot
nanobot-stack-selftest
```

If everything is green, your assistant is live at `https://ai.yourdomain.com`.

## Installation

### Step-by-step walkthrough

<details>
<summary><strong>1. Prepare your server</strong></summary>

SSH into your server and make sure it's up to date:

```bash
sudo apt update && sudo apt upgrade -y
```

Verify you have enough disk space:

```bash
df -h /opt
# You need at least 10 GB free (30 GB recommended with Ollama)
```
</details>

<details>
<summary><strong>2. Set up DNS records</strong></summary>

Create A records pointing to your server's IP address for:

| Subdomain | Example |
|-----------|---------|
| `ai` | ai.yourdomain.com |
| `rag` | rag.yourdomain.com |
| `observability` | observability.yourdomain.com |
| `chat` | chat.yourdomain.com (optional, for web UI) |

You can verify with:

```bash
dig +short ai.yourdomain.com
# Should return your server's IP
```
</details>

<details>
<summary><strong>3. Configure and deploy</strong></summary>

```bash
git clone https://github.com/Draeyo/nanobot-stack.git
cd nanobot-stack

# Create your config
cp stack.env.example stack.env
nano stack.env
```

The only required setting is `DOMAIN`. See [Configuration](#configuration) for all options.

```bash
# Deploy (installs everything: packages, Qdrant, Python venvs, Docker, Ollama…)
sudo ./deploy.sh
```

The script will:
- Install system packages (Python, PostgreSQL, Docker, etc.)
- Create a dedicated `nanobot` system user
- Download and install Qdrant (vector database)
- Set up Python virtual environments
- Generate secure random passwords and tokens
- Install Ollama and pull CPU-optimised models
- Configure systemd services and Traefik routing
- Start everything
</details>

<details>
<summary><strong>4. Add your API keys</strong></summary>

The deploy script generates `.env` files with empty API key placeholders. Fill them in:

```bash
# RAG bridge (handles embeddings and model routing)
sudo nano /opt/nanobot-stack/rag-bridge/.env
# Set: OPENAI_API_KEY=sk-...
# And/or: ANTHROPIC_API_KEY=sk-ant-...
# And/or: OPENROUTER_API_KEY=sk-or-...

# Nanobot agent (the main chat model)
sudo nano /opt/nanobot-stack/nanobot/config/.env
# Same keys here
```

Then restart:

```bash
sudo systemctl restart nanobot-rag nanobot
```
</details>

<details>
<summary><strong>5. Verify the installation</strong></summary>

```bash
# Run the built-in self-test
nanobot-stack-selftest

# Check the health dashboard
curl -s http://127.0.0.1:8089/healthz | jq .

# Or open in your browser:
# https://rag.yourdomain.com/admin              (full admin console)
# https://rag.yourdomain.com/dashboard?token=TOKEN  (legacy analytics)
```

You can find your bridge token in:

```bash
cat /opt/nanobot-stack/rag-bridge/.bridge_token
```
</details>

## Configuration

All configuration lives in `stack.env` (git-ignored — your settings never end up in the repo).

### Minimal configuration

```bash
DOMAIN="yourdomain.com"
```

### Full configuration example

```bash
DOMAIN="yourdomain.com"
NANOBOT_SUBDOMAIN="bot"                    # bot.yourdomain.com instead of ai.
AUTHENTIK_OUTPOST_FQDN="sso.yourdomain.com"
INSTALL_NANOBOT_WEBUI="true"               # enable the chat web UI
INSTALL_OLLAMA="true"                      # install Ollama for offline mode
RERANKER_DEVICE="cuda"                     # GPU-accelerated reranking (if available)
NOTIFICATION_WEBHOOK_URL="https://ntfy.sh/my-alerts"
LANGFUSE_INIT_ORG_NAME="My Company"
```

See [`stack.env.example`](stack.env.example) for every available option with documentation.

### What goes where

| File | In Git? | Purpose |
|------|---------|---------|
| `stack.env.example` | ✅ | Documented template — copy this |
| `stack.env` | ❌ | Your settings (domain, subdomains, tuning) |
| `src/config/model_router.json` | ✅ | Default model routing (preserved on update) |
| `src/bridge/*.py` | ✅ | RAG bridge source code |
| `/opt/nanobot-stack/rag-bridge/.env` | ❌ (on server) | API keys and secrets |
| `/opt/nanobot-stack/nanobot/config/.env` | ❌ (on server) | Agent API keys |

## Authentik setup

nanobot-stack uses [Authentik](https://goauthentik.io/) as its SSO provider via Traefik's ForwardAuth middleware. Every HTTP request to your public subdomains is verified by Authentik before reaching the backend services. This section walks you through the full integration.

### Prerequisites

You need a running Authentik instance accessible at a public FQDN (e.g. `auth.yourdomain.com`). If you don't have one yet, follow the [Authentik install docs](https://docs.goauthentik.io/docs/install-config/).

### Step 1 — Create a Proxy Provider in Authentik

1. Log into your Authentik admin interface (`https://auth.yourdomain.com/if/admin/`).
2. Navigate to **Applications → Providers → Create**.
3. Select **Proxy Provider** and configure:

| Field | Value |
|-------|-------|
| Name | `nanobot-stack` |
| Authorization flow | Pick your default or create one |
| Type | **Forward auth (single application)** |
| External host | `https://rag.yourdomain.com` |

4. Click **Save**.

> **Note**: If you want a single provider protecting all subdomains (ai, rag, observability, chat), use **Forward auth (domain level)** instead and set the cookie domain to `.yourdomain.com`.

### Step 2 — Create an Application

1. Navigate to **Applications → Applications → Create**.
2. Configure:

| Field | Value |
|-------|-------|
| Name | `nanobot-stack` |
| Slug | `nanobot-stack` |
| Provider | Select the **nanobot-stack** proxy provider from step 1 |

3. Click **Save**.

### Step 3 — Create (or reuse) an Outpost

1. Navigate to **Applications → Outposts**.
2. If you already have a running **Proxy outpost** (embedded or Docker), add the `nanobot-stack` application to it.
3. If you don't have one:
   - Click **Create**, select **Proxy** type.
   - Name it (e.g. `traefik-outpost`).
   - Add the `nanobot-stack` application.
   - Choose the integration (Docker or embedded).
4. Note the outpost's FQDN — this is usually your Authentik server's domain (e.g. `auth.yourdomain.com`).

### Step 4 — Configure nanobot-stack

Set the outpost FQDN in your `stack.env`:

```bash
# stack.env
AUTHENTIK_OUTPOST_FQDN="auth.yourdomain.com"
```

Then deploy (or redeploy) so Traefik picks up the rendered config:

```bash
sudo ./deploy.sh
# or, if already deployed:
sudo ./update.sh
```

This renders `traefik/authentik-forwardauth.yaml.template` into `/etc/traefik/dynamic/authentik-forwardauth.yaml`, which defines the `authentik-forwardauth` middleware pointing at your outpost.

### How it works

```
Browser request
    │
    ▼
┌──────────┐     auth subrequest      ┌────────────┐
│  Traefik │ ──────────────────────►  │  Authentik  │
│          │ ◄──────────────────────  │  Outpost    │
│          │   200 OK + user headers  └────────────┘
│          │   (or 302 redirect to login)
└────┬─────┘
     │  X-authentik-username
     │  X-authentik-email
     │  X-authentik-groups
     │  ...
     ▼
┌──────────────┐
│  RAG bridge  │  (also checks X-Bridge-Token)
│  / nanobot   │
└──────────────┘
```

For every incoming request, Traefik sends a subrequest to Authentik. If the user has a valid session, Authentik returns `200 OK` with identity headers (`X-authentik-username`, `X-authentik-email`, `X-authentik-groups`, etc.) which are forwarded to the backend. If not, Authentik returns a `302` redirect to the login page.

### Protected routes

| Subdomain | Route | Protected? |
|-----------|-------|------------|
| `ai.yourdomain.com` | nanobot agent | Yes |
| `rag.yourdomain.com` | RAG bridge (all endpoints including `/admin`) | Yes |
| `observability.yourdomain.com` | Langfuse tracing dashboard | Yes |
| `chat.yourdomain.com` | Web UI (optional) | Yes |
| `rag.yourdomain.com/webhooks/whatsapp` | WhatsApp callback | No (webhooks can't do interactive login) |

### Disabling authentication

For local-only or development setups where you don't want SSO:

1. Remove the middleware reference from `traefik/nanobot-stack.yaml.template`:
   ```yaml
   # Remove or comment out this line from each router:
   middlewares:
     - authentik-forwardauth
   ```

2. Delete the middleware file:
   ```bash
   sudo rm /etc/traefik/dynamic/authentik-forwardauth.yaml
   ```

3. Reload Traefik:
   ```bash
   sudo systemctl reload traefik
   ```

The RAG bridge will still require an `X-Bridge-Token` (or `?token=`) on every request — Authentik only adds the SSO login layer on top.

### Restricting access by group

Authentik forwards the user's groups in the `X-authentik-groups` header. You can use this in Authentik's policy engine to restrict the nanobot-stack application to specific groups:

1. In Authentik, go to **Customisation → Policies → Create**.
2. Create an **Expression Policy** with:
   ```python
   return ak_is_group_member(request.user, name="nanobot-admins")
   ```
3. Bind this policy to the `nanobot-stack` application.

Only members of the `nanobot-admins` group will be able to access the stack.

---

## Admin UI

The admin UI is a comprehensive web-based console for managing every aspect of the nanobot-stack. It consolidates analytics, settings, tools, vector database, logs, chat, channels, shell, configuration, and advanced features into a single Authentik-protected interface.

### Accessing the admin UI

```
https://rag.yourdomain.com/admin
```

On first visit, the UI will prompt you for your **bridge token** (stored in `localStorage` for subsequent visits). Find your token with:

```bash
cat /opt/nanobot-stack/rag-bridge/.bridge_token
```

Authentication is dual-layer:
1. **Authentik SSO** — handled by Traefik before the request reaches the RAG bridge
2. **Bridge token** — checked by the application on every API call

### Enabling / disabling

The admin UI is enabled by default. To disable it:

```bash
# In /opt/nanobot-stack/rag-bridge/.env:
ADMIN_UI_ENABLED=false
```

Then restart: `sudo systemctl restart nanobot-rag`

### Sections

The admin UI has 10 tabs:

#### 1. Analytics

Real-time system dashboard merged from the legacy `/dashboard`. Shows:
- Health status, circuit breaker states, cache hit rates, rate limiter state
- Token usage chart (by model) and cache performance chart
- Working memory sessions, loaded plugins, model route table
- Auto-refresh toggle (30s interval)

#### 2. Settings

Centralized view of all ~80 configurable parameters grouped by section (domain, system, network, models, RAG tuning, tools, channels, etc.):
- Current value (sensitive values are masked), default, description
- Inline editing: modify a value and click "Propose Change"
- Changes go through the **Config Writer** approval workflow — they are validated, staged, and diffed before being applied
- Requires `CONFIG_WRITER_ENABLED=true` for write operations

#### 3. Tools & Routing

- **Plugins**: lists loaded plugins with tool count and hook count
- **Plugin tools**: expandable list with a "Test" button to invoke any tool with JSON parameters
- **Model routing**: task → model chain table, route preview form to test classification

#### 4. Vector DB

- **Collections overview**: all Qdrant collections with point counts and status
- **Search tester**: run a query against selected collections, see results with relevance scores
- **Collection browser**: scroll through individual points with payload preview

#### 5. Logs

- **Audit log viewer**: timestamps, HTTP methods, paths, status codes, IPs, response times
- Filters: by method (GET/POST/...), path substring, status range
- Paginated with configurable limit

#### 6. Chat

- Full chat interface with SSE streaming
- Pipeline progress sidebar showing each step in real time (classify, HyDE, retrieve, rerank, generate...)
- Toggle switches: auto-classify, HyDE, citations, self-critique
- Session ID support for multi-turn conversations
- Source panel when citations are returned

#### 7. Channels

- **Adapter status cards**: Telegram, Discord, WhatsApp — configured/running/error
- **DM pairing management**: pending pairing requests with approve/reject buttons
- **Approved users**: list of all approved channel users with revoke option
- DM policy indicator (pairing mode vs open mode)

#### 8. Elevated Shell

- **Command allowlist**: reference table of all permitted commands (default + user-added)
- **Pending actions**: proposed system commands awaiting approval
- **Action history**: all past actions with status, stdout/stderr, return codes
- **Propose form**: submit a new command for approval

#### 9. Config Writer

- **Pending changes**: proposed configuration modifications awaiting review
- **Diff viewer**: syntax-colored unified diff (green = added, red = removed)
- **Change history**: all past changes with status
- **Rollback**: one-click rollback for previously applied changes

#### 10. Advanced

- **Knowledge Graph Explorer**: entity search and relation finder
- **PII Scanner**: paste text to detect and classify personal data
- **Pipeline Explainer**: run a query and see the full pipeline trace (classification, routing, retrieval, timing)
- **Working Memory**: session statistics and context tracking

## Usage

### Updating

When you pull new changes:

```bash
git pull

# Full update (code + Python deps + restart)
sudo ./update.sh

# Fast update (code only, skips pip — a few seconds)
sudo ./update.sh --code-only

# Dependencies only (no code change)
sudo ./update.sh --deps-only
```

### Rotating secrets

For compliance or after a suspected compromise:

```bash
# Rotate everything (bridge token, DB password, Langfuse keys)
sudo nanobot-rotate-secrets --all

# Or selectively:
sudo nanobot-rotate-secrets --bridge
sudo nanobot-rotate-secrets --postgres
sudo nanobot-rotate-secrets --langfuse
```

### Adding documents

Drop files into the document directories and they'll be automatically indexed every 10 minutes:

```bash
# General documentation
/opt/nanobot-stack/rag-docs/docs/

# Operational runbooks
/opt/nanobot-stack/rag-docs/runbooks/

# Project context
/opt/nanobot-stack/rag-docs/projects/
```

Supported formats: Markdown, text, PDF, DOCX, HTML, YAML, JSON, CSV, and image files (JPEG, PNG — described via AI vision).

To trigger immediate indexing:

```bash
nanobot-rag-ingest
```

### Monitoring

- **Admin UI**: `https://rag.yourdomain.com/admin` (full management console)
- **Legacy dashboard**: `https://rag.yourdomain.com/dashboard?token=TOKEN`
- **Langfuse**: `https://observability.yourdomain.com`
- **Prometheus metrics**: `http://127.0.0.1:8089/metrics`
- **Self-test**: `nanobot-stack-selftest`

### Service management

```bash
# View status of all services
sudo systemctl status qdrant nanobot-rag nanobot

# Restart everything
sudo systemctl restart qdrant nanobot-rag nanobot

# View logs
sudo journalctl -u nanobot-rag -f
sudo journalctl -u nanobot -f
```

## FAQ

<details>
<summary><strong>Do I need a GPU?</strong></summary>

No. Everything runs on CPU. The default Ollama model (qwen2.5:7b) needs about 5GB of RAM. The cross-encoder reranker runs on CPU in ~50ms. A GPU will make the reranker faster but is not required.
</details>

<details>
<summary><strong>How much does it cost to run?</strong></summary>

The infrastructure itself is free (self-hosted). The only cost is LLM API usage. The model router minimises this by using cheap models (gpt-4.1-mini at ~$0.15/M tokens) for simple tasks and premium models only when needed. With the embedding cache and smart routing, typical personal usage costs a few dollars per month.
</details>

<details>
<summary><strong>Can I use it without Traefik/Authentik?</strong></summary>

Yes. The core services (nanobot, RAG bridge, Qdrant) work locally on `127.0.0.1` without any reverse proxy. Traefik and Authentik are only needed for public HTTPS access with authentication. For local-only use, you can skip them entirely and access the services directly (e.g. `http://127.0.0.1:8089/admin`). See the [Authentik Setup](#authentik-setup) section for instructions on disabling the middleware.
</details>

<details>
<summary><strong>Can I use only OpenRouter (without separate OpenAI/Anthropic keys)?</strong></summary>

Yes. Set only `OPENROUTER_API_KEY` and leave the others empty. The router will skip unconfigured providers and use OpenRouter for everything. Embedding fallback will also route through OpenRouter.
</details>

<details>
<summary><strong>What happens when my internet goes down?</strong></summary>

If Ollama is installed (`INSTALL_OLLAMA=true`), the circuit breakers will detect cloud provider failures within seconds and automatically route to the local model. You'll get degraded but functional responses. When connectivity returns, the circuit breakers will gradually re-enable cloud providers.
</details>

<details>
<summary><strong>Can I add my own tools?</strong></summary>

**Option 1 — Plugin system (recommended)**: Drop a Python file into the plugins directory with `@tool` and `@hook` decorators. Plugins are hot-reloaded without restarts.

**Option 2 — Manual**: Add new Python functions to `src/bridge/tools.py`, expose them in `src/bridge/extensions.py` as endpoints, and add corresponding MCP tool wrappers in `src/mcp/rag_mcp_server.py`. Then run `sudo ./update.sh --code-only`.
</details>

<details>
<summary><strong>How do I back up my data?</strong></summary>

The important data lives in:
- `/var/lib/qdrant/` — Vector database (memory + documents)
- `/opt/nanobot-stack/rag-bridge/state/` — Ingestion state + feedback + audit logs
- `/opt/nanobot-stack/rag-docs/` — Source documents
- `/opt/docker/langfuse/data/` — Langfuse traces

A simple `rsync` or filesystem snapshot covers everything.
</details>

## Troubleshooting

<details>
<summary><strong>selftest shows "not_configured" for all profiles</strong></summary>

You haven't added your API keys yet. Edit the `.env` files:

```bash
sudo nano /opt/nanobot-stack/rag-bridge/.env
sudo nano /opt/nanobot-stack/nanobot/config/.env
sudo systemctl restart nanobot-rag nanobot
```
</details>

<details>
<summary><strong>Qdrant won't start</strong></summary>

Check logs: `sudo journalctl -u qdrant -n 50`

Common causes:
- Port 6333 already in use: `sudo ss -tlnp | grep 6333`
- Insufficient disk space: `df -h /var/lib/qdrant`
- Permission issue: `sudo chown -R nanobot:nanobot /var/lib/qdrant`
</details>

<details>
<summary><strong>Langfuse shows "unhealthy"</strong></summary>

Langfuse runs in Docker. Check its containers:

```bash
cd /opt/docker/langfuse
sudo docker compose ps
sudo docker compose logs langfuse-web --tail 50
```
</details>

<details>
<summary><strong>Circuit breakers are all OPEN</strong></summary>

This means all LLM providers have failed recently. Check:
1. Your API keys are correct
2. Your server has internet access: `curl -I https://api.openai.com`
3. Your account has credit: check the provider dashboards

The breakers auto-recover after 2 minutes. Force reset by restarting: `sudo systemctl restart nanobot-rag`
</details>

<details>
<summary><strong>Ollama models fail to pull</strong></summary>

```bash
# Check Ollama is running
sudo systemctl status ollama

# Manual pull with verbose output
ollama pull qwen2.5:7b

# Check available disk space
df -h /usr/share/ollama
```
</details>

## Project structure

```
nanobot-stack/
├── .gitignore                    # Excludes secrets, caches, state
├── stack.env.example             # Configuration template
├── lib.sh                        # Shared functions & defaults
├── deploy.sh                     # First-time installer
├── update.sh                     # Fast updater
├── rotate-secrets.sh             # Secret rotation
├── src/
│   ├── bridge/                   # RAG bridge (32 Python modules)
│   ├── mcp/                      # MCP server (26 tools for the agent)
│   └── config/                   # Default configs (model_router.json, policy prompt)
├── systemd/                      # Service definitions (.template)
├── traefik/                      # Reverse proxy config (.template)
├── scripts/                      # Helper scripts (.template)
└── docs/
    └── REFERENCE.md              # Full API & endpoint reference
```

## Contributing

Contributions are welcome. To get started:

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-improvement`
3. Make your changes
4. Test locally: `sudo ./deploy.sh` on a test server or VM
5. Submit a pull request

Please keep in mind:
- **No secrets or personal data** in commits.
- **Template files** (`.template`) are rendered at deploy time — put `${VARIABLE}` placeholders, not hardcoded values.
- **Python code** should pass `python3 -c "import ast; ast.parse(open('file.py').read())"`.
- **Bash scripts** should pass `bash -n script.sh`.

## License

[MIT](LICENSE)
