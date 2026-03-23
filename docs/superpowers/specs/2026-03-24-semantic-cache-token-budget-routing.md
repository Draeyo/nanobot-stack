# Spec : Cache Sémantique, Budget Tokens & Routing Local-First

**Date :** 2026-03-24
**Statut :** Implémenté ✅ (routing local-first 🔧 partiel)
**Projet :** nanobot-stack v10
**Scope :** Cache LLM L2 par similarité, budget tokens/coûts, classificateur étendu, downgrade automatique

---

## 1. Contexte & Objectifs

Avec un budget de 50-100€/mois, optimiser l'utilisation des tokens est critique. Trois leviers complémentaires :

1. **Cache sémantique (L2)** — réutiliser les réponses LLM pour des requêtes similaires (économie estimée 20-30%)
2. **Budget tokens** — tracker et enforcer un plafond quotidien, avec downgrade automatique vers Ollama
3. **Routing local-first** — diriger les tâches simples vers des modèles locaux/cheap, réserver les modèles premium aux tâches complexes

---

## 2. Cache Sémantique (`semantic_cache.py`)

### Architecture

```
SemanticCache
  ├── L1 : LLMResponseCache (token_optimizer.py) — exact match en mémoire
  └── L2 : SemanticCache (Qdrant) — similarité cosine >= SEMANTIC_CACHE_THRESHOLD

Flux :
chat_request → L1 hit? → retourner
             → L2 hit? → retourner + log cache hit
             → LLM call → stocker L1 + L2 → retourner
```

### API du module

```python
semantic_cache_get(task_type: str, query: str, threshold: float = 0.92) -> dict | None
semantic_cache_put(task_type: str, query: str, response: str, ttl: int = 86400)
semantic_cache_invalidate(task_type: str | None = None)
```

### Collection Qdrant `semantic_cache`

**Payload d'un point :**

```json
{
  "task_type": "general_chat",
  "query_text": "...",
  "response": "...",
  "created_at": "2026-03-24T08:00:00Z",
  "expires_at": "2026-03-25T08:00:00Z",
  "hit_count": 3
}
```

### Variables d'environnement

| Variable | Défaut | Description |
|----------|--------|-------------|
| `SEMANTIC_CACHE_ENABLED` | `false` | Activer le cache L2 |
| `SEMANTIC_CACHE_THRESHOLD` | `0.92` | Seuil cosine minimal pour hit |
| `SEMANTIC_CACHE_TTL` | `86400` | TTL en secondes (24h) |
| `SEMANTIC_CACHE_MAX_SIZE` | `1000` | Nombre max de points dans Qdrant |

### Initialisation

```python
# app.py
from semantic_cache import SEMANTIC_CACHE_ENABLED, init_semantic_cache
if SEMANTIC_CACHE_ENABLED:
    _sem_cache = init_semantic_cache(
        qdrant_client=qdrant,
        embed_fn=lambda t: embed_texts([t])[0][0]
    )
```

---

## 3. Budget Tokens (`token_budget.py`)

### Modèle de données

```sql
-- state/token_budgets.db

CREATE TABLE token_budgets (
    period          TEXT PRIMARY KEY,        -- ex: '2026-03-24' (jour)
    budget_tokens   INTEGER NOT NULL,
    used_tokens     INTEGER DEFAULT 0,
    budget_cost_cents INTEGER DEFAULT 0,
    used_cost_cents INTEGER DEFAULT 0,
    reset_at        TEXT NOT NULL
);

CREATE TABLE token_usage_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT DEFAULT '',
    operation_type      TEXT NOT NULL DEFAULT 'chat',  -- 'chat' | 'embedding' | 'classification'
    task_type           TEXT NOT NULL,
    model               TEXT NOT NULL,
    input_tokens        INTEGER NOT NULL,
    output_tokens       INTEGER NOT NULL,
    estimated_cost_cents REAL NOT NULL,
    timestamp           TEXT NOT NULL
);
```

### Table de coûts estimés (par 1M tokens)

| Modèle | Input | Output |
|--------|-------|--------|
| `gpt-4.1-mini` | $0.04 | $0.16 |
| `gpt-4.1` | $0.20 | $0.80 |
| `claude-sonnet-4` | $0.30 | $1.50 |
| `ollama/*` | $0 | $0 |

