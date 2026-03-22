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
  <a href="#usage">Usage</a> •
  <a href="#faq">FAQ</a> •
  <a href="#contributing">Contributing</a>
</p>

---

## What is this?

**nanobot-stack** is a one-command deployment script that sets up a complete, self-hosted AI assistant on your own server. Think of it as your private AI that:

- **Remembers everything** — It stores facts, decisions, and conversations in a vector database and recalls them automatically when relevant.
- **Routes to the best model** — It picks the cheapest model that can handle each task (a quick rewrite doesn't need GPT-4) and falls back to alternatives if one provider is down.
- **Uses tools** — It can run shell commands, fetch web pages, send notifications, and execute multi-step plans.
- **Works offline** — An optional local model (Ollama) keeps things running when your internet connection drops.
- **Stays private** — Everything runs on your hardware. Your conversations and data never leave your server.

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
                     │   ├──► Web fetcher
                     │   └──► Webhook notifications
                     ▼
              ┌────────────┐    ┌─────────┐
              │  Qdrant    │    │ Ollama  │
              │  (vector   │    │ (local  │
              │  database) │    │ models) │
              └────────────┘    └─────────┘
```

**How a question flows through the stack:**

1. You ask a question via the web UI or API.
2. The **RAG bridge** classifies your question (is it a memory lookup? a coding task? an incident?).
3. It searches **Qdrant** for relevant memories and documents.
4. It injects the found context + your user profile into the prompt.
5. It picks the best model for the job (cheap for simple tasks, premium for complex ones).
6. If all cloud providers are down, it falls back to **Ollama** running locally.
7. The answer streams back to you in real time, with the agent narrating each step.

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
- Fallback chains with circuit breakers: if OpenAI is down, it tries Anthropic, then OpenRouter, then local Ollama
- Hot-reloadable config: edit `model_router.json` without restarting
- Gracefully skips providers whose API keys aren't configured
</details>

<details>
<summary><strong>Tool execution</strong> — Your AI can do things, not just talk</summary>

- **Restricted shell**: pre-approved read-only commands (systemctl status, journalctl, curl, dig, df…)
- **Web fetcher**: fetch and extract text from any URL
- **Notifications**: send alerts via webhook (ntfy.sh, Slack, Telegram)
- **Multi-step planner**: decomposes complex tasks into steps and executes them sequentially
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
- Real-time HTML dashboard at `/dashboard`
- Structured JSON logging for SIEM integration
- Append-only audit log (who called what, when)
</details>

<details>
<summary><strong>Security</strong> — Production-grade hardening</summary>

- All services bind to localhost (127.0.0.1) — public access only through Traefik
- Authentication via Authentik ForwardAuth
- Bearer token on all internal bridge endpoints
- Qdrant API key protection
- systemd hardening (NoNewPrivileges, ProtectSystem=strict…)
- Secret rotation script with orderly restarts
- No secrets in the Git repository
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
# https://rag.yourdomain.com/dashboard?token=YOUR_BRIDGE_TOKEN
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

- **Dashboard**: `https://rag.yourdomain.com/dashboard?token=TOKEN`
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

Yes. The core services (nanobot, RAG bridge, Qdrant) work locally on `127.0.0.1` without any reverse proxy. Traefik and Authentik are only needed for public HTTPS access with authentication. For local-only use, you can skip them entirely and access the services directly.
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

Yes. Add new Python functions to `src/bridge/tools.py`, expose them in `src/bridge/extensions.py` as endpoints, and add corresponding MCP tool wrappers in `src/mcp/rag_mcp_server.py`. Then run `sudo ./update.sh --code-only`.
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
├── stack.env.example          # Configuration template
├── lib.sh                     # Shared functions & defaults
├── deploy.sh                  # First-time installer
├── update.sh                  # Fast updater
├── rotate-secrets.sh          # Secret rotation
├── src/
│   ├── bridge/                # RAG bridge (16 Python modules)
│   ├── mcp/                   # MCP server (22 tools for the agent)
│   └── config/                # Default configs
├── systemd/                   # Service definitions (.template)
├── traefik/                   # Reverse proxy config (.template)
├── scripts/                   # Helper scripts (.template)
└── docs/
    └── REFERENCE.md           # Full API & endpoint reference
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
