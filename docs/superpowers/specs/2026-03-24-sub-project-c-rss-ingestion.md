# Spec : Ingestion RSS/News — Sous-projet C

**Date :** 2026-03-24
**Statut :** Spécifié
**Projet :** nanobot-stack
**Scope :** Ingestion de flux RSS dans Qdrant, résumés LLM, section `rss_digest` dans les briefings, interface d'administration

---

## 1. Contexte & Objectifs

La section `topics` du scheduler (sous-projet A) lit actuellement la collection Qdrant `documents` — une collection générique peu adaptée au suivi de l'actualité. Ce sous-projet apporte :

1. **Gestion des abonnements RSS** — CRUD de flux avec catégorisation, intervalle de refresh configurable
2. **Pipeline d'ingestion** — fetch → parse → embed → Qdrant, avec déduplication par `entry.id`/URL
3. **Résumé LLM par article** — résumé court (modèle cheap) stocké dans le payload Qdrant
4. **Section `rss_digest`** — digest curé pour les briefings, remplace la source de `topics`
5. **Sync planifiée** — job système APScheduler `*/30 * * * *`
6. **Admin UI** — 12ème onglet pour gérer les flux et monitorer la sync

---

## 2. Architecture

```
RssIngestor (rss_ingestor.py)
  ├── sync_feed(feed_id)          — fetch XML → parse → embed → Qdrant upsert
  ├── sync_all()                  — parallel sync tous les flux actifs
  └── collect_digest(since_hours, categories) → str   — pour JobExecutor

APScheduler (job système "RSS Sync")
  └── toutes les 30 min → RssIngestor.sync_all()

JobExecutor (scheduler_executor.py)
  └── section "rss_digest" → RssIngestor.collect_digest()
  └── section "topics"     → redirigée vers rss_articles (remplace documents)

RssApi (rss_api.py)
  └── /api/rss/feeds CRUD + /api/rss/feeds/{id}/sync
```

### Fichiers créés / modifiés

| Fichier | Action | Description |
|---------|--------|-------------|
| `src/bridge/rss_ingestor.py` | Créer | `RssIngestor` — pipeline complet |
| `src/bridge/rss_api.py` | Créer | API REST `/api/rss/*` |
| `src/bridge/app.py` | Modifier | Mount `rss_router`, injecter `RssIngestor` au startup |
| `src/bridge/scheduler_executor.py` | Modifier | Ajout section `rss_digest`, redirection `topics` |
| `src/bridge/scheduler_registry.py` | Modifier | Job système RSS sync |
| `src/bridge/admin_ui.py` | Modifier | 12ème onglet "RSS" |
| `migrations/013_rss.py` | Créer | Tables `rss_feeds`, `rss_entries` |
| `src/bridge/requirements.txt` | Modifier | Ajouter `feedparser>=6.0` |

---

## 3. Modèle de données

### Table `rss_feeds`

```sql
CREATE TABLE rss_feeds (
    id                      TEXT PRIMARY KEY,
    url                     TEXT NOT NULL UNIQUE,
    name                    TEXT NOT NULL,
    category                TEXT NOT NULL DEFAULT 'general',
    refresh_interval_min    INTEGER NOT NULL DEFAULT 60,
    last_fetched            TEXT,
    last_status             TEXT,          -- 'ok' | 'error' | NULL
    article_count           INTEGER DEFAULT 0,
    enabled                 INTEGER NOT NULL DEFAULT 1,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);
```

### Table `rss_entries`

```sql
CREATE TABLE rss_entries (
    id          TEXT PRIMARY KEY,          -- uuid v4
    feed_id     TEXT NOT NULL,
    entry_id    TEXT NOT NULL UNIQUE,      -- entry.id ou entry.link (dédup)
    url         TEXT NOT NULL,
    title       TEXT NOT NULL,
    published_at TEXT,
    embedded    INTEGER NOT NULL DEFAULT 0,
    summarized  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
```

`updated_at` géré exclusivement par la couche applicative (pas de trigger SQLite).

### Collection Qdrant `rss_articles`

TTL recommandé : 30 jours (configurable via `RSS_ARTICLE_TTL_DAYS`).

**Payload d'un point :**

```json
{
  "title": "Titre de l'article",
  "url": "https://...",
  "feed_id": "uuid",
  "feed_name": "Le Monde",
  "category": "tech",
  "published_at": "2026-03-24T08:00:00Z",
  "summary": "Résumé LLM en 2-3 phrases.",
  "tags": ["rss", "tech"],
  "text": "Titre. Résumé."
}
```

