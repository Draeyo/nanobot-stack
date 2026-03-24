# Nanobot-Stack — Index des Features & Roadmap

**Date :** 2026-03-24
**Statut :** Document vivant
**Scope :** Vue d'ensemble de toutes les features planifiées et implémentées

---

## Vue d'ensemble

Nanobot-stack est un assistant personnel self-hosted à single-user (usage francophone, budget 50-100€/mois API LLM). L'architecture repose sur :

- **Bridge FastAPI** (`src/bridge/`) — cœur du système, expose les endpoints REST
- **Qdrant** — base vectorielle pour la mémoire sémantique multi-couche
- **SQLite** — état local (jobs, trust, mémoire procédurale, budget)
- **Multi-canal** — ntfy, Telegram, Discord, WhatsApp
- **LiteLLM + AdaptiveRouter** — routing intelligent vers les modèles LLM

---

## Statut des features

| Légende | Signification |
|---------|--------------|
| ✅ Implémenté | Code mergé sur `main`, fonctionnel |
| 🔧 Partiel | Fichier créé, intégration incomplète |
| 📋 Spécifié | Spec rédigée, non implémenté |
| 💡 Planifié | Mentionné, spec à écrire |

---

## Phase 1 — Core & Mémoire (v9, base)

| Feature | Fichier | Statut | Description |
|---------|---------|--------|-------------|
| Chat adaptatif | `app.py` | ✅ | Multi-tour avec contexte, routing par type de tâche |
| Mémoire multi-couche | `conversation_memory.py`, `working_memory.py` | ✅ | Court/long terme, Qdrant + SQLite |
| Knowledge Graph | `knowledge_graph.py` | ✅ | Entités, relations, graph traversal |
| Recherche hybride | `app.py` | ✅ | Dense + sparse (BM25) sur toutes collections |
| Planner | `planner.py` | ✅ | Décomposition tâches complexes, exécution parallèle |
| Shell sandboxé | `elevated_shell.py`, `tools.py` | ✅ | Commandes read-only + write avec approbation |
| 3 canaux (chat) | `channels/` | ✅ | Discord, Telegram, WhatsApp (bidirectionnel) |
| Admin UI | `admin_ui.py` | ✅ | Interface Alpine.js, ~11 onglets |
| Rate limiter | `rate_limiter.py` | ✅ | Par user/IP |
| Audit | `audit.py` | ✅ | Log actions admin |
| Export | `export.py` | ✅ | Export conversations |
| Code interpreter | `code_interpreter.py` | ✅ | Exécution Python sandboxée |
| PII filter | `pii_filter.py` | ✅ | Anonymisation avant stockage/livraison |
| Compression contexte | `context_compression.py` | ✅ | Résumé automatique fenêtre contexte |
| Streaming | `streaming.py` | ✅ | SSE pour réponses longues |
| DM Pairing | `dm_pairing.py` | ✅ | Approbation utilisateurs par canal |

---

## Phase 2 — v10 Evolution

Spec complète : [`2026-03-23-nanobot-v10-evolution-design.md`](2026-03-23-nanobot-v10-evolution-design.md)

### 2.1 Trust Engine

Spec : [`2026-03-24-trust-engine.md`](2026-03-24-trust-engine.md)

| Feature | Fichier | Statut | Description |
|---------|---------|--------|-------------|
| Niveaux de confiance | `trust_engine.py` | ✅ | `auto`, `notify_then_execute`, `approval_required`, `blocked` |
| Auto-promotion | `trust_engine.py` | ✅ | Promotion après N exécutions réussies consécutives |
| Audit trail | `trust.db` | ✅ | `trust_policies` + `trust_audit` |
| API trust | `extensions.py` | ✅ | `GET/POST /trust/policies`, `GET /trust/audit` |
| Intégration shell | `elevated_shell.py` | ✅ | `propose_action()` consulte trust level |
| Onglet Admin UI | `admin_ui.py` | 🔧 | Dropdowns trust level, compteurs, promotion manuelle |

### 2.2 Mémoire Procédurale

Spec : [`2026-03-24-procedural-memory.md`](2026-03-24-procedural-memory.md)

