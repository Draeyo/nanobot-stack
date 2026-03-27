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
    failed = 0

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
                try:
                    new_value = encryptor.decrypt_field(value)
                except Exception as row_exc:  # pylint: disable=broad-except
                    logger.warning(
                        "Skipping undecryptable row rowid=%s in %s.%s: %s",
                        rowid, table, column, row_exc,
                    )
                    failed += 1
                    continue
            db.execute(f"UPDATE {table} SET {column} = ? WHERE rowid = ?", (new_value, rowid))
            processed += 1
        db.commit()
    finally:
        db.close()

    logger.info(
        "%s %s.%s: processed=%d skipped=%d failed=%d total=%d",
        direction, table, column, processed, skipped, failed, total,
    )
    return {"processed": processed, "total": total, "skipped": skipped, "failed": failed}


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
    except Exception:  # pylint: disable=broad-except
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
        except Exception as exc:  # pylint: disable=broad-except
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
                except Exception as exc:  # pylint: disable=broad-except
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
                result = _qdrant_encrypt_collection(
                    qdrant_client, collection, [field], encryptor_qdrant, "encrypt"
                )
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
                result = _qdrant_encrypt_collection(
                    qdrant_client, collection, [field], encryptor_qdrant, "decrypt"
                )
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
            failed = 0
            try:
                rows = db.execute(f"SELECT rowid, {column} FROM {table}").fetchall()
                for rowid, value in rows:
                    if not isinstance(value, str) or not old_encryptor_sqlite.is_encrypted(value):
                        continue
                    try:
                        plain = old_encryptor_sqlite.decrypt_field(value)
                    except Exception as row_exc:  # pylint: disable=broad-except
                        logger.warning(
                            "Skipping undecryptable row rowid=%s in %s.%s during rotation: %s",
                            rowid, table, column, row_exc,
                        )
                        failed += 1
                        continue
                    new_value = new_enc_sqlite.encrypt_field(plain)
                    db.execute(f"UPDATE {table} SET {column} = ? WHERE rowid = ?", (new_value, rowid))
                    processed += 1
                db.commit()
            except sqlite3.OperationalError:
                pass
            finally:
                db.close()
            progress["sqlite"][f"{table}.{column}"] = {"processed": processed, "failed": failed}

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
                    except Exception:  # pylint: disable=broad-except
                        break
                    failed = 0
                    for point in results:
                        payload = dict(point.payload or {})
                        val = payload.get(field)
                        if not isinstance(val, str) or not old_encryptor_qdrant.is_encrypted(val):
                            continue
                        try:
                            plain = old_encryptor_qdrant.decrypt_field(val)
                        except Exception as point_exc:  # pylint: disable=broad-except
                            logger.warning(
                                "Skipping undecryptable point %s in %s.%s during rotation: %s",
                                point.id, collection, field, point_exc,
                            )
                            failed += 1
                            continue
                        payload[field] = new_enc_qdrant.encrypt_field(plain)
                        try:
                            qdrant_client.set_payload(
                                collection_name=collection, payload=payload, points=[point.id]
                            )
                            processed += 1
                        except Exception:  # pylint: disable=broad-except
                            pass
                    if offset is None:
                        break
                progress["qdrant"][f"{collection}.{field}"] = {"processed": processed, "failed": failed}

    return progress
