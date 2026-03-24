"""Tests for BackupManager."""
# pylint: disable=protected-access,wrong-import-position
from __future__ import annotations

import os
import sqlite3
import sys
import tarfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "bridge"))

from backup_manager import BackupManager  # noqa: E402  (after sys.path insert)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_backup_db(tmp_path: Path) -> Path:
    """Create a minimal scheduler.db with backup_log table."""
    db_path = tmp_path / "scheduler.db"
    db = sqlite3.connect(str(db_path))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""
        CREATE TABLE IF NOT EXISTS backup_log (
            id              TEXT PRIMARY KEY,
            started_at      TEXT NOT NULL,
            completed_at    TEXT,
            archive_path    TEXT,
            archive_s3_key  TEXT,
            size_bytes      INTEGER DEFAULT 0,
            collections_count INTEGER DEFAULT 0,
            sqlite_files_count INTEGER DEFAULT 0,
            encrypted       INTEGER DEFAULT 0,
            status          TEXT NOT NULL DEFAULT 'running',
            error_msg       TEXT
        )
    """)
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_backup_log_started_at ON backup_log(started_at DESC)"
    )
    db.commit()
    db.close()
    return db_path


def _make_manager(tmp_path: Path, **overrides) -> BackupManager:
    """Create a BackupManager pointed at tmp_path, with BACKUP_ENABLED=true."""
    _make_backup_db(tmp_path)
    settings = {
        "BACKUP_ENABLED": True,
        "BACKUP_LOCAL_PATH": str(tmp_path / "backups"),
        "BACKUP_RETENTION_COUNT": 7,
        **overrides,
    }
    mgr = BackupManager(
        state_dir=str(tmp_path),
        qdrant_url="http://qdrant-test:6333",
        settings=settings,
    )
    return mgr


def _gen_fernet_key() -> str:
    """Generate a valid Fernet key as a str."""
    from cryptography.fernet import Fernet  # noqa: PLC0415
    return Fernet.generate_key().decode()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSnapshotQdrantMock:
    @pytest.mark.asyncio
    async def test_snapshot_qdrant_mock(self, tmp_path):
        """Mock httpx POST + GET: verify snapshot file downloaded to tmp_dir."""
        mgr = _make_manager(tmp_path)
        snapshot_subdir = tmp_path / "snapshots"
        snapshot_subdir.mkdir()

        fake_snapshot_name = "my-collection-snapshot-1.snapshot"
        fake_content = b"fake-snapshot-data-xyz"

        # Build a fake streaming response for the download
        async def _aiter_bytes(**_kwargs):
            yield fake_content

        mock_stream_resp = MagicMock()
        mock_stream_resp.raise_for_status = MagicMock()
        mock_stream_resp.aiter_bytes = _aiter_bytes

        mock_post_resp = MagicMock()
        mock_post_resp.raise_for_status = MagicMock()
        mock_post_resp.json.return_value = {"result": {"name": fake_snapshot_name}}

        # Context manager for stream
        class _FakeStreamCtx:
            async def __aenter__(self):
                return mock_stream_resp
            async def __aexit__(self, *args):
                pass

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_post_resp)
        mock_client.stream = MagicMock(return_value=_FakeStreamCtx())

        class _FakeClientCtx:
            async def __aenter__(self):
                return mock_client
            async def __aexit__(self, *args):
                pass

        with patch("backup_manager.httpx.AsyncClient", return_value=_FakeClientCtx()):
            result_path = await mgr._snapshot_qdrant_collection("my-collection", snapshot_subdir)

        assert result_path.exists()
        assert result_path.read_bytes() == fake_content
        mock_client.post.assert_awaited_once()
        post_url = mock_client.post.call_args[0][0]
        assert "my-collection" in post_url
        assert "snapshots" in post_url


class TestBackupSqliteFiles:
    def test_backup_sqlite_files(self, tmp_path):
        """Creates fake .db files in state_dir, verify they're copied to tmp_dir."""
        mgr = _make_manager(tmp_path)

        # Create fake .db files in state_dir (tmp_path)
        (tmp_path / "scheduler.db").write_bytes(b"fake-scheduler-db")
        (tmp_path / "memory.db").write_bytes(b"fake-memory-db")
        (tmp_path / "not_a_db.txt").write_bytes(b"ignored")

        copy_dir = tmp_path / "copies"
        copy_dir.mkdir()

        copied = mgr._backup_sqlite_files(copy_dir)

        db_names = {p.name for p in copied}
        assert "scheduler.db" in db_names
        assert "memory.db" in db_names
        assert "not_a_db.txt" not in db_names
        for p in copied:
            assert p.exists()