| Feature | Fichier | Statut | Description |
|---------|---------|--------|-------------|
| Observation actions | `procedural_memory.py` | ✅ | `log_action()` après chaque outil |
| Détection patterns | `procedural_memory.py` | ✅ | Analyse fenêtre glissante 100 actions, seuil 10 nouvelles |
| Matching workflows | `procedural_memory.py` | ✅ | Qdrant cosine > 0.85 sur `procedural_workflows` |
| Suggestion | `procedural_memory.py` | ✅ | Proposé si confiance > 0.7 |
| Replay | `procedural_memory.py` | ✅ | Rejoue les étapes via trust engine |
| Intégration planner | `planner.py` | 🔧 | `log_action()` après chaque étape |
| Onglet Admin UI | `admin_ui.py` | 🔧 | Liste workflows, confiance, toggles |

### 2.3 Profil Utilisateur Enrichi

| Feature | Fichier | Statut | Description |
|---------|---------|--------|-------------|
| Préférences communication | `user_profile.py` | ✅ | Ton, verbosité, format, style code |
| Préférences outils | `user_profile.py` | ✅ | Shell préféré, collections par défaut |
| Schedule / timezone | `user_profile.py` | ✅ | Timezone, heures de travail, prefs notif |
| Learning log | `user_profile.py` | ✅ | Historique évolution préférences |

### 2.4 Knowledge Graph Enrichi

| Feature | Fichier | Statut | Description |
|---------|---------|--------|-------------|
| Nouvelles colonnes | `knowledge_graph.py` | ✅ | `confidence`, `source`, `aliases`, `last_confirmed` |
| Nouveaux types entités | `knowledge_graph.py` | ✅ | `event`, `deadline`, `location`, `tool`, `workflow`, `preference` |
| Nouvelles relations | `knowledge_graph.py` | ✅ | `scheduled_for`, `blocked_by`, `prefers`, `replaced_by`, `part_of`, `owns` |
| Merge entités | `knowledge_graph.py` | ✅ | `merge_entity(name1, name2)` |
| Requête temporelle | `knowledge_graph.py` | ✅ | `temporal_query(entity, time_range)` |
| Subgraph traversal | `knowledge_graph.py` | ✅ | `get_subgraph(entity, depth=2)` |

### 2.5 Architecture Sub-Agents

Spec : [`2026-03-24-sub-agents-architecture.md`](2026-03-24-sub-agents-architecture.md)

| Feature | Fichier | Statut | Description |
|---------|---------|--------|-------------|
| AgentBase | `agents/base.py` | ✅ | Interface commune, `AgentResult` |
| Orchestrateur | `agents/orchestrator.py` | ✅ | Décomposition tâches, assignation agents |
| Agent Ops | `agents/ops_agent.py` | ✅ | SRE/SysAdmin, accès runbooks, commandes diagnostiques |
| Agent Registry | `agents/__init__.py` | ✅ | `AGENT_REGISTRY = {"orchestrator": ..., "ops": ...}` |
| API agents | `extensions.py` | ✅ | `POST /agent/run`, `GET /agent/status`, `GET /agent/history` |
| Onglet Admin UI | `admin_ui.py` | 🔧 | Agents disponibles, exécutions récentes, coût |

### 2.6 Cache Sémantique, Budget Tokens & Routing

Spec : [`2026-03-24-semantic-cache-token-budget-routing.md`](2026-03-24-semantic-cache-token-budget-routing.md)

| Feature | Fichier | Statut | Description |
|---------|---------|--------|-------------|
| Cache L2 Qdrant | `semantic_cache.py` | ✅ | Cosine > 0.92, TTL configurable |
| Init hook | `app.py` | ✅ | `init_semantic_cache()` au démarrage |
| Env var opt-in | `.env` | ✅ | `SEMANTIC_CACHE_ENABLED=false` par défaut |

### 2.7 Budget Tokens

| Feature | Fichier | Statut | Description |
|---------|---------|--------|-------------|
| Tracking usage | `token_budget.py` | ✅ | Par session, operation_type, model |
| Enforcement budget | `token_budget.py` | ✅ | Downgrade Ollama si `budget_pressure > 0.8` |
| Table coûts | `token_budget.py` | ✅ | Coûts estimés par modèle ($/1M tokens) |
| Intégration router | `adaptive_router.py` | 🔧 | `budget_pressure` dans `get_model_ranking()` |
| Onglet Admin UI | `admin_ui.py` | 🔧 | Chart.js coûts, projection mensuelle, alertes |

### 2.8 Routing Local-First

