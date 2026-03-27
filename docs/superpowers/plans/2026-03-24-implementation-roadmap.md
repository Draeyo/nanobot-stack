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
| **014** | **`migrations/014_web_search.py`** | **→ Sub-D** |
| 015 | `migrations/015_backup_log.py` | ✅ deployed |
| **016** | **`migrations/016_local_docs.py`** | **→ Sub-E** |
| **017** | **`migrations/017_voice.py`** | **→ Sub-G** |
| **018** | **`migrations/018_memory_decay.py`** | **→ Sub-H** |
| **019** | **`migrations/019_push_subscriptions.py`** | **→ Sub-I** |
| **020** | **`migrations/020_github_obsidian.py`** | **→ Sub-J** |
| **021** | **`migrations/021_browser.py`** | **→ Sub-K** |
| *(none)* | *(field-level, no DDL)* | **→ Sub-L** |

> ⚠️ **The individual plans for Sub-E, Sub-G, Sub-H, Sub-I, Sub-J, Sub-K all contain wrong migration numbers.** Step 0 of each task below is to patch the plan file before execution.

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

- [ ] **Step 1: Patch Sub-E (014 → 016)**

```bash
sed -i 's/014_local_docs/016_local_docs/g; s/VERSION = 14/VERSION = 16/g' \
  docs/superpowers/plans/2026-03-24-sub-project-e-local-docs.md
```

- [ ] **Step 2: Patch Sub-G (014 → 017)**

```bash
sed -i 's/014_voice/017_voice/g; s/VERSION = 14/VERSION = 17/g' \
  docs/superpowers/plans/2026-03-24-sub-project-g-voice-interface.md
```

- [ ] **Step 3: Patch Sub-H (016 → 018)**

```bash
sed -i 's/016_memory_decay/018_memory_decay/g; s/VERSION = 16/VERSION = 18/g' \
  docs/superpowers/plans/2026-03-24-sub-project-h-memory-decay.md
```

- [ ] **Step 4: Patch Sub-I (016 → 019)**

```bash
sed -i 's/016_push_subscriptions/019_push_subscriptions/g; s/VERSION = 16/VERSION = 19/g' \
  docs/superpowers/plans/2026-03-24-sub-project-i-pwa-mobile.md
```

- [ ] **Step 5: Patch Sub-J (016 or 018 → 020)**

```bash
sed -i 's/016_github_obsidian/020_github_obsidian/g; \
        s/018_github_obsidian/020_github_obsidian/g; \
        s/VERSION = 16/VERSION = 20/g; \
        s/VERSION = 18/VERSION = 20/g' \
  docs/superpowers/plans/2026-03-24-sub-project-j-dev-integrations.md
```

- [ ] **Step 6: Patch Sub-K (019 → 021)**

```bash
sed -i 's/019_browser/021_browser/g; s/VERSION = 19/VERSION = 21/g' \
  docs/superpowers/plans/2026-03-24-sub-project-k-browser-automation.md
```

- [ ] **Step 7: Verify no plan still references a duplicate migration**

```bash
grep -rn "VERSION = 1[46]" docs/superpowers/plans/
# Expected: zero matches (014 = Sub-D only, 016 now = Sub-E only)
grep -rn "migrations/014" docs/superpowers/plans/ | grep -v sub-project-d
# Expected: zero matches
```

- [ ] **Step 8: Commit**

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

### Task 2: Sub-D — Web Search (SearXNG)

**Plan:** `docs/superpowers/plans/2026-03-24-sub-project-d-web-search.md`
**Migration:** 014
**Shared files touched:** `app.py`, `scheduler_registry.py`, `scheduler_executor.py`, `agents/__init__.py`

**Infra prerequisite:** SearXNG container must be defined in `docker-compose.yml` with `SEARXNG_URL` env var. The plan includes this — verify it is added.

- [ ] **Step 1: Pull latest main**

```bash
git checkout main && git pull
```

- [ ] **Step 2: Create worktree**

```bash
git worktree add ../nanobot-sub-d -b feature/sub-d-web-search
cd ../nanobot-sub-d
```

- [ ] **Step 3: Execute plan**

Use `superpowers:subagent-driven-development` with plan `docs/superpowers/plans/2026-03-24-sub-project-d-web-search.md`.

