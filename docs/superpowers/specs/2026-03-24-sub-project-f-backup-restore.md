# Spec : Backup & Restore Automatique — Sous-projet F

**Date :** 2026-03-24
**Statut :** Spécifié
**Projet :** nanobot-stack
**Scope :** Sauvegarde automatique planifiée de toutes les données persistantes (Qdrant, SQLite, stack.env), chiffrement optionnel AES-256, stockage local et/ou S3-compatible, politique de rétention, restauration manuelle via script shell

---

## 1. Contexte & Objectifs

nanobot-stack accumule des données précieuses : collections vectorielles Qdrant (mémoire sémantique, documents, articles RSS), bases SQLite (mémoire procédurale, règles de confiance, budgets, jobs, flux RSS), et la configuration sensible `stack.env`. Aucun mécanisme de sauvegarde structuré n'existe aujourd'hui — une corruption de disque ou une mauvaise manipulation Docker rendrait la perte irréversible.

**Objectifs :**
- Sauvegarder automatiquement l'intégralité des données persistantes via un job APScheduler (défaut : 3h du matin)
- Deux stratégies complémentaires : snapshots Qdrant via REST API et copie directe des fichiers SQLite
- Archiver dans un `.tar.gz` horodaté, avec chiffrement AES-256 optionnel
- Stocker les archives localement et/ou sur un backend S3-compatible (Backblaze B2, AWS S3, MinIO)
- Appliquer une politique de rétention automatique (garder les N derniers backups)
- Permettre la restauration complète via un script shell interactif (`restore.sh`) — action délibérée, jamais automatique
- Opt-in explicite via `BACKUP_ENABLED` — aucun impact sur les instances non configurées

---

## 2. Architecture

### Nouveau module : `src/bridge/backup_manager.py`

Classe centrale `BackupManager` avec méthodes publiques clairement séparées entre la phase de backup et la phase utilitaire.

```
BackupManager
  ├── run_backup() → BackupResult
  │     ├── _snapshot_qdrant_collections() → list[Path]
  │     │     └── Pour chaque collection :
  │     │           POST /collections/{name}/snapshots  (création)
  │     │           GET  /collections/{name}/snapshots  (récupération URL)
  │     │           httpx.get(snapshot_url) → .snapshot file
  │     ├── _copy_sqlite_databases() → list[Path]
  │     │     └── shutil.copy2() pour chaque .db depuis STATE_DIR
  │     ├── _copy_stack_env() → Path
  │     │     └── shutil.copy2() de /opt/nanobot-stack/stack.env
  │     ├── _create_archive(files) → Path
  │     │     └── tarfile.open(mode='w:gz') — archive horodatée
  │     ├── _encrypt_archive(path) → Path          [si BACKUP_ENCRYPTION_KEY]
  │     │     └── Fernet(key).encrypt() → .tar.gz.enc
  │     ├── _upload_to_s3(path) → str              [si BACKUP_S3_ENABLED]
  │     │     └── boto3.client('s3').upload_file()
  │     ├── _apply_retention_policy()
  │     │     └── Trier par date, supprimer les archives > BACKUP_RETENTION_COUNT
  │     └── _write_backup_log(result)
  │           └── INSERT INTO backup_log
  │
  ├── list_backups() → list[BackupRecord]
  ├── delete_backup(backup_id) → bool
  └── get_status() → BackupStatus
        ├── Dernier backup (depuis backup_log)
        └── Prochain run (depuis APScheduler)
```

### Intégration avec le scheduler existant

`scheduler_registry.py` reçoit un nouveau job système non supprimable :

```
scheduler_registry.py
  └── Job système "Backup automatique"
        └── cron BACKUP_CRON (défaut: 0 3 * * *) → BackupManager.run_backup()
```

### Fichiers créés / modifiés

| Fichier | Action | Description |
|---------|--------|-------------|
| `src/bridge/backup_manager.py` | Créer | `BackupManager` — pipeline complet backup + gestion rétention |
| `src/bridge/backup_api.py` | Créer | API REST `/api/backup/*` — 4 endpoints |
| `src/bridge/app.py` | Modifier | Mount `backup_router`, injecter `BackupManager` au startup |
| `src/bridge/scheduler_registry.py` | Modifier | Job système backup cron |
| `src/bridge/admin_ui.py` | Modifier | Bloc "Backup" dans l'onglet "Avancé" existant |
| `migrations/015_backup_log.py` | Créer | Table `backup_log` dans `scheduler.db` |
| `src/bridge/requirements.txt` | Modifier | Ajouter `cryptography>=42.0`, `boto3>=1.34` (optionnel) |
| `scripts/backup.sh` | Créer | Script shell standalone (hors FastAPI) |
| `scripts/restore.sh` | Créer | Script shell interactif de restauration |
| `tests/test_backup_manager.py` | Créer | Tests unitaires avec mocks |

---

## 3. Modèle de données

### Table `backup_log`

Stockée dans `scheduler.db` (base partagée des jobs système).

