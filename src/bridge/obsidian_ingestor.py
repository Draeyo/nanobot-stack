"""obsidian_ingestor — Obsidian vault ingestion extending LocalDocIngestor."""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import pathlib
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from local_doc_ingestor import LocalDocIngestor, IngestResult

logger = logging.getLogger("rag-bridge.obsidian_ingestor")


class ObsidianIngestor(LocalDocIngestor):
    """Ingest Obsidian markdown notes with frontmatter and WikiLink metadata."""

    def __init__(self, state_dir: str, qdrant_client: Any, **kwargs: Any) -> None:
        super().__init__(state_dir=state_dir, qdrant_client=qdrant_client, **kwargs)
        vault_path_env = os.getenv("OBSIDIAN_VAULT_PATH", "")
        if not vault_path_env:
            self._enabled: bool = False
            self._vault_path: pathlib.Path | None = None
            logger.warning("OBSIDIAN_VAULT_PATH not set — ObsidianIngestor disabled")
        else:
            self._enabled = True
            self._vault_path = pathlib.Path(vault_path_env)
        self._db_path: pathlib.Path = pathlib.Path(state_dir) / "scheduler.db"

    @property
    def is_enabled(self) -> bool:
        """Whether the Obsidian ingestor is enabled."""
        return self._enabled

    @property
    def vault_path(self) -> pathlib.Path | None:
        """Path to the Obsidian vault (or None if disabled)."""
        return self._vault_path

    @property
    def vault_path_str(self) -> str:
        """String representation of vault_path, empty string if None."""
        return str(self._vault_path) if self._vault_path else ""

    def _parse_frontmatter(self, content: str) -> dict:
        """Extract YAML frontmatter from Obsidian note content."""
        if not content.startswith("---\n"):
            return {}
        end_idx = content.find("\n---", 4)
        if end_idx == -1:
            return {}
        yaml_block = content[4:end_idx]
        try:
            import yaml  # pylint: disable=import-outside-toplevel
            result = yaml.safe_load(yaml_block)
            if not isinstance(result, dict):
                return {}
            return result
        except Exception:  # pylint: disable=broad-except
            logger.warning("Failed to parse frontmatter YAML", exc_info=True)
            return {}

    def _extract_wikilinks(self, content: str) -> list[str]:
        """Extract [[WikiLink]] references from content, deduplicated and lowercased."""
        raw = re.findall(r'\[\[([^|\]]+)(?:\|[^\]]+)?\]\]', content)
        seen: dict[str, None] = {}
        for item in raw:
            normalized = item.strip().lower()
            if "://" in normalized:
                continue
            seen.setdefault(normalized, None)
        return list(seen.keys())

    def ingest_file(  # type: ignore[override]
        self, file_path: str | pathlib.Path, **kwargs: Any
    ) -> IngestResult:
        """Override: ingest a single Obsidian .md file with frontmatter metadata."""
        if not self._enabled:
            return IngestResult(status="disabled", doc_id="")

        resolved = pathlib.Path(file_path).resolve()
        if not str(file_path).endswith(".md"):
            return IngestResult(status="skipped", doc_id="")

        if self._vault_path and not resolved.is_relative_to(self._vault_path.resolve()):
            raise PermissionError(f"Path traversal attempt: {file_path}")

        raw_content = resolved.read_text(encoding="utf-8")
        frontmatter = self._parse_frontmatter(raw_content)
        wikilinks = self._extract_wikilinks(raw_content)

        extra_metadata = {
            "source": "obsidian",
            "obsidian_tags": frontmatter.get("tags", []),
            "obsidian_aliases": frontmatter.get("aliases", []),
            "frontmatter_created": frontmatter.get("created"),
            "frontmatter_modified": frontmatter.get("modified"),
            "wikilinks_count": len(wikilinks),
        }
        kwargs["extra_metadata"] = extra_metadata

        result_dict = super().ingest_file(str(file_path))
        valid_fields = {f.name for f in dataclasses.fields(IngestResult)}
        result = IngestResult(**{k: v for k, v in result_dict.items() if k in valid_fields})

        if result.status in ("indexed", "updated") and result.doc_id:
            self._update_obsidian_index(result.doc_id, str(file_path), wikilinks)

        return result

    def _update_obsidian_index(
        self, doc_id: str, source_path: str, wikilinks: list[str]
    ) -> None:
        """Store WikiLink references in obsidian_index table."""
        if not self._db_path.exists():
            return
        try:
            db = sqlite3.connect(str(self._db_path))
            try:
                now = datetime.now(timezone.utc).isoformat()
                for link in wikilinks:
                    db.execute(
                        "INSERT OR REPLACE INTO obsidian_index "
                        "(id, source_doc_id, source_path, target_note_name, created_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{doc_id}:{link}")),
                            doc_id,
                            source_path,
                            link,
                            now,
                        ),
                    )
                db.commit()
            finally:
                db.close()
        except Exception:  # pylint: disable=broad-except
            logger.warning("_update_obsidian_index failed", exc_info=True)

    async def ingest_vault(self) -> dict:
        """Ingest all .md files in the vault recursively."""
        if not self._enabled or self._vault_path is None:
            return {"status": "disabled"}

        indexed = 0
        updated = 0
        skipped = 0
        errors = 0
        total_files = 0

        loop = asyncio.get_running_loop()
        for md_file in self._vault_path.rglob("*.md"):
            total_files += 1
            try:
                result = await loop.run_in_executor(None, self.ingest_file, md_file)
                status = getattr(result, "status", "skipped")
                if status == "indexed":
                    indexed += 1
                elif status == "updated":
                    updated += 1
                else:
                    skipped += 1
            except Exception:  # pylint: disable=broad-except
                logger.warning("ingest_vault: error on %s", md_file, exc_info=True)
                errors += 1

        return {
            "indexed": indexed,
            "updated": updated,
            "skipped": skipped,
            "errors": errors,
            "total_files": total_files,
        }
