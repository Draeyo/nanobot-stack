# Spec : Ingestion de Documents Locaux — Sous-projet E

**Date :** 2026-03-24
**Statut :** Spécifié
**Projet :** nanobot-stack
**Scope :** Surveillance de dossier local, ingestion multi-format (PDF, Markdown, TXT, DOCX), chunking sémantique, déduplication par hash, extension de la collection `docs_reference`, API REST et interface d'administration

---

## 1. Contexte & Objectifs

La collection Qdrant `docs_reference` existe déjà dans nanobot-stack mais son alimentation est manuelle et sans pipeline structuré. Ce sous-projet apporte un système complet d'ingestion de documents locaux, déclenché automatiquement par la surveillance d'un dossier ou manuellement via l'API REST.

**Objectifs :**
- Surveiller un dossier configurable en temps réel (Watchdog) pour ingérer automatiquement les nouveaux fichiers et les fichiers modifiés
- Prendre en charge quatre formats : PDF (via `pypdf`), Markdown (`.md`), texte brut (`.txt`), DOCX (via `python-docx`)
- Appliquer un chunking sémantique avec taille et chevauchement configurables
- Étendre le payload de la collection `docs_reference` existante — pas de nouvelle collection
- Dédupliquer par hash SHA-256 : si le hash du fichier n'a pas changé depuis la dernière ingestion, le fichier est ignoré
- Filtrer les données personnelles (PII) avant stockage dans Qdrant (réutilisation du filtre existant `pii_filter.py`)
- Exposer une API REST pour l'ingestion manuelle, la consultation et la suppression
- Fournir un bloc de supervision dans l'onglet "Vector DB" de l'Admin UI (pas un nouvel onglet)

---

## 2. Architecture

### Nouveau module : `src/bridge/local_doc_ingestor.py`

Classe centrale `LocalDocIngestor` avec cinq méthodes publiques.

```
LocalDocIngestor (local_doc_ingestor.py)
  ├── ingest_file(file_path: str) → IngestResult
  │     ├── _detect_format(file_path) → str          — "pdf" | "md" | "txt" | "docx" | None
  │     ├── _extract_text(file_path, fmt) → str       — dispatch vers parseur format
  │     ├── _compute_hash(file_path) → str            — SHA-256 hex
  │     ├── _is_already_indexed(file_path, hash) → bool  — consultation docs_ingestion_log
  │     ├── _chunk(text, title) → list[str]           — chunking sémantique
  │     ├── _extract_metadata(file_path) → dict       — title, tags, source_path, file_type
  │     ├── PiiFilter.filter(chunk) (existant)        — anonymisation avant embedding
  │     └── QdrantClient.upsert(docs_reference, points)
  ├── ingest_directory(path: str) → list[IngestResult]
  │     └── parcourt récursivement le dossier, filtre par LOCAL_DOCS_FORMATS
  ├── delete_document(doc_id: str) → bool
  │     ├── QdrantClient.delete(docs_reference, filter doc_id)
  │     └── UPDATE docs_ingestion_log SET status='deleted'
  ├── list_documents(limit, offset) → list[dict]
  │     └── SELECT depuis docs_ingestion_log (status != 'deleted')
  └── get_status() → dict
        └── totaux fichiers, chunks, last_indexed

LocalDocWatcher (local_doc_ingestor.py)
  ├── Watchdog Observer + FileSystemEventHandler
  ├── on_created(event)  → LocalDocIngestor.ingest_file()
  └── on_modified(event) → LocalDocIngestor.ingest_file()

APScheduler job (scheduler_registry.py)
  └── "Local Docs Batch Scan" — toutes les heures → LocalDocIngestor.ingest_directory()
```

### Intégration avec le système existant

```
app.py (startup)
  └── Si LOCAL_DOCS_ENABLED=true :
        ├── Instancier LocalDocIngestor (injecté comme singleton)
        ├── Démarrer LocalDocWatcher (thread daemon)
        └── Enregistrer job APScheduler "local_docs_batch_scan"

local_docs_api.py
  ├── POST /api/docs/ingest          → LocalDocIngestor.ingest_file()
  ├── GET  /api/docs/                → LocalDocIngestor.list_documents()
  ├── DELETE /api/docs/{doc_id}      → LocalDocIngestor.delete_document()
  └── GET  /api/docs/status          → LocalDocIngestor.get_status()
```

