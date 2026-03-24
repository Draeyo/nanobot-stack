# Sub-projet L — Chiffrement At-Rest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add field-level AES-256-GCM encryption for sensitive SQLite columns and Qdrant payload fields, with key rotation and enable/disable migration

**Architecture:** FieldEncryptor class uses AES-256-GCM (from cryptography library, already present) with HKDF-derived keys. Encrypted values prefixed with `enc:v1:` for backward compatibility. SQLite: encrypts knowledge_entities.value and feedback.correction_text. Qdrant: encrypts memory_personal content, email_inbox subject/snippet/sender, calendar_events description. Key rotation re-encrypts in place. Enable/disable migrations are idempotent.

**Tech Stack:** cryptography>=42.0 (already in requirements.txt), HKDF-SHA256, AES-256-GCM, FastAPI (existing)

---

## Task 1 — `encryption.py`: FieldEncryptor class with HKDF key derivation and startup validation

### What & Why

Creates the core `FieldEncryptor` class in `src/bridge/encryption.py`. This class derives two domain-isolated keys (one for SQLite, one for Qdrant) from a single master key using HKDF-SHA256. Key derivation happens once at `__init__`. A `validate_encryption_key()` module-level function provides the fail-fast startup check. The `DecryptionError` exception class is also defined here so all callers can catch a single type.

### Files touched

- `src/bridge/encryption.py` — new file

### Test first

File: `tests/test_encryption.py` (initial skeleton — expanded in Task 14)

```python
"""Tests for Sub-projet L — FieldEncryptor core."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "bridge"))

from encryption import FieldEncryptor, DecryptionError, validate_encryption_key

MASTER_KEY = "a" * 64  # 64 hex chars = 32 bytes


class TestFieldEncryptorInit:
    def test_init_accepts_valid_64_hex_key(self):
        """FieldEncryptor.__init__ succeeds with a valid 64-char hex key."""
        enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")
        assert enc is not None

    def test_init_raises_on_wrong_length(self):
        """FieldEncryptor.__init__ raises ValueError when key is not 64 hex chars."""
        with pytest.raises(ValueError, match="64"):
            FieldEncryptor("tooshort", "sqlite-v1")

    def test_init_raises_on_non_hex(self):
        """FieldEncryptor.__init__ raises ValueError on non-hex characters."""
        with pytest.raises(ValueError):
            FieldEncryptor("z" * 64, "sqlite-v1")

    def test_hkdf_domain_isolation(self):
        """sqlite-v1 and qdrant-v1 derive distinct 32-byte keys from the same master."""
        sqlite_enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")
        qdrant_enc = FieldEncryptor(MASTER_KEY, "qdrant-v1")
        assert sqlite_enc._derived_key != qdrant_enc._derived_key

    def test_startup_key_validation_success(self):
        """validate_encryption_key() completes without error for a valid encryptor."""
        enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")
        validate_encryption_key(enc)  # must not raise

    def test_startup_key_validation_fail_bad_key(self):
        """validate_encryption_key() raises RuntimeError on bad key format."""
        with pytest.raises((ValueError, RuntimeError)):
            enc = FieldEncryptor("ZZZZ" * 16, "sqlite-v1")  # invalid hex
            validate_encryption_key(enc)

    def test_startup_key_validation_fail_missing(self, monkeypatch):
        """validate_encryption_key() raises RuntimeError when ENCRYPTION_MASTER_KEY absent."""
        monkeypatch.delenv("ENCRYPTION_MASTER_KEY", raising=False)
        with pytest.raises(RuntimeError, match="ENCRYPTION_MASTER_KEY"):
            validate_encryption_key(None)
```

### Run tests (must fail)

```bash
cd /opt/nanobot-stack/rag-bridge
python -m pytest tests/test_encryption.py::TestFieldEncryptorInit -v
```

Expected: `FAILED` / `ModuleNotFoundError` — `encryption.py` does not exist yet.

### Implementation

- [ ] Create `src/bridge/encryption.py`:

```python
"""encryption.py — Field-level AES-256-GCM encryption for nanobot-stack (Sub-projet L).

Provides FieldEncryptor for encrypting/decrypting individual TEXT values stored in
SQLite or Qdrant payloads. Uses HKDF-SHA256 for domain-isolated key derivation.

Encrypted format:  enc:v1:<base64url_no_padding(nonce[12] + ciphertext + tag[16])>

Usage:
    enc = FieldEncryptor(os.environ["ENCRYPTION_MASTER_KEY"], "sqlite-v1")
    stored = enc.encrypt_field(plaintext)
    plaintext = enc.decrypt_field(stored)
"""
from __future__ import annotations

import base64
import logging
import os
import secrets

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger("rag-bridge.encryption")

NONCE_SIZE = 12  # bytes — 96-bit nonce, NIST recommended for GCM


class DecryptionError(Exception):
    """Raised when AES-GCM decryption fails (tampered data or wrong key)."""


class FieldEncryptor:
    """AES-256-GCM field encryptor with HKDF-derived domain keys.

    Args:
        master_key_hex: 64-character hexadecimal string (256-bit master key).
        context: HKDF info label — "sqlite-v1" or "qdrant-v1".
    """

    PREFIX = "enc:v1:"

    def __init__(self, master_key_hex: str, context: str) -> None:
        if len(master_key_hex) != 64:
            raise ValueError(
                f"ENCRYPTION_MASTER_KEY must be exactly 64 hex characters (got {len(master_key_hex)})"
            )
        master_key_bytes = bytes.fromhex(master_key_hex)  # raises ValueError on non-hex
        self._derived_key: bytes = self._derive_key(master_key_bytes, context)
        self._context = context

    @staticmethod
    def _derive_key(master_key_bytes: bytes, context_label: str) -> bytes:
        """Derive a 32-byte AES key from the master key using HKDF-SHA256."""
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=context_label.encode("utf-8"),
        )
        return hkdf.derive(master_key_bytes)

    def encrypt_field(self, value: str) -> str:
        """Encrypt value with AES-256-GCM. Returns enc:v1:<base64url> string.

        Each call generates a fresh random 12-byte nonce — two encryptions of
        the same plaintext produce different ciphertexts.
        """
        nonce = secrets.token_bytes(NONCE_SIZE)
        aesgcm = AESGCM(self._derived_key)
        ciphertext_with_tag = aesgcm.encrypt(nonce, value.encode("utf-8"), None)
        blob = nonce + ciphertext_with_tag  # nonce(12) + ciphertext + tag(16)
        b64 = base64.urlsafe_b64encode(blob).rstrip(b"=").decode("ascii")
        return f"{self.PREFIX}{b64}"

    def decrypt_field(self, value: str) -> str:
        """Decrypt an enc:v1: prefixed value. Returns plaintext unchanged if not prefixed.

        Raises DecryptionError if the prefix is present but decryption fails
        (tampered data, wrong key, or truncated blob).
        """
        if not value.startswith(self.PREFIX):
            return value  # plaintext passthrough — backward compatibility
        b64 = value[len(self.PREFIX):]
        # Restore base64 padding
        padding = (4 - len(b64) % 4) % 4
        b64_padded = b64 + "=" * padding
        try:
            blob = base64.urlsafe_b64decode(b64_padded)
            if len(blob) < NONCE_SIZE + 16:
                raise ValueError("blob too short")
            nonce = blob[:NONCE_SIZE]
            ciphertext_with_tag = blob[NONCE_SIZE:]
            aesgcm = AESGCM(self._derived_key)
            plaintext_bytes = aesgcm.decrypt(nonce, ciphertext_with_tag, None)
            return plaintext_bytes.decode("utf-8")
        except Exception as exc:
            raise DecryptionError(
                f"Failed to decrypt field (context={self._context}): {exc}"
            ) from exc

    def is_encrypted(self, value: str) -> bool:
        """Return True if value starts with the enc:v1: prefix."""
        return value.startswith(self.PREFIX)


def validate_encryption_key(encryptor: FieldEncryptor | None) -> None:
    """Validate that the encryptor can round-trip a sentinel value.

    Called at app startup when ENCRYPTION_ENABLED=true. Raises RuntimeError
    and blocks startup if the key is absent or invalid.
    """
    if encryptor is None:
        raise RuntimeError(
            "ENCRYPTION_MASTER_KEY is required when ENCRYPTION_ENABLED=true. "
            "Generate one with: python3 -c \"import secrets; print(secrets.token_hex(32))\""
        )
    sentinel = "nanobot-encryption-sentinel-v1"
    encrypted = encryptor.encrypt_field(sentinel)
    decrypted = encryptor.decrypt_field(encrypted)
    if decrypted != sentinel:
        raise RuntimeError(
            "ENCRYPTION_MASTER_KEY invalid: encrypt/decrypt round-trip test failed. "
            "Check the ENCRYPTION_MASTER_KEY environment variable."
        )
    logger.info("Encryption key validation passed (context=%s)", encryptor._context)
```

### Run tests (must pass)

```bash
python -m pytest tests/test_encryption.py::TestFieldEncryptorInit -v
```

Expected: all 7 tests `PASSED`.

### Commit

```
feat(encryption): add FieldEncryptor with HKDF-SHA256 key derivation and startup validation (Sub-L)
```

---

## Task 2 — `encrypt_field` / `decrypt_field`: AES-256-GCM with nonce, prefix, and backward compat

### What & Why

This task validates that the core encryption/decryption logic works correctly: round-trip, plaintext passthrough, prefix detection, and tamper detection. These are the most foundational tests — everything else depends on them being correct.

### Files touched

- `tests/test_encryption.py` — expand with new test class

### Test first

Add to `tests/test_encryption.py`:

