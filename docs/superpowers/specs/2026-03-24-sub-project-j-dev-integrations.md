# Spec : Intégrations Développeur — Sous-projet J

**Date :** 2026-03-24
**Statut :** Spécifié
**Projet :** nanobot-stack
**Scope :** Intégration GitHub (PRs, issues, commits via API REST) et Obsidian (vault Markdown avec frontmatter YAML et WikiLinks), extension de la collection `memory_projects`, nouvelle section de briefing `dev_digest`, classe `ObsidianIngestor` étendant `LocalDocIngestor`, nouveau type de routage `knowledge_lookup`

---

## 1. Contexte & Objectifs

Le sous-projet J connecte nanobot-stack aux deux outils centraux du workflow quotidien d'un développeur solo : GitHub comme source d'activité de code, et Obsidian comme base de connaissances personnelle. Ces deux intégrations enrichissent l'assistant avec un contexte développeur concret — l'état des PRs en cours, les issues assignées, les notes de conception — sans nécessiter de SaaS tiers ni de stockage cloud.

Ce sous-projet s'appuie directement sur l'infrastructure posée par Sub-projet E (`LocalDocIngestor`, `LocalDocWatcher`) pour la partie Obsidian, et sur le scheduler (Sub-projet A, `scheduler_registry.py`) pour la synchronisation GitHub périodique. L'objectif est d'enrichir les briefings avec un nouveau bloc `dev_digest` et d'étendre le routage de requêtes avec un type `knowledge_lookup` pointant vers les notes Obsidian.

**Objectifs GitHub :**
- Récupérer les PRs assignées/créées par l'utilisateur, les issues ouvertes qui lui sont assignées, et les commits récents (7 derniers jours) via l'API GitHub REST (authentification PAT)
- Étendre la collection Qdrant `memory_projects` existante avec ces artefacts (pas de nouvelle collection)
- Synchronisation automatique toutes les 30 minutes via APScheduler, déclenchable manuellement via l'API
- Journaliser chaque synchronisation dans `github_sync_log`, avec le rate limit restant après chaque appel
- Opt-in explicite via `GITHUB_ENABLED`

**Objectifs Obsidian :**
- Ingérer un vault Obsidian (dossier de fichiers `.md`) en réutilisant `LocalDocIngestor` (Sub-projet E) pour le pipeline d'embedding et de stockage dans `docs_reference`
- Ajouter une couche d'extraction spécifique à Obsidian : frontmatter YAML (`tags`, `aliases`, `created`, `modified`) et WikiLinks (`[[note name]]`)
- Tracer les backlinks entre notes dans `obsidian_index` (table SQLite dédiée)
- Enregistrer les relations WikiLink dans `knowledge_graph.py`
- Surveiller le vault en temps réel via `LocalDocWatcher` (réutilisé tel quel)
- Exposer un type de requête `knowledge_lookup` dans le routeur adaptatif, orientant les recherches vers `docs_reference` filtré par source Obsidian
- Opt-in explicite via `OBSIDIAN_VAULT_PATH`

---

## 2. Architecture

### Vue d'ensemble

```
GitHub API (api.github.com)
  └── PyGithub (PAT) → GitHubSyncer (src/bridge/dev_integrations.py)
        ├── sync_pull_requests()   → points Qdrant memory_projects
        ├── sync_issues()          → points Qdrant memory_projects
        ├── sync_commits()         → points Qdrant memory_projects
        └── log_rate_limit()       → github_sync_log

Vault Obsidian (système de fichiers local)
  └── ObsidianIngestor (src/bridge/obsidian_ingestor.py)
        ├── Hérite de : LocalDocIngestor (src/bridge/local_doc_ingestor.py)
        ├── _extract_frontmatter(text) → dict  — PyYAML
        ├── _extract_wikilinks(text)   → list[str]
        ├── _update_obsidian_index(doc_id, links) → None
        └── ingest_file(file_path) → IngestResult  (override)

DevIntegrationManager (src/bridge/dev_integrations.py)
  ├── GitHubSyncer
  └── ObsidianIngestor
  ├── sync_github()    → dict  (orchestration complète GitHub)
  └── get_status()     → dict  (état des deux intégrations)

APScheduler (scheduler_registry.py)
  └── job "github_sync" — toutes les GITHUB_SYNC_INTERVAL minutes → DevIntegrationManager.sync_github()

LocalDocWatcher (local_doc_ingestor.py — réutilisé)
  └── surveille OBSIDIAN_VAULT_PATH → ObsidianIngestor.ingest_file()
```

### Diagramme de classes

