# Nanobot-Stack v10 Evolution Design

**Date:** 2026-03-23
**Status:** Draft
**Scope:** Mémoire procédurale, trust engine, sub-agents, routing intelligent, optimisation coûts
**Context:** Assistant personnel self-hosted, budget 50-100€/mois API LLM

---

## 1. Contexte & Motivation

Le nanobot-stack v9 est une plateforme mature avec mémoire multi-couche (Qdrant + knowledge graph + feedback), routing adaptatif, 3 canaux (Discord/Telegram/WhatsApp), et exécution d'outils sandboxée. Cette évolution vise à transformer l'assistant en un agent véritablement autonome qui :

1. **Apprend continuellement** — workflows, préférences, connaissances
2. **Agit avec confiance calibrée** — trust levels configurables par action
3. **Orchestre des sub-agents spécialisés** — délégation intelligente
4. **Optimise ses coûts** — cache sémantique, routing local-first, budget tokens

## 2. Approche Retenue : Hybride (Couche Agent Additive)

Couche `AgentBase` + `Orchestrator` par-dessus les modules existants. Les nouvelles features sont des modules standalone qui appellent les fonctions existantes (`run_chat_task`, `run_shell_command`, etc.). Le planner évolue, n'est pas remplacé. Tout est rétrocompatible et opt-in via env vars.

**Pourquoi pas un refactor complet ?** Pour un usage single-user, le risque de régression et le temps avant livraison (3-4 mois) ne justifient pas une réécriture. L'approche hybride livre de la valeur dès la semaine 1 et converge naturellement vers une architecture propre.

---

## 3. Phase 1 — Trust Engine & Mémoire Procédurale (Semaines 1-3)

### 3.1 Trust Engine

**Nouveau fichier :** `src/bridge/trust_engine.py`
**Nouvelle base :** `state/trust.db`

#### Modèle de données

```sql
CREATE TABLE trust_policies (
    action_type TEXT PRIMARY KEY,
    trust_level TEXT NOT NULL DEFAULT 'approval_required',
    auto_promote_after INTEGER DEFAULT 0,
    successful_executions INTEGER DEFAULT 0,
    failed_executions INTEGER DEFAULT 0,
    last_promoted_at TEXT DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE trust_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type TEXT NOT NULL,
    action_detail TEXT NOT NULL,
    trust_level TEXT NOT NULL,
    outcome TEXT NOT NULL,
    rollback_info TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
```

#### Niveaux de confiance

| Niveau | Comportement |
|--------|-------------|
| `auto` | Exécution immédiate, notification optionnelle |
| `notify_then_execute` | Notification envoyée, exécution après 60s sauf annulation via `POST /trust/cancel/{audit_id}` |
| `approval_required` | Mise en file d'attente, exécution après approbation admin |
| `blocked` | Refusé systématiquement |

#### Auto-promotion

Après N exécutions réussies consécutives (`TRUST_AUTO_PROMOTE_THRESHOLD`, défaut: 20), le trust level monte d'un cran : `blocked → approval_required → notify_then_execute → auto`. Les échecs réinitialisent le compteur.

#### API

```
GET    /trust/policies              — liste toutes les policies
POST   /trust/policies/{type}       — modifier le trust level
GET    /trust/audit                 — historique audité
POST   /trust/promote/{type}        — promotion manuelle
```

#### Relation avec Elevated Shell

Le trust engine est une **couche de politique** que `elevated_shell.py` consulte, pas un système parallèle. Le flow devient :
1. `elevated_shell.propose_action()` appelle `trust_engine.get_trust_level("shell_write")`
2. Si `auto` → exécution directe (pas de mise en file)
3. Si `notify_then_execute` → notification + timer 60s avec endpoint `POST /trust/cancel/{id}`
4. Si `approval_required` → file d'approbation existante (comportement actuel)
5. Si `blocked` → refus immédiat

Un seul audit trail : `trust_audit` remplace le logging duplicatif. `elevated_actions.db` reste pour la file d'approbation uniquement.

#### Intégration

| Fichier existant | Modification |
|-----------------|-------------|
| `tools.py` → `run_shell_command()` | Wrapper `check_and_execute("shell_read", ...)` |
| `elevated_shell.py` → `propose_action()` | Consulte `trust_engine.get_trust_level()` avant mise en file |
| `tools.py` → `web_fetch()` | Wrapper `check_and_execute("web_fetch", ...)` |
| `tools.py` → `send_notification()` | Wrapper `check_and_execute("notify", ...)` |

