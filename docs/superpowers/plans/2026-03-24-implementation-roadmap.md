# Implementation Roadmap — Orchestration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Coordinate the sequential implementation of all nanobot-stack v10 features (Admin UI v2, Sub-D through Sub-L) with correct migration numbering, shared-file conflict resolution, and validated execution order.

**Architecture:** Each sub-project is implemented in its own git branch, sequentially merged to `main`. Shared files (`app.py`, `requirements.txt`, `scheduler_registry.py`, `docker-compose.yml`) are touched once per sub-project; merge conflicts are resolved at PR time. Sub-L (Encryption At-Rest) is always last as it modifies existing stored data.

**Tech Stack:** Python/FastAPI, APScheduler, SQLite migrations, Qdrant, Docker Compose, pytest, pylint (10.00/10 enforced in CI).

---

## Migration Number Map (source of truth)

| # | File | Status |
|---|------|--------|
| 008 | `migrations/008_initial.py` | ✅ deployed |
| 010 | `migrations/010_v10_evolution.py` | ✅ deployed |
| 011 | `migrations/011_scheduler.py` | ✅ deployed |
| 012 | `migrations/012_email_calendar.py` | ✅ deployed |
| 013 | `migrations/013_rss.py` | ✅ deployed |
| 014 | `migrations/014_web_search.py` | ✅ deployed |
| 015 | `migrations/015_backup_log.py` | ✅ deployed |
| 016 | `migrations/016_local_docs.py` | ✅ deployed |
| 017 | `migrations/017_voice.py` | ✅ deployed |
| 018 | `migrations/018_memory_decay.py` | ✅ deployed |
| 019 | `migrations/019_push_subscriptions.py` | ✅ deployed |
| 020 | `migrations/020_github_obsidian.py` | ✅ deployed |
| 021 | `migrations/021_browser.py` | ✅ deployed |
| *(none)* | *(field-level, no DDL)* | ✅ deployed |

---

## Dependency Graph

```
Admin UI v2 ──────────────────────────────────┐
Sub-D (Web Search)  ──────────────────────────┤
Sub-E (Local Docs)  ──────────────────────────┤
Sub-G (Voice)       ──── needs infra ─────────┤
Sub-H (Memory Decay) ─── benefits from B/C/D ─┤ → Sub-L (last)
Sub-I (PWA Mobile)  ──────────────────────────┤
Sub-J (Dev Integrations) ─────────────────────┤
Sub-K (Browser Auto) ─── benefits from Sub-D ─┤
```

**Hard constraints:**
- Sub-L must be **last** — it retrofits encryption onto existing stored data.
- Sub-H should run **after Sub-B, Sub-C, Sub-D** are live so memory collections are populated.
- Sub-K should run **after Sub-D** — BrowserAgent reuses WebSearchAgent result structures.

**No other hard ordering.** The sequence below is the recommended priority order.

---

## Shared Files — Conflict Map

Every sub-project that adds a router or job touches the same files. Always merge in the order below to keep conflicts manageable.

| File | Touched by |
|------|-----------|
| `src/bridge/app.py` | Sub-D, Sub-E, Sub-G, Sub-I, Sub-J, Sub-K |
| `src/bridge/requirements.txt` | Sub-E, Sub-G, Sub-I, Sub-J, Sub-K, Sub-L |
| `src/bridge/scheduler_registry.py` | Sub-D, Sub-H, Sub-J |
| `src/bridge/scheduler_executor.py` | Sub-D, Sub-H |
| `docker-compose.yml` | Sub-D (SearXNG), Sub-G (faster-whisper, Piper), Sub-K (Playwright) |
| `src/bridge/agents/__init__.py` | Sub-D (WebSearchAgent), Sub-K (BrowserAgent) |

**Resolution strategy:** Each sub-project branch starts from `main` at merge time. For `app.py` / `requirements.txt`, the pattern is always additive (append a router mount / append a dependency), so conflicts are trivial. Resolve by keeping both additions.

---

## Execution Order

### Task 0: Pre-flight — Fix Migration Numbers in Plan Files

**Files:**
- Modify: `docs/superpowers/plans/2026-03-24-sub-project-e-local-docs.md`
- Modify: `docs/superpowers/plans/2026-03-24-sub-project-g-voice-interface.md`
- Modify: `docs/superpowers/plans/2026-03-24-sub-project-h-memory-decay.md`
- Modify: `docs/superpowers/plans/2026-03-24-sub-project-i-pwa-mobile.md`
- Modify: `docs/superpowers/plans/2026-03-24-sub-project-j-dev-integrations.md`
- Modify: `docs/superpowers/plans/2026-03-24-sub-project-k-browser-automation.md`

- [x] **Step 1: Patch Sub-E (014 → 016)**