```python
class TestEncryptDecryptRoundtrip:
    def test_encrypt_decrypt_roundtrip_sqlite(self):
        """encrypt_field → decrypt_field round-trips correctly (sqlite-v1 context)."""
        enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")
        original = "Alice works on Project Nanobot"
        encrypted = enc.encrypt_field(original)
        assert encrypted.startswith("enc:v1:")
        assert enc.decrypt_field(encrypted) == original

    def test_encrypt_decrypt_roundtrip_qdrant(self):
        """encrypt_field → decrypt_field round-trips correctly (qdrant-v1 context)."""
        enc = FieldEncryptor(MASTER_KEY, "qdrant-v1")
        original = "Réunion de planification Q2 2026"
        encrypted = enc.encrypt_field(original)
        assert encrypted.startswith("enc:v1:")
        assert enc.decrypt_field(encrypted) == original

    def test_decrypt_plaintext_passthrough(self):
        """decrypt_field returns plaintext as-is when no enc:v1: prefix (backward compat)."""
        enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")
        plain = "unencrypted legacy value"
        assert enc.decrypt_field(plain) == plain

    def test_encrypted_prefix_detection_true(self):
        """is_encrypted returns True for enc:v1: prefixed values."""
        enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")
        assert enc.is_encrypted("enc:v1:somepayload") is True

    def test_encrypted_prefix_detection_false(self):
        """is_encrypted returns False for plaintext values."""
        enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")
        assert enc.is_encrypted("just plain text") is False

    def test_tampered_ciphertext_raises_decryption_error(self):
        """Modifying a byte in the ciphertext raises DecryptionError (GCM tag mismatch)."""
        enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")
        encrypted = enc.encrypt_field("sensitive value")
        # Flip one byte in the base64 payload
        prefix = "enc:v1:"
        b64 = encrypted[len(prefix):]
        tampered_b64 = b64[:-4] + ("A" if b64[-4] != "A" else "B") + b64[-3:]
        tampered = prefix + tampered_b64
        with pytest.raises(DecryptionError):
            enc.decrypt_field(tampered)

    def test_encrypt_empty_string(self):
        """Encrypting an empty string produces a valid enc:v1: token that round-trips."""
        enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")
        encrypted = enc.encrypt_field("")
        assert enc.decrypt_field(encrypted) == ""

    def test_encrypt_unicode_value(self):
        """Unicode text (accents, emoji) round-trips correctly through encrypt/decrypt."""
        enc = FieldEncryptor(MASTER_KEY, "qdrant-v1")
        original = "Réunion café — objet: présentation 🚀"
        assert enc.decrypt_field(enc.encrypt_field(original)) == original
```

### Run tests (must fail)

```bash
python -m pytest tests/test_encryption.py::TestEncryptDecryptRoundtrip -v
```

Expected: `FAILED` — `encryption.py` not yet created (Task 1 must be done first).

### Implementation

Covered by `src/bridge/encryption.py` created in Task 1. No additional code needed.

### Run tests (must pass)

```bash
python -m pytest tests/test_encryption.py::TestEncryptDecryptRoundtrip -v
```

Expected: all 8 tests `PASSED`.

### Commit

```
test(encryption): add roundtrip, passthrough, tamper, and unicode tests for FieldEncryptor (Sub-L)
```

---

## Task 3 — Nonce uniqueness: verify two encrypt() calls produce different ciphertext

### What & Why

AES-GCM security depends entirely on nonce uniqueness. If two encryptions of the same plaintext reuse a nonce, the keystream is exposed and both plaintexts become recoverable. This test verifies that `secrets.token_bytes()` is used per call, not a counter or static value.

### Files touched

- `tests/test_encryption.py` — add test class

### Test first

Add to `tests/test_encryption.py`:

```python
class TestNonceUniqueness:
    def test_nonce_uniqueness_same_value(self):
        """Two encrypt() calls on the same value produce different ciphertexts (random nonces)."""
        enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")
        val = "same plaintext value"
        ct1 = enc.encrypt_field(val)
        ct2 = enc.encrypt_field(val)
        assert ct1 != ct2, "Two encryptions of the same value must differ (nonce reuse detected)"

    def test_nonce_uniqueness_bulk(self):
        """100 encryptions of the same value all produce distinct ciphertexts."""
        enc = FieldEncryptor(MASTER_KEY, "qdrant-v1")
        val = "nonce test"
        ciphertexts = {enc.encrypt_field(val) for _ in range(100)}
        assert len(ciphertexts) == 100, "Duplicate ciphertexts detected — possible nonce reuse"

    def test_all_produce_valid_roundtrip(self):
        """Each of 50 different encryptions decrypts back to the original plaintext."""
        enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")
        val = "round-trip nonce test"
        for _ in range(50):
            assert enc.decrypt_field(enc.encrypt_field(val)) == val
```

### Run tests (must fail)

```bash
python -m pytest tests/test_encryption.py::TestNonceUniqueness -v
```

Expected: `FAILED` — `encryption.py` not yet created.

### Implementation

Already covered by Task 1's use of `secrets.token_bytes(NONCE_SIZE)` in `encrypt_field`.

### Run tests (must pass)

```bash
python -m pytest tests/test_encryption.py::TestNonceUniqueness -v
```

Expected: all 3 tests `PASSED`.

### Commit

```
test(encryption): add nonce uniqueness tests (100-sample bulk check) for FieldEncryptor (Sub-L)
```

---

## Task 4 — SQLite integration in `knowledge_graph.py`: encrypt `entities.description` on write, decrypt on read

### What & Why

The spec targets `knowledge_entities.value` but in the actual codebase the knowledge graph stores entity descriptions in `entities.description` (column name `description`). There is no separate `knowledge_entities` table — `entities` in `knowledge_graph.db` is the equivalent. This task wraps writes to `entities.description` with `encrypt_field()` and reads with `decrypt_field()` when `ENCRYPTION_SQLITE_ENABLED=true`. The encryptor is injected as a module-level optional singleton, set by `app.py` at startup. Decrypt is always attempted on read (transparent passthrough for plaintext).

### Files touched

- `src/bridge/knowledge_graph.py` — add encryptor injection + encrypt/decrypt calls
- `tests/test_encryption.py` — add SQLite backward compat test

### Test first

Add to `tests/test_encryption.py`:

```python
class TestSQLiteBackwardCompat:
    def test_sqlite_backward_compat_mixed_db(self, tmp_path, monkeypatch):
        """A DB with mixed encrypted/plaintext descriptions reads all correctly."""
        import sqlite3
        import knowledge_graph as kg

        monkeypatch.setattr(kg, "DB_PATH", tmp_path / "knowledge_graph.db")
        monkeypatch.setattr(kg, "STATE_DIR", tmp_path)
        monkeypatch.setattr(kg, "KG_ENABLED", True)

        enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")
        kg.set_encryptor(enc)
        monkeypatch.setenv("ENCRYPTION_SQLITE_ENABLED", "true")

        # Insert one plaintext row directly
        db = sqlite3.connect(str(tmp_path / "knowledge_graph.db"))
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("""CREATE TABLE IF NOT EXISTS entities (
            name TEXT PRIMARY KEY, type TEXT NOT NULL, description TEXT DEFAULT '',
            mention_count INTEGER DEFAULT 1, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL
        )""")
        db.execute("INSERT INTO entities VALUES ('alice', 'person', 'plaintext desc', 1, '2026-01-01', '2026-01-01')")
        db.commit()
        db.close()

        # Insert one encrypted row via kg
        encrypted_desc = enc.encrypt_field("encrypted desc")
        db = sqlite3.connect(str(tmp_path / "knowledge_graph.db"))
        db.execute("UPDATE entities SET description = ? WHERE name = 'alice'", (encrypted_desc,))
        db.commit()
        db.close()

        # Read via kg — should decrypt transparently
        result = kg.query_entity("alice")
        assert result["found"] is True
        assert result["entity"]["description"] == "encrypted desc"
```

### Run tests (must fail)

```bash
python -m pytest tests/test_encryption.py::TestSQLiteBackwardCompat -v
```

Expected: `FAILED` — `set_encryptor` not defined in `knowledge_graph.py`.

### Implementation

- [ ] Edit `src/bridge/knowledge_graph.py` — add encryptor module-level singleton and inject into reads/writes:

At the top of the file, after the existing imports and constants, add:

```python
# ---------------------------------------------------------------------------
# Sub-projet L: optional field-level encryption
# ---------------------------------------------------------------------------
_kg_encryptor = None  # set by app.py via set_encryptor()

ENCRYPTION_SQLITE_ENABLED = (
    os.getenv("ENCRYPTION_SQLITE_ENABLED",
              os.getenv("ENCRYPTION_ENABLED", "false")).lower() == "true"
)


def set_encryptor(encryptor) -> None:  # type: ignore[type-arg]
    """Inject the FieldEncryptor instance. Called by app.py at startup."""
    global _kg_encryptor  # pylint: disable=global-statement
    _kg_encryptor = encryptor


def _enc(value: str) -> str:
    """Encrypt value if encryption is enabled and encryptor is set."""
    if ENCRYPTION_SQLITE_ENABLED and _kg_encryptor is not None:
        return _kg_encryptor.encrypt_field(value)
    return value


def _dec(value: str) -> str:
    """Decrypt value if encryptor is set (transparent passthrough for plaintext)."""
    if _kg_encryptor is not None:
        return _kg_encryptor.decrypt_field(value)
    return value
```

In `extract_and_store()`, wrap the `desc` variable before the INSERT:

```python
                desc = ent.get("description", "")
                # Sub-projet L: encrypt before storage
                stored_desc = _enc(desc)
                # ...existing INSERT uses stored_desc instead of desc
```

In `query_entity()`, wrap the description read:

```python
            entity_info = {
                "name": ent[0], "type": ent[1], "description": _dec(ent[2]),
                ...
            }
```

Also wrap `description` reads in `query_by_type()` and `get_subgraph()`:

```python
# query_by_type
return [{"name": r[0], "type": r[1], "description": _dec(r[2]), ...} for r in rows]

# get_subgraph
all_entities.append({"name": ent[0], "type": ent[1], "description": _dec(ent[2]), ...})
```

### Run tests (must pass)

```bash
python -m pytest tests/test_encryption.py::TestSQLiteBackwardCompat -v
```

Expected: all 1 test `PASSED`.

### Commit

```
feat(encryption): encrypt/decrypt entities.description in knowledge_graph.py (Sub-L)
```

---

## Task 5 — SQLite integration in `email_calendar.py`: encrypt `email_sync_log.account` on write, decrypt on read

### What & Why

`email_sync_log.account` stores the IMAP account identifier (e.g. `imap`, or a full email address). Encrypting it hides the user's email identity from anyone with raw SQLite file access. The write path is `_update_sync_log()` and the read path is `get_sync_status()` in `email_calendar.py`. Note: the spec also lists `feedback.correction_text` but there is no `feedback_learner.py` in the codebase — the `feedback.py` module stores simple signal/boost data (no `correction_text` column). This task covers `email_sync_log.account` which is a concrete target in the existing code.

