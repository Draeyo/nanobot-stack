# Spec : Recherche Web avec SearXNG — Sous-projet D

**Date :** 2026-03-24
**Statut :** Spécifié
**Projet :** nanobot-stack
**Scope :** Intégration SearXNG self-hosted comme moteur de recherche web, nouveau `WebSearchAgent`, collection Qdrant `web_search_results` (TTL 6h), outil MCP `web_search`, section `web_digest` dans les briefings, rate limiting, stockage RAG des résultats

---

## 1. Contexte & Objectifs

Le sous-projet D ajoute une capacité de recherche web proactive à nanobot-stack, tout en maintenant la philosophie self-hosted du projet : aucune dépendance à une API de recherche commerciale payante (Google Custom Search, Bing API, etc.). SearXNG est un méta-moteur de recherche open-source auto-hébergeable qui agrège plusieurs sources (Google, Bing, DuckDuckGo, Wikipedia...) via une interface REST JSON. Il s'intègre naturellement au stack Docker existant.

Ce sous-projet s'appuie sur l'architecture sub-agents (sous-projet v10) et le scheduler (sous-projet A). Il complète le cycle RSS (sous-projet C) en permettant des recherches à la demande sur des sujets précis, plutôt qu'une ingestion passive de flux.

**Objectifs :**
- Intégrer SearXNG comme conteneur Docker sur le réseau privé (`docker-compose.yml`)
- Exposer un outil `web_search` dans le système MCP (`src/mcp/`) invocable par le LLM
- Implémenter un `WebSearchAgent` spécialisé, enregistré dans `AGENT_REGISTRY`
- Stocker les résultats dans Qdrant (`web_search_results`, TTL 6h) pour permettre des requêtes RAG ultérieures dans la même session
- Classifier les requêtes `web_research` et `web_factcheck` dans le routeur adaptatif existant
- Ajouter une section `web_digest` dans les briefings pour les top résultats sur des topics configurés
- Maîtriser les coûts via un rate limiting strict (`WEB_SEARCH_RATE_LIMIT_PER_HOUR`)
- Opt-in explicite via `SEARXNG_ENABLED` — aucun impact si non configuré

---

## 2. Architecture

### Vue d'ensemble

```
SearXNG (Docker container — réseau privé)
  └── HTTP JSON API → port interne 8080

WebSearchAgent (src/bridge/agents/web_search_agent.py)
  ├── search(query, num_results, categories) → list[SearchResult]
  ├── _call_searxng(query, params) → list[dict]
  ├── _embed_and_store(results, qdrant_client) → int
  ├── _check_rate_limit() → bool
  └── collect_web_digest(topics, num_per_topic) → str

MCP Tool (src/mcp/tools/web_search.py)
  └── web_search(query, num_results, categories) → str

OrchestratorAgent (src/bridge/agents/orchestrator.py)
  └── Routing : task_type == "web_research" | "web_factcheck"
        └── Délègue à WebSearchAgent.run(task, context)

JobExecutor (src/bridge/scheduler_executor.py)
  └── section "web_digest" → WebSearchAgent.collect_web_digest()

WebSearchApi (src/bridge/web_search_api.py)
  └── POST /tools/web-search
```

### Diagramme de classes

```
AgentBase (agents/base.py)
  └── WebSearchAgent (agents/web_search_agent.py)
        ├── name: str = "web_search"
        ├── description: str
        ├── searxng_url: str                   — depuis SEARXNG_URL
        ├── max_results: int                   — depuis WEB_SEARCH_MAX_RESULTS
        ├── rate_limit: int                    — depuis WEB_SEARCH_RATE_LIMIT_PER_HOUR
        ├── result_ttl_hours: int              — depuis WEB_SEARCH_RESULT_TTL_HOURS
        │
        ├── search(query, num_results, categories) → list[SearchResult]
        ├── run(task, context) → AgentResult          [override AgentBase]
        ├── _call_searxng(query, params) → list[dict]
        ├── _embed_and_store(results, qdrant_client) → int
        ├── _check_rate_limit(db_conn) → bool
        ├── _increment_rate_counter(db_conn) → None
        ├── _build_rag_context(query, qdrant_client) → str
        └── collect_web_digest(topics, num_per_topic, qdrant_client) → str

@dataclass
SearchResult
  ├── url: str
  ├── title: str
  ├── snippet: str
  ├── score: float
  ├── category: str
  ├── engine: str
  └── fetched_at: str
```