Le champ `text` (utilisé pour l'embedding dense) est `title + ". " + summary` (pas le texte complet par défaut — voir `RSS_EMBED_FULL_TEXT`).

---

## 4. Variables d'environnement

| Variable | Défaut | Description |
|----------|--------|-------------|
| `RSS_ENABLED` | `false` | Activer l'ingestion RSS |
| `RSS_MAX_ARTICLES_PER_DIGEST` | `10` | Articles max par section `rss_digest` |
| `RSS_EMBED_FULL_TEXT` | `false` | Embedder le texte complet (vs titre+résumé) |
| `RSS_SUMMARIZE_ENABLED` | `true` | Générer un résumé LLM par article |
| `RSS_ARTICLE_TTL_DAYS` | `30` | Durée de vie des articles dans Qdrant |
| `RSS_SYNC_TIMEOUT_S` | `30` | Timeout HTTP par flux (secondes) |

---

## 5. Sections de briefing

### Section `rss_digest` (nouvelle)

Requête `rss_articles` pour les articles publiés depuis `last_run` (ou 24h si absent), groupés par catégorie, puis appel LLM pour générer un digest structuré.

**Format de sortie LLM attendu :**

```
## Tech
- [Titre](URL) — Résumé en une phrase.
- ...

## Général
- ...
```

**Contrainte coût :** la section `rss_digest` avec `RSS_SUMMARIZE_ENABLED=true` consomme un appel LLM par article lors de l'ingestion (cheap model). Pour les briefings, un seul appel LLM supplémentaire agrège tous les résumés. Ne pas activer avec un cron < 1h.

### Section `topics` (modifiée)

La source passe de `documents` à `rss_articles`. Le comportement de la section `topics` reste identique (fenêtre 7 jours, résumé LLM). La redirection est transparente : aucun changement d'API ou de configuration pour l'utilisateur.

### Sections valides (liste complète après sous-projet C)

`"system_health"`, `"personal_notes"`, `"topics"`, `"reminders"`, `"weekly_summary"`, `"custom"`, `"agenda"` *(B)*, `"email_digest"` *(B)*, `"rss_digest"` *(C)*

---

## 6. API REST

Préfixe : `/api/rss`

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| GET | `/feeds` | Liste tous les flux avec statut et `article_count` |
| POST | `/feeds` | Créer un flux (`url`, `name`, `category`, `refresh_interval_min`) |
| GET | `/feeds/{id}` | Détail d'un flux |
| PUT | `/feeds/{id}` | Modifier un flux (body partiel accepté) |
| DELETE | `/feeds/{id}` | Supprimer un flux et ses entrées Qdrant |
| POST | `/feeds/{id}/toggle` | Activer / désactiver |
| POST | `/feeds/{id}/sync` | Sync manuelle immédiate (retourne `{"synced": N, "new": M}`) |
| GET | `/feeds/{id}/articles` | Articles récents (`?limit=20&offset=0`) |

**Validation à la création/modification :**
- URL validée (format HTTP/HTTPS, accessible) — retourne HTTP 422 si invalide
- `refresh_interval_min` entre 15 et 1440 — retourne HTTP 422 si hors plage
- `category` : string libre, max 50 chars

---

## 7. Admin UI — Onglet "RSS"

12ème onglet dans l'Admin UI existante (pattern Alpine.js).

### Vue liste des flux

Tableau avec colonnes :
- Nom du flux + URL (tronquée)
- Catégorie
- Intervalle de refresh
- Dernier fetch (date relative, ex: "il y a 23 min")
- Statut dernière sync (✅ ok / ❌ error)
- Nombre d'articles ingérés
- Toggle actif/inactif
- Actions : Modifier | Sync maintenant | Supprimer

### Formulaire ajout/édition

Panneau latéral :
- URL du flux
- Nom affiché
- Catégorie (texte libre avec suggestions : `tech`, `actualité`, `science`, `finance`, `general`)
- Intervalle de refresh (slider 15-1440 min, affichage "toutes les X heures")
- Bouton "Tester l'URL" — vérifie accessibilité et parse les premiers articles (aperçu)

### Statistiques globales

En en-tête de l'onglet :
- Nombre total de flux actifs / total
- Articles ingérés les dernières 24h
- Date de la dernière sync globale

---

## 8. Pipeline d'exécution

### Sync d'un flux (`sync_feed`)

```
RssIngestor.sync_feed(feed_id)
  1. Récupérer config flux depuis SQLite
  2. httpx.AsyncClient.get(url, timeout=RSS_SYNC_TIMEOUT_S)
  3. feedparser.parse(content)
  4. Pour chaque entry :
     a. Calculer entry_id = entry.id ou entry.link
     b. Si entry_id existe dans rss_entries → skip (dédup)
     c. Insérer dans rss_entries (embedded=0, summarized=0)
     d. Si RSS_SUMMARIZE_ENABLED : appeler LLM cheap (adaptive_router "summarize") → summary
     e. Générer embedding (embed_texts([title + ". " + summary]))
     f. Upsert dans Qdrant rss_articles
     g. Marquer embedded=1, summarized=1 dans rss_entries
  5. Mettre à jour rss_feeds.last_fetched, last_status, article_count, updated_at
  6. Retourner {"feed_id": ..., "synced": N, "new": M, "errors": K}
```

### Sync globale (`sync_all`)

```
RssIngestor.sync_all()
  1. Lister tous les flux enabled=1
  2. Filtrer : last_fetched IS NULL OR (now - last_fetched) >= refresh_interval_min
  3. asyncio.gather(*[sync_feed(f.id) for f in due_feeds], return_exceptions=True)
  4. Logger résumé global
```

### Digest pour briefing (`collect_digest`)

```
RssIngestor.collect_digest(since_hours, categories)
  1. Qdrant query rss_articles : filter published_at >= (now - since_hours)
     et category IN categories (si fourni)
  2. Trier par published_at desc, limit=RSS_MAX_ARTICLES_PER_DIGEST
  3. Grouper par category
  4. Formater prompt avec titres + résumés
  5. Appel LLM (adaptive_router "briefing") → digest structuré markdown
  6. Retourner texte digest
```

---

## 9. Sécurité & Contraintes

- **Validation URL** : uniquement HTTP/HTTPS, pas d'URLs locales (blacklist `localhost`, `127.`, `10.`, `192.168.`) sauf si `RSS_ALLOW_LOCAL_URLS=true`
- **Timeout réseau** : `RSS_SYNC_TIMEOUT_S` (défaut 30s) — évite les flux lents qui bloquent la sync
- **Rate limit** : les endpoints `/sync` manuels sont soumis au rate limiter global de l'API
- **Taille** : entrées tronquées à 10 000 chars avant embedding (protection mémoire)
- **PII** : les résumés LLM passent par le filtre PII avant stockage dans Qdrant
- **Pas d'authentification RSS** : les flux RSS authentifiés (Basic Auth dans l'URL) ne sont pas supportés dans cette version — URL doit être publique

