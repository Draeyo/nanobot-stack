# Sub-projet E — Ingestion Documents Locaux Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Watch a local folder and ingest PDF/MD/TXT/DOCX files into the docs_reference Qdrant collection with semantic chunking, dedup by SHA256 hash, and PII filtering

**Architecture:** LocalDocIngestor handles chunking and Qdrant upsert for each format. LocalDocWatcher uses watchdog to monitor a directory and call ingestor on file events. SQLite table docs_ingestion_log tracks indexed files by hash for dedup. PII filter applied before storage.

**Tech Stack:** pypdf>=3.0, python-docx>=1.0, watchdog>=3.0, Qdrant (existing), PII filter (existing), FastAPI (existing)

---

## Migration number check

Existing migrations: 008, 010, 011, 012, 013, 015. Slot **014** is free — use `migrations/016_local_docs.py`.

> `pypdf>=5.0` and `python-docx>=1.1` are already present in `requirements.txt`. Only `watchdog>=3.0` needs to be added.

---

## Task 1 — Migration 014: `docs_ingestion_log` table

### Test first

**File:** `tests/test_migration_014.py`

```python
"""Tests for migration 014 — docs_ingestion_log table."""
import os
import sqlite3
import tempfile
import pathlib
import pytest

os.environ.setdefault("RAG_STATE_DIR", tempfile.mkdtemp())

import importlib
import sys

def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "migration_014",
        pathlib.Path(__file__).parent.parent / "migrations" / "016_local_docs.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_check_returns_false_before_migration(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    mod = _load_migration()
    assert mod.check({}) is False


def test_migrate_creates_table(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    mod = _load_migration()
    mod.migrate({})
    db = sqlite3.connect(str(tmp_path / "scheduler.db"))
    tables = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='docs_ingestion_log'"
    ).fetchall()
    db.close()
    assert len(tables) == 1


def test_migrate_creates_indexes(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    mod = _load_migration()
    mod.migrate({})
    db = sqlite3.connect(str(tmp_path / "scheduler.db"))
    indexes = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }
    db.close()
    assert "idx_docs_log_status" in indexes
    assert "idx_docs_log_file_type" in indexes
    assert "idx_docs_log_last_indexed" in indexes


def test_check_returns_true_after_migration(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    mod = _load_migration()
    mod.migrate({})
    assert mod.check({}) is True


def test_migrate_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_STATE_DIR", str(tmp_path))
    mod = _load_migration()
    mod.migrate({})
    mod.migrate({})  # second call must not raise
    assert mod.check({}) is True
```

**Run (red):**
```bash
cd /opt/nanobot-stack/rag-bridge
python -m pytest tests/test_migration_014.py -v 2>&1 | head -30
```

### Implementation

**File:** `migrations/016_local_docs.py`

```python
"""016_local_docs — docs_ingestion_log table."""
from __future__ import annotations

import logging
import os
import pathlib
import sqlite3

VERSION = 16

logger = logging.getLogger("migration.v14")
STATE_DIR = pathlib.Path(os.getenv("RAG_STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))


def check(_ctx: dict) -> bool:
    db_path = STATE_DIR / "scheduler.db"
    if not db_path.exists():
        return False
    db = sqlite3.connect(str(db_path))
    try:
        tables = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='docs_ingestion_log'"
        ).fetchall()
        return len(tables) > 0
    finally:
        db.close()


def migrate(_ctx: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    db_path = STATE_DIR / "scheduler.db"
    db = sqlite3.connect(str(db_path))
    db.execute("PRAGMA journal_mode=WAL")
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS docs_ingestion_log (
                id            TEXT PRIMARY KEY,
                file_path     TEXT NOT NULL UNIQUE,
                file_hash     TEXT NOT NULL,
                file_type     TEXT NOT NULL,
                chunks_count  INTEGER NOT NULL DEFAULT 0,
                status        TEXT NOT NULL,
                error_message TEXT,
                last_indexed  TEXT NOT NULL,
                created_at    TEXT NOT NULL
            );
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_docs_log_status
            ON docs_ingestion_log(status);
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_docs_log_file_type
            ON docs_ingestion_log(file_type);
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_docs_log_last_indexed
            ON docs_ingestion_log(last_indexed);
        """)
        db.commit()
        logger.info("Migration 014: docs_ingestion_log table created at %s", db_path)
    finally:
        db.close()
```

**Run (green):**
```bash
python -m pytest tests/test_migration_014.py -v
```

**Commit:**
```
feat(migration): add 016_local_docs — docs_ingestion_log table with status/type/hash indexes
```

- [ ] Write test file `tests/test_migration_014.py`
- [ ] Run tests (expect red)
- [ ] Create `migrations/016_local_docs.py`
- [ ] Run tests (expect green)
- [ ] Commit

---

## Task 2 — `LocalDocIngestor` skeleton: `__init__`, `_hash_file()`, `_is_already_indexed()`, env var guard

### Test first

**File:** `tests/test_local_doc_ingestor.py` (initial skeleton)

```python
"""Tests for LocalDocIngestor — Sub-project E."""
from __future__ import annotations

import hashlib
import os
import pathlib
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures" / "docs"


@pytest.fixture()
def tmp_state(tmp_path):
    """Isolated state directory + env vars."""
    watch_path = tmp_path / "watched-docs"
    watch_path.mkdir()
    env = {
        "RAG_STATE_DIR": str(tmp_path),
        "LOCAL_DOCS_ENABLED": "true",
        "LOCAL_DOCS_WATCH_PATH": str(watch_path),
        "LOCAL_DOCS_CHUNK_SIZE": "512",
        "LOCAL_DOCS_CHUNK_OVERLAP": "50",
        "LOCAL_DOCS_FORMATS": "pdf,md,txt,docx",
    }
    with patch.dict(os.environ, env, clear=False):
        yield tmp_path, watch_path


@pytest.fixture()
def mock_qdrant():
    q = MagicMock()
    q.upsert = MagicMock(return_value=None)
    q.delete = MagicMock(return_value=None)
    return q


@pytest.fixture()
def ingestor(tmp_state, mock_qdrant):
    tmp_path, watch_path = tmp_state
    # Run migration so DB table exists
    import importlib.util, pathlib as _pl
    spec = importlib.util.spec_from_file_location(
        "migration_014",
        _pl.Path(__file__).parent.parent / "migrations" / "016_local_docs.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.migrate({})

    from local_doc_ingestor import LocalDocIngestor
    return LocalDocIngestor(state_dir=str(tmp_path), qdrant_client=mock_qdrant)


# ---------------------------------------------------------------------------
# Task 2: skeleton tests
# ---------------------------------------------------------------------------

def test_disabled_flag(tmp_state, mock_qdrant):
    """LOCAL_DOCS_ENABLED=false → ingest_file returns status='disabled', no Qdrant call."""
    tmp_path, watch_path = tmp_state
    with patch.dict(os.environ, {"LOCAL_DOCS_ENABLED": "false"}, clear=False):
        import importlib.util, pathlib as _pl
        spec = importlib.util.spec_from_file_location(
            "migration_014",
            _pl.Path(__file__).parent.parent / "migrations" / "016_local_docs.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.migrate({})
        from local_doc_ingestor import LocalDocIngestor
        ing = LocalDocIngestor(state_dir=str(tmp_path), qdrant_client=mock_qdrant)
        # Create a file in watch_path
        f = watch_path / "hello.txt"
        f.write_text("hello world")
        result = ing.ingest_file(str(f))
    assert result["status"] == "disabled"
    mock_qdrant.upsert.assert_not_called()


def test_compute_hash_stable(ingestor, tmp_state):
    """Two consecutive hash calls on the same file return the same SHA-256."""
    _, watch_path = tmp_state
    f = watch_path / "stable.txt"
    f.write_text("content for hashing")
    h1 = ingestor._hash_file(str(f))
    h2 = ingestor._hash_file(str(f))
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex


def test_compute_hash_changes(ingestor, tmp_state):
    """Hash changes after file content changes."""
    _, watch_path = tmp_state
    f = watch_path / "mutable.txt"
    f.write_text("original content")
    h1 = ingestor._hash_file(str(f))
    f.write_text("modified content")
    h2 = ingestor._hash_file(str(f))
    assert h1 != h2


def test_is_already_indexed_false_for_new_file(ingestor, tmp_state):
    """New file not yet in log returns False."""
    _, watch_path = tmp_state
    f = watch_path / "new.txt"
    f.write_text("brand new")
    result = ingestor._is_already_indexed(str(f), "deadbeef" * 8)
    assert result is False


def test_ingest_file_outside_watch_path_raises(ingestor, tmp_path):
    """File outside LOCAL_DOCS_WATCH_PATH raises ValueError or PermissionError."""
    outside = tmp_path / "outside.txt"
    outside.write_text("should not be ingested")
    with pytest.raises((ValueError, PermissionError)):
        ingestor.ingest_file(str(outside))
```

**Run (red):**
```bash
python -m pytest tests/test_local_doc_ingestor.py::test_disabled_flag \
  tests/test_local_doc_ingestor.py::test_compute_hash_stable \
  tests/test_local_doc_ingestor.py::test_compute_hash_changes \
  tests/test_local_doc_ingestor.py::test_is_already_indexed_false_for_new_file \
  tests/test_local_doc_ingestor.py::test_ingest_file_outside_watch_path_raises -v
```

### Implementation

**File:** `src/bridge/local_doc_ingestor.py` (skeleton)