| Feature | Fichier | Statut | Description |
|---------|---------|--------|-------------|
| Classificateur étendu | `query_classifier.py` | ✅ | 15 types (vs 9) dont `code_write`, `ops_query`, `notification` |
| Routes local-first | `model_router.json` | 🔧 | `general_chat`, `translation`, `memory_lookup` → Ollama |
| Budget pressure | `adaptive_router.py` | 🔧 | Bonus score modèles locaux à partir de 0.5+ |

---

## Phase 3 — Sous-Projets Proactifs

### Sous-projet A — Scheduler & Briefing Matinal ✅

Spec : [`2026-03-23-scheduler-briefing-design.md`](2026-03-23-scheduler-briefing-design.md)
Plan : [`../plans/2026-03-23-scheduler-briefing.md`](../plans/2026-03-23-scheduler-briefing.md)

| Feature | Fichier | Statut | Description |
|---------|---------|--------|-------------|
| SchedulerManager | `scheduler.py` | ✅ | APScheduler + SQLAlchemyJobStore |
| JobExecutor | `scheduler_executor.py` | ✅ | Pipeline collect → LLM → PII → broadcast → Qdrant |
| JobRegistry | `scheduler_registry.py` | ✅ | 3 jobs système prédéfinis |
| BroadcastNotifier | `broadcast_notifier.py` | ✅ | Fan-out ntfy/Telegram/Discord/WhatsApp |
| API REST | `scheduler_api.py` | ✅ | CRUD + toggle + run + history |
| Admin UI onglet | `admin_ui.py` | ✅ | 11ème onglet, liste + formulaire + historique |
| Sections briefing | `scheduler_executor.py` | ✅ | `system_health`, `personal_notes`, `topics`, `reminders`, `weekly_summary`, `custom` |
| Startup cleanup | `scheduler.py` | ✅ | Reset jobs bloqués à "running" |
| Migration | `migrations/011_scheduler.py` | ✅ | Tables `scheduled_jobs` + `job_runs` |

### Sous-projet B — Intégration Email/Calendrier 📋

Spec : [`2026-03-24-sub-project-b-email-calendar.md`](2026-03-24-sub-project-b-email-calendar.md)

| Feature | Fichier | Statut | Description |
|---------|---------|--------|-------------|
| IMAP email fetch | `email_calendar.py` | 📋 | Emails non-lus via imaplib/TLS |
| CalDAV/ICS sync | `email_calendar.py` | 📋 | Événements du jour via caldav ou .ics |
| Collection Qdrant | `email_inbox`, `calendar_events` | 📋 | TTL 7j / 30j |
| Section `agenda` | `scheduler_executor.py` | 📋 | Événements calendrier dans briefing |
| Section `email_digest` | `scheduler_executor.py` | 📋 | Résumé emails importants dans briefing |
| API sync | `scheduler_api.py` ou nouveau | 📋 | Déclenchement sync manuelle |
| Migration | `migrations/012_email_calendar.py` | 📋 | Table `email_sync_log` |

**Dépendances Python :** `caldav>=1.3`, `icalendar>=5.0`

### Sous-projet C — Ingestion RSS/News 📋

Spec : [`2026-03-24-sub-project-c-rss-ingestion.md`](2026-03-24-sub-project-c-rss-ingestion.md)

| Feature | Fichier | Statut | Description |
|---------|---------|--------|-------------|
| Feed manager | `rss_ingestor.py` | 📋 | CRUD abonnements RSS (SQLite) |
| Pipeline ingestion | `rss_ingestor.py` | 📋 | fetch → parse → embed → Qdrant |
| Résumé LLM | `rss_ingestor.py` | 📋 | Un résumé par article (modèle cheap) |
| Collection Qdrant | `rss_articles` | 📋 | Titre, URL, résumé, catégorie, date |
| Section `rss_digest` | `scheduler_executor.py` | 📋 | Digest RSS dans briefing |
| Remplacement `topics` | `scheduler_executor.py` | 📋 | Source → `rss_articles` au lieu de `documents` |
| API REST | `rss_api.py` | 📋 | `/api/rss/feeds` CRUD + sync manuelle |
| Admin UI onglet | `admin_ui.py` | 📋 | 12ème onglet — liste feeds, statut sync |
| Sync schedulée | `scheduler_registry.py` | 📋 | Job système `*/30 * * * *` |
| Migration | `migrations/013_rss.py` | 📋 | Tables `rss_feeds`, `rss_entries` |