### Fichiers créés / modifiés

| Fichier | Action | Description |
|---------|--------|-------------|
| `docker-compose.yml` | Modifier | Nouveau service `searxng`, réseau privé uniquement |
| `searxng/settings.yml` | Créer | Config SearXNG (moteurs, formats, instance privée) |
| `src/bridge/agents/web_search_agent.py` | Créer | `WebSearchAgent` + `SearchResult` |
| `src/bridge/agents/__init__.py` | Modifier | Enregistrer `"web_search"` dans `AGENT_REGISTRY` |
| `src/mcp/tools/web_search.py` | Créer | Outil MCP `web_search` |
| `src/mcp/__init__.py` | Modifier | Enregistrer l'outil `web_search` |
| `src/bridge/web_search_api.py` | Créer | Endpoint `POST /tools/web-search` |
| `src/bridge/app.py` | Modifier | Mount `web_search_router`, injecter `WebSearchAgent` au startup |
| `src/bridge/scheduler_executor.py` | Modifier | Ajout section `web_digest` |
| `src/bridge/query_classifier.py` | Modifier | Ajout types `web_research`, `web_factcheck` |
| `src/bridge/admin_ui.py` | Modifier | Bloc stats recherche web dans l'onglet "Tools & Routing" |
| `migrations/014_web_search.py` | Créer | Table `web_search_log` |
| `tests/test_web_search_agent.py` | Créer | Tests unitaires avec mocks |

---

## 3. Modèle de données

### Table `web_search_log`

```sql
CREATE TABLE web_search_log (
    id              TEXT PRIMARY KEY,          -- UUID v4 généré à l'insertion
    query           TEXT NOT NULL,             -- Requête brute envoyée à SearXNG
    categories      TEXT NOT NULL DEFAULT '[]', -- JSON array, ex: ["general","news"]
    num_results     INTEGER NOT NULL DEFAULT 5,
    results_stored  INTEGER NOT NULL DEFAULT 0, -- Nombre de résultats upsertés dans Qdrant
    duration_ms     INTEGER,                   -- Durée totale de la recherche en millisecondes
    status          TEXT NOT NULL,             -- 'ok' | 'error' | 'rate_limited' | 'disabled'
    error_message   TEXT,                      -- Message d'erreur si status='error'
    source          TEXT NOT NULL DEFAULT 'api', -- 'api' | 'mcp' | 'agent' | 'scheduler'
    created_at      TEXT NOT NULL              -- Timestamp ISO 8601 UTC
);
```

```sql
CREATE INDEX idx_web_search_log_created_at ON web_search_log(created_at);
CREATE INDEX idx_web_search_log_status     ON web_search_log(status);
```

`id` est un UUID v4 généré à l'insertion. `categories` est un array JSON sérialisé en texte (cohérent avec le reste du schéma SQLite du projet). `source` distingue l'origine de la requête pour le monitoring. L'index sur `created_at` sert au calcul du rate limiting par fenêtre glissante d'une heure.

### Collection Qdrant `web_search_results`

TTL : `WEB_SEARCH_RESULT_TTL_HOURS * 3600` secondes (défaut : 6h = 21 600s).

Les résultats web sont éphémères par nature — une page indexée ce matin peut changer d'ici ce soir. Le TTL court évite de polluer le contexte RAG avec des résultats périmés tout en permettant des requêtes de suivi dans la même session de travail.