```sql
CREATE TABLE backup_log (
    id                  TEXT PRIMARY KEY,           -- UUID v4
    started_at          TEXT NOT NULL,              -- ISO 8601 UTC
    completed_at        TEXT,                       -- NULL si en cours ou échec avant complétion
    archive_path        TEXT,                       -- Chemin absolu local (NULL si S3 uniquement)
    archive_s3_key      TEXT,                       -- Clé S3 (NULL si pas de S3)
    size_bytes          INTEGER,                    -- Taille de l'archive finale (chiffrée si applicable)
    collections_count   INTEGER NOT NULL DEFAULT 0, -- Nombre de collections Qdrant sauvegardées
    sqlite_files_count  INTEGER NOT NULL DEFAULT 0, -- Nombre de fichiers SQLite sauvegardés
    encrypted           INTEGER NOT NULL DEFAULT 0, -- 0 | 1
    status              TEXT NOT NULL,              -- 'running' | 'success' | 'error'
    error_msg           TEXT                        -- NULL si succès, message d'erreur sinon
);
```

`id` : UUID v4 généré à l'insertion. `started_at` est enregistré dès le début de `run_backup()`, avant toute opération. `completed_at` est mis à jour en fin de pipeline (succès ou erreur). `status` passe de `'running'` à `'success'` ou `'error'` en fin de run. Si le processus meurt pendant un backup, les entrées `status='running'` de plus de 2h sont considérées comme des échecs au prochain démarrage.

---

## 4. Variables d'environnement

### Activation et planification

| Variable | Défaut | Description |
|----------|--------|-------------|
| `BACKUP_ENABLED` | `false` | Opt-in global. Si `false`, le job APScheduler n'est pas enregistré et `run_backup()` retourne immédiatement |
| `BACKUP_CRON` | `0 3 * * *` | Expression cron du job de backup (APScheduler, format standard 5 champs) |

### Stockage local

| Variable | Défaut | Description |
|----------|--------|-------------|
| `BACKUP_LOCAL_PATH` | `/opt/nanobot-stack/backups/` | Répertoire local de destination des archives. Créé automatiquement s'il n'existe pas |
| `BACKUP_RETENTION_COUNT` | `7` | Nombre d'archives locales à conserver. Les plus anciennes sont supprimées après chaque backup réussi |

### Chiffrement

| Variable | Défaut | Description |
|----------|--------|-------------|
| `BACKUP_ENCRYPTION_KEY` | — | **Secret** — clé base64url 32 bytes (Fernet). Si absent, le chiffrement est désactivé. Jamais loggué, jamais inclus dans l'archive |

### Stockage S3-compatible (optionnel)

| Variable | Défaut | Description |
|----------|--------|-------------|
| `BACKUP_S3_ENABLED` | `false` | Activer l'upload S3. Si `true`, `BACKUP_S3_ENDPOINT`, `BACKUP_S3_BUCKET`, `BACKUP_S3_ACCESS_KEY` et `BACKUP_S3_SECRET_KEY` sont requis |
| `BACKUP_S3_ENDPOINT` | — | URL du endpoint S3-compatible (ex: `https://s3.us-west-004.backblazeb2.com`) |
| `BACKUP_S3_BUCKET` | — | Nom du bucket de destination |
| `BACKUP_S3_ACCESS_KEY` | — | **Secret** — identifiant d'accès S3 |
| `BACKUP_S3_SECRET_KEY` | — | **Secret** — clé secrète S3 |
| `BACKUP_S3_PREFIX` | `nanobot-backups/` | Préfixe (dossier virtuel) dans le bucket |

Au moins une destination doit être active : soit `BACKUP_LOCAL_PATH` défini (toujours actif par défaut), soit `BACKUP_S3_ENABLED=true`. Si les deux sont actifs, l'archive est copiée dans les deux destinations.

---

## 5. Pipeline d'exécution

### Backup (`BackupManager.run_backup()`)

