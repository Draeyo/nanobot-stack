"""RAG feedback loop — relevance boosting for search results.

Tracks which chunks were useful vs unhelpful and adjusts scoring
in future searches. Stored in a simple SQLite table alongside ingest.db.
"""
from __future__ import annotations
import logging, os, pathlib, sqlite3, threading
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("rag-bridge.feedback")
FEEDBACK_ENABLED = os.getenv("FEEDBACK_ENABLED", "true").lower() == "true"
FEEDBACK_BOOST_POSITIVE = float(os.getenv("FEEDBACK_BOOST_POSITIVE", "0.1"))
FEEDBACK_BOOST_NEGATIVE = float(os.getenv("FEEDBACK_BOOST_NEGATIVE", "-0.05"))
STATE_DIR = pathlib.Path(os.getenv("STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))

_lock = threading.Lock()

def _conn():
    db = sqlite3.connect(str(STATE_DIR / "feedback.db"))
    db.execute("""CREATE TABLE IF NOT EXISTS feedback (
        chunk_id TEXT NOT NULL,
        collection TEXT NOT NULL,
        query TEXT NOT NULL,
        signal TEXT NOT NULL CHECK(signal IN ('positive','negative')),
        created_at TEXT NOT NULL,
        PRIMARY KEY (chunk_id, query, signal)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS relevance_boost (
        chunk_id TEXT PRIMARY KEY,
        collection TEXT NOT NULL,
        boost REAL NOT NULL DEFAULT 0.0,
        positive_count INTEGER NOT NULL DEFAULT 0,
        negative_count INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    )""")
    return db

def record_feedback(chunk_id: str, collection: str, query: str, signal: str) -> dict[str, Any]:
    if not FEEDBACK_ENABLED or signal not in ("positive", "negative"):
        return {"recorded": False}
    now = datetime.now(timezone.utc).isoformat()
    boost_delta = FEEDBACK_BOOST_POSITIVE if signal == "positive" else FEEDBACK_BOOST_NEGATIVE
    with _lock:
        db = _conn()
        try:
            db.execute("INSERT OR IGNORE INTO feedback VALUES (?,?,?,?,?)",
                       (chunk_id, collection, query[:500], signal, now))
            db.execute("""INSERT INTO relevance_boost (chunk_id, collection, boost, positive_count, negative_count, updated_at)
                          VALUES (?, ?, ?, ?, ?, ?)
                          ON CONFLICT(chunk_id) DO UPDATE SET
                            boost = boost + ?,
                            positive_count = positive_count + ?,
                            negative_count = negative_count + ?,
                            updated_at = ?""",
                       (chunk_id, collection, boost_delta,
                        1 if signal == "positive" else 0,
                        1 if signal == "negative" else 0, now,
                        boost_delta,
                        1 if signal == "positive" else 0,
                        1 if signal == "negative" else 0, now))
            db.commit()
        finally:
            db.close()
    return {"recorded": True, "chunk_id": chunk_id, "signal": signal, "boost_delta": boost_delta}

def get_boost(chunk_id: str) -> float:
    if not FEEDBACK_ENABLED:
        return 0.0
    try:
        db = _conn()
        row = db.execute("SELECT boost FROM relevance_boost WHERE chunk_id = ?", (chunk_id,)).fetchone()
        db.close()
        return row[0] if row else 0.0
    except Exception:
        return 0.0

def get_boosts_batch(chunk_ids: list[str]) -> dict[str, float]:
    if not FEEDBACK_ENABLED or not chunk_ids:
        return {}
    try:
        db = _conn()
        placeholders = ",".join("?" for _ in chunk_ids)
        rows = db.execute(f"SELECT chunk_id, boost FROM relevance_boost WHERE chunk_id IN ({placeholders})", chunk_ids).fetchall()
        db.close()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}

def feedback_stats() -> dict[str, Any]:
    if not FEEDBACK_ENABLED:
        return {"enabled": False}
    try:
        db = _conn()
        total = db.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        positive = db.execute("SELECT COUNT(*) FROM feedback WHERE signal='positive'").fetchone()[0]
        negative = db.execute("SELECT COUNT(*) FROM feedback WHERE signal='negative'").fetchone()[0]
        boosted = db.execute("SELECT COUNT(*) FROM relevance_boost WHERE boost != 0").fetchone()[0]
        db.close()
        return {"enabled": True, "total_signals": total, "positive": positive, "negative": negative, "chunks_with_boost": boosted}
    except Exception as exc:
        return {"enabled": True, "error": str(exc)}


def apply_feedback_boosts(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply stored relevance boosts to reranked search results."""
    if not FEEDBACK_ENABLED or not results:
        return results
    chunk_ids = [str(r.get("id", "")) for r in results if r.get("id")]
    boosts = get_boosts_batch(chunk_ids)
    if not boosts:
        return results
    for row in results:
        cid = str(row.get("id", ""))
        fb = boosts.get(cid, 0.0)
        if fb != 0.0:
            row["feedback_boost"] = fb
            row["final_score"] = row.get("final_score", 0.0) + fb
    results.sort(key=lambda x: x.get("final_score", 0.0), reverse=True)
    return results