**Dépendances Python :** `feedparser>=6.0`

---

## Phase 4 — Features Avancées (Planifié)

### 4.1 Admin UI v2 — Onglets manquants 🔧

Spec : [`2026-03-24-admin-ui-v2.md`](2026-03-24-admin-ui-v2.md)

Les onglets suivants sont spécifiés dans le v10 evolution design mais pas encore implémentés :

| Onglet | Contenu | Statut |
|--------|---------|--------|
| Trust Policies | Table des actions, dropdowns trust level, auto-promotion controls | 🔧 |
| Cost Dashboard | Chart.js temps-réel, breakdown jour/semaine par modèle, projection mensuelle | 🔧 |
| Procedural Workflows | Liste workflows appris, confiance, toggles replay | 🔧 |
| Agent Status | Agents disponibles, exécutions récentes, coût par agent | 🔧 |

### 4.2 Sous-projet D — Recherche Web (SearXNG) 📋

Spec : [`2026-03-24-sub-project-d-web-search.md`](2026-03-24-sub-project-d-web-search.md)

| Feature | Fichier | Statut | Description |
|---------|---------|--------|-------------|
| WebSearchAgent | `agents/web_search_agent.py` | 📋 | Agent étendant AgentBase, interroge SearXNG |
| SearXNG Docker | `docker-compose.yml` | 📋 | Service SearXNG sur réseau privé |
| Tool `web_search` | `src/mcp/` | 📋 | Outil MCP pour OrchestratorAgent |
| Collection Qdrant | `web_search_results` | 📋 | Résultats cachés, TTL 6h |
| Section briefing | `scheduler_executor.py` | 📋 | Section `web_digest` — top résultats sur topics configurés |
| Table SQLite | `web_search_log` | 📋 | Historique requêtes, rate limiting |
| API REST | `web_search_api.py` | 📋 | `POST /tools/web-search` |

### 4.3 Sous-projet E — Ingestion Documents Locaux 📋

Spec : [`2026-03-24-sub-project-e-local-docs.md`](2026-03-24-sub-project-e-local-docs.md)

| Feature | Fichier | Statut | Description |
|---------|---------|--------|-------------|
| LocalDocIngestor | `local_doc_ingestor.py` | 📋 | Ingestion PDF, MD, TXT, DOCX |
| LocalDocWatcher | `local_doc_ingestor.py` | 📋 | Surveillance dossier en temps réel (watchdog) |
| Extension `docs_reference` | Qdrant | 📋 | Nouveaux champs payload : source_path, file_hash, chunk_index |
| Table SQLite | `docs_ingestion_log` | 📋 | Migration 014 |
| API REST | `local_docs_api.py` | 📋 | CRUD + status + upload manuel |

**Dépendances Python :** `pypdf>=3.0`, `python-docx>=1.0`, `watchdog>=3.0`

### 4.4 Sous-projet F — Backup & Restore Automatique 📋

Spec : [`2026-03-24-sub-project-f-backup-restore.md`](2026-03-24-sub-project-f-backup-restore.md)

| Feature | Fichier | Statut | Description |
|---------|---------|--------|-------------|
| BackupManager | `backup_manager.py` | 📋 | Snapshots Qdrant + SQLite, archive .tar.gz |
| Chiffrement archives | `backup_manager.py` | 📋 | AES-256 Fernet optionnel |
| Stockage S3 | `backup_manager.py` | 📋 | Backblaze B2 / AWS S3 / MinIO via boto3 |
| Job cron | `scheduler_registry.py` | 📋 | Backup quotidien à 3h par défaut |
| Script restore | `scripts/restore.sh` | 📋 | Restore manuel avec prompts de sécurité |
| Table SQLite | `backup_log` | 📋 | Migration 015 |
| API REST | `backup_api.py` | 📋 | Trigger, list, status, delete |

**Dépendances Python :** `cryptography>=42.0`, `boto3>=1.34` (optionnel si S3)

### 4.5 Sous-projet G — Interface Vocale (STT/TTS) 📋

Spec : [`2026-03-24-sub-project-g-voice-interface.md`](2026-03-24-sub-project-g-voice-interface.md)