**Payload d'un point :**

```json
{
  "query":       "configuration nginx reverse proxy",
  "url":         "https://nginx.org/en/docs/beginners_guide.html",
  "title":       "Beginner's Guide — nginx",
  "snippet":     "This guide gives a basic introduction to nginx and describes...",
  "score":       0.87,
  "category":    "general",
  "engine":      "google",
  "source":      "web_search",
  "fetched_at":  "2026-03-24T09:15:00Z",
  "created_at":  "2026-03-24T09:15:02Z"
}
```

| Champ payload | Type | Description |
|---------------|------|-------------|
| `query` | `str` | Requête d'origine — permet le regroupement RAG par intention |
| `url` | `str` | URL du résultat — clé de dédup (hash utilisé comme point ID) |
| `title` | `str` | Titre de la page |
| `snippet` | `str` | Extrait renvoyé par SearXNG (max 500 chars) |
| `score` | `float` | Score de pertinence agrégé par SearXNG (0.0–1.0) |
| `category` | `str` | Catégorie SearXNG (`general`, `news`, `it`, `science`, etc.) |
| `engine` | `str` | Moteur source (ex: `google`, `bing`, `duckduckgo`, `wikipedia`) |
| `source` | `str` | Toujours `"web_search"` — identifiant collection |
| `fetched_at` | `str` | Timestamp ISO 8601 de la récupération |
| `created_at` | `str` | Timestamp ISO 8601 d'ingestion dans Qdrant |

Le champ vectorisé est `title + ". " + snippet` (même convention que `rss_articles`). Le point ID Qdrant est `uuid5(NAMESPACE_URL, url)` — déterministe, permet un upsert idempotent si le même résultat apparaît dans deux recherches distinctes.

---

## 4. Variables d'environnement

### Activation

| Variable | Défaut | Description |
|----------|--------|-------------|
| `SEARXNG_ENABLED` | `false` | Opt-in global. Si `false`, toutes les méthodes de `WebSearchAgent` retournent des résultats vides sans erreur. L'outil MCP retourne un message explicatif. |

### Connexion SearXNG

| Variable | Défaut | Description |
|----------|--------|-------------|
| `SEARXNG_URL` | `http://searxng:8080` | URL interne SearXNG sur le réseau Docker privé. Pas d'exposition externe. |

### Paramètres de recherche

| Variable | Défaut | Description |
|----------|--------|-------------|
| `WEB_SEARCH_MAX_RESULTS` | `5` | Nombre maximum de résultats renvoyés par requête. Plage valide : 1–20. |
| `WEB_SEARCH_RATE_LIMIT_PER_HOUR` | `20` | Nombre maximum de recherches autorisées par fenêtre glissante d'une heure. Toutes sources confondues (API, MCP, scheduler). |
| `WEB_SEARCH_RESULT_TTL_HOURS` | `6` | Durée de vie des résultats dans Qdrant. Après expiration, les points sont supprimés automatiquement par le TTL Qdrant. |

Si `SEARXNG_URL` n'est pas défini et `SEARXNG_ENABLED=true`, le bridge lève une `ConfigurationError` au démarrage avec un message d'erreur explicite.

---

## 5. Pipeline d'exécution

### 5.1 Recherche directe (`search`)