```
LocalDocIngestor (src/bridge/local_doc_ingestor.py)
  │
  └─── ObsidianIngestor (src/bridge/obsidian_ingestor.py)
         ├── ingest_file(file_path) → IngestResult       [override]
         │     ├── [appel parent]  super().ingest_file() avec métadonnées enrichies
         │     ├── _extract_frontmatter(raw_text) → dict
         │     ├── _extract_wikilinks(raw_text)   → list[str]
         │     └── _update_obsidian_index(doc_id, source_note, links)
         ├── _extract_frontmatter(text) → dict
         │     └── yaml.safe_load(frontmatter_block) → {tags, aliases, created, modified}
         ├── _extract_wikilinks(text) → list[str]
         │     └── re.findall(r'\[\[([^\]]+)\]\]', text)
         └── _update_obsidian_index(doc_id, note_path, wikilinks)
               ├── DELETE FROM obsidian_index WHERE source_doc_id = doc_id
               └── INSERT INTO obsidian_index (source_doc_id, source_path, target_note_name, created_at)

GitHubSyncer (src/bridge/dev_integrations.py)
  ├── sync_pull_requests(repos) → list[UpsertResult]
  ├── sync_issues(repos)        → list[UpsertResult]
  ├── sync_commits(repos)       → list[UpsertResult]
  ├── _discover_repos()         → list[str]
  ├── _upsert_to_qdrant(item_type, items) → int
  ├── _log_rate_limit(response)  → None
  └── get_last_sync_status()     → dict

DevIntegrationManager (src/bridge/dev_integrations.py)
  ├── github: GitHubSyncer
  ├── obsidian: ObsidianIngestor
  ├── sync_github()  → dict
  └── get_status()   → dict
```

### Fichiers créés / modifiés

| Fichier | Action | Description |
|---------|--------|-------------|
| `src/bridge/dev_integrations.py` | Créer | `DevIntegrationManager` + `GitHubSyncer` — orchestration GitHub et status global |
| `src/bridge/obsidian_ingestor.py` | Créer | `ObsidianIngestor` — hérite de `LocalDocIngestor`, ajoute frontmatter + WikiLinks |
| `src/bridge/dev_integrations_api.py` | Créer | API REST `/api/dev/*` — sync manuelle, statut, log |
| `src/bridge/app.py` | Modifier | Startup : init `DevIntegrationManager`, démarrage watcher Obsidian, enregistrement job GitHub |
| `src/bridge/scheduler_registry.py` | Modifier | Job système `github_sync` avec intervalle configurable |
| `src/bridge/scheduler_executor.py` | Modifier | Section `dev_digest` dans les briefings |
| `src/bridge/adaptive_router.py` | Modifier | Nouveau type `knowledge_lookup` → recherche dans `docs_reference` filtré `source="obsidian"` |
| `src/bridge/knowledge_graph.py` | Modifier | Ajout `add_wikilink_relation(source, target)` pour les relations Obsidian |
| `src/bridge/admin_ui.py` | Modifier | Sous-section "GitHub" et "Obsidian" dans l'onglet "Vector DB" |
| `migrations/018_github_sync_log.py` | Créer | Tables `github_sync_log` et `obsidian_index` |
| `requirements.txt` | Modifier | Ajouter `PyGithub>=2.0`, `PyYAML>=6.0` |
| `tests/test_dev_integrations.py` | Créer | Tests unitaires et d'intégration pour GitHub + Obsidian |

---

## 3. Modèle de données

### Table `github_sync_log`

```sql
CREATE TABLE github_sync_log (
    id            TEXT PRIMARY KEY,          -- UUID v4 — identifiant de la synchronisation
    synced_at     TEXT NOT NULL,             -- Timestamp ISO 8601 UTC de fin de sync
    repos_synced  TEXT NOT NULL,             -- JSON array des repos synchronisés (ex: '["user/repo1","user/repo2"]')
    items_synced  INTEGER NOT NULL DEFAULT 0, -- Nombre total de points upsertés dans Qdrant
    status        TEXT NOT NULL,             -- 'ok' | 'error' | 'partial'
    error_message TEXT,                      -- Null si status = 'ok'
    rate_limit_remaining INTEGER,            -- Limite API restante après le dernier appel GitHub
    rate_limit_reset TEXT                    -- Timestamp ISO 8601 de remise à zéro du rate limit
);
```

`status='partial'` est utilisé lorsqu'au moins un repo a échoué mais que d'autres ont été synchronisés avec succès. `rate_limit_remaining` et `rate_limit_reset` sont extraits des en-têtes de réponse GitHub (`X-RateLimit-Remaining`, `X-RateLimit-Reset`) après le dernier appel de la synchronisation.

**Index :**

```sql
CREATE INDEX idx_github_sync_log_synced_at ON github_sync_log(synced_at);
CREATE INDEX idx_github_sync_log_status    ON github_sync_log(status);
```

### Table `obsidian_index`

```sql
CREATE TABLE obsidian_index (
    id               TEXT PRIMARY KEY,  -- UUID v4
    source_doc_id    TEXT NOT NULL,     -- doc_id dans docs_ingestion_log (clé étrangère logique)
    source_path      TEXT NOT NULL,     -- Chemin absolu de la note source
    target_note_name TEXT NOT NULL,     -- Nom de la note cible tel qu'écrit dans [[...]]
    created_at       TEXT NOT NULL      -- Timestamp ISO 8601 UTC d'insertion
);
```

`obsidian_index` est réinitialisée (DELETE + INSERT) à chaque réingestion d'une note pour garantir la cohérence des backlinks. `target_note_name` est la valeur brute extraite du WikiLink, avant résolution en `doc_id`. La résolution (mapping `target_note_name` → `doc_id`) est effectuée à la demande par `knowledge_graph.py` lors de la construction du graphe.

**Index :**

