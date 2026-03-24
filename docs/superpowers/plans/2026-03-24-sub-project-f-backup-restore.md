# Sub-projet F — Backup & Restore Implementation Plan

> **Status: IMPLEMENTED** — See commit history for details.

**Goal:** Automated daily backup (Qdrant snapshots + SQLite) with optional AES-256 encryption and S3 storage

**Files implemented:**
- `migrations/015_backup_log.py`
- `src/bridge/backup_manager.py`
- `src/bridge/backup_api.py`
- `scripts/backup.sh`, `scripts/restore.sh`
- Modified: `src/bridge/scheduler_registry.py`
- `tests/test_backup_manager.py` — 11 tests passing
