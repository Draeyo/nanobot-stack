"""Tests for Sub-projet L — FieldEncryptor, migrations, API."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "bridge"))

from encryption import FieldEncryptor, DecryptionError, validate_encryption_key

MASTER_KEY = "a" * 64  # 64 hex chars = 32 bytes


class TestFieldEncryptorInit:
    def test_init_accepts_valid_64_hex_key(self):
        enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")
        assert enc is not None

    def test_init_raises_on_wrong_length(self):
        with pytest.raises(ValueError, match="64"):
            FieldEncryptor("tooshort", "sqlite-v1")

    def test_init_raises_on_non_hex(self):
        with pytest.raises(ValueError):
            FieldEncryptor("z" * 64, "sqlite-v1")

    def test_hkdf_domain_isolation(self):
        sqlite_enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")
        qdrant_enc = FieldEncryptor(MASTER_KEY, "qdrant-v1")
        assert sqlite_enc._derived_key != qdrant_enc._derived_key

    def test_startup_key_validation_success(self):
        enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")
        validate_encryption_key(enc)

    def test_startup_key_validation_fail_bad_key(self):
        with pytest.raises((ValueError, RuntimeError)):
            enc = FieldEncryptor("ZZZZ" * 16, "sqlite-v1")
            validate_encryption_key(enc)

    def test_startup_key_validation_fail_missing(self, monkeypatch):
        monkeypatch.delenv("ENCRYPTION_MASTER_KEY", raising=False)
        with pytest.raises(RuntimeError, match="ENCRYPTION_MASTER_KEY"):
            validate_encryption_key(None)


class TestEncryptDecryptRoundtrip:
    def test_encrypt_decrypt_roundtrip_sqlite(self):
        enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")
        original = "Alice works on Project Nanobot"
        encrypted = enc.encrypt_field(original)
        assert encrypted.startswith("enc:v1:")
        assert enc.decrypt_field(encrypted) == original

    def test_encrypt_decrypt_roundtrip_qdrant(self):
        enc = FieldEncryptor(MASTER_KEY, "qdrant-v1")
        original = "Réunion de planification Q2 2026"
        encrypted = enc.encrypt_field(original)
        assert encrypted.startswith("enc:v1:")
        assert enc.decrypt_field(encrypted) == original

    def test_decrypt_plaintext_passthrough(self):
        enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")
        plain = "unencrypted legacy value"
        assert enc.decrypt_field(plain) == plain

    def test_encrypted_prefix_detection_true(self):
        enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")
        assert enc.is_encrypted("enc:v1:somepayload") is True

    def test_encrypted_prefix_detection_false(self):
        enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")
        assert enc.is_encrypted("just plain text") is False

    def test_tampered_ciphertext_raises_decryption_error(self):
        enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")
        encrypted = enc.encrypt_field("sensitive value")
        prefix = "enc:v1:"
        b64 = encrypted[len(prefix):]
        tampered_b64 = b64[:-4] + ("A" if b64[-4] != "A" else "B") + b64[-3:]
        tampered = prefix + tampered_b64
        with pytest.raises(DecryptionError):
            enc.decrypt_field(tampered)

    def test_encrypt_empty_string(self):
        enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")
        encrypted = enc.encrypt_field("")
        assert enc.decrypt_field(encrypted) == ""

    def test_encrypt_unicode_value(self):
        enc = FieldEncryptor(MASTER_KEY, "qdrant-v1")
        original = "Réunion café — objet: présentation 🚀"
        assert enc.decrypt_field(enc.encrypt_field(original)) == original


class TestNonceUniqueness:
    def test_nonce_uniqueness_same_value(self):
        enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")
        val = "same plaintext value"
        ct1 = enc.encrypt_field(val)
        ct2 = enc.encrypt_field(val)
        assert ct1 != ct2

    def test_nonce_uniqueness_bulk(self):
        enc = FieldEncryptor(MASTER_KEY, "qdrant-v1")
        val = "nonce test"
        ciphertexts = {enc.encrypt_field(val) for _ in range(100)}
        assert len(ciphertexts) == 100

    def test_all_produce_valid_roundtrip(self):
        enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")
        val = "round-trip nonce test"
        for _ in range(50):
            assert enc.decrypt_field(enc.encrypt_field(val)) == val


class TestSQLiteBackwardCompat:
    def test_sqlite_backward_compat_mixed_db(self, tmp_path, monkeypatch):
        import sqlite3
        import importlib
        import knowledge_graph as kg

        monkeypatch.setenv("ENCRYPTION_SQLITE_ENABLED", "true")
        importlib.reload(kg)

        monkeypatch.setattr(kg, "DB_PATH", tmp_path / "knowledge_graph.db")
        monkeypatch.setattr(kg, "STATE_DIR", tmp_path)
        monkeypatch.setattr(kg, "KG_ENABLED", True)

        enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")
        kg.set_encryptor(enc)

        db = sqlite3.connect(str(tmp_path / "knowledge_graph.db"))
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("""CREATE TABLE IF NOT EXISTS entities (
            name TEXT PRIMARY KEY, type TEXT NOT NULL, description TEXT DEFAULT '',
            mention_count INTEGER DEFAULT 1, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL
        )""")
        encrypted_desc = enc.encrypt_field("encrypted desc")
        db.execute("INSERT INTO entities VALUES ('alice', 'person', ?, 1, '2026-01-01', '2026-01-01')", (encrypted_desc,))
        db.commit()
        db.close()

        result = kg.query_entity("alice")
        assert result["found"] is True
        assert result["entity"]["description"] == "encrypted desc"


class TestEmailSyncLogEncryption:
    def test_email_sync_log_account_encrypted_at_rest(self, tmp_path, monkeypatch):
        import sqlite3
        from email_calendar import EmailCalendarFetcher
        import email_calendar as ecm

        enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")
        monkeypatch.setattr(ecm, "STATE_DIR", tmp_path)
        monkeypatch.setenv("ENCRYPTION_SQLITE_ENABLED", "true")

        fetcher = EmailCalendarFetcher()
        fetcher.set_encryptor(enc)
        fetcher._db_path = str(tmp_path / "scheduler.db")
        fetcher._sqlite_enc_enabled = True

        db = sqlite3.connect(str(tmp_path / "scheduler.db"))
        db.execute("""CREATE TABLE IF NOT EXISTS email_sync_log (
            id TEXT PRIMARY KEY, account TEXT, last_synced TEXT, items_synced INTEGER, status TEXT
        )""")
        db.commit()
        db.close()

        fetcher._update_sync_log("user@example.com", "2026-03-24T10:00:00Z", 5, "ok")

        db = sqlite3.connect(str(tmp_path / "scheduler.db"))
        row = db.execute("SELECT account FROM email_sync_log").fetchone()
        db.close()
        assert row[0].startswith("enc:v1:"), "account should be stored encrypted"

        status = fetcher.get_sync_status()
        assert "user@example.com" in status


class TestQdrantPayloadHelpers:
    def test_encrypt_payload_encrypts_target_fields(self):
        from email_calendar import EmailCalendarFetcher
        enc = FieldEncryptor(MASTER_KEY, "qdrant-v1")
        fetcher = EmailCalendarFetcher()
        fetcher.set_encryptor(enc)
        fetcher._qdrant_enc_enabled = True

        payload = {
            "message_id": "abc123",
            "subject": "Meeting tomorrow",
            "sender": "boss@example.com",
            "snippet": "Let's sync at 10am",
            "date": "2026-03-24",
        }
        encrypted = fetcher._encrypt_payload(payload, ["subject", "snippet", "sender"])
        assert encrypted["subject"].startswith("enc:v1:")
        assert encrypted["snippet"].startswith("enc:v1:")
        assert encrypted["sender"].startswith("enc:v1:")
        assert encrypted["message_id"] == "abc123"

    def test_decrypt_payload_restores_plaintext(self):
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
        from email_calendar import EmailCalendarFetcher
        enc = FieldEncryptor(MASTER_KEY, "qdrant-v1")
        fetcher = EmailCalendarFetcher()
        fetcher.set_encryptor(enc)
        fetcher._qdrant_enc_enabled = True

        payload = {"subject": "Test", "sender": "x@y.com", "snippet": "body"}
        once = fetcher._encrypt_payload(payload, ["subject", "snippet", "sender"])
        twice = fetcher._encrypt_payload(once, ["subject", "snippet", "sender"])
        assert once["subject"] == twice["subject"]

    def test_decrypt_payload_plaintext_passthrough(self):
        from email_calendar import EmailCalendarFetcher
        enc = FieldEncryptor(MASTER_KEY, "qdrant-v1")
        fetcher = EmailCalendarFetcher()
        fetcher.set_encryptor(enc)

        payload = {"subject": "plain subject", "sender": "plain@email.com"}
        decrypted = fetcher._decrypt_payload(payload, ["subject", "sender"])
        assert decrypted["subject"] == "plain subject"


class TestMemoryPersonalEncryption:
    def test_qdrant_vector_search_unaffected_by_payload_encryption(self):
        enc = FieldEncryptor(MASTER_KEY, "qdrant-v1")
        plain_text = "Alice decided to use Python for the project"
        encrypted_text = enc.encrypt_field(plain_text)
        assert plain_text == enc.decrypt_field(encrypted_text)
        assert not plain_text.startswith("enc:v1:")
        assert encrypted_text.startswith("enc:v1:")

    def test_memory_payload_encrypt_decrypt_roundtrip(self):
        enc = FieldEncryptor(MASTER_KEY, "qdrant-v1")
        payload = {
            "text": "Alice works on nanobot daily",
            "subject": "work",
            "tags": ["work"],
            "source_name": "user",
        }
        stored_payload = payload.copy()
        stored_payload["text"] = enc.encrypt_field(payload["text"])
        assert stored_payload["text"].startswith("enc:v1:")
        retrieved_payload = stored_payload.copy()
        retrieved_payload["text"] = enc.decrypt_field(stored_payload["text"])
        assert retrieved_payload["text"] == "Alice works on nanobot daily"


class TestEncryptionAPIInit:
    def test_encryption_api_module_importable(self):
        from encryption_api import router
        from fastapi import APIRouter
        assert isinstance(router, APIRouter)

    def test_router_has_required_routes(self):
        from encryption_api import router
        paths = {route.path for route in router.routes}
        # Paths include the router prefix when accessed via router.routes
        assert any("status" in p for p in paths)
        assert any("enable" in p for p in paths)
        assert any("disable" in p for p in paths)
        assert any("rotate" in p for p in paths)
        assert any("migration-status" in p for p in paths)


class TestEnableMigration:
    def test_enable_migration_encrypts_plaintext_sqlite(self, tmp_path):
        import sqlite3
        from encryption_migrations import run_enable_migration

        enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")
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

        run_enable_migration(enc, None, None, str(tmp_path))

        db = sqlite3.connect(str(kg_db))
        rows = dict(db.execute("SELECT name, description FROM entities").fetchall())
        db.close()

        assert rows["alice"].startswith("enc:v1:")
        assert rows["bob"] == "enc:v1:already"

    def test_enable_migration_returns_progress_dict(self, tmp_path):
        from encryption_migrations import run_enable_migration
        enc = FieldEncryptor(MASTER_KEY, "sqlite-v1")
        progress = run_enable_migration(enc, None, None, str(tmp_path))
        assert "sqlite" in progress
        assert "qdrant" in progress

    def test_enable_migration_idempotent(self, tmp_path):
        import sqlite3
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
        run_enable_migration(enc, None, None, str(tmp_path))

        db = sqlite3.connect(str(kg_db))
        row = db.execute("SELECT description FROM entities WHERE name='alice'").fetchone()
        db.close()
        assert enc.decrypt_field(row[0]) == "desc"


class TestDisableMigration:
    def test_disable_migration_decrypts_sqlite(self, tmp_path):
        import sqlite3
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

        run_enable_migration(enc, None, None, str(tmp_path))
        run_disable_migration(enc, None, None, str(tmp_path))

        db = sqlite3.connect(str(kg_db))
        row = db.execute("SELECT description FROM entities WHERE name='alice'").fetchone()
        db.close()
        assert row[0] == "plaintext"

    def test_disable_migration_idempotent_on_plaintext(self, tmp_path):
        import sqlite3
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


class TestKeyRotation:
    def test_key_rotation_sqlite(self, tmp_path):
        import sqlite3
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

        run_enable_migration(enc_a, None, None, str(tmp_path))
        run_rotation_migration(enc_a, None, None, str(tmp_path), key_b)

        db = sqlite3.connect(str(kg_db))
        row = db.execute("SELECT description FROM entities WHERE name='alice'").fetchone()
        db.close()

        stored = row[0]
        assert stored.startswith("enc:v1:")
        assert enc_b.decrypt_field(stored) == "original text"

        with pytest.raises(DecryptionError):
            enc_a.decrypt_field(stored)


class TestStartupValidation:
    def test_startup_validation_passes_with_valid_key(self):
        from encryption import FieldEncryptor, validate_encryption_key
        enc = FieldEncryptor("c" * 64, "sqlite-v1")
        validate_encryption_key(enc)

    def test_startup_validation_fails_with_none(self):
        from encryption import validate_encryption_key
        with pytest.raises(RuntimeError, match="ENCRYPTION_MASTER_KEY"):
            validate_encryption_key(None)

    def test_startup_validation_fails_bad_hex(self):
        from encryption import FieldEncryptor
        with pytest.raises(ValueError):
            FieldEncryptor("ZZ" * 32, "sqlite-v1")


class TestEncryptionRouterMount:
    def test_router_prefix_is_api_encryption(self):
        from encryption_api import router
        assert router.prefix == "/api/encryption"

    def test_status_route_returns_dict(self, monkeypatch):
        monkeypatch.setenv("ENCRYPTION_ENABLED", "false")
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from encryption_api import router, init_encryption_api
        test_app = FastAPI()
        init_encryption_api()
        test_app.include_router(router)
        client = TestClient(test_app)
        resp = client.get("/api/encryption/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "encryption_enabled" in data
        assert "partially_encrypted" in data