```sql
CREATE INDEX idx_obsidian_index_source_doc_id    ON obsidian_index(source_doc_id);
CREATE INDEX idx_obsidian_index_target_note_name ON obsidian_index(target_note_name);
```

### Extension de la collection Qdrant `memory_projects`

Pas de nouvelle collection. Les points GitHub sont upsertés dans `memory_projects` (collection existante). La compatibilité ascendante est assurée : les points déjà présents ne sont pas modifiés.

**Payload d'un point inséré par `GitHubSyncer` :**

```json
{
  "source":      "github",
  "repo":        "monuser/mon-projet",
  "type":        "pr",
  "title":       "feat: ajout du routeur adaptatif",
  "url":         "https://github.com/monuser/mon-projet/pull/42",
  "state":       "open",
  "labels":      ["enhancement", "v10"],
  "body_snippet":"Implémente le routeur adaptatif avec trois stratégies...",
  "author":      "monuser",
  "assignees":   ["monuser"],
  "created_at":  "2026-03-20T10:00:00Z",
  "updated_at":  "2026-03-24T08:30:00Z",
  "synced_at":   "2026-03-24T09:00:00Z"
}
```

| Champ payload | Type | Valeurs possibles | Description |
|---------------|------|-------------------|-------------|
| `source` | `str` | `"github"` | Discriminant d'origine — permet de filtrer les points GitHub dans `memory_projects` |
| `repo` | `str` | `"user/repo"` | Dépôt GitHub au format `owner/name` |
| `type` | `str` | `"pr"` \| `"issue"` \| `"commit"` | Type d'artefact GitHub |
| `title` | `str` | — | Titre de la PR, de l'issue, ou message de commit (première ligne) |
| `url` | `str` | — | URL HTML directe vers l'artefact |
| `state` | `str` | `"open"` \| `"closed"` \| `"merged"` | État de la PR ou de l'issue |
| `labels` | `list[str]` | — | Labels GitHub appliqués |
| `body_snippet` | `str` | — | 500 premiers caractères du corps (body) — null pour les commits |
| `author` | `str` | — | Login GitHub de l'auteur |
| `assignees` | `list[str]` | — | Logins des assignés |
| `created_at` | `str` | ISO 8601 | Date de création de l'artefact |
| `updated_at` | `str` | ISO 8601 | Date de dernière modification |
| `synced_at` | `str` | ISO 8601 | Timestamp de l'ingestion par nanobot-stack |

**ID de point Qdrant :** `hash(f"github:{repo}:{type}:{item_number_or_sha}")` — UUID v5 déterministe basé sur le namespace UUID DNS, garantissant l'idempotence des upserts.

Le champ vectorisé est `title + " " + body_snippet` (ou `title` seul pour les commits sans body). Le modèle sentence-transformers est identique aux autres collections.

---

## 4. Variables d'environnement

### GitHub

| Variable | Défaut | Description |
|----------|--------|-------------|
| `GITHUB_ENABLED` | `false` | Opt-in global. Si `false`, aucun appel à l'API GitHub, job scheduler non enregistré |
| `GITHUB_TOKEN` | — | **Secret** — Personal Access Token (PAT) GitHub. Portées requises : `read:user`, `repo` (lecture seule). Jamais stocké en base ni dans Qdrant |
| `GITHUB_USERNAME` | — | Login GitHub de l'utilisateur authentifié. Utilisé pour filtrer les PRs/issues assignées ou créées par l'utilisateur |
| `GITHUB_REPOS` | `""` | Liste de dépôts à synchroniser, séparés par des virgules (ex: `"user/repo1,org/repo2"`). Si vide, découverte automatique via `github.get_user().get_repos()` (dépôts avec push dans les 90 derniers jours) |
| `GITHUB_SYNC_INTERVAL` | `30` | Intervalle de synchronisation en minutes. Valeur minimale : `5`. Valeur maximale : `1440` (24h) |

### Obsidian

| Variable | Défaut | Description |
|----------|--------|-------------|
| `OBSIDIAN_VAULT_PATH` | `""` | Chemin absolu vers le dossier racine du vault Obsidian. Si vide ou absent, la fonctionnalité Obsidian est désactivée silencieusement. Le dossier doit exister au démarrage, sinon un warning est émis et la fonctionnalité est désactivée |
| `OBSIDIAN_SYNC_INTERVAL` | `3600` | Intervalle du scan batch complet en secondes (par défaut : 1h). Le watcher temps réel (`LocalDocWatcher`) est toujours actif en complément |

`OBSIDIAN_VAULT_PATH` agit comme un opt-in implicite : la présence d'une valeur non vide active la fonctionnalité. Aucune variable `OBSIDIAN_ENABLED` séparée.

---

## 5. Pipeline d'exécution

### 5.1 Synchronisation GitHub (`sync_github`)

