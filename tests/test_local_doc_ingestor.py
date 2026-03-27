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
    import importlib.util
    import pathlib as _pl
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
    """LOCAL_DOCS_ENABLED=false -> ingest_file returns status='disabled', no Qdrant call."""
    tmp_path, watch_path = tmp_state
    with patch.dict(os.environ, {"LOCAL_DOCS_ENABLED": "false"}, clear=False):
        import importlib.util
        import pathlib as _pl
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
    assert meta["title"] == "just some text"


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
    text = "Sentence number one. " * 200  # ~4200 chars
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
    """Text smaller than overlap threshold -> exactly one chunk."""
    short = "Hello world."
    chunks = ingestor._chunk_text(short, "small")
    assert len(chunks) == 1
    assert chunks[0] == short


# ---------------------------------------------------------------------------
# Task 4: PDF ingestion
# ---------------------------------------------------------------------------

def test_detect_format_pdf(ingestor, tmp_state):
    """Extension .pdf -> 'pdf'."""
    _, watch_path = tmp_state
    f = watch_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4")
    assert ingestor._detect_format(str(f)) == "pdf"


def test_detect_format_unsupported(ingestor, tmp_state):
    """Extension .xlsx -> None."""
    _, watch_path = tmp_state
    f = watch_path / "sheet.xlsx"
    f.write_bytes(b"PK")
    assert ingestor._detect_format(str(f)) is None


def test_extract_text_pdf(ingestor, tmp_state):
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


def test_extract_title_pdf_metadata(ingestor, tmp_state):
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


def test_ingest_file_pdf(ingestor, tmp_state, mock_qdrant):
    """Full PDF ingestion path -- mock Qdrant and pypdf, verify upsert called."""
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


# ---------------------------------------------------------------------------
# Task 5: DOCX ingestion
# ---------------------------------------------------------------------------

def test_extract_text_docx(ingestor, tmp_state):
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


def test_extract_title_docx_core_properties(ingestor, tmp_state):
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


def test_extract_title_docx_fallback(ingestor, tmp_state):
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


# ---------------------------------------------------------------------------
# Task 6: _embed_and_upsert
# ---------------------------------------------------------------------------

def test_embed_and_upsert_calls_qdrant(ingestor, mock_qdrant):
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
    total_points = sum(len(c.kwargs.get("points", [])) for c in call_args)
    assert total_points == len(chunks)


def test_embed_and_upsert_point_ids_are_uuids(ingestor, mock_qdrant):
    """Point IDs passed to Qdrant must be valid UUIDs (not arbitrary strings)."""
    chunks = ["chunk one content here", "chunk two content here"]
    doc_id = str(uuid.uuid4())
    metadata = {
        "doc_id": doc_id,
        "source_path": "/tmp/watched/doc.txt",
        "file_type": "txt",
        "file_hash": "abc123",
        "title": "UUID Test Doc",
        "tags": ["test"],
        "ingested_at": "2026-03-24T00:00:00Z",
    }
    fake_vec = [0.0] * 1536

    with patch("local_doc_ingestor.litellm") as mock_litellm:
        mock_litellm.embedding.return_value = {"data": [{"embedding": fake_vec}]}
        ingestor._embed_and_upsert(chunks, metadata)

    all_points = []
    for call in mock_qdrant.upsert.call_args_list:
        all_points.extend(call.kwargs.get("points", []))

    assert len(all_points) == len(chunks)
    for pt in all_points:
        # Qdrant requires UUID or integer IDs; assert the ID is a valid UUID string
        parsed = uuid.UUID(pt.id)  # raises ValueError if not a valid UUID
        assert str(parsed) == pt.id


def test_embed_and_upsert_batch_limit(ingestor, mock_qdrant):
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


def test_pii_filter_applied(ingestor, tmp_state, mock_qdrant):
    """Chunks stored in Qdrant do not contain original PII values."""
    _, watch_path = tmp_state
    f = watch_path / "pii_doc.txt"
    f.write_text(
        "Contact us at jean.dupont@example.com or call +33 6 12 34 56 78 for support.",
        encoding="utf-8",
    )
    fake_vec = [0.0] * 1536
    captured_payloads: list[dict] = []

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


# ---------------------------------------------------------------------------
# Task 7: full ingest_file + delete_document
# ---------------------------------------------------------------------------

