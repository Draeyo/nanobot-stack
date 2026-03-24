# Spec : Chiffrement At-Rest — Sous-projet L

**Date :** 2026-03-24
**Statut :** Spécifié
**Projet :** nanobot-stack
**Scope :** Protection des données persistantes contre l'accès physique non autorisé — chiffrement au niveau système de fichiers (recommandé) et chiffrement applicatif optionnel au niveau champ pour les données les plus sensibles (SQLite et Qdrant)

---

## 1. Contexte & Objectifs

### Problème

nanobot-stack est une instance self-hosted mono-utilisateur qui accumule des données personnelles sensibles : mémoire personnelle sémantique (`memory_personal`), emails ingérés (`email_inbox`), événements de calendrier (`calendar_events`), bases SQLite contenant des entités de connaissance, des corrections de feedback et des logs de synchronisation. Ces données sont aujourd'hui protégées uniquement par les permissions UNIX du système d'exploitation (`/opt/nanobot-stack/` appartenant à l'utilisateur `nanobot`). Un accès physique au disque — vol du serveur, accès à une image disque, snapshot non autorisé — expose l'intégralité des données en clair.

### Threat model précis

Le sous-projet L protège contre une menace **statique** : l'extraction d'une image disque ou d'un fichier de base de données sans accès au système d'exploitation en cours d'exécution. Il ne protège **pas** contre un processus compromis en cours d'exécution : le bridge doit déchiffrer les données pour les utiliser, une compromission du processus donne accès aux données en mémoire. Ce trade-off est assumé et documenté.

Menaces couvertes :
- Vol physique du serveur ou du disque
- Accès à une image disque (snapshot, backup non chiffré exposé)
- Lecture directe des fichiers `.db` ou des fichiers de données Qdrant par un attaquant avec accès au système de fichiers mais pas au processus actif

Menaces **non** couvertes :
- Compromission du processus bridge en cours d'exécution
- Injection SQL ou RCE sur l'API
- Accès via les credentials valides de l'utilisateur système

### Approche retenue — deux couches complémentaires

**Couche 1 — Chiffrement système de fichiers (recommandé, coût quasi nul) :** chiffrement de la partition ou du répertoire `/opt/nanobot-stack/` au niveau OS via eCryptfs ou LUKS/dm-crypt. Transparent pour l'application, aucune modification de code. Monté au démarrage avec une passphrase. C'est **l'approche recommandée** pour la majorité des déploiements.

**Couche 2 — Chiffrement applicatif au niveau champ (opt-in, coût modéré) :** chiffrement AES-256-GCM de champs spécifiques dans SQLite et Qdrant. Activé via `ENCRYPTION_ENABLED=true`. Utile si la couche 1 n'est pas disponible ou pour une défense en profondeur sur les champs les plus sensibles.

### Objectifs

- Documenter et outiller la mise en place de la couche 1 (eCryptfs / LUKS)
- Implémenter la couche 2 avec une classe `FieldEncryptor` réutilisable
- Protéger les champs sensibles identifiés dans SQLite et Qdrant
- Garantir la compatibilité ascendante : les données existantes en clair restent lisibles
- Fournir des endpoints de gestion du cycle de vie (enable, disable, rotate, status, migration)
- Validation de clé au démarrage pour détecter immédiatement une mauvaise configuration
- Opt-in explicite — `ENCRYPTION_ENABLED=false` par défaut, aucun impact sur les instances non configurées

---

## 2. Architecture & Périmètre du chiffrement

### Vue d'ensemble

```
Couche 1 (OS)                         Couche 2 (Application)
─────────────────────────────────────  ──────────────────────────────────────────
eCryptfs / LUKS                        FieldEncryptor (AES-256-GCM)
  └── /opt/nanobot-stack/ chiffré        ├── SQLite : champs TEXT sensibles
        ├── data/*.db (SQLite)           │     ├── knowledge_entities.value
        └── qdrant_data/                 │     ├── feedback.correction_text
              └── collections/           │     └── email_sync_log.account
                                         └── Qdrant : payloads sensibles
                                               ├── memory_personal → content
                                               ├── email_inbox → subject, snippet, sender
                                               └── calendar_events → description
```

### Collections Qdrant : périmètre de chiffrement

| Collection | Sensibilité | Champs chiffrés | Chiffrement applicatif |
|------------|-------------|-----------------|------------------------|
| `memory_personal` | Très élevée | `content` | Oui |
| `email_inbox` | Très élevée | `subject`, `snippet`, `sender` | Oui |
| `calendar_events` | Élevée | `description` | Oui |
| `docs_reference` | Faible | — | Non |
| `ops_runbooks` | Faible | — | Non |
| `memory_projects` | Modérée | — | Non |
| `semantic_cache` | Faible | — | Non |
| `procedural_workflows` | Faible | — | Non |

### Bases SQLite : périmètre de chiffrement

| Base | Table | Champ | Justification |
|------|-------|-------|---------------|
| `rag.db` | `knowledge_entities` | `value` | Contenu des entités de mémoire personnelle |
| `rag.db` | `feedback` | `correction_text` | Corrections utilisateur — révèle les erreurs du système et les sujets traités |
| `rss.db` | `email_sync_log` | `account` | Dissimule l'adresse IMAP — révèle l'identité numérique de l'utilisateur |

Les autres bases (`trust.db`, `procedural_memory.db`, `token_budgets.db`, `scheduler.db`) ne contiennent pas de champs à haute valeur d'information personnelle identifiable — elles sont protégées par la couche 1 uniquement.

### Nouveaux fichiers créés / modifiés

| Fichier | Action | Description |
|---------|--------|-------------|
| `src/bridge/encryption.py` | Créer | `FieldEncryptor`, dérivation de clé HKDF, helpers chiffrement/déchiffrement |
| `src/bridge/encryption_api.py` | Créer | API REST `/api/encryption/*` — 5 endpoints |
| `src/bridge/app.py` | Modifier | Validation clé au startup, mount `encryption_router` |
| `src/bridge/memory_manager.py` | Modifier | `_encrypt_payload()` / `_decrypt_payload()` sur `memory_personal` |
| `src/bridge/email_calendar.py` | Modifier | `_encrypt_payload()` / `_decrypt_payload()` sur `email_inbox`, `calendar_events` |
| `src/bridge/knowledge_entity_store.py` | Modifier | Chiffrement/déchiffrement `knowledge_entities.value` |
| `src/bridge/feedback_learner.py` | Modifier | Chiffrement/déchiffrement `feedback.correction_text` |
| `src/bridge/email_calendar.py` | Modifier | Chiffrement/déchiffrement `email_sync_log.account` |
| `docs/ops/encryption-setup.md` | Créer | Guide opérationnel eCryptfs et LUKS |
| `tests/test_encryption.py` | Créer | Tests unitaires du module encryption |

---

## 3. Chiffrement SQLite (chiffrement applicatif au niveau champ)

### 3.1 Choix de l'approche : column-level encryption vs SQLCipher

SQLCipher (chiffrement de l'intégralité du fichier SQLite) a été évalué et **écarté** pour ce déploiement. Les raisons :
- Installation non triviale : nécessite de compiler Python `pysqlite3` ou `sqlcipher3` contre une bibliothèque SQLCipher native — incompatible avec un environnement Docker standard et avec les environnements CI
- Remplacement de la bibliothèque `sqlite3` stdlib — risque de régression sur les migrations existantes
- Coût d'intégration disproportionné pour un threat model ciblant uniquement les champs les plus sensibles

**Approche retenue :** chiffrement au niveau colonne sur un sous-ensemble de champs TEXT. Implémentation dans `src/bridge/encryption.py` via la classe `FieldEncryptor`.

### 3.2 Classe `FieldEncryptor`

```python
# src/bridge/encryption.py

class FieldEncryptor:
    """Chiffrement AES-256-GCM de valeurs TEXT individuelles pour stockage SQLite et Qdrant."""

    PREFIX = "enc:v1:"

    def __init__(self, master_key_hex: str, context: str):
        # Dérivation de clé via HKDF-SHA256
        # context : "sqlite-v1" | "qdrant-v1"
        ...

    def encrypt_field(self, value: str) -> str:
        """
        Chiffre value en AES-256-GCM.
        Retourne "enc:v1:<base64url(nonce + ciphertext + tag)>"
        Nonce : 12 bytes aléatoires (96 bits, recommandé NIST pour GCM)
        """
        ...

    def decrypt_field(self, value: str) -> str:
        """
        Déchiffre si value commence par "enc:v1:".
        Retourne value tel quel si c'est du texte en clair (compatibilité ascendante).
        Lève DecryptionError si le préfixe est présent mais le déchiffrement échoue.
        """
        ...

    def is_encrypted(self, value: str) -> bool:
        """Retourne True si value commence par le préfixe "enc:v1:"."""
        return value.startswith(self.PREFIX)
```

**Format du champ chiffré :**

```
enc:v1:<base64url_standard(nonce[12] + ciphertext + tag[16])>
```

- Le préfixe `enc:v1:` permet la détection immédiate d'un champ chiffré et rend la migration idempotente
- `v1` est la version du schéma de chiffrement — facilite la rotation de schéma future sans casser la compatibilité
- Base64 URL-safe sans padding pour rester dans le domaine des caractères TEXT SQLite et JSON Qdrant
- Nonce de 12 bytes généré aléatoirement à chaque appel `encrypt_field` (un nonce différent par valeur, par écriture)

### 3.3 Dérivation de clé HKDF

```python
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

def _derive_key(master_key_bytes: bytes, context_label: str) -> bytes:
    """
    Dérive une clé 32 bytes (256 bits) depuis la clé maître via HKDF-SHA256.
    context_label : "sqlite-v1" ou "qdrant-v1"
    Chaque contexte produit une clé distincte — isolation des domaines de chiffrement.
    """
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=context_label.encode("utf-8"),
    )
    return hkdf.derive(master_key_bytes)
```

La clé maître est transmise en variable d'environnement `ENCRYPTION_MASTER_KEY` sous forme de 64 caractères hexadécimaux (256 bits). Elle est lue une seule fois au démarrage et convertie en `bytes` via `bytes.fromhex()`. Elle n'est jamais stockée en base, jamais loggée, jamais sérialisée.

### 3.4 Intégration dans les stores SQLite

Chaque store accède au `FieldEncryptor` via une instance partagée injectée depuis `app.py` au démarrage :

```python
# Écriture — dans knowledge_entity_store.py
def upsert_entity(self, entity_id: str, value: str, ...):
    stored_value = self.encryptor.encrypt_field(value) if ENCRYPTION_SQLITE_ENABLED else value
    # INSERT OR REPLACE INTO knowledge_entities ...

# Lecture — dans knowledge_entity_store.py
def get_entity(self, entity_id: str) -> dict:
    row = self.db.execute("SELECT value FROM knowledge_entities WHERE id = ?", (entity_id,)).fetchone()
    value = self.encryptor.decrypt_field(row["value"])  # transparent si plaintext
    ...
```

Le déchiffrement est **toujours tenté** à la lecture, même si `ENCRYPTION_SQLITE_ENABLED=false` : `decrypt_field()` retourne le texte en clair tel quel si la valeur ne commence pas par `enc:v1:`. Cela garantit qu'une base chiffrée reste lisible si `ENCRYPTION_ENABLED` est désactivé après coup (le processus détectera les valeurs chiffrées et les déchiffrera si la clé est présente, ou lèvera une `DecryptionError` si la clé est absente).

---

## 4. Chiffrement Qdrant (chiffrement applicatif au niveau payload)

### 4.1 Principe général

Les vecteurs denses sont calculés depuis le texte **en clair** avant chiffrement, puis le texte est chiffré pour stockage dans le payload. La recherche vectorielle (similarité cosinus) opère sur les vecteurs non chiffrés — le chiffrement des payloads est donc **transparent pour la recherche sémantique**. Seule la récupération et l'affichage des payloads nécessitent un déchiffrement.

```
Ingestion :
  plaintext → embed() → vecteur dense (stocké en clair dans Qdrant)
  plaintext → encrypt_field() → payload chiffré (stocké dans le payload Qdrant)

Retrieval :
  query → embed() → recherche vectorielle → points trouvés (vecteurs non chiffrés)
  points[i].payload["content"] → decrypt_field() → plaintext restitué
```

### 4.2 Méthodes sur les ingestors existants

Chaque ingestor concerné reçoit deux méthodes privées supplémentaires :

```python
def _encrypt_payload(self, payload: dict) -> dict:
    """
    Retourne une copie du payload avec les champs sensibles chiffrés.
    Appelé juste avant chaque upsert Qdrant.
    Idempotent : si le champ commence déjà par "enc:v1:", il n'est pas re-chiffré.
    """
    encrypted = payload.copy()
    for field in self.ENCRYPTED_FIELDS:
        if field in encrypted and isinstance(encrypted[field], str):
            if not self.encryptor.is_encrypted(encrypted[field]):
                encrypted[field] = self.encryptor.encrypt_field(encrypted[field])
    return encrypted

def _decrypt_payload(self, payload: dict) -> dict:
    """
    Retourne une copie du payload avec les champs sensibles déchiffrés.
    Appelé juste après chaque retrieval Qdrant.
    Transparent : les champs en clair sont retournés tels quels.
    """
    decrypted = payload.copy()
    for field in self.ENCRYPTED_FIELDS:
        if field in decrypted and isinstance(decrypted[field], str):
            decrypted[field] = self.encryptor.decrypt_field(decrypted[field])
    return decrypted
```

### 4.3 Champs chiffrés par collection

| Ingestor | Collection Qdrant | `ENCRYPTED_FIELDS` |
|----------|-------------------|--------------------|
| `MemoryManager` | `memory_personal` | `["content"]` |
| `EmailCalendarFetcher` | `email_inbox` | `["subject", "snippet", "sender"]` |
| `EmailCalendarFetcher` | `calendar_events` | `["description"]` |

### 4.4 Cohérence avec la déduplication

Les mécanismes de déduplication existants (par `message_id`, `event_uid`, etc.) opèrent sur des champs d'identifiant qui ne sont **pas** chiffrés. La déduplication reste donc fonctionnelle avec ou sans chiffrement activé.

---

## 5. Gestion des clés

### 5.1 Clé maître

- Variable d'environnement : `ENCRYPTION_MASTER_KEY`
- Format : chaîne hexadécimale de 64 caractères (= 256 bits = 32 bytes)
- Génération recommandée : `python3 -c "import secrets; print(secrets.token_hex(32))"`
- À stocker dans `stack.env` (protégé par les permissions UNIX et, idéalement, par la couche 1 eCryptfs/LUKS)
- **Jamais** inclus dans les logs, jamais persisté en base de données, jamais retourné par un endpoint API

### 5.2 Dérivation de clés de domaine

À partir de `ENCRYPTION_MASTER_KEY`, deux clés dérivées sont calculées via HKDF-SHA256 :

| Label HKDF | Usage | Variable de contrôle |
|------------|-------|----------------------|
| `sqlite-v1` | Chiffrement des champs SQLite | `ENCRYPTION_SQLITE_ENABLED` |
| `qdrant-v1` | Chiffrement des payloads Qdrant | `ENCRYPTION_QDRANT_ENABLED` |

Les deux clés dérivées sont calculées une seule fois au démarrage du bridge et conservées en mémoire. Un attaquant qui compromet une clé dérivée ne compromet pas l'autre domaine.

### 5.3 Validation de clé au démarrage

Au démarrage de `app.py`, si `ENCRYPTION_ENABLED=true`, le bridge effectue une validation de la clé :

```python
def validate_encryption_key(encryptor: FieldEncryptor) -> None:
    """
    Test de cohérence : chiffre une valeur connue et vérifie que le déchiffrement produit l'original.
    Lève une RuntimeError et bloque le démarrage si la clé est invalide ou malformée.
    """
    sentinel = "nanobot-encryption-sentinel-v1"
    encrypted = encryptor.encrypt_field(sentinel)
    decrypted = encryptor.decrypt_field(encrypted)
    if decrypted != sentinel:
        raise RuntimeError(
            "ENCRYPTION_MASTER_KEY invalide : le test de cohérence encrypt/decrypt a échoué. "
            "Vérifier la variable d'environnement."
        )
```

Si la clé est absente alors que `ENCRYPTION_ENABLED=true`, le bridge lève une `RuntimeError` au startup avec un message explicite et refuse de démarrer. Ce comportement "fail-fast" évite de démarrer silencieusement avec une configuration incohérente.

### 5.4 Rotation de clé

La rotation est déclenchée via `POST /api/encryption/rotate` avec la nouvelle clé dans le corps de la requête. Le processus est entièrement applicatif (re-chiffrement champ par champ) et se déroule en plusieurs étapes séquentielles :

```
POST /api/encryption/rotate { "new_master_key": "<hex64>" }
  1. Valider le format de new_master_key (64 hex chars)
  2. Initialiser new_encryptor avec new_master_key
  3. Valider new_encryptor (test encrypt/decrypt sentinel)
  4. Créer un job de migration asynchrone :
       Pour chaque champ chiffré dans SQLite :
         a. Lire la valeur chiffrée
         b. Déchiffrer avec l'ancienne clé
         c. Re-chiffrer avec la nouvelle clé
         d. Mettre à jour en base
       Pour chaque point dans les collections Qdrant concernées :
         a. Scroll paginated (par lots de 100)
         b. Déchiffrer les champs sensibles avec l'ancienne clé
         c. Re-chiffrer avec la nouvelle clé
         d. Upsert le payload mis à jour
  5. À la fin de la migration : remplacer l'encryptor actif par new_encryptor
  6. Logger l'événement de rotation (date, durée) dans encryption_log — sans jamais loguer les clés
```

La rotation est une **opération offline** au sens où elle doit être déclenchée manuellement, elle peut prendre plusieurs minutes sur de grands volumes, et elle est non-réversible automatiquement (l'ancienne clé n'est plus mémorisée après la rotation). L'administrateur doit s'assurer de mettre à jour `ENCRYPTION_MASTER_KEY` dans `stack.env` immédiatement après une rotation réussie.

---

## 6. Variables d'environnement

| Variable | Défaut | Description |
|----------|--------|-------------|
| `ENCRYPTION_ENABLED` | `false` | Activation globale du chiffrement applicatif. Si `false`, aucune tentative de chiffrement à l'écriture. Le déchiffrement à la lecture reste fonctionnel (transparence lors de la désactivation). |
| `ENCRYPTION_MASTER_KEY` | — | **Obligatoire si `ENCRYPTION_ENABLED=true`.** Chaîne hexadécimale de 64 caractères (256 bits). Refus de démarrage si absent et chiffrement activé. |
| `ENCRYPTION_SQLITE_ENABLED` | Valeur de `ENCRYPTION_ENABLED` | Contrôle indépendant du chiffrement SQLite. Permet d'activer le chiffrement Qdrant sans toucher aux bases SQLite, ou inversement. |
| `ENCRYPTION_QDRANT_ENABLED` | Valeur de `ENCRYPTION_ENABLED` | Contrôle indépendant du chiffrement des payloads Qdrant. |

**Règle de priorité :** si `ENCRYPTION_ENABLED=false` et `ENCRYPTION_SQLITE_ENABLED=true`, le chiffrement SQLite est actif (`ENCRYPTION_SQLITE_ENABLED` a la priorité sur `ENCRYPTION_ENABLED` pour son domaine). Si `ENCRYPTION_ENABLED=true` et `ENCRYPTION_SQLITE_ENABLED=false`, le chiffrement SQLite est désactivé même si le chiffrement global est actif.

**Exemple de configuration minimale dans `stack.env` :**

```dotenv
ENCRYPTION_ENABLED=true
ENCRYPTION_MASTER_KEY=a3f8c2e1d9b74506f1e8a2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3
```

---

## 7. Migration & Compatibilité ascendante

### 7.1 Principe de non-régression

`ENCRYPTION_ENABLED=false` est la valeur par défaut. Aucune modification de comportement n'est apportée aux instances non configurées. Les bases SQLite et collections Qdrant existantes, en clair, continuent de fonctionner sans aucune action.

### 7.2 Activation du chiffrement sur des données existantes

Lorsque `ENCRYPTION_ENABLED` est activé pour la première fois sur une instance existante, les données historiques en clair restent lisibles (`decrypt_field()` les retourne telles quelles). Le chiffrement ne s'applique qu'aux **nouvelles écritures**.

Pour chiffrer rétroactivement les données existantes en clair, déclencher la migration via :

```
POST /api/encryption/enable
```

Ce endpoint déclenche un job de migration asynchrone qui :

```
enable_migration_job()
  Pour chaque base SQLite concernée :
    SELECT id, <champ> FROM <table>
    Pour chaque ligne :
      Si NOT is_encrypted(valeur) → encrypt_field() → UPDATE
  Pour chaque collection Qdrant concernée :
    Scroll paginé (lots de 100 points)
    Pour chaque point :
      Si NOT is_encrypted(payload[field]) → encrypt_field() → upsert payload
  Mettre à jour encryption_log : migration_completed_at = now
```

La migration est **idempotente** : le contrôle `is_encrypted()` (présence du préfixe `enc:v1:`) évite de re-chiffrer un champ déjà chiffré. La migration peut être relancée sans risque en cas d'interruption.

### 7.3 Désactivation et déchiffrement des données

Pour revenir à un stockage en clair :

```
POST /api/encryption/disable
```

Ce endpoint déclenche le processus inverse : pour chaque champ chiffré (`is_encrypted()` retourne True), déchiffre et réécrit la valeur en clair. À la fin de la migration inverse, `ENCRYPTION_ENABLED` est mis à `false` en mémoire (persistance dans `encryption_log`). L'administrateur doit mettre à jour manuellement `ENCRYPTION_ENABLED=false` dans `stack.env`.

### 7.4 Compatibilité lors d'une désactivation partielle

Si l'instance redémarre avec `ENCRYPTION_ENABLED=false` alors que des données chiffrées existent en base (migration non effectuée) :
- Les lectures déclenchent `decrypt_field()` qui retourne les valeurs en clair si le préfixe est présent **et si la clé est disponible**
- Si `ENCRYPTION_MASTER_KEY` est absent et des valeurs chiffrées existent, les lectures lèveront une `DecryptionError` — comportement correct (fail visible plutôt que corruption silencieuse)
- L'endpoint `GET /api/encryption/status` signale cette situation via `partially_encrypted: true`

---

## 8. Sécurité & Threat Model

### 8.1 Propriétés de sécurité garanties

| Propriété | Mécanisme |
|-----------|-----------|
| Confidentialité au repos | AES-256-GCM (authentifié) — état de l'art NIST |
| Intégrité des données chiffrées | Tag d'authentification GCM 128 bits — toute corruption est détectée |
| Isolation des domaines | HKDF avec labels différents par domaine — compromission d'une clé dérivée n'affecte pas les autres |
| Unicité des nonces | 12 bytes aléatoires (CSPRNG) par appel `encrypt_field()` — probabilité de collision négligeable (2^-96) |
| Pas de fuite de clé par les logs | Clé maître et clés dérivées jamais transmises à un logger |
| Fail-fast sur mauvaise clé | Validation au démarrage — pas de démarrage silencieux avec une clé invalide |

### 8.2 Considérations opérationnelles

**Backup de la clé maître :** `ENCRYPTION_MASTER_KEY` doit être sauvegardé dans un emplacement distinct des données chiffrées (coffre-fort de mots de passe, gestionnaire de secrets). La perte de la clé maître rend les données irrécupérables. Si le sub-projet F (backup automatique) est actif et que `BACKUP_ENCRYPTION_KEY` est utilisé, les backups eux-mêmes contiennent les données potentiellement chiffrées par la couche applicative — la clé du backup et la clé `ENCRYPTION_MASTER_KEY` doivent être sauvegardées séparément.

**Absence de chiffrement des vecteurs Qdrant :** les vecteurs denses eux-mêmes ne sont pas chiffrés. Un attaquant avec accès aux vecteurs bruts ne peut pas reconstruire le texte original directement, mais des attaques d'inversion d'embeddings sont théoriquement possibles sur des textes courts. Ce risque est acceptable dans le threat model retenu (accès statique à l'image disque sans capacité de calcul GPU).

**Chiffrement des snapshots Qdrant :** les snapshots Qdrant produits par le sub-projet F contiennent les payloads. Si `ENCRYPTION_QDRANT_ENABLED=true`, les payloads dans les snapshots sont chiffrés — les snapshots sont donc protégés au niveau applicatif même sans chiffrement du backup lui-même. Cela ne dispense pas de chiffrer les archives de backup.

**Rotation de clé périodique :** aucune rotation automatique n'est planifiée (overhead opérationnel non justifié pour un usage mono-utilisateur). La rotation manuelle via `POST /api/encryption/rotate` est disponible et documentée.

### 8.3 Chiffrement système de fichiers (Couche 1) — Mise en place

La couche 1 est indépendante de l'application et doit être mise en place **avant** la couche 2 idéalement. Elle protège la totalité des données y compris les fichiers `.env`, les logs et les fichiers temporaires que la couche applicative ne couvre pas.

**Option A — eCryptfs (recommandé pour Ubuntu/Debian, chiffrement au niveau répertoire) :**

```bash
# 1. Installer eCryptfs
sudo apt-get install ecryptfs-utils

# 2. Créer le répertoire chiffré (lower) et le point de montage (upper)
sudo mkdir -p /opt/nanobot-stack-encrypted
sudo mkdir -p /opt/nanobot-stack

# 3. Monter le répertoire chiffré
sudo mount -t ecryptfs /opt/nanobot-stack-encrypted /opt/nanobot-stack \
  -o ecryptfs_cipher=aes,ecryptfs_key_bytes=32,ecryptfs_passthrough=no,ecryptfs_enable_filename_crypto=yes

# Lors de la première exécution : saisir une passphrase, confirmer les paramètres de chiffrement

# 4. Copier les données existantes si nécessaire
# (s'assurer que /opt/nanobot-stack est monté avant de démarrer le bridge)

# 5. Ajouter au /etc/fstab pour montage automatique au boot :
# /opt/nanobot-stack-encrypted /opt/nanobot-stack ecryptfs \
#   ecryptfs_cipher=aes,ecryptfs_key_bytes=32,ecryptfs_passthrough=no,ecryptfs_enable_filename_crypto=yes 0 0

# 6. Créer un service systemd de déverrouillage au boot (optionnel — stockage sécurisé de la passphrase) :
# Voir /etc/ecryptfs/auto-mount pour les options non interactives
```

**Option B — LUKS/dm-crypt (recommandé pour partition dédiée) :**

```bash
# 1. Identifier la partition ou le disque dédié (ex: /dev/sdb1)
sudo cryptsetup luksFormat /dev/sdb1
# Saisir et confirmer la passphrase LUKS

# 2. Ouvrir le volume chiffré
sudo cryptsetup open /dev/sdb1 nanobot-data

# 3. Formater et monter
sudo mkfs.ext4 /dev/mapper/nanobot-data
sudo mount /dev/mapper/nanobot-data /opt/nanobot-stack

# 4. Configurer /etc/crypttab pour déverrouillage au boot :
# nanobot-data /dev/sdb1 none luks

# 5. Configurer /etc/fstab :
# /dev/mapper/nanobot-data /opt/nanobot-stack ext4 defaults 0 2
```

**Comparaison des deux options :**

| Critère | eCryptfs | LUKS/dm-crypt |
|---------|----------|---------------|
| Granularité | Répertoire | Partition/disque entier |
| Installation | `apt install ecryptfs-utils` | Natif Linux (cryptsetup) |
| Transparence | Transparente pour l'application | Transparente pour l'application |
| Chiffrement des noms de fichiers | Optionnel | Oui (niveau bloc) |
| Performance | Légère surcharge CPU par fichier | Très faible surcharge (AES-NI) |
| Cas d'usage recommandé | Pas de partition dédiée disponible | Partition ou disque dédié disponible |

---

## 9. Dépendances Python

```
cryptography>=42.0
```

Cette dépendance est **déjà présente** dans `requirements.txt` — elle a été introduite par le sub-projet F (Backup & Restore) pour le chiffrement AES des archives. Le sub-projet L ne requiert **aucune nouvelle dépendance externe**.

La bibliothèque `cryptography` fournit :
- `cryptography.hazmat.primitives.ciphers.aead.AESGCM` — chiffrement AES-256-GCM
- `cryptography.hazmat.primitives.kdf.hkdf.HKDF` — dérivation de clé
- `cryptography.hazmat.primitives.hashes.SHA256` — algorithme de hachage pour HKDF

---

## 10. API REST

Préfixe : `/api/encryption`

Fichier : `src/bridge/encryption_api.py`, monté dans `app.py`.

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| GET | `/status` | Retourne l'état courant du chiffrement |
| POST | `/enable` | Déclenche la migration de chiffrement (données existantes en clair → chiffrées) |
| POST | `/disable` | Déclenche la migration de déchiffrement (données chiffrées → en clair) |
| POST | `/rotate` | Re-chiffre toutes les données avec une nouvelle clé maître |
| GET | `/migration-status` | Retourne la progression du job de migration en cours |

### `GET /api/encryption/status`

```json
{
  "encryption_enabled": true,
  "sqlite_enabled": true,
  "qdrant_enabled": true,
  "partially_encrypted": false,
  "sqlite_fields_encrypted": {
    "knowledge_entities.value": 142,
    "feedback.correction_text": 37,
    "email_sync_log.account": 4
  },
  "qdrant_fields_encrypted": {
    "memory_personal.content": 89,
    "email_inbox.subject": 213,
    "email_inbox.snippet": 213,
    "email_inbox.sender": 213,
    "calendar_events.description": 31
  },
  "last_rotation_at": "2026-03-20T14:22:00Z",
  "migration_in_progress": false
}
```

`partially_encrypted` est `true` si des champs sensibles contiennent un mélange de valeurs chiffrées et en clair (typiquement : `ENCRYPTION_ENABLED` vient d'être activé mais `POST /enable` n'a pas encore été appelé).

### `POST /api/encryption/enable`

Déclenche le job de migration asynchrone. Retourne immédiatement avec un `job_id`. Réponse :

```json
{
  "job_id": "enc-mig-20260324-142200",
  "status": "started",
  "message": "Migration de chiffrement démarrée. Suivre la progression via GET /api/encryption/migration-status"
}
```

Erreurs :
- `409 Conflict` si une migration est déjà en cours
- `400 Bad Request` si `ENCRYPTION_ENABLED=false` et `ENCRYPTION_MASTER_KEY` absent

### `POST /api/encryption/disable`

Même comportement que `/enable` en sens inverse. Requiert que `ENCRYPTION_MASTER_KEY` soit encore présent dans l'environnement (nécessaire pour déchiffrer). Retourne le même format de réponse que `/enable`.

### `POST /api/encryption/rotate`

Corps de la requête :

```json
{
  "new_master_key": "b4e9d3f2a1c80617e2f9b3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4"
}
```

Réponse :

```json
{
  "job_id": "enc-rot-20260324-150000",
  "status": "started",
  "message": "Rotation de clé démarrée. Mettre à jour ENCRYPTION_MASTER_KEY dans stack.env à la fin de la migration."
}
```

Erreurs :
- `400 Bad Request` si `new_master_key` n'est pas une chaîne hexadécimale de 64 caractères
- `400 Bad Request` si `new_master_key` est identique à la clé courante
- `409 Conflict` si une migration est déjà en cours

### `GET /api/encryption/migration-status`

Retourne la progression du job de migration en cours (ou du dernier job terminé) :

```json
{
  "job_id": "enc-mig-20260324-142200",
  "type": "enable",
  "status": "in_progress",
  "started_at": "2026-03-24T14:22:00Z",
  "completed_at": null,
  "progress": {
    "sqlite": {
      "knowledge_entities": {"processed": 98, "total": 142, "percent": 69},
      "feedback": {"processed": 37, "total": 37, "percent": 100},
      "email_sync_log": {"processed": 2, "total": 4, "percent": 50}
    },
    "qdrant": {
      "memory_personal": {"processed": 45, "total": 89, "percent": 51},
      "email_inbox": {"processed": 0, "total": 213, "percent": 0},
      "calendar_events": {"processed": 0, "total": 31, "percent": 0}
    }
  },
  "error": null
}
```

---

## 11. Tests

Fichier : `tests/test_encryption.py`

| Test | Description |
|------|-------------|
| `test_encrypt_decrypt_roundtrip_sqlite` | `encrypt_field(value)` suivi de `decrypt_field()` retourne la valeur originale — contexte `sqlite-v1` |
| `test_encrypt_decrypt_roundtrip_qdrant` | Même test pour le contexte `qdrant-v1` |
| `test_decrypt_plaintext_passthrough` | `decrypt_field("valeur en clair")` retourne `"valeur en clair"` sans erreur — compatibilité ascendante |
| `test_encrypted_prefix_detection` | `is_encrypted("enc:v1:abc")` → `True` ; `is_encrypted("texte")` → `False` |
| `test_nonce_uniqueness` | Deux appels consécutifs `encrypt_field(même_valeur)` produisent des ciphertexts différents (nonces distincts) |
| `test_tampered_ciphertext_raises` | Modifier un byte du ciphertext → `decrypt_field()` lève `DecryptionError` (intégrité GCM) |
| `test_hkdf_domain_isolation` | La clé dérivée pour `sqlite-v1` est différente de celle pour `qdrant-v1` avec la même clé maître |
| `test_qdrant_vector_search_unaffected` | Mock Qdrant : après chiffrement du payload, la recherche vectorielle retourne le même point (vecteur inchangé), le payload déchiffré correspond au texte original |
| `test_qdrant_encrypt_payload_idempotent` | `_encrypt_payload()` sur un payload déjà chiffré ne re-chiffre pas (préfixe `enc:v1:` détecté) |
| `test_sqlite_backward_compat_mixed_db` | Base avec mélange de valeurs chiffrées et en clair — toutes les lectures retournent le plaintext correct |
| `test_key_rotation` | Chiffrer une valeur avec key_A, effectuer la rotation vers key_B, vérifier que la valeur est déchiffrable avec key_B et non avec key_A |
| `test_startup_key_validation_success` | `validate_encryption_key()` réussit avec une clé valide |
| `test_startup_key_validation_fail_bad_key` | `validate_encryption_key()` lève `RuntimeError` si la clé est de mauvais format |
| `test_startup_key_validation_fail_missing` | Démarrage avec `ENCRYPTION_ENABLED=true` et `ENCRYPTION_MASTER_KEY` absent → `RuntimeError` |
| `test_enable_migration_idempotent` | Lancer deux fois `enable_migration_job()` sur la même base — le deuxième run ne modifie aucune ligne |
| `test_disable_migration_roundtrip` | `enable_migration_job()` puis `disable_migration_job()` → toutes les valeurs reviennent en plaintext |
| `test_api_status_partially_encrypted` | `GET /api/encryption/status` retourne `partially_encrypted: true` si des valeurs en clair existent alors que `ENCRYPTION_ENABLED=true` |
| `test_api_rotate_invalid_key_format` | `POST /api/encryption/rotate` avec clé de 32 chars → `400 Bad Request` |
| `test_api_rotate_same_key` | `POST /api/encryption/rotate` avec la même clé courante → `400 Bad Request` |
| `test_api_conflict_double_migration` | Lancer `/enable` deux fois rapidement → le second retourne `409 Conflict` |

---

## 12. Ordre d'implémentation

1. **`src/bridge/encryption.py`** — Classe `FieldEncryptor` (HKDF, AES-256-GCM, prefix detection, encrypt/decrypt, validate)
2. **`app.py` — validation au startup** — Lecture de `ENCRYPTION_MASTER_KEY`, instanciation des deux `FieldEncryptor` (`sqlite-v1`, `qdrant-v1`), appel `validate_encryption_key()`, refus de démarrage si invalide
3. **Chiffrement SQLite** — Intégration dans `knowledge_entity_store.py`, `feedback_learner.py`, `email_calendar.py` (table `email_sync_log`)
4. **Chiffrement Qdrant** — Méthodes `_encrypt_payload()` / `_decrypt_payload()` dans `memory_manager.py` et `email_calendar.py`
5. **`src/bridge/encryption_api.py`** — 5 endpoints REST + jobs de migration asynchrones + suivi de progression
6. **`app.py` — mount router** — Montage du `encryption_router` dans l'application FastAPI
7. **Tests (`tests/test_encryption.py`)** — Couverture complète des 18 cas de test définis
8. **Documentation opérationnelle (`docs/ops/encryption-setup.md`)** — Guide eCryptfs et LUKS, procédure de rotation, checklist de déploiement
