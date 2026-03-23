"""Lightweight knowledge graph stored in SQLite.

Tracks entities and relationships extracted from memories and conversations.
Enables relational queries like "What decisions were made about project X?"
or "What does person Y work on?"
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("rag-bridge.knowledge_graph")

KG_ENABLED = os.getenv("KNOWLEDGE_GRAPH_ENABLED", "true").lower() == "true"
STATE_DIR = pathlib.Path(os.getenv("STATE_DIR", "/opt/nanobot-stack/rag-bridge/state"))
DB_PATH = STATE_DIR / "knowledge_graph.db"

_lock = threading.Lock()

EXTRACT_ENTITIES_PROMPT = """Extract entities and relationships from this text.

Return ONLY JSON:
{
  "entities": [
    {"name": "entity name", "type": "person|project|technology|concept|organization|decision", "description": "brief description"}
  ],
  "relations": [
    {"source": "entity1", "relation": "works_on|decided|uses|depends_on|related_to|created|manages", "target": "entity2", "context": "brief context"}
  ]
}

Only extract clear, factual relationships. Skip vague or uncertain ones.
Text: {text}"""


def _get_conn() -> sqlite3.Connection:
    """Get a SQLite connection with schema initialization."""
    db = sqlite3.connect(str(DB_PATH))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS entities (
        name TEXT PRIMARY KEY,
        type TEXT NOT NULL,
        description TEXT DEFAULT '',
        mention_count INTEGER DEFAULT 1,
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS relations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        relation TEXT NOT NULL,
        target TEXT NOT NULL,
        context TEXT DEFAULT '',
        strength REAL DEFAULT 1.0,
        created_at TEXT NOT NULL,
        UNIQUE(source, relation, target)
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_relations_source ON relations(source)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_relations_target ON relations(target)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type)")
    return db


def extract_and_store(text: str, run_chat_fn) -> dict[str, Any]:
    """Extract entities and relations from text and store them in the graph."""
    if not KG_ENABLED:
        return {"stored": False, "reason": "knowledge graph disabled"}

    try:
        result = run_chat_fn("structured_extraction", [
            {"role": "user", "content": EXTRACT_ENTITIES_PROMPT.format(text=text[:3000])},
        ], json_mode=True, max_tokens=800)
        data = json.loads(result.get("text", "{}"))
    except Exception as exc:
        logger.warning("KG extraction failed: %s", exc)
        return {"stored": False, "error": str(exc)}

    entities = data.get("entities", [])
    relations = data.get("relations", [])

    if not entities and not relations:
        return {"stored": False, "reason": "no entities or relations found"}

    now = datetime.now(timezone.utc).isoformat()
    stored_entities = 0
    stored_relations = 0

    with _lock:
        db = _get_conn()
        try:
            for ent in entities:
                name = ent.get("name", "").strip().lower()
                etype = ent.get("type", "concept")
                desc = ent.get("description", "")
                if not name:
                    continue
                db.execute("""INSERT INTO entities (name, type, description, mention_count, first_seen, last_seen)
                              VALUES (?, ?, ?, 1, ?, ?)
                              ON CONFLICT(name) DO UPDATE SET
                                mention_count = mention_count + 1,
                                last_seen = ?,
                                description = CASE WHEN length(excluded.description) > length(entities.description)
                                              THEN excluded.description ELSE entities.description END""",
                           (name, etype, desc, now, now, now))
                stored_entities += 1

            for rel in relations:
                src = rel.get("source", "").strip().lower()
                tgt = rel.get("target", "").strip().lower()
                rtype = rel.get("relation", "related_to")
                ctx = rel.get("context", "")
                if not src or not tgt:
                    continue
                db.execute("""INSERT INTO relations (source, relation, target, context, strength, created_at)
                              VALUES (?, ?, ?, ?, 1.0, ?)
                              ON CONFLICT(source, relation, target) DO UPDATE SET
                                strength = strength + 0.5,
                                context = CASE WHEN length(excluded.context) > length(relations.context)
                                          THEN excluded.context ELSE relations.context END""",
                           (src, rtype, tgt, ctx, now))
                stored_relations += 1

            db.commit()
        finally:
            db.close()

    return {"stored": True, "entities": stored_entities, "relations": stored_relations}


def query_entity(name: str, depth: int = 1) -> dict[str, Any]:
    """Query the graph for an entity and its relationships."""
    if not KG_ENABLED:
        return {"found": False, "reason": "knowledge graph disabled"}

    name_lower = name.strip().lower()
    with _lock:
        db = _get_conn()
        try:
            # Fuzzy name match
            ent = db.execute(
                "SELECT name, type, description, mention_count, first_seen, last_seen FROM entities WHERE name LIKE ?",
                (f"%{name_lower}%",)
            ).fetchone()
            if not ent:
                return {"found": False}

            entity_info = {
                "name": ent[0], "type": ent[1], "description": ent[2],
                "mention_count": ent[3], "first_seen": ent[4], "last_seen": ent[5],
            }

            # Outgoing relations
            outgoing = db.execute(
                "SELECT relation, target, context, strength FROM relations WHERE source LIKE ? ORDER BY strength DESC LIMIT 20",
                (f"%{name_lower}%",)
            ).fetchall()

            # Incoming relations
            incoming = db.execute(
                "SELECT source, relation, context, strength FROM relations WHERE target LIKE ? ORDER BY strength DESC LIMIT 20",
                (f"%{name_lower}%",)
            ).fetchall()

            relations_out = [{"relation": r[0], "target": r[1], "context": r[2], "strength": r[3]} for r in outgoing]
            relations_in = [{"source": r[0], "relation": r[1], "context": r[2], "strength": r[3]} for r in incoming]

            return {
                "found": True,
                "entity": entity_info,
                "outgoing_relations": relations_out,
                "incoming_relations": relations_in,
            }
        finally:
            db.close()


def query_relations(entity1: str, entity2: str) -> dict[str, Any]:
    """Find all relationships between two entities."""
    if not KG_ENABLED:
        return {"found": False}

    e1 = entity1.strip().lower()
    e2 = entity2.strip().lower()
    with _lock:
        db = _get_conn()
        try:
            rows = db.execute(
                """SELECT source, relation, target, context, strength FROM relations
                   WHERE (source LIKE ? AND target LIKE ?) OR (source LIKE ? AND target LIKE ?)
                   ORDER BY strength DESC""",
                (f"%{e1}%", f"%{e2}%", f"%{e2}%", f"%{e1}%")
            ).fetchall()
            return {
                "found": len(rows) > 0,
                "relations": [{"source": r[0], "relation": r[1], "target": r[2], "context": r[3], "strength": r[4]} for r in rows],
            }
        finally:
            db.close()


def get_stats() -> dict[str, Any]:
    """Return knowledge graph statistics."""
    if not KG_ENABLED:
        return {"enabled": False}
    with _lock:
        db = _get_conn()
        try:
            entity_count = db.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
            relation_count = db.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
            type_counts = dict(db.execute("SELECT type, COUNT(*) FROM entities GROUP BY type").fetchall())
            top_entities = db.execute(
                "SELECT name, type, mention_count FROM entities ORDER BY mention_count DESC LIMIT 10"
            ).fetchall()
            return {
                "enabled": True,
                "entities": entity_count,
                "relations": relation_count,
                "entity_types": type_counts,
                "top_entities": [{"name": e[0], "type": e[1], "mentions": e[2]} for e in top_entities],
            }
        except Exception as exc:
            return {"enabled": True, "error": str(exc)}
        finally:
            db.close()