### Fichiers créés / modifiés

| Fichier | Action | Description |
|---------|--------|-------------|
| `src/bridge/local_doc_ingestor.py` | Créer | `LocalDocIngestor` + `LocalDocWatcher` — pipeline complet |
| `src/bridge/local_docs_api.py` | Créer | API REST `/api/docs/*` |
| `src/bridge/app.py` | Modifier | Startup : init ingestor, watcher, job scheduler |
| `src/bridge/scheduler_registry.py` | Modifier | Job système "local_docs_batch_scan" toutes les heures |
| `src/bridge/admin_ui.py` | Modifier | Bloc "Documents Locaux" dans l'onglet "Vector DB" |
| `migrations/014_local_docs.py` | Créer | Table `docs_ingestion_log` |
| `requirements.txt` | Modifier | Ajouter `pypdf>=3.0`, `python-docx>=1.0`, `watchdog>=3.0` |
| `tests/test_local_doc_ingestor.py` | Créer | Tests unitaires et d'intégration |

---

## 3. Modèle de données

### Table `docs_ingestion_log`

```sql
CREATE TABLE docs_ingestion_log (
    id            TEXT PRIMARY KEY,          -- UUID v4 — identifiant du document
    file_path     TEXT NOT NULL UNIQUE,      -- Chemin absolu du fichier source
    file_hash     TEXT NOT NULL,             -- SHA-256 hex du contenu (déduplication)
    file_type     TEXT NOT NULL,             -- 'pdf' | 'md' | 'txt' | 'docx'
    chunks_count  INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL,             -- 'indexed' | 'error' | 'deleted' | 'skipped'
    error_message TEXT,                      -- Null si status != 'error'
    last_indexed  TEXT NOT NULL,             -- Timestamp ISO 8601 UTC
    created_at    TEXT NOT NULL              -- Timestamp ISO 8601 UTC — première ingestion
);
```

`id` est l'identifiant stable du document : il est utilisé comme préfixe pour les IDs des points Qdrant (`{doc_id}_{chunk_index}`). `file_path` est unique — si un fichier est réingéré après modification (nouveau hash), la ligne existante est mise à jour (upsert par `file_path`). `status='skipped'` est écrit lorsque le hash est identique à la dernière ingestion, sans aucun upsert Qdrant.

**Index :**

```sql
CREATE INDEX idx_docs_log_status ON docs_ingestion_log(status);
CREATE INDEX idx_docs_log_file_type ON docs_ingestion_log(file_type);
CREATE INDEX idx_docs_log_last_indexed ON docs_ingestion_log(last_indexed);
```

### Extension de la collection Qdrant `docs_reference`

Pas de nouvelle collection. Le payload des points existants est étendu avec les champs suivants lors de l'ingestion par `LocalDocIngestor`. Les points déjà présents qui ne proviennent pas de ce sous-projet conservent leur payload inchangé (pas de migration des données existantes).

**Payload d'un point inséré par `LocalDocIngestor` :**

```json
{
  "source_path":   "/opt/nanobot-stack/watched-docs/guide-deploiement.pdf",
  "file_type":     "pdf",
  "file_hash":     "e3b0c44298fc1c149afbf4c8996fb924...",
  "chunk_index":   2,
  "total_chunks":  15,
  "title":         "Guide de déploiement v3",
  "tags":          ["deploiement", "guide", "ops"],
  "ingested_at":   "2026-03-24T09:15:00Z",
  "doc_id":        "550e8400-e29b-41d4-a716-446655440000",
  "text":          "Contenu du chunk après filtrage PII..."
}
```