```
run_backup()
  1. Vérifier BACKUP_ENABLED — retourner {"status": "disabled"} si false
  2. Créer répertoire temporaire sécurisé (tempfile.mkdtemp(), permissions 700)
  3. INSERT INTO backup_log (status='running', started_at=now) → backup_id
  4. _snapshot_qdrant_collections(tmp_dir) → list[Path]
       a. GET http://localhost:6333/collections → liste des collections
       b. Pour chaque collection :
            POST /collections/{name}/snapshots
            Attendre status 200 (réponse synchrone Qdrant)
            GET /collections/{name}/snapshots → récupérer le nom du snapshot
            httpx.get(/collections/{name}/snapshots/{snapshot_name}) → télécharger .snapshot
            Sauvegarder dans tmp_dir/qdrant/{name}/{snapshot_name}
       c. Retourner liste des fichiers .snapshot téléchargés
  5. _copy_sqlite_databases(tmp_dir) → list[Path]
       a. Fichiers cibles :
            /opt/nanobot-stack/rag-bridge/state/rag.db
            /opt/nanobot-stack/rag-bridge/state/trust.db
            /opt/nanobot-stack/rag-bridge/state/procedural_memory.db
            /opt/nanobot-stack/rag-bridge/state/token_budgets.db
            /opt/nanobot-stack/rag-bridge/state/scheduler.db
            /opt/nanobot-stack/rag-bridge/state/rss.db
       b. shutil.copy2(src, tmp_dir/sqlite/{filename}) pour chaque fichier existant
       c. Retourner liste des chemins copiés
  6. _copy_stack_env(tmp_dir) → Path
       a. shutil.copy2(/opt/nanobot-stack/stack.env, tmp_dir/stack.env)
       b. Retourner le chemin de la copie
  7. _create_archive(tmp_dir) → Path
       a. Nom de l'archive : nanobot-backup-{ISO8601_UTC}.tar.gz
            ex: nanobot-backup-2026-03-24T03:00:00.tar.gz
       b. tarfile.open(archive_path, 'w:gz')
       c. Ajouter récursivement tmp_dir/ dans l'archive (arcname relatif)
       d. Retourner archive_path
  8. _encrypt_archive(archive_path) → Path          [si BACKUP_ENCRYPTION_KEY défini]
       a. Lire archive en bytes
       b. key = base64.urlsafe_b64decode(BACKUP_ENCRYPTION_KEY)
       c. token = Fernet(key).encrypt(data)
       d. Écrire dans {archive_path}.enc
       e. Supprimer l'archive .tar.gz non chiffrée
       f. Retourner le chemin .tar.gz.enc
  9. Copier vers destination(s) :
       a. BACKUP_LOCAL_PATH : shutil.copy2(archive, BACKUP_LOCAL_PATH/)
       b. S3 (si BACKUP_S3_ENABLED) : _upload_to_s3(archive)
            boto3.client('s3', endpoint_url=...).upload_file(archive, bucket, key)
            key = BACKUP_S3_PREFIX + basename(archive)
  10. _apply_retention_policy()
        a. Lister les archives dans BACKUP_LOCAL_PATH triées par date (nom ou mtime)
        b. Si len(archives) > BACKUP_RETENTION_COUNT :
             Supprimer les plus anciennes (archives en trop)
        c. Mise à jour backup_log.status='deleted' pour les entrées supprimées (archive_path NULL)
  11. Nettoyer tmp_dir (shutil.rmtree)
  12. UPDATE backup_log SET status='success', completed_at=now, size_bytes=...,
          collections_count=..., sqlite_files_count=..., encrypted=...
  13. Retourner BackupResult(status='success', backup_id=..., archive_path=..., size_bytes=...)

  [En cas d'exception à n'importe quelle étape]
  → UPDATE backup_log SET status='error', completed_at=now, error_msg=str(e)
  → Nettoyer tmp_dir si existant
  → Logger l'erreur (sans inclure les secrets d'env)
  → Retourner BackupResult(status='error', error_msg=...)
```

### Restore (`scripts/restore.sh`)

La restauration est une opération manuelle, délibérée — elle n'est jamais déclenchée automatiquement.

```
restore.sh <archive_path>
  1. Vérifier que le script est lancé en tant que root ou avec sudo
  2. Vérifier existence de l'archive (chemin absolu requis)
  3. Afficher un résumé : archive, date de création, taille
  4. Demander confirmation explicite :
       "ATTENTION : cette opération va écraser toutes les données actuelles.
        Tapez 'RESTORE' pour confirmer : "
  5. Déchiffrement (si archive .enc) :
       a. Demander BACKUP_ENCRYPTION_KEY (lire depuis stdin, masqué)
       b. python3 -c "from cryptography.fernet import Fernet; ..."
       c. Produire archive .tar.gz temporaire
  6. Arrêter les services Docker :
       docker compose -f /opt/nanobot-stack/docker-compose.yml stop
  7. Extraire l'archive dans un répertoire temporaire :
       tar -xzf archive.tar.gz -C /tmp/nanobot-restore-{timestamp}/
  8. Restaurer les fichiers SQLite :
       cp /tmp/nanobot-restore-*/sqlite/*.db /opt/nanobot-stack/rag-bridge/state/
       (avec vérification d'intégrité : sqlite3 file.db "PRAGMA integrity_check;")
  9. Restaurer stack.env :
       cp /tmp/nanobot-restore-*/stack.env /opt/nanobot-stack/stack.env
       chmod 600 /opt/nanobot-stack/stack.env
  10. Restaurer les snapshots Qdrant :
        Démarrer uniquement le service Qdrant :
        docker compose -f /opt/nanobot-stack/docker-compose.yml start qdrant
        Attendre que l'API soit disponible (poll GET /healthz, max 30s)
        Pour chaque fichier .snapshot dans qdrant/ :
          Extraire le nom de la collection depuis le chemin du répertoire parent
          Supprimer la collection existante si présente :
            DELETE http://localhost:6333/collections/{name}
          Recréer via upload :
            curl -X POST http://localhost:6333/collections/{name}/snapshots/upload \
              -H "Content-Type: multipart/form-data" \
              -F "snapshot=@{snapshot_file}"
  11. Redémarrer tous les services :
        docker compose -f /opt/nanobot-stack/docker-compose.yml up -d
  12. Nettoyer /tmp/nanobot-restore-{timestamp}/
  13. Afficher récapitulatif : "Restauration terminée. Services démarrés."
```

---

## 6. Sécurité