- [ ] **Step 4: Verify migration slot**

```bash
ls migrations/014_web_search.py  # must exist
ls migrations/015_backup_log.py  # must still exist unchanged
```

- [ ] **Step 5: Run full test suite + pylint**

```bash
cd src/bridge && python -m pytest ../../tests/ -v
pylint $(git ls-files '*.py') --fail-under=10
```

- [ ] **Step 6: PR → merge → delete branch**

```bash
git push -u origin feature/sub-d-web-search
gh pr create --title "feat(sub-d): web search via SearXNG + WebSearchAgent"
```

---

### Task 3: Sub-E — Local Document Ingestion

**Plan:** `docs/superpowers/plans/2026-03-24-sub-project-e-local-docs.md`
**Migration:** 016 (after Task 0 patch)
**Shared files touched:** `app.py`, `requirements.txt`

- [ ] **Step 1: Pull latest main (after Sub-D merged)**

```bash
git checkout main && git pull
```

- [ ] **Step 2: Create worktree**

```bash
git worktree add ../nanobot-sub-e -b feature/sub-e-local-docs
cd ../nanobot-sub-e
```

- [ ] **Step 3: Verify migration number in plan is 016**

```bash
grep "VERSION" docs/superpowers/plans/2026-03-24-sub-project-e-local-docs.md | head -3
# Expected: VERSION = 16
```

- [ ] **Step 4: Execute plan**

Use `superpowers:subagent-driven-development` with plan `docs/superpowers/plans/2026-03-24-sub-project-e-local-docs.md`.

- [ ] **Step 5: Run full test suite + pylint**

```bash
cd src/bridge && python -m pytest ../../tests/ -v
pylint $(git ls-files '*.py') --fail-under=10
```

- [ ] **Step 6: PR → merge → delete branch**

```bash
git push -u origin feature/sub-e-local-docs
gh pr create --title "feat(sub-e): local document ingestion (PDF/MD/TXT/DOCX + watchdog)"
```

---

### Task 4: Sub-G — Voice Interface

**Plan:** `docs/superpowers/plans/2026-03-24-sub-project-g-voice-interface.md`
**Migration:** 017 (after Task 0 patch)
**Shared files touched:** `app.py`, `requirements.txt`, `docker-compose.yml`

**Infra prerequisite:** faster-whisper and Piper TTS containers added to `docker-compose.yml`. The plan includes this.

- [ ] **Step 1: Pull latest main**

```bash
git checkout main && git pull
```

- [ ] **Step 2: Create worktree**

```bash
git worktree add ../nanobot-sub-g -b feature/sub-g-voice
cd ../nanobot-sub-g
```

- [ ] **Step 3: Verify migration number in plan is 017**

```bash
grep "VERSION" docs/superpowers/plans/2026-03-24-sub-project-g-voice-interface.md | head -3
# Expected: VERSION = 17
```

- [ ] **Step 4: Execute plan**

Use `superpowers:subagent-driven-development` with plan `docs/superpowers/plans/2026-03-24-sub-project-g-voice-interface.md`.

- [ ] **Step 5: Run full test suite + pylint**

```bash
cd src/bridge && python -m pytest ../../tests/ -v
pylint $(git ls-files '*.py') --fail-under=10
```

- [ ] **Step 6: PR → merge → delete branch**

```bash
git push -u origin feature/sub-g-voice
gh pr create --title "feat(sub-g): voice interface — faster-whisper STT + Piper TTS"
```

---

### Task 5: Sub-H — Memory Decay & Feedback Loop

**Plan:** `docs/superpowers/plans/2026-03-24-sub-project-h-memory-decay.md`
**Migration:** 018 (after Task 0 patch)
**Shared files touched:** `scheduler_registry.py`, `scheduler_executor.py`

**Soft prerequisite:** Sub-B (Email/Calendar) and Sub-C (RSS) should be running in production so memory collections contain real data. This sub-project will still work without them but decay will have nothing to act on yet.

- [ ] **Step 1: Pull latest main**

```bash
git checkout main && git pull
```

- [ ] **Step 2: Create worktree**

```bash
git worktree add ../nanobot-sub-h -b feature/sub-h-memory-decay
cd ../nanobot-sub-h
```

