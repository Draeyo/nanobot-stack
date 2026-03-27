"""Tests for ObsidianIngestor — Obsidian vault ingestion component."""
from __future__ import annotations
import os
import pathlib
import tempfile
import uuid
import pytest
from unittest.mock import MagicMock, patch, AsyncMock


def _make_ingestor(monkeypatch, vault_path=None, **kwargs):
    tmp = tempfile.mkdtemp()
    vault = vault_path or tmp
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", vault)
    monkeypatch.setenv("RAG_STATE_DIR", tmp)

    with patch("local_doc_ingestor.LocalDocIngestor.__init__", return_value=None):
        from obsidian_ingestor import ObsidianIngestor
        ingestor = ObsidianIngestor(
            state_dir=tmp,
            qdrant_client=MagicMock(),
        )
        ingestor._db_path = pathlib.Path(tmp) / "scheduler.db"
        ingestor._vault_path = pathlib.Path(vault)
        ingestor._enabled = bool(vault)
        ingestor._qdrant = MagicMock()
    return ingestor


class TestObsidianIngestorInit:
    def test_enabled_when_vault_path_set(self, monkeypatch, tmp_path):
        ingestor = _make_ingestor(monkeypatch, vault_path=str(tmp_path))
        assert ingestor._enabled is True

    def test_disabled_when_vault_path_empty(self, monkeypatch):
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", "")
        with patch("local_doc_ingestor.LocalDocIngestor.__init__", return_value=None):
            from obsidian_ingestor import ObsidianIngestor
            ingestor = ObsidianIngestor(state_dir="/tmp", qdrant_client=None)
        assert ingestor._enabled is False

    def test_vault_path_stored_as_pathlib(self, monkeypatch, tmp_path):
        ingestor = _make_ingestor(monkeypatch, vault_path=str(tmp_path))
        assert isinstance(ingestor._vault_path, pathlib.Path)

    def test_disabled_when_vault_path_missing_from_env(self, monkeypatch):
        monkeypatch.delenv("OBSIDIAN_VAULT_PATH", raising=False)
        with patch("local_doc_ingestor.LocalDocIngestor.__init__", return_value=None):
            from obsidian_ingestor import ObsidianIngestor
            ingestor = ObsidianIngestor(state_dir="/tmp", qdrant_client=None)
        assert ingestor._enabled is False


class TestParseFrontmatter:
    def _get_method(self, monkeypatch, tmp_path):
        ingestor = _make_ingestor(monkeypatch, vault_path=str(tmp_path))
        return ingestor._parse_frontmatter

    def test_extracts_tags(self, monkeypatch, tmp_path):
        parse = self._get_method(monkeypatch, tmp_path)
        content = "---\ntags: [python, architecture]\n---\n# Note"
        result = parse(content)
        assert result.get("tags") == ["python", "architecture"]

    def test_extracts_aliases(self, monkeypatch, tmp_path):
        parse = self._get_method(monkeypatch, tmp_path)
        content = "---\naliases: [RAG, retrieval]\n---\n# Note"
        result = parse(content)
        assert result.get("aliases") == ["RAG", "retrieval"]

    def test_extracts_created_and_modified(self, monkeypatch, tmp_path):
        parse = self._get_method(monkeypatch, tmp_path)
        content = "---\ncreated: 2026-01-01\nmodified: 2026-03-20\n---\n# Note"
        result = parse(content)
        assert "created" in result
        assert "modified" in result

    def test_returns_empty_dict_when_no_frontmatter(self, monkeypatch, tmp_path):
        parse = self._get_method(monkeypatch, tmp_path)
        content = "# Just a note\nSome content."
        result = parse(content)
        assert result == {}

    def test_returns_empty_dict_on_invalid_yaml(self, monkeypatch, tmp_path):
        parse = self._get_method(monkeypatch, tmp_path)
        content = "---\ntags: [unclosed\n---\n# Note"
        result = parse(content)
        assert result == {}

    def test_does_not_raise_on_malformed_input(self, monkeypatch, tmp_path):
        parse = self._get_method(monkeypatch, tmp_path)
        content = "---\n: invalid yaml :\n---\n# Note"
        result = parse(content)
        assert isinstance(result, dict)

    def test_returns_empty_dict_when_frontmatter_not_at_start(self, monkeypatch, tmp_path):
        parse = self._get_method(monkeypatch, tmp_path)
        content = "# Note\n---\ntags: [python]\n---\n"
        result = parse(content)
        assert result == {}

    def test_uses_safe_load_not_unsafe_load(self, monkeypatch, tmp_path):
        parse = self._get_method(monkeypatch, tmp_path)
        content = "---\ntags: !!python/object:os.system [echo hacked]\n---\n# Note"
        result = parse(content)
        assert isinstance(result, dict)