### Budget pressure

```python
budget_pressure = used_tokens / budget_tokens  # 0.0 → 1.0

# Comportement selon la pression :
# 0.0 - 0.5 : routing normal
# 0.5 - 0.8 : bonus score modèles locaux dans adaptive_router
# 0.8+      : downgrade systématique vers Ollama (sauf tâches premium-only)
# 1.0       : toutes requêtes refusées sauf priorité critique
```

### API REST

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| GET | `/budget/status` | Budget du jour (tokens + coût, pression) |
| GET | `/budget/history` | Historique journalier (`?days=7`) |
| GET | `/budget/usage` | Breakdown par modèle/task_type |
| POST | `/budget/reset` | Réinitialiser le budget (admin) |

### Variables d'environnement

| Variable | Défaut | Description |
|----------|--------|-------------|
| `TOKEN_BUDGET_ENABLED` | `false` | Activer le tracking/enforcement |
| `DAILY_TOKEN_BUDGET` | `5000000` | Budget quotidien en tokens |
| `DAILY_COST_BUDGET_CENTS` | `300` | Budget quotidien en centimes ($3) |

---

## 4. Classificateur Étendu (`query_classifier.py`)

Extension de 9 à 15 types de tâches pour un routing plus précis.

### Nouveaux types

| Type | Route modèle | Coût | Description |
|------|-------------|------|-------------|
| `code_write` | `code_reasoning` (Sonnet) | Premium | Génération de code |
| `code_review` | `code_reasoning` (Sonnet) | Premium | Revue de code |
| `code_explain` | `code_reasoning` (Sonnet) | Premium | Explication de code |
| `ops_query` | `incident_triage` (mini) | Cheap | Questions ops/serveur |
| `ops_action` | `tool_planning` (reasoning) | Medium | Actions ops avec outils |
| `web_research` | `retrieval_answer` | Medium | Recherche web |
| `notification` | `fallback_general` (mini) | Cheap | Rédaction notifications |

### Types existants (v9)

`general_chat`, `factual_question`, `creative_writing`, `tool_planning`, `classify_query`, `retrieval_answer`, `code_reasoning`, `incident_triage`, `fallback_general`

---

## 5. Routing Local-First (`adaptive_router.py` + `model_router.json`)

### Routes local-first (ajout dans `model_router.json`)

```json
"general_chat":    ["local_fast", "cheap_rewrite", "cheap_rewrite_backup"],
"translation":     ["local_fast", "translation_primary", "translation_backup"],
"memory_lookup":   ["local_fast", "cheap_rewrite", "cheap_rewrite_backup"],
"notification":    ["local_fast", "cheap_rewrite", "cheap_rewrite_backup"],
"ops_query":       ["local_fast", "incident_triage", "fallback_general"]
```

Où `local_fast` correspond à un modèle Ollama (ex: `ollama/llama3.2`).

### Intégration budget_pressure dans `adaptive_router.py`

```python
def get_model_ranking(task_type: str, candidates: list[str],
                      budget_pressure: float = 0.0) -> list[str]:
    # À pressure >= 0.5 : +0.3 bonus score pour les modèles locaux
    # À pressure >= 0.8 : modèles locaux systématiquement en tête
    #                     sauf pour task_types premium-only (code_write, code_review)
```

### Statut d'implémentation

| Composant | Statut |
|-----------|--------|
| `semantic_cache.py` + `SemanticCache` class | ✅ |
| `token_budget.py` + tracking + enforcement | ✅ |
| Classificateur 15 types | ✅ |
| Routes local-first `model_router.json` | 🔧 |
| `budget_pressure` dans `adaptive_router.py` | 🔧 |
| Onglet Cost Dashboard Admin UI | 🔧 |

---

## 6. Tests

- Cache L2 : requête identique → L1 hit ; requête similaire (cosine 0.93) → L2 hit ; requête différente → LLM call
- Cache L2 : TTL expiré → miss (pas de hit)
- Budget : `record_usage()` incrémente correctement ; `get_pressure()` retourne 0.0/0.5/1.0 selon usage
- Budget : downgrade Ollama si pressure >= 0.8 (mock `adaptive_router`)
- Classificateur : 15 types correctement classifiés sur exemples de référence
- Routing : `local_fast` en tête pour `general_chat` quand Ollama disponible
