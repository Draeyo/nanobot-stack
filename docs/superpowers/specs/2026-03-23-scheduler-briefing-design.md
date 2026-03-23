# Spec : Scheduler & Briefing matinal — Sous-projet A

**Date :** 2026-03-23
**Statut :** Approuvé
**Projet :** nanobot-stack
**Scope :** Système de tâches planifiées complet avec interface Admin UI et génération de briefings configurables

---

## 1. Contexte & Objectifs

Transformer nanobot-stack d'un assistant *réactif* (répond aux questions) en assistant *proactif* (informe sans qu'on lui demande). Le scheduler est le fondement des sous-projets suivants (intégrations email/calendrier, ingestion RSS).

**Objectifs :**
- Exécuter des tâches planifiées avec expressions cron arbitraires
- Générer des briefings configurables section par section
- Livrer sur tous les canaux disponibles (ntfy, Telegram, Discord, WhatsApp)
- Gérer le cycle de vie complet depuis l'Admin UI (créer, modifier, supprimer, tester, historique)

---

## 2. Architecture

### Nouveau module : `src/bridge/scheduler.py`

`APScheduler` avec `AsyncIOScheduler` + `SQLAlchemyJobStore` (SQLite) démarre avec le bridge FastAPI. Un objet `SchedulerManager` est injecté dans l'app via `@app.on_event("startup")` / `@app.on_event("shutdown")`, en cohérence avec le pattern existant dans `app.py` (pas `lifespan`).

```
FastAPI app (on_event startup/shutdown)
  └── SchedulerManager (scheduler.py)
        ├── APScheduler (AsyncIOScheduler)
        │     └── SQLAlchemyJobStore  →  state/scheduler_jobs.db
        ├── JobExecutor
        │     ├── Collecte des sections en parallèle (async)
        │     ├── Assemble le prompt structuré
        │     ├── Sélectionne le modèle via AdaptiveRouter + exécute via LiteLLM (voir §2.2)
        │     ├── Livre via BroadcastNotifier (voir §2.1)
        │     └── Mémorise dans Qdrant (conversation_summaries)
        └── JobRegistry (jobs système prédéfinis)
```

### 2.1 BroadcastNotifier — nouveau module `src/bridge/broadcast_notifier.py`

`NotificationManager` n'existe pas dans le codebase. Deux primitives existent :
- `tools.send_notification(url, message)` — webhook ntfy/JSON, un seul canal
- `ChannelAdapter.send_message(channel_id, text)` — chat bidirectionnel, nécessite un `channel_id` par utilisateur

**Solution :** créer `BroadcastNotifier` dans `src/bridge/broadcast_notifier.py`, injecté dans `SchedulerManager`. Il expose :

```python
async def broadcast(channels: list[str], message: str) -> dict[str, bool]:
    """Livre message sur chaque canal. Retourne {canal: succès}."""
```

Valeurs légales pour `channels` : `"ntfy"`, `"telegram"`, `"discord"`, `"whatsapp"`.

Comportement par canal :
- `"ntfy"` → appelle `tools.send_notification(message)` — la fonction lit `NOTIFICATION_WEBHOOK_URL` en interne, pas de paramètre URL
- `"telegram"` / `"discord"` / `"whatsapp"` → appelle `dm_pairing.list_approved_users()`, filtre les entrées dont `platform_id.split(":")[0] == canal` (ex: `platform_id = "telegram:123456"` → préfixe `"telegram"`), extrait l'identifiant `platform_id.split(":")[1]` et appelle `channel_adapter.send_message(numeric_id, message)` pour chacun.
- Chaque canal est tenté indépendamment (échec d'un canal n'interrompt pas les autres)
- Retourne `{"ntfy": True, "telegram": False, ...}` stocké dans `job_runs.channels_ok`

### 2.2 Sélection du modèle et exécution LLM

`AdaptiveRouter` (classe dans `adaptive_router.py`) n'est **pas** appelable directement — il expose `get_model_ranking(task_type, candidates)` et `record_quality(...)`. Il ne déclenche pas d'appel LLM.

Le `JobExecutor` procède ainsi :
1. Obtenir la liste ordonnée de modèles : `adaptive_router.get_model_ranking("briefing", candidate_models)`
2. Appeler le modèle via **LiteLLM** (`litellm.acompletion(model=selected_model, messages=[...])`) avec les messages assemblés à l'étape 4 du pipeline
3. En cas d'erreur sur le modèle principal, tenter le suivant dans le ranking (même logique que les fallback chains existantes)
4. Appeler `adaptive_router.record_quality(task_type, model, score)` après réception de la réponse