class TestExtractWikilinks:
    def _get_method(self, monkeypatch, tmp_path):
        ingestor = _make_ingestor(monkeypatch, vault_path=str(tmp_path))
        return ingestor._extract_wikilinks

    def test_extracts_simple_wikilink(self, monkeypatch, tmp_path):
        extract = self._get_method(monkeypatch, tmp_path)
        content = "See [[My Note]] for details."
        result = extract(content)
        assert "my note" in result

    def test_extracts_multiple_wikilinks(self, monkeypatch, tmp_path):
        extract = self._get_method(monkeypatch, tmp_path)
        content = "[[Note A]] and [[Note B]] are related."
        result = extract(content)
        assert len(result) == 2

    def test_extracts_note_name_from_aliased_wikilink(self, monkeypatch, tmp_path):
        extract = self._get_method(monkeypatch, tmp_path)
        content = "See [[architecture notes|archi]] here."
        result = extract(content)
        assert "architecture notes" in result
        assert "archi" not in result

    def test_deduplicates_wikilinks(self, monkeypatch, tmp_path):
        extract = self._get_method(monkeypatch, tmp_path)
        content = "[[Note A]] is great. Also see [[Note A]]."
        result = extract(content)
        assert len(result) == 1

    def test_excludes_url_links(self, monkeypatch, tmp_path):
        extract = self._get_method(monkeypatch, tmp_path)
        content = "[[https://example.com]] is not a wikilink."
        result = extract(content)
        assert result == []

    def test_normalizes_to_lowercase(self, monkeypatch, tmp_path):
        extract = self._get_method(monkeypatch, tmp_path)
        content = "See [[My Note]] for details."
        result = extract(content)
        assert result[0] == "my note"

    def test_strips_whitespace(self, monkeypatch, tmp_path):
        extract = self._get_method(monkeypatch, tmp_path)
        content = "[[ My Note ]]"
        result = extract(content)
        assert result[0] == "my note"

    def test_returns_empty_list_when_no_wikilinks(self, monkeypatch, tmp_path):
        extract = self._get_method(monkeypatch, tmp_path)
        content = "# No wikilinks here\nJust plain text."
        result = extract(content)
        assert result == []

    def test_preserves_insertion_order_after_dedup(self, monkeypatch, tmp_path):
        extract = self._get_method(monkeypatch, tmp_path)
        content = "[[Note B]] then [[Note A]] then [[Note B]] again."
        result = extract(content)
        assert result == ["note b", "note a"]


class TestIngestVault:
    def _make_vault(self, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "note_a.md").write_text(
            "---\ntags: [python]\naliases: [NoteA]\n---\n# Note A\nSee [[Note B]] for more.",
            encoding="utf-8",
        )
        (vault / "note_b.md").write_text(
            "# Note B\nThis has no frontmatter. Links to [[Note A]].",
            encoding="utf-8",
        )
        subdir = vault / "subdir"
        subdir.mkdir()
        (subdir / "note_c.md").write_text(
            "---\ntags: [architecture]\n---\n# Note C\nNo links.",
            encoding="utf-8",
        )
        return vault

    @pytest.mark.asyncio
    async def test_ingest_vault_processes_all_md_files(self, monkeypatch, tmp_path):
        vault = self._make_vault(tmp_path)
        ingestor = _make_ingestor(monkeypatch, vault_path=str(vault))
        ingestor._enabled = True
        call_count = 0
        def mock_ingest_file(path, **kwargs):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            result.status = "indexed"
            result.doc_id = str(uuid.uuid4())
            return result
        ingestor.ingest_file = mock_ingest_file
        ingestor._update_obsidian_index = MagicMock()
        result = await ingestor.ingest_vault()
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_ingest_vault_returns_stats(self, monkeypatch, tmp_path):
        vault = self._make_vault(tmp_path)
        ingestor = _make_ingestor(monkeypatch, vault_path=str(vault))
        ingestor._enabled = True
        def mock_ingest_file(path, **kwargs):
            result = MagicMock()
            result.status = "indexed"
            result.doc_id = str(uuid.uuid4())
            return result
        ingestor.ingest_file = mock_ingest_file
        ingestor._update_obsidian_index = MagicMock()
        stats = await ingestor.ingest_vault()
        assert "indexed" in stats
        assert "errors" in stats
        assert stats["indexed"] == 3

    @pytest.mark.asyncio
    async def test_ingest_vault_returns_disabled_when_not_enabled(self, monkeypatch, tmp_path):
        ingestor = _make_ingestor(monkeypatch, vault_path=str(tmp_path))
        ingestor._enabled = False
        stats = await ingestor.ingest_vault()
        assert stats.get("status") == "disabled"

    @pytest.mark.asyncio
    async def test_ingest_vault_skips_non_md_files(self, monkeypatch, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "note.md").write_text("# Note", encoding="utf-8")
        (vault / "image.png").write_bytes(b"\x89PNG")
        (vault / "config.json").write_text("{}", encoding="utf-8")
        ingestor = _make_ingestor(monkeypatch, vault_path=str(vault))
        ingestor._enabled = True
        call_count = 0
        def mock_ingest_file(path, **kwargs):
            nonlocal call_count
            call_count += 1
            r = MagicMock(); r.status = "indexed"; r.doc_id = str(uuid.uuid4())
            return r
        ingestor.ingest_file = mock_ingest_file
        ingestor._update_obsidian_index = MagicMock()
        await ingestor.ingest_vault()
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_ingest_vault_counts_errors(self, monkeypatch, tmp_path):
        vault = tmp_path / "vault"
        vault.mkdir()
        for i in range(3):
            (vault / f"note{i}.md").write_text(f"# Note {i}", encoding="utf-8")
        ingestor = _make_ingestor(monkeypatch, vault_path=str(vault))
        ingestor._enabled = True
        call_count = 0
        def mock_ingest_file(path, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise Exception("ingest error")
            r = MagicMock(); r.status = "indexed"; r.doc_id = "doc-id"
            return r
        ingestor.ingest_file = mock_ingest_file
        ingestor._update_obsidian_index = MagicMock()
        stats = await ingestor.ingest_vault()
        assert stats["errors"] == 1
        assert stats["indexed"] == 2