```
DevIntegrationManager.sync_github()
  1. Vérifier GITHUB_ENABLED (retourner {"status": "disabled"} si false)
  2. GitHubSyncer._discover_repos()
       - Si GITHUB_REPOS non vide → parser la liste CSV
       - Sinon → github.Github(GITHUB_TOKEN).get_user(GITHUB_USERNAME).get_repos(type='owner', sort='pushed')
                  Filtrer : pushed_at dans les 90 derniers jours
                  Limiter à 50 dépôts maximum
  3. Initialiser un batch_id = UUID v4 pour cette synchronisation
  4. Exécuter en parallèle (asyncio.gather) pour chaque repo :
       GitHubSyncer.sync_pull_requests(repo)
       GitHubSyncer.sync_issues(repo)
       GitHubSyncer.sync_commits(repo)
  5. Consolider les résultats → total items_synced
  6. Appeler _log_rate_limit() avec la dernière réponse GitHub
  7. Insérer dans github_sync_log (id=batch_id, synced_at=now, repos_synced=[...], items_synced=N, status=...)
  8. Retourner {"status": "ok", "repos": N, "items": M, "rate_limit_remaining": K}
```

#### Sync des Pull Requests (`sync_pull_requests`)

```
GitHubSyncer.sync_pull_requests(repo_name)
  1. github.get_repo(repo_name).get_pulls(state='open', sort='updated', direction='desc')
  2. Filtrer : PR assignée à GITHUB_USERNAME OU auteur = GITHUB_USERNAME
  3. Pour chaque PR (limite 100 par repo) :
       a. Construire le payload (voir section 3)
       b. Générer l'ID Qdrant : uuid5(NAMESPACE_DNS, f"github:{repo}:pr:{pr.number}")
       c. Vectoriser title + body_snippet
       d. PointStruct(id=..., vector=..., payload=...)
  4. QdrantClient.upsert(collection_name="memory_projects", points=batch)
     Taille de batch : 50 points maximum
  5. Retourner UpsertResult(type="pr", repo=repo_name, count=N)
```

#### Sync des Issues (`sync_issues`)

```
GitHubSyncer.sync_issues(repo_name)
  1. github.get_repo(repo_name).get_issues(state='open', assignee=GITHUB_USERNAME, sort='updated')
  2. Exclure les issues qui sont en réalité des PRs (issue.pull_request is not None)
  3. Pour chaque issue (limite 100 par repo) :
       a. Construire le payload avec type="issue"
       b. Générer l'ID Qdrant : uuid5(NAMESPACE_DNS, f"github:{repo}:issue:{issue.number}")
       c. Vectoriser title + body_snippet
  4. Batch upsert dans memory_projects
  5. Retourner UpsertResult(type="issue", repo=repo_name, count=N)
```

#### Sync des Commits (`sync_commits`)

```
GitHubSyncer.sync_commits(repo_name)
  1. since = datetime.utcnow() - timedelta(days=7)
  2. github.get_repo(repo_name).get_commits(author=GITHUB_USERNAME, since=since)
  3. Pour chaque commit (limite 50 par repo) :
       a. Construire le payload avec type="commit", title=commit.commit.message.split('\n')[0]
       b. state="merged" (les commits sont toujours dans la branche principale)
       c. body_snippet=None (commits sans body structuré)
       d. Générer l'ID Qdrant : uuid5(NAMESPACE_DNS, f"github:{repo}:commit:{commit.sha[:12]}")
       e. Vectoriser title seul
  4. Batch upsert dans memory_projects
  5. Retourner UpsertResult(type="commit", repo=repo_name, count=N)
```

#### Journalisation du rate limit

```
GitHubSyncer._log_rate_limit(github_instance)
  1. rate_limit = github_instance.get_rate_limit()
  2. core = rate_limit.core
  3. logger.info(f"GitHub rate limit: {core.remaining}/{core.limit}, reset at {core.reset}")
  4. Si core.remaining < 100 :
       logger.warning(f"GitHub rate limit faible : {core.remaining} requêtes restantes")
  5. Persister dans github_sync_log (champs rate_limit_remaining, rate_limit_reset)
```

### 5.2 Ingestion Obsidian (`ObsidianIngestor.ingest_file`)

```
ObsidianIngestor.ingest_file(file_path)
  1. Vérifier que OBSIDIAN_VAULT_PATH est défini (retourner IngestResult(status='disabled') sinon)
  2. Vérifier que file_path se termine par '.md' (seul format Obsidian supporté par cet ingestor)
  3. Lire le contenu brut du fichier (UTF-8)
  4. _extract_frontmatter(raw_text) → frontmatter_dict
       a. Détecter bloc YAML : texte commençant par '---\n' et terminant par '\n---\n'
       b. yaml.safe_load(frontmatter_block)
       c. Extraire : tags (list[str]), aliases (list[str]), created (str), modified (str)
       d. Si le bloc est absent ou malformé → frontmatter_dict = {} (ne pas interrompre l'ingestion)
       e. Loguer un warning si le YAML est invalide
  5. _extract_wikilinks(raw_text) → wikilinks
       a. re.findall(r'\[\[([^\|\]]+)(?:\|[^\]]+)?\]\]', raw_text)
          — Gère les alias WikiLink : [[note|alias]] → extraire uniquement "note"
       b. Normaliser : strip() + lowercase pour la déduplication
       c. Dédupliquer : list(dict.fromkeys(wikilinks))
       d. Exclure les liens vers des URL (contiennent '://')
  6. Construire les métadonnées enrichies pour passer au parent :
       extra_metadata = {
           "source": "obsidian",
           "obsidian_tags": frontmatter_dict.get("tags", []),
           "obsidian_aliases": frontmatter_dict.get("aliases", []),
           "frontmatter_created": frontmatter_dict.get("created"),
           "frontmatter_modified": frontmatter_dict.get("modified"),
           "wikilinks_count": len(wikilinks)
       }
  7. Appeler super().ingest_file(file_path, extra_metadata=extra_metadata)
       — Le parent gère : hash, dédup, chunking, PII filter, embedding, upsert docs_reference
       — Les champs extra_metadata sont fusionnés dans le payload Qdrant
  8. Si result.status in ('indexed', 'updated') :
       _update_obsidian_index(result.doc_id, file_path, wikilinks)
       knowledge_graph.add_wikilink_relations(result.doc_id, file_path, wikilinks)
  9. Retourner result (IngestResult du parent, enrichi avec wikilinks_count)
```

