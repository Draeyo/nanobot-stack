# Spec : Intégration Email/Calendrier — Sous-projet B

**Date :** 2026-03-24
**Statut :** Approuvé
**Projet :** nanobot-stack
**Scope :** Ingestion emails IMAP et événements CalDAV/ICS dans Qdrant, avec nouvelles sections de briefing `agenda` et `email_digest`

---

## 1. Contexte & Objectifs

Connecter nanobot-stack aux sources d'information personnelles les plus importantes : la boîte mail et le calendrier. Le sous-projet B s'appuie directement sur le scheduler (sous-projet A) pour déclencher la synchronisation et enrichir les briefings existants.

**Objectifs :**
- Récupérer les emails récents non lus via IMAP (stdlib, zéro dépendance externe)
- Récupérer les événements du jour via CalDAV ou fichier `.ics` local
- Stocker les données dans deux nouvelles collections Qdrant avec TTL (pas de surcharge mémoire)
- Exposer deux nouvelles sections de briefing : `agenda` (calendrier du jour) et `email_digest` (résumé des emails importants)
- Opt-in explicite via variable d'environnement — aucun impact si non configuré

---

## 2. Architecture

### Nouveau module : `src/bridge/email_calendar.py`

Classe centrale `EmailCalendarFetcher` avec trois méthodes publiques asynchrones.

```
EmailCalendarFetcher
  ├── fetch_today_agenda() → list[dict]
  │     ├── Source CalDAV : caldav >= 1.3 (si CALENDAR_CALDAV_URL défini)
  │     └── Source ICS    : icalendar >= 5.0 (si CALENDAR_ICS_PATH défini)
  ├── fetch_recent_emails(since_hours) → list[dict]
  │     └── imaplib (stdlib) — TLS uniquement — IMAP_SSL
  └── sync_to_qdrant(qdrant_client) → dict
        ├── Upsert collection email_inbox (TTL 7 jours)
        └── Upsert collection calendar_events (TTL 30 jours)
```

### Intégration avec le scheduler existant

`scheduler_executor.py` reçoit deux nouvelles sections :

```
JobExecutor.run(job_id)
  └── Collecte sections (asyncio.gather)
        ├── [existant] system_health, personal_notes, topics, reminders, weekly_summary, custom
        ├── [nouveau]  agenda        → EmailCalendarFetcher.fetch_today_agenda()
        └── [nouveau]  email_digest  → EmailCalendarFetcher.fetch_recent_emails() + filtre + LLM
```

### Fichiers créés / modifiés

| Fichier | Action | Description |
|---------|--------|-------------|
| `src/bridge/email_calendar.py` | Créer | EmailCalendarFetcher — IMAP, CalDAV/ICS, sync Qdrant |
| `src/bridge/scheduler_executor.py` | Modifier | Ajout sections `agenda` et `email_digest` |
| `migrations/012_email_calendar.py` | Créer | Table `email_sync_log` |
| `requirements.txt` | Modifier | Ajouter `caldav>=1.3`, `icalendar>=5.0` |
| `tests/test_email_calendar.py` | Créer | Tests unitaires avec mocks |

---

## 3. Modèle de données

### Table `email_sync_log`

```sql
CREATE TABLE email_sync_log (
    id         TEXT PRIMARY KEY,
    account    TEXT NOT NULL,  -- 'imap' | 'caldav' | 'ics'
    last_synced TEXT NOT NULL,
    items_synced INTEGER DEFAULT 0,
    status     TEXT NOT NULL   -- 'ok' | 'error'
);
```

