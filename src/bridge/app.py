"""nanobot RAG bridge v7 — FastAPI application.

Improvements over v6:
  - Cross-encoder reranker (BAAI/bge-reranker-v2-m3)
  - Hybrid search: dense + sparse vectors in Qdrant
  - Metadata enrichment at ingestion (title, section, doc_date)
  - Readiness probe in /healthz (Qdrant + API key checks)
  - Circuit breaker on LLM provider fallback chains
  - Rate limiting on /remember (configurable token bucket)
  - Prometheus metrics endpoint (/metrics)
  - Structured JSON logging
  - Audit log middleware (append-only JSONL)
  - LRU embedding cache with TTL
  - Batched embeddings at ingestion
  - Background /ingest with GC of deleted files
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import pathlib
import re
import sqlite3
import threading
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from litellm import completion as litellm_completion
from litellm import embedding as litellm_embedding
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse

from circuit_breaker import CircuitBreakerRegistry
from embedding_cache import EmbeddingCache
from rate_limiter import RateLimiterRegistry
from reranker import rerank, is_available as reranker_status
from token_optimizer import (
    LLMResponseCache, TokenTracker,
    estimate_messages_tokens, estimate_tokens,
)

load_dotenv()

# ---------------------------------------------------------------------------
# Structured JSON logging
# ---------------------------------------------------------------------------
try:
    from pythonjsonlogger import json_log_formatter

    handler = logging.StreamHandler()
    formatter = json_log_formatter.JSONFormatter(
        fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level"},
    )
    handler.setFormatter(formatter)
    logging.root.handlers = [handler]
except ImportError:
    pass  # graceful fallback to default logging

logger = logging.getLogger("rag-bridge")
logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BASE_DIR = pathlib.Path(__file__).resolve().parent
STATE_DIR = pathlib.Path(os.getenv("STATE_DIR", str(BASE_DIR / "state")))
DOCS_DIR = pathlib.Path(os.getenv("DOCS_DIR", "/opt/nanobot-stack/rag-docs"))
QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "")
MODEL_ROUTER_FILE = pathlib.Path(os.getenv("MODEL_ROUTER_FILE", str(BASE_DIR / "model_router.json")))
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "")
LANGFUSE_PUBLIC_KEY = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.getenv("LANGFUSE_SECRET_KEY", "")

SEARCH_LIMIT = int(os.getenv("SEARCH_LIMIT", "8"))
PREFETCH_MULTIPLIER = int(os.getenv("PREFETCH_MULTIPLIER", "4"))
MAX_PREFETCH = int(os.getenv("MAX_PREFETCH", "24"))
MAX_CHUNK_CHARS = int(os.getenv("MAX_CHUNK_CHARS", "1800"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "200"))
AUTO_SUMMARIZE_MEMORY = os.getenv("AUTO_SUMMARIZE_MEMORY", "true").lower() == "true"
DEFAULT_ANSWER_TASK = os.getenv("DEFAULT_ANSWER_TASK", "retrieval_answer")
BRIDGE_TOKEN = os.getenv("RAG_BRIDGE_TOKEN", "")

# Embedding cache config
EMBEDDING_CACHE_SIZE = int(os.getenv("EMBEDDING_CACHE_SIZE", "512"))
EMBEDDING_CACHE_TTL = float(os.getenv("EMBEDDING_CACHE_TTL", "3600"))

# Embedding batch size for ingestion
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "32"))

# Rate limiter config: /remember allows N calls per minute
REMEMBER_RATE_CAPACITY = int(os.getenv("REMEMBER_RATE_CAPACITY", "30"))
REMEMBER_RATE_REFILL = float(os.getenv("REMEMBER_RATE_REFILL", "0.5"))  # tokens/sec

# Circuit breaker config
CB_FAILURE_THRESHOLD = int(os.getenv("CB_FAILURE_THRESHOLD", "3"))
CB_RECOVERY_TIMEOUT = float(os.getenv("CB_RECOVERY_TIMEOUT", "120"))

# Sparse vector toggle
SPARSE_VECTORS_ENABLED = os.getenv("SPARSE_VECTORS_ENABLED", "true").lower() == "true"

# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------
STATE_DIR.mkdir(parents=True, exist_ok=True)

qdrant_kwargs: dict[str, Any] = {"url": QDRANT_URL}
if QDRANT_API_KEY:
    qdrant_kwargs["api_key"] = QDRANT_API_KEY
qdrant = QdrantClient(**qdrant_kwargs)

try:
    from langfuse import Langfuse
    langfuse = Langfuse(
        public_key=LANGFUSE_PUBLIC_KEY or None,
        secret_key=LANGFUSE_SECRET_KEY or None,
        host=LANGFUSE_HOST or None,
    ) if LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY and LANGFUSE_HOST else None
except Exception:
    langfuse = None

embedding_cache = EmbeddingCache(max_size=EMBEDDING_CACHE_SIZE, ttl_seconds=EMBEDDING_CACHE_TTL)
circuit_breakers = CircuitBreakerRegistry(failure_threshold=CB_FAILURE_THRESHOLD, recovery_timeout=CB_RECOVERY_TIMEOUT)
rate_limiters = RateLimiterRegistry()
rate_limiters.register("remember", capacity=REMEMBER_RATE_CAPACITY, refill_rate=REMEMBER_RATE_REFILL)
rate_limiters.register("ingest", capacity=2, refill_rate=0.02)  # max 1 ingest per ~50s

llm_cache = LLMResponseCache()
token_tracker = TokenTracker()

# ---------------------------------------------------------------------------
# FastAPI app with middlewares
# ---------------------------------------------------------------------------
app = FastAPI(title="nanobot-rag-bridge-v8")

# Prometheus metrics
try:
    from prometheus_fastapi_instrumentator import Instrumentator
    instrumentator = Instrumentator(
        should_group_status_codes=True,
        excluded_handlers=["/healthz"],
    )
    instrumentator.instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
    logger.info("Prometheus metrics enabled at /metrics")
except ImportError:
    logger.info("prometheus-fastapi-instrumentator not installed, /metrics disabled")

# Audit log middleware
try:
    from audit import AuditLogMiddleware
    app.add_middleware(AuditLogMiddleware)
    logger.info("Audit log middleware enabled")
except Exception as exc:
    logger.warning("Audit log middleware not loaded: %s", exc)

# Shutdown hook: persist token stats
@app.on_event("shutdown")
def _shutdown():
    token_tracker.flush()
    logger.info("Token stats flushed to disk")

# v8 extension setup is deferred to after run_chat_task / verify_token are defined (see below)

# ---------------------------------------------------------------------------
# Collection mapping
# ---------------------------------------------------------------------------
COLLECTION_DIR_MAP = {
    "docs_reference": DOCS_DIR / "docs",
    "memory_personal": DOCS_DIR / "memory",
    "ops_runbooks": DOCS_DIR / "runbooks",
    "memory_projects": DOCS_DIR / "projects",
    "conversation_summaries": DOCS_DIR / "conversations",
}

# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------
def verify_token(request: Request):
    if not BRIDGE_TOKEN:
        return
    header = request.headers.get("X-Bridge-Token", "")
    if header != BRIDGE_TOKEN:
        raise HTTPException(status_code=401, detail="invalid or missing X-Bridge-Token")

# ---------------------------------------------------------------------------
# Model router with mtime hot-reload
# ---------------------------------------------------------------------------
_router_cache: dict[str, Any] = {"mtime": 0.0, "data": {}}

def load_router() -> dict[str, Any]:
    try:
        mt = MODEL_ROUTER_FILE.stat().st_mtime
    except FileNotFoundError:
        raise RuntimeError(f"Model router file not found: {MODEL_ROUTER_FILE}")
    if mt != _router_cache["mtime"]:
        _router_cache["data"] = json.loads(MODEL_ROUTER_FILE.read_text(encoding="utf-8"))
        _router_cache["mtime"] = mt
        logger.info("Reloaded model_router.json (mtime=%s)", mt)
    return _router_cache["data"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()

def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def make_point_id(raw: str) -> str:
    return str(uuid.UUID(sha256_text(raw)[:32]))

def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()

# ---------------------------------------------------------------------------
# Paragraph-aware chunking
# ---------------------------------------------------------------------------

def chunk_text(text: str, max_chars: int = MAX_CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = text.strip()
    if not text:
        return []
    paragraphs = re.split(r"\n{2,}", text)
    paragraphs = [normalize_whitespace(p) for p in paragraphs if p.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(para) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            start = 0
            while start < len(para):
                end = min(len(para), start + max_chars)
                chunks.append(para[start:end])
                if end == len(para):
                    break
                start = max(0, end - overlap)
            continue
        candidate = f"{current} {para}".strip() if current else para
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                chunks.append(current)
            if overlap > 0 and current:
                tail = current[-overlap:]
                current = f"{tail} {para}".strip()
            else:
                current = para
    if current:
        chunks.append(current)
    return chunks

# ---------------------------------------------------------------------------
# Metadata-enriched text extraction
# ---------------------------------------------------------------------------

def _extract_title(path: pathlib.Path, text: str) -> str:
    """Try to extract a title from the content, fallback to filename."""
    # Markdown H1
    m = re.search(r"^#\s+(.+)", text, re.MULTILINE)
    if m:
        return m.group(1).strip()[:200]
    # First non-empty line
    for line in text.split("\n"):
        line = line.strip()
        if line and len(line) < 200:
            return line
    return path.stem.replace("_", " ").replace("-", " ").title()

def _extract_sections(text: str) -> list[str]:
    """Extract markdown heading names for section metadata."""
    return re.findall(r"^#{1,4}\s+(.+)", text, re.MULTILINE)[:20]

def extract_text(path: pathlib.Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt", ".log", ".yaml", ".yml", ".json", ".toml", ".ini", ".conf", ".csv"}:
        return path.read_text(encoding="utf-8", errors="ignore")
    if suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if suffix == ".docx":
        import docx
        doc = docx.Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs)
    if suffix in {".html", ".htm"}:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="ignore"), "html.parser")
        return soup.get_text("\n")
    return path.read_text(encoding="utf-8", errors="ignore")

# ---------------------------------------------------------------------------
# Sparse vector computation (simple TF-based for Qdrant hybrid search)
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9_\-]{2,}", text.lower())

def compute_sparse_vector(text: str) -> models.SparseVector:
    """Compute a simple TF-based sparse vector for Qdrant.

    Each unique token gets a hash-based index and a log-normalised TF weight.
    """
    tokens = _tokenize(text)
    if not tokens:
        return models.SparseVector(indices=[0], values=[0.0])
    tf = Counter(tokens)
    total = len(tokens)
    indices = []
    values = []
    for token, count in tf.items():
        # Stable hash → positive int32 index
        idx = int(hashlib.md5(token.encode()).hexdigest()[:8], 16) % (2**31)
        weight = (1.0 + math.log(count)) / (1.0 + math.log(total))
        indices.append(idx)
        values.append(round(weight, 6))
    return models.SparseVector(indices=indices, values=values)

# ---------------------------------------------------------------------------
# Router helpers
# ---------------------------------------------------------------------------

def safe_router_view(router: dict[str, Any]) -> dict[str, Any]:
    out = {"version": router.get("version"), "task_routes": router.get("task_routes", {}), "profiles": {}}
    for name, p in router.get("profiles", {}).items():
        out["profiles"][name] = {
            "kind": p.get("kind"), "provider": p.get("provider"),
            "model": p.get("model"), "api_key_env": p.get("api_key_env"),
            "api_base_env": p.get("api_base_env"), "timeout": p.get("timeout"),
            "max_tokens": p.get("max_tokens"), "temperature": p.get("temperature"),
        }
    return out

def route_chain(task_type: str) -> list[str]:
    router = load_router()
    task_routes = router.get("task_routes", {})
    return task_routes.get(task_type) or task_routes.get("fallback_general", [])

def resolve_profile(profile_name: str) -> dict[str, Any]:
    router = load_router()
    profile = router.get("profiles", {}).get(profile_name)
    if not profile:
        raise KeyError(f"Unknown profile: {profile_name}")
    cfg = dict(profile)
    api_key_env = cfg.get("api_key_env", "")
    api_base_env = cfg.get("api_base_env", "")
    cfg["api_key"] = os.getenv(api_key_env, "") if api_key_env else ""
    cfg["api_base"] = os.getenv(api_base_env, "") if api_base_env else None
    return cfg

# ---------------------------------------------------------------------------
# Qdrant collection management — hybrid dense + sparse
# ---------------------------------------------------------------------------

def ensure_collection(name: str, vector_size: int):
    try:
        qdrant.get_collection(name)
    except (UnexpectedResponse, Exception):
        vectors_config = {"dense": models.VectorParams(size=vector_size, distance=models.Distance.COSINE)}
        sparse_config = {}
        if SPARSE_VECTORS_ENABLED:
            sparse_config = {"sparse": models.SparseVectorParams()}
        qdrant.create_collection(
            collection_name=name,
            vectors_config=vectors_config,
            sparse_vectors_config=sparse_config if sparse_config else None,
        )
        logger.info("Created collection %s (dense_size=%d, sparse=%s)", name, vector_size, SPARSE_VECTORS_ENABLED)

# ---------------------------------------------------------------------------
# Langfuse tracing
# ---------------------------------------------------------------------------

@contextmanager
def traced(name: str, metadata: dict[str, Any] | None = None):
    if not langfuse:
        yield None
        return
    try:
        trace = langfuse.trace(name=name, metadata=metadata or {})
        span = trace.span(name=name, metadata=metadata or {})
        yield span
        span.end()
        langfuse.flush()
    except Exception:
        yield None

def _safe_flush():
    if langfuse:
        try:
            langfuse.flush()
        except Exception:
            pass

# ---------------------------------------------------------------------------
# LLM calls with circuit breaker
# ---------------------------------------------------------------------------

def run_chat_task(
    task_type: str,
    messages: list[dict[str, str]],
    json_mode: bool = False,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> dict[str, Any]:
    # Check LLM response cache for deterministic tasks
    cached = llm_cache.get(task_type, messages)
    if cached is not None:
        return cached

    input_tokens = estimate_messages_tokens(messages)
    attempts = []
    chain = route_chain(task_type)
    if not chain:
        raise RuntimeError(f"No route chain for task_type={task_type}")
    with traced("chat_task", {"task_type": task_type, "chain": chain}):
        for profile_name in chain:
            cb = circuit_breakers.get(profile_name)
            if not cb.is_available:
                attempts.append({"profile": profile_name, "status": "circuit_open"})
                continue
            try:
                profile = resolve_profile(profile_name)
                # Skip profiles with missing API keys (not configured) without tripping the circuit breaker
                if profile.get("api_key_env") and not profile.get("api_key"):
                    attempts.append({"profile": profile_name, "status": "not_configured", "reason": f"env {profile['api_key_env']} is empty"})
                    continue
                kwargs: dict[str, Any] = {
                    "model": profile["model"],
                    "messages": messages,
                    "timeout": profile.get("timeout", 60),
                    "temperature": temperature if temperature is not None else profile.get("temperature", 0.2),
                    "max_tokens": max_tokens or profile.get("max_tokens", 1200),
                }
                if profile.get("api_key"):
                    kwargs["api_key"] = profile["api_key"]
                if profile.get("api_base"):
                    kwargs["api_base"] = profile["api_base"]
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                resp = litellm_completion(**kwargs)
                content = resp.choices[0].message.content or ""
                cb.record_success()
                attempts.append({"profile": profile_name, "model": profile["model"], "status": "ok"})
                _safe_flush()

                output_tokens = estimate_tokens(content)
                token_tracker.record(task_type, profile["model"], input_tokens, output_tokens)

                result = {"text": content, "attempts": attempts, "profile": profile_name, "model": profile["model"]}
                llm_cache.put(task_type, messages, result)
                return result
            except Exception as e:
                cb.record_failure()
                attempts.append({"profile": profile_name, "status": "error", "error": str(e)})
        _safe_flush()
        raise RuntimeError(json.dumps({"task_type": task_type, "attempts": attempts}))

# v8: Mount extension endpoints (classify, conversation-hook, plan, feedback, etc.)
# Placed here so run_chat_task and verify_token are already defined.
try:
    from extensions import router as ext_router, init_extensions

    def _ext_search(query: str, collections: list[str] | None = None, limit: int = 5, tags: list[str] | None = None) -> dict:
        """Adapter for extensions to call the search logic."""
        class _S(BaseModel):
            query: str; collections: list[str] = Field(default_factory=list)
            tags: list[str] = Field(default_factory=list); limit: int = 5
            source_name: str | None = None; source_path_prefix: str | None = None
        body = _S(query=query, collections=collections or [], tags=tags or [], limit=limit)
        return search(body)

    def _ext_remember(text: str, collection: str = "memory_personal", subject: str = "",
                      tags: list[str] | None = None, source: str = "extension", summarize: bool = True) -> dict:
        """Adapter for extensions to call remember logic."""
        body = RememberIn(text=text, collection=collection, subject=subject,
                          tags=tags or [], source=source, summarize=summarize)
        return remember(body)

    def _ext_embed(texts: list[str]) -> tuple[list[list[float]], Any]:
        """Adapter for extensions to call embedding (for dedup)."""
        return embed_texts(texts)

    init_extensions(run_chat_fn=run_chat_task, search_fn=_ext_search,
                    remember_fn=_ext_remember, verify_token_dep=verify_token,
                    embed_fn=_ext_embed)
    app.include_router(ext_router)
    logger.info("v8 extensions loaded (classify, conversation-hook, plan, feedback, profile, dashboard, smart-chat)")
except Exception as exc:
    logger.warning("v8 extensions not loaded: %s", exc)

# ---------------------------------------------------------------------------
# Embeddings with cache and batching
# ---------------------------------------------------------------------------

def _raw_embed(texts: list[str]) -> tuple[list[list[float]], list[dict[str, Any]]]:
    """Call the embedding API via the router chain (no cache)."""
    router = load_router()
    chain = router.get("embeddings", {}).get("chain", [])
    attempts: list[dict[str, Any]] = []
    for profile_name in chain:
        cb = circuit_breakers.get(profile_name)
        if not cb.is_available:
            attempts.append({"profile": profile_name, "status": "circuit_open"})
            continue
        try:
            profile = resolve_profile(profile_name)
            if profile.get("api_key_env") and not profile.get("api_key"):
                attempts.append({"profile": profile_name, "status": "not_configured", "reason": f"env {profile['api_key_env']} is empty"})
                continue
            kwargs: dict[str, Any] = {
                "model": profile["model"],
                "input": texts,
                "timeout": profile.get("timeout", 45),
            }
            if profile.get("api_key"):
                kwargs["api_key"] = profile["api_key"]
            if profile.get("api_base"):
                kwargs["api_base"] = profile["api_base"]
            resp = litellm_embedding(**kwargs)
            vectors = [row["embedding"] if isinstance(row, dict) else row.embedding for row in resp.data]
            cb.record_success()
            attempts.append({"profile": profile_name, "model": profile["model"], "status": "ok"})
            # Track embedding token usage
            embed_tokens = sum(estimate_tokens(t) for t in texts)
            token_tracker.record("embedding", profile["model"], embed_tokens, 0)
            return vectors, attempts
        except Exception as e:
            cb.record_failure()
            attempts.append({"profile": profile_name, "status": "error", "error": str(e)})
    raise RuntimeError(json.dumps({"embedding_attempts": attempts}))


def embed_texts(texts: list[str]) -> tuple[list[list[float]], list[dict[str, Any]]]:
    """Embed with LRU cache: only call the API for uncached texts."""
    cached, uncached_indices = embedding_cache.get_many(texts)

    if not uncached_indices:
        # Everything was cached
        vectors = [cached[i] for i in range(len(texts))]
        return vectors, [{"status": "all_cached", "count": len(texts)}]

    uncached_texts = [texts[i] for i in uncached_indices]
    new_vectors, attempts = _raw_embed(uncached_texts)

    # Store in cache
    embedding_cache.put_many(uncached_texts, new_vectors)

    # Merge cached + new into original order
    new_iter = iter(new_vectors)
    result = []
    for i in range(len(texts)):
        if i in cached:
            result.append(cached[i])
        else:
            result.append(next(new_iter))

    attempts.append({"cached": len(cached), "computed": len(uncached_indices)})
    return result, attempts


def embed_texts_batched(texts: list[str], batch_size: int = EMBEDDING_BATCH_SIZE) -> tuple[list[list[float]], list[dict[str, Any]]]:
    """Embed in batches for large ingestion jobs."""
    all_vectors: list[list[float]] = []
    all_attempts: list[dict[str, Any]] = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        vectors, attempts = embed_texts(batch)
        all_vectors.extend(vectors)
        all_attempts.extend(attempts)
    return all_vectors, all_attempts

# ---------------------------------------------------------------------------
# SQLite state tracker
# ---------------------------------------------------------------------------

def sqlite_conn():
    conn = sqlite3.connect(str(STATE_DIR / "ingest.db"))
    conn.execute("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, updated_at TEXT NOT NULL)")
    return conn

def file_needs_ingest(conn: sqlite3.Connection, path: pathlib.Path, digest: str) -> bool:
    row = conn.execute("SELECT sha256 FROM files WHERE path = ?", (str(path),)).fetchone()
    return (not row) or row[0] != digest

def mark_ingested(conn: sqlite3.Connection, path: pathlib.Path, digest: str):
    conn.execute(
        "INSERT INTO files(path, sha256, updated_at) VALUES(?,?,?) ON CONFLICT(path) DO UPDATE SET sha256=excluded.sha256, updated_at=excluded.updated_at",
        (str(path), digest, utcnow()),
    )
    conn.commit()

def choose_collections(collections: list[str] | None) -> list[str]:
    return collections if collections else list(COLLECTION_DIR_MAP.keys())

# ---------------------------------------------------------------------------
# Search (hybrid dense + sparse)
# ---------------------------------------------------------------------------

def _hybrid_search(collection: str, dense_vector: list[float], query_text: str, limit: int) -> list:
    """Search using Qdrant hybrid prefetch (dense + sparse) with RRF fusion."""
    if SPARSE_VECTORS_ENABLED:
        sparse_vec = compute_sparse_vector(query_text)
        try:
            return qdrant.query_points(
                collection_name=collection,
                prefetch=[
                    models.Prefetch(query=dense_vector, using="dense", limit=limit),
                    models.Prefetch(query=sparse_vec, using="sparse", limit=limit),
                ],
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                with_payload=True,
                limit=limit,
            ).points
        except Exception:
            # Fallback: collection may not have sparse vectors (pre-v7 data)
            pass
    # Dense-only fallback
    try:
        return qdrant.search(
            collection_name=collection,
            query_vector=("dense", dense_vector),
            limit=limit,
            with_payload=True,
        )
    except Exception:
        # Legacy collection without named vectors
        return qdrant.search(
            collection_name=collection,
            query_vector=dense_vector,
            limit=limit,
            with_payload=True,
        )

# ---------------------------------------------------------------------------
# Background ingestion with status tracking
# ---------------------------------------------------------------------------
_ingest_lock = threading.Lock()
_ingest_status: dict[str, Any] = {"running": False, "last_run": None, "last_result": None}

def _run_ingest_sync() -> dict[str, Any]:
    conn = sqlite_conn()
    indexed = []
    skipped = 0
    gc_deleted = 0
    current_paths: set[str] = set()

    for collection, folder in COLLECTION_DIR_MAP.items():
        folder.mkdir(parents=True, exist_ok=True)
        for path in sorted(folder.rglob("*")):
            if not path.is_file():
                continue
            current_paths.add(str(path))

            # Standalone image files → process via vision, skip text extraction
            try:
                from vision import is_image_file
                if is_image_file(path):
                    digest = sha256_text(str(path) + str(path.stat().st_mtime))
                    if not file_needs_ingest(conn, path, digest):
                        skipped += 1
                        continue
                    from vision import extract_images, build_vision_messages, VISION_ENABLED
                    if not VISION_ENABLED:
                        skipped += 1
                        continue
                    images = extract_images(path)
                    if not images:
                        skipped += 1
                        continue
                    doc_date = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
                    for img_idx, img in enumerate(images):
                        try:
                            vision_resp = run_chat_task(
                                "vision_describe",
                                build_vision_messages(img["data"], img.get("format", "png"), f"Image file: {path.name}"),
                                max_tokens=500,
                            )
                            desc = vision_resp["text"]
                            if desc:
                                img_vectors, _ = embed_texts([desc])
                                ensure_collection(collection, len(img_vectors[0]))
                                img_pid = make_point_id(f"{collection}|{path}|img{img_idx}|{digest}")
                                img_point_vectors: dict[str, Any] = {"dense": img_vectors[0]}
                                if SPARSE_VECTORS_ENABLED:
                                    img_point_vectors["sparse"] = compute_sparse_vector(desc)
                                qdrant.upsert(collection_name=collection, points=[models.PointStruct(
                                    id=img_pid, vector=img_point_vectors,
                                    payload={
                                        "text": f"[Image: {path.name}] {desc}",
                                        "path": str(path), "source_name": path.name,
                                        "title": path.stem.replace("_", " ").replace("-", " ").title(),
                                        "tags": [path.parent.name, "image"],
                                        "doc_date": doc_date, "created_at": utcnow(),
                                    },
                                )])
                        except Exception:
                            pass
                    mark_ingested(conn, path, digest)
                    indexed.append({"collection": collection, "path": str(path), "chunks": 0, "vision_chunks": len(images), "title": path.name})
                    continue
            except ImportError:
                pass

            try:
                raw = extract_text(path)
            except Exception:
                continue
            raw_stripped = raw.strip()
            if not raw_stripped:
                continue
            raw_normalized = normalize_whitespace(raw_stripped)
            digest = sha256_text(raw_normalized)
            if not file_needs_ingest(conn, path, digest):
                skipped += 1
                continue

            # Metadata enrichment
            title = _extract_title(path, raw_stripped)
            sections = _extract_sections(raw_stripped)
            doc_date = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()

            chunks = chunk_text(raw_stripped)
            if not chunks:
                continue

            # Batched embedding
            vectors, embedding_attempts = embed_texts_batched(chunks)
            ensure_collection(collection, len(vectors[0]))

            points = []
            for idx, (chunk, vector) in enumerate(zip(chunks, vectors)):
                pid = make_point_id(f"{collection}|{path}|{idx}|{digest}")
                point_vectors: dict[str, Any] = {"dense": vector}
                if SPARSE_VECTORS_ENABLED:
                    point_vectors["sparse"] = compute_sparse_vector(chunk)
                points.append(models.PointStruct(
                    id=pid,
                    vector=point_vectors,
                    payload={
                        "text": chunk,
                        "path": str(path),
                        "source_name": path.name,
                        "title": title,
                        "sections": sections,
                        "doc_date": doc_date,
                        "tags": [path.parent.name],
                        "chunk_index": idx,
                        "total_chunks": len(chunks),
                        "created_at": utcnow(),
                    },
                ))
            qdrant.upsert(collection_name=collection, points=points)
            mark_ingested(conn, path, digest)

            # Optional: generate chunk summaries for lighter context injection
            if os.getenv("INGEST_CHUNK_SUMMARIES", "false").lower() == "true":
                try:
                    from context_compression import build_chunk_summary_messages
                    for pt in points:
                        chunk_str = pt.payload.get("text", "")
                        if len(chunk_str) > 200:  # skip tiny chunks
                            summ_msgs = build_chunk_summary_messages(chunk_str)
                            summ_resp = run_chat_task("rewrite_polish", summ_msgs, max_tokens=100)
                            pt.payload["summary"] = summ_resp.get("text", "")[:300]
                    # Re-upsert with summaries added to payloads
                    qdrant.upsert(collection_name=collection, points=points)
                except Exception as exc:
                    logger.debug("Chunk summary generation failed for %s: %s", path, exc)

            # Vision: extract images and generate descriptions
            vision_chunks = 0
            try:
                from vision import extract_images, build_vision_messages, VISION_ENABLED
                if VISION_ENABLED:
                    images = extract_images(path)
                    for img_idx, img in enumerate(images):
                        try:
                            vision_resp = run_chat_task(
                                "vision_describe",
                                build_vision_messages(img["data"], img.get("format", "png"), f"From: {path.name}, page {img.get('page', '?')}"),
                                max_tokens=500,
                            )
                            desc = vision_resp["text"]
                            if desc:
                                img_vectors, _ = embed_texts([desc])
                                img_pid = make_point_id(f"{collection}|{path}|img{img_idx}|{digest}")
                                img_point_vectors: dict[str, Any] = {"dense": img_vectors[0]}
                                if SPARSE_VECTORS_ENABLED:
                                    img_point_vectors["sparse"] = compute_sparse_vector(desc)
                                qdrant.upsert(collection_name=collection, points=[models.PointStruct(
                                    id=img_pid,
                                    vector=img_point_vectors,
                                    payload={
                                        "text": f"[Image from {path.name} p.{img.get('page', '?')}] {desc}",
                                        "path": str(path),
                                        "source_name": path.name,
                                        "title": title,
                                        "tags": [path.parent.name, "image_description"],
                                        "chunk_index": len(chunks) + img_idx,
                                        "created_at": utcnow(),
                                    },
                                )])
                                vision_chunks += 1
                        except Exception:
                            pass
            except ImportError:
                pass

            indexed.append({
                "collection": collection, "path": str(path),
                "chunks": len(points), "vision_chunks": vision_chunks,
                "title": title, "embedding_attempts": embedding_attempts,
            })

    # GC: purge orphan entries
    all_tracked = conn.execute("SELECT path FROM files").fetchall()
    for (tracked_path,) in all_tracked:
        if tracked_path not in current_paths:
            conn.execute("DELETE FROM files WHERE path = ?", (tracked_path,))
            conn.commit()
            for collection in COLLECTION_DIR_MAP:
                try:
                    qdrant.delete(
                        collection_name=collection,
                        points_selector=models.FilterSelector(
                            filter=models.Filter(
                                must=[models.FieldCondition(key="path", match=models.MatchValue(value=tracked_path))]
                            )
                        ),
                    )
                except Exception:
                    pass
            gc_deleted += 1
            logger.info("GC: purged orphan path %s", tracked_path)

    return {"ok": True, "indexed": indexed, "skipped": skipped, "gc_deleted": gc_deleted}

def _background_ingest():
    try:
        result = _run_ingest_sync()
        _ingest_status["last_result"] = result
    except Exception as e:
        _ingest_status["last_result"] = {"ok": False, "error": str(e)}
    finally:
        _ingest_status["running"] = False
        _ingest_status["last_run"] = utcnow()

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class SearchIn(BaseModel):
    query: str
    collections: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    limit: int = 5
    source_name: str | None = None
    source_path_prefix: str | None = None

class RememberIn(BaseModel):
    text: str
    collection: str = "memory_personal"
    subject: str | None = None
    tags: list[str] = Field(default_factory=list)
    source: str = "nanobot"
    summarize: bool = True

class AskIn(BaseModel):
    question: str
    collections: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    limit: int = 6
    answer_task: str = DEFAULT_ANSWER_TASK

class RoutePreviewIn(BaseModel):
    task_type: str

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/healthz")
def healthz():
    """Readiness probe: checks Qdrant connectivity and at least one API key."""
    checks: dict[str, Any] = {"time": utcnow()}

    # Qdrant
    try:
        collections = qdrant.get_collections()
        checks["qdrant"] = {"ok": True, "collections": len(collections.collections)}
    except Exception as e:
        checks["qdrant"] = {"ok": False, "error": str(e)}

    # At least one LLM API key
    has_key = any(os.getenv(k, "") for k in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"])
    checks["api_keys"] = {"configured": has_key}

    # Langfuse
    checks["langfuse"] = {"enabled": bool(langfuse)}

    # Reranker
    checks["reranker"] = reranker_status()

    checks["ok"] = checks["qdrant"].get("ok", False) and has_key
    return checks

@app.get("/routes", dependencies=[Depends(verify_token)])
def routes():
    return safe_router_view(load_router())

@app.post("/route-preview", dependencies=[Depends(verify_token)])
def route_preview_endpoint(body: RoutePreviewIn):
    chain = route_chain(body.task_type)
    profiles = []
    for name in chain:
        cb = circuit_breakers.get(name)
        try:
            p = resolve_profile(name)
            profiles.append({
                "profile": name, "kind": p.get("kind"),
                "provider": p.get("provider"), "model": p.get("model"),
                "api_key_env": p.get("api_key_env"),
                "configured": bool(p.get("api_key")),
                "circuit_breaker": cb.to_dict(),
            })
        except Exception as e:
            profiles.append({"profile": name, "error": str(e)})
    return {"task_type": body.task_type, "chain": profiles}

@app.post("/search", dependencies=[Depends(verify_token)])
def search(body: SearchIn):
    query_vectors, embedding_attempts = embed_texts([body.query])
    query_vector = query_vectors[0]
    collections = choose_collections(body.collections)
    prefetch = min(MAX_PREFETCH, max(body.limit * PREFETCH_MULTIPLIER, body.limit))
    all_rows = []
    for collection in collections:
        try:
            hits = _hybrid_search(collection, query_vector, body.query, prefetch)
        except Exception:
            continue
        for hit in hits:
            payload = hit.payload or {}
            if body.tags and not set(body.tags).intersection(set(payload.get("tags", []))):
                continue
            if body.source_name and payload.get("source_name") != body.source_name:
                continue
            if body.source_path_prefix and not str(payload.get("path", "")).startswith(body.source_path_prefix):
                continue
            all_rows.append({
                "collection": collection, "id": str(hit.id),
                "score": float(hit.score) if hasattr(hit, "score") and hit.score is not None else 0.0,
                "payload": payload,
            })
    # Cross-encoder reranking (or legacy fallback)
    reranked = rerank(body.query, all_rows)[:body.limit]
    # Apply feedback boosts
    try:
        from feedback import apply_feedback_boosts
        reranked = apply_feedback_boosts(reranked)
    except Exception:
        pass
    return {"query": body.query, "results": reranked, "embedding_attempts": embedding_attempts, "collections": collections}

@app.post("/remember", dependencies=[Depends(verify_token)])
def remember(body: RememberIn):
    rate_limiters.check("remember")
    final_text = normalize_whitespace(body.text)
    summary_attempts: list[dict[str, Any]] = []
    if body.summarize and AUTO_SUMMARIZE_MEMORY:
        try:
            response = run_chat_task(
                "remember_extract",
                [
                    {"role": "system", "content": "Extract the durable memory into concise JSON with keys summary and tags."},
                    {"role": "user", "content": final_text},
                ],
                json_mode=True, max_tokens=500,
            )
            summary_attempts = response["attempts"]
            data = json.loads(response["text"])
            final_text = normalize_whitespace(data.get("summary") or final_text)
            if data.get("tags"):
                body.tags = sorted(set(body.tags + [str(x) for x in data.get("tags", [])]))
        except Exception:
            pass

    vectors, embedding_attempts = embed_texts([final_text])
    vector = vectors[0]
    ensure_collection(body.collection, len(vector))
    point_id = make_point_id(f"{body.collection}|{body.source}|{body.subject or ''}|{final_text}")
    point_vectors: dict[str, Any] = {"dense": vector}
    if SPARSE_VECTORS_ENABLED:
        point_vectors["sparse"] = compute_sparse_vector(final_text)
    payload = {
        "text": final_text, "subject": body.subject, "tags": body.tags,
        "source_name": body.source, "created_at": utcnow(),
        "path": f"memory://{body.collection}/{point_id}",
    }
    qdrant.upsert(collection_name=body.collection, points=[models.PointStruct(id=point_id, vector=point_vectors, payload=payload)])
    return {"ok": True, "id": point_id, "payload": payload, "embedding_attempts": embedding_attempts, "summary_attempts": summary_attempts}

@app.post("/ask", dependencies=[Depends(verify_token)])
def ask(body: AskIn):
    retrieved = search(SearchIn(query=body.question, collections=body.collections, tags=body.tags, limit=body.limit))
    # Use slim snippets to reduce tokens sent to the LLM (strip heavy metadata)
    try:
        from context_compression import slim_snippets
        snippets = slim_snippets(retrieved["results"])
    except ImportError:
        snippets = []
        for item in retrieved["results"]:
            payload = item["payload"]
            snippets.append({
                "text": payload.get("text", "")[:1500],
                "source": payload.get("title") or payload.get("path", ""),
                "score": round(item.get("final_score", 0), 3),
            })
    answer = run_chat_task(
        body.answer_task,
        [
            {"role": "system", "content": "Answer only from the provided retrieval context. If the context is insufficient, say so clearly. Cite relevant source paths in the answer."},
            {"role": "user", "content": json.dumps({"question": body.question, "context": snippets}, ensure_ascii=False)},
        ],
        max_tokens=1800,
    )
    return {"question": body.question, "answer": answer["text"], "answer_attempts": answer["attempts"], "results": retrieved["results"], "embedding_attempts": retrieved["embedding_attempts"]}

@app.post("/chat", dependencies=[Depends(verify_token)])
def chat(body: dict[str, Any]):
    task_type = body.get("task_type", "fallback_general")
    messages = body.get("messages", [])
    if not messages:
        raise HTTPException(status_code=400, detail="messages are required")
    return run_chat_task(task_type, messages, json_mode=bool(body.get("json_mode", False)))

@app.post("/ingest", dependencies=[Depends(verify_token)])
def ingest():
    rate_limiters.check("ingest")
    if _ingest_status["running"]:
        return {"ok": False, "detail": "ingestion already in progress", "status": _ingest_status}
    _ingest_status["running"] = True
    t = threading.Thread(target=_background_ingest, daemon=True)
    t.start()
    return {"ok": True, "detail": "ingestion started in background", "status_endpoint": "/ingest-status"}

@app.post("/ingest-sync", dependencies=[Depends(verify_token)])
def ingest_sync():
    return _run_ingest_sync()

@app.get("/ingest-status", dependencies=[Depends(verify_token)])
def ingest_status():
    return _ingest_status

@app.get("/cache-stats", dependencies=[Depends(verify_token)])
def cache_stats():
    return {"embedding_cache": embedding_cache.stats(), "llm_cache": llm_cache.stats()}

@app.get("/circuit-breakers", dependencies=[Depends(verify_token)])
def cb_status():
    return {"circuit_breakers": circuit_breakers.all_status()}

@app.get("/rate-limits", dependencies=[Depends(verify_token)])
def rl_status():
    return {"rate_limits": rate_limiters.all_status()}

@app.get("/token-stats", dependencies=[Depends(verify_token)])
def token_stats():
    return token_tracker.stats()

@app.post("/token-stats/reset", dependencies=[Depends(verify_token)])
def token_stats_reset():
    token_tracker.reset()
    return {"ok": True, "detail": "token stats reset"}

@app.post("/selftest", dependencies=[Depends(verify_token)])
def selftest():
    routes_view = safe_router_view(load_router())
    try:
        _, embed_attempts = embed_texts(["nanobot selftest"])
    except Exception as e:
        embed_attempts = [{"status": "error", "error": str(e)}]
    try:
        preview = route_preview_endpoint(RoutePreviewIn(task_type="final_answer"))
        route_ok = True
    except Exception as e:
        preview = {"error": str(e)}
        route_ok = False
    try:
        result = run_chat_task("classify_query", [{"role": "user", "content": "Classify: remind me what we decided about backups"}])
        chat_attempts = result["attempts"]
    except Exception as e:
        chat_attempts = [{"status": "error", "error": str(e)}]
    return {
        "ok": route_ok, "time": utcnow(),
        "routes": routes_view.get("task_routes", {}),
        "route_preview": preview,
        "embedding_attempts": embed_attempts,
        "chat_attempts": chat_attempts,
        "langfuse_enabled": bool(langfuse),
        "auth_enabled": bool(BRIDGE_TOKEN),
        "reranker": reranker_status(),
        "embedding_cache": embedding_cache.stats(),
        "circuit_breakers": circuit_breakers.all_status(),
        "sparse_vectors": SPARSE_VECTORS_ENABLED,
    }

# ---------------------------------------------------------------------------
# Mount streaming endpoints
# ---------------------------------------------------------------------------
try:
    from streaming import router as stream_router, set_dependencies as stream_set_deps
    stream_set_deps({
        "verify_token": verify_token,
        "run_chat_task": run_chat_task,
        "search_fn": _ext_search,
        "embed_fn": _ext_embed,
    })
    app.include_router(stream_router, dependencies=[Depends(verify_token)])
    logger.info("SSE streaming endpoints mounted (/smart-chat-stream)")
except Exception as exc:
    logger.warning("Failed to mount streaming endpoints: %s", exc)