#### Mise à jour de l'index WikiLink

```
ObsidianIngestor._update_obsidian_index(doc_id, source_path, wikilinks)
  1. DELETE FROM obsidian_index WHERE source_doc_id = doc_id
     — Réinitialisation complète pour cette note (idempotent)
  2. Pour chaque wikilink dans wikilinks :
       INSERT INTO obsidian_index
         (id, source_doc_id, source_path, target_note_name, created_at)
       VALUES (uuid4(), doc_id, source_path, wikilink, now_utc())
  3. logger.debug(f"Obsidian index mis à jour : {len(wikilinks)} liens pour {source_path}")
```

### 5.3 Section `dev_digest` dans les briefings

```
JobExecutor (scheduler_executor.py)
  └── section "dev_digest" :
        1. Vérifier GITHUB_ENABLED (skip silencieux si false)
        2. SELECT points FROM memory_projects
             WHERE payload.source = "github"
               AND payload.state = "open"
             ORDER BY payload.updated_at DESC
             LIMIT 10
           (via QdrantClient.scroll avec filter)
        3. Grouper par type : prs, issues, commits
        4. Construire le bloc texte pour le prompt :
             "## Activité GitHub\n"
             "### PRs ouvertes ({N}) :\n"
             "- [{title}]({url}) — {repo} ({labels})\n" pour chaque PR
             "### Issues assignées ({M}) :\n"
             "- [{title}]({url}) — {repo}\n" pour chaque issue
             "### Commits récents (7j) :\n"
             "- {title} — {repo} ({created_at})\n" pour chaque commit
        5. Retourner le bloc texte formaté
```

### 5.4 Type de routage `knowledge_lookup`

```
AdaptiveRouter (adaptive_router.py) — extension
  └── Nouveau type : "knowledge_lookup"
        - Indicateurs déclencheurs :
            Mots-clés dans la requête : "dans mes notes", "dans mon vault", "obsidian",
            "j'ai noté", "ma note sur", "est-ce que j'ai documenté", "selon mes notes"
        - Action : recherche vectorielle dans docs_reference
            avec filtre payload : source = "obsidian"
        - Retourne les N chunks les plus similaires (N = SEARCH_TOP_K, défaut 5)
        - Si aucun résultat avec score > 0.6 : message "Aucune note Obsidian trouvée pour cette requête"
        - Coût estimé : "low" (pas d'appel LLM supplémentaire pour la recherche)
```

---

## 6. Sécurité

- **`GITHUB_TOKEN` en mémoire uniquement** : le PAT GitHub est lu depuis les variables d'environnement au démarrage et injecté directement dans l'instance `github.Github()`. Il n'est jamais écrit dans SQLite, jamais inclus dans les payloads Qdrant, jamais loggé (même en mode DEBUG). Les exceptions PyGithub sont capturées et loggées sans le token
- **Portée OAuth minimale** : la documentation recommande d'utiliser un PAT avec uniquement `read:user` (informations de profil, repos publics) et `repo` en lecture seule. Les opérations d'écriture GitHub ne sont pas implémentées et aucune portée write n'est requise
- **Confinement au vault** : `ObsidianIngestor.ingest_file()` vérifie que le chemin absolu résolu commence par `OBSIDIAN_VAULT_PATH` (même mécanisme que `LocalDocIngestor` — protection path traversal). Toute tentative d'ingestion hors du vault retourne HTTP 403
- **Rate limit GitHub** : le rate limit (5 000 req/h pour un token authentifié) est vérifié après chaque appel. Si `rate_limit_remaining < 100`, un warning est émis et la synchronisation en cours s'arrête proprement (les repos déjà synchronisés sont conservés, `status='partial'` dans `github_sync_log`)
- **WikiLinks non résolus** : les WikiLinks vers des notes inexistantes sont stockés dans `obsidian_index` avec `target_note_name` mais ne génèrent pas d'erreur. La résolution est lazy (à la demande de `knowledge_graph.py`)
- **Opt-in explicite** : `GITHUB_ENABLED=false` par défaut — aucun appel réseau externe sans configuration délibérée. L'absence de `OBSIDIAN_VAULT_PATH` désactive silencieusement Obsidian
- **PII filter Obsidian** : les chunks issus du vault Obsidian passent par `PiiFilter.filter()` (hérité de `LocalDocIngestor`) avant embedding et stockage — les notes personnelles peuvent contenir des informations sensibles
- **Frontmatter YAML** : le parsing utilise `yaml.safe_load()` exclusivement — pas de `yaml.load()` — pour prévenir l'exécution de code arbitraire via un fichier YAML malveillant