- **Permissions des archives** : les fichiers `.tar.gz` et `.tar.gz.enc` sont créés avec `chmod 600` (lecture/écriture propriétaire uniquement). Le répertoire temporaire est créé avec `tempfile.mkdtemp()` (permissions 700 par défaut sur Linux)
- **Clé de chiffrement non loggée** : `BACKUP_ENCRYPTION_KEY` n'est jamais incluse dans les logs, les entrées `backup_log`, ni dans les messages d'erreur. En cas d'erreur de déchiffrement, le message est générique : "Déchiffrement échoué — vérifier BACKUP_ENCRYPTION_KEY"
- **Credentials S3 non loggués** : `BACKUP_S3_ACCESS_KEY` et `BACKUP_S3_SECRET_KEY` ne sont jamais exposés dans les logs ni les réponses API
- **stack.env chiffré dans l'archive** : la présence de `stack.env` dans l'archive est une raison supplémentaire d'activer le chiffrement. L'Admin UI affiche un avertissement si `BACKUP_ENCRYPTION_KEY` n'est pas défini et que le backup est activé
- **Fernet symétrique** : la clé Fernet doit être générée via `Fernet.generate_key()` et stockée dans `BACKUP_ENCRYPTION_KEY`. La même clé est requise pour la restauration — l'utilisateur est responsable de sa conservation hors du système sauvegardé
- **Nettoyage systématique** : le répertoire temporaire est supprimé en fin de pipeline (succès ou erreur), y compris les snapshots Qdrant bruts et les copies SQLite non chiffrées. Bloc `try/finally` garanti
- **Restauration root uniquement** : `restore.sh` vérifie `EUID == 0` en premier — sort avec code 1 si non-root, avec message explicatif
- **Pas d'exécution automatique de restauration** : aucun endpoint API ni job APScheduler ne peut déclencher une restauration — exclusivement `restore.sh` en ligne de commande

---

## 7. Dépendances Python

```
cryptography>=42.0
boto3>=1.34
```

`cryptography` est utilisée pour le chiffrement Fernet (AES-128-CBC + HMAC-SHA256 sous le capot de Fernet). Elle est importée inconditionnellement si `BACKUP_ENCRYPTION_KEY` est défini au démarrage — sinon le module entier est importé de façon lazy pour ne pas alourdir le démarrage.

`boto3` est importé uniquement si `BACKUP_S3_ENABLED=true`. L'import est conditionnel dans `BackupManager.__init__` :

```python
if self.s3_enabled:
    try:
        import boto3
        self._boto3 = boto3
    except ImportError:
        raise RuntimeError(
            "BACKUP_S3_ENABLED=true mais boto3 n'est pas installé. "
            "Ajouter boto3>=1.34 à requirements.txt."
        )
```

`httpx` est déjà présent (appels REST Qdrant). `tarfile`, `shutil`, `tempfile`, `uuid` sont dans la stdlib Python.

---

## 8. API REST

Préfixe : `/api/backup`

| Méthode | Endpoint | Corps / Params | Réponse | Description |
|---------|----------|---------------|---------|-------------|
| POST | `/run` | — | `BackupResult` | Déclenche un backup manuel immédiat. Réponse synchrone (attendre la fin). Si un backup est déjà en cours (`status='running'`), retourne HTTP 409 |
| GET | `/list` | `?limit=20&offset=0` | `list[BackupRecord]` | Liste les archives connues depuis `backup_log`, triées par `started_at` desc. Inclut `id`, `started_at`, `completed_at`, `archive_path`, `size_bytes`, `encrypted`, `status`, `error_msg` |
| GET | `/status` | — | `BackupStatus` | Dernier backup (`backup_log` ORDER BY started_at DESC LIMIT 1), prochain run (depuis APScheduler si `BACKUP_ENABLED=true`), statut global (enabled/disabled) |
| DELETE | `/{backup_id}` | — | `{"deleted": true}` | Supprimer l'archive locale et/ou la clé S3 associée à `backup_id`. Met à jour `backup_log.archive_path=NULL`. Retourne HTTP 404 si `backup_id` inconnu |

Ces endpoints sont exposés dans `src/bridge/backup_api.py` et montés dans `app.py` sous le préfixe `/api/backup`.

**Schémas de réponse :**

```python
class BackupRecord(BaseModel):
    id: str
    started_at: str
    completed_at: str | None
    archive_path: str | None
    archive_s3_key: str | None
    size_bytes: int | None
    collections_count: int
    sqlite_files_count: int
    encrypted: bool
    status: str          # 'running' | 'success' | 'error' | 'deleted'
    error_msg: str | None

class BackupStatus(BaseModel):
    enabled: bool
    last_backup: BackupRecord | None
    next_run_at: str | None  # ISO 8601 UTC, None si BACKUP_ENABLED=false
    backup_cron: str | None

class BackupResult(BaseModel):
    status: str          # 'success' | 'error' | 'disabled'
    backup_id: str | None
    archive_path: str | None
    size_bytes: int | None
    error_msg: str | None
```

---

## 9. Tests

Fichier : `tests/test_backup_manager.py`