#### Settings

| Variable | Défaut | Description |
|----------|--------|-------------|
| `TRUST_ENGINE_ENABLED` | `true` | Activer le trust engine |
| `TRUST_DEFAULT_LEVEL` | `approval_required` | Niveau par défaut pour nouvelles actions |
| `TRUST_AUTO_PROMOTE_THRESHOLD` | `20` | Exécutions réussies avant promotion |
| `TRUST_NOTIFY_CHANNEL` | _(vide)_ | Webhook pour notifications trust |
| `TRUST_ROLLBACK_WINDOW_HOURS` | `24` | Fenêtre de rollback |

---

### 3.2 Mémoire Procédurale

**Nouveau fichier :** `src/bridge/procedural_memory.py`
**Nouvelle base :** `state/procedural_memory.db`
**Nouvelle collection Qdrant :** `procedural_workflows`

#### Modèle de données

```sql
CREATE TABLE action_sequences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger_pattern TEXT NOT NULL UNIQUE,
    trigger_embedding_id TEXT DEFAULT '',
    steps_json TEXT NOT NULL,
    frequency INTEGER DEFAULT 1,
    last_observed TEXT NOT NULL,
    last_executed TEXT DEFAULT '',
    success_rate REAL DEFAULT 1.0,
    confidence REAL DEFAULT 0.0,
    auto_suggest BOOLEAN DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE action_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    action TEXT NOT NULL,
    params_json TEXT NOT NULL,
    result_summary TEXT DEFAULT '',
    timestamp TEXT NOT NULL
);
```

#### Flux

1. **Observation** : après chaque exécution d'outil, `log_action()` enregistre l'action
2. **Détection** : `detect_patterns()` s'exécute en tâche de fond, uniquement après 10+ nouvelles actions depuis la dernière analyse, avec une fenêtre glissante de 100 actions maximum (pas de scan illimité)
3. **Matching** : à chaque nouvelle requête, `match_workflow()` cherche dans Qdrant si un workflow similaire existe (cosine > 0.85)
4. **Suggestion** : si confiance > 0.7, le workflow est proposé à l'utilisateur
5. **Replay** : `execute_workflow()` rejoue les étapes (soumises au trust engine)

#### Intégration

| Point d'intégration | Action |
|--------------------|--------|
| `planner.py` → `execute_step()` | Appeler `log_action()` après chaque étape |
| `tools.py` → `run_shell_command()` | Appeler `log_action()` après exécution |
| `extensions.py` → `/conversation-hook` | Incrémenter compteur actions ; si >= 10, lancer `detect_patterns()` en background |
| `extensions.py` → `/context-prefetch` | Appeler `match_workflow()` pour suggestions |

---

### 3.3 Profil Utilisateur Enrichi

**Fichier modifié :** `src/bridge/user_profile.py`

Extension du `DEFAULT_PROFILE` :

```python
DEFAULT_PROFILE = {
    "name": "", "language": "auto", "style": "concise and technical",
    "expertise": [], "context": "", "preferences": {},
    "communication": {
        "tone": "professional",
        "verbosity": "concise",
        "format_preference": "markdown",
        "code_style": {},
    },
    "tool_preferences": {
        "preferred_shell": "bash",
        "default_search_collections": [],
    },
    "schedule": {
        "timezone": "",
        "working_hours": "",
        "notification_preferences": {},
    },
    "learning_log": [],
}
```

Le `PROFILE_UPDATE_PROMPT` est enrichi pour extraire ces signaux de préférence plus fins. Un `learning_log` append-only trace l'évolution des préférences avec timestamps.

---

### 3.4 Graphe de Connaissances Enrichi

**Fichier modifié :** `src/bridge/knowledge_graph.py`

#### Nouvelles colonnes

```sql
ALTER TABLE entities ADD COLUMN updated_at TEXT DEFAULT '';
ALTER TABLE entities ADD COLUMN confidence REAL DEFAULT 1.0;
ALTER TABLE entities ADD COLUMN source TEXT DEFAULT 'conversation';
ALTER TABLE entities ADD COLUMN aliases TEXT DEFAULT '[]';

ALTER TABLE relations ADD COLUMN last_confirmed TEXT DEFAULT '';
ALTER TABLE relations ADD COLUMN source TEXT DEFAULT 'conversation';
ALTER TABLE relations ADD COLUMN confidence REAL DEFAULT 1.0;
```

#### Nouveaux types