```python
"""LocalDocIngestor — Sub-project E: local document ingestion pipeline."""
from __future__ import annotations

import hashlib
import logging
import os
import pathlib
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("rag-bridge.local_doc_ingestor")

DOCS_COLLECTION = "docs_reference"
MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB


@dataclass
class IngestResult:
    status: str          # 'indexed' | 'skipped' | 'error' | 'disabled'
    doc_id: str = ""
    file_path: str = ""
    file_type: str = ""
    chunks_count: int = 0
    title: str = ""
    tags: list[str] = field(default_factory=list)
    reason: str = ""
    error_message: str = ""

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "doc_id": self.doc_id,
            "file_path": self.file_path,
            "file_type": self.file_type,
            "chunks_count": self.chunks_count,
            "title": self.title,
            "tags": self.tags,
            "reason": self.reason,
            "error_message": self.error_message,
        }


class LocalDocIngestor:
    """Pipeline: detect format → extract text → chunk → PII filter → embed → Qdrant upsert."""

    def __init__(
        self,
        state_dir: str | pathlib.Path,
        qdrant_client: Any,
    ) -> None:
        self._state_dir = pathlib.Path(state_dir)
        self._qdrant = qdrant_client
        self._db_path = self._state_dir / "scheduler.db"

        self._enabled = os.getenv("LOCAL_DOCS_ENABLED", "false").lower() in ("1", "true", "yes")
        self._watch_path = pathlib.Path(
            os.getenv("LOCAL_DOCS_WATCH_PATH", "/opt/nanobot-stack/watched-docs/")
        ).resolve()
        self._chunk_size = int(os.getenv("LOCAL_DOCS_CHUNK_SIZE", "512"))
        self._chunk_overlap = int(os.getenv("LOCAL_DOCS_CHUNK_OVERLAP", "50"))
        self._formats = set(
            os.getenv("LOCAL_DOCS_FORMATS", "pdf,md,txt,docx").lower().split(",")
        )
        self._watch_path.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # Core helpers
    # ------------------------------------------------------------------

    def _hash_file(self, file_path: str) -> str:
        """Return SHA-256 hex digest of file binary content."""
        h = hashlib.sha256()
        with open(file_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    def _detect_format(self, file_path: str) -> str | None:
        ext = pathlib.Path(file_path).suffix.lower().lstrip(".")
        return ext if ext in {"pdf", "md", "txt", "docx"} else None

    def _is_already_indexed(self, file_path: str, file_hash: str) -> bool:
        """Return True if file_path exists in log with the same hash (no change)."""
        db = self._connect()
        try:
            row = db.execute(
                "SELECT file_hash FROM docs_ingestion_log WHERE file_path = ?",
                (file_path,),
            ).fetchone()
            if row is None:
                return False
            return row["file_hash"] == file_hash
        finally:
            db.close()

    def _assert_within_watch_path(self, file_path: str) -> None:
        resolved = pathlib.Path(file_path).resolve()
        if not str(resolved).startswith(str(self._watch_path)):
            raise PermissionError(
                f"Path '{resolved}' is outside the allowed watch path '{self._watch_path}'"
            )

    # ------------------------------------------------------------------
    # Public entrypoint (stub — filled in later tasks)
    # ------------------------------------------------------------------

    def ingest_file(self, file_path: str) -> dict:
        if not self._enabled:
            return IngestResult(status="disabled").as_dict()
        self._assert_within_watch_path(file_path)
        # Full implementation added in Task 7
        return IngestResult(status="skipped", reason="not_yet_implemented").as_dict()
```

**Run (green):**
```bash
python -m pytest tests/test_local_doc_ingestor.py::test_disabled_flag \
  tests/test_local_doc_ingestor.py::test_compute_hash_stable \
  tests/test_local_doc_ingestor.py::test_compute_hash_changes \
  tests/test_local_doc_ingestor.py::test_is_already_indexed_false_for_new_file \
  tests/test_local_doc_ingestor.py::test_ingest_file_outside_watch_path_raises -v
```

**Commit:**
```
feat(local-docs): add LocalDocIngestor skeleton with _hash_file, _is_already_indexed, env guard
```

- [ ] Add skeleton tests to `tests/test_local_doc_ingestor.py`
- [ ] Run tests (expect red)
- [ ] Create `src/bridge/local_doc_ingestor.py` with skeleton
- [ ] Run tests (expect green)
- [ ] Commit

---

## Task 3 — Plain text / Markdown ingestion: `_chunk_text()`, `_ingest_txt_md()`

### Test first

Add to `tests/test_local_doc_ingestor.py`:

```python
# ---------------------------------------------------------------------------
# Task 3: TXT / MD ingestion
# ---------------------------------------------------------------------------

def _make_txt(watch_path: pathlib.Path, name: str, content: str) -> pathlib.Path:
    f = watch_path / name
    f.write_text(content, encoding="utf-8")
    return f


def test_extract_text_txt(ingestor, tmp_state):
    """_extract_text returns identical content for plain text files."""
    _, watch_path = tmp_state
    content = "Hello world.\nThis is a test.\nThird line."
    f = _make_txt(watch_path, "sample.txt", content)
    text = ingestor._extract_text(str(f), "txt")
    assert text == content


def test_extract_text_md(ingestor, tmp_state):
    """_extract_text returns raw Markdown text (no HTML/stripped tags)."""
    _, watch_path = tmp_state
    content = "# Title\n\nSome **bold** text and a [link](http://example.com).\n"
    f = _make_txt(watch_path, "sample.md", content)
    text = ingestor._extract_text(str(f), "md")
    assert "Title" in text
    assert "bold" in text


def test_extract_title_md_h1(ingestor, tmp_state):
    """First '# ...' line becomes the title for Markdown files."""
    _, watch_path = tmp_state
    f = _make_txt(watch_path, "guide.md", "# My Guide\n\nSome text.")
    meta = ingestor._extract_metadata(str(f))
    assert meta["title"] == "My Guide"


def test_extract_title_fallback(ingestor, tmp_state):
    """File with no explicit title falls back to the filename stem."""
    _, watch_path = tmp_state
    f = _make_txt(watch_path, "readme.txt", "just some text")
    meta = ingestor._extract_metadata(str(f))
    assert meta["title"] == "readme"


def test_tags_from_path(ingestor, tmp_state):
    """Path segments and filename are tokenized into tags, stopwords removed."""
    _, watch_path = tmp_state
    subdir = watch_path / "ops"
    subdir.mkdir()
    f = subdir / "guide-deploy.txt"
    f.write_text("content")
    meta = ingestor._extract_metadata(str(f))
    assert "ops" in meta["tags"]
    assert "guide" in meta["tags"]
    assert "deploy" in meta["tags"]
    assert "txt" in meta["tags"]
    # French stopwords must not appear
    for stopword in ("de", "la", "le", "les", "un", "une", "du", "des"):
        assert stopword not in meta["tags"]


def test_chunk_basic(ingestor):
    """Long text produces multiple chunks each within size limit."""
    text = "Sentence number one. " * 200  # ~4200 chars ≈ 1050 tokens
    chunks = ingestor._chunk_text(text, "test title")
    assert len(chunks) > 1
    char_limit = ingestor._chunk_size * 4
    for c in chunks:
        assert len(c) <= char_limit * 1.5  # allow slight overshoot at sentence boundary


def test_chunk_overlap(ingestor):
    """Last N chars of chunk N appear at start of chunk N+1 (overlap)."""
    text = ". ".join(f"Sentence {i} with some padding words here there" for i in range(80))
    chunks = ingestor._chunk_text(text, "overlap test")
    if len(chunks) > 1:
        # The tail of chunk[0] should share tokens with the start of chunk[1]
        overlap_chars = ingestor._chunk_overlap * 4
        tail = chunks[0][-overlap_chars:]
        head = chunks[1][:overlap_chars * 2]
        # At least some content overlap should exist
        assert any(word in head for word in tail.split() if len(word) > 4)


def test_chunk_small_text(ingestor):
    """Text smaller than overlap threshold → exactly one chunk."""
    short = "Hello world."
    chunks = ingestor._chunk_text(short, "small")
    assert len(chunks) == 1
    assert chunks[0] == short
```

**Run (red):**
```bash
python -m pytest tests/test_local_doc_ingestor.py -k "txt or md or chunk or title or tag" -v
```

### Implementation

Add to `src/bridge/local_doc_ingestor.py`:

```python
    # ------------------------------------------------------------------
    # Text extraction
    # ------------------------------------------------------------------

    def _extract_text(self, file_path: str, fmt: str) -> str:
        p = pathlib.Path(file_path)
        if fmt == "txt":
            return p.read_text(encoding="utf-8", errors="replace")
        if fmt == "md":
            return p.read_text(encoding="utf-8")
        raise ValueError(f"_extract_text called with unsupported fmt={fmt!r}")

    # ------------------------------------------------------------------
    # Metadata extraction
    # ------------------------------------------------------------------

    _FR_STOPWORDS = frozenset(["de", "la", "le", "les", "un", "une", "du", "des"])

    def _extract_metadata(self, file_path: str) -> dict:
        p = pathlib.Path(file_path)
        fmt = self._detect_format(file_path) or "txt"
        title = self._extract_title(p, fmt)
        tags = self._extract_tags(p)
        return {
            "title": title,
            "tags": tags,
            "source_path": str(p.resolve()),
            "file_type": fmt,
        }

    def _extract_title(self, p: pathlib.Path, fmt: str) -> str:
        try:
            if fmt == "md":
                for line in p.read_text(encoding="utf-8").splitlines():
                    if line.startswith("# "):
                        return line[2:].strip()
            elif fmt == "txt":
                for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.strip():
                        return line.strip()[:80]
        except Exception:
            pass
        return p.stem

    def _extract_tags(self, p: pathlib.Path) -> list[str]:
        import re as _re
        try:
            rel = p.resolve().relative_to(self._watch_path)
        except ValueError:
            rel = p
        parts = list(rel.parts)
        tokens: list[str] = []
        for part in parts:
            tokens.extend(_re.split(r"[/\-_ ]", pathlib.Path(part).stem))
        # Add file_type as last tag
        ext = p.suffix.lower().lstrip(".")
        if ext:
            tokens.append(ext)
        seen: set[str] = set()
        result: list[str] = []
        for t in tokens:
            t = t.lower()
            if t and t not in self._FR_STOPWORDS and t not in seen:
                seen.add(t)
                result.append(t)
        return result

    # ------------------------------------------------------------------
    # Chunking
    # ------------------------------------------------------------------

    def _chunk_text(self, text: str, title: str) -> list[str]:
        """Sliding-window chunker on sentence boundaries."""
        overlap_chars = self._chunk_overlap * 4
        size_chars = self._chunk_size * 4

        # Short text → single chunk
        if len(text) <= overlap_chars:
            return [text]

        import re as _re
        sentences = _re.split(r"(?<=\.)\s+|(?<=\.\n)|\n\n", text)
        sentences = [s for s in sentences if s.strip()]

        chunks: list[str] = []
        current_parts: list[str] = []
        current_len = 0

        for sent in sentences:
            if current_len + len(sent) > size_chars and current_parts:
                chunk_text = " ".join(current_parts)
                chunks.append(chunk_text)
                # Overlap: keep last overlap_chars worth of content
                overlap_text = chunk_text[-overlap_chars:]
                current_parts = [overlap_text]
                current_len = len(overlap_text)
            current_parts.append(sent)
            current_len += len(sent)

        if current_parts:
            chunks.append(" ".join(current_parts))

        return chunks if chunks else [text]
```

**Run (green):**
```bash
python -m pytest tests/test_local_doc_ingestor.py -k "txt or md or chunk or title or tag" -v
```

**Commit:**
```
feat(local-docs): add _extract_text for TXT/MD, _chunk_text with overlap, _extract_metadata
```

- [ ] Add TXT/MD/chunk/metadata tests to `tests/test_local_doc_ingestor.py`
- [ ] Run tests (expect red)
- [ ] Implement `_extract_text`, `_chunk_text`, `_extract_metadata`, `_extract_title`, `_extract_tags` in `local_doc_ingestor.py`
- [ ] Run tests (expect green)
- [ ] Commit

---

## Task 4 — PDF ingestion: `_extract_text` for PDF via pypdf

### Test first

Create fixture `tests/fixtures/docs/sample.pdf` (a real 3-page PDF with known text). Since fixtures are binary, generate them once with a helper script or check them in. For tests, use a minimal programmatically-generated PDF if `reportlab` is available, or mock `pypdf.PdfReader`.

Add to `tests/test_local_doc_ingestor.py`:

```python
# ---------------------------------------------------------------------------
# Task 4: PDF ingestion
# ---------------------------------------------------------------------------

def test_detect_format_pdf(ingestor, tmp_state):
    """Extension .pdf → 'pdf'."""
    _, watch_path = tmp_state
    f = watch_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4")
    assert ingestor._detect_format(str(f)) == "pdf"


def test_detect_format_unsupported(ingestor, tmp_state):
    """Extension .xlsx → None."""
    _, watch_path = tmp_state
    f = watch_path / "sheet.xlsx"
    f.write_bytes(b"PK")
    assert ingestor._detect_format(str(f)) is None


def test_extract_text_pdf(ingestor, tmp_state, monkeypatch):
    """PDF extraction returns non-empty text using pypdf.PdfReader."""
    _, watch_path = tmp_state
    f = watch_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4 fake")

    # Mock pypdf so test does not need a real PDF binary
    mock_page = MagicMock()
    mock_page.extract_text.return_value = "Page one content. Known keyword here."
    mock_reader = MagicMock()
    mock_reader.pages = [mock_page, mock_page]
    mock_reader.metadata = {}

    with patch("local_doc_ingestor.pypdf") as mock_pypdf:
        mock_pypdf.PdfReader.return_value = mock_reader
        text = ingestor._extract_text(str(f), "pdf")

    assert "Known keyword here" in text
    assert len(text) > 0


def test_extract_title_pdf_metadata(ingestor, tmp_state, monkeypatch):
    """PDF title extracted from reader.metadata['/Title'] when present."""
    _, watch_path = tmp_state
    f = watch_path / "titled.pdf"
    f.write_bytes(b"%PDF-1.4 fake")

    mock_page = MagicMock()
    mock_page.extract_text.return_value = "Content text."
    mock_reader = MagicMock()
    mock_reader.pages = [mock_page]
    mock_reader.metadata = {"/Title": "Official PDF Title"}

    with patch("local_doc_ingestor.pypdf") as mock_pypdf:
        mock_pypdf.PdfReader.return_value = mock_reader
        meta = ingestor._extract_metadata(str(f))

    assert meta["title"] == "Official PDF Title"


def test_ingest_file_pdf(ingestor, tmp_state, mock_qdrant, monkeypatch):
    """Full PDF ingestion path — mock Qdrant and pypdf, verify upsert called."""
    _, watch_path = tmp_state
    f = watch_path / "report.pdf"
    f.write_bytes(b"%PDF-1.4 fake binary content for hashing purposes only")

    mock_page = MagicMock()
    mock_page.extract_text.return_value = "This is a detailed report. " * 40
    mock_reader = MagicMock()
    mock_reader.pages = [mock_page]
    mock_reader.metadata = {"/Title": "Annual Report"}

    fake_embedding = [0.1] * 1536

    with patch("local_doc_ingestor.pypdf") as mock_pypdf, \
         patch("local_doc_ingestor.litellm") as mock_litellm:
        mock_pypdf.PdfReader.return_value = mock_reader
        mock_litellm.embedding.return_value = {
            "data": [{"embedding": fake_embedding}]
        }
        result = ingestor.ingest_file(str(f))

    assert result["status"] == "indexed"
    assert result["file_type"] == "pdf"
    assert result["chunks_count"] >= 1
    assert mock_qdrant.upsert.called
```

**Run (red):**
```bash
python -m pytest tests/test_local_doc_ingestor.py -k "pdf" -v
```

### Implementation

Add PDF branch to `_extract_text` and `_extract_title` in `local_doc_ingestor.py`:

```python
# At module level — lazy import guard
try:
    import pypdf as pypdf  # type: ignore[import]
except ImportError:
    pypdf = None  # type: ignore[assignment]

# Inside LocalDocIngestor._extract_text, add:
        if fmt == "pdf":
            if pypdf is None:
                raise ImportError("pypdf is required for PDF ingestion")
            reader = pypdf.PdfReader(file_path)
            pages_text = []
            for page in reader.pages:
                t = page.extract_text() or ""
                pages_text.append(t)
            return " ".join(pages_text)

# Inside LocalDocIngestor._extract_title, add before the fallback return:
            if fmt == "pdf":
                if pypdf is None:
                    return p.stem
                reader = pypdf.PdfReader(str(p))
                meta = reader.metadata or {}
                title_val = meta.get("/Title") or meta.get("title") or ""
                if title_val:
                    return str(title_val).strip()
                # Fallback to first non-empty text line
                for page in reader.pages:
                    text = page.extract_text() or ""
                    for line in text.splitlines():
                        if line.strip():
                            return line.strip()[:80]
```

Also add `ingest_file` full dispatch (stub upgrade — more complete version in Task 7, but enough for `test_ingest_file_pdf` to pass with a partial implementation that at minimum calls `_extract_text`, `_chunk_text`, embeds, and upserts):