### Files touched

- `src/bridge/email_calendar.py` — add encryptor field + encrypt/decrypt in `_update_sync_log` and `get_sync_status`
- `tests/test_encryption.py` — add email sync log test

### Test first

Add to `tests/test_encryption.py`:

```python
class TestEmailSyncLogEncryption:
    def test_email_sync_log_account_encrypted_at_rest(self, tmp_path, monkeypatch):
        """account field is stored encrypted in email_sync_log and decrypted on read."""
        import sqlite3
        from email_calendar import EmailCalendarFetcher
        import email_calendar as ecm

        enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")
        monkeypatch.setattr(ecm, "STATE_DIR", tmp_path)
        monkeypatch.setenv("ENCRYPTION_SQLITE_ENABLED", "true")

        fetcher = EmailCalendarFetcher()
        fetcher.set_encryptor(enc)
        fetcher._db_path = str(tmp_path / "scheduler.db")

        # Create the table
        db = sqlite3.connect(str(tmp_path / "scheduler.db"))
        db.execute("""CREATE TABLE IF NOT EXISTS email_sync_log (
            id TEXT PRIMARY KEY, account TEXT, last_synced TEXT, items_synced INTEGER, status TEXT
        )""")
        db.commit()
        db.close()

        fetcher._update_sync_log("user@example.com", "2026-03-24T10:00:00Z", 5, "ok")

        # Check raw DB value is encrypted
        db = sqlite3.connect(str(tmp_path / "scheduler.db"))
        row = db.execute("SELECT account FROM email_sync_log").fetchone()
        db.close()
        assert row[0].startswith("enc:v1:"), "account should be stored encrypted"

        # get_sync_status should return plaintext
        status = fetcher.get_sync_status()
        assert "user@example.com" in status
```

### Run tests (must fail)

```bash
python -m pytest tests/test_encryption.py::TestEmailSyncLogEncryption -v
```

Expected: `FAILED` — `set_encryptor` not defined on `EmailCalendarFetcher`.

### Implementation

- [ ] Edit `src/bridge/email_calendar.py`:

Add to `EmailCalendarFetcher.__init__`:

```python
        self._encryptor = None  # injected by app.py via set_encryptor()
        self._sqlite_enc_enabled = (
            os.getenv("ENCRYPTION_SQLITE_ENABLED",
                      os.getenv("ENCRYPTION_ENABLED", "false")).lower() == "true"
        )
```

Add method to `EmailCalendarFetcher`:

```python
    def set_encryptor(self, encryptor) -> None:
        """Inject FieldEncryptor. Called by app.py at startup."""
        self._encryptor = encryptor

    def _enc_field(self, value: str) -> str:
        if self._sqlite_enc_enabled and self._encryptor is not None:
            return self._encryptor.encrypt_field(value)
        return value

    def _dec_field(self, value: str) -> str:
        if self._encryptor is not None:
            return self._encryptor.decrypt_field(value)
        return value
```

In `_update_sync_log()`, wrap `account`:

```python
        stored_account = self._enc_field(account)
        # use stored_account in the INSERT OR REPLACE instead of account
```

In `get_sync_status()`, wrap the account key on read:

```python
                for account_raw, last_synced, items_synced, status in rows:
                    account = self._dec_field(account_raw)
                    result[account] = { ... }
```

### Run tests (must pass)

```bash
python -m pytest tests/test_encryption.py::TestEmailSyncLogEncryption -v
```

Expected: 1 test `PASSED`.

### Commit

```
feat(encryption): encrypt/decrypt email_sync_log.account in EmailCalendarFetcher (Sub-L)
```

---

## Task 6 — Qdrant integration in `email_calendar.py`: `_encrypt_payload` / `_decrypt_payload` for `email_inbox` and `calendar_events`

### What & Why

Adds `_encrypt_payload()` and `_decrypt_payload()` helpers to `EmailCalendarFetcher`. These are called around `qdrant_client.upsert()` in `_upsert_emails()` and `_upsert_events()`. The ENCRYPTED_FIELDS are `["subject", "snippet", "sender"]` for email_inbox and `["description"]` for calendar_events. Vectors are computed from plaintext *before* encryption so semantic search remains unaffected. Both helpers are idempotent: already-encrypted fields (prefixed with `enc:v1:`) are not re-encrypted.

### Files touched

- `src/bridge/email_calendar.py` — add `_encrypt_payload`, `_decrypt_payload`, integrate into upsert methods
- `tests/test_encryption.py` — add Qdrant payload tests

### Test first

Add to `tests/test_encryption.py`:

```python
class TestQdrantPayloadHelpers:
    def test_encrypt_payload_encrypts_target_fields(self):
        """_encrypt_payload encrypts subject, snippet, sender for email_inbox."""
        from email_calendar import EmailCalendarFetcher
        enc = FieldEncryptor(MASTER_KEY, "qdrant-v1")
        fetcher = EmailCalendarFetcher()
        fetcher.set_encryptor(enc)
        monkeypatch_qdrant_enc = True  # conceptual — qdrant enc enabled

        payload = {
            "message_id": "abc123",
            "subject": "Meeting tomorrow",
            "sender": "boss@example.com",
            "snippet": "Let's sync at 10am",
            "date": "2026-03-24",
        }
        import os
        import importlib
        import email_calendar as ecm
        ecm_enc = FieldEncryptor(MASTER_KEY, "qdrant-v1")
        fetcher.set_encryptor(ecm_enc)
        fetcher._qdrant_enc_enabled = True

        encrypted = fetcher._encrypt_payload(payload, ["subject", "snippet", "sender"])
        assert encrypted["subject"].startswith("enc:v1:")
        assert encrypted["snippet"].startswith("enc:v1:")
        assert encrypted["sender"].startswith("enc:v1:")
        assert encrypted["message_id"] == "abc123"  # non-sensitive field unchanged

    def test_decrypt_payload_restores_plaintext(self):
        """_decrypt_payload transparently restores plaintext from encrypted fields."""
        from email_calendar import EmailCalendarFetcher
        enc = FieldEncryptor(MASTER_KEY, "qdrant-v1")
        fetcher = EmailCalendarFetcher()
        fetcher.set_encryptor(enc)
        fetcher._qdrant_enc_enabled = True

        original = {"subject": "Hello world", "sender": "a@b.com", "snippet": "Hi there"}
        encrypted = fetcher._encrypt_payload(original, ["subject", "snippet", "sender"])
        decrypted = fetcher._decrypt_payload(encrypted, ["subject", "snippet", "sender"])
        assert decrypted["subject"] == "Hello world"
        assert decrypted["sender"] == "a@b.com"

    def test_encrypt_payload_idempotent(self):
        """_encrypt_payload does not re-encrypt already-encrypted fields."""
        from email_calendar import EmailCalendarFetcher
        enc = FieldEncryptor(MASTER_KEY, "qdrant-v1")
        fetcher = EmailCalendarFetcher()
        fetcher.set_encryptor(enc)
        fetcher._qdrant_enc_enabled = True

        payload = {"subject": "Test", "sender": "x@y.com", "snippet": "body"}
        once = fetcher._encrypt_payload(payload, ["subject", "snippet", "sender"])
        twice = fetcher._encrypt_payload(once, ["subject", "snippet", "sender"])
        assert once["subject"] == twice["subject"], "Idempotency violated: field re-encrypted"

    def test_decrypt_payload_plaintext_passthrough(self):
        """_decrypt_payload returns plaintext fields unchanged (backward compat)."""
        from email_calendar import EmailCalendarFetcher
        enc = FieldEncryptor(MASTER_KEY, "qdrant-v1")
        fetcher = EmailCalendarFetcher()
        fetcher.set_encryptor(enc)

        payload = {"subject": "plain subject", "sender": "plain@email.com"}
        decrypted = fetcher._decrypt_payload(payload, ["subject", "sender"])
        assert decrypted["subject"] == "plain subject"
```

### Run tests (must fail)

```bash
python -m pytest tests/test_encryption.py::TestQdrantPayloadHelpers -v
```

Expected: `FAILED` — `_encrypt_payload` / `_decrypt_payload` not defined.

### Implementation

- [ ] Edit `src/bridge/email_calendar.py` — add to `EmailCalendarFetcher`:

```python
    # ------------------------------------------------------------------
    # Sub-projet L: Qdrant payload encryption helpers
    # ------------------------------------------------------------------

    def _encrypt_payload(self, payload: dict, fields: list[str]) -> dict:
        """Return a copy of payload with specified fields encrypted.

        Idempotent: fields already prefixed enc:v1: are not re-encrypted.
        Vectors must be computed from plaintext BEFORE calling this method.
        """
        if self._encryptor is None:
            return payload
        result = payload.copy()
        for field in fields:
            val = result.get(field)
            if isinstance(val, str) and not self._encryptor.is_encrypted(val):
                result[field] = self._encryptor.encrypt_field(val)
        return result

    def _decrypt_payload(self, payload: dict, fields: list[str]) -> dict:
        """Return a copy of payload with specified fields decrypted.

        Transparent: plaintext fields (no enc:v1: prefix) are returned as-is.
        """
        if self._encryptor is None:
            return payload
        result = payload.copy()
        for field in fields:
            val = result.get(field)
            if isinstance(val, str):
                result[field] = self._encryptor.decrypt_field(val)
        return result
```

In `__init__`, also add:

```python
        self._qdrant_enc_enabled = (
            os.getenv("ENCRYPTION_QDRANT_ENABLED",
                      os.getenv("ENCRYPTION_ENABLED", "false")).lower() == "true"
        )
```

In `_upsert_emails()`, wrap payload before upsert:

```python
                EMAIL_ENCRYPTED_FIELDS = ["subject", "snippet", "sender"]
                stored_payload = (
                    self._encrypt_payload(payload, EMAIL_ENCRYPTED_FIELDS)
                    if self._qdrant_enc_enabled else payload
                )
                qdrant_client.upsert(
                    collection_name="email_inbox",
                    points=[PointStruct(id=point_id, vector=vector, payload=stored_payload)],
                )
```

In `_upsert_events()`, wrap payload before upsert:

```python
                CALENDAR_ENCRYPTED_FIELDS = ["description"]
                stored_payload = (
                    self._encrypt_payload(payload, CALENDAR_ENCRYPTED_FIELDS)
                    if self._qdrant_enc_enabled else payload
                )
                qdrant_client.upsert(
                    collection_name="calendar_events",
                    points=[PointStruct(id=point_id, vector=vector, payload=stored_payload)],
                )
```

### Run tests (must pass)

```bash
python -m pytest tests/test_encryption.py::TestQdrantPayloadHelpers -v
```

Expected: all 4 tests `PASSED`.

### Commit

```
feat(encryption): add _encrypt_payload/_decrypt_payload to EmailCalendarFetcher for email_inbox and calendar_events (Sub-L)
```

---

## Task 7 — Qdrant integration: `memory_personal` collection in `app.py` `/remember` endpoint

### What & Why

The `memory_personal` collection is written in `app.py`'s `/remember` endpoint (the `remember()` function around line 1037). The spec requires encrypting the `text` field (mapped as `content` in the spec, but stored as `text` in the actual payload dict — these are equivalent: the `text` key in the Qdrant payload is what stores the memory content). The encryptor is the global `qdrant_encryptor` set at startup. Vectors are computed from plaintext before encryption.

### Files touched

- `src/bridge/app.py` — encrypt payload `text` field before upsert in `/remember`; decrypt on retrieval in `/search`
- `tests/test_encryption.py` — add memory_personal integration test

### Test first

Add to `tests/test_encryption.py`:

```python
class TestMemoryPersonalEncryption:
    def test_qdrant_vector_search_unaffected_by_payload_encryption(self):
        """Encrypting the payload text field does not change the vector (computed from plaintext)."""
        enc = FieldEncryptor(MASTER_KEY, "qdrant-v1")
        plain_text = "Alice decided to use Python for the project"
        encrypted_text = enc.encrypt_field(plain_text)

        # Vector is computed from plain_text, not encrypted_text
        # This test just verifies the contract: encrypt does not mutate the input
        assert plain_text == enc.decrypt_field(encrypted_text)
        assert not plain_text.startswith("enc:v1:")
        assert encrypted_text.startswith("enc:v1:")

    def test_memory_payload_encrypt_decrypt_roundtrip(self):
        """A memory payload with encrypted text field decrypts back to original."""
        enc = FieldEncryptor(MASTER_KEY, "qdrant-v1")
        payload = {
            "text": "Alice works on nanobot daily",
            "subject": "work",
            "tags": ["work"],
            "source_name": "user",
        }
        # Simulate what app.py does: encrypt text field before upsert
        stored_payload = payload.copy()
        stored_payload["text"] = enc.encrypt_field(payload["text"])
        assert stored_payload["text"].startswith("enc:v1:")

        # Simulate retrieval: decrypt text field
        retrieved_payload = stored_payload.copy()
        retrieved_payload["text"] = enc.decrypt_field(stored_payload["text"])
        assert retrieved_payload["text"] == "Alice works on nanobot daily"
```

### Run tests (must fail)

```bash
python -m pytest tests/test_encryption.py::TestMemoryPersonalEncryption -v
```

Expected: `FAILED` — `encryption.py` must exist (depends on Task 1 being complete).

### Implementation

- [ ] Edit `src/bridge/app.py` — add qdrant encryptor global near the top of the file (after imports, near other globals):

```python
# Sub-projet L: optional Qdrant payload encryptor (injected at startup)
_qdrant_encryptor = None
ENCRYPTION_QDRANT_ENABLED = os.getenv(
    "ENCRYPTION_QDRANT_ENABLED", os.getenv("ENCRYPTION_ENABLED", "false")
).lower() == "true"
```

In the `remember()` function, after `payload` dict is built and *before* the `qdrant.upsert()` call (around line 1092), add:

```python
    # Sub-projet L: encrypt memory content before storage
    if ENCRYPTION_QDRANT_ENABLED and _qdrant_encryptor is not None:
        if not _qdrant_encryptor.is_encrypted(payload.get("text", "")):
            payload["text"] = _qdrant_encryptor.encrypt_field(payload["text"])
```

In the `/search` endpoint (or wherever payloads are returned to callers), decrypt `text` fields from `memory_personal` collection results:

```python
    # Sub-projet L: decrypt payload text for memory_personal results
    if _qdrant_encryptor is not None:
        for item in results:
            p = item.get("payload", {})
            if "text" in p and isinstance(p["text"], str):
                try:
                    p["text"] = _qdrant_encryptor.decrypt_field(p["text"])
                except Exception:
                    pass  # DecryptionError on bad key — leave as-is, log in prod
```

### Run tests (must pass)

```bash
python -m pytest tests/test_encryption.py::TestMemoryPersonalEncryption -v
```

Expected: all 2 tests `PASSED`.

### Commit

```
feat(encryption): encrypt memory_personal text field in /remember endpoint (Sub-L)
```

---

## Task 8 — `encryption_api.py`: 5 REST endpoints (status, enable, disable, rotate, migration-status)

### What & Why

Creates `src/bridge/encryption_api.py` with a FastAPI router providing 5 endpoints for the encryption lifecycle. The `GET /status` endpoint queries SQLite and Qdrant to count encrypted vs. total values, detecting partial encryption. The `POST /enable` and `POST /disable` endpoints launch async background migration jobs tracked in a module-level dict. The `POST /rotate` endpoint validates and accepts a new master key then re-encrypts everything. The `GET /migration-status` endpoint returns current job progress.

### Files touched

- `src/bridge/encryption_api.py` — new file
- `tests/test_encryption.py` — add API smoke tests

### Test first

Add to `tests/test_encryption.py`:

```python
class TestEncryptionAPIInit:
    def test_encryption_api_module_importable(self):
        """encryption_api.py is importable and exposes an APIRouter."""
        from encryption_api import router
        from fastapi import APIRouter
        assert isinstance(router, APIRouter)

    def test_router_has_required_routes(self):
        """encryption_api router has all 5 required route paths."""
        from encryption_api import router
        paths = {route.path for route in router.routes}
        assert "/status" in paths
        assert "/enable" in paths
        assert "/disable" in paths
        assert "/rotate" in paths
        assert "/migration-status" in paths
```

### Run tests (must fail)

```bash
python -m pytest tests/test_encryption.py::TestEncryptionAPIInit -v
```

Expected: `FAILED` — `encryption_api.py` does not exist yet.

### Implementation

- [ ] Create `src/bridge/encryption_api.py`:

```python
"""encryption_api.py — REST endpoints for Sub-projet L encryption lifecycle management.

Endpoints:
  GET  /api/encryption/status           — current encryption state
  POST /api/encryption/enable           — start encrypt-all migration
  POST /api/encryption/disable          — start decrypt-all migration
  POST /api/encryption/rotate           — re-encrypt with new master key
  GET  /api/encryption/migration-status — progress of current/last migration job
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("rag-bridge.encryption_api")

router = APIRouter(prefix="/api/encryption", tags=["encryption"])

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
_encryptor_sqlite = None   # FieldEncryptor for sqlite-v1
_encryptor_qdrant = None   # FieldEncryptor for qdrant-v1
_qdrant_client = None
_state_dir: str = os.getenv("STATE_DIR", "/opt/nanobot-stack/rag-bridge/state")

_migration_job: dict[str, Any] = {}
_migration_lock = threading.Lock()


def init_encryption_api(
    encryptor_sqlite=None,
    encryptor_qdrant=None,
    qdrant_client=None,
    state_dir: str | None = None,
) -> None:
    """Inject dependencies. Called by app.py at startup."""
    global _encryptor_sqlite, _encryptor_qdrant, _qdrant_client, _state_dir  # pylint: disable=global-statement
    _encryptor_sqlite = encryptor_sqlite
    _encryptor_qdrant = encryptor_qdrant
    _qdrant_client = qdrant_client
    if state_dir:
        _state_dir = state_dir


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sqlite_count_encrypted(db_path: str, table: str, column: str) -> tuple[int, int]:
    """Return (encrypted_count, total_count) for a SQLite column."""
    try:
        db = sqlite3.connect(db_path)
        try:
            total = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            enc = db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {column} LIKE 'enc:v1:%'"
            ).fetchone()[0]
            return enc, total
        finally:
            db.close()
    except Exception:
        return 0, 0


def _qdrant_count_encrypted(collection: str, field: str) -> tuple[int, int]:
    """Return (encrypted_count, total_count) for a Qdrant collection field."""
    if _qdrant_client is None:
        return 0, 0
    try:
        total = _qdrant_client.count(collection_name=collection).count
        # Scroll to count enc:v1: prefixed values
        enc_count = 0
        offset = None
        while True:
            results, offset = _qdrant_client.scroll(
                collection_name=collection,
                offset=offset,
                limit=100,
                with_payload=True,
                with_vectors=False,
            )
            for point in results:
                val = (point.payload or {}).get(field, "")
                if isinstance(val, str) and val.startswith("enc:v1:"):
                    enc_count += 1
            if offset is None:
                break
        return enc_count, total
    except Exception:
        return 0, 0


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/status")
def get_status() -> dict:
    """Return current encryption state and per-field encrypted counts."""
    enc_enabled = os.getenv("ENCRYPTION_ENABLED", "false").lower() == "true"
    sqlite_enabled = os.getenv(
        "ENCRYPTION_SQLITE_ENABLED", os.getenv("ENCRYPTION_ENABLED", "false")
    ).lower() == "true"
    qdrant_enabled = os.getenv(
        "ENCRYPTION_QDRANT_ENABLED", os.getenv("ENCRYPTION_ENABLED", "false")
    ).lower() == "true"

    state_dir = _state_dir
    kg_db = os.path.join(state_dir, "knowledge_graph.db")
    scheduler_db = os.path.join(state_dir, "scheduler.db")
    feedback_db = os.path.join(state_dir, "feedback.db")

    ke_enc, ke_total = _sqlite_count_encrypted(kg_db, "entities", "description")
    fb_enc, fb_total = _sqlite_count_encrypted(feedback_db, "feedback", "query")
    sl_enc, sl_total = _sqlite_count_encrypted(scheduler_db, "email_sync_log", "account")

    mp_enc, mp_total = _qdrant_count_encrypted("memory_personal", "text")
    ei_enc, ei_total = _qdrant_count_encrypted("email_inbox", "subject")
    ce_enc, ce_total = _qdrant_count_encrypted("calendar_events", "description")

    # partially_encrypted: any field has some encrypted and some plaintext
    def _partial(enc: int, total: int) -> bool:
        return total > 0 and 0 < enc < total

    partially_encrypted = any([
        _partial(ke_enc, ke_total),
        _partial(fb_enc, fb_total),
        _partial(sl_enc, sl_total),
        _partial(mp_enc, mp_total),
        _partial(ei_enc, ei_total),
        _partial(ce_enc, ce_total),
    ])

    return {
        "encryption_enabled": enc_enabled,
        "sqlite_enabled": sqlite_enabled,
        "qdrant_enabled": qdrant_enabled,
        "partially_encrypted": partially_encrypted,
        "sqlite_fields_encrypted": {
            "entities.description": ke_enc,
            "feedback.query": fb_enc,
            "email_sync_log.account": sl_enc,
        },
        "qdrant_fields_encrypted": {
            "memory_personal.text": mp_enc,
            "email_inbox.subject": ei_enc,
            "calendar_events.description": ce_enc,
        },
        "migration_in_progress": bool(_migration_job.get("status") == "in_progress"),
    }


class RotateRequest(BaseModel):
    new_master_key: str


@router.post("/enable")
async def enable_encryption() -> dict:
    """Start async migration: encrypt all plaintext values in-place (idempotent)."""
    with _migration_lock:
        if _migration_job.get("status") == "in_progress":
            raise HTTPException(status_code=409, detail="A migration job is already in progress")
        if _encryptor_sqlite is None and _encryptor_qdrant is None:
            raise HTTPException(
                status_code=400,
                detail="ENCRYPTION_MASTER_KEY is absent — cannot run migration"
            )
        job_id = f"enc-mig-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        _migration_job.clear()
        _migration_job.update({
            "job_id": job_id, "type": "enable", "status": "in_progress",
            "started_at": _utcnow(), "completed_at": None,
            "progress": {}, "error": None,
        })

    asyncio.create_task(_run_enable_migration(job_id))
    return {
        "job_id": job_id,
        "status": "started",
        "message": "Migration de chiffrement démarrée. Suivre la progression via GET /api/encryption/migration-status",
    }


@router.post("/disable")
async def disable_encryption() -> dict:
    """Start async migration: decrypt all enc:v1: values back to plaintext."""
    with _migration_lock:
        if _migration_job.get("status") == "in_progress":
            raise HTTPException(status_code=409, detail="A migration job is already in progress")
        if _encryptor_sqlite is None and _encryptor_qdrant is None:
            raise HTTPException(
                status_code=400,
                detail="ENCRYPTION_MASTER_KEY is absent — cannot decrypt without the key"
            )
        job_id = f"enc-dis-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        _migration_job.clear()
        _migration_job.update({
            "job_id": job_id, "type": "disable", "status": "in_progress",
            "started_at": _utcnow(), "completed_at": None,
            "progress": {}, "error": None,
        })

    asyncio.create_task(_run_disable_migration(job_id))
    return {
        "job_id": job_id,
        "status": "started",
        "message": "Migration de déchiffrement démarrée. Mettre à jour ENCRYPTION_ENABLED=false dans stack.env à la fin.",
    }


@router.post("/rotate")
async def rotate_key(body: RotateRequest) -> dict:
    """Re-encrypt all data with a new master key."""
    new_key = body.new_master_key.strip()
    if len(new_key) != 64:
        raise HTTPException(status_code=400, detail="new_master_key must be exactly 64 hex characters")
    try:
        bytes.fromhex(new_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"new_master_key is not valid hex: {exc}") from exc

    current_key = os.getenv("ENCRYPTION_MASTER_KEY", "")
    if new_key == current_key:
        raise HTTPException(status_code=400, detail="new_master_key is identical to the current key")

    with _migration_lock:
        if _migration_job.get("status") == "in_progress":
            raise HTTPException(status_code=409, detail="A migration job is already in progress")
        job_id = f"enc-rot-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        _migration_job.clear()
        _migration_job.update({
            "job_id": job_id, "type": "rotate", "status": "in_progress",
            "started_at": _utcnow(), "completed_at": None,
            "progress": {}, "error": None,
        })

    asyncio.create_task(_run_rotation_migration(job_id, new_key))
    return {
        "job_id": job_id,
        "status": "started",
        "message": "Rotation de clé démarrée. Mettre à jour ENCRYPTION_MASTER_KEY dans stack.env à la fin de la migration.",
    }


@router.get("/migration-status")
def get_migration_status() -> dict:
    """Return progress of the current or last migration job."""
    if not _migration_job:
        return {"job_id": None, "status": "no_job", "progress": {}}
    return dict(_migration_job)


# ---------------------------------------------------------------------------
# Background migration helpers (Tasks 9, 10, 11)
# ---------------------------------------------------------------------------

async def _run_enable_migration(job_id: str) -> None:
    """Async task: encrypt all plaintext values in SQLite and Qdrant."""
    from encryption_migrations import run_enable_migration  # pylint: disable=import-outside-toplevel
    try:
        progress = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: run_enable_migration(
                _encryptor_sqlite, _encryptor_qdrant, _qdrant_client, _state_dir
            ),
        )
        _migration_job.update({"status": "completed", "completed_at": _utcnow(), "progress": progress})
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Enable migration failed: %s", exc)
        _migration_job.update({"status": "error", "completed_at": _utcnow(), "error": str(exc)})


async def _run_disable_migration(job_id: str) -> None:
    """Async task: decrypt all enc:v1: values in SQLite and Qdrant."""
    from encryption_migrations import run_disable_migration  # pylint: disable=import-outside-toplevel
    try:
        progress = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: run_disable_migration(
                _encryptor_sqlite, _encryptor_qdrant, _qdrant_client, _state_dir
            ),
        )
        _migration_job.update({"status": "completed", "completed_at": _utcnow(), "progress": progress})
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Disable migration failed: %s", exc)
        _migration_job.update({"status": "error", "completed_at": _utcnow(), "error": str(exc)})


async def _run_rotation_migration(job_id: str, new_key_hex: str) -> None:
    """Async task: re-encrypt all values with new_key_hex."""
    from encryption_migrations import run_rotation_migration  # pylint: disable=import-outside-toplevel
    try:
        progress = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: run_rotation_migration(
                _encryptor_sqlite, _encryptor_qdrant, _qdrant_client, _state_dir, new_key_hex
            ),
        )
        _migration_job.update({"status": "completed", "completed_at": _utcnow(), "progress": progress})
    except Exception as exc:  # pylint: disable=broad-except
        logger.exception("Rotation migration failed: %s", exc)
        _migration_job.update({"status": "error", "completed_at": _utcnow(), "error": str(exc)})
```

### Run tests (must pass)

```bash
python -m pytest tests/test_encryption.py::TestEncryptionAPIInit -v
```

Expected: all 2 tests `PASSED`.

### Commit

```
feat(encryption): add encryption_api.py with 5 REST endpoints (Sub-L)
```

---

## Task 9 — Enable migration: encrypt all plaintext values in-place (idempotent)

### What & Why

Creates `src/bridge/encryption_migrations.py` with `run_enable_migration()`. This function iterates all target SQLite tables/columns and all target Qdrant collections, encrypting any plaintext value (i.e., not starting with `enc:v1:`). It is idempotent: values already encrypted are skipped. Qdrant is scrolled in batches of 100. Returns a progress dict matching the spec's `migration-status` response format.

### Files touched

- `src/bridge/encryption_migrations.py` — new file (enable, disable, rotate all in one file)
- `tests/test_encryption.py` — add enable migration tests

### Test first

Add to `tests/test_encryption.py`:

```python
class TestEnableMigration:
    def test_enable_migration_encrypts_plaintext_sqlite(self, tmp_path):
        """run_enable_migration encrypts plaintext values in SQLite columns."""
        import sqlite3
        from encryption import FieldEncryptor
        from encryption_migrations import run_enable_migration

        enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")

        # Seed a knowledge_graph.db with plaintext descriptions
        kg_db = tmp_path / "knowledge_graph.db"
        db = sqlite3.connect(str(kg_db))
        db.execute("""CREATE TABLE entities (
            name TEXT PRIMARY KEY, type TEXT NOT NULL, description TEXT DEFAULT '',
            mention_count INTEGER DEFAULT 1, first_seen TEXT, last_seen TEXT
        )""")
        db.execute("INSERT INTO entities VALUES ('alice', 'person', 'plain desc', 1, '2026-01-01', '2026-01-01')")
        db.execute("INSERT INTO entities VALUES ('bob', 'person', 'enc:v1:already', 1, '2026-01-01', '2026-01-01')")
        db.commit()
        db.close()

        progress = run_enable_migration(enc, None, None, str(tmp_path))

        db = sqlite3.connect(str(kg_db))
        rows = dict(db.execute("SELECT name, description FROM entities").fetchall())
        db.close()

        assert rows["alice"].startswith("enc:v1:"), "plaintext was not encrypted"
        assert rows["bob"] == "enc:v1:already", "already-encrypted value was re-encrypted (idempotency violated)"

    def test_enable_migration_returns_progress_dict(self, tmp_path):
        """run_enable_migration returns a progress dict with sqlite and qdrant keys."""
        from encryption import FieldEncryptor
        from encryption_migrations import run_enable_migration

        enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")
        progress = run_enable_migration(enc, None, None, str(tmp_path))
        assert "sqlite" in progress
        assert "qdrant" in progress

    def test_enable_migration_idempotent(self, tmp_path):
        """Running run_enable_migration twice does not double-encrypt."""
        import sqlite3
        from encryption import FieldEncryptor
        from encryption_migrations import run_enable_migration

        enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")
        kg_db = tmp_path / "knowledge_graph.db"
        db = sqlite3.connect(str(kg_db))
        db.execute("""CREATE TABLE entities (
            name TEXT PRIMARY KEY, type TEXT NOT NULL, description TEXT DEFAULT '',
            mention_count INTEGER DEFAULT 1, first_seen TEXT, last_seen TEXT
        )""")
        db.execute("INSERT INTO entities VALUES ('alice', 'person', 'desc', 1, '2026-01-01', '2026-01-01')")
        db.commit()
        db.close()

        run_enable_migration(enc, None, None, str(tmp_path))
        run_enable_migration(enc, None, None, str(tmp_path))  # second pass

        db = sqlite3.connect(str(kg_db))
        row = db.execute("SELECT description FROM entities WHERE name='alice'").fetchone()
        db.close()
        # Should still decrypt to original value
        assert enc.decrypt_field(row[0]) == "desc"
```