`id` : identifiant unique de la ligne de log (UUID généré à l'insertion). `account` identifie la source synchronisée. `last_synced` est un timestamp ISO 8601 UTC. `status` est mis à jour à chaque run (upsert par `account`).

### Collections Qdrant

**`email_inbox`** — TTL 7 jours

| Champ payload | Type | Description |
|---------------|------|-------------|
| `message_id` | `str` | En-tête `Message-ID` — clé de dédup |
| `subject` | `str` | Sujet de l'email |
| `sender` | `str` | Adresse expéditeur |
| `date` | `str` | Date ISO 8601 de réception |
| `snippet` | `str` | Extrait du corps (500 chars max) |
| `tags` | `list[str]` | Ex: `["unread", "flagged", "known_contact"]` |
| `source` | `str` | Toujours `"imap"` |
| `created_at` | `str` | Timestamp d'ingestion ISO 8601 |

**`calendar_events`** — TTL 30 jours

| Champ payload | Type | Description |
|---------------|------|-------------|
| `event_uid` | `str` | UID iCalendar — clé de dédup |
| `title` | `str` | Résumé / titre de l'événement |
| `start_dt` | `str` | Datetime de début ISO 8601 |
| `end_dt` | `str` | Datetime de fin ISO 8601 |
| `location` | `str` | Lieu (vide si absent) |
| `description` | `str` | Description de l'événement (500 chars max) |
| `source` | `str` | `"caldav"` ou `"ics"` |
| `created_at` | `str` | Timestamp d'ingestion ISO 8601 |

Les vecteurs denses utilisent `sentence-transformers` (même modèle que les autres collections). Le champ vectorisé est `subject + " " + snippet` pour les emails, `title + " " + description` pour les événements.

---

## 4. Variables d'environnement

### Activation

| Variable | Défaut | Description |
|----------|--------|-------------|
| `EMAIL_CALENDAR_ENABLED` | `false` | Opt-in global. Si `false`, `EmailCalendarFetcher` retourne des listes vides sans erreur |

### Source email (IMAP)

| Variable | Défaut | Description |
|----------|--------|-------------|
| `EMAIL_IMAP_HOST` | — | Serveur IMAP (ex: `imap.gmail.com`) |
| `EMAIL_IMAP_PORT` | `993` | Port IMAP TLS |
| `EMAIL_IMAP_USER` | — | Adresse email / identifiant |
| `EMAIL_IMAP_PASSWORD` | — | **Secret** — jamais stocké en base ni dans Qdrant |
| `EMAIL_IMAP_FOLDER` | `INBOX` | Dossier à surveiller |
| `EMAIL_MAX_FETCH` | `20` | Nombre maximum d'emails récupérés par run |

### Source calendrier (CalDAV ou ICS)

| Variable | Défaut | Description |
|----------|--------|-------------|
| `CALENDAR_CALDAV_URL` | — | URL du serveur CalDAV (ex: `https://nextcloud.example.com/remote.php/dav/calendars/user/personal/`) |
| `CALENDAR_USERNAME` | — | Identifiant CalDAV |
| `CALENDAR_PASSWORD` | — | **Secret** — jamais stocké en base ni dans Qdrant |
| `CALENDAR_ICS_PATH` | — | Chemin absolu vers un fichier `.ics` local (alternative à CalDAV) |

Si ni `CALENDAR_CALDAV_URL` ni `CALENDAR_ICS_PATH` ne sont définis, `fetch_today_agenda()` retourne une liste vide. Si les deux sont définis, CalDAV a la priorité.

---

## 5. Pipeline d'exécution

### Section `agenda`

```
fetch_today_agenda()
  1. Connexion CalDAV (si CALENDAR_CALDAV_URL) OU lecture fichier ICS (si CALENDAR_ICS_PATH)
  2. Filtrer les événements dans la fenêtre [now, now + 24h]
  3. Trier par start_dt croissant
  4. Retourner list[dict] avec champs : title, start_dt, end_dt, location, description
  5. Formater en liste structurée pour le prompt :
       "09:00 – Réunion équipe (salle A)"
       "14:30 – Dentiste (15 rue de la Paix)"
```

### Section `email_digest`

```
fetch_recent_emails(since_hours)
  1. Connexion IMAP via imaplib.IMAP4_SSL (TLS obligatoire, port 993)
  2. SELECT sur EMAIL_IMAP_FOLDER
  3. Recherche IMAP : UNSEEN SINCE <date> (fenêtre = depuis last_run ou 24h)
  4. Récupérer les EMAIL_MAX_FETCH emails les plus récents (FETCH body[header])
  5. Filtrage "important" (heuristique — au moins un critère) :
       - Email flagged (\\Flagged)
       - Email avec References ou In-Reply-To (thread actif)
       - Expéditeur présent dans dm_pairing.list_approved_users() (known_contact)
  6. Retourner list[dict] avec champs : message_id, subject, sender, date, snippet, tags
  7. Résumé LLM : appel litellm.acompletion() avec modèle économique
       (même logique que AdaptiveRouter "briefing" — fallback si erreur)
```

**Calcul de la fenêtre temporelle :** identique à la règle `personal_notes` du scheduler. Si le cron est < 24h, fenêtre = `now - last_synced` (depuis `email_sync_log`). Si `last_synced` est null, fenêtre = 24h. Si le cron est ≥ 24h, fenêtre fixe = 24h.

### `sync_to_qdrant(qdrant_client)`

```
sync_to_qdrant(qdrant_client) → dict
  1. Vérifier EMAIL_CALENDAR_ENABLED (retourner {"emails": 0, "events": 0} si false)
  2. fetch_recent_emails(since_hours=24) → emails
  3. fetch_today_agenda() → events
  4. Pour chaque email :
       - Calculer vecteur dense sur "subject + snippet"
       - Upsert dans email_inbox avec id = hash(message_id)
       - Dédup : si message_id déjà présent (payload filter), skip
  5. Pour chaque event :
       - Calculer vecteur dense sur "title + description"
       - Upsert dans calendar_events avec id = hash(event_uid)
       - Dédup : si event_uid déjà présent, skip
  6. Mettre à jour email_sync_log (upsert par account) :
       - account='imap', last_synced=now, items_synced=len(emails), status='ok'/'error'
       - account='caldav' ou 'ics', last_synced=now, items_synced=len(events), status='ok'/'error'
  7. Retourner {"emails": N, "events": M}
```

---

## 6. Nouvelles sections dans le scheduler

Les valeurs autorisées pour le champ `sections` des jobs sont étendues :

**Sections existantes :** `"system_health"`, `"personal_notes"`, `"topics"`, `"reminders"`, `"weekly_summary"`, `"custom"`

**Nouvelles sections :** `"agenda"`, `"email_digest"`

Ces deux sections ne sont disponibles que si `EMAIL_CALENDAR_ENABLED=true`. Si la feature flag est `false`, les sections sont silencieusement ignorées (pas d'erreur, pas de contenu dans le prompt).

| Clé | Contenu | Source | Fenêtre temporelle |
|-----|---------|--------|--------------------|
| `agenda` | Événements calendrier du jour | CalDAV ou ICS | now + 24h |
| `email_digest` | Résumé emails importants | IMAP | depuis last_run ou 24h |

**Note coût :** la section `email_digest` déclenche un appel LLM supplémentaire (résumé). Elle ne doit pas être activée sur des jobs dont le cron est plus fréquent que toutes les 2 heures. L'API retourne un avertissement HTTP 400 si `email_digest` est activé avec un cron dont l'intervalle est < 2h.

---

## 7. Sécurité

- **TLS obligatoire** : IMAP exclusivement via `imaplib.IMAP4_SSL` — toute tentative de connexion non-TLS est refusée
- **Secrets en env vars uniquement** : `EMAIL_IMAP_PASSWORD` et `CALENDAR_PASSWORD` ne sont jamais écrits en base SQLite, jamais inclus dans les payloads Qdrant, jamais loggés
- **Snippets tronqués** : les corps d'emails sont tronqués à 500 chars avant stockage dans Qdrant (réduction surface d'exposition des données personnelles)
- **Opt-in explicite** : `EMAIL_CALENDAR_ENABLED=false` par défaut — aucune connexion réseau externe sans action délibérée
- **PII** : les snippets d'emails passent par le filtre PII existant avant inclusion dans les briefings
- **Aucune donnée d'authentification dans les logs** : les exceptions `imaplib` et `caldav` sont capturées et loggées sans les credentials

---

## 8. Dépendances Python à ajouter (`requirements.txt`)

```
caldav>=1.3
icalendar>=5.0
```

`imaplib` est dans la stdlib Python — aucune dépendance externe pour la partie email.

---

## 9. Format de migration

```python
# migrations/012_email_calendar.py
VERSION = 12

def check(ctx) -> bool:
    """Idempotency guard — retourne True si la migration est déjà appliquée."""
    tables = ctx.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='email_sync_log'"
    ).fetchall()
    return len(tables) > 0

def migrate(ctx):
    ctx.execute("""
        CREATE TABLE IF NOT EXISTS email_sync_log (
            id          TEXT PRIMARY KEY,
            account     TEXT NOT NULL,
            last_synced TEXT NOT NULL,
            items_synced INTEGER DEFAULT 0,
            status      TEXT NOT NULL
        );
    """)
```

---

## 10. Admin UI — Statut de synchronisation

Extension de l'onglet "Scheduler" existant (pas de nouvel onglet).

### Bloc "Intégration Email/Calendrier"

Affiché en bas de l'onglet Scheduler, visible uniquement si `EMAIL_CALENDAR_ENABLED=true` (retourné par un nouvel endpoint `GET /api/email-calendar/status`).

Contenu du bloc :
- Statut IMAP : dernière synchronisation (`last_synced`), nombre d'items (`items_synced`), statut (`ok` / `error` en badge coloré)
- Statut CalDAV/ICS : mêmes informations
- Bouton "Synchroniser maintenant" → `POST /api/email-calendar/sync` (retourne `{"emails": N, "events": M}`)
- Si `EMAIL_CALENDAR_ENABLED=false` : message informatif "Intégration désactivée — définir `EMAIL_CALENDAR_ENABLED=true`"

### Nouvelles sections dans le formulaire de job

Les checkboxes `agenda` et `email_digest` sont ajoutées au formulaire de création/édition de job, grisées avec une info-bulle si `EMAIL_CALENDAR_ENABLED=false`. `email_digest` affiche un avertissement si le cron est < 2h (cohérent avec la validation API).

---

## 11. API REST

Préfixe : `/api/email-calendar`

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| GET | `/status` | Retourne `enabled`, `imap_last_sync`, `caldav_last_sync`, `imap_status`, `caldav_status` |
| POST | `/sync` | Déclenche `sync_to_qdrant()` manuellement, retourne `{"emails": N, "events": M}` |

Ces deux endpoints sont exposés dans un nouveau fichier `src/bridge/email_calendar_api.py` et montés dans `app.py`.

---

## 12. Tests

Fichier : `tests/test_email_calendar.py`

| Test | Description |
|------|-------------|
| `test_imap_fetch_mock` | Mock `imaplib.IMAP4_SSL`, vérifie que `fetch_recent_emails()` retourne les bons champs |
| `test_imap_dedup_by_message_id` | Deux emails avec même `message_id` → un seul upsert dans Qdrant |
| `test_caldav_fetch_mock` | Mock `caldav.DAVClient`, vérifie que `fetch_today_agenda()` filtre sur la fenêtre 24h |
| `test_ics_fetch_local` | Fichier `.ics` de test en fixture, vérifie parsing `icalendar` |
| `test_agenda_formatting` | Vérifie le format de sortie `"HH:MM – Titre (Lieu)"` |
| `test_email_digest_window_calculation` | last_synced il y a 3h → fenêtre = 3h ; last_synced null → fenêtre = 24h |
| `test_sync_to_qdrant_upsert_count` | Mock Qdrant, vérifie que `sync_to_qdrant()` retourne `{"emails": N, "events": M}` exact |
| `test_disabled_flag` | `EMAIL_CALENDAR_ENABLED=false` → toutes les méthodes retournent listes vides, aucun appel IMAP/CalDAV |
| `test_important_filter_heuristic` | Email flagged → inclus ; email sans reply et expéditeur inconnu → exclu |
| `test_tls_only` | Vérifier que `IMAP4_SSL` est utilisé, pas `IMAP4` |

---

## 13. Ordre d'implémentation

1. Migration `migrations/012_email_calendar.py` — table `email_sync_log`
2. `email_calendar.py` — `EmailCalendarFetcher` + `fetch_recent_emails()` via IMAP
3. `email_calendar.py` — `fetch_today_agenda()` via CalDAV/ICS
4. `email_calendar.py` — `sync_to_qdrant()` + dédup + mise à jour `email_sync_log`
5. `scheduler_executor.py` — ajout sections `agenda` et `email_digest` + validation cron < 2h pour `email_digest`
6. `email_calendar_api.py` — endpoints `/status` et `/sync` + montage dans `app.py`
7. Tests (`test_email_calendar.py`)
8. Admin UI — bloc statut dans l'onglet Scheduler + checkboxes dans le formulaire de job