`candidate_models` pour `"briefing"` : les mêmes modèles que `"classify_query"` dans `model_router.json` (modèles rapides/économiques).

### Fichiers créés / modifiés

| Fichier | Action | Description |
|---------|--------|-------------|
| `src/bridge/scheduler.py` | Créer | SchedulerManager, JobExecutor, JobRegistry |
| `src/bridge/broadcast_notifier.py` | Créer | BroadcastNotifier (fan-out multi-canal) |
| `src/bridge/scheduler_api.py` | Créer | Endpoints REST `/api/scheduler/*` |
| `src/bridge/app.py` | Modifier | Injection SchedulerManager via on_event |
| `src/bridge/admin_ui.py` | Modifier | Ajout section "Scheduler" (11ème onglet) |
| `migrations/009_scheduler.py` | Créer | Tables scheduled_jobs + job_runs (format Python) |
| `requirements.txt` | Modifier | Ajouter `apscheduler>=3.10`, `croniter>=2.0`, `sqlalchemy>=2.0` |

---

## 3. Modèle de données

### Table `scheduled_jobs`

```sql
CREATE TABLE scheduled_jobs (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    cron        TEXT NOT NULL,
    prompt      TEXT NOT NULL DEFAULT '',
    sections    TEXT NOT NULL DEFAULT '[]',  -- JSON array: ["system_health", ...]
    channels    TEXT NOT NULL DEFAULT '[]',  -- JSON array: ["ntfy", "telegram", ...]
    enabled     INTEGER NOT NULL DEFAULT 1,
    system      INTEGER NOT NULL DEFAULT 0,
    timeout_s   INTEGER NOT NULL DEFAULT 60,
    last_run    TEXT,
    last_status TEXT,                        -- "ok" | "error" | "timeout" | "running"
    last_output TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
```

`updated_at` est géré **exclusivement par la couche applicative** : toute opération `UPDATE` sur `scheduled_jobs` doit inclure `updated_at = datetime.utcnow().isoformat()`. Aucun trigger SQLite n'est utilisé (cohérence avec le reste du codebase).

### Table `job_runs`

```sql
CREATE TABLE job_runs (
    id           TEXT PRIMARY KEY,
    job_id       TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    duration_ms  INTEGER,
    status       TEXT NOT NULL,
    output       TEXT,
    error        TEXT,
    channels_ok  TEXT                        -- JSON dict: {"ntfy": true, ...}
);
```

### Valeurs légales

**`sections` (valeurs autorisées) :**
`"system_health"`, `"personal_notes"`, `"topics"`, `"reminders"`, `"weekly_summary"`, `"custom"`

**`channels` (valeurs autorisées) :**
`"ntfy"`, `"telegram"`, `"discord"`, `"whatsapp"`

La validation est effectuée à l'API (endpoint POST/PUT) : toute valeur hors de ces listes retourne HTTP 422.

---

## 4. Sections de briefing disponibles

| Clé | Contenu | Source | Fenêtre temporelle |
|-----|---------|--------|--------------------|
| `system_health` | CPU, RAM, disque, services systemd actifs/en échec | subprocess (shell restreint) | temps réel |
| `personal_notes` | Notes/mémoires ajoutées récemment | Qdrant `personal_memories` | 24h si cron < 1/jour, sinon depuis `last_run` |
| `topics` | Résumé des sujets d'intérêt | Qdrant `documents` + résumé LLM | 7 jours |
| `reminders` | Rappels explicites | Qdrant `personal_memories` filtrée tag `reminder` | tous les rappels actifs |
| `weekly_summary` | Bilan de la semaine | Qdrant `conversation_summaries` | 7 jours |
| `custom` | Prompt libre | Champ `prompt` du job | — |

**Règle fenêtre temporelle pour `personal_notes` :** si l'intervalle cron est < 24h (ex: toutes les 30 min), la fenêtre est `now - last_run` (ou 1h si `last_run` est null). Si l'intervalle est ≥ 24h, la fenêtre est 24h.

**Note coût :** la section `topics` déclenche un appel LLM supplémentaire. Elle ne doit pas être activée sur des jobs dont le cron est plus fréquent que toutes les 6 heures. L'API retourne un avertissement HTTP 400 si `topics` est activé avec un cron dont l'intervalle est < 6h.