| Champ payload | Type | Description |
|---------------|------|-------------|
| `source_path` | `str` | Chemin absolu du fichier source |
| `file_type` | `str` | `"pdf"` \| `"md"` \| `"txt"` \| `"docx"` |
| `file_hash` | `str` | SHA-256 hex — permet de retrouver tous les chunks d'une version |
| `chunk_index` | `int` | Numéro du chunk (0-based) |
| `total_chunks` | `int` | Nombre total de chunks pour ce document |
| `title` | `str` | Titre extrait (premier H1 pour MD, métadonnée PDF, nom de fichier en fallback) |
| `tags` | `list[str]` | Tags auto-extraits depuis le chemin et le nom de fichier (voir section 5) |
| `ingested_at` | `str` | Timestamp ISO 8601 UTC de l'ingestion |
| `doc_id` | `str` | UUID stable du document (clé dans `docs_ingestion_log`) |
| `text` | `str` | Texte brut du chunk après PII filter — utilisé pour l'embedding |

**ID de point Qdrant :** `{doc_id}_{chunk_index:04d}` (ex: `550e8400-..._0002`). Ce schéma garantit l'unicité et permet la suppression atomique de tous les chunks d'un document par filtre payload `doc_id`.

---

## 4. Variables d'environnement

| Variable | Défaut | Description |
|----------|--------|-------------|
| `LOCAL_DOCS_ENABLED` | `false` | Opt-in global. Si `false`, watcher non démarré, API retourne HTTP 503 |
| `LOCAL_DOCS_WATCH_PATH` | `/opt/nanobot-stack/watched-docs/` | Chemin absolu du dossier surveillé. Le dossier est créé au démarrage s'il n'existe pas |
| `LOCAL_DOCS_CHUNK_SIZE` | `512` | Taille cible des chunks en tokens (approximation : 1 token ≈ 4 chars) |
| `LOCAL_DOCS_CHUNK_OVERLAP` | `50` | Chevauchement entre chunks consécutifs en tokens |
| `LOCAL_DOCS_FORMATS` | `pdf,md,txt,docx` | Formats acceptés, séparés par des virgules. Tout fichier d'une autre extension est ignoré silencieusement |

**Note sur `LOCAL_DOCS_CHUNK_SIZE` :** la valeur est exprimée en tokens estimés. L'implémentation utilise `len(text) // 4` comme approximation sans dépendance à un tokenizer externe, sauf si `tiktoken` est déjà présent dans l'environnement (auquel cas `cl100k_base` est utilisé).

---

## 5. Pipeline d'exécution

### Ingestion d'un fichier (`ingest_file`)

```
LocalDocIngestor.ingest_file(file_path)
  1. Vérifier LOCAL_DOCS_ENABLED (retourner IngestResult(status='disabled') si false)
  2. _detect_format(file_path)
       - Extension .pdf → "pdf"
       - Extension .md  → "md"
       - Extension .txt → "txt"
       - Extension .docx → "docx"
       - Autre           → None → retourner IngestResult(status='skipped', reason='unsupported_format')
  3. Vérifier format dans LOCAL_DOCS_FORMATS (retourner 'skipped' si absent)
  4. _compute_hash(file_path) → SHA-256 du contenu binaire complet
  5. _is_already_indexed(file_path, hash)
       - SELECT file_hash FROM docs_ingestion_log WHERE file_path = ?
       - Si hash identique → UPDATE last_indexed, status='skipped'
                           → retourner IngestResult(status='skipped', reason='same_hash')
       - Si hash différent (fichier modifié) → supprimer anciens chunks Qdrant (filter doc_id)
       - Si pas de ligne → insertion future (nouvelle ingestion)
  6. _extract_text(file_path, fmt)
       - "pdf"  : pypdf.PdfReader → ' '.join(page.extract_text() for page in reader.pages)
       - "md"   : pathlib.Path.read_text(encoding='utf-8')
       - "txt"  : pathlib.Path.read_text(encoding='utf-8', errors='replace')
       - "docx" : python_docx.Document → '\n'.join(p.text for p in doc.paragraphs)
       - Erreur parsing → status='error', message loggé, aucun upsert Qdrant
  7. _extract_metadata(file_path)
       - title :
           PDF  → reader.metadata.get('/Title') ou première ligne non-vide
           MD   → première ligne commençant par "# " (H1) ou nom de fichier
           TXT  → première ligne non-vide (tronquée à 80 chars) ou nom de fichier
           DOCX → doc.core_properties.title ou premier paragraphe non-vide ou nom de fichier
       - tags  :
           Décomposer le chemin relatif par rapport à LOCAL_DOCS_WATCH_PATH
           Tokeniser les segments (split sur '/', '-', '_', ' ')
           Normaliser en minuscules, retirer stopwords FR courts (de, la, le, les, un, une, du, des)
           Ajouter le file_type comme tag
           Exemple : "/opt/.../watched-docs/ops/guide-deploiement-v3.pdf"
                     → tags = ["ops", "guide", "deploiement", "v3", "pdf"]
  8. _chunk(text, title)
       - Algorithme : window glissante sur les phrases (split sur '. ', '.\n', '\n\n')
       - Construire chunks de LOCAL_DOCS_CHUNK_SIZE tokens en ajoutant des phrases
       - Chevauchement : réinjecter les LOCAL_DOCS_CHUNK_OVERLAP derniers tokens du chunk N
         au début du chunk N+1
       - Si text vide ou < LOCAL_DOCS_CHUNK_OVERLAP tokens → chunk unique
       - Retourner list[str]
  9. Pour chaque chunk :
       a. PiiFilter.filter(chunk) → chunk_filtered  (filtre PII existant src/bridge/pii_filter.py)
       b. Préparer payload Qdrant (voir section 3)
       c. Générer embedding dense (même modèle sentence-transformers que les autres collections)
       d. Construire PointStruct(id=f"{doc_id}_{chunk_index:04d}", vector=..., payload=...)
  10. QdrantClient.upsert(collection_name="docs_reference", points=batch)
        Taille de batch : 100 points maximum par requête Qdrant
  11. Upsert docs_ingestion_log :
        INSERT OR REPLACE INTO docs_ingestion_log
          (id, file_path, file_hash, file_type, chunks_count, status, last_indexed, created_at)
        VALUES (doc_id, file_path, hash, fmt, len(chunks), 'indexed', now, now_or_existing)
  12. Retourner IngestResult(status='indexed', doc_id=doc_id, chunks_count=N)
```