- **Entités** : `event`, `deadline`, `location`, `tool`, `workflow`, `preference`
- **Relations** : `scheduled_for`, `blocked_by`, `prefers`, `replaced_by`, `part_of`, `owns`

#### Nouvelles fonctions

- `merge_entity(name1, name2)` — fusion de doublons
- `query_by_type(entity_type, limit)` — requête par type
- `temporal_query(entity, time_range)` — relations dans une période
- `get_subgraph(entity, depth=2)` — traversée multi-hop

---

## 4. Phase 2 — Architecture Sub-Agents (Semaines 3-5)

### 4.1 Agent Base

**Nouveau répertoire :** `src/bridge/agents/`
**Nouveau fichier :** `src/bridge/agents/base.py`

```python
class AgentBase:
    name: str
    description: str
    tools: list[str]
    trust_overrides: dict
    max_steps: int = 10

    async def run(self, task: str, context: dict) -> AgentResult: ...
    def _build_system_prompt(self, context: dict) -> str: ...
    def _select_tools(self, task: str) -> list[dict]: ...

@dataclass
class AgentResult:
    status: str           # 'completed', 'needs_approval', 'failed', 'delegated'
    output: str
    actions_taken: list
    cost_tokens: int
    sub_results: list
    artifacts: dict
```

### 4.2 Orchestrateur

**Nouveau fichier :** `src/bridge/agents/orchestrator.py`

Décompose les tâches complexes en sub-tasks avec assignation d'agent :

```json
{
  "goal": "...",
  "subtasks": [
    {"id": 1, "agent": "self|ops", "task": "...", "depends_on": [], "priority": "high"}
  ],
  "estimated_cost": "low|medium|high"
}
```

- L'orchestrateur traduit ses subtasks au format planner existant (`{"steps": [{"action": ..., "input": ...}]}`) via une couche `_subtask_to_plan_step()` avant de les passer à `execute_plan_parallel()`
- Les endpoints existants (`/plan`, `/execute-plan`) restent inchangés
- Nouveau endpoint : `POST /agent/run`

### 4.3 Agent Ops/SysAdmin

**Nouveau fichier :** `src/bridge/agents/ops_agent.py`

SRE personnel avec accès aux commandes diagnostiques étendues :

```python
# Nouvelles commandes read-only ajoutées à l'allowlist
"ss": True, "ps": ["aux"], "top": ["-bn1"],
"netstat": ["-tlnp"], "lsof": ["-i"],
"du": ["-sh"], "last": True, "w": True
```

- Consulte la collection `ops_runbooks` avant toute action
- Exécute les actions via le trust engine
- Escalade vers l'utilisateur pour les actions à haut risque

### 4.4 Agent Registry

**Nouveau fichier :** `src/bridge/agents/__init__.py`

```python
AGENT_REGISTRY = {
    "orchestrator": OrchestratorAgent,
    "ops": OpsAgent,
}
```

#### API

```
POST   /agent/run        — exécuter une tâche via l'orchestrateur
GET    /agent/status      — agents disponibles et capacités
GET    /agent/history     — historique d'exécution avec coûts
```

---

## 5. Phase 3 — Routing Intelligent & Coûts (Semaines 5-7)

### 5.1 Cache Sémantique

**Nouveau fichier :** `src/bridge/semantic_cache.py`
**Nouvelle collection Qdrant :** `semantic_cache`

- L1 : cache exact existant (`LLMResponseCache`, en mémoire)
- L2 : cache sémantique Qdrant (cosine > 0.92)
- Estimation : 20-30% d'économie tokens sur patterns répétés

```python
semantic_cache_get(task_type, query, threshold=0.92) -> dict | None
semantic_cache_put(task_type, query, response, ttl=600)
semantic_cache_invalidate(task_type=None)
```

| Variable | Défaut | Description |
|----------|--------|-------------|
| `SEMANTIC_CACHE_ENABLED` | `false` | Activer le cache sémantique |
| `SEMANTIC_CACHE_THRESHOLD` | `0.92` | Seuil de similarité cosine |
| `SEMANTIC_CACHE_TTL` | `86400` | TTL en secondes (défaut : 24h, adapté à un usage personnel avec patterns récurrents) |

### 5.2 Classificateur Étendu

**Fichier modifié :** `src/bridge/query_classifier.py`

Extension de 9 à 15 types :