```python
    def ingest_file(self, file_path: str) -> dict:
        if not self._enabled:
            return IngestResult(status="disabled").as_dict()
        self._assert_within_watch_path(file_path)

        # File size guard
        size = pathlib.Path(file_path).stat().st_size
        if size > MAX_FILE_BYTES:
            msg = f"File too large: {size} bytes (max {MAX_FILE_BYTES})"
            self._log_error(file_path, msg)
            return IngestResult(status="error", file_path=file_path, error_message=msg).as_dict()

        fmt = self._detect_format(file_path)
        if fmt is None or fmt not in self._formats:
            return IngestResult(status="skipped", file_path=file_path, reason="unsupported_format").as_dict()

        file_hash = self._hash_file(file_path)
        if self._is_already_indexed(file_path, file_hash):
            self._update_log_skipped(file_path)
            return IngestResult(status="skipped", file_path=file_path, reason="same_hash").as_dict()

        # Check for modified file (different hash but known path)
        existing_doc_id = self._get_existing_doc_id(file_path)
        if existing_doc_id:
            self._delete_qdrant_chunks(existing_doc_id)

        try:
            text = self._extract_text(file_path, fmt)
        except Exception as exc:
            msg = f"Extraction error: {exc}"
            logger.exception("extract_text failed for %s", file_path)
            self._log_error(file_path, msg)
            return IngestResult(status="error", file_path=file_path, error_message=msg).as_dict()

        meta = self._extract_metadata(file_path)
        chunks = self._chunk_text(text, meta["title"])

        doc_id = existing_doc_id or str(uuid.uuid4())
        points = []
        for i, chunk in enumerate(chunks):
            from pii_filter import redact_for_ingest
            filtered_chunk, _ = redact_for_ingest(chunk)
            vector = self._embed(filtered_chunk)
            if vector is None:
                continue
            payload = {
                "source_path": str(pathlib.Path(file_path).resolve()),
                "file_type": fmt,
                "file_hash": file_hash,
                "chunk_index": i,
                "total_chunks": len(chunks),
                "title": meta["title"],
                "tags": meta["tags"],
                "ingested_at": self._now(),
                "doc_id": doc_id,
                "text": filtered_chunk,
            }
            point_id = f"{doc_id}_{i:04d}"
            from qdrant_client.models import PointStruct  # type: ignore[import]
            points.append(PointStruct(id=point_id, vector=vector, payload=payload))

        # Upsert in batches of 100
        for batch_start in range(0, len(points), 100):
            batch = points[batch_start: batch_start + 100]
            self._qdrant.upsert(collection_name=DOCS_COLLECTION, points=batch)

        self._upsert_log(doc_id, file_path, file_hash, fmt, len(chunks), "indexed")
        return IngestResult(
            status="indexed",
            doc_id=doc_id,
            file_path=file_path,
            file_type=fmt,
            chunks_count=len(chunks),
            title=meta["title"],
            tags=meta["tags"],
        ).as_dict()

    def _embed(self, text: str) -> list[float] | None:
        try:
            import litellm  # type: ignore[import]
            resp = litellm.embedding(model="text-embedding-3-small", input=[text])
            return resp["data"][0]["embedding"]
        except Exception as exc:
            logger.warning("Embedding failed: %s", exc)
            return None

    def _delete_qdrant_chunks(self, doc_id: str) -> None:
        try:
            from qdrant_client.models import Filter, FieldCondition, MatchValue, FilterSelector  # type: ignore[import]
            self._qdrant.delete(
                collection_name=DOCS_COLLECTION,
                points_selector=FilterSelector(
                    filter=Filter(must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))])
                ),
            )
        except Exception as exc:
            logger.warning("Qdrant delete failed for doc_id=%s: %s", doc_id, exc)

    def _get_existing_doc_id(self, file_path: str) -> str | None:
        db = self._connect()
        try:
            row = db.execute(
                "SELECT id FROM docs_ingestion_log WHERE file_path = ?", (file_path,)
            ).fetchone()
            return row["id"] if row else None
        finally:
            db.close()

    def _upsert_log(
        self, doc_id: str, file_path: str, file_hash: str,
        file_type: str, chunks_count: int, status: str, error_message: str = ""
    ) -> None:
        now = self._now()
        db = self._connect()
        try:
            existing = db.execute(
                "SELECT created_at FROM docs_ingestion_log WHERE id = ? OR file_path = ?",
                (doc_id, file_path),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            db.execute(
                """
                INSERT OR REPLACE INTO docs_ingestion_log
                  (id, file_path, file_hash, file_type, chunks_count, status, error_message, last_indexed, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (doc_id, file_path, file_hash, file_type, chunks_count,
                 status, error_message or None, now, created_at),
            )
            db.commit()
        finally:
            db.close()

    def _log_error(self, file_path: str, message: str) -> None:
        doc_id = self._get_existing_doc_id(file_path) or str(uuid.uuid4())
        fmt = self._detect_format(file_path) or "unknown"
        self._upsert_log(doc_id, file_path, "", fmt, 0, "error", message)

    def _update_log_skipped(self, file_path: str) -> None:
        now = self._now()
        db = self._connect()
        try:
            db.execute(
                "UPDATE docs_ingestion_log SET status='skipped', last_indexed=? WHERE file_path=?",
                (now, file_path),
            )
            db.commit()
        finally:
            db.close()
```

**Run (green):**
```bash
python -m pytest tests/test_local_doc_ingestor.py -k "pdf or detect_format" -v
```

**Commit:**
```
feat(local-docs): add PDF extraction via pypdf, full ingest_file dispatch pipeline
```

- [ ] Add PDF tests to `tests/test_local_doc_ingestor.py`
- [ ] Run tests (expect red)
- [ ] Implement PDF branch in `_extract_text` + `_extract_title` + full `ingest_file`
- [ ] Run tests (expect green)
- [ ] Commit

---

## Task 5 — DOCX ingestion: `_extract_text` for DOCX via python-docx

### Test first

Add to `tests/test_local_doc_ingestor.py`:

```python
# ---------------------------------------------------------------------------
# Task 5: DOCX ingestion
# ---------------------------------------------------------------------------

def test_extract_text_docx(ingestor, tmp_state, monkeypatch):
    """DOCX extraction returns paragraph text joined with newlines."""
    _, watch_path = tmp_state
    f = watch_path / "doc.docx"
    f.write_bytes(b"PK fake docx content")

    mock_para1 = MagicMock()
    mock_para1.text = "First paragraph content."
    mock_para2 = MagicMock()
    mock_para2.text = "Second paragraph here."
    mock_doc = MagicMock()
    mock_doc.paragraphs = [mock_para1, mock_para2]
    mock_doc.core_properties.title = ""

    with patch("local_doc_ingestor.docx") as mock_docx:
        mock_docx.Document.return_value = mock_doc
        text = ingestor._extract_text(str(f), "docx")

    assert "First paragraph content." in text
    assert "Second paragraph here." in text


def test_extract_title_docx_core_properties(ingestor, tmp_state, monkeypatch):
    """DOCX title extracted from doc.core_properties.title when set."""
    _, watch_path = tmp_state
    f = watch_path / "titled.docx"
    f.write_bytes(b"PK fake")

    mock_para = MagicMock()
    mock_para.text = "Body paragraph."
    mock_doc = MagicMock()
    mock_doc.paragraphs = [mock_para]
    mock_doc.core_properties.title = "DOCX Core Title"

    with patch("local_doc_ingestor.docx") as mock_docx:
        mock_docx.Document.return_value = mock_doc
        meta = ingestor._extract_metadata(str(f))

    assert meta["title"] == "DOCX Core Title"


def test_extract_title_docx_fallback(ingestor, tmp_state, monkeypatch):
    """DOCX without core title falls back to first non-empty paragraph."""
    _, watch_path = tmp_state
    f = watch_path / "notitled.docx"
    f.write_bytes(b"PK fake")

    mock_para = MagicMock()
    mock_para.text = "First paragraph as title fallback."
    mock_doc = MagicMock()
    mock_doc.paragraphs = [mock_para]
    mock_doc.core_properties.title = ""

    with patch("local_doc_ingestor.docx") as mock_docx:
        mock_docx.Document.return_value = mock_doc
        meta = ingestor._extract_metadata(str(f))

    assert "First paragraph" in meta["title"]
```

**Run (red):**
```bash
python -m pytest tests/test_local_doc_ingestor.py -k "docx" -v
```

### Implementation

At module level in `local_doc_ingestor.py`:

```python
try:
    import docx as docx  # type: ignore[import]  # python-docx
except ImportError:
    docx = None  # type: ignore[assignment]
```

Add DOCX branch to `_extract_text`:

```python
        if fmt == "docx":
            if docx is None:
                raise ImportError("python-docx is required for DOCX ingestion")
            document = docx.Document(file_path)
            return "\n".join(p.text for p in document.paragraphs)
```

Add DOCX branch to `_extract_title`:

```python
            if fmt == "docx":
                if docx is None:
                    return p.stem
                document = docx.Document(str(p))
                core_title = document.core_properties.title or ""
                if core_title.strip():
                    return core_title.strip()
                for para in document.paragraphs:
                    if para.text.strip():
                        return para.text.strip()[:80]
```

Also add `watchdog` to `requirements.txt`:

```
watchdog>=3.0
```

**Run (green):**
```bash
python -m pytest tests/test_local_doc_ingestor.py -k "docx" -v
```

**Commit:**
```
feat(local-docs): add DOCX extraction via python-docx + add watchdog to requirements.txt
```

- [ ] Add DOCX tests to `tests/test_local_doc_ingestor.py`
- [ ] Run tests (expect red)
- [ ] Implement DOCX branch in `_extract_text` and `_extract_title`
- [ ] Add `watchdog>=3.0` to `src/bridge/requirements.txt`
- [ ] Run tests (expect green)
- [ ] Commit

---

## Task 6 — Qdrant upsert: `_embed_and_upsert(chunks, metadata)` to `docs_reference`

> Note: The `_embed` and upsert-in-batches logic was introduced in Task 4 inside `ingest_file`. Task 6 extracts and hardens this into a dedicated `_embed_and_upsert` method so it can be unit-tested in isolation.

### Test first

Add to `tests/test_local_doc_ingestor.py`:

```python
# ---------------------------------------------------------------------------
# Task 6: _embed_and_upsert
# ---------------------------------------------------------------------------

def test_embed_and_upsert_calls_qdrant(ingestor, mock_qdrant, monkeypatch):
    """_embed_and_upsert calls qdrant.upsert with correct number of points."""
    chunks = ["chunk one content here", "chunk two content here", "chunk three"]
    metadata = {
        "doc_id": str(uuid.uuid4()),
        "source_path": "/tmp/watched/doc.txt",
        "file_type": "txt",
        "file_hash": "abc123",
        "title": "Test Doc",
        "tags": ["test", "doc"],
        "ingested_at": "2026-03-24T00:00:00Z",
    }
    fake_vec = [0.0] * 1536

    with patch("local_doc_ingestor.litellm") as mock_litellm:
        mock_litellm.embedding.return_value = {"data": [{"embedding": fake_vec}]}
        points_upserted = ingestor._embed_and_upsert(chunks, metadata)

    assert points_upserted == len(chunks)
    assert mock_qdrant.upsert.called
    call_args = mock_qdrant.upsert.call_args_list
    total_points = sum(len(c.kwargs.get("points", c.args[1] if len(c.args) > 1 else [])) for c in call_args)
    assert total_points == len(chunks)


def test_embed_and_upsert_batch_limit(ingestor, mock_qdrant, monkeypatch):
    """More than 100 chunks triggers multiple upsert calls (batch size=100)."""
    chunks = [f"chunk {i}" for i in range(150)]
    metadata = {
        "doc_id": str(uuid.uuid4()),
        "source_path": "/tmp/watched/large.txt",
        "file_type": "txt",
        "file_hash": "deadbeef",
        "title": "Large Doc",
        "tags": ["large"],
        "ingested_at": "2026-03-24T00:00:00Z",
    }
    fake_vec = [0.0] * 1536

    with patch("local_doc_ingestor.litellm") as mock_litellm:
        mock_litellm.embedding.return_value = {"data": [{"embedding": fake_vec}]}
        ingestor._embed_and_upsert(chunks, metadata)

    # Should have called upsert at least twice (100 + 50)
    assert mock_qdrant.upsert.call_count >= 2


def test_pii_filter_applied(ingestor, tmp_state, mock_qdrant, monkeypatch):
    """Chunks stored in Qdrant do not contain original PII values."""
    _, watch_path = tmp_state
    f = watch_path / "pii_doc.txt"
    f.write_text(
        "Contact us at jean.dupont@example.com or call +33 6 12 34 56 78 for support.",
        encoding="utf-8",
    )
    fake_vec = [0.0] * 1536
    captured_payloads: list[dict] = []

    original_upsert = mock_qdrant.upsert
    def capturing_upsert(collection_name, points):
        for pt in points:
            captured_payloads.append(pt.payload)
    mock_qdrant.upsert.side_effect = capturing_upsert

    with patch("local_doc_ingestor.litellm") as mock_litellm:
        mock_litellm.embedding.return_value = {"data": [{"embedding": fake_vec}]}
        result = ingestor.ingest_file(str(f))

    assert result["status"] == "indexed"
    for payload in captured_payloads:
        assert "jean.dupont@example.com" not in payload.get("text", "")
        assert "+33 6 12 34 56 78" not in payload.get("text", "")
```

**Run (red):**
```bash
python -m pytest tests/test_local_doc_ingestor.py -k "upsert or pii" -v
```

### Implementation

Refactor `ingest_file` to delegate embedding/upsert to a dedicated method. Add to `local_doc_ingestor.py`:

```python
    def _embed_and_upsert(self, chunks: list[str], metadata: dict) -> int:
        """Embed chunks with PII filter applied, upsert to Qdrant. Returns count of points upserted."""
        from pii_filter import redact_for_ingest
        try:
            from qdrant_client.models import PointStruct  # type: ignore[import]
        except ImportError as exc:
            logger.error("qdrant_client not available: %s", exc)
            return 0

        points = []
        doc_id = metadata["doc_id"]
        total = len(chunks)
        for i, chunk in enumerate(chunks):
            filtered_chunk, _ = redact_for_ingest(chunk)
            vector = self._embed(filtered_chunk)
            if vector is None:
                continue
            payload = {
                "source_path": metadata["source_path"],
                "file_type": metadata["file_type"],
                "file_hash": metadata["file_hash"],
                "chunk_index": i,
                "total_chunks": total,
                "title": metadata["title"],
                "tags": metadata["tags"],
                "ingested_at": metadata["ingested_at"],
                "doc_id": doc_id,
                "text": filtered_chunk,
            }
            point_id = f"{doc_id}_{i:04d}"
            points.append(PointStruct(id=point_id, vector=vector, payload=payload))

        upserted = 0
        for batch_start in range(0, len(points), 100):
            batch = points[batch_start: batch_start + 100]
            self._qdrant.upsert(collection_name=DOCS_COLLECTION, points=batch)
            upserted += len(batch)

        return upserted
```

Update `ingest_file` to call `_embed_and_upsert` instead of the inline logic.

**Run (green):**
```bash
python -m pytest tests/test_local_doc_ingestor.py -k "upsert or pii" -v
```

**Commit:**
```
feat(local-docs): extract _embed_and_upsert method, enforce PII filter on all chunks
```

- [ ] Add upsert/PII tests to `tests/test_local_doc_ingestor.py`
- [ ] Run tests (expect red)
- [ ] Extract `_embed_and_upsert` in `local_doc_ingestor.py`, wire PII filter
- [ ] Run tests (expect green)
- [ ] Commit

---

## Task 7 — `ingest_file` dispatch: dedup check, all formats, logging + `delete_document`

### Test first

Add to `tests/test_local_doc_ingestor.py`:

```python
# ---------------------------------------------------------------------------
# Task 7: full ingest_file + delete_document
# ---------------------------------------------------------------------------

def test_ingest_file_unsupported_format(ingestor, tmp_state, mock_qdrant):
    """Extension .xlsx → status='skipped', reason='unsupported_format', no Qdrant call."""
    _, watch_path = tmp_state
    f = watch_path / "sheet.xlsx"
    f.write_bytes(b"PK fake xlsx")
    result = ingestor.ingest_file(str(f))
    assert result["status"] == "skipped"
    assert result["reason"] == "unsupported_format"
    mock_qdrant.upsert.assert_not_called()


def test_dedup_same_hash(ingestor, tmp_state, mock_qdrant, monkeypatch):
    """Ingesting the same file twice → second call returns status='skipped', no second upsert."""
    _, watch_path = tmp_state
    f = watch_path / "dedup.txt"
    f.write_text("stable content for dedup test", encoding="utf-8")
    fake_vec = [0.0] * 1536

    with patch("local_doc_ingestor.litellm") as mock_litellm:
        mock_litellm.embedding.return_value = {"data": [{"embedding": fake_vec}]}
        result1 = ingestor.ingest_file(str(f))

    assert result1["status"] == "indexed"
    upsert_count_after_first = mock_qdrant.upsert.call_count

    with patch("local_doc_ingestor.litellm") as mock_litellm:
        mock_litellm.embedding.return_value = {"data": [{"embedding": fake_vec}]}
        result2 = ingestor.ingest_file(str(f))

    assert result2["status"] == "skipped"
    assert result2["reason"] == "same_hash"
    assert mock_qdrant.upsert.call_count == upsert_count_after_first  # no new upsert


def test_dedup_changed_hash(ingestor, tmp_state, mock_qdrant, monkeypatch):
    """Modified file → second ingest re-indexes and deletes old Qdrant chunks."""
    _, watch_path = tmp_state
    f = watch_path / "changing.txt"
    f.write_text("original content version one", encoding="utf-8")
    fake_vec = [0.0] * 1536

    with patch("local_doc_ingestor.litellm") as mock_litellm:
        mock_litellm.embedding.return_value = {"data": [{"embedding": fake_vec}]}
        result1 = ingestor.ingest_file(str(f))

    assert result1["status"] == "indexed"
    first_upsert_count = mock_qdrant.upsert.call_count

    f.write_text("completely different content version two plus more", encoding="utf-8")

    with patch("local_doc_ingestor.litellm") as mock_litellm:
        mock_litellm.embedding.return_value = {"data": [{"embedding": fake_vec}]}
        result2 = ingestor.ingest_file(str(f))

    assert result2["status"] == "indexed"
    assert mock_qdrant.upsert.call_count > first_upsert_count
    mock_qdrant.delete.assert_called()  # old chunks removed


def test_file_size_limit(ingestor, tmp_state, mock_qdrant, monkeypatch):
    """File > 50 MB → status='error', no Qdrant call."""
    _, watch_path = tmp_state
    f = watch_path / "huge.txt"
    f.write_text("x")  # actual small file

    # Mock stat to report oversized file
    mock_stat = MagicMock()
    mock_stat.st_size = 51 * 1024 * 1024
    with patch.object(pathlib.Path, "stat", return_value=mock_stat):
        result = ingestor.ingest_file(str(f))

    assert result["status"] == "error"
    mock_qdrant.upsert.assert_not_called()


def test_delete_document(ingestor, tmp_state, mock_qdrant, monkeypatch):
    """delete_document calls Qdrant delete with doc_id filter + marks log 'deleted'."""
    _, watch_path = tmp_state
    f = watch_path / "to_delete.txt"
    f.write_text("content to be deleted later")
    fake_vec = [0.0] * 1536

    with patch("local_doc_ingestor.litellm") as mock_litellm:
        mock_litellm.embedding.return_value = {"data": [{"embedding": fake_vec}]}
        result = ingestor.ingest_file(str(f))

    doc_id = result["doc_id"]
    deleted = ingestor.delete_document(doc_id)

    assert deleted is True
    mock_qdrant.delete.assert_called()
    # Verify log is marked deleted
    db = sqlite3.connect(str(ingestor._db_path))
    row = db.execute(
        "SELECT status FROM docs_ingestion_log WHERE id=?", (doc_id,)
    ).fetchone()
    db.close()
    assert row[0] == "deleted"


def test_delete_unknown_doc_id(ingestor):
    """Unknown doc_id → delete_document returns False."""
    result = ingestor.delete_document("00000000-0000-0000-0000-000000000000")
    assert result is False


def test_list_documents(ingestor, tmp_state, mock_qdrant, monkeypatch):
    """3 indexed + 1 deleted → list_documents returns 3 (non-deleted)."""
    _, watch_path = tmp_state
    fake_vec = [0.0] * 1536

    doc_ids = []
    for i in range(3):
        f = watch_path / f"file_{i}.txt"
        f.write_text(f"content {i} unique text here")
        with patch("local_doc_ingestor.litellm") as mock_litellm:
            mock_litellm.embedding.return_value = {"data": [{"embedding": fake_vec}]}
            r = ingestor.ingest_file(str(f))
        doc_ids.append(r["doc_id"])

    # Delete one
    ingestor.delete_document(doc_ids[0])

    docs = ingestor.list_documents()
    assert len(docs) == 2
    statuses = {d["status"] for d in docs}
    assert "deleted" not in statuses


def test_get_status_breakdown(ingestor, tmp_state, mock_qdrant, monkeypatch):
    """get_status breakdown contains correct counters per file type."""
    _, watch_path = tmp_state
    fake_vec = [0.0] * 1536

    txt_file = watch_path / "a.txt"
    txt_file.write_text("txt content here for testing")
    md_file = watch_path / "b.md"
    md_file.write_text("# Title\n\nmd content for testing")

    with patch("local_doc_ingestor.litellm") as mock_litellm:
        mock_litellm.embedding.return_value = {"data": [{"embedding": fake_vec}]}
        ingestor.ingest_file(str(txt_file))
        ingestor.ingest_file(str(md_file))

    status = ingestor.get_status()
    assert status["enabled"] is True
    assert status["total_files"] >= 2
    assert "txt" in status["breakdown"] or "md" in status["breakdown"]
```