### Ingestion batch du dossier (`ingest_directory`)

```
LocalDocIngestor.ingest_directory(path)
  1. Vérifier LOCAL_DOCS_ENABLED
  2. pathlib.Path(path).rglob('*') → tous les fichiers
  3. Filtrer par extension selon LOCAL_DOCS_FORMATS
  4. Pour chaque fichier : ingest_file(file_path)
     - Les erreurs individuelles sont loggées mais n'interrompent pas le batch
  5. Retourner list[IngestResult] — résumé : indexed=N, skipped=M, errors=K
```

### Watcher temps réel (`LocalDocWatcher`)

```
LocalDocWatcher.start(path, ingestor)
  1. watchdog.observers.Observer()
  2. FileSystemEventHandler.on_created(event)
       - Ignorer les événements sur dossiers (is_directory=True)
       - Ignorer les fichiers temporaires (extension .tmp, .part, préfixe '.')
       - Délai anti-rebond : attendre 2 secondes après le dernier événement pour un même chemin
         (évite les ingestions partielles sur les fichiers en cours d'écriture)
       - ingestor.ingest_file(event.src_path)
  3. FileSystemEventHandler.on_modified(event)
       - Même logique que on_created
  4. Observer.schedule(handler, path, recursive=True)
  5. Observer.start() — thread daemon (n'empêche pas l'arrêt de l'application)
  6. Arrêt propre : Observer.stop() + Observer.join() dans le shutdown hook FastAPI
```

### Suppression d'un document (`delete_document`)

```
LocalDocIngestor.delete_document(doc_id)
  1. Vérifier existence dans docs_ingestion_log (retourner False si absent ou déjà 'deleted')
  2. QdrantClient.delete(
       collection_name="docs_reference",
       points_selector=FilterSelector(
         filter=Filter(must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))])
       )
     )
  3. UPDATE docs_ingestion_log SET status='deleted', last_indexed=now WHERE id=doc_id
  4. Retourner True
```

---

## 6. Sécurité