| Feature | Fichier | Statut | Description |
|---------|---------|--------|-------------|
| VoiceProcessor | `voice_processor.py` | 📋 | Pipeline STT (faster-whisper) + TTS (Piper) |
| Docker Piper | `docker-compose.yml` | 📋 | Service `piper` sur réseau privé |
| Endpoint transcription | `voice_api.py` | 📋 | `POST /api/voice/transcribe` — audio → texte |
| Endpoint synthèse | `voice_api.py` | 📋 | `POST /api/voice/synthesize` — texte → audio MP3 |
| Endpoint chat vocal | `voice_api.py` | 📋 | `POST /api/voice/chat` — round-trip complet |
| Interface Admin UI | `admin_ui.py` | 📋 | Bouton micro dans l'onglet Chat |
| Table SQLite | `voice_sessions` | 📋 | Métriques sessions, pas d'audio stocké |

**Dépendances Python :** `faster-whisper>=1.0`

### 4.6 Sous-projet H — Memory Decay & Boucle de Feedback 📋

Spec : [`2026-03-24-sub-project-h-memory-decay.md`](2026-03-24-sub-project-h-memory-decay.md)

| Feature | Fichier | Statut | Description |
|---------|---------|--------|-------------|
| MemoryDecayManager | `memory_decay.py` | 📋 | Dégradation exponentielle + seuil suppression |
| FeedbackLearner | `feedback_learner.py` | 📋 | Ajustement routing depuis feedbacks utilisateur |
| Job cron hebdo | `scheduler_registry.py` | 📋 | Scan decay toutes les collections permanentes |
| Extension table `feedback` | `rag.db` | 📋 | Ajout colonnes query_type, model_used, correction_text |
| Table `routing_adjustments` | SQLite | 📋 | Scores d'ajustement par type/modèle |
| Table `memory_decay_log` | SQLite | 📋 | Audit suppressions (migration 016) |
| API REST | `memory_api.py` | 📋 | Decay stats, feedback, forget, routing adjustments |

### 4.7 Sous-projet I — Progressive Web App Mobile 📋

Spec : [`2026-03-24-sub-project-i-pwa-mobile.md`](2026-03-24-sub-project-i-pwa-mobile.md)

| Feature | Fichier | Statut | Description |
|---------|---------|--------|-------------|
| Manifest PWA | `static/manifest.json` | 📋 | Installable sur mobile, display:standalone |
| Service Worker | `static/sw.js` | 📋 | Cache assets, offline fallback, push handler |
| Interface mobile | `admin_ui.py` | 📋 | Vue chat responsive (< 768px) avec Alpine.js |
| Push Notifications | `push_notifications.py` | 📋 | VAPID keys, Web Push API |
| BroadcastNotifier | `broadcast_notifier.py` | 📋 | Nouveau canal `webpush` |
| Table SQLite | `push_subscriptions` | 📋 | Migration 017 |

**Dépendances Python :** `pywebpush>=2.0`

### 4.8 Sous-projet J — Intégrations Développeur (GitHub & Obsidian) 📋

Spec : [`2026-03-24-sub-project-j-dev-integrations.md`](2026-03-24-sub-project-j-dev-integrations.md)

| Feature | Fichier | Statut | Description |
|---------|---------|--------|-------------|
| GitHubSyncer | `dev_integrations.py` | 📋 | Sync PRs, issues, commits via API GitHub |
| ObsidianIngestor | `obsidian_ingestor.py` | 📋 | Extension LocalDocIngestor + YAML frontmatter + WikiLinks |
| Section briefing | `scheduler_executor.py` | 📋 | Section `dev_digest` — activité GitHub du jour |
| Extension `memory_projects` | Qdrant | 📋 | Nouveaux payloads `source:github` |
| Table SQLite | `github_sync_log` | 📋 | Migration 018 |
| Table SQLite | `obsidian_index` | 📋 | Backlinks WikiLink tracking |

**Dépendances Python :** `PyGithub>=2.0`

### 4.9 Sous-projet K — Browser Automation (Playwright) 📋

Spec : [`2026-03-24-sub-project-k-browser-automation.md`](2026-03-24-sub-project-k-browser-automation.md)

| Feature | Fichier | Statut | Description |
|---------|---------|--------|-------------|
| BrowserAgent | `agents/browser_agent.py` | 📋 | Agent Playwright headless, étend AgentBase |
| Docker Playwright | `docker-compose.yml` | 📋 | Sidecar Chromium sandboxé |
| Trust integration | `trust_engine.py` | 📋 | Niveaux par action : read/fill/submit |
| Domain allowlist | `browser_agent.py` | 📋 | `BROWSER_ALLOWED_DOMAINS` — réseau restreint |
| Collection Qdrant | `browser_screenshots` | 📋 | Screenshots optionnels, TTL 24h |
| Table SQLite | `browser_action_log` | 📋 | Migration 019 |
| API REST | `browser_api.py` | 📋 | `POST /api/browser/run` + logs |