- [ ] **Step 3: Verify migration number in plan is 018**

```bash
grep "VERSION" docs/superpowers/plans/2026-03-24-sub-project-h-memory-decay.md | head -3
# Expected: VERSION = 18
```

- [ ] **Step 4: Execute plan**

Use `superpowers:subagent-driven-development` with plan `docs/superpowers/plans/2026-03-24-sub-project-h-memory-decay.md`.

- [ ] **Step 5: Run full test suite + pylint**

```bash
cd src/bridge && python -m pytest ../../tests/ -v
pylint $(git ls-files '*.py') --fail-under=10
```

- [ ] **Step 6: PR → merge → delete branch**

```bash
git push -u origin feature/sub-h-memory-decay
gh pr create --title "feat(sub-h): memory decay scoring + feedback learning loop"
```

---

### Task 6: Sub-I — PWA Mobile ✅ IMPLEMENTED

**Plan:** `docs/superpowers/plans/2026-03-24-sub-project-i-pwa-mobile.md`
**Migration:** 019
**Branch:** `feature/sub-i-pwa` — pushed, PR open

- [x] **Step 1–5: Implemented and reviewed**
- [ ] **Step 6: PR → merge → delete branch**

```bash
# PR open at https://github.com/Draeyo/nanobot-stack/compare/feature/sub-i-pwa
```

---

### Task 7: Sub-J — GitHub & Obsidian Integrations ✅ IMPLEMENTED

**Plan:** `docs/superpowers/plans/2026-03-24-sub-project-j-dev-integrations.md`
**Migration:** 020
**Branch:** `feature/sub-j-dev-integrations` — pushed, PR open

- [x] **Step 1–5: Implemented and reviewed**
- [ ] **Step 6: PR → merge → delete branch**

```bash
# PR open at https://github.com/Draeyo/nanobot-stack/compare/feature/sub-j-dev-integrations
```

---

### Task 8: Sub-K — Browser Automation ✅ IMPLEMENTED

**Plan:** `docs/superpowers/plans/2026-03-24-sub-project-k-browser-automation.md`
**Migration:** 021
**Branch:** `feature/sub-k-browser-automation` — pushed, PR open
**Tests:** 357 passing — pylint 10.00/10

Notable implementation decisions:
- `seed_default_trust_policies()` uses direct SQLite UPDATE on `te.TRUST_DB_PATH` to set `auto_promote_after` deterministically (trust_engine API doesn't expose this field)
- Playwright guard: `not PLAYWRIGHT_AVAILABLE and async_playwright is None` (AND, not OR — tests mock `async_playwright` while `PLAYWRIGHT_AVAILABLE=False`)
- Domain allowlist: `allowed[4:] if allowed.startswith("www.") else allowed` (not `lstrip` which corrupts entries)

- [x] **Step 1–5: Implemented, spec-reviewed, quality-reviewed**
- [ ] **Step 6: PR → merge → delete branch**

```bash
# PR open at https://github.com/Draeyo/nanobot-stack/compare/feature/sub-k-browser-automation
```

---

### Task 9: Sub-L — Encryption At-Rest ✅ IMPLEMENTED ⚠️ FINAL — run last

**Plan:** `docs/superpowers/plans/2026-03-24-sub-project-l-encryption-at-rest.md`
**Migration:** none (field-level encryption, no DDL changes)
**Branch:** `feature/sub-l-encryption-at-rest` — pushed, PR open
**Tests:** 396 passing (39 new) — pylint 10.00/10

Notable implementation decisions:
- Two domain keys via HKDF context: `sqlite-v1` and `qdrant-v1`
- Encrypted format: `enc:v1:<base64url_no_padding(nonce[12] + ciphertext + tag[16])>`
- `decrypt_field()` passthrough for non-`enc:v1:` values (backward compat)
- All `_migration_job.update()` calls protected by `_migration_lock`
- `asyncio.get_running_loop().run_in_executor()` (not deprecated `get_event_loop()`)
- Per-row/per-point error isolation in all migration paths

- [x] **Step 1–5: Implemented, spec-reviewed, quality-reviewed**
- [ ] **Step 6: PR → merge → delete branch**

```bash
# PR open at https://github.com/Draeyo/nanobot-stack/compare/feature/sub-l-encryption-at-rest
```

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