---

## 5. Variables de template

Disponibles dans le champ `prompt` de chaque job :

| Variable | Valeur |
|----------|--------|
| `{{date}}` | Date du jour (ex: "lundi 23 mars 2026") |
| `{{time}}` | Heure d'exécution (ex: "08:00") |
| `{{day}}` | Jour de la semaine |
| `{{hostname}}` | Nom du serveur |
| `{{last_run}}` | Date de la dernière exécution réussie |
| `{{job_name}}` | Nom du job |

---

## 6. API REST

Préfixe : `/api/scheduler`

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| GET | `/jobs` | Liste tous les jobs (inclut `next_run_time` calculé via APScheduler) |
| POST | `/jobs` | Créer un job |
| GET | `/jobs/{id}` | Détail d'un job (inclut `next_run_time`) |
| PUT | `/jobs/{id}` | Modifier un job (accepte un body partiel — seuls les champs présents sont mis à jour) |
| DELETE | `/jobs/{id}` | Supprimer (retourne HTTP 403 si `system=true`) |
| POST | `/jobs/{id}/run` | Déclencher manuellement (retourne HTTP 409 si `last_status = "running"`) |
| POST | `/jobs/{id}/toggle` | Activer / désactiver |
| GET | `/jobs/{id}/history` | Historique des exécutions (`?limit=30&offset=0`, défaut limit=30) |

**`next_run_time`** : calculé via `scheduler.get_job(id).next_run_time` (APScheduler) et inclus dans la réponse `GET /jobs` et `GET /jobs/{id}`. Format ISO 8601.

**Validation à la création/modification :**
- Expression cron validée via `croniter` (HTTP 422 si invalide)
- Valeurs de `sections` et `channels` validées contre les listes autorisées (HTTP 422 si valeur inconnue)
- Au moins un canal requis
- `timeout_s` entre 10 et 300 secondes
- `topics` interdit si intervalle cron < 6h (HTTP 400 avec message explicatif)

---

## 7. Admin UI — Section "Scheduler"

11ème onglet dans l'Admin UI existante, suivant les patterns Alpine.js déjà en place.

### Vue liste
Tableau avec colonnes :
- Nom du job
- Prochain déclenchement (depuis `next_run_time` dans la réponse API, ex: "dans 14h32")
- Canaux (icônes)
- Statut dernière exécution (✅ ok / ❌ error / ⏱ timeout)
- Toggle actif/inactif
- Actions : Modifier | Historique | Lancer maintenant | Supprimer

### Formulaire création/édition
Panneau latéral avec :
- Nom du job
- Expression cron + aide (affiche "Prochain déclenchement : ..." calculé client-side via `next_run_time`)
- Timeout (slider 10-300s)
- Sections (checkboxes avec description ; `topics` grisé avec avertissement si cron < 6h)
- Canaux de livraison (checkboxes)
- Prompt personnalisé (visible si section `custom` cochée, avec liste des variables disponibles)
- Bouton "Tester maintenant" → appelle `POST /jobs/{id}/run`, affiche le résultat inline (ou HTTP 409 si déjà en cours)

### Vue historique
Liste des exécutions paginées (30 par page) : date/heure, durée, statut, canaux livrés, aperçu du message (tronqué à 300 chars, expandable).

---

## 8. Jobs système prédéfinis

Livrés préconfigurés (`system=1`, modifiables mais non-supprimables) :

| Nom | Cron | Sections | Canaux |
|-----|------|----------|--------|
| Briefing matinal | `0 8 * * *` | Toutes | Tous |
| Surveillance système | `*/30 * * * *` | `system_health` | `ntfy` |
| Bilan hebdomadaire | `0 9 * * 1` | `weekly_summary` | Tous |

---

## 9. Pipeline d'exécution