---

## 10. Dépendances Python

```
feedparser>=6.0
```

`httpx` est déjà présent. `asyncio` est stdlib.

---

## 11. Format de migration

```python
# migrations/013_rss.py
VERSION = 13

def check(_ctx: dict) -> bool:
    db_path = STATE_DIR / "rss.db"
    if not db_path.exists():
        return False
    db = sqlite3.connect(str(db_path))
    try:
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('rss_feeds','rss_entries')"
        ).fetchall()}
        return tables == {"rss_feeds", "rss_entries"}
    finally:
        db.close()

def migrate(_ctx: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    db_path = STATE_DIR / "rss.db"
    db = sqlite3.connect(str(db_path))
    db.execute("PRAGMA journal_mode=WAL")
    try:
        db.execute("CREATE TABLE IF NOT EXISTS rss_feeds (...)")
        db.execute("CREATE TABLE IF NOT EXISTS rss_entries (...)")
        db.commit()
    finally:
        db.close()
```

La migration crée une nouvelle base `rss.db` (pas d'ALTER sur les bases existantes).

---

## 12. Tests

- **Unit** : déduplication par `entry_id`, validation URL (local blacklist), calcul `due_feeds` selon `refresh_interval_min`
- **Unit** : `collect_digest` — fenêtre temporelle, groupement par catégorie, limite `RSS_MAX_ARTICLES_PER_DIGEST`
- **Unit** : `sync_feed` — mock `feedparser` + mock `httpx`, vérification upsert Qdrant et mise à jour SQLite
- **Unit** : `sync_all` — parallélisme, gestion des flux dus vs non-dus, exception dans un flux n'interrompt pas les autres
- **Integration** : création flux → sync → vérification `rss_entries` + `rss_articles` Qdrant
- **Integration** : `rss_digest` dans `JobExecutor` — mock `collect_digest`, vérification assemblage prompt
- **API** : `POST /api/rss/feeds` validation (URL invalide → 422, interval hors plage → 422)
- **API** : `POST /api/rss/feeds/{id}/sync` — retourne `{"synced": N, "new": M}`

---

## 13. Ordre d'implémentation

1. Migration `013_rss.py` + modèle de données SQLite
2. `rss_ingestor.py` — `sync_feed()` (fetch + parse + dedup + SQLite)
3. `rss_ingestor.py` — embedding + upsert Qdrant
4. `rss_ingestor.py` — résumé LLM par article (opt-in `RSS_SUMMARIZE_ENABLED`)
5. `rss_ingestor.py` — `sync_all()` + `collect_digest()`
6. `rss_api.py` — endpoints CRUD + sync manuelle
7. `scheduler_executor.py` — section `rss_digest` + redirection `topics`
8. `scheduler_registry.py` — job système RSS sync `*/30 * * * *`
9. `app.py` — mount router, injecter `RssIngestor` au startup
10. `admin_ui.py` — 12ème onglet RSS
11. Tests `tests/test_rss.py`