```
WebSearchAgent.search(query, num_results, categories)
  1. Vérifier SEARXNG_ENABLED → retourner [] si false
  2. _check_rate_limit(db_conn)
     a. Compter les entrées dans web_search_log WHERE
        created_at >= (now - 1h) AND status != 'rate_limited'
     b. Si count >= WEB_SEARCH_RATE_LIMIT_PER_HOUR :
          - Insérer log avec status='rate_limited'
          - Lever WebSearchRateLimitError
  3. t0 = now
  4. _call_searxng(query, {
       "q": query,
       "format": "json",
       "categories": ",".join(categories or ["general"]),
       "language": "fr-FR",
       "safesearch": 1,
       "pageno": 1
     })
     a. httpx.AsyncClient.get(SEARXNG_URL + "/search", params=params, timeout=10s)
     b. Vérifier HTTP 200, parser JSON
     c. Extraire "results" → list[dict]
     d. Tronquer à min(num_results, WEB_SEARCH_MAX_RESULTS)
     e. Mapper vers list[SearchResult]
  5. t1 = now, duration_ms = (t1 - t0) * 1000
  6. _embed_and_store(results, qdrant_client)
     a. Pour chaque SearchResult :
          - Texte à embedder : title + ". " + snippet
          - Point ID : uuid5(NAMESPACE_URL, url)
          - Upsert dans web_search_results avec payload complet
          - TTL : WEB_SEARCH_RESULT_TTL_HOURS * 3600
     b. Retourner count upserts
  7. Insérer dans web_search_log :
       status='ok', results_stored=count, duration_ms=duration_ms
  8. Retourner list[SearchResult]
```

### 5.2 Requête RAG sur résultats précédents (`_build_rag_context`)

```
WebSearchAgent._build_rag_context(query, qdrant_client)
  1. Encoder query en vecteur dense (même modèle embed que les autres collections)
  2. Requête Qdrant web_search_results :
       - vector: embed(query)
       - limit: 5
       - score_threshold: 0.75
       - with_payload: True
  3. Formater les résultats comme contexte texte :
       "[1] Titre (URL)\nSnippet...\n\n[2] ..."
  4. Retourner str (vide si aucun résultat)
```

Ce contexte RAG est injecté dans le prompt du `WebSearchAgent` avant tout appel LLM, permettant de répondre à des questions de suivi sans relancer une nouvelle recherche.

### 5.3 Exécution en tant qu'agent (`run`)

```
WebSearchAgent.run(task, context) → AgentResult
  1. LLM décompose task → {query, num_results, categories}
       (prompt court, modèle cheap — adaptive_router "query_extraction")
  2. Vérifier si résultats pertinents déjà dans Qdrant (RAG context, score > 0.85)
     → Si oui et résultats < 2h : utiliser RAG context, ne pas relancer SearXNG
  3. Si recherche nécessaire : search(query, num_results, categories)
  4. _build_rag_context(query, qdrant_client)
  5. LLM synthèse : prompt avec task + résultats + contexte RAG
       (adaptive_router "web_research" — modèle standard ou flash selon budget)
  6. Retourner AgentResult(
         status='completed',
         output=synthèse_llm,
         actions_taken=[{"tool": "web_search", "query": query, "results": N}],
         cost_tokens=total_tokens,
         artifacts={"results": [r.__dict__ for r in results]}
     )
```

### 5.4 Section `web_digest` pour les briefings

```
WebSearchAgent.collect_web_digest(topics, num_per_topic, qdrant_client)
  1. Vérifier SEARXNG_ENABLED → retourner "" si false
  2. topics = WEB_DIGEST_TOPICS (liste depuis env var, séparée par virgules)
  3. Pour chaque topic (asyncio.gather, max 3 topics en parallèle) :
     a. search(topic, num_results=num_per_topic, categories=["news","general"])
     b. Collecter list[SearchResult]
  4. Dédupliquer par URL (union des résultats tous topics confondus)
  5. Grouper par topic
  6. Appel LLM (adaptive_router "briefing") :
       Prompt : résultats bruts groupés par topic → digest structuré markdown
  7. Retourner str digest
```

### 5.5 Intégration OrchestratorAgent

Le routage se fait dans `OrchestratorAgent._assign_agent(subtask)` :

```python
if subtask.task_type in ("web_research", "web_factcheck"):
    return AGENT_REGISTRY["web_search"]
```