class TestCreateArchive:
    def test_create_archive(self, tmp_path):
        """Verify .tar.gz created with size > 0."""
        mgr = _make_manager(tmp_path)

        source_dir = tmp_path / "source"
        source_dir.mkdir()
        (source_dir / "file1.db").write_bytes(b"content1" * 100)
        (source_dir / "file2.db").write_bytes(b"content2" * 100)

        archive_path = tmp_path / "test-archive.tar.gz"
        size = mgr._create_archive(source_dir, archive_path)

        assert archive_path.exists()
        assert size > 0
        assert size == archive_path.stat().st_size

        # Verify it's a valid tar.gz
        with tarfile.open(str(archive_path), "r:gz") as tar:
            names = tar.getnames()
        assert "file1.db" in names
        assert "file2.db" in names


class TestEncryptDecryptRoundtrip:
    def test_encrypt_decrypt_roundtrip(self, tmp_path):
        """Encrypt then decrypt: verify original content restored."""
        key = _gen_fernet_key()
        settings = {
            "BACKUP_ENABLED": True,
            "BACKUP_LOCAL_PATH": str(tmp_path / "backups"),
            "BACKUP_ENCRYPTION_KEY": key,
        }
        _make_backup_db(tmp_path)
        mgr = BackupManager(
            state_dir=str(tmp_path),
            qdrant_url="http://qdrant-test:6333",
            settings=settings,
        )

        original_content = b"This is secret backup data 1234567890"
        archive_path = tmp_path / "nanobot-backup-2026-01-01T03-00-00.tar.gz"
        archive_path.write_bytes(original_content)

        # Encrypt
        enc_path = mgr._encrypt_archive(archive_path)
        assert enc_path.exists()
        assert enc_path.name.endswith(".enc")
        assert not archive_path.exists()  # original deleted
        assert enc_path.read_bytes() != original_content

        # Decrypt
        dec_path = mgr._decrypt_archive(enc_path)
        assert dec_path.exists()
        assert dec_path.read_bytes() == original_content


class TestApplyRetention:
    def test_apply_retention(self, tmp_path):
        """Create 10 fake archives, retention=7, verify 3 oldest deleted."""
        settings = {
            "BACKUP_ENABLED": True,
            "BACKUP_LOCAL_PATH": str(tmp_path / "backups"),
            "BACKUP_RETENTION_COUNT": 7,
        }
        _make_backup_db(tmp_path)
        mgr = BackupManager(
            state_dir=str(tmp_path),
            qdrant_url="http://qdrant-test:6333",
            settings=settings,
        )

        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # Create 10 fake archives with staggered mtime
        import time
        archives = []
        base_time = time.time() - 1000
        for i in range(10):
            p = backup_dir / f"nanobot-backup-2026-01-{i+1:02d}T03-00-00.tar.gz"
            p.write_bytes(b"x")
            os.utime(str(p), (base_time + i * 60, base_time + i * 60))
            archives.append(p)

        mgr._apply_retention(backup_dir)

        remaining = list(backup_dir.glob("nanobot-backup-*"))
        assert len(remaining) == 7

        # The 3 oldest (i=0,1,2) should be deleted
        for i in range(3):
            assert not archives[i].exists(), f"Archive {i} should have been deleted"
        # The 7 newest should remain
        for i in range(3, 10):
            assert archives[i].exists(), f"Archive {i} should still exist"


class TestRunBackupDisabled:
    @pytest.mark.asyncio
    async def test_run_backup_disabled(self, tmp_path):
        """BACKUP_ENABLED=false → returns {'status': 'disabled'}."""
        _make_backup_db(tmp_path)
        mgr = BackupManager(
            state_dir=str(tmp_path),
            qdrant_url="http://qdrant-test:6333",
            settings={
                "BACKUP_ENABLED": False,
                "BACKUP_LOCAL_PATH": str(tmp_path / "backups"),
            },
        )
        result = await mgr.run_backup()
        assert result == {"status": "disabled"}


class TestRunBackupFullMock:
    @pytest.mark.asyncio
    async def test_run_backup_full_mock(self, tmp_path):
        """Mock all external steps, verify backup_log entry created with status='success'."""
        mgr = _make_manager(tmp_path)
        mgr.local_path.mkdir(parents=True, exist_ok=True)

        # Mock _snapshot_all_collections → empty list (no Qdrant needed)
        fake_snapshot_paths: list[Path] = []

        # Create a real .db file so SQLite backup finds something
        # NOTE: do NOT overwrite scheduler.db — it holds the backup_log table
        (tmp_path / "memory.db").write_bytes(b"fake-db-data" * 50)

        with patch.object(
            mgr, "_snapshot_all_collections", new=AsyncMock(return_value=fake_snapshot_paths)
        ):
            result = await mgr.run_backup()

        assert result["status"] == "success"
        assert result["backup_id"] is not None

        # Verify backup_log entry
        backups = mgr.list_backups()
        assert len(backups) == 1
        entry = backups[0]
        assert entry["status"] == "success"
        assert entry["id"] == result["backup_id"]
        assert entry["completed_at"] is not None
        assert entry["sqlite_files_count"] >= 1