---

## 7. Dépendances Python

```
PyGithub>=2.0
PyYAML>=6.0
```

`re`, `uuid`, `hashlib`, `datetime`, `pathlib` sont dans la stdlib Python. `qdrant-client`, `sentence-transformers`, `watchdog`, `apscheduler` et `pii_filter` sont déjà présents dans le projet. `LocalDocIngestor` et `LocalDocWatcher` (Sub-projet E) sont réutilisés sans modification.

**Note sur PyGithub :** à partir de la version 2.0, le paquet PyPI s'appelle `PyGithub` mais l'import Python reste `from github import Github`. Ne pas confondre avec l'ancien paquet `pygithub` (obsolète).

**Note sur PyYAML :** déjà probablement présent dans l'environnement via d'autres dépendances (APScheduler, etc.). Ajouter explicitement `PyYAML>=6.0` dans `requirements.txt` pour épingler la version et éviter des incompatibilités.

---

## 8. API REST

Préfixe : `/api/dev`

| Méthode | Endpoint | Corps / Params | Description |
|---------|----------|----------------|-------------|
| `GET` | `/status` | — | Statut global des deux intégrations (GitHub + Obsidian) |
| `POST` | `/github/sync` | `{"repos": ["user/repo"]}` (optionnel) | Déclenche une synchronisation GitHub manuelle. Si `repos` absent, utilise `GITHUB_REPOS` ou découverte automatique |
| `GET` | `/github/log` | `?limit=20&offset=0` | Historique des synchronisations GitHub depuis `github_sync_log` |
| `GET` | `/obsidian/status` | — | Statut du vault Obsidian (chemin, note count, dernière sync) |
| `POST` | `/obsidian/sync` | — | Déclenche un scan batch complet du vault (`ObsidianIngestor.ingest_directory()`) |

### Schémas de réponse

**`GET /api/dev/status` — 200 OK**

```json
{
  "github": {
    "enabled": true,
    "username": "monuser",
    "repos_configured": ["monuser/nanobot-stack"],
    "last_sync": "2026-03-24T09:00:00Z",
    "last_sync_status": "ok",
    "items_in_qdrant": 47,
    "rate_limit_remaining": 4823
  },
  "obsidian": {
    "enabled": true,
    "vault_path": "/home/user/obsidian-vault",
    "note_count": 312,
    "last_sync": "2026-03-24T08:45:00Z",
    "watcher_running": true,
    "wikilinks_indexed": 1047
  }
}
```

**`POST /api/dev/github/sync` — 200 OK**

```json
{
  "status": "ok",
  "repos_synced": ["monuser/nanobot-stack"],
  "items_synced": 12,
  "breakdown": {
    "pr": 3,
    "issue": 7,
    "commit": 2
  },
  "rate_limit_remaining": 4811,
  "rate_limit_reset": "2026-03-24T10:00:00Z"
}
```

Codes d'erreur : `503` si `GITHUB_ENABLED=false`, `429` si le rate limit GitHub est épuisé (`rate_limit_remaining < 10`), `500` si erreur PyGithub inattendue.

**`GET /api/dev/github/log` — 200 OK**

```json
{
  "items": [
    {
      "id": "550e8400-...",
      "synced_at": "2026-03-24T09:00:00Z",
      "repos_synced": ["monuser/nanobot-stack"],
      "items_synced": 12,
      "status": "ok",
      "rate_limit_remaining": 4811
    }
  ],
  "total": 48,
  "limit": 20,
  "offset": 0
}
```

**`GET /api/dev/obsidian/status` — 200 OK**

```json
{
  "enabled": true,
  "vault_path": "/home/user/obsidian-vault",
  "note_count": 312,
  "chunks_count": 4821,
  "wikilinks_indexed": 1047,
  "last_sync": "2026-03-24T08:45:00Z",
  "watcher_running": true
}
```

Code `503` si `OBSIDIAN_VAULT_PATH` non défini.

**`POST /api/dev/obsidian/sync` — 200 OK**

```json
{
  "status": "ok",
  "notes_indexed": 312,
  "notes_skipped": 8,
  "notes_errors": 1,
  "chunks_total": 4821,
  "wikilinks_extracted": 1047
}
```

---

## 9. Admin UI

Extension de l'onglet "Vector DB" existant — pas de nouvel onglet. Deux sous-sections sont ajoutées : "GitHub" et "Obsidian", affichées après le bloc "Documents Locaux" (Sub-projet E).

### Sous-section "GitHub"

Visible uniquement si `GITHUB_ENABLED=true` (retourné par `GET /api/dev/status`).

**En-tête — statistiques globales :**

| Indicateur | Source | Affichage |
|------------|--------|-----------|
| Dépôts synchronisés | `repos_configured` | Liste textuelle |
| Items dans Qdrant | `items_in_qdrant` | Compteur |
| Dernière synchronisation | `last_sync` | Date relative (ex: "il y a 12 min") |
| Statut dernière sync | `last_sync_status` | Badge : vert "OK" / orange "Partiel" / rouge "Erreur" |
| Rate limit restant | `rate_limit_remaining` | Compteur + barre de progression (max 5000) |

**Historique des synchronisations :**