Les deux types de tâche sont gérés par le même agent. La distinction `web_factcheck` influe sur le prompt de synthèse (mode vérification de faits vs. recherche informative) et force la citation explicite des sources dans la réponse.

---

## 6. Sécurité

- **Réseau privé Docker** : SearXNG est exposé uniquement sur le réseau interne `nanobot-net` (ou équivalent dans `docker-compose.yml`). Aucun port n'est ouvert vers l'extérieur (`ports` non défini dans le service `searxng`). Le bridge accède à SearXNG via le nom de service DNS interne (`http://searxng:8080`).
- **SEARXNG_URL sur réseau interne uniquement** : si `SEARXNG_URL` pointe vers un hôte public externe (`http://` avec IP/domaine public), le bridge logue un avertissement au démarrage. La validation est informative, non bloquante (cas d'usage légitime : instance SearXNG partagée sur VPN).
- **Rate limiting** : le compteur de requêtes est centralisé dans `web_search_log` (SQLite). Toutes les entrées de la fenêtre glissante d'une heure sont comptées, quelle que soit la source (`api`, `mcp`, `agent`, `scheduler`). L'atomicité est assurée par une transaction SQLite.
- **Snippets tronqués** : les snippets reçus de SearXNG sont tronqués à 500 chars avant stockage dans Qdrant — protection contre l'injection de contenu volumineux.
- **Pas de navigation de page** : le sous-projet D ne récupère que les métadonnées SearXNG (titre, snippet, URL). Aucun fetch du contenu complet des pages. L'accès au contenu réel est une feature distincte (sous-projet futur).
- **Validation des paramètres** : `num_results` est borné à `[1, 20]`, `categories` est une whitelist (`general`, `news`, `it`, `science`, `files`, `images`, `videos`). Toute valeur hors whitelist retourne HTTP 422.
- **PII** : les snippets passent par le filtre PII existant avant inclusion dans les briefings `web_digest`.
- **Logs** : les requêtes sont loguées dans `web_search_log` mais les snippets complets n'y sont pas stockés — uniquement `query`, `num_results`, `categories`, `status`, `duration_ms`.

### Configuration SearXNG (`searxng/settings.yml`)

```yaml
server:
  secret_key: "${SEARXNG_SECRET_KEY}"  # Variable d'env — ne pas hardcoder
  bind_address: "0.0.0.0:8080"
  public_instance: false               # Instance privée — pas d'UI publique
  image_proxy: false

search:
  safe_search: 1
  default_lang: "fr-FR"
  formats:
    - html
    - json                             # Indispensable pour l'API REST

general:
  debug: false
  instance_name: "nanobot-searxng"

ui:
  static_use_hash: true
  default_theme: simple

enabled_plugins:
  - Hash_plugin
  - Search_on_category_select

# Moteurs activés (liste minimale — ajuster selon besoins)
engines:
  - name: google
    engine: google
    shortcut: g
    disabled: false
  - name: bing
    engine: bing
    shortcut: b
    disabled: false
  - name: duckduckgo
    engine: duckduckgo
    shortcut: ddg
    disabled: false
  - name: wikipedia
    engine: wikipedia
    shortcut: wp
    language: fr
    disabled: false
```

La variable `SEARXNG_SECRET_KEY` doit être définie dans `.env` (valeur aléatoire longue). Elle est passée au conteneur via `environment:` dans `docker-compose.yml`.

---

## 7. Dépendances Python

Aucune nouvelle dépendance — `httpx>=0.27` est déjà présent dans `requirements.txt` et suffit pour les appels REST à SearXNG. `asyncio` est stdlib.

```
# Pas de modification de requirements.txt nécessaire
```

`httpx.AsyncClient` est utilisé pour tous les appels HTTP (cohérent avec le reste du bridge). La session client est instanciée une fois au démarrage et partagée via le cycle de vie FastAPI (`lifespan`), évitant la création de connexions à chaque requête.

---

## 8. API REST

Préfixe : `/tools`

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| POST | `/tools/web-search` | Déclenche une recherche web, retourne les résultats et les stocke dans Qdrant |
| GET | `/tools/web-search/stats` | Statistiques d'utilisation (requêtes/heure, total, dernière recherche) |
| GET | `/tools/web-search/status` | Retourne `enabled`, `rate_limit_remaining`, `searxng_reachable` |

### `POST /tools/web-search`

**Corps de la requête :**

```json
{
  "query": "best practices kubernetes resource limits",
  "num_results": 5,
  "categories": ["it", "general"]
}
```

| Champ | Type | Requis | Défaut | Contraintes |
|-------|------|--------|--------|-------------|
| `query` | `str` | Oui | — | 3–500 chars, non vide |
| `num_results` | `int` | Non | `WEB_SEARCH_MAX_RESULTS` | 1–20 |
| `categories` | `list[str]` | Non | `["general"]` | Whitelist (voir §6) |

**Réponse 200 :**

```json
{
  "query": "best practices kubernetes resource limits",
  "results": [
    {
      "url": "https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/",
      "title": "Resource Management for Pods and Containers",
      "snippet": "When you specify a Pod, you can optionally specify how much of each resource...",
      "score": 0.91,
      "category": "it",
      "engine": "google"
    }
  ],
  "count": 5,
  "stored_in_qdrant": 5,
  "duration_ms": 412,
  "rate_limit_remaining": 17
}
```

**Codes d'erreur :**

| Code | Condition |
|------|-----------|
| 400 | `SEARXNG_ENABLED=false` — retourne `{"error": "web_search_disabled", "message": "..."}` |
| 422 | Validation échouée (query vide, num_results hors plage, catégorie invalide) |
| 429 | Rate limit atteint — retourne `{"error": "rate_limited", "retry_after_seconds": N}` |
| 503 | SearXNG injoignable — retourne `{"error": "searxng_unavailable", "message": "..."}` |

### `GET /tools/web-search/stats`

**Réponse 200 :**

```json
{
  "searches_last_hour": 3,
  "searches_last_24h": 47,
  "searches_total": 312,
  "rate_limit_per_hour": 20,
  "rate_limit_remaining": 17,
  "last_search_at": "2026-03-24T09:15:02Z",
  "last_search_query": "kubernetes resource limits",
  "top_categories": ["general", "it", "news"],
  "avg_duration_ms": 387
}
```

### `GET /tools/web-search/status`

**Réponse 200 :**

```json
{
  "enabled": true,
  "searxng_url": "http://searxng:8080",
  "searxng_reachable": true,
  "rate_limit_per_hour": 20,
  "rate_limit_remaining": 17,
  "result_ttl_hours": 6
}
```

`searxng_reachable` est évalué en live (requête `GET /` à SearXNG avec timeout 2s). Si `SEARXNG_ENABLED=false`, les trois champs `rate_limit_*` retournent `null`.

Ces trois endpoints sont définis dans `src/bridge/web_search_api.py` et montés dans `app.py` sous le router `web_search_router`.

---

## 9. Tests

Fichier : `tests/test_web_search_agent.py`

| Test | Description |
|------|-------------|
| `test_search_mock_searxng` | Mock `httpx.AsyncClient.get`, vérifie que `search()` retourne les bons `SearchResult` avec les bons champs |
| `test_search_trims_to_max_results` | `num_results=3` mais SearXNG retourne 10 → vérifie que seuls 3 sont renvoyés |
| `test_qdrant_upsert_on_search` | Mock Qdrant, vérifie que `_embed_and_store()` appelle `upsert` pour chaque résultat avec le bon payload |
| `test_qdrant_dedup_by_url` | Deux recherches avec un résultat en commun → l'ID Qdrant est identique (uuid5 déterministe), pas de doublon |
| `test_rate_limit_enforcement` | Insérer `WEB_SEARCH_RATE_LIMIT_PER_HOUR` entrées dans `web_search_log` dans la fenêtre 1h → vérifier que `search()` lève `WebSearchRateLimitError` |
| `test_rate_limit_window_sliding` | Entrées vieilles de 61 min → ne comptent pas dans la fenêtre, nouvelle recherche acceptée |
| `test_rate_limit_log_entry` | Vérifier qu'une recherche bloquée insère une entrée avec `status='rate_limited'` |
| `test_disabled_flag` | `SEARXNG_ENABLED=false` → `search()` retourne `[]` sans appel HTTP ni Qdrant |
| `test_searxng_unavailable` | Mock retourne `httpx.ConnectError` → `search()` insère log `status='error'` et lève `WebSearchUnavailableError` |
| `test_rag_context_build` | Mock Qdrant avec 3 résultats stockés, vérifie format de sortie `_build_rag_context()` |
| `test_rag_context_empty` | Qdrant retourne 0 résultats au-dessus du seuil → `_build_rag_context()` retourne `""` |
| `test_agent_run_uses_rag_cache` | Résultats récents (< 2h) en Qdrant → `run()` ne relance pas SearXNG (mock assert_not_called) |
| `test_agent_run_bypasses_stale_cache` | Résultats en Qdrant mais score < 0.85 → `run()` relance SearXNG |
| `test_web_digest_parallel_topics` | 3 topics configurés → 3 appels `search()` en parallèle, résultat dédupliqué par URL |
| `test_web_digest_disabled` | `SEARXNG_ENABLED=false` → `collect_web_digest()` retourne `""` |
| `test_api_post_web_search_200` | `POST /tools/web-search` valide → 200 avec `results`, `count`, `stored_in_qdrant` |
| `test_api_post_web_search_disabled_400` | `SEARXNG_ENABLED=false` → 400 avec `error="web_search_disabled"` |
| `test_api_post_web_search_rate_limited_429` | Rate limit atteint → 429 avec `retry_after_seconds` |
| `test_api_post_web_search_invalid_category_422` | `categories=["invalid_cat"]` → 422 |
| `test_api_get_stats` | `GET /tools/web-search/stats` → tous les champs présents, `searches_last_hour` cohérent avec `web_search_log` |
| `test_searxng_categories_whitelist` | Vérifie que les 7 catégories valides passent, les autres retournent 422 |
| `test_snippet_truncation` | Snippet > 500 chars renvoyé par SearXNG → tronqué à 500 avant upsert Qdrant |

---

## 10. Ordre d'implémentation

1. **Docker** — Ajouter le service `searxng` dans `docker-compose.yml` + créer `searxng/settings.yml`. Vérifier l'accès depuis le bridge avec `curl http://searxng:8080/search?q=test&format=json`.
2. **Migration** — `migrations/014_web_search.py` : table `web_search_log` + index `created_at` et `status`.
3. **`WebSearchAgent` — structure de base** — `src/bridge/agents/web_search_agent.py` : classe, `SearchResult`, chargement env vars, `_check_rate_limit()`, `_increment_rate_counter()`.
4. **`WebSearchAgent` — appel SearXNG** — `_call_searxng()` avec `httpx`, parsing JSON, mapping `SearchResult`, gestion erreurs (`ConnectError`, non-200).
5. **`WebSearchAgent` — stockage Qdrant** — `_embed_and_store()` avec uuid5 déterministe, TTL, upsert.
6. **`WebSearchAgent` — méthode publique** — `search()` : assemblage complet (rate limit → SearXNG → Qdrant → log).
7. **`WebSearchAgent` — RAG et agent** — `_build_rag_context()` + `run()` avec logique de cache RAG (éviter re-fetch si résultats récents suffisamment pertinents).
8. **`WebSearchAgent` — digest** — `collect_web_digest()` + variable `WEB_DIGEST_TOPICS`.
9. **AGENT_REGISTRY** — Enregistrer `"web_search": WebSearchAgent(...)` dans `src/bridge/agents/__init__.py`.
10. **Outil MCP** — `src/mcp/tools/web_search.py` + enregistrement dans `src/mcp/__init__.py`.
11. **OrchestratorAgent** — Ajouter routing `web_research` / `web_factcheck` dans `_assign_agent()`.
12. **Query classifier** — Ajouter `"web_research"` et `"web_factcheck"` dans `src/bridge/query_classifier.py` + entrées correspondantes dans `model_router.json`.
13. **API REST** — `src/bridge/web_search_api.py` : endpoints `/tools/web-search`, `/tools/web-search/stats`, `/tools/web-search/status`. Mount dans `app.py`.
14. **Scheduler** — Ajouter section `web_digest` dans `scheduler_executor.py`. Ajouter `WEB_DIGEST_TOPICS` aux variables d'env documentées. Valider que la section est silencieusement ignorée si `SEARXNG_ENABLED=false`.
15. **Admin UI** — Bloc "Recherche Web" dans l'onglet "Tools & Routing" : statut SearXNG, compteurs, dernière requête, `rate_limit_remaining`.
16. **Tests** — `tests/test_web_search_agent.py` (liste complète §9).

### Sections valides (liste complète après sous-projet D)

`"system_health"`, `"personal_notes"`, `"topics"`, `"reminders"`, `"weekly_summary"`, `"custom"`, `"agenda"` *(B)*, `"email_digest"` *(B)*, `"rss_digest"` *(C)*, `"web_digest"` *(D)*

---

## Annexe — Configuration `docker-compose.yml`

Extrait du service à ajouter :

```yaml
services:
  searxng:
    image: searxng/searxng:latest
    container_name: nanobot-searxng
    restart: unless-stopped
    networks:
      - nanobot-net                # Réseau privé — pas de ports exposés à l'hôte
    volumes:
      - ./searxng:/etc/searxng:rw  # settings.yml monté en lecture/écriture
    environment:
      - SEARXNG_SECRET_KEY=${SEARXNG_SECRET_KEY}
    healthcheck:
      test: ["CMD", "wget", "-q", "--spider", "http://localhost:8080/"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
    cap_drop:
      - ALL
    cap_add:
      - CHOWN
      - SETGID
      - SETUID
```

Le service ne définit pas de `ports:` — il n'est joignable que depuis les autres conteneurs sur `nanobot-net`. Le bridge FastAPI l'atteint via `http://searxng:8080` (résolution DNS interne Docker). Aucun port SearXNG ne doit être ouvert sur l'interface hôte en production.

---

## Annexe — Format de migration

```python
# migrations/014_web_search.py
VERSION = 14

def check(ctx) -> bool:
    """Idempotency guard — retourne True si la migration est déjà appliquée."""
    tables = ctx.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='web_search_log'"
    ).fetchall()
    return len(tables) > 0

def migrate(ctx):
    ctx.execute("""
        CREATE TABLE IF NOT EXISTS web_search_log (
            id              TEXT PRIMARY KEY,
            query           TEXT NOT NULL,
            categories      TEXT NOT NULL DEFAULT '[]',
            num_results     INTEGER NOT NULL DEFAULT 5,
            results_stored  INTEGER NOT NULL DEFAULT 0,
            duration_ms     INTEGER,
            status          TEXT NOT NULL,
            error_message   TEXT,
            source          TEXT NOT NULL DEFAULT 'api',
            created_at      TEXT NOT NULL
        );
    """)
    ctx.execute(
        "CREATE INDEX IF NOT EXISTS idx_web_search_log_created_at "
        "ON web_search_log(created_at);"
    )
    ctx.execute(
        "CREATE INDEX IF NOT EXISTS idx_web_search_log_status "
        "ON web_search_log(status);"
    )
```