class TestS3UploadMock:
    @pytest.mark.asyncio
    async def test_s3_upload_mock(self, tmp_path):
        """Mock boto3: verify upload called with correct bucket/key."""
        settings = {
            "BACKUP_ENABLED": True,
            "BACKUP_LOCAL_PATH": str(tmp_path / "backups"),
            "BACKUP_RETENTION_COUNT": 7,
            "BACKUP_S3_ENABLED": True,
            "BACKUP_S3_ENDPOINT": "https://s3.example.com",
            "BACKUP_S3_BUCKET": "my-bucket",
            "BACKUP_S3_ACCESS_KEY": "AKIAIOSFODNN7EXAMPLE",
            "BACKUP_S3_SECRET_KEY": "wJalrXUtnFEMI/K7MDENG",
            "BACKUP_S3_PREFIX": "nanobot-backups/",
        }
        _make_backup_db(tmp_path)
        mgr = BackupManager(
            state_dir=str(tmp_path),
            qdrant_url="http://qdrant-test:6333",
            settings=settings,
        )

        # Create a fake archive to upload
        archive = tmp_path / "nanobot-backup-2026-01-01T03-00-00.tar.gz"
        archive.write_bytes(b"fake archive content")

        mock_boto3_client = MagicMock()
        mock_boto3_client.upload_file = MagicMock()

        with patch("backup_manager.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_boto3_client
            s3_key = await mgr._upload_to_s3(archive)

        mock_boto3.client.assert_called_once_with(
            "s3",
            endpoint_url="https://s3.example.com",
            aws_access_key_id="AKIAIOSFODNN7EXAMPLE",
            aws_secret_access_key="wJalrXUtnFEMI/K7MDENG",
        )
        mock_boto3_client.upload_file.assert_called_once()
        call_args = mock_boto3_client.upload_file.call_args[0]
        assert call_args[1] == "my-bucket"
        assert "nanobot-backups/" in call_args[2]
        assert s3_key.startswith("nanobot-backups/")


class TestListBackups:
    def test_list_backups(self, tmp_path):
        """Verify list_backups reads correctly from backup_log."""
        mgr = _make_manager(tmp_path)

        db = sqlite3.connect(str(tmp_path / "scheduler.db"))
        db.execute(
            "INSERT INTO backup_log (id, started_at, status, size_bytes) VALUES (?,?,?,?)",
            ("id-001", "2026-01-01T03:00:00+00:00", "success", 12345),
        )
        db.execute(
            "INSERT INTO backup_log (id, started_at, status, size_bytes) VALUES (?,?,?,?)",
            ("id-002", "2026-01-02T03:00:00+00:00", "error", 0),
        )
        db.commit()
        db.close()

        backups = mgr.list_backups()
        assert len(backups) == 2
        # Should be ordered DESC by started_at
        assert backups[0]["id"] == "id-002"
        assert backups[1]["id"] == "id-001"
        assert backups[1]["size_bytes"] == 12345


class TestDeleteBackup:
    def test_delete_backup(self, tmp_path):
        """Verify file deleted from disk and removed from backup_log."""
        mgr = _make_manager(tmp_path)

        # Create a fake archive file
        archive_file = tmp_path / "backups" / "nanobot-backup-2026-01-01T03-00-00.tar.gz"
        archive_file.parent.mkdir(parents=True, exist_ok=True)
        archive_file.write_bytes(b"fake archive")

        db = sqlite3.connect(str(tmp_path / "scheduler.db"))
        db.execute(
            "INSERT INTO backup_log (id, started_at, status, archive_path) VALUES (?,?,?,?)",
            ("del-id-001", "2026-01-01T03:00:00+00:00", "success", str(archive_file)),
        )
        db.commit()
        db.close()

        result = mgr.delete_backup("del-id-001")
        assert result is True

        # File should be gone
        assert not archive_file.exists()

        # Record should be gone from backup_log
        db = sqlite3.connect(str(tmp_path / "scheduler.db"))
        row = db.execute("SELECT id FROM backup_log WHERE id=?", ("del-id-001",)).fetchone()
        db.close()
        assert row is None

    def test_delete_backup_not_found(self, tmp_path):
        """Deleting a non-existent backup_id returns False."""
        mgr = _make_manager(tmp_path)
        result = mgr.delete_backup("nonexistent-id")
        assert result is False