```bash
sed -i 's/014_local_docs/016_local_docs/g; s/VERSION = 14/VERSION = 16/g' \
  docs/superpowers/plans/2026-03-24-sub-project-e-local-docs.md
```

- [x] **Step 2: Patch Sub-G (014 → 017)**

```bash
sed -i 's/014_voice/017_voice/g; s/VERSION = 14/VERSION = 17/g' \
  docs/superpowers/plans/2026-03-24-sub-project-g-voice-interface.md
```

- [x] **Step 3: Patch Sub-H (016 → 018)**

```bash
sed -i 's/016_memory_decay/018_memory_decay/g; s/VERSION = 16/VERSION = 18/g' \
  docs/superpowers/plans/2026-03-24-sub-project-h-memory-decay.md
```

- [x] **Step 4: Patch Sub-I (016 → 019)**

```bash
sed -i 's/016_push_subscriptions/019_push_subscriptions/g; s/VERSION = 16/VERSION = 19/g' \
  docs/superpowers/plans/2026-03-24-sub-project-i-pwa-mobile.md
```

- [x] **Step 5: Patch Sub-J (016 or 018 → 020)**

```bash
sed -i 's/016_github_obsidian/020_github_obsidian/g; \
        s/018_github_obsidian/020_github_obsidian/g; \
        s/VERSION = 16/VERSION = 20/g; \
        s/VERSION = 18/VERSION = 20/g' \
  docs/superpowers/plans/2026-03-24-sub-project-j-dev-integrations.md
```

- [x] **Step 6: Patch Sub-K (019 → 021)**

```bash
sed -i 's/019_browser/021_browser/g; s/VERSION = 19/VERSION = 21/g' \
  docs/superpowers/plans/2026-03-24-sub-project-k-browser-automation.md
```

- [x] **Step 7: Verify no plan still references a duplicate migration**

```bash
grep -rn "VERSION = 1[46]" docs/superpowers/plans/
# Expected: zero matches (014 = Sub-D only, 016 now = Sub-E only)
grep -rn "migrations/014" docs/superpowers/plans/ | grep -v sub-project-d
# Expected: zero matches
```

- [x] **Step 8: Commit**

```bash
git add docs/superpowers/plans/
git commit -m "docs: fix migration numbers in sub-project plans (E→016, G→017, H→018, I→019, J→020, K→021)"
```

---

### Task 1: Admin UI v2

**Plan:** `docs/superpowers/plans/2026-03-24-admin-ui-v2.md`
**Migration:** none
**Shared files touched:** none

- [ ] **Step 1: Create worktree**

```bash
git worktree add ../nanobot-admin-ui-v2 -b feature/admin-ui-v2
cd ../nanobot-admin-ui-v2
```

- [ ] **Step 2: Execute plan**

Use `superpowers:subagent-driven-development` with plan `docs/superpowers/plans/2026-03-24-admin-ui-v2.md`.

- [ ] **Step 3: Run full test suite**

```bash
cd src/bridge && python -m pytest ../../tests/ -v
# Expected: all pass, pylint 10.00/10
```

- [ ] **Step 4: PR → merge → delete branch**

```bash
git push -u origin feature/admin-ui-v2
gh pr create --title "feat: Admin UI v2 — automated tests for trust/cost/workflows/agent tabs"
# After CI green → merge → git worktree remove ../nanobot-admin-ui-v2
```

---

### Task 2: Sub-D — Web Search (SearXNG) ✅ MERGED ON MAIN

**Plan:** `docs/superpowers/plans/2026-03-24-sub-project-d-web-search.md`
**Migration:** 014
**Tests:** 157 passing — pylint 10.00/10

Notable implementation:
- `WebSearchAgent` extends `AgentBase`, queries SearXNG, rate-limits by IP, caches results in `web_search_results` Qdrant collection (TTL 6h)
- SearXNG service added to `docker-compose.yml` on private network
- `web_digest` section added to `JobExecutor` guarded by `SEARXNG_ENABLED`
- `SEARXNG_ENABLED=false` default — all paths guarded

- [x] **Step 1–5: Implemented, spec-reviewed, quality-reviewed**
- [x] **Step 6: PR → merge → delete branch**

---

### Task 3: Sub-E — Local Document Ingestion ✅ MERGED ON MAIN

**Plan:** `docs/superpowers/plans/2026-03-24-sub-project-e-local-docs.md`
**Migration:** 016
**Tests:** 125 passing — pylint 10.00/10

Notable implementation:
- `LocalDocIngestor`: TXT/MD/PDF/DOCX extraction, semantic chunking, PII filter, Qdrant upsert with dedup by file hash
- `LocalDocWatcher`: watchdog-based directory monitor with clean shutdown
- UUID-based Qdrant point IDs (not hash) to avoid ID collisions across file updates
- `LOCAL_DOCS_ENABLED=false` default; watcher only started when enabled