**Run (red):**
```bash
python -m pytest tests/test_local_doc_ingestor.py -k "dedup or delete or list or status_breakdown or unsupported or size_limit" -v
```

### Implementation

Add to `local_doc_ingestor.py`:

```python
    def delete_document(self, doc_id: str) -> bool:
        db = self._connect()
        try:
            row = db.execute(
                "SELECT status FROM docs_ingestion_log WHERE id=?", (doc_id,)
            ).fetchone()
            if row is None or row["status"] == "deleted":
                return False
        finally:
            db.close()

        self._delete_qdrant_chunks(doc_id)
        now = self._now()
        db = self._connect()
        try:
            db.execute(
                "UPDATE docs_ingestion_log SET status='deleted', last_indexed=? WHERE id=?",
                (now, doc_id),
            )
            db.commit()
        finally:
            db.close()
        return True

    def list_documents(self, limit: int = 100, offset: int = 0,
                       file_type: str | None = None, status: str | None = None) -> list[dict]:
        db = self._connect()
        try:
            query = "SELECT * FROM docs_ingestion_log WHERE status != 'deleted'"
            params: list[Any] = []
            if file_type:
                query += " AND file_type=?"
                params.append(file_type)
            if status:
                query += " AND status=?"
                params.append(status)
            query += " ORDER BY last_indexed DESC LIMIT ? OFFSET ?"
            params += [limit, offset]
            rows = db.execute(query, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            db.close()

    def get_status(self) -> dict:
        db = self._connect()
        try:
            total_row = db.execute(
                "SELECT COUNT(*) FROM docs_ingestion_log WHERE status='indexed'"
            ).fetchone()
            total_files = total_row[0] if total_row else 0

            chunks_row = db.execute(
                "SELECT SUM(chunks_count) FROM docs_ingestion_log WHERE status='indexed'"
            ).fetchone()
            total_chunks = chunks_row[0] or 0

            last_row = db.execute(
                "SELECT MAX(last_indexed) FROM docs_ingestion_log WHERE status='indexed'"
            ).fetchone()
            last_indexed = last_row[0] if last_row else None

            type_rows = db.execute(
                "SELECT file_type, COUNT(*) as files, SUM(chunks_count) as chunks "
                "FROM docs_ingestion_log WHERE status='indexed' GROUP BY file_type"
            ).fetchall()
            breakdown = {
                r["file_type"]: {"files": r["files"], "chunks": r["chunks"] or 0}
                for r in type_rows
            }
        finally:
            db.close()

        return {
            "enabled": self._enabled,
            "watch_path": str(self._watch_path),
            "total_files": total_files,
            "total_chunks": total_chunks,
            "last_indexed": last_indexed,
            "breakdown": breakdown,
            "watcher_running": False,  # updated by LocalDocWatcher after start
        }

    def ingest_directory(self, path: str | None = None) -> list[dict]:
        if not self._enabled:
            return [{"status": "disabled"}]
        scan_path = pathlib.Path(path) if path else self._watch_path
        results = []
        for fp in scan_path.rglob("*"):
            if not fp.is_file():
                continue
            fmt = self._detect_format(str(fp))
            if fmt is None or fmt not in self._formats:
                continue
            try:
                r = self.ingest_file(str(fp))
                results.append(r)
            except Exception as exc:
                logger.warning("ingest_directory: error on %s: %s", fp, exc)
                results.append({"status": "error", "file_path": str(fp), "error_message": str(exc)})
        return results
```

**Run (green):**
```bash
python -m pytest tests/test_local_doc_ingestor.py -k "dedup or delete or list or status_breakdown or unsupported or size_limit" -v
```

**Commit:**
```
feat(local-docs): complete ingest_file dispatch, delete_document, list_documents, get_status, ingest_directory
```

- [ ] Add all Task 7 tests to `tests/test_local_doc_ingestor.py`
- [ ] Run tests (expect red)
- [ ] Implement `delete_document`, `list_documents`, `get_status`, `ingest_directory` in `local_doc_ingestor.py`
- [ ] Run tests (expect green)
- [ ] Commit

---

## Task 8 — `LocalDocWatcher` class: watchdog FileSystemEventHandler, anti-debounce 2s

### Test first

Add to `tests/test_local_doc_ingestor.py`:

```python
# ---------------------------------------------------------------------------
# Task 8: LocalDocWatcher
# ---------------------------------------------------------------------------

def test_watcher_ignores_temp_files(tmp_state, monkeypatch):
    """Watcher events on temp/hidden files do not call ingest_file."""
    from local_doc_ingestor import LocalDocWatcher
    _, watch_path = tmp_state

    mock_ingestor = MagicMock()
    watcher = LocalDocWatcher()

    # Simulate events on temp/hidden file names
    temp_files = [
        str(watch_path / ".gitignore"),
        str(watch_path / "partial.tmp"),
        str(watch_path / "download.part"),
        str(watch_path / "swap.swp"),
    ]
    for temp_file in temp_files:
        event = MagicMock()
        event.is_directory = False
        event.src_path = temp_file
        watcher._handle_event(event, mock_ingestor)

    mock_ingestor.ingest_file.assert_not_called()


def test_watcher_debounce(tmp_state, monkeypatch):
    """Two on_modified events within 2s on the same file → one ingest_file call."""
    import time
    from local_doc_ingestor import LocalDocWatcher
    _, watch_path = tmp_state

    mock_ingestor = MagicMock()
    mock_ingestor.ingest_file.return_value = {"status": "indexed"}

    watcher = LocalDocWatcher()
    f = str(watch_path / "rapid.txt")

    event = MagicMock()
    event.is_directory = False
    event.src_path = f

    # Two rapid events — both scheduled, only one should fire
    watcher._handle_event(event, mock_ingestor)
    watcher._handle_event(event, mock_ingestor)  # resets debounce timer

    # Wait for debounce to fire
    time.sleep(2.5)

    assert mock_ingestor.ingest_file.call_count == 1


def test_batch_ingestion_partial_errors(ingestor, tmp_state, mock_qdrant, monkeypatch):
    """ingest_directory: 2 valid + 1 corrupt file → 2 indexed, 1 error, no exception raised."""
    _, watch_path = tmp_state
    fake_vec = [0.0] * 1536

    (watch_path / "ok1.txt").write_text("valid content one here")
    (watch_path / "ok2.txt").write_text("valid content two here")
    (watch_path / "corrupt.pdf").write_bytes(b"not a real pdf")

    with patch("local_doc_ingestor.litellm") as mock_litellm, \
         patch("local_doc_ingestor.pypdf") as mock_pypdf:
        mock_litellm.embedding.return_value = {"data": [{"embedding": fake_vec}]}
        mock_pypdf.PdfReader.side_effect = Exception("corrupt PDF")
        results = ingestor.ingest_directory()

    statuses = [r["status"] for r in results]
    assert statuses.count("indexed") == 2
    assert statuses.count("error") == 1
```

**Run (red):**
```bash
python -m pytest tests/test_local_doc_ingestor.py -k "watcher or debounce or batch" -v
```

### Implementation

Add to `local_doc_ingestor.py`:

```python
class LocalDocWatcher:
    """Watchdog-based file system monitor. Calls LocalDocIngestor on file events."""

    # Temp/hidden file patterns to ignore
    _IGNORE_EXTENSIONS = frozenset([".tmp", ".part", ".swp"])

    def __init__(self) -> None:
        self._timers: dict[str, Any] = {}
        self._lock = __import__("threading").Lock()
        self._observer: Any = None
        self._running = False

    def _is_temp_file(self, path: str) -> bool:
        p = pathlib.Path(path)
        if p.name.startswith("."):
            return True
        if p.suffix.lower() in self._IGNORE_EXTENSIONS:
            return True
        return False

    def _handle_event(self, event: Any, ingestor: "LocalDocIngestor") -> None:
        if event.is_directory:
            return
        src_path = event.src_path
        if self._is_temp_file(src_path):
            return

        import threading

        def _fire():
            with self._lock:
                self._timers.pop(src_path, None)
            try:
                ingestor.ingest_file(src_path)
            except Exception as exc:
                logger.warning("Watcher ingest_file failed for %s: %s", src_path, exc)

        with self._lock:
            existing = self._timers.get(src_path)
            if existing is not None:
                existing.cancel()
            t = threading.Timer(2.0, _fire)
            self._timers[src_path] = t
            t.start()

    def start(self, path: str, ingestor: "LocalDocIngestor") -> None:
        try:
            from watchdog.observers import Observer  # type: ignore[import]
            from watchdog.events import FileSystemEventHandler  # type: ignore[import]
        except ImportError:
            logger.warning("watchdog not installed — LocalDocWatcher not started")
            return

        watcher_ref = self

        class _Handler(FileSystemEventHandler):
            def on_created(self, event):
                watcher_ref._handle_event(event, ingestor)

            def on_modified(self, event):
                watcher_ref._handle_event(event, ingestor)

        self._observer = Observer()
        self._observer.schedule(_Handler(), path, recursive=True)
        self._observer.daemon = True
        self._observer.start()
        self._running = True
        logger.info("LocalDocWatcher started on %s", path)

    def stop(self) -> None:
        if self._observer and self._running:
            self._observer.stop()
            self._observer.join()
            self._running = False
            logger.info("LocalDocWatcher stopped")

    @property
    def is_running(self) -> bool:
        return self._running
```

**Run (green):**
```bash
python -m pytest tests/test_local_doc_ingestor.py -k "watcher or debounce or batch" -v
```

**Commit:**
```
feat(local-docs): add LocalDocWatcher with watchdog, 2s debounce, temp-file ignore
```

- [ ] Add watcher/debounce/batch tests to `tests/test_local_doc_ingestor.py`
- [ ] Run tests (expect red)
- [ ] Implement `LocalDocWatcher` class in `local_doc_ingestor.py`
- [ ] Run tests (expect green)
- [ ] Commit

---

## Task 9 — `local_docs_api.py`: REST endpoints

### Test first

Add to `tests/test_local_doc_ingestor.py` (integration section, marked with `@pytest.mark.integration`):

```python
# ---------------------------------------------------------------------------
# Task 9: API integration tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_api_ingest_endpoint(tmp_state, mock_qdrant, monkeypatch):
    """POST /api/docs/ingest with valid file → HTTP 200, status='indexed'."""
    import importlib
    _, watch_path = tmp_state
    f = watch_path / "api_test.md"
    f.write_text("# API Test\n\nSome content for the API test endpoint.")
    fake_vec = [0.0] * 1536

    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    import importlib.util, pathlib as _pl
    spec = importlib.util.spec_from_file_location(
        "migration_014",
        _pl.Path(__file__).parent.parent / "migrations" / "016_local_docs.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.migrate({})

    from local_doc_ingestor import LocalDocIngestor
    from local_docs_api import router as docs_router, init_local_docs_api

    app_test = FastAPI()
    ing = LocalDocIngestor(state_dir=str(tmp_state[0]), qdrant_client=mock_qdrant)
    init_local_docs_api(ingestor=ing)
    app_test.include_router(docs_router)

    client = TestClient(app_test)

    with patch("local_doc_ingestor.litellm") as mock_litellm:
        mock_litellm.embedding.return_value = {"data": [{"embedding": fake_vec}]}
        resp = client.post("/api/docs/ingest", json={"file_path": str(f)})

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "indexed"
    assert data["file_type"] == "md"


@pytest.mark.integration
def test_api_ingest_outside_path(tmp_state, mock_qdrant):
    """POST /api/docs/ingest with path outside watch dir → HTTP 422."""
    _, watch_path = tmp_state
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    import importlib.util, pathlib as _pl
    spec = importlib.util.spec_from_file_location(
        "migration_014",
        _pl.Path(__file__).parent.parent / "migrations" / "016_local_docs.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.migrate({})

    from local_doc_ingestor import LocalDocIngestor
    from local_docs_api import router as docs_router, init_local_docs_api

    app_test = FastAPI()
    ing = LocalDocIngestor(state_dir=str(tmp_state[0]), qdrant_client=mock_qdrant)
    init_local_docs_api(ingestor=ing)
    app_test.include_router(docs_router)

    client = TestClient(app_test)
    resp = client.post("/api/docs/ingest", json={"file_path": "/etc/passwd"})
    assert resp.status_code == 422


@pytest.mark.integration
def test_api_list_and_delete(tmp_state, mock_qdrant, monkeypatch):
    """Ingest → list → delete → file absent from list."""
    _, watch_path = tmp_state
    f = watch_path / "list_test.txt"
    f.write_text("content for list and delete test")
    fake_vec = [0.0] * 1536

    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    import importlib.util, pathlib as _pl
    spec = importlib.util.spec_from_file_location(
        "migration_014",
        _pl.Path(__file__).parent.parent / "migrations" / "016_local_docs.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.migrate({})

    from local_doc_ingestor import LocalDocIngestor
    from local_docs_api import router as docs_router, init_local_docs_api

    app_test = FastAPI()
    ing = LocalDocIngestor(state_dir=str(tmp_state[0]), qdrant_client=mock_qdrant)
    init_local_docs_api(ingestor=ing)
    app_test.include_router(docs_router)

    client = TestClient(app_test)

    with patch("local_doc_ingestor.litellm") as mock_litellm:
        mock_litellm.embedding.return_value = {"data": [{"embedding": fake_vec}]}
        ingest_resp = client.post("/api/docs/ingest", json={"file_path": str(f)})

    assert ingest_resp.status_code == 200
    doc_id = ingest_resp.json()["doc_id"]

    list_resp = client.get("/api/docs/")
    assert list_resp.status_code == 200
    items = list_resp.json()["items"]
    assert any(i["doc_id"] == doc_id for i in items)

    del_resp = client.delete(f"/api/docs/{doc_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["deleted"] is True

    list_resp2 = client.get("/api/docs/")
    items2 = list_resp2.json()["items"]
    assert not any(i["doc_id"] == doc_id for i in items2)


@pytest.mark.integration
def test_api_status_endpoint(tmp_state, mock_qdrant, monkeypatch):
    """GET /api/docs/status returns total_files consistent with DB."""
    _, watch_path = tmp_state
    fake_vec = [0.0] * 1536

    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    import importlib.util, pathlib as _pl
    spec = importlib.util.spec_from_file_location(
        "migration_014",
        _pl.Path(__file__).parent.parent / "migrations" / "016_local_docs.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.migrate({})

    from local_doc_ingestor import LocalDocIngestor
    from local_docs_api import router as docs_router, init_local_docs_api

    app_test = FastAPI()
    ing = LocalDocIngestor(state_dir=str(tmp_state[0]), qdrant_client=mock_qdrant)
    init_local_docs_api(ingestor=ing)
    app_test.include_router(docs_router)

    client = TestClient(app_test)

    f = watch_path / "status_test.txt"
    f.write_text("status test content here for verification")
    with patch("local_doc_ingestor.litellm") as mock_litellm:
        mock_litellm.embedding.return_value = {"data": [{"embedding": fake_vec}]}
        client.post("/api/docs/ingest", json={"file_path": str(f)})

    status_resp = client.get("/api/docs/status")
    assert status_resp.status_code == 200
    data = status_resp.json()
    assert data["total_files"] >= 1
    assert "breakdown" in data
    assert "watcher_running" in data


@pytest.mark.integration
def test_api_disabled(tmp_state, mock_qdrant):
    """LOCAL_DOCS_ENABLED=false → all endpoints return HTTP 503."""
    with patch.dict(os.environ, {"LOCAL_DOCS_ENABLED": "false"}, clear=False):
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        import importlib.util, pathlib as _pl
        spec = importlib.util.spec_from_file_location(
            "migration_014",
            _pl.Path(__file__).parent.parent / "migrations" / "016_local_docs.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.migrate({})

        from local_doc_ingestor import LocalDocIngestor
        from local_docs_api import router as docs_router, init_local_docs_api

        app_test = FastAPI()
        ing = LocalDocIngestor(state_dir=str(tmp_state[0]), qdrant_client=mock_qdrant)
        init_local_docs_api(ingestor=ing)
        app_test.include_router(docs_router)

        client = TestClient(app_test)

        assert client.post("/api/docs/ingest", json={"file_path": "/tmp/x.txt"}).status_code == 503
        assert client.get("/api/docs/").status_code == 503
        assert client.get("/api/docs/status").status_code == 503
```

**Run (red):**
```bash
python -m pytest tests/test_local_doc_ingestor.py -m integration -v
```

### Implementation

**File:** `src/bridge/local_docs_api.py`