**Dépendances Python :** `playwright>=1.44`

### 4.10 Sous-projet L — Chiffrement At-Rest 📋

Spec : [`2026-03-24-sub-project-l-encryption-at-rest.md`](2026-03-24-sub-project-l-encryption-at-rest.md)

| Feature | Fichier | Statut | Description |
|---------|---------|--------|-------------|
| FieldEncryptor | `encryption.py` | 📋 | AES-256-GCM, HKDF, préfixe `enc:v1:` |
| Chiffrement SQLite | Injecteur dans tous les modules | 📋 | Champs sensibles : entities.value, feedback.correction |
| Chiffrement Qdrant | Dans chaque ingestor | 📋 | Payloads memory_personal, email_inbox, calendar_events |
| Key rotation | `encryption_api.py` | 📋 | Re-chiffrement en ligne |
| Migration enable/disable | `encryption_api.py` | 📋 | Idempotent, check préfixe enc:v1: |
| API REST | `encryption_api.py` | 📋 | Status, enable, disable, rotate, migration-status |
| Documentation FS | `README.md` | 📋 | eCryptfs/LUKS setup — approche recommandée |

**Dépendances Python :** `cryptography>=42.0` (déjà requis par sous-projet F)

---

## Collections Qdrant — Vue d'ensemble

| Collection | Usage | TTL | Sous-projet |
|------------|-------|-----|-------------|
| `docs_reference` | Documents de référence ingérés | Permanent | Core |
| `memory_personal` | Notes personnelles, mémoires | Permanent | Core |
| `ops_runbooks` | Runbooks SRE/ops | Permanent | Core |
| `memory_projects` | Contexte projets | Permanent | Core |
| `conversation_summaries` | Résumés conversations | Permanent | Core |
| `semantic_cache` | Cache requêtes LLM | 86400s (24h) | Core |
| `procedural_workflows` | Embeddings triggers workflows | Permanent | Core |
| `email_inbox` | Emails récents | 7 jours | B |
| `calendar_events` | Événements calendrier | 30 jours | B |
| `rss_articles` | Articles RSS ingérés | 30 jours | C |
| `web_search_results` | Résultats SearXNG cachés | 6h | D |
| `browser_screenshots` | Captures écran Browser Agent | 24h | K |

---

## Bases SQLite — Vue d'ensemble

| Base | Tables principales | Géré par | Sous-projet |
|------|--------------------|---------|-------------|
| `rag.db` | `knowledge_entities`, `knowledge_relations`, `feedback`, `dm_pairing` | `app.py` | Core |
| `scheduler.db` | `scheduled_jobs`, `job_runs`, `email_sync_log` | `scheduler.py` | A, B |
| `trust.db` | `trust_policies`, `trust_audit` | `trust_engine.py` | Core |
| `procedural_memory.db` | `action_sequences`, `action_log` | `procedural_memory.py` | Core |
| `token_budgets.db` | `token_budgets`, `token_usage_log` | `token_budget.py` | Core |
| `rss.db` | `rss_feeds`, `rss_entries` | `rss_ingestor.py` | C |
| `scheduler.db` | `web_search_log` | `web_search_api.py` | D |
| `scheduler.db` | `docs_ingestion_log` | `local_doc_ingestor.py` | E |
| `scheduler.db` | `backup_log` | `backup_manager.py` | F |
| `scheduler.db` | `voice_sessions` | `voice_processor.py` | G |
| `rag.db` | `memory_decay_log`, `routing_adjustments` | `memory_decay.py` | H |
| `scheduler.db` | `push_subscriptions` | `push_notifications.py` | I |
| `scheduler.db` | `github_sync_log`, `obsidian_index` | `dev_integrations.py` | J |
| `scheduler.db` | `browser_action_log` | `browser_agent.py` | K |

---

## Variables d'environnement — Vue d'ensemble

### Core
| Variable | Défaut | Description |
|----------|--------|-------------|
| `BRIDGE_TOKEN` | _(requis)_ | Token auth API |
| `QDRANT_URL` | `http://localhost:6333` | URL Qdrant |
| `MODEL_ROUTER_PATH` | `model_router.json` | Config routing LLM |
| `RAG_STATE_DIR` | `/opt/nanobot-stack/rag-bridge/state` | Répertoire état |

