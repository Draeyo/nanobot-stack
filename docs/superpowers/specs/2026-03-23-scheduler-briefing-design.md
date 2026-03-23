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

`APScheduler` avec `AsyncIOScheduler` + `SQLAlchemyJobStore` (SQLite) démarre avec le bridge FastAPI. Un objet `SchedulerManager` est injecté dans l'app via le pattern `lifespan` existant.

```
FastAPI app (lifespan)
  └── SchedulerManager (scheduler.py)
        ├── APScheduler (AsyncIOScheduler)
        │     └── SQLAlchemyJobStore  →  state/scheduler_jobs.db
        ├── JobExecutor
        │     ├── Collecte des sections en parallèle (async)
        │     ├── Assemble le prompt structuré
        │     ├── Route via adaptive_router (task_type: "briefing")
        │     ├── Livre via NotificationManager (ntfy/Telegram/Discord/WhatsApp)
        │     └── Mémorise dans Qdrant (conversation_summaries)
        └── JobRegistry (jobs système prédéfinis)
```

### Fichiers créés / modifiés

| Fichier | Action | Description |
|---------|--------|-------------|
| `src/bridge/scheduler.py` | Créer | SchedulerManager, JobExecutor, JobRegistry |
| `src/bridge/scheduler_api.py` | Créer | Endpoints REST `/api/scheduler/*` |
| `src/bridge/app.py` | Modifier | Injection SchedulerManager dans lifespan |
| `src/bridge/admin_ui.py` | Modifier | Ajout section "Scheduler" (11ème onglet) |
| `migrations/009_scheduler.sql` | Créer | Tables scheduled_jobs + job_runs |

---

## 3. Modèle de données

### Table `scheduled_jobs`