```python
"""Local Docs API — FastAPI router for /api/docs/*."""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("rag-bridge.local_docs_api")

router = APIRouter(prefix="/api/docs", tags=["local-docs"])

# Injected at startup by app.py
_ingestor: Any = None


def init_local_docs_api(ingestor: Any) -> None:
    global _ingestor
    _ingestor = ingestor


def _get_ingestor() -> Any:
    if _ingestor is None:
        raise HTTPException(status_code=503, detail="LocalDocIngestor not initialised")
    return _ingestor


def _require_enabled(ingestor: Any) -> None:
    """Raise HTTP 503 if LOCAL_DOCS_ENABLED is false."""
    import os
    if not os.getenv("LOCAL_DOCS_ENABLED", "false").lower() in ("1", "true", "yes"):
        raise HTTPException(
            status_code=503,
            detail="Local document ingestion is disabled. Set LOCAL_DOCS_ENABLED=true to enable.",
        )


# ------------------------------------------------------------------
# Pydantic models
# ------------------------------------------------------------------

class IngestRequest(BaseModel):
    file_path: str


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@router.post("/ingest")
def ingest_file(req: IngestRequest) -> dict:
    ingestor = _get_ingestor()
    _require_enabled(ingestor)
    try:
        result = ingestor.ingest_file(req.file_path)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("ingest_file failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if result.get("status") == "error":
        raise HTTPException(status_code=500, detail=result.get("error_message", "ingestion error"))
    return result


@router.get("/")
def list_documents(
    limit: int = 20,
    offset: int = 0,
    file_type: Optional[str] = None,
    status: Optional[str] = None,
) -> dict:
    ingestor = _get_ingestor()
    _require_enabled(ingestor)
    items = ingestor.list_documents(limit=limit, offset=offset, file_type=file_type, status=status)
    # Count total (without pagination)
    all_items = ingestor.list_documents(limit=10_000, offset=0, file_type=file_type, status=status)
    return {
        "items": items,
        "total": len(all_items),
        "limit": limit,
        "offset": offset,
    }


@router.delete("/{doc_id}")
def delete_document(doc_id: str) -> dict:
    ingestor = _get_ingestor()
    _require_enabled(ingestor)
    deleted = ingestor.delete_document(doc_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Document '{doc_id}' not found")
    return {"deleted": True, "doc_id": doc_id}


@router.get("/status")
def get_status() -> dict:
    ingestor = _get_ingestor()
    _require_enabled(ingestor)
    return ingestor.get_status()
```

**Run (green):**
```bash
python -m pytest tests/test_local_doc_ingestor.py -m integration -v
```

**Commit:**
```
feat(local-docs): add local_docs_api.py with POST /ingest, GET /, DELETE /{doc_id}, GET /status
```

- [ ] Add integration API tests to `tests/test_local_doc_ingestor.py`
- [ ] Run tests (expect red)
- [ ] Create `src/bridge/local_docs_api.py`
- [ ] Run tests (expect green)
- [ ] Commit

---

## Task 10 — Mount in `app.py`

### Test first

```bash
# Verify the app still imports cleanly and the endpoint appears in OpenAPI
cd /opt/nanobot-stack/rag-bridge
python -c "
import os; os.environ.setdefault('LOCAL_DOCS_ENABLED','true')
from app import app
routes = [r.path for r in app.routes]
assert any('/api/docs' in r for r in routes), f'docs routes missing: {routes}'
print('OK — /api/docs/* mounted')
"
```

### Implementation

Add to `src/bridge/app.py` after the RSS block (around line 1463):

```python
# ---------------------------------------------------------------------------
# Sub-project E: Local Document Ingestion
# ---------------------------------------------------------------------------
try:
    from local_doc_ingestor import LocalDocIngestor, LocalDocWatcher
    from local_docs_api import router as local_docs_router, init_local_docs_api

    _local_doc_ingestor = LocalDocIngestor(state_dir=STATE_DIR, qdrant_client=qdrant)
    init_local_docs_api(ingestor=_local_doc_ingestor)
    app.include_router(local_docs_router, dependencies=[Depends(verify_token)])

    _LOCAL_DOCS_ENABLED = os.getenv("LOCAL_DOCS_ENABLED", "false").lower() in ("1", "true", "yes")
    if _LOCAL_DOCS_ENABLED:
        _local_doc_watcher = LocalDocWatcher()
        _watch_path = os.getenv("LOCAL_DOCS_WATCH_PATH", "/opt/nanobot-stack/watched-docs/")
        _local_doc_watcher.start(path=_watch_path, ingestor=_local_doc_ingestor)
        logger.info("LocalDocWatcher started on %s", _watch_path)

    logger.info("Local Docs endpoints mounted (/api/docs/*)")
except Exception as exc:
    logger.info("Local Docs API not loaded: %s", exc)
```

Also add a shutdown hook for the watcher. Find the existing shutdown section in `app.py` (search for `@app.on_event("shutdown")` or `lifespan`) and add:

```python
    # Stop local doc watcher
    try:
        _local_doc_watcher.stop()
    except Exception:
        pass
```

**Run (green):**
```bash
python -c "
import os; os.environ.setdefault('LOCAL_DOCS_ENABLED','true')
from app import app
routes = [r.path for r in app.routes]
assert any('/api/docs' in r for r in routes)
print('OK')
"
```

**Commit:**
```
feat(local-docs): mount local_docs_api in app.py, start LocalDocWatcher on startup
```

- [ ] Add mount block to `src/bridge/app.py` after the RSS block
- [ ] Add watcher stop to shutdown hook
- [ ] Verify import check passes
- [ ] Commit

---

## Task 11 — Full test suite run + fixture files

### Create fixture files

**Script** (run once to generate binary fixtures):

```python
# scripts/create_test_fixtures.py
"""Generate binary test fixtures for sub-project E tests."""
import pathlib

FIXTURES = pathlib.Path("tests/fixtures/docs")
FIXTURES.mkdir(parents=True, exist_ok=True)

# sample.txt
(FIXTURES / "sample.txt").write_text(
    "This is a sample plain text document.\n" * 60,
    encoding="utf-8",
)

# sample.md
(FIXTURES / "sample.md").write_text(
    "# Sample Markdown Document\n\n"
    "This document contains **bold** and _italic_ text.\n\n"
    "- Item one\n- Item two\n- Item three\n\n"
    "```python\nprint('hello')\n```\n\n"
    "More content here to ensure chunking.\n" * 20,
    encoding="utf-8",
)

# sample_pii.txt
(FIXTURES / "sample_pii.txt").write_text(
    "Contact: jean.dupont@example.com\nPhone: +33 6 12 34 56 78\nRegular content here.\n",
    encoding="utf-8",
)

# sample.pdf — minimal valid PDF (3 pages)
try:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    import io
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle("Test Document")
    for page_num in range(1, 4):
        c.drawString(72, 700, f"Page {page_num} of Test Document")
        c.drawString(72, 680, "Known keyword: nanobot-stack sample content")
        c.showPage()
    c.save()
    (FIXTURES / "sample.pdf").write_bytes(buf.getvalue())
    print("sample.pdf created with reportlab")
except ImportError:
    # Minimal PDF stub (not parseable but prevents file-not-found)
    (FIXTURES / "sample.pdf").write_bytes(b"%PDF-1.4\n%stub\n")
    print("sample.pdf: reportlab not available, stub created")

# sample.docx — minimal DOCX with title and paragraphs
try:
    import docx
    document = docx.Document()
    document.core_properties.title = "Test DOCX Document"
    for i in range(1, 6):
        document.add_paragraph(f"Paragraph {i}: This is sample content for testing.")
    document.save(str(FIXTURES / "sample.docx"))
    print("sample.docx created")
except ImportError:
    (FIXTURES / "sample.docx").write_bytes(b"PK stub")
    print("sample.docx: python-docx not available, stub created")

print("Fixtures written to", FIXTURES)
```

```bash
cd /opt/nanobot-stack/rag-bridge
python scripts/create_test_fixtures.py
```

### Full test run

```bash
# Unit tests only
python -m pytest tests/test_local_doc_ingestor.py -m "not integration" -v

# Integration tests
python -m pytest tests/test_local_doc_ingestor.py -m integration -v

# Full suite
python -m pytest tests/test_local_doc_ingestor.py -v --tb=short
```

### Coverage check

```bash
python -m pytest tests/test_local_doc_ingestor.py \
  --cov=local_doc_ingestor --cov=local_docs_api \
  --cov-report=term-missing -v
```

**Target:** ≥ 85% coverage on `local_doc_ingestor.py` and `local_docs_api.py`.

**Commit:**
```
test(local-docs): add fixture generator script, complete test suite for sub-project E
```

- [ ] Create `scripts/create_test_fixtures.py`
- [ ] Run fixture generator
- [ ] Run full test suite (expect all green)
- [ ] Check coverage ≥ 85%
- [ ] Commit

---

## Summary of files created / modified

| File | Action |
|------|--------|
| `migrations/016_local_docs.py` | Create — `docs_ingestion_log` table + 3 indexes |
| `src/bridge/local_doc_ingestor.py` | Create — `LocalDocIngestor` + `LocalDocWatcher` |
| `src/bridge/local_docs_api.py` | Create — `GET /api/docs/`, `POST /api/docs/ingest`, `DELETE /api/docs/{doc_id}`, `GET /api/docs/status` |
| `src/bridge/app.py` | Modify — mount local_docs_api, start watcher, shutdown hook |
| `src/bridge/requirements.txt` | Modify — add `watchdog>=3.0` (pypdf + python-docx already present) |
| `tests/test_local_doc_ingestor.py` | Create — full unit + integration test suite |
| `tests/test_migration_014.py` | Create — migration idempotency tests |
| `scripts/create_test_fixtures.py` | Create — binary fixture generator |
| `tests/fixtures/docs/` | Create — sample.txt, sample.md, sample_pii.txt, sample.pdf, sample.docx |

## Environment variables to set for local dev

```bash
export LOCAL_DOCS_ENABLED=true
export LOCAL_DOCS_WATCH_PATH=/opt/nanobot-stack/watched-docs/
export LOCAL_DOCS_CHUNK_SIZE=512
export LOCAL_DOCS_CHUNK_OVERLAP=50
export LOCAL_DOCS_FORMATS=pdf,md,txt,docx
```