Tableau affichant les 10 dernières lignes de `github_sync_log` : date, repos, items, statut, rate limit. Bouton "Voir tout" → ouvre la liste complète via `GET /api/dev/github/log`.

**Bouton "Synchroniser maintenant" :**

Déclenche `POST /api/dev/github/sync`, affiche le résultat inline (breakdown par type, rate limit mis à jour). Désactivé si `GITHUB_ENABLED=false` ou si `rate_limit_remaining < 10`.

**Si `GITHUB_ENABLED=false` :**

Message informatif : `"Intégration GitHub désactivée — définir GITHUB_ENABLED=true et GITHUB_TOKEN pour activer la synchronisation."` Le reste de la sous-section est masqué.

### Sous-section "Obsidian"

Visible si `OBSIDIAN_VAULT_PATH` est défini et non vide.

**En-tête — statistiques globales :**

| Indicateur | Source | Affichage |
|------------|--------|-----------|
| Chemin du vault | `vault_path` | Texte monospace tronqué, tooltip chemin complet |
| Notes indexées | `note_count` | Compteur |
| Chunks en base | `chunks_count` | Compteur |
| WikiLinks extraits | `wikilinks_indexed` | Compteur |
| Dernière synchronisation | `last_sync` | Date relative |
| Watcher actif | `watcher_running` | Badge vert "Actif" / rouge "Arrêté" |

**Bouton "Scanner le vault" :**

Déclenche `POST /api/dev/obsidian/sync`, affiche le résumé inline (notes indexées, ignorées, erreurs, chunks créés). Désactivé si vault non configuré.

**Si `OBSIDIAN_VAULT_PATH` non défini :**

Message informatif : `"Vault Obsidian non configuré — définir OBSIDIAN_VAULT_PATH pour activer l'intégration."` Le reste de la sous-section est masqué.

---

## 10. Tests

Fichier : `tests/test_dev_integrations.py`

### Fixtures

```
tests/fixtures/github/
  ├── pr_list.json         — Réponse API GitHub mock : 3 PRs ouvertes
  ├── issue_list.json      — Réponse API GitHub mock : 5 issues assignées
  ├── commit_list.json     — Réponse API GitHub mock : 8 commits des 7 derniers jours
  └── rate_limit.json      — Réponse mock rate_limit : remaining=4500, reset=...

tests/fixtures/obsidian/
  ├── note_simple.md       — Note sans frontmatter, sans WikiLinks
  ├── note_frontmatter.md  — Note avec frontmatter YAML complet (tags, aliases, created, modified)
  ├── note_wikilinks.md    — Note avec 5 WikiLinks dont 2 avec alias ([[note|alias]])
  ├── note_malformed.md    — Note avec frontmatter YAML invalide (YAML brisé)
  └── note_pii.md          — Note contenant un email et un numéro de téléphone fictifs
```

### Tests unitaires — GitHub

| Test | Description |
|------|-------------|
| `test_github_sync_disabled` | `GITHUB_ENABLED=false` → `sync_github()` retourne `{"status": "disabled"}`, aucun appel PyGithub |
| `test_github_discover_repos_from_env` | `GITHUB_REPOS="user/repo1,user/repo2"` → `_discover_repos()` retourne exactement ces deux repos |
| `test_github_discover_repos_auto` | `GITHUB_REPOS=""` → appel `get_user().get_repos()`, filtre par `pushed_at` dans les 90 derniers jours |
| `test_github_sync_prs_mock` | Mock `pr_list.json` → 3 points upsertés dans `memory_projects` avec `type="pr"` |
| `test_github_sync_issues_excludes_prs` | Issue avec `pull_request` non null → exclue du résultat |
| `test_github_sync_commits_window` | Commits avant la fenêtre de 7 jours → exclus |
| `test_github_qdrant_id_deterministic` | Même repo/PR → même ID Qdrant (uuid5 idempotent) |
| `test_github_qdrant_id_unique_across_types` | PR #42 et issue #42 sur le même repo → IDs différents |
| `test_github_payload_fields` | Vérifier que `source="github"`, `type`, `repo`, `url`, `state`, `labels` sont présents dans le payload |
| `test_github_body_snippet_truncated` | Body > 500 chars → `body_snippet` tronqué à 500 chars |
| `test_github_rate_limit_logged` | Après sync → `github_sync_log` contient `rate_limit_remaining` et `rate_limit_reset` |
| `test_github_rate_limit_warning_low` | `rate_limit_remaining=50` → warning loggué |
| `test_github_rate_limit_abort_sync` | `rate_limit_remaining=5` → sync interrompue, `status='partial'` dans le log |
| `test_github_partial_sync_on_repo_error` | Premier repo réussit, deuxième lève une exception → `status='partial'`, premier repo conservé dans Qdrant |
| `test_github_pagination` | Mock avec `get_pulls()` renvoyant plus de 100 PRs → pagination respectée, limite 100 par repo |
| `test_github_sync_log_inserted` | Après `sync_github()` → une ligne insérée dans `github_sync_log` avec les bons champs |
| `test_github_token_not_in_logs` | Exception PyGithub → message loggué sans le token |

### Tests unitaires — Obsidian