```sql
CREATE TABLE scheduled_jobs (
    id          TEXT PRIMARY KEY,           -- uuid4
    name        TEXT NOT NULL,              -- "Briefing matinal"
    cron        TEXT NOT NULL,              -- "0 8 * * *"
    prompt      TEXT NOT NULL,              -- template avec variables
    sections    JSON NOT NULL DEFAULT '[]', -- sections activées
    channels    JSON NOT NULL DEFAULT '[]', -- canaux de livraison
    enabled     BOOLEAN NOT NULL DEFAULT 1,
    system      BOOLEAN NOT NULL DEFAULT 0, -- jobs prédéfinis non-supprimables
    timeout_s   INTEGER NOT NULL DEFAULT 60,
    last_run    DATETIME,
    last_status TEXT,                       -- "ok" | "error" | "timeout" | "running"
    last_output TEXT,                       -- aperçu tronqué (500 chars max)
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### Table `job_runs`

```sql
CREATE TABLE job_runs (
    id           TEXT PRIMARY KEY,
    job_id       TEXT NOT NULL REFERENCES scheduled_jobs(id) ON DELETE CASCADE,
    started_at   DATETIME NOT NULL,
    duration_ms  INTEGER,
    status       TEXT NOT NULL,             -- "ok" | "error" | "timeout"
    output       TEXT,                      -- résultat tronqué à 2000 chars
    error        TEXT,                      -- message d'erreur si status != "ok"
    channels_ok  JSON                       -- canaux sur lesquels la livraison a réussi
);
```

---

## 4. Sections de briefing disponibles

| Clé | Contenu | Source |
|-----|---------|--------|
| `system_health` | CPU, RAM, disque, services systemd actifs/en échec | subprocess (shell restreint) |
| `personal_notes` | Notes/mémoires ajoutées depuis le dernier briefing | Qdrant `personal_memories` |
| `topics` | Résumé des sujets d'intérêt | Qdrant `documents` + résumé LLM |
| `reminders` | Rappels explicites | Qdrant `personal_memories` filtrée tag `reminder` |
| `weekly_summary` | Bilan de la semaine | Qdrant `conversation_summaries` (7 jours) |
| `custom` | Prompt libre | Champ `prompt` du job |

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
| GET | `/jobs` | Liste tous les jobs |
| POST | `/jobs` | Créer un job |
| GET | `/jobs/{id}` | Détail d'un job |
| PUT | `/jobs/{id}` | Modifier un job |
| DELETE | `/jobs/{id}` | Supprimer (interdit si `system=true`) |
| POST | `/jobs/{id}/run` | Déclencher manuellement |
| POST | `/jobs/{id}/toggle` | Activer / désactiver |
| GET | `/jobs/{id}/history` | 30 dernières exécutions |

**Validation à la création/modification :**
- Expression cron validée via `croniter` (refus + message d'erreur si invalide)
- Au moins un canal requis
- `timeout_s` entre 10 et 300 secondes

---

## 7. Admin UI — Section "Scheduler"

11ème onglet dans l'Admin UI existante, suivant les patterns Alpine.js + Chart.js déjà en place.

### Vue liste
Tableau avec colonnes :
- Nom du job
- Prochain déclenchement (calculé dynamiquement, ex: "dans 14h32")
- Canaux (icônes)
- Statut dernière exécution (✅ ok / ❌ error / ⏱ timeout)
- Toggle actif/inactif
- Actions : Modifier | Historique | Lancer maintenant | Supprimer

### Formulaire création/édition
Panneau latéral avec :
- Nom du job
- Expression cron + aide (affiche "Prochain déclenchement : ...")
- Timeout (slider 10-300s)
- Sections (checkboxes avec description)
- Canaux de livraison (checkboxes)
- Prompt personnalisé (visible si section `custom` cochée, avec liste des variables disponibles)
- Bouton "Tester maintenant" → exécute et affiche le résultat inline

### Vue historique
Liste des 30 dernières exécutions : date/heure, durée, statut, aperçu du message (tronqué à 300 chars, expandable).

---

## 8. Jobs système prédéfinis

Livrés préconfigurés (`system=true`, modifiables mais non-supprimables) :

| Nom | Cron | Sections | Canaux |
|-----|------|----------|--------|
| Briefing matinal | `0 8 * * *` | Toutes | Tous |
| Surveillance système | `*/30 * * * *` | `system_health` | ntfy |
| Bilan hebdomadaire | `0 9 * * 1` | `weekly_summary` | Tous |

---

## 9. Pipeline d'exécution

```
JobExecutor.run(job)
  1. Marquer job comme "running" dans SQLite
  2. Collecter les sections en parallèle (asyncio.gather, timeout global)
     ├── system_health  → subprocess read-only
     ├── personal_notes → Qdrant query (dernières 24h/7j selon fréquence)
     ├── topics         → Qdrant query + résumé LLM
     ├── reminders      → Qdrant query filtrée tag "reminder"
     └── weekly_summary → Qdrant query conversation_summaries
  3. Assembler prompt structuré (sections + prompt custom + variables résolues)
  4. adaptive_router(task_type="briefing") → modèle rapide économique
  5. Formater résultat en Markdown
  6. NotificationManager.send(channels, message) — livraison parallèle
  7. Qdrant.upsert(collection="conversation_summaries", content=result)
  8. Écrire job_runs entry
  9. Mettre à jour last_run, last_status, last_output dans scheduled_jobs
```

---

## 10. Sécurité & Contraintes

- **Zéro élévation** : `system_health` utilise exclusivement le shell restreint existant
- **Isolation** : `max_instances=1` par job (APScheduler) — pas d'exécutions concurrentes
- **Timeout** : configurable par job (défaut 60s), le job est interrompu et marqué `timeout`
- **PII** : le résultat passe par le filtre PII existant avant livraison et stockage
- **Validation cron** : expressions validées à la sauvegarde via `croniter`
- **Rate limit** : les jobs manuels (`/run`) sont soumis au rate limiter global de l'API

---

## 11. Dépendances Python à ajouter

```
apscheduler>=3.10
croniter>=2.0
```

---

## 12. Tests

- Unit : validation cron, résolution des variables de template, assemblage du prompt
- Integration : création job → déclenchement manuel → vérification job_runs
- Mock : NotificationManager, adaptive_router, Qdrant pour les tests d'exécution

---

## 13. Ordre d'implémentation suggéré

1. Migration SQL + modèle de données
2. `scheduler.py` — SchedulerManager + APScheduler setup
3. `scheduler.py` — JobExecutor (sections + pipeline LLM)
4. `scheduler_api.py` — endpoints CRUD
5. Jobs système prédéfinis (JobRegistry)
6. Intégration `app.py` (lifespan)
7. Admin UI — section Scheduler
8. Tests
