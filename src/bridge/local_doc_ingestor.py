"""local_doc_ingestor — base class for local document ingestion into Qdrant."""
from __future__ import annotations

import logging
import os
import pathlib
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("rag-bridge.local_doc_ingestor")


@dataclass
class IngestResult:
    """Result of ingesting a single file."""

    status: str  # "indexed" | "updated" | "skipped" | "disabled" | "error"
    doc_id: str
    extra: dict = field(default_factory=dict)


class LocalDocIngestor:
    """Base class for ingesting local documents into Qdrant.

    Subclasses must override :meth:`ingest_file`.
    """

    def __init__(
        self,
        state_dir: str | pathlib.Path,
        qdrant_client: Any,
        **kwargs: Any,
    ) -> None:
        self._state_dir = pathlib.Path(state_dir)
        self._qdrant = qdrant_client
        self._db_path = self._state_dir / "scheduler.db"

    async def ingest_file(
        self, file_path: str | pathlib.Path, **kwargs: Any
    ) -> IngestResult:
        """Ingest a single file. Override in subclasses."""
        resolved = pathlib.Path(file_path).resolve()
        suffix = resolved.suffix.lower()

        if suffix == ".md":
            content = resolved.read_text(encoding="utf-8")
        else:
            return IngestResult(status="skipped", doc_id="")

        extra_metadata = kwargs.get("extra_metadata", {})
        doc_id = self._derive_doc_id(str(file_path))

        await self._upsert_to_qdrant(doc_id, content, str(file_path), extra_metadata)
        return IngestResult(status="indexed", doc_id=doc_id)

    def _derive_doc_id(self, path: str) -> str:
        import hashlib  # pylint: disable=import-outside-toplevel
        return hashlib.sha256(path.encode()).hexdigest()[:32]

    async def _upsert_to_qdrant(
        self,
        doc_id: str,
        content: str,
        source_path: str,
        extra_metadata: dict,
    ) -> None:
        if self._qdrant is None:
            return
        try:
            import litellm  # pylint: disable=import-outside-toplevel
            from qdrant_client.models import PointStruct  # pylint: disable=import-outside-toplevel
            import uuid  # pylint: disable=import-outside-toplevel

            response = await litellm.aembedding(
                model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
                input=[content[:8000]],
            )
            vector = response["data"][0]["embedding"]
            payload = {"source_path": source_path, "content": content[:2000], **extra_metadata}
            point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, doc_id))
            self._qdrant.upsert(
                collection_name="documents",
                points=[PointStruct(id=point_id, vector=vector, payload=payload)],
            )
        except Exception:  # pylint: disable=broad-except
            logger.warning("_upsert_to_qdrant failed for %s", source_path, exc_info=True)