| Test | Description |
|------|-------------|
| `test_obsidian_extract_frontmatter_full` | `note_frontmatter.md` → `tags`, `aliases`, `created`, `modified` extraits correctement |
| `test_obsidian_extract_frontmatter_absent` | `note_simple.md` → `frontmatter_dict = {}`, aucune erreur |
| `test_obsidian_extract_frontmatter_malformed` | `note_malformed.md` → `frontmatter_dict = {}`, warning loggué, ingestion non interrompue |
| `test_obsidian_extract_wikilinks_simple` | `[[note name]]` → `["note name"]` |
| `test_obsidian_extract_wikilinks_alias` | `[[note name\|alias affiché]]` → `["note name"]` (alias exclu) |
| `test_obsidian_extract_wikilinks_dedup` | Même WikiLink deux fois → une seule occurrence dans la liste |
| `test_obsidian_extract_wikilinks_url_excluded` | `[[https://example.com]]` → exclu (contient `://`) |
| `test_obsidian_extra_metadata_in_qdrant_payload` | `source="obsidian"` et `obsidian_tags` présents dans le payload du point Qdrant |
| `test_obsidian_wikilinks_in_obsidian_index` | `note_wikilinks.md` → 5 lignes dans `obsidian_index` après ingestion |
| `test_obsidian_index_reset_on_reingest` | Réingérer une note modifiée → ancien index supprimé, nouvelles lignes insérées (idempotent) |
| `test_obsidian_pii_filter_applied` | `note_pii.md` → chunk Qdrant ne contient pas l'email ni le téléphone originaux |
| `test_obsidian_disabled_without_vault_path` | `OBSIDIAN_VAULT_PATH=""` → `ingest_file()` retourne `status='disabled'` |
| `test_obsidian_path_traversal_rejected` | Chemin hors `OBSIDIAN_VAULT_PATH` → `PermissionError` levée |
| `test_obsidian_non_md_file_skipped` | Fichier `.txt` passé à `ObsidianIngestor` → `status='skipped'`, aucun upsert Qdrant |
| `test_obsidian_knowledge_graph_called` | Après ingestion réussie → `knowledge_graph.add_wikilink_relations()` appelé avec les bons arguments |
| `test_obsidian_watcher_triggers_ingestor` | Événement Watchdog sur `new_note.md` dans le vault → `ObsidianIngestor.ingest_file()` appelé |

### Tests d'intégration (marqueur `@pytest.mark.integration`)

| Test | Description |
|------|-------------|
| `test_api_github_status_enabled` | `GET /api/dev/status` avec `GITHUB_ENABLED=true` → champ `github.enabled=true` |
| `test_api_github_status_disabled` | `GET /api/dev/status` avec `GITHUB_ENABLED=false` → `github.enabled=false` |
| `test_api_github_sync_manual` | `POST /api/dev/github/sync` → HTTP 200, champs `status`, `items_synced`, `breakdown` présents |
| `test_api_github_sync_disabled` | `POST /api/dev/github/sync` avec `GITHUB_ENABLED=false` → HTTP 503 |
| `test_api_github_log_pagination` | `GET /api/dev/github/log?limit=5` → 5 entrées maximum |
| `test_api_obsidian_status` | `GET /api/dev/obsidian/status` avec vault configuré → `note_count` cohérent avec fichiers présents |
| `test_api_obsidian_sync` | `POST /api/dev/obsidian/sync` → HTTP 200, `notes_indexed` > 0 |
| `test_api_obsidian_status_no_vault` | `GET /api/dev/obsidian/status` sans `OBSIDIAN_VAULT_PATH` → HTTP 503 |

---

## 11. Ordre d'implémentation

1. Migration `migrations/018_github_sync_log.py` — tables `github_sync_log` et `obsidian_index` + index
2. `dev_integrations.py` — `GitHubSyncer._discover_repos()` + `_log_rate_limit()` + structure `DevIntegrationManager`
3. `dev_integrations.py` — `GitHubSyncer.sync_pull_requests()` + `sync_issues()` + `sync_commits()` + `sync_github()`
4. `dev_integrations.py` — `DevIntegrationManager.get_status()`
5. `obsidian_ingestor.py` — `ObsidianIngestor._extract_frontmatter()` + `_extract_wikilinks()` + `_update_obsidian_index()`
6. `obsidian_ingestor.py` — `ObsidianIngestor.ingest_file()` (override avec appel parent + enrichissements)
7. `knowledge_graph.py` — méthode `add_wikilink_relations(doc_id, source_path, wikilinks)`
8. `dev_integrations_api.py` — endpoints REST `/api/dev/*` + montage dans `app.py`
9. `scheduler_registry.py` — job `github_sync` avec intervalle `GITHUB_SYNC_INTERVAL`
10. `scheduler_executor.py` — section `dev_digest` dans les briefings
11. `adaptive_router.py` — type de routage `knowledge_lookup` + détection de mots-clés
12. `app.py` — startup : init `DevIntegrationManager`, démarrage watcher Obsidian sur `OBSIDIAN_VAULT_PATH`, enregistrement job GitHub
13. Tests `tests/test_dev_integrations.py` — unitaires GitHub puis unitaires Obsidian puis intégration
14. `admin_ui.py` — sous-sections "GitHub" et "Obsidian" dans l'onglet "Vector DB"
