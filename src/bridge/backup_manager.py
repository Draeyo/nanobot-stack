"""BackupManager — automated backup pipeline for nanobot-stack.

Handles:
- Qdrant collection snapshots (via REST API)
- SQLite file copies
- stack.env copy
- .tar.gz archive creation
- Optional Fernet AES-256 encryption
- Optional S3-compatible upload
- Retention policy (keep last N backups)
- backup_log persistence in scheduler.db
"""
from __future__ import annotations

import logging
import os
import pathlib
import shutil
import sqlite3
import tarfile
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

try:
    import boto3 as boto3  # optional S3 dependency
except ImportError:  # pragma: no cover
    boto3 = None  # type: ignore[assignment]

logger = logging.getLogger("rag-bridge.backup_manager")

_SENTINEL = object()


def _env_bool(key: str, default: bool = False) -> bool:
    val = os.getenv(key, "").strip().lower()
    if val in ("1", "true", "yes"):
        return True
    if val in ("0", "false", "no", ""):
        return default
    return default


class BackupManager:
    """Full backup pipeline for nanobot-stack data."""

    def __init__(
        self,
        state_dir: str | pathlib.Path,
        qdrant_url: str,
        settings: dict[str, Any] | None = None,
    ) -> None:
        self.state_dir = pathlib.Path(state_dir)
        self.qdrant_url = qdrant_url.rstrip("/")

        cfg = settings or {}

        self.enabled: bool = cfg.get(
            "BACKUP_ENABLED", _env_bool("BACKUP_ENABLED", False)
        )
        self.local_path = pathlib.Path(
            cfg.get("BACKUP_LOCAL_PATH", os.getenv("BACKUP_LOCAL_PATH", "/opt/nanobot-stack/backups"))
        )
        self.retention_count: int = int(
            cfg.get("BACKUP_RETENTION_COUNT", os.getenv("BACKUP_RETENTION_COUNT", "7"))
        )
        self.encryption_key: str | None = cfg.get(
            "BACKUP_ENCRYPTION_KEY", os.getenv("BACKUP_ENCRYPTION_KEY", None)
        )
        self.s3_enabled: bool = cfg.get(
            "BACKUP_S3_ENABLED", _env_bool("BACKUP_S3_ENABLED", False)
        )
        self.s3_endpoint: str | None = cfg.get(
            "BACKUP_S3_ENDPOINT", os.getenv("BACKUP_S3_ENDPOINT", None)
        )
        self.s3_bucket: str | None = cfg.get(
            "BACKUP_S3_BUCKET", os.getenv("BACKUP_S3_BUCKET", None)
        )
        self.s3_access_key: str | None = cfg.get(
            "BACKUP_S3_ACCESS_KEY", os.getenv("BACKUP_S3_ACCESS_KEY", None)
        )
        self.s3_secret_key: str | None = cfg.get(
            "BACKUP_S3_SECRET_KEY", os.getenv("BACKUP_S3_SECRET_KEY", None)
        )
        self.s3_prefix: str = cfg.get(
            "BACKUP_S3_PREFIX", os.getenv("BACKUP_S3_PREFIX", "nanobot-backups/")
        )
        self.stack_env_path = pathlib.Path(
            cfg.get("STACK_ENV_PATH", os.getenv("STACK_ENV_PATH", "/opt/nanobot-stack/stack.env"))
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _db_path(self) -> pathlib.Path:
        return self.state_dir / "scheduler.db"

    def _open_db(self) -> sqlite3.Connection:
        db = sqlite3.connect(str(self._db_path()))
        db.row_factory = sqlite3.Row
        return db

    def _timestamp(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")

    def _iso_now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # Qdrant snapshot
    # ------------------------------------------------------------------

    async def _snapshot_qdrant_collection(
        self, collection_name: str, tmp_dir: pathlib.Path
    ) -> pathlib.Path:
        """Create a Qdrant snapshot for one collection and download it."""
        async with httpx.AsyncClient(timeout=300) as client:
            # Trigger snapshot creation
            create_resp = await client.post(
                f"{self.qdrant_url}/collections/{collection_name}/snapshots"
            )
            create_resp.raise_for_status()
            snapshot_info = create_resp.json()

            # Snapshot name is returned in the result
            snapshot_name: str = snapshot_info.get("result", {}).get("name", "")
            if not snapshot_name:
                # Fallback: list snapshots and take the most recent
                list_resp = await client.get(
                    f"{self.qdrant_url}/collections/{collection_name}/snapshots"
                )
                list_resp.raise_for_status()
                snapshots = list_resp.json().get("result", [])
                if not snapshots:
                    raise RuntimeError(
                        f"No snapshot found for collection {collection_name}"
                    )
                snapshot_name = snapshots[-1]["name"]

            # Download the snapshot file
            download_url = (
                f"{self.qdrant_url}/collections/{collection_name}/snapshots/{snapshot_name}"
            )
            dest_file = tmp_dir / f"{collection_name}_{snapshot_name}"
            async with client.stream("GET", download_url) as stream_resp:
                stream_resp.raise_for_status()
                with open(dest_file, "wb") as fh:
                    async for chunk in stream_resp.aiter_bytes(chunk_size=65536):
                        fh.write(chunk)

        logger.info(
            "Qdrant snapshot downloaded: collection=%s size=%d bytes",
            collection_name,
            dest_file.stat().st_size,
        )
        return dest_file

    async def _snapshot_all_collections(self, tmp_dir: pathlib.Path) -> list[pathlib.Path]:
        """Fetch collection list from Qdrant and snapshot each one."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{self.qdrant_url}/collections")
            resp.raise_for_status()
            collections = [c["name"] for c in resp.json().get("result", {}).get("collections", [])]

        paths: list[pathlib.Path] = []
        for name in collections:
            try:
                p = await self._snapshot_qdrant_collection(name, tmp_dir)
                paths.append(p)
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning("Failed to snapshot collection %s: %s", name, exc)
        return paths

    # ------------------------------------------------------------------
    # SQLite backup
    # ------------------------------------------------------------------

    def _backup_sqlite_files(self, tmp_dir: pathlib.Path) -> list[pathlib.Path]:
        """Copy all .db files from state_dir into tmp_dir."""
        copied: list[pathlib.Path] = []
        for db_file in self.state_dir.glob("*.db"):
            dest = tmp_dir / db_file.name
            shutil.copy2(str(db_file), str(dest))
            copied.append(dest)
            logger.debug("Copied SQLite: %s", db_file.name)
        return copied

    # ------------------------------------------------------------------
    # stack.env backup
    # ------------------------------------------------------------------

    def _copy_stack_env(self, tmp_dir: pathlib.Path) -> pathlib.Path | None:
        """Copy stack.env if it exists."""
        if self.stack_env_path.exists():
            dest = tmp_dir / "stack.env"
            shutil.copy2(str(self.stack_env_path), str(dest))
            return dest
        return None

    # ------------------------------------------------------------------
    # Archive
    # ------------------------------------------------------------------

    def _create_archive(self, tmp_dir: pathlib.Path, archive_path: pathlib.Path) -> int:
        """Create a .tar.gz archive from all files in tmp_dir. Returns size_bytes."""
        with tarfile.open(str(archive_path), "w:gz") as tar:
            for item in tmp_dir.iterdir():
                tar.add(str(item), arcname=item.name)
        size = archive_path.stat().st_size
        logger.info("Archive created: %s (%d bytes)", archive_path.name, size)
        return size

    # ------------------------------------------------------------------
    # Encryption
    # ------------------------------------------------------------------

    def _encrypt_archive(self, archive_path: pathlib.Path) -> pathlib.Path:
        """Encrypt archive using Fernet. Returns path to .enc file."""
        from cryptography.fernet import Fernet  # pylint: disable=import-outside-toplevel

        key = (self.encryption_key or "").strip()
        fernet = Fernet(key.encode() if isinstance(key, str) else key)

        plaintext = archive_path.read_bytes()
        ciphertext = fernet.encrypt(plaintext)

        enc_path = archive_path.parent / (archive_path.name + ".enc")
        enc_path.write_bytes(ciphertext)
        archive_path.unlink()  # remove unencrypted copy
        logger.info("Archive encrypted: %s", enc_path.name)
        return enc_path

    def _decrypt_archive(self, archive_path: pathlib.Path) -> pathlib.Path:
        """Decrypt a .enc Fernet archive. Returns path to decrypted .tar.gz."""
        from cryptography.fernet import Fernet  # pylint: disable=import-outside-toplevel

        key = (self.encryption_key or "").strip()
        fernet = Fernet(key.encode() if isinstance(key, str) else key)

        ciphertext = archive_path.read_bytes()
        plaintext = fernet.decrypt(ciphertext)

        # strip .enc extension
        out_path = archive_path.with_suffix("")  # removes last suffix
        out_path.write_bytes(plaintext)
        logger.info("Archive decrypted: %s", out_path.name)
        return out_path

    # ------------------------------------------------------------------
    # S3 upload
    # ------------------------------------------------------------------

    async def _upload_to_s3(self, archive_path: pathlib.Path) -> str:
        """Upload archive to S3-compatible storage. Returns S3 key."""
        if boto3 is None:  # pragma: no cover
            raise RuntimeError("boto3 is not installed — cannot upload to S3")

        s3_key = f"{self.s3_prefix}{archive_path.name}"
        client = boto3.client(
            "s3",
            endpoint_url=self.s3_endpoint,
            aws_access_key_id=self.s3_access_key,
            aws_secret_access_key=self.s3_secret_key,
        )
        client.upload_file(str(archive_path), self.s3_bucket, s3_key)
        logger.info("Uploaded to S3: bucket=%s key=%s", self.s3_bucket, s3_key)
        return s3_key

    # ------------------------------------------------------------------
    # Retention
    # ------------------------------------------------------------------

    def _apply_retention(self, backup_dir: pathlib.Path) -> None:
        """Delete oldest archives beyond retention_count."""
        archives = sorted(
            list(backup_dir.glob("nanobot-backup-*")),
            key=lambda p: p.stat().st_mtime,
        )
        excess = len(archives) - self.retention_count
        if excess <= 0:
            return
        for old in archives[:excess]:
            try:
                old.unlink()
                logger.info("Retention: deleted old backup %s", old.name)
            except OSError as exc:
                logger.warning("Retention: could not delete %s: %s", old.name, exc)

    # ------------------------------------------------------------------
    # backup_log persistence
    # ------------------------------------------------------------------

    def _log_backup_start(self, backup_id: str, started_at: str) -> None:
        db = self._open_db()
        try:
            db.execute(
                """
                INSERT INTO backup_log
                    (id, started_at, status)
                VALUES (?, ?, 'running')
                """,
                (backup_id, started_at),
            )
            db.commit()
        finally:
            db.close()

    def _log_backup_finish(
        self,
        backup_id: str,
        *,
        completed_at: str,
        archive_path: str | None,
        archive_s3_key: str | None,
        size_bytes: int,
        collections_count: int,
        sqlite_files_count: int,
        encrypted: int,
        status: str,
        error_msg: str | None,
    ) -> None:
        db = self._open_db()
        try:
            db.execute(
                """
                UPDATE backup_log SET
                    completed_at=?,
                    archive_path=?,
                    archive_s3_key=?,
                    size_bytes=?,
                    collections_count=?,
                    sqlite_files_count=?,
                    encrypted=?,
                    status=?,
                    error_msg=?
                WHERE id=?
                """,
                (
                    completed_at,
                    archive_path,
                    archive_s3_key,
                    size_bytes,
                    collections_count,
                    sqlite_files_count,
                    encrypted,
                    status,
                    error_msg,
                    backup_id,
                ),
            )
            db.commit()
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Public API — run_backup
    # ------------------------------------------------------------------

    async def run_backup(self) -> dict:
        """Full backup pipeline. Returns status dict."""
        if not self.enabled:
            return {"status": "disabled"}

        backup_id = str(uuid.uuid4())
        started_at = self._iso_now()
        timestamp = self._timestamp()
        archive_name = f"nanobot-backup-{timestamp}.tar.gz"

        # Ensure backup_log table exists (best-effort)
        try:
            self._log_backup_start(backup_id, started_at)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Could not write backup_log start entry: %s", exc)

        self.local_path.mkdir(parents=True, exist_ok=True)
        archive_path: pathlib.Path | None = None
        s3_key: str | None = None
        size_bytes = 0
        collections_count = 0
        sqlite_files_count = 0
        encrypted = 0
        error_msg: str | None = None
        status = "error"

        with tempfile.TemporaryDirectory(prefix="nanobot-backup-") as tmp_str:
            tmp_dir = pathlib.Path(tmp_str)
            qdrant_subdir = tmp_dir / "qdrant_snapshots"
            qdrant_subdir.mkdir()
            sqlite_subdir = tmp_dir / "sqlite"
            sqlite_subdir.mkdir()

            try:
                # 1. Qdrant snapshots
                snapshot_paths = await self._snapshot_all_collections(qdrant_subdir)
                collections_count = len(snapshot_paths)

                # 2. SQLite files
                sqlite_paths = self._backup_sqlite_files(sqlite_subdir)
                sqlite_files_count = len(sqlite_paths)

                # 3. stack.env
                self._copy_stack_env(tmp_dir)

                # 4. Create archive in backup_dir
                raw_archive = self.local_path / archive_name
                size_bytes = self._create_archive(tmp_dir, raw_archive)
                archive_path = raw_archive

                # 5. Optional encryption
                if self.encryption_key:
                    archive_path = self._encrypt_archive(archive_path)
                    size_bytes = archive_path.stat().st_size
                    encrypted = 1

                # 6. Optional S3 upload
                if self.s3_enabled:
                    s3_key = await self._upload_to_s3(archive_path)

                # 7. Retention
                self._apply_retention(self.local_path)

                status = "success"
                logger.info(
                    "Backup completed: id=%s archive=%s size=%d",
                    backup_id,
                    archive_path.name,
                    size_bytes,
                )

            except Exception as exc:  # pylint: disable=broad-except
                error_msg = str(exc)
                logger.exception("Backup failed: %s", exc)

        # 8. Update backup_log
        completed_at = self._iso_now()
        try:
            self._log_backup_finish(
                backup_id,
                completed_at=completed_at,
                archive_path=str(archive_path) if archive_path else None,
                archive_s3_key=s3_key,
                size_bytes=size_bytes,
                collections_count=collections_count,
                sqlite_files_count=sqlite_files_count,
                encrypted=encrypted,
                status=status,
                error_msg=error_msg,
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Could not write backup_log finish entry: %s", exc)

        return {
            "status": status,
            "backup_id": backup_id,
            "archive_path": str(archive_path) if archive_path else None,
            "archive_s3_key": s3_key,
            "size_bytes": size_bytes,
            "collections_count": collections_count,
            "sqlite_files_count": sqlite_files_count,
            "encrypted": bool(encrypted),
            "error_msg": error_msg,
            "started_at": started_at,
            "completed_at": completed_at,
        }

    # ------------------------------------------------------------------
    # Public API — list / status / delete
    # ------------------------------------------------------------------

    def list_backups(self) -> list[dict]:
        """Return all backup_log records ordered by started_at DESC."""
        try:
            db = self._open_db()
            try:
                rows = db.execute(
                    "SELECT * FROM backup_log ORDER BY started_at DESC"
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                db.close()
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("list_backups failed: %s", exc)
            return []

    def get_status(self) -> dict:
        """Return last backup record and estimated next run time."""
        backups = self.list_backups()
        last = backups[0] if backups else None

        # Estimate next run via croniter if available
        next_run: str | None = None
        try:
            from croniter import croniter  # pylint: disable=import-outside-toplevel

            cron_expr = os.getenv("BACKUP_CRON", "0 3 * * *")
            it = croniter(cron_expr, datetime.now(timezone.utc))
            next_run = it.get_next(datetime).isoformat()
        except Exception:  # pylint: disable=broad-except
            pass

        return {
            "enabled": self.enabled,
            "last_backup": last,
            "next_run": next_run,
            "retention_count": self.retention_count,
            "s3_enabled": self.s3_enabled,
            "encryption_enabled": bool(self.encryption_key),
        }

    def delete_backup(self, backup_id: str) -> bool:
        """Delete archive file from disk and remove from backup_log."""
        try:
            db = self._open_db()
            try:
                row = db.execute(
                    "SELECT archive_path FROM backup_log WHERE id=?", (backup_id,)
                ).fetchone()
                if row is None:
                    return False

                archive_path_str: str | None = row["archive_path"]
                if archive_path_str:
                    p = pathlib.Path(archive_path_str)
                    if p.exists():
                        p.unlink()
                        logger.info("Deleted backup archive: %s", p.name)

                db.execute("DELETE FROM backup_log WHERE id=?", (backup_id,))
                db.commit()
                return True
            finally:
                db.close()
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("delete_backup failed: %s", exc)
            return False