| Nouveau type | Route modèle | Coût |
|-------------|--------------|------|
| `code_write` | `code_reasoning` (Sonnet) | Premium |
| `code_review` | `code_reasoning` (Sonnet) | Premium |
| `code_explain` | `code_reasoning` (Sonnet) | Premium |
| `ops_query` | `incident_triage` (mini) | Cheap |
| `ops_action` | `tool_planning` (reasoning) | Medium |
| `web_research` | `retrieval_answer` | Medium |
| `notification` | `fallback_general` (mini) | Cheap |

### 5.3 Budget Tokens

**Nouveau fichier :** `src/bridge/token_budget.py`
**Nouvelle base :** `state/token_budgets.db`

```sql
CREATE TABLE token_budgets (
    period TEXT PRIMARY KEY,
    budget_tokens INTEGER NOT NULL,
    used_tokens INTEGER DEFAULT 0,
    budget_cost_cents INTEGER DEFAULT 0,
    used_cost_cents INTEGER DEFAULT 0,
    reset_at TEXT NOT NULL
);

CREATE TABLE token_usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT DEFAULT '',
    operation_type TEXT NOT NULL DEFAULT 'chat',  -- 'chat', 'embedding', 'classification'
    task_type TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    estimated_cost_cents REAL NOT NULL,
    timestamp TEXT NOT NULL
);
```

**Table de coûts estimés (par 1M tokens) :**