- **Confinement au dossier surveillé** : `ingest_file()` vérifie que le chemin absolu résolu commence bien par `LOCAL_DOCS_WATCH_PATH` (protection contre les path traversal via l'API manuelle). Toute tentative d'ingestion d'un fichier hors du dossier autorisé retourne HTTP 403
- **Opt-in explicite** : `LOCAL_DOCS_ENABLED=false` par défaut — aucune surveillance de fichier ni ingestion sans action délibérée
- **PII filter obligatoire** : chaque chunk passe par `PiiFilter.filter()` avant embedding et avant stockage dans Qdrant — les données personnelles détectées sont remplacées par des tokens génériques
- **Aucun secret dans le payload** : le payload Qdrant ne contient que les métadonnées listées en section 3, jamais de variables d'environnement ni de tokens
- **Taille maximale par fichier** : les fichiers dépassant 50 Mo sont rejetés avec `status='error'` (protection mémoire — le texte extrait peut être bien plus volumineux que le binaire)
- **Rate limit API** : les endpoints `/api/docs/*` sont soumis au rate limiter global de l'application FastAPI
- **Validation du chemin (API)** : `POST /api/docs/ingest` accepte uniquement un `file_path` absolu pointant vers un fichier existant dans `LOCAL_DOCS_WATCH_PATH`. Tout autre chemin retourne HTTP 422
- **Fichiers temporaires ignorés** : le watcher ignore les fichiers dont le nom commence par `.` ou se termine par `.tmp`, `.part`, `.swp` pour éviter les ingestions partielles

---

## 7. Dépendances Python

```
pypdf>=3.0
python-docx>=1.0
watchdog>=3.0
```

`pathlib`, `hashlib`, `asyncio` sont dans la stdlib Python. `sentence-transformers` et `qdrant-client` sont déjà présents. `pii_filter` est interne au projet.

**Note :** `pypdf` remplace l'ancien `PyPDF2` (déprécié depuis 3.x). Le nom d'import est `import pypdf` (pas `import pypdf2`).

---

## 8. API REST

Préfixe : `/api/docs`

| Méthode | Endpoint | Corps / Params | Description |
|---------|----------|----------------|-------------|
| `POST` | `/ingest` | `{"file_path": "/opt/.../doc.pdf"}` | Déclenche l'ingestion d'un fichier. Retourne `IngestResult` |
| `GET` | `/` | `?limit=20&offset=0&file_type=pdf&status=indexed` | Liste les documents indexés avec métadonnées |
| `DELETE` | `/{doc_id}` | — | Supprime tous les chunks Qdrant + marque comme 'deleted' en base |
| `GET` | `/status` | — | Résumé global : total fichiers, total chunks, last_indexed, breakdown par type |

### Schémas de réponse

**`POST /api/docs/ingest` — 200 OK**

```json
{
  "doc_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "indexed",
  "file_path": "/opt/nanobot-stack/watched-docs/guide.pdf",
  "file_type": "pdf",
  "chunks_count": 15,
  "title": "Guide de déploiement v3",
  "tags": ["guide", "deploiement", "v3", "pdf"]
}
```

Codes d'erreur : `422` si `file_path` invalide ou hors `LOCAL_DOCS_WATCH_PATH`, `503` si `LOCAL_DOCS_ENABLED=false`, `500` si erreur de parsing.

**`GET /api/docs/` — 200 OK**

```json
{
  "items": [
    {
      "doc_id": "550e8400-...",
      "file_path": "/opt/.../guide.pdf",
      "file_type": "pdf",
      "chunks_count": 15,
      "status": "indexed",
      "last_indexed": "2026-03-24T09:15:00Z",
      "title": "Guide de déploiement v3",
      "tags": ["guide", "deploiement", "v3"]
    }
  ],
  "total": 42,
  "limit": 20,
  "offset": 0
}
```

**`DELETE /api/docs/{doc_id}` — 200 OK**

```json
{"deleted": true, "doc_id": "550e8400-..."}
```

Code `404` si `doc_id` inconnu.

**`GET /api/docs/status` — 200 OK**

```json
{
  "enabled": true,
  "watch_path": "/opt/nanobot-stack/watched-docs/",
  "total_files": 42,
  "total_chunks": 687,
  "last_indexed": "2026-03-24T09:15:00Z",
  "breakdown": {
    "pdf": {"files": 20, "chunks": 350},
    "md":  {"files": 15, "chunks": 210},
    "txt": {"files": 5,  "chunks": 80},
    "docx": {"files": 2, "chunks": 47}
  },
  "watcher_running": true
}
```

---

## 9. Tests

Fichier : `tests/test_local_doc_ingestor.py`

### Fixtures

```
tests/fixtures/docs/
  ├── sample.pdf          — PDF 3 pages, titre "Test Document", texte connu
  ├── sample.md           — Markdown avec H1, liste, code block
  ├── sample.txt          — Texte brut 2000 chars
  ├── sample.docx         — DOCX avec titre de propriété et 5 paragraphes
  └── sample_pii.txt      — Fichier contenant un numéro de téléphone et un email fictifs
```

### Tests unitaires

| Test | Description |
|------|-------------|
| `test_detect_format_pdf` | Extension `.pdf` → `"pdf"` |
| `test_detect_format_unsupported` | Extension `.xlsx` → `None` |
| `test_compute_hash_stable` | Deux appels consécutifs sur le même fichier → même SHA-256 |
| `test_compute_hash_changes` | Hash différent après modification du contenu |
| `test_extract_text_pdf` | `sample.pdf` → texte non vide contenant les mots clés connus |
| `test_extract_text_md` | `sample.md` → texte brut (sans syntaxe Markdown) |
| `test_extract_text_txt` | `sample.txt` → contenu identique au fichier |
| `test_extract_text_docx` | `sample.docx` → paragraphes concaténés |
| `test_extract_title_md_h1` | Premier `# Titre` extrait comme titre |
| `test_extract_title_fallback` | Fichier sans titre explicite → nom de fichier |
| `test_tags_from_path` | Chemin `watched-docs/ops/guide-deploy.pdf` → tags `["ops", "guide", "deploy", "pdf"]` |
| `test_chunk_basic` | Texte de 2000 chars → plusieurs chunks de taille ≤ `LOCAL_DOCS_CHUNK_SIZE * 4` chars |
| `test_chunk_overlap` | Dernier token du chunk N apparaît dans le chunk N+1 |
| `test_chunk_small_text` | Texte < `LOCAL_DOCS_CHUNK_OVERLAP` tokens → exactement 1 chunk |
| `test_dedup_same_hash` | Ingérer `sample.txt` deux fois → deuxième appel retourne `status='skipped'`, aucun upsert Qdrant |
| `test_dedup_changed_hash` | Modifier le contenu entre deux ingestions → deuxième appel retourne `status='indexed'`, anciens chunks Qdrant supprimés |
| `test_pii_filter_applied` | `sample_pii.txt` ingéré → chunks dans Qdrant ne contiennent pas le numéro de téléphone ni l'email originaux |
| `test_ingest_file_pdf` | Ingestion complète `sample.pdf` — mock Qdrant, vérifier nombre de points upsertés = `chunks_count` |
| `test_ingest_file_unsupported_format` | Extension `.xlsx` → `IngestResult(status='skipped')`, aucun appel Qdrant |
| `test_ingest_file_outside_watch_path` | Chemin hors `LOCAL_DOCS_WATCH_PATH` → `ValueError` ou `PermissionError` |
| `test_delete_document` | Mock Qdrant, vérifier `delete()` appelé avec filtre `doc_id` correct, ligne marquée 'deleted' en SQLite |
| `test_delete_unknown_doc_id` | `doc_id` absent → retourner `False` |
| `test_list_documents` | 3 documents indexés, 1 supprimé → `list_documents()` retourne 3 entrées |
| `test_get_status_breakdown` | Vérifier que `breakdown` contient les bons compteurs par type de fichier |
| `test_disabled_flag` | `LOCAL_DOCS_ENABLED=false` → `ingest_file()` retourne `status='disabled'`, aucun appel Qdrant ni SQLite |
| `test_file_size_limit` | Fichier > 50 Mo (mock) → `status='error'`, message d'erreur dans la table |
| `test_watcher_ignores_temp_files` | Événement Watchdog sur `.gitignore.swp` → aucun appel à `ingest_file()` |
| `test_watcher_debounce` | Deux événements `on_modified` en < 2s sur le même fichier → un seul appel `ingest_file()` |
| `test_batch_ingestion_partial_errors` | 3 fichiers dont 1 corrompu → 2 ingérés, 1 erreur, batch non interrompu |

### Tests d'intégration (marqueur `@pytest.mark.integration`)

| Test | Description |
|------|-------------|
| `test_api_ingest_endpoint` | `POST /api/docs/ingest` avec `sample.md` → HTTP 200, `status='indexed'` |
| `test_api_ingest_outside_path` | `POST /api/docs/ingest` hors `LOCAL_DOCS_WATCH_PATH` → HTTP 422 |
| `test_api_list_and_delete` | Ingérer → lister → supprimer → vérifier absent de la liste |
| `test_api_status_endpoint` | `GET /api/docs/status` → `total_files` cohérent avec données en base |
| `test_api_disabled` | `LOCAL_DOCS_ENABLED=false` → tous les endpoints retournent HTTP 503 |

---

## 10. Admin UI

Extension de l'onglet "Vector DB" existant — pas de nouvel onglet.

### Bloc "Documents Locaux"

Affiché en bas de l'onglet "Vector DB", après les informations sur les collections Qdrant existantes. Visible uniquement si `LOCAL_DOCS_ENABLED=true` (retourné par `GET /api/docs/status`).

**En-tête du bloc — statistiques globales :**

| Indicateur | Source | Affichage |
|------------|--------|-----------|
| Fichiers indexés | `total_files` | Compteur |
| Chunks en base | `total_chunks` | Compteur |
| Dernière ingestion | `last_indexed` | Date relative (ex: "il y a 3 min") |
| Watcher actif | `watcher_running` | Badge vert "Actif" / rouge "Arrêté" |
| Chemin surveillé | `watch_path` | Texte monospace tronqué |

**Tableau des documents :**

Colonnes : Nom du fichier (tronqué, tooltip chemin complet) | Type | Chunks | Dernier indexé | Statut | Actions

- Statut affiché en badge coloré : `indexed` (vert), `error` (rouge), `skipped` (gris), `deleted` (barré)
- Colonne "Actions" : bouton "Supprimer" → `DELETE /api/docs/{doc_id}` avec confirmation
- Pagination côté client : 20 documents par page avec boutons Précédent/Suivant
- Filtre par type (dropdown : Tous / PDF / Markdown / TXT / DOCX)

**Bouton "Ingérer un fichier manuellement" :**

- Champ texte pour saisir un chemin absolu (ou URL vers un fichier local)
- Bouton "Ingérer" → `POST /api/docs/ingest` → affiche le résultat inline (statut, chunks créés, erreur éventuelle)
- Désactivé si `LOCAL_DOCS_ENABLED=false`

**Répartition par type (breakdown) :**

Affichée sous forme de mini-tableau à 4 colonnes (un par format) avec le nombre de fichiers et de chunks. Pas de chart supplémentaire.

**Si `LOCAL_DOCS_ENABLED=false` :**

Message informatif : `"Ingestion de documents désactivée — définir LOCAL_DOCS_ENABLED=true pour activer la surveillance du dossier."` Le reste du bloc "Documents Locaux" est masqué.

---

## 11. Ordre d'implémentation

1. Migration `migrations/014_local_docs.py` — table `docs_ingestion_log` + index
2. `local_doc_ingestor.py` — `LocalDocIngestor._compute_hash()`, `_detect_format()`, `_is_already_indexed()`, `_extract_metadata()`
3. `local_doc_ingestor.py` — `_extract_text()` pour PDF (`pypdf`) uniquement + `_chunk()` + `ingest_file()` (PDF uniquement)
4. `local_doc_ingestor.py` — `_extract_text()` pour MD et TXT
5. `local_doc_ingestor.py` — `_extract_text()` pour DOCX (`python-docx`)
6. `local_doc_ingestor.py` — `LocalDocWatcher` (Watchdog) + anti-rebond
7. `local_doc_ingestor.py` — `ingest_directory()`, `delete_document()`, `list_documents()`, `get_status()`
8. `local_docs_api.py` — endpoints REST + montage dans `app.py`
9. `scheduler_registry.py` — job système `local_docs_batch_scan` toutes les heures
10. `app.py` — startup : init singleton `LocalDocIngestor`, démarrage `LocalDocWatcher`, enregistrement job
11. Tests `tests/test_local_doc_ingestor.py` — unitaires puis intégration
12. `admin_ui.py` — bloc "Documents Locaux" dans l'onglet "Vector DB"