| Test | Description |
|------|-------------|
| `test_backup_disabled_flag` | `BACKUP_ENABLED=false` → `run_backup()` retourne immédiatement `{"status": "disabled"}`, aucun appel Qdrant ni accès disque |
| `test_snapshot_qdrant_mock` | Mock `httpx.AsyncClient` sur `/collections` et `/collections/{name}/snapshots` — vérifier que chaque collection reçoit un `POST` de création et un téléchargement |
| `test_copy_sqlite_files` | Mock `shutil.copy2`, vérifier que les 6 bases SQLite sont copiées (bases existantes) et que les fichiers absents sont silencieusement ignorés (pas d'erreur) |
| `test_create_archive_structure` | Vérifier que le `.tar.gz` produit contient les répertoires `qdrant/`, `sqlite/` et le fichier `stack.env` |
| `test_encrypt_decrypt_roundtrip` | Chiffrer une archive fictive avec une clé Fernet de test, déchiffrer, vérifier que le contenu est identique à l'original |
| `test_encrypt_disabled_when_no_key` | Sans `BACKUP_ENCRYPTION_KEY`, l'archive est un `.tar.gz` non chiffré — vérifier l'absence de fichier `.enc` |
| `test_retention_policy_cleanup` | 10 archives présentes, `BACKUP_RETENTION_COUNT=7` → vérifier que les 3 plus anciennes sont supprimées après un backup réussi |
| `test_retention_policy_no_cleanup` | 5 archives présentes, `BACKUP_RETENTION_COUNT=7` → aucune suppression |
| `test_backup_log_insert_success` | Après un backup réussi, vérifier que `backup_log` contient une entrée `status='success'` avec `size_bytes > 0` et `completed_at` non null |
| `test_backup_log_insert_error` | Si `_snapshot_qdrant_collections` lève une exception, vérifier que `backup_log` contient `status='error'` et `error_msg` non null |
| `test_s3_upload_mock` | `BACKUP_S3_ENABLED=true`, mock `boto3.client`, vérifier que `upload_file` est appelé avec le bon bucket et la bonne clé |
| `test_s3_disabled` | `BACKUP_S3_ENABLED=false` → boto3 jamais importé ni appelé |
| `test_tmp_dir_cleanup_on_success` | Vérifier que le répertoire temporaire est supprimé après un backup réussi |
| `test_tmp_dir_cleanup_on_error` | Vérifier que le répertoire temporaire est supprimé même si le pipeline lève une exception |
| `test_api_run_backup_409_concurrent` | Si un backup est en cours (`status='running'` dans `backup_log`), `POST /api/backup/run` retourne HTTP 409 |
| `test_api_list_backups` | `GET /api/backup/list` retourne les entrées de `backup_log` triées par date desc |
| `test_api_delete_backup` | `DELETE /api/backup/{id}` supprime le fichier local et met `archive_path=NULL` dans `backup_log` |
| `test_api_status_disabled` | `BACKUP_ENABLED=false` → `GET /api/backup/status` retourne `{"enabled": false, "next_run_at": null}` |

---

## 10. Admin UI

Extension de l'onglet "Avancé" existant (pas de nouvel onglet).

### Bloc "Sauvegarde automatique"

Affiché en bas de l'onglet "Avancé", toujours visible (mais avec un badge "Désactivé" si `BACKUP_ENABLED=false`).

**En-tête du bloc :**
- Titre "Sauvegarde automatique" + badge statut : `Activé` (vert) / `Désactivé` (gris)
- Si `BACKUP_ENABLED=false` : message informatif "Sauvegarde désactivée — définir `BACKUP_ENABLED=true` dans stack.env"
- Si activé sans `BACKUP_ENCRYPTION_KEY` : avertissement orange "stack.env sera sauvegardé sans chiffrement — recommandé : définir `BACKUP_ENCRYPTION_KEY`"

**Informations affichées (données depuis `GET /api/backup/status`) :**
- Dernier backup : date relative (ex: "il y a 6h"), taille (ex: "142 MB"), statut badge vert/rouge
- Prochain backup planifié : date/heure absolue ISO (ex: "demain à 03:00")
- Expression cron active : `BACKUP_CRON`
- Destination(s) actives : "Local (`/opt/nanobot-stack/backups/`)" et/ou "S3 (`bucket-name`)"

**Actions :**
- Bouton "Sauvegarder maintenant" → `POST /api/backup/run` (confirmation requise si déjà un backup en cours)
- Pendant l'exécution : spinner + "Sauvegarde en cours..." (polling `GET /api/backup/status` toutes les 3s)

**Liste des archives (données depuis `GET /api/backup/list`) :**

Tableau avec colonnes :
- Date de création (date relative + date absolue au survol)
- Taille
- Chiffré (oui/non — badge)
- Statut (succès / erreur)
- Actions : Supprimer → `DELETE /api/backup/{id}` (confirmation requise)

Si aucune archive : "Aucune sauvegarde effectuée."

---

## 11. Scripts shell

### `scripts/backup.sh` — Backup standalone

Ce script peut être lancé indépendamment de FastAPI (utile pour les premiers tests, les crons système, ou les environnements sans APScheduler).

```bash
#!/usr/bin/env bash
# backup.sh — Backup standalone nanobot-stack
# Usage : ./backup.sh [--encrypt] [--dest /chemin/vers/backups]
# Requiert : python3, docker, curl, tar

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACK_DIR="/opt/nanobot-stack"
STATE_DIR="${STACK_DIR}/rag-bridge/state"
QDRANT_API="http://localhost:6333"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%S")
ARCHIVE_NAME="nanobot-backup-${TIMESTAMP}.tar.gz"
TMP_DIR=$(mktemp -d)
DEST_DIR="${BACKUP_LOCAL_PATH:-${STACK_DIR}/backups}"
ENCRYPT=0

# --- Parsing des arguments ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --encrypt) ENCRYPT=1; shift ;;
    --dest)    DEST_DIR="$2"; shift 2 ;;
    *)         echo "Usage: $0 [--encrypt] [--dest /path]"; exit 1 ;;
  esac
done

cleanup() { rm -rf "${TMP_DIR}"; }
trap cleanup EXIT

echo "[backup.sh] Démarrage — ${TIMESTAMP}"
echo "[backup.sh] Destination : ${DEST_DIR}"

# --- Création de la structure temporaire ---
mkdir -p "${TMP_DIR}/qdrant" "${TMP_DIR}/sqlite"

# --- Snapshots Qdrant ---
echo "[backup.sh] Récupération des collections Qdrant..."
COLLECTIONS=$(curl -sf "${QDRANT_API}/collections" | python3 -c \
  "import sys,json; data=json.load(sys.stdin); \
   print('\n'.join(c['name'] for c in data['result']['collections']))")

for COLLECTION in ${COLLECTIONS}; do
  echo "[backup.sh]   Snapshot de la collection '${COLLECTION}'..."
  mkdir -p "${TMP_DIR}/qdrant/${COLLECTION}"
  SNAPSHOT_NAME=$(curl -sf -X POST \
    "${QDRANT_API}/collections/${COLLECTION}/snapshots" | \
    python3 -c "import sys,json; print(json.load(sys.stdin)['result']['name'])")
  curl -sf "${QDRANT_API}/collections/${COLLECTION}/snapshots/${SNAPSHOT_NAME}" \
    -o "${TMP_DIR}/qdrant/${COLLECTION}/${SNAPSHOT_NAME}"
  echo "[backup.sh]   OK — ${SNAPSHOT_NAME}"
done

# --- Copie des bases SQLite ---
echo "[backup.sh] Copie des bases SQLite..."
for DB in rag.db trust.db procedural_memory.db token_budgets.db scheduler.db rss.db; do
  SRC="${STATE_DIR}/${DB}"
  if [[ -f "${SRC}" ]]; then
    cp "${SRC}" "${TMP_DIR}/sqlite/${DB}"
    echo "[backup.sh]   Copié : ${DB}"
  else
    echo "[backup.sh]   Absent (ignoré) : ${DB}"
  fi
done

# --- Copie de stack.env ---
if [[ -f "${STACK_DIR}/stack.env" ]]; then
  cp "${STACK_DIR}/stack.env" "${TMP_DIR}/stack.env"
  echo "[backup.sh] stack.env copié"
fi

# --- Création de l'archive ---
mkdir -p "${DEST_DIR}"
ARCHIVE_PATH="${DEST_DIR}/${ARCHIVE_NAME}"
echo "[backup.sh] Création de l'archive ${ARCHIVE_NAME}..."
tar -czf "${ARCHIVE_PATH}" -C "${TMP_DIR}" .
chmod 600 "${ARCHIVE_PATH}"
echo "[backup.sh] Archive créée : $(du -sh "${ARCHIVE_PATH}" | cut -f1)"

# --- Chiffrement optionnel ---
if [[ "${ENCRYPT}" -eq 1 ]]; then
  if [[ -z "${BACKUP_ENCRYPTION_KEY:-}" ]]; then
    echo "[backup.sh] ERREUR : --encrypt demandé mais BACKUP_ENCRYPTION_KEY non défini"
    exit 1
  fi
  echo "[backup.sh] Chiffrement AES-256 (Fernet)..."
  python3 - <<PYEOF
import os, base64
from cryptography.fernet import Fernet
key = os.environ['BACKUP_ENCRYPTION_KEY'].encode()
fernet = Fernet(key)
with open('${ARCHIVE_PATH}', 'rb') as f:
    data = f.read()
token = fernet.encrypt(data)
enc_path = '${ARCHIVE_PATH}.enc'
with open(enc_path, 'wb') as f:
    f.write(token)
os.chmod(enc_path, 0o600)
os.remove('${ARCHIVE_PATH}')
print(f"[backup.sh] Archive chiffrée : {enc_path}")
PYEOF
  ARCHIVE_PATH="${ARCHIVE_PATH}.enc"
fi

# --- Politique de rétention ---
RETENTION="${BACKUP_RETENTION_COUNT:-7}"
echo "[backup.sh] Politique de rétention : conserver les ${RETENTION} dernières archives"
mapfile -t ARCHIVES < <(ls -t "${DEST_DIR}"/nanobot-backup-*.tar.gz* 2>/dev/null)
TOTAL=${#ARCHIVES[@]}
if [[ "${TOTAL}" -gt "${RETENTION}" ]]; then
  TO_DELETE=$(( TOTAL - RETENTION ))
  echo "[backup.sh] Suppression de ${TO_DELETE} archive(s) ancienne(s)..."
  for i in $(seq $(( RETENTION )) $(( TOTAL - 1 ))); do
    echo "[backup.sh]   Supprimé : ${ARCHIVES[$i]}"
    rm -f "${ARCHIVES[$i]}"
  done
fi

echo "[backup.sh] Backup terminé avec succès — ${ARCHIVE_PATH}"
```

### `scripts/restore.sh` — Restauration interactive

```bash
#!/usr/bin/env bash
# restore.sh — Restauration complète nanobot-stack
# Usage : ./restore.sh <chemin_archive>
# ATTENTION : opération destructive — écrase toutes les données actuelles

set -euo pipefail

STACK_DIR="/opt/nanobot-stack"
STATE_DIR="${STACK_DIR}/rag-bridge/state"
QDRANT_API="http://localhost:6333"
COMPOSE_FILE="${STACK_DIR}/docker-compose.yml"

# --- Vérifications préliminaires ---
if [[ "${EUID}" -ne 0 ]]; then
  echo "ERREUR : restore.sh doit être lancé en tant que root (sudo ./restore.sh <archive>)"
  exit 1
fi

if [[ $# -lt 1 ]]; then
  echo "Usage : $0 <chemin_archive>"
  echo "Exemple : $0 /opt/nanobot-stack/backups/nanobot-backup-2026-03-24T03:00:00.tar.gz"
  exit 1
fi

ARCHIVE_PATH="$1"
if [[ ! -f "${ARCHIVE_PATH}" ]]; then
  echo "ERREUR : archive introuvable : ${ARCHIVE_PATH}"
  exit 1
fi

TIMESTAMP=$(date -u +"%Y%m%dT%H%M%S")
TMP_DIR=$(mktemp -d)
cleanup() { rm -rf "${TMP_DIR}"; }
trap cleanup EXIT

ARCHIVE_SIZE=$(du -sh "${ARCHIVE_PATH}" | cut -f1)
ARCHIVE_DATE=$(stat -c "%y" "${ARCHIVE_PATH}" | cut -d'.' -f1)

echo ""
echo "========================================"
echo "  nanobot-stack — Restauration complète"
echo "========================================"
echo ""
echo "  Archive  : ${ARCHIVE_PATH}"
echo "  Date     : ${ARCHIVE_DATE}"
echo "  Taille   : ${ARCHIVE_SIZE}"
echo ""
echo "  ATTENTION : cette opération va :"
echo "    - Arrêter tous les services Docker"
echo "    - Écraser toutes les bases SQLite actuelles"
echo "    - Écraser stack.env"
echo "    - Supprimer et recréer toutes les collections Qdrant"
echo ""

read -rp "Tapez 'RESTORE' pour confirmer (tout autre saisie annule) : " CONFIRM
if [[ "${CONFIRM}" != "RESTORE" ]]; then
  echo "Restauration annulée."
  exit 0
fi

WORKING_ARCHIVE="${ARCHIVE_PATH}"

# --- Déchiffrement si nécessaire ---
if [[ "${ARCHIVE_PATH}" == *.enc ]]; then
  echo ""
  echo "[restore.sh] Archive chiffrée détectée."
  read -rsp "BACKUP_ENCRYPTION_KEY : " ENC_KEY
  echo ""
  DECRYPTED_PATH="${TMP_DIR}/decrypted.tar.gz"
  python3 - <<PYEOF
import os
from cryptography.fernet import Fernet, InvalidToken
key = '${ENC_KEY}'.encode()
try:
    fernet = Fernet(key)
except Exception:
    print("ERREUR : clé invalide (doit être une clé Fernet base64url 32 bytes)")
    exit(1)
with open('${ARCHIVE_PATH}', 'rb') as f:
    token = f.read()
try:
    data = fernet.decrypt(token)
except InvalidToken:
    print("ERREUR : déchiffrement échoué — clé incorrecte ou archive corrompue")
    exit(1)
with open('${DECRYPTED_PATH}', 'wb') as f:
    f.write(data)
print("[restore.sh] Déchiffrement OK")
PYEOF
  WORKING_ARCHIVE="${DECRYPTED_PATH}"
fi

# --- Extraction de l'archive ---
echo "[restore.sh] Extraction de l'archive..."
tar -xzf "${WORKING_ARCHIVE}" -C "${TMP_DIR}/"
echo "[restore.sh] Extraction OK"

# --- Arrêt des services ---
echo "[restore.sh] Arrêt des services Docker..."
docker compose -f "${COMPOSE_FILE}" stop
echo "[restore.sh] Services arrêtés"

# --- Restauration des bases SQLite ---
echo "[restore.sh] Restauration des bases SQLite..."
if [[ -d "${TMP_DIR}/sqlite" ]]; then
  for DB_FILE in "${TMP_DIR}/sqlite/"*.db; do
    [[ -f "${DB_FILE}" ]] || continue
    DB_NAME=$(basename "${DB_FILE}")
    DEST="${STATE_DIR}/${DB_NAME}"
    # Vérification d'intégrité avant remplacement
    if sqlite3 "${DB_FILE}" "PRAGMA integrity_check;" | grep -q "ok"; then
      cp "${DB_FILE}" "${DEST}"
      echo "[restore.sh]   Restauré : ${DB_NAME}"
    else
      echo "[restore.sh]   AVERTISSEMENT : ${DB_NAME} — intégrité SQLite échouée, ignoré"
    fi
  done
else
  echo "[restore.sh]   Aucun répertoire sqlite/ dans l'archive"
fi

# --- Restauration de stack.env ---
if [[ -f "${TMP_DIR}/stack.env" ]]; then
  cp "${TMP_DIR}/stack.env" "${STACK_DIR}/stack.env"
  chmod 600 "${STACK_DIR}/stack.env"
  echo "[restore.sh] stack.env restauré"
fi

# --- Démarrage de Qdrant seul pour les snapshots ---
echo "[restore.sh] Démarrage du service Qdrant..."
docker compose -f "${COMPOSE_FILE}" start qdrant

echo "[restore.sh] Attente de l'API Qdrant (max 30s)..."
for i in $(seq 1 30); do
  if curl -sf "${QDRANT_API}/healthz" > /dev/null 2>&1; then
    echo "[restore.sh] Qdrant disponible"
    break
  fi
  if [[ "${i}" -eq 30 ]]; then
    echo "ERREUR : Qdrant ne répond pas après 30s — vérifier les logs Docker"
    exit 1
  fi
  sleep 1
done

# --- Restauration des snapshots Qdrant ---
if [[ -d "${TMP_DIR}/qdrant" ]]; then
  for COLLECTION_DIR in "${TMP_DIR}/qdrant/"*/; do
    [[ -d "${COLLECTION_DIR}" ]] || continue
    COLLECTION_NAME=$(basename "${COLLECTION_DIR}")
    SNAPSHOT_FILE=$(ls "${COLLECTION_DIR}"*.snapshot 2>/dev/null | head -1)
    [[ -f "${SNAPSHOT_FILE}" ]] || continue

    echo "[restore.sh]   Collection '${COLLECTION_NAME}' — suppression..."
    curl -sf -X DELETE "${QDRANT_API}/collections/${COLLECTION_NAME}" > /dev/null || true

    echo "[restore.sh]   Collection '${COLLECTION_NAME}' — upload snapshot..."
    HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
      -X POST "${QDRANT_API}/collections/${COLLECTION_NAME}/snapshots/upload" \
      -H "Content-Type: multipart/form-data" \
      -F "snapshot=@${SNAPSHOT_FILE}")

    if [[ "${HTTP_STATUS}" == "200" || "${HTTP_STATUS}" == "201" ]]; then
      echo "[restore.sh]   OK — ${COLLECTION_NAME}"
    else
      echo "[restore.sh]   AVERTISSEMENT : ${COLLECTION_NAME} — HTTP ${HTTP_STATUS}"
    fi
  done
else
  echo "[restore.sh]   Aucun répertoire qdrant/ dans l'archive"
fi

# --- Redémarrage complet ---
echo "[restore.sh] Redémarrage de tous les services..."
docker compose -f "${COMPOSE_FILE}" up -d
echo ""
echo "========================================"
echo "  Restauration terminée avec succès."
echo "  Vérifier les logs : docker compose logs -f"
echo "========================================"
```

---

## 12. Format de migration

```python
# migrations/015_backup_log.py
VERSION = 15

def check(ctx) -> bool:
    """Idempotency guard — retourne True si la migration est déjà appliquée."""
    import sqlite3
    from pathlib import Path

    STATE_DIR = Path("/opt/nanobot-stack/rag-bridge/state")
    db_path = STATE_DIR / "scheduler.db"
    if not db_path.exists():
        return False
    db = sqlite3.connect(str(db_path))
    try:
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='backup_log'"
        ).fetchall()}
        return "backup_log" in tables
    finally:
        db.close()

def migrate(ctx) -> None:
    import sqlite3
    from pathlib import Path

    STATE_DIR = Path("/opt/nanobot-stack/rag-bridge/state")
    db_path = STATE_DIR / "scheduler.db"
    db = sqlite3.connect(str(db_path))
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS backup_log (
                id                  TEXT PRIMARY KEY,
                started_at          TEXT NOT NULL,
                completed_at        TEXT,
                archive_path        TEXT,
                archive_s3_key      TEXT,
                size_bytes          INTEGER,
                collections_count   INTEGER NOT NULL DEFAULT 0,
                sqlite_files_count  INTEGER NOT NULL DEFAULT 0,
                encrypted           INTEGER NOT NULL DEFAULT 0,
                status              TEXT NOT NULL,
                error_msg           TEXT
            );
        """)
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_backup_log_started_at "
            "ON backup_log(started_at DESC);"
        )
        db.commit()
    finally:
        db.close()
```

---

## 13. Ordre d'implémentation

1. Migration `migrations/015_backup_log.py` — table `backup_log` dans `scheduler.db`
2. `backup_manager.py` — `BackupManager.__init__()` + lecture des variables d'environnement + `get_status()`
3. `backup_manager.py` — `_snapshot_qdrant_collections()` (appels REST Qdrant + téléchargement .snapshot)
4. `backup_manager.py` — `_copy_sqlite_databases()` + `_copy_stack_env()`
5. `backup_manager.py` — `_create_archive()` (tar.gz horodaté)
6. `backup_manager.py` — `_encrypt_archive()` (Fernet AES-256, opt-in)
7. `backup_manager.py` — `_upload_to_s3()` (boto3, opt-in) + `_apply_retention_policy()`
8. `backup_manager.py` — `run_backup()` (orchestration complète + écriture `backup_log`)
9. `backup_manager.py` — `list_backups()` + `delete_backup()`
10. `backup_api.py` — 4 endpoints + montage dans `app.py`
11. `scheduler_registry.py` — enregistrement du job système backup cron
12. `scripts/backup.sh` — script standalone
13. `scripts/restore.sh` — script de restauration interactif
14. Tests `tests/test_backup_manager.py`
15. `admin_ui.py` — bloc "Sauvegarde automatique" dans l'onglet "Avancé"