| Modèle | Input | Output |
|--------|-------|--------|
| gpt-4.1-mini | $0.04 | $0.16 |
| gpt-4.1 | $0.20 | $0.80 |
| claude-sonnet-4 | $0.30 | $1.50 |
| ollama/* | $0 | $0 |

**Downgrade automatique :** quand `budget_pressure > 0.8`, le routing bascule vers Ollama pour les tâches compatibles.

### 5.4 Routing Local-First

**Fichier modifié :** `src/config/model_router.json`

Nouvelles routes local-first :

```json
"general_chat": ["local_fast", "cheap_rewrite", "cheap_rewrite_backup"],
"translation": ["local_fast", "translation_primary", "translation_backup"],
"memory_lookup": ["local_fast", "cheap_rewrite", "cheap_rewrite_backup"]
```

**Fichier modifié :** `src/bridge/adaptive_router.py`

Nouveau paramètre `budget_pressure` dans `get_model_ranking()` : à 0.5+, les modèles locaux reçoivent un bonus de score ; à 0.8+, ils sont systématiquement préférés sauf pour les tâches premium-only.

---

## 6. Phase 4 — Admin UI & Observabilité (Semaines 7-8)

**Fichier modifié :** `src/bridge/admin_ui.py`

Nouveaux onglets :

| Onglet | Contenu |
|--------|---------|
| **Trust Policies** | Table des actions avec dropdowns trust level, compteurs, contrôles de promotion |
| **Cost Dashboard** | Chart.js temps-réel, breakdown jour/semaine par modèle, projection mensuelle, alertes budget |
| **Procedural Workflows** | Liste des workflows appris, patterns déclencheurs, séquences d'étapes, confiance, toggles |
| **Agent Status** | Agents disponibles, exécutions récentes, coût tokens par agent |

---

## 7. Phase 5 — Migration & Polish (Semaines 8-9)

### 7.1 Migration

**Nouveau fichier :** `migrations/v10_evolution.py`

- Création des nouvelles tables SQLite (trust, procedural memory, token budgets)
- ALTER TABLE sur knowledge_graph.db (nouvelles colonnes)
- Création des collections Qdrant (semantic_cache, procedural_workflows)
- Seed des trust policies par défaut
- Idempotent (`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN` avec `try/except OperationalError` pour compatibilité SQLite < 3.35)

### 7.2 Tests d'intégration

- Trust engine avec le flow elevated shell existant
- Détection de patterns après conversations multi-étapes
- Taux de hit du cache sémantique
- Délégation orchestrateur → agent ops
- Enforcement budget et downgrade modèle
- Vérification que tous les endpoints existants sont inchangés

---

## 8. Résumé des Fichiers

### Nouveaux fichiers (9)

| Fichier | Rôle |
|---------|------|
| `src/bridge/trust_engine.py` | Trust levels configurables avec auto-promotion |
| `src/bridge/procedural_memory.py` | Observation, détection de patterns, replay de workflows |
| `src/bridge/semantic_cache.py` | Cache LLM par similarité sémantique |
| `src/bridge/token_budget.py` | Tracking et enforcement budget tokens/coûts |
| `src/bridge/agents/__init__.py` | Registre des agents |
| `src/bridge/agents/base.py` | AgentBase et AgentResult |
| `src/bridge/agents/orchestrator.py` | Orchestrateur hiérarchique |
| `src/bridge/agents/ops_agent.py` | Agent SRE/SysAdmin |
| `migrations/v10_evolution.py` | Script de migration |

### Fichiers modifiés (12)

| Fichier | Changements |
|---------|------------|
| `src/bridge/app.py` | Wire trust engine dans `run_chat_task()`, budget checks, mount new routers |
| `src/bridge/extensions.py` | Nouveaux endpoints trust, agents, workflows, cost dashboard |
| `src/bridge/tools.py` | Wrapper trust engine sur `run_shell_command()` et `web_fetch()` |
| `src/bridge/elevated_shell.py` | Intégration trust engine pour auto-exécution |
| `src/bridge/planner.py` | Appels `log_action()` après chaque étape |
| `src/bridge/query_classifier.py` | Extension à 15 types de tâches |
| `src/bridge/adaptive_router.py` | Ranking cost-aware avec `budget_pressure` |
| `src/bridge/knowledge_graph.py` | Nouvelles colonnes, types, fonctions merge/temporal |
| `src/bridge/user_profile.py` | Structure de préférences enrichie |
| `src/bridge/token_optimizer.py` | Intégration avec token_budget |
| `src/bridge/admin_ui.py` | 4 nouveaux onglets |
| `src/bridge/settings_registry.py` | ~20 nouveaux settings |

### Nouvelles bases de données (3 SQLite + 2 Qdrant)

| Base | Tables |
|------|--------|
| `state/trust.db` | `trust_policies`, `trust_audit` |
| `state/procedural_memory.db` | `action_sequences`, `action_log` |
| `state/token_budgets.db` | `token_budgets`, `token_usage_log` |
| Qdrant `semantic_cache` | Cache LLM par similarité |
| Qdrant `procedural_workflows` | Embeddings des triggers de workflows |

---

## 9. Rétrocompatibilité

Toutes les features sont opt-in via env vars :

| Variable | Défaut | Impact si désactivé |
|----------|--------|-------------------|
| `TRUST_ENGINE_ENABLED` | `true` | Tous les niveaux démarrent à `approval_required` (comportement actuel) |
| `PROCEDURAL_MEMORY_ENABLED` | `false` | Aucune observation/suggestion de workflow |
| `SEMANTIC_CACHE_ENABLED` | `false` | Cache exact seul (comportement actuel) |
| `TOKEN_BUDGET_ENABLED` | `false` | Pas de plafond ni de downgrade |
| `AGENT_ORCHESTRATOR_ENABLED` | `false` | Planner v9 seul (comportement actuel) |

Tous les endpoints existants (`/plan`, `/execute-plan`, `/smart-chat`, etc.) restent inchangés.

---

## 10. Estimation Coûts dans le Budget 50-100€/mois

| Optimisation | Économie estimée |
|-------------|-----------------|
| Cache sémantique | 20-30% de tokens en moins sur patterns répétés |
| Routing local-first (Ollama) | `general_chat`, `translation`, `memory_lookup` → gratuit |
| Budget manager + downgrade auto | Plafond dur, pas de surprise |
| Replay workflows procéduraux | Élimine les appels LLM planning pour workflows connus |

À tarification actuelle, 50-70€/mois couvre ~3-5M tokens/jour GPT-4.1-mini + ~500K tokens/jour Claude Sonnet 4, largement suffisant pour un usage personnel.

---

## 11. Vérification

### Comment tester les changements

1. **Trust engine** : Proposer une commande shell → vérifier qu'elle respecte le trust level configuré → tester l'auto-promotion après N exécutions
2. **Mémoire procédurale** : Exécuter la même séquence d'actions 3 fois → vérifier la détection du pattern → tester la suggestion au prochain trigger similaire
3. **Cache sémantique** : Poser la même question reformulée → vérifier le cache hit → mesurer la latence vs un appel LLM
4. **Orchestrateur** : Soumettre une tâche complexe multi-domaine → vérifier la décomposition en sub-tasks → vérifier la délégation à l'agent ops
5. **Budget** : Configurer un plafond bas → vérifier le downgrade vers Ollama → vérifier l'alerte dans l'admin UI
6. **Régression** : Exécuter le selftest existant (`nanobot-stack-selftest`) → tous les endpoints v9 doivent passer sans modification