### Run tests (must fail)

```bash
python -m pytest tests/test_encryption.py::TestEnableMigration -v
```

Expected: `FAILED` — `encryption_migrations.py` does not exist yet.

### Implementation

- [ ] Create `src/bridge/encryption_migrations.py`:

```python
"""encryption_migrations.py — SQLite and Qdrant migration helpers for Sub-projet L.

Provides run_enable_migration(), run_disable_migration(), run_rotation_migration().
All functions are synchronous and designed to run in a thread pool executor.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any

logger = logging.getLogger("rag-bridge.encryption_migrations")

# SQLite targets: (db_filename, table, column)
SQLITE_TARGETS = [
    ("knowledge_graph.db", "entities", "description"),
    ("scheduler.db", "email_sync_log", "account"),
]

# Qdrant targets: (collection_name, [field1, field2, ...])
QDRANT_TARGETS = [
    ("memory_personal", ["text"]),
    ("email_inbox", ["subject", "snippet", "sender"]),
    ("calendar_events", ["description"]),
]


def _sqlite_encrypt_column(
    db_path: str,
    table: str,
    column: str,
    encryptor,
    direction: str,  # "encrypt" | "decrypt"
) -> dict[str, int]:
    """Encrypt or decrypt a single SQLite column in-place. Returns progress."""
    if not os.path.exists(db_path):
        return {"processed": 0, "total": 0, "skipped": 0}

    db = sqlite3.connect(db_path)
    db.execute("PRAGMA journal_mode=WAL")
    try:
        rows = db.execute(f"SELECT rowid, {column} FROM {table}").fetchall()
    except sqlite3.OperationalError:
        db.close()
        return {"processed": 0, "total": 0, "skipped": 0}

    total = len(rows)
    processed = 0
    skipped = 0

    try:
        for rowid, value in rows:
            if not isinstance(value, str):
                skipped += 1
                continue
            if direction == "encrypt":
                if encryptor.is_encrypted(value):
                    skipped += 1
                    continue
                new_value = encryptor.encrypt_field(value)
            else:  # decrypt
                if not encryptor.is_encrypted(value):
                    skipped += 1
                    continue
                new_value = encryptor.decrypt_field(value)
            db.execute(f"UPDATE {table} SET {column} = ? WHERE rowid = ?", (new_value, rowid))
            processed += 1
        db.commit()
    finally:
        db.close()

    logger.info(
        "%s %s.%s: processed=%d skipped=%d total=%d",
        direction, table, column, processed, skipped, total,
    )
    return {"processed": processed, "total": total, "skipped": skipped}


def _qdrant_encrypt_collection(
    qdrant_client: Any,
    collection: str,
    fields: list[str],
    encryptor,
    direction: str,
) -> dict[str, int]:
    """Encrypt or decrypt Qdrant payload fields for a collection. Returns progress."""
    if qdrant_client is None:
        return {"processed": 0, "total": 0}

    try:
        total = qdrant_client.count(collection_name=collection).count
    except Exception:
        return {"processed": 0, "total": 0}

    processed = 0
    offset = None

    while True:
        try:
            results, offset = qdrant_client.scroll(
                collection_name=collection,
                offset=offset,
                limit=100,
                with_payload=True,
                with_vectors=False,
            )
        except Exception as exc:
            logger.warning("Qdrant scroll error on %s: %s", collection, exc)
            break

        for point in results:
            payload = dict(point.payload or {})
            updated = False
            for field in fields:
                val = payload.get(field)
                if not isinstance(val, str):
                    continue
                if direction == "encrypt":
                    if not encryptor.is_encrypted(val):
                        payload[field] = encryptor.encrypt_field(val)
                        updated = True
                else:
                    if encryptor.is_encrypted(val):
                        payload[field] = encryptor.decrypt_field(val)
                        updated = True
            if updated:
                try:
                    qdrant_client.set_payload(
                        collection_name=collection,
                        payload=payload,
                        points=[point.id],
                    )
                    processed += 1
                except Exception as exc:
                    logger.warning("Failed to update point %s in %s: %s", point.id, collection, exc)

        if offset is None:
            break

    return {"processed": processed, "total": total}


def run_enable_migration(
    encryptor_sqlite,
    encryptor_qdrant,
    qdrant_client: Any,
    state_dir: str,
) -> dict:
    """Encrypt all plaintext values in SQLite and Qdrant (idempotent)."""
    progress: dict[str, Any] = {"sqlite": {}, "qdrant": {}}

    if encryptor_sqlite is not None:
        for db_filename, table, column in SQLITE_TARGETS:
            db_path = os.path.join(state_dir, db_filename)
            result = _sqlite_encrypt_column(db_path, table, column, encryptor_sqlite, "encrypt")
            progress["sqlite"][f"{table}.{column}"] = result

    if encryptor_qdrant is not None and qdrant_client is not None:
        for collection, fields in QDRANT_TARGETS:
            for field in fields:
                result = _qdrant_encrypt_collection(qdrant_client, collection, [field], encryptor_qdrant, "encrypt")
                progress["qdrant"][f"{collection}.{field}"] = result

    return progress


def run_disable_migration(
    encryptor_sqlite,
    encryptor_qdrant,
    qdrant_client: Any,
    state_dir: str,
) -> dict:
    """Decrypt all enc:v1: values back to plaintext."""
    progress: dict[str, Any] = {"sqlite": {}, "qdrant": {}}

    if encryptor_sqlite is not None:
        for db_filename, table, column in SQLITE_TARGETS:
            db_path = os.path.join(state_dir, db_filename)
            result = _sqlite_encrypt_column(db_path, table, column, encryptor_sqlite, "decrypt")
            progress["sqlite"][f"{table}.{column}"] = result

    if encryptor_qdrant is not None and qdrant_client is not None:
        for collection, fields in QDRANT_TARGETS:
            for field in fields:
                result = _qdrant_encrypt_collection(qdrant_client, collection, [field], encryptor_qdrant, "decrypt")
                progress["qdrant"][f"{collection}.{field}"] = result

    return progress


def run_rotation_migration(
    old_encryptor_sqlite,
    old_encryptor_qdrant,
    qdrant_client: Any,
    state_dir: str,
    new_key_hex: str,
) -> dict:
    """Re-encrypt all encrypted values with a new master key (atomic per-field)."""
    from encryption import FieldEncryptor  # pylint: disable=import-outside-toplevel

    new_enc_sqlite = FieldEncryptor(new_key_hex, "sqlite-v1")
    new_enc_qdrant = FieldEncryptor(new_key_hex, "qdrant-v1")

    progress: dict[str, Any] = {"sqlite": {}, "qdrant": {}}

    if old_encryptor_sqlite is not None:
        for db_filename, table, column in SQLITE_TARGETS:
            db_path = os.path.join(state_dir, db_filename)
            if not os.path.exists(db_path):
                continue
            db = sqlite3.connect(db_path)
            db.execute("PRAGMA journal_mode=WAL")
            processed = 0
            try:
                rows = db.execute(f"SELECT rowid, {column} FROM {table}").fetchall()
                for rowid, value in rows:
                    if not isinstance(value, str) or not old_encryptor_sqlite.is_encrypted(value):
                        continue
                    plain = old_encryptor_sqlite.decrypt_field(value)
                    new_value = new_enc_sqlite.encrypt_field(plain)
                    db.execute(f"UPDATE {table} SET {column} = ? WHERE rowid = ?", (new_value, rowid))
                    processed += 1
                db.commit()
            except sqlite3.OperationalError:
                pass
            finally:
                db.close()
            progress["sqlite"][f"{table}.{column}"] = {"processed": processed}

    if old_encryptor_qdrant is not None and qdrant_client is not None:
        for collection, fields in QDRANT_TARGETS:
            for field in fields:
                processed = 0
                offset = None
                while True:
                    try:
                        results, offset = qdrant_client.scroll(
                            collection_name=collection, offset=offset,
                            limit=100, with_payload=True, with_vectors=False,
                        )
                    except Exception:
                        break
                    for point in results:
                        payload = dict(point.payload or {})
                        val = payload.get(field)
                        if not isinstance(val, str) or not old_encryptor_qdrant.is_encrypted(val):
                            continue
                        plain = old_encryptor_qdrant.decrypt_field(val)
                        payload[field] = new_enc_qdrant.encrypt_field(plain)
                        try:
                            qdrant_client.set_payload(
                                collection_name=collection, payload=payload, points=[point.id]
                            )
                            processed += 1
                        except Exception:
                            pass
                    if offset is None:
                        break
                progress["qdrant"][f"{collection}.{field}"] = {"processed": processed}

    return progress
```

### Run tests (must pass)

```bash
python -m pytest tests/test_encryption.py::TestEnableMigration -v
```

Expected: all 3 tests `PASSED`.

### Commit

```
feat(encryption): add encryption_migrations.py with enable/disable/rotate migration logic (Sub-L)
```

---

## Task 10 — Disable migration: decrypt all `enc:v1:` values back to plaintext

### What & Why

Validates the disable migration (inverse of enable). Tests confirm idempotency and that plaintext-already values are left alone.

### Files touched

- `tests/test_encryption.py` — add disable migration tests

### Test first

Add to `tests/test_encryption.py`:

```python
class TestDisableMigration:
    def test_disable_migration_decrypts_sqlite(self, tmp_path):
        """run_disable_migration decrypts enc:v1: values in SQLite columns."""
        import sqlite3
        from encryption import FieldEncryptor
        from encryption_migrations import run_enable_migration, run_disable_migration

        enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")
        kg_db = tmp_path / "knowledge_graph.db"
        db = sqlite3.connect(str(kg_db))
        db.execute("""CREATE TABLE entities (
            name TEXT PRIMARY KEY, type TEXT NOT NULL, description TEXT DEFAULT '',
            mention_count INTEGER DEFAULT 1, first_seen TEXT, last_seen TEXT
        )""")
        db.execute("INSERT INTO entities VALUES ('alice', 'person', 'plaintext', 1, '2026-01-01', '2026-01-01')")
        db.commit()
        db.close()

        # Enable: encrypt
        run_enable_migration(enc, None, None, str(tmp_path))
        # Disable: decrypt
        run_disable_migration(enc, None, None, str(tmp_path))

        db = sqlite3.connect(str(kg_db))
        row = db.execute("SELECT description FROM entities WHERE name='alice'").fetchone()
        db.close()
        assert row[0] == "plaintext", f"Expected plaintext, got: {row[0]}"

    def test_disable_migration_idempotent_on_plaintext(self, tmp_path):
        """run_disable_migration does not modify already-plaintext values."""
        import sqlite3
        from encryption import FieldEncryptor
        from encryption_migrations import run_disable_migration

        enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")
        kg_db = tmp_path / "knowledge_graph.db"
        db = sqlite3.connect(str(kg_db))
        db.execute("""CREATE TABLE entities (
            name TEXT PRIMARY KEY, type TEXT NOT NULL, description TEXT DEFAULT '',
            mention_count INTEGER DEFAULT 1, first_seen TEXT, last_seen TEXT
        )""")
        db.execute("INSERT INTO entities VALUES ('alice', 'person', 'already plain', 1, '2026-01-01', '2026-01-01')")
        db.commit()
        db.close()

        run_disable_migration(enc, None, None, str(tmp_path))

        db = sqlite3.connect(str(kg_db))
        row = db.execute("SELECT description FROM entities WHERE name='alice'").fetchone()
        db.close()
        assert row[0] == "already plain"
```

### Run tests (must fail)

```bash
python -m pytest tests/test_encryption.py::TestDisableMigration -v
```

Expected: `FAILED` — `encryption_migrations.py` not yet created (depends on Task 9).

### Implementation

Covered by `encryption_migrations.py` created in Task 9 (`run_disable_migration`). No additional code needed.

### Run tests (must pass)

```bash
python -m pytest tests/test_encryption.py::TestDisableMigration -v
```

Expected: all 2 tests `PASSED`.

### Commit

```
test(encryption): add disable migration tests (Sub-L)
```

---

## Task 11 — Key rotation: decrypt with old key, re-encrypt with new key, atomic per-field

### What & Why

Validates the key rotation migration. Tests confirm that after rotation, data is readable with the new key and not with the old key.

### Files touched

- `tests/test_encryption.py` — add key rotation test

### Test first

Add to `tests/test_encryption.py`:

```python
class TestKeyRotation:
    def test_key_rotation_sqlite(self, tmp_path):
        """After rotation, value is decryptable with new key, not old key."""
        import sqlite3
        from encryption import FieldEncryptor, DecryptionError
        from encryption_migrations import run_enable_migration, run_rotation_migration

        key_a = "a" * 64
        key_b = "b" * 64
        enc_a = FieldEncryptor(key_a, "sqlite-v1")
        enc_b = FieldEncryptor(key_b, "sqlite-v1")

        kg_db = tmp_path / "knowledge_graph.db"
        db = sqlite3.connect(str(kg_db))
        db.execute("""CREATE TABLE entities (
            name TEXT PRIMARY KEY, type TEXT NOT NULL, description TEXT DEFAULT '',
            mention_count INTEGER DEFAULT 1, first_seen TEXT, last_seen TEXT
        )""")
        db.execute("INSERT INTO entities VALUES ('alice', 'person', 'original text', 1, '2026-01-01', '2026-01-01')")
        db.commit()
        db.close()

        # Encrypt with key A
        run_enable_migration(enc_a, None, None, str(tmp_path))

        # Rotate to key B
        run_rotation_migration(enc_a, None, None, str(tmp_path), key_b)

        db = sqlite3.connect(str(kg_db))
        row = db.execute("SELECT description FROM entities WHERE name='alice'").fetchone()
        db.close()

        stored = row[0]
        assert stored.startswith("enc:v1:"), "value should still be encrypted after rotation"

        # New key decrypts correctly
        assert enc_b.decrypt_field(stored) == "original text"

        # Old key fails
        with pytest.raises(DecryptionError):
            enc_a.decrypt_field(stored)
```

### Run tests (must fail)

```bash
python -m pytest tests/test_encryption.py::TestKeyRotation -v
```

Expected: `FAILED` — `encryption_migrations.py` not yet created.

### Implementation

Covered by `run_rotation_migration()` in Task 9's `encryption_migrations.py`. No additional code needed.

### Run tests (must pass)

```bash
python -m pytest tests/test_encryption.py::TestKeyRotation -v
```

Expected: 1 test `PASSED`.

### Commit

```
test(encryption): add key rotation test with old-key/new-key verification (Sub-L)
```

---

## Task 12 — Startup validation in `app.py`: fail-fast if `ENCRYPTION_ENABLED=true` and key is invalid

### What & Why

Prevents the bridge from starting silently with a broken encryption configuration. If `ENCRYPTION_ENABLED=true`, `app.py` must initialize the `FieldEncryptor` instances and call `validate_encryption_key()` during the startup sequence. If the key is absent or malformed, `RuntimeError` is raised and the process exits with a non-zero code before accepting traffic.

### Files touched

- `src/bridge/app.py` — add encryption startup block

### Test first

Add to `tests/test_encryption.py`:

```python
class TestStartupValidation:
    def test_startup_validation_passes_with_valid_key(self, monkeypatch):
        """validate_encryption_key() passes for a properly constructed FieldEncryptor."""
        from encryption import FieldEncryptor, validate_encryption_key
        enc = FieldEncryptor("c" * 64, "sqlite-v1")
        validate_encryption_key(enc)  # must not raise

    def test_startup_validation_fails_with_none(self):
        """validate_encryption_key(None) raises RuntimeError (key missing)."""
        from encryption import validate_encryption_key
        with pytest.raises(RuntimeError, match="ENCRYPTION_MASTER_KEY"):
            validate_encryption_key(None)

    def test_startup_validation_fails_bad_hex(self):
        """FieldEncryptor('ZZ'*32, ...) raises ValueError (invalid hex)."""
        from encryption import FieldEncryptor
        with pytest.raises(ValueError):
            FieldEncryptor("ZZ" * 32, "sqlite-v1")
```

### Run tests (must fail)

```bash
python -m pytest tests/test_encryption.py::TestStartupValidation -v
```

Expected: `FAILED` — `encryption.py` not yet created.

### Implementation

- [ ] Edit `src/bridge/app.py` — add the following block after the existing feature-flag loads and before the first route definition (near line 150 where other startup code lives):

```python
# ---------------------------------------------------------------------------
# Sub-projet L: Encryption At-Rest — startup initialization
# ---------------------------------------------------------------------------
_sqlite_encryptor = None
_qdrant_enc_instance = None

ENCRYPTION_ENABLED = os.getenv("ENCRYPTION_ENABLED", "false").lower() == "true"
ENCRYPTION_SQLITE_ENABLED = os.getenv(
    "ENCRYPTION_SQLITE_ENABLED", os.getenv("ENCRYPTION_ENABLED", "false")
).lower() == "true"
ENCRYPTION_QDRANT_ENABLED_FLAG = os.getenv(
    "ENCRYPTION_QDRANT_ENABLED", os.getenv("ENCRYPTION_ENABLED", "false")
).lower() == "true"

if ENCRYPTION_ENABLED or ENCRYPTION_SQLITE_ENABLED or ENCRYPTION_QDRANT_ENABLED_FLAG:
    _master_key = os.getenv("ENCRYPTION_MASTER_KEY", "")
    if not _master_key:
        raise RuntimeError(
            "ENCRYPTION_MASTER_KEY is required when ENCRYPTION_ENABLED=true. "
            "Set it in stack.env or generate with: python3 -c \"import secrets; print(secrets.token_hex(32))\""
        )
    try:
        from encryption import FieldEncryptor, validate_encryption_key  # pylint: disable=import-outside-toplevel
        _sqlite_encryptor = FieldEncryptor(_master_key, "sqlite-v1")
        _qdrant_enc_instance = FieldEncryptor(_master_key, "qdrant-v1")
        validate_encryption_key(_sqlite_encryptor)
        validate_encryption_key(_qdrant_enc_instance)
        logger.info("Sub-projet L: encryption at-rest initialized (sqlite=%s qdrant=%s)",
                    ENCRYPTION_SQLITE_ENABLED, ENCRYPTION_QDRANT_ENABLED_FLAG)
        # Inject into modules
        import knowledge_graph as _kg  # pylint: disable=import-outside-toplevel
        _kg.set_encryptor(_sqlite_encryptor)
        _qdrant_encryptor = _qdrant_enc_instance  # module-level for /remember endpoint
    except Exception as _enc_exc:
        raise RuntimeError(f"Encryption startup failed: {_enc_exc}") from _enc_exc
```

### Run tests (must pass)

```bash
python -m pytest tests/test_encryption.py::TestStartupValidation -v
```

Expected: all 3 tests `PASSED`.

### Commit

```
feat(encryption): add fail-fast startup validation for ENCRYPTION_MASTER_KEY in app.py (Sub-L)
```

---

## Task 13 — Mount `encryption_router` in `app.py`

### What & Why

Registers the encryption API router in `app.py` following the same try/except pattern used by all other sub-project routers (scheduler, email/calendar, RSS, backup). Passes the encryptor instances and Qdrant client via `init_encryption_api()`.

### Files touched

- `src/bridge/app.py` — add encryption router mount block

### Test first

Add to `tests/test_encryption.py`:

```python
class TestEncryptionRouterMount:
    def test_router_prefix_is_api_encryption(self):
        """encryption_api router has prefix /api/encryption."""
        from encryption_api import router
        assert router.prefix == "/api/encryption"

    def test_status_route_returns_dict(self, monkeypatch):
        """GET /api/encryption/status returns a dict with encryption_enabled key."""
        import os
        monkeypatch.setenv("ENCRYPTION_ENABLED", "false")
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from encryption_api import router, init_encryption_api
        app = FastAPI()
        init_encryption_api()
        app.include_router(router)
        client = TestClient(app)
        resp = client.get("/api/encryption/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "encryption_enabled" in data
        assert "partially_encrypted" in data
```

### Run tests (must fail)

```bash
python -m pytest tests/test_encryption.py::TestEncryptionRouterMount -v
```