- [x] **Step 1–5: Implemented, spec-reviewed, quality-reviewed**
- [x] **Step 6: PR → merge → delete branch**

---

### Task 4: Sub-G — Voice Interface ✅ MERGED ON MAIN

**Plan:** `docs/superpowers/plans/2026-03-24-sub-project-g-voice-interface.md`
**Migration:** 017
**Commit on main:** `1b0f79d feat(voice): implement Sub-G — Voice Interface (STT/TTS)`
**Tests:** 126 passing — pylint 10.00/10

- [x] **Fully implemented and merged to `main`**

---

### Task 5: Sub-H — Memory Decay & Feedback Loop ✅ MERGED ON MAIN

**Plan:** `docs/superpowers/plans/2026-03-24-sub-project-h-memory-decay.md`
**Migration:** 018
**Commits on main:** `5b9c95e` through `8c43c5b` (6 commits)
**Tests:** 186 passing — pylint 10.00/10

Notable implementation:
- `MemoryDecayManager`: exponential decay scoring (`score_point`), `run_decay_scan`, `confirm_access`, `forget`
- `FeedbackLearner`: `record_feedback`, `analyze_recent_feedback`, `apply_adjustments`
- `adaptive_router.py` applies `routing_adjustments` multiplier in `get_model_ranking()`
- Weekly `Memory Decay Scan` job registered when `MEMORY_DECAY_ENABLED=true`
- `/memory/decay`, `/memory/feedback`, `/memory/forget` endpoints

- [x] **Fully implemented and merged to `main`**

---

### Task 6: Sub-I — PWA Mobile ✅ MERGED ON MAIN

**Plan:** `docs/superpowers/plans/2026-03-24-sub-project-i-pwa-mobile.md`
**Migration:** 019

- [x] **Step 1–5: Implemented and reviewed**
- [x] **Step 6: PR → merge → delete branch**

---

### Task 7: Sub-J — GitHub & Obsidian Integrations ✅ MERGED ON MAIN

**Plan:** `docs/superpowers/plans/2026-03-24-sub-project-j-dev-integrations.md`
**Migration:** 020

- [x] **Step 1–5: Implemented and reviewed**
- [x] **Step 6: PR → merge → delete branch**

---

### Task 8: Sub-K — Browser Automation ✅ MERGED ON MAIN

**Plan:** `docs/superpowers/plans/2026-03-24-sub-project-k-browser-automation.md`
**Migration:** 021
**Tests:** 357 passing — pylint 10.00/10

Notable implementation decisions:
- `seed_default_trust_policies()` uses direct SQLite UPDATE on `te.TRUST_DB_PATH` to set `auto_promote_after` deterministically (trust_engine API doesn't expose this field)
- Playwright guard: `not PLAYWRIGHT_AVAILABLE and async_playwright is None` (AND, not OR — tests mock `async_playwright` while `PLAYWRIGHT_AVAILABLE=False`)
- Domain allowlist: `allowed[4:] if allowed.startswith("www.") else allowed` (not `lstrip` which corrupts entries)

- [x] **Step 1–5: Implemented, spec-reviewed, quality-reviewed**
- [x] **Step 6: PR → merge → delete branch**

---

### Task 9: Sub-L — Encryption At-Rest ✅ MERGED ON MAIN

**Plan:** `docs/superpowers/plans/2026-03-24-sub-project-l-encryption-at-rest.md`
**Migration:** none (field-level encryption, no DDL changes)
**Tests:** 396 passing (39 new) — pylint 10.00/10

Notable implementation decisions:
- Two domain keys via HKDF context: `sqlite-v1` and `qdrant-v1`
- Encrypted format: `enc:v1:<base64url_no_padding(nonce[12] + ciphertext + tag[16])>`
- `decrypt_field()` passthrough for non-`enc:v1:` values (backward compat)
- All `_migration_job.update()` calls protected by `_migration_lock`
- `asyncio.get_running_loop().run_in_executor()` (not deprecated `get_event_loop()`)
- Per-row/per-point error isolation in all migration paths

- [x] **Step 1–5: Implemented, spec-reviewed, quality-reviewed**
- [x] **Step 6: PR → merge → delete branch**

---

## Quick Reference — Final State

After all tasks complete, the migration table will be:

```
008 010 011 012 013 014 015 016 017 018 019 020 021
init v10 sched email rss web bkp docs voice decay pwa dev browser
```

And `src/bridge/app.py` will mount these routers (in addition to existing ones):
```python
from web_search_api import router as web_search_router    # Sub-D
from local_docs_api  import router as local_docs_router   # Sub-E
from voice_api       import router as voice_router        # Sub-G
from memory_api      import router as memory_router       # Sub-H
from pwa_api         import router as pwa_router          # Sub-I
from dev_api         import router as dev_router          # Sub-J
from browser_api     import router as browser_router      # Sub-K
```