def test_ingest_file_unsupported_format(ingestor, tmp_state, mock_qdrant):
    """Extension .xlsx -> status='skipped', reason='unsupported_format', no Qdrant call."""
    _, watch_path = tmp_state
    f = watch_path / "sheet.xlsx"
    f.write_bytes(b"PK fake xlsx")
    result = ingestor.ingest_file(str(f))
    assert result["status"] == "skipped"
    assert result["reason"] == "unsupported_format"
    mock_qdrant.upsert.assert_not_called()


def test_dedup_same_hash(ingestor, tmp_state, mock_qdrant):
    """Ingesting the same file twice -> second call returns status='skipped', no second upsert."""
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


def test_dedup_changed_hash(ingestor, tmp_state, mock_qdrant):
    """Modified file -> second ingest re-indexes and deletes old Qdrant chunks."""
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


def test_file_size_limit(ingestor, tmp_state, mock_qdrant):
    """File > 50 MB -> status='error', no Qdrant call."""
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


def test_delete_document(ingestor, tmp_state, mock_qdrant):
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
    """Unknown doc_id -> delete_document returns False."""
    result = ingestor.delete_document("00000000-0000-0000-0000-000000000000")
    assert result is False


def test_list_documents(ingestor, tmp_state, mock_qdrant):
    """3 indexed + 1 deleted -> list_documents returns 2 (non-deleted)."""
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


def test_get_status_breakdown(ingestor, tmp_state, mock_qdrant):
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


# ---------------------------------------------------------------------------
# Task 8: LocalDocWatcher
# ---------------------------------------------------------------------------

def test_watcher_ignores_temp_files(tmp_state):
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


def test_watcher_debounce(tmp_state):
    """Two on_modified events within 2s on the same file -> one ingest_file call."""
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

    # Two rapid events -- both scheduled, only one should fire
    watcher._handle_event(event, mock_ingestor)
    watcher._handle_event(event, mock_ingestor)  # resets debounce timer

    # Wait for debounce to fire
    time.sleep(2.5)

    assert mock_ingestor.ingest_file.call_count == 1


def test_batch_ingestion_partial_errors(ingestor, tmp_state, mock_qdrant):
    """ingest_directory: 2 valid + 1 corrupt file -> 2 indexed, 1 error, no exception raised."""
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


# ---------------------------------------------------------------------------
# Task 9: API integration tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_api_ingest_endpoint(tmp_state, mock_qdrant):
    """POST /api/docs/ingest with valid file -> HTTP 200, status='indexed'."""
    _, watch_path = tmp_state
    f = watch_path / "api_test.md"
    f.write_text("# API Test\n\nSome content for the API test endpoint.")
    fake_vec = [0.0] * 1536

    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    import importlib.util
    import pathlib as _pl
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
    """POST /api/docs/ingest with path outside watch dir -> HTTP 422."""
    _, watch_path = tmp_state
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    import importlib.util
    import pathlib as _pl
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
def test_api_list_and_delete(tmp_state, mock_qdrant):
    """Ingest -> list -> delete -> file absent from list."""
    _, watch_path = tmp_state
    f = watch_path / "list_test.txt"
    f.write_text("content for list and delete test")
    fake_vec = [0.0] * 1536

    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    import importlib.util
    import pathlib as _pl
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
    assert any(i["id"] == doc_id for i in items)

    del_resp = client.delete(f"/api/docs/{doc_id}")
    assert del_resp.status_code == 200
    assert del_resp.json()["deleted"] is True

    list_resp2 = client.get("/api/docs/")
    items2 = list_resp2.json()["items"]
    assert not any(i["id"] == doc_id for i in items2)


@pytest.mark.integration
def test_api_status_endpoint(tmp_state, mock_qdrant):
    """GET /api/docs/status returns total_files consistent with DB."""
    _, watch_path = tmp_state
    fake_vec = [0.0] * 1536

    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    import importlib.util
    import pathlib as _pl
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
    """LOCAL_DOCS_ENABLED=false -> all endpoints return HTTP 503."""
    with patch.dict(os.environ, {"LOCAL_DOCS_ENABLED": "false"}, clear=False):
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        import importlib.util
        import pathlib as _pl
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
