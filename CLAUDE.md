# nanobot-stack — Developer Guide

## Architecture

Single-user self-hosted AI assistant with RAG. French-first UI.

```
src/bridge/       ← FastAPI core (all Python, ~22k LOC)
src/config/       ← model_router.json, langfuse compose
src/mcp/          ← MCP server integration
tests/            ← 33 test files, pytest + pytest-asyncio
migrations/       ← Idempotent DB migrations (SQLite + Qdrant)
docs/             ← Specs, plans, roadmap
```

## Key Modules

| Module | Purpose |
|--------|---------|
| `app.py` | FastAPI entrypoint, all core endpoints, middleware stack |
| `admin_ui.py` | Single-file SPA (Alpine.js + Chart.js), no build step |
| `streaming.py` | SSE streaming for smart-chat with pipeline phases |
| `adaptive_router.py` | Model ranking with budget_pressure + quality scores |
| `token_budget.py` | Daily token budget tracking + enforcement |
| `trust_engine.py` | Action trust levels (auto → blocked) |
| `procedural_memory.py` | Learned workflow patterns |
| `knowledge_graph.py` | Entity-relation graph in SQLite |
| `system_metrics.py` | psutil-based CPU/RAM/Disk monitoring |
| `backup_manager.py` | Qdrant + SQLite backup with encryption |

## Running Locally

```bash
# Install deps
pip install -r src/bridge/requirements.txt

# Start Qdrant
docker run -p 6333:6333 qdrant/qdrant:v1.13.2

# Run bridge
cd src/bridge && RAG_BRIDGE_TOKEN=dev uvicorn app:app --reload

# Run tests
pytest tests/ -v -x
```

## Docker

```bash
cp stack.env.example .env  # edit with your values
docker compose up -d
```

## Conventions

- **No build step** for admin UI — Python string constants in `admin_ui.py`
- **Alpine.js 3** for reactivity, **Chart.js 4** for charts
- **Neon Observatory** design system (dark theme, tonal surfaces, no hard borders)
- All endpoints require `X-Bridge-Token` header except `/healthz` and `/metrics`
- Pydantic models with `max_length` / `Field` constraints on all user inputs
- `hmac.compare_digest()` for token comparison (timing-safe)
- Audit log is **mandatory** — all requests logged to JSONL with level field

## Error Handling Policy

- Catch **specific** exceptions, not bare `except Exception`
- Log with `logger.exception()` for stack traces
- Return sanitized error messages to clients (no internal paths/keys)
- Use `X-Request-ID` header for log correlation

## Testing

```bash
# Full suite
pytest tests/ -v

# Skip known pre-existing failures
pytest tests/ -k "not test_daily_report_model_items and not test_workflow_items"
```

Test pattern: `FastAPI()` + `TestClient`, mock `verify_token`, mock DB where needed.

## Deployment

- **Production**: `docker compose up -d` (bridge + Qdrant + optional Traefik)
- **Bare metal**: `deploy.sh` (systemd services)
- **Backups**: `POST /api/backup/run`, verify with `POST /api/backup/verify/{id}`
- **Restore**: `POST /api/backup/restore/{id}` (requires BackupManager support)