### Notifications & Canaux
| Variable | Défaut | Description |
|----------|--------|-------------|
| `NOTIFICATION_WEBHOOK_URL` | _(vide)_ | URL webhook ntfy |
| `TELEGRAM_BOT_TOKEN` | _(vide)_ | Token bot Telegram |
| `DISCORD_BOT_TOKEN` | _(vide)_ | Token bot Discord |
| `WHATSAPP_API_URL` | _(vide)_ | URL API WhatsApp |

### Trust Engine
| Variable | Défaut | Description |
|----------|--------|-------------|
| `TRUST_ENGINE_ENABLED` | `true` | Activer le trust engine |
| `TRUST_DEFAULT_LEVEL` | `approval_required` | Niveau par défaut |
| `TRUST_AUTO_PROMOTE_THRESHOLD` | `20` | Exécutions avant promotion |

### Cache & Budget
| Variable | Défaut | Description |
|----------|--------|-------------|
| `SEMANTIC_CACHE_ENABLED` | `false` | Cache sémantique L2 |
| `SEMANTIC_CACHE_THRESHOLD` | `0.92` | Seuil similarité cosine |
| `TOKEN_BUDGET_ENABLED` | `false` | Enforcement budget tokens |
| `AGENT_ORCHESTRATOR_ENABLED` | `false` | Sub-agents orchestrateur |

### Email/Calendrier *(sous-projet B)*
| Variable | Défaut | Description |
|----------|--------|-------------|
| `EMAIL_IMAP_HOST` | _(vide)_ | Serveur IMAP |
| `EMAIL_IMAP_USER` | _(vide)_ | Utilisateur IMAP |
| `EMAIL_IMAP_PASSWORD` | _(vide)_ | Mot de passe IMAP (TLS) |
| `EMAIL_IMAP_FOLDER` | `INBOX` | Dossier à surveiller |
| `CALENDAR_CALDAV_URL` | _(vide)_ | URL CalDAV |
| `CALENDAR_USERNAME` | _(vide)_ | Utilisateur CalDAV |
| `CALENDAR_PASSWORD` | _(vide)_ | Mot de passe CalDAV |
| `CALENDAR_ICS_PATH` | _(vide)_ | Chemin fichier .ics local |

### RSS *(sous-projet C)*
| Variable | Défaut | Description |
|----------|--------|-------------|
| `RSS_MAX_ARTICLES_PER_DIGEST` | `10` | Articles max par digest |
| `RSS_EMBED_FULL_TEXT` | `false` | Embedder le texte complet |

---

## Numérotation des migrations SQLite

| Migration | Table(s) | Sous-projet |
|-----------|----------|-------------|
| 011 | `scheduled_jobs`, `job_runs` | A |
| 012 | `email_sync_log` | B |
| 013 | `rss_feeds`, `rss_entries` | C |
| 014 | `docs_ingestion_log` | E |
| 015 | `backup_log` | F |
| 016 | `memory_decay_log`, `routing_adjustments` | H |
| 017 | `push_subscriptions` | I |
| 018 | `github_sync_log`, `obsidian_index` | J |
| 019 | `browser_action_log` | K |

---

## Ordre d'implémentation recommandé

```
✅ Phase 2 — v10 Evolution (implémentée)
✅ Sous-projet A — Scheduler/Briefing (implémenté, mergé)
📋 Sous-projet B — Email/Calendrier       [priorité haute]
📋 Sous-projet C — RSS/News               [priorité haute]
🔧 Admin UI v2 (4 onglets manquants)      [priorité haute]
📋 Sous-projet D — Recherche Web          [priorité haute]
📋 Sous-projet E — Ingestion Docs         [priorité haute]
📋 Sous-projet F — Backup & Restore       [priorité haute]
📋 Sous-projet G — Interface Vocale       [priorité moyenne]
📋 Sous-projet H — Memory Decay           [priorité moyenne]
📋 Sous-projet I — PWA Mobile             [priorité moyenne]
📋 Sous-projet J — Intégrations Dev       [priorité moyenne]
📋 Sous-projet K — Browser Automation     [priorité basse]
📋 Sous-projet L — Chiffrement At-Rest    [priorité basse]
```