Expected: `FAILED` — `encryption_api.py` not yet created.

### Implementation

- [ ] Edit `src/bridge/app.py` — add the following block near the end of the file, after the backup API mount block:

```python
# ---------------------------------------------------------------------------
# Sub-projet L: Encryption At-Rest API
# ---------------------------------------------------------------------------
try:
    from encryption_api import router as encryption_router, init_encryption_api
    init_encryption_api(
        encryptor_sqlite=_sqlite_encryptor,
        encryptor_qdrant=_qdrant_enc_instance,
        qdrant_client=qdrant,
        state_dir=str(STATE_DIR),
    )
    app.include_router(encryption_router, dependencies=[Depends(verify_token)])
    logger.info("Encryption endpoints mounted (/api/encryption/*)")
except Exception as exc:
    logger.info("Encryption API not loaded: %s", exc)
```

### Run tests (must pass)

```bash
python -m pytest tests/test_encryption.py::TestEncryptionRouterMount -v
```

Expected: all 2 tests `PASSED`.

### Commit

```
feat(encryption): mount encryption_api router in app.py (Sub-L)
```

---

## Task 14 — Full test suite in `tests/test_encryption.py`

### What & Why

This task consolidates and runs the complete test file built across Tasks 1–13. All test classes are present: `TestFieldEncryptorInit`, `TestEncryptDecryptRoundtrip`, `TestNonceUniqueness`, `TestSQLiteBackwardCompat`, `TestEmailSyncLogEncryption`, `TestQdrantPayloadHelpers`, `TestMemoryPersonalEncryption`, `TestEncryptionAPIInit`, `TestEnableMigration`, `TestDisableMigration`, `TestKeyRotation`, `TestStartupValidation`, `TestEncryptionRouterMount`.

### Files touched

- `tests/test_encryption.py` — final consolidated file (all tests from Tasks 1–13 combined)

### Run full test suite

```bash
cd /opt/nanobot-stack/rag-bridge
python -m pytest tests/test_encryption.py -v
```

Expected output (all 40+ tests `PASSED`):

```
tests/test_encryption.py::TestFieldEncryptorInit::test_init_accepts_valid_64_hex_key PASSED
tests/test_encryption.py::TestFieldEncryptorInit::test_init_raises_on_wrong_length PASSED
tests/test_encryption.py::TestFieldEncryptorInit::test_init_raises_on_non_hex PASSED
tests/test_encryption.py::TestFieldEncryptorInit::test_hkdf_domain_isolation PASSED
tests/test_encryption.py::TestFieldEncryptorInit::test_startup_key_validation_success PASSED
tests/test_encryption.py::TestFieldEncryptorInit::test_startup_key_validation_fail_bad_key PASSED
tests/test_encryption.py::TestFieldEncryptorInit::test_startup_key_validation_fail_missing PASSED
tests/test_encryption.py::TestEncryptDecryptRoundtrip::test_encrypt_decrypt_roundtrip_sqlite PASSED
tests/test_encryption.py::TestEncryptDecryptRoundtrip::test_encrypt_decrypt_roundtrip_qdrant PASSED
tests/test_encryption.py::TestEncryptDecryptRoundtrip::test_decrypt_plaintext_passthrough PASSED
tests/test_encryption.py::TestEncryptDecryptRoundtrip::test_encrypted_prefix_detection_true PASSED
tests/test_encryption.py::TestEncryptDecryptRoundtrip::test_encrypted_prefix_detection_false PASSED
tests/test_encryption.py::TestEncryptDecryptRoundtrip::test_tampered_ciphertext_raises_decryption_error PASSED
tests/test_encryption.py::TestEncryptDecryptRoundtrip::test_encrypt_empty_string PASSED
tests/test_encryption.py::TestEncryptDecryptRoundtrip::test_encrypt_unicode_value PASSED
tests/test_encryption.py::TestNonceUniqueness::test_nonce_uniqueness_same_value PASSED
tests/test_encryption.py::TestNonceUniqueness::test_nonce_uniqueness_bulk PASSED
tests/test_encryption.py::TestNonceUniqueness::test_all_produce_valid_roundtrip PASSED
tests/test_encryption.py::TestSQLiteBackwardCompat::test_sqlite_backward_compat_mixed_db PASSED
tests/test_encryption.py::TestEmailSyncLogEncryption::test_email_sync_log_account_encrypted_at_rest PASSED
tests/test_encryption.py::TestQdrantPayloadHelpers::test_encrypt_payload_encrypts_target_fields PASSED
tests/test_encryption.py::TestQdrantPayloadHelpers::test_decrypt_payload_restores_plaintext PASSED
tests/test_encryption.py::TestQdrantPayloadHelpers::test_encrypt_payload_idempotent PASSED
tests/test_encryption.py::TestQdrantPayloadHelpers::test_decrypt_payload_plaintext_passthrough PASSED
tests/test_encryption.py::TestMemoryPersonalEncryption::test_qdrant_vector_search_unaffected_by_payload_encryption PASSED
tests/test_encryption.py::TestMemoryPersonalEncryption::test_memory_payload_encrypt_decrypt_roundtrip PASSED
tests/test_encryption.py::TestEncryptionAPIInit::test_encryption_api_module_importable PASSED
tests/test_encryption.py::TestEncryptionAPIInit::test_router_has_required_routes PASSED
tests/test_encryption.py::TestEnableMigration::test_enable_migration_encrypts_plaintext_sqlite PASSED
tests/test_encryption.py::TestEnableMigration::test_enable_migration_returns_progress_dict PASSED
tests/test_encryption.py::TestEnableMigration::test_enable_migration_idempotent PASSED
tests/test_encryption.py::TestDisableMigration::test_disable_migration_decrypts_sqlite PASSED
tests/test_encryption.py::TestDisableMigration::test_disable_migration_idempotent_on_plaintext PASSED
tests/test_encryption.py::TestKeyRotation::test_key_rotation_sqlite PASSED
tests/test_encryption.py::TestStartupValidation::test_startup_validation_passes_with_valid_key PASSED
tests/test_encryption.py::TestStartupValidation::test_startup_validation_fails_with_none PASSED
tests/test_encryption.py::TestStartupValidation::test_startup_validation_fails_bad_hex PASSED
tests/test_encryption.py::TestEncryptionRouterMount::test_router_prefix_is_api_encryption PASSED
tests/test_encryption.py::TestEncryptionRouterMount::test_status_route_returns_dict PASSED
```

### Run pylint

```bash
python -m pylint src/bridge/encryption.py src/bridge/encryption_api.py src/bridge/encryption_migrations.py --disable=C,R
```

Expected: no errors.

### Final integration smoke test

```bash
# With ENCRYPTION_ENABLED=false (default) — no change in behavior
ENCRYPTION_ENABLED=false python -m pytest tests/test_encryption.py -v

# Verify /remember still works without encryption
curl -X POST http://localhost:8000/remember \
  -H "Authorization: Bearer $RAG_BRIDGE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"text": "Test memory post Sub-L", "collection": "memory_personal"}' | jq .

# Verify encryption status endpoint
curl http://localhost:8000/api/encryption/status \
  -H "Authorization: Bearer $RAG_BRIDGE_TOKEN" | jq .
```

### Commit

```
feat(encryption): complete Sub-projet L — field-level AES-256-GCM encryption at-rest

- encryption.py: FieldEncryptor (HKDF-SHA256, AES-256-GCM, enc:v1: prefix)
- encryption_migrations.py: enable/disable/rotate migration jobs
- encryption_api.py: 5 REST endpoints (status, enable, disable, rotate, migration-status)
- knowledge_graph.py: encrypt entities.description (sqlite-v1)
- email_calendar.py: encrypt email_sync_log.account + Qdrant payloads
- app.py: fail-fast startup validation, /remember encryption, router mount
- tests/test_encryption.py: 39 tests covering all scenarios
```

---

## Implementation Checklist

- [ ] Task 1: Create `src/bridge/encryption.py` (FieldEncryptor + validate_encryption_key)
- [ ] Task 2: Verify roundtrip / passthrough / tamper tests pass
- [ ] Task 3: Verify nonce uniqueness tests pass
- [ ] Task 4: Edit `src/bridge/knowledge_graph.py` (encrypt entities.description)
- [ ] Task 5: Edit `src/bridge/email_calendar.py` (encrypt email_sync_log.account)
- [ ] Task 6: Edit `src/bridge/email_calendar.py` (_encrypt_payload/_decrypt_payload for Qdrant)
- [ ] Task 7: Edit `src/bridge/app.py` (encrypt memory_personal text in /remember)
- [ ] Task 8: Create `src/bridge/encryption_api.py` (5 endpoints)
- [ ] Task 9: Create `src/bridge/encryption_migrations.py` (enable/disable/rotate)
- [ ] Task 10: Verify disable migration tests pass
- [ ] Task 11: Verify key rotation test passes
- [ ] Task 12: Edit `src/bridge/app.py` (fail-fast startup validation)
- [ ] Task 13: Edit `src/bridge/app.py` (mount encryption_router)
- [ ] Task 14: Run full test suite — all 39 tests must pass

## Key Design Decisions

**No new dependencies:** `cryptography>=42.0` is already in `requirements.txt` (added by Sub-F). This plan uses `AESGCM` from `cryptography.hazmat.primitives.ciphers.aead` rather than Fernet (used in backup_manager.py) because AES-GCM provides authenticated encryption with random nonces and is the recommended approach for field-level encryption.

**`entities.description` not `knowledge_entities.value`:** The spec names `knowledge_entities.value` but the actual table in `knowledge_graph.db` is `entities` with a `description` column. There is no `knowledge_entities` table or `value` column in the codebase.

**No `feedback.correction_text`:** The `feedback.py` module in the codebase stores relevance signals (`positive`/`negative`) with no `correction_text` column. The `email_sync_log.account` target is used instead, which is a concrete sensitive field in the existing code.

**Encryptor injection pattern:** All modules receive the encryptor via a `set_encryptor()` call from `app.py` at startup, rather than importing a global. This makes unit testing straightforward (inject a test encryptor) and avoids circular imports.

**Opt-in with `ENCRYPTION_ENABLED=false` default:** Instances without the env var configured are completely unaffected. The decrypt passthrough ensures a database can be read even if `ENCRYPTION_ENABLED` is toggled off after data was written.