```
JobExecutor.run(job_id)
  0. Vérifier last_status != "running" (sinon skip silencieux — APScheduler max_instances=1)
  1. Écrire job_runs entry avec status="running", started_at=now
  2. Mettre à jour scheduled_jobs.last_status="running", updated_at=now
  3. Collecter les sections en parallèle (asyncio.gather, timeout=job.timeout_s)
     ├── system_health  → subprocess read-only (shell restreint)
     ├── personal_notes → Qdrant query (fenêtre selon règle §4)
     ├── topics         → Qdrant query + appel LLM supplémentaire
     ├── reminders      → Qdrant query filtrée tag "reminder"
     └── weekly_summary → Qdrant query conversation_summaries (7j)
  4. Assembler prompt structuré (sections + prompt custom + variables résolues)
  5. Sélectionner modèle via adaptive_router.get_model_ranking("briefing", candidates), appeler litellm.acompletion() avec fallback
  6. Filtrage PII sur le résultat (pipeline existant)
  7. BroadcastNotifier.broadcast(job.channels, message) — livraison parallèle
  8. Qdrant.upsert(collection="conversation_summaries", content=result)
  9. Mettre à jour job_runs: status, duration_ms, output (tronqué 2000 chars), channels_ok
 10. Mettre à jour scheduled_jobs: last_run, last_status, last_output, updated_at
```

**Gestion des erreurs :** toute exception dans les étapes 3-8 est capturée, le job est marqué `"error"` avec le message d'erreur dans `job_runs.error`. Le job est **toujours** marqué terminal (ok/error/timeout) même en cas de crash partiel.

---

## 10. Récupération après crash (startup cleanup)

Au démarrage du bridge (`on_event("startup")`), avant l'initialisation d'APScheduler, exécuter :

```sql
UPDATE scheduled_jobs
SET last_status = 'error', updated_at = ?
WHERE last_status = 'running';

UPDATE job_runs
SET status = 'error', error = 'interrupted by process restart'
WHERE status = 'running';
```

Cela garantit qu'aucun job ne reste bloqué à `"running"` après un redémarrage inattendu.

---

## 11. Sécurité & Contraintes

- **Zéro élévation** : `system_health` utilise exclusivement le shell restreint existant
- **Isolation** : `max_instances=1` par job (APScheduler) — pas d'exécutions concurrentes
- **Run manuel concurrent** : `POST /jobs/{id}/run` retourne HTTP 409 si `last_status = "running"`
- **Timeout** : configurable par job (défaut 60s), le job est interrompu et marqué `"timeout"`
- **PII** : le résultat passe par le filtre PII existant avant livraison et stockage
- **Validation cron** : expressions validées à la sauvegarde via `croniter`
- **Rate limit** : les jobs manuels (`/run`) sont soumis au rate limiter global de l'API
- **Jobs système** : `DELETE /jobs/{id}` retourne HTTP 403 si `system=1`

---

## 12. Dépendances Python à ajouter (`requirements.txt`)

```
apscheduler>=3.10
croniter>=2.0
sqlalchemy>=2.0
```

---

## 13. Format de migration

Le fichier de migration suit le format Python du projet (`migrations/NNN_name.py`) :

```python
# migrations/009_scheduler.py
VERSION = 9

def check(ctx) -> bool:
    """Idempotency guard — retourne True si la migration est déjà appliquée."""
    tables = ctx.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='scheduled_jobs'"
    ).fetchall()
    return len(tables) > 0

def migrate(ctx):
    ctx.execute("""CREATE TABLE IF NOT EXISTS scheduled_jobs (...);""")
    ctx.execute("""CREATE TABLE IF NOT EXISTS job_runs (...);""")
```

La fonction `check()` est optionnelle dans le runner mais recommandée ici car la migration crée des tables avec état réel.

---

## 14. Tests

- **Unit** : validation cron, résolution des variables de template, assemblage du prompt, règle fenêtre temporelle `personal_notes`, validation `topics` + cron < 6h
- **Unit** : `BroadcastNotifier` — mock des adapters de canaux, vérification du résultat `channels_ok`
- **Integration** : création job → déclenchement manuel → vérification `job_runs` + `last_status`
- **Integration** : startup cleanup — simuler un job bloqué à "running", vérifier reset au démarrage
- **Mock** : `BroadcastNotifier`, `adaptive_router`, Qdrant pour les tests d'exécution

---

## 15. Ordre d'implémentation

1. Migration Python (`009_scheduler.py`) + modèle de données
2. `broadcast_notifier.py` — BroadcastNotifier
3. `scheduler.py` — SchedulerManager + APScheduler setup + startup cleanup
4. `scheduler.py` — JobExecutor (sections + pipeline LLM)
5. `scheduler_api.py` — endpoints CRUD + validation
6. Jobs système prédéfinis (JobRegistry)
7. Intégration `app.py` (on_event startup/shutdown)
8. Admin UI — section Scheduler
9. Tests
