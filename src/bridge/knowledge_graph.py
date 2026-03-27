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
_v10_migrated = False

# ---------------------------------------------------------------------------
# Sub-projet L: optional field-level encryption
# ---------------------------------------------------------------------------
_kg_encryptor = None  # set by app.py via set_encryptor()

ENCRYPTION_SQLITE_ENABLED = (
    os.getenv("ENCRYPTION_SQLITE_ENABLED",
              os.getenv("ENCRYPTION_ENABLED", "false")).lower() == "true"
)


def set_encryptor(encryptor) -> None:  # type: ignore[type-arg]
    """Inject the FieldEncryptor instance. Called by app.py at startup."""
    global _kg_encryptor  # pylint: disable=global-statement
    _kg_encryptor = encryptor


def _enc(value: str) -> str:
    """Encrypt value if encryption is enabled and encryptor is set."""
    if ENCRYPTION_SQLITE_ENABLED and _kg_encryptor is not None:
        return _kg_encryptor.encrypt_field(value)
    return value


def _dec(value: str) -> str:
    """Decrypt value if encryptor is set (transparent passthrough for plaintext)."""
    if _kg_encryptor is not None:
        return _kg_encryptor.decrypt_field(value)
    return value

ENTITY_TYPES = ["person", "project", "technology", "concept", "organization",
                 "decision", "event", "deadline", "location", "tool", "workflow", "preference"]
RELATION_TYPES = ["works_on", "decided", "uses", "depends_on", "related_to", "created",
                  "manages", "scheduled_for", "blocked_by", "prefers", "replaced_by",
                  "part_of", "owns"]

_ENTITY_TYPES_STR = "|".join(ENTITY_TYPES)
_RELATION_TYPES_STR = "|".join(RELATION_TYPES)

EXTRACT_ENTITIES_PROMPT = (
    "Extract entities and relationships from this text.\n\n"
    "Return ONLY JSON:\n"
    "{\n"
    '  "entities": [\n'
    f'    {{"name": "entity name", "type": "{_ENTITY_TYPES_STR}", "description": "brief description"}}\n'
    "  ],\n"
    '  "relations": [\n'
    f'    {{"source": "entity1", "relation": "{_RELATION_TYPES_STR}", "target": "entity2", "context": "brief context"}}\n'
    "  ]\n"
    "}\n\n"
    "Only extract clear, factual relationships. Skip vague or uncertain ones.\n"
    "Text: {text}"
)


def _get_conn() -> sqlite3.Connection:
    """Get a SQLite connection with schema initialization."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
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
    # v10 schema additions — run once per process
    global _v10_migrated  # pylint: disable=global-statement
    if not _v10_migrated:
        for col, spec in [
            ("updated_at", "TEXT DEFAULT ''"),
            ("confidence", "REAL DEFAULT 1.0"),
            ("source", "TEXT DEFAULT 'conversation'"),
            ("aliases", "TEXT DEFAULT '[]'"),
        ]:
            try:
                db.execute(f"ALTER TABLE entities ADD COLUMN {col} {spec}")
            except sqlite3.OperationalError:
                pass  # column already exists
        for col, spec in [
            ("last_confirmed", "TEXT DEFAULT ''"),
            ("source", "TEXT DEFAULT 'conversation'"),
            ("confidence", "REAL DEFAULT 1.0"),
        ]:
            try:
                db.execute(f"ALTER TABLE relations ADD COLUMN {col} {spec}")
            except sqlite3.OperationalError:
                pass
        _v10_migrated = True
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
        return {"stored": False, "error": "extraction failed"}

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
                if etype not in ENTITY_TYPES:
                    etype = "concept"
                db.execute("""INSERT INTO entities (name, type, description, mention_count, first_seen, last_seen, updated_at)
                              VALUES (?, ?, ?, 1, ?, ?, ?)
                              ON CONFLICT(name) DO UPDATE SET
                                mention_count = mention_count + 1,
                                last_seen = ?,
                                updated_at = ?,
                                description = CASE WHEN length(excluded.description) > length(entities.description)
                                              THEN excluded.description ELSE entities.description END""",
                           (name, etype, _enc(desc), now, now, now, now, now))
                stored_entities += 1

            for rel in relations:
                src = rel.get("source", "").strip().lower()
                tgt = rel.get("target", "").strip().lower()
                rtype = rel.get("relation", "related_to")
                ctx = rel.get("context", "")
                if not src or not tgt:
                    continue
                if rtype not in RELATION_TYPES:
                    rtype = "related_to"
                db.execute("""INSERT INTO relations (source, relation, target, context, strength, created_at, last_confirmed)
                              VALUES (?, ?, ?, ?, 1.0, ?, ?)
                              ON CONFLICT(source, relation, target) DO UPDATE SET
                                strength = strength + 0.5,
                                last_confirmed = ?,
                                context = CASE WHEN length(excluded.context) > length(relations.context)
                                          THEN excluded.context ELSE relations.context END""",
                           (src, rtype, tgt, ctx, now, now, now))
                stored_relations += 1

            db.commit()
        finally:
            db.close()

    return {"stored": True, "entities": stored_entities, "relations": stored_relations}


def query_entity(name: str, depth: int = 1) -> dict[str, Any]:  # pylint: disable=unused-argument
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
                "name": ent[0], "type": ent[1], "description": _dec(ent[2]),
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


def merge_entity(name1: str, name2: str) -> dict[str, Any]:
    """Merge two entities into one, combining mentions and relations."""
    if not KG_ENABLED:
        return {"merged": False, "reason": "knowledge graph disabled"}
    n1, n2 = name1.strip().lower(), name2.strip().lower()
    with _lock:
        db = _get_conn()
        try:
            e1 = db.execute("SELECT * FROM entities WHERE name = ?", (n1,)).fetchone()
            e2 = db.execute("SELECT * FROM entities WHERE name = ?", (n2,)).fetchone()
            if not e1 or not e2:
                return {"merged": False, "reason": "one or both entities not found"}
            # Keep e1, merge e2 into it
            db.execute("UPDATE entities SET mention_count = mention_count + ?, last_seen = ? WHERE name = ?",
                       (e2[3], datetime.now(timezone.utc).isoformat(), n1))
            # Update aliases
            try:
                aliases = json.loads(e1[6] if len(e1) > 6 and e1[6] else "[]")
            except (json.JSONDecodeError, IndexError):
                aliases = []
            if n2 not in aliases:
                aliases.append(n2)
            db.execute("UPDATE entities SET aliases = ? WHERE name = ?", (json.dumps(aliases), n1))
            # Redirect relations
            db.execute("UPDATE OR IGNORE relations SET source = ? WHERE source = ?", (n1, n2))
            db.execute("UPDATE OR IGNORE relations SET target = ? WHERE target = ?", (n1, n2))
            db.execute("DELETE FROM relations WHERE source = ? OR target = ?", (n2, n2))
            db.execute("DELETE FROM entities WHERE name = ?", (n2,))
            db.commit()
            return {"merged": True, "kept": n1, "removed": n2}
        finally:
            db.close()


def query_by_type(entity_type: str, limit: int = 50) -> list[dict[str, Any]]:
    """Get all entities of a given type."""
    if not KG_ENABLED:
        return []
    with _lock:
        db = _get_conn()
        try:
            rows = db.execute(
                "SELECT name, type, description, mention_count, first_seen, last_seen FROM entities WHERE type = ? ORDER BY mention_count DESC LIMIT ?",
                (entity_type, limit)
            ).fetchall()
            return [{"name": r[0], "type": r[1], "description": _dec(r[2]), "mention_count": r[3], "first_seen": r[4], "last_seen": r[5]} for r in rows]
        finally:
            db.close()


def temporal_query(entity: str, start_date: str = "", end_date: str = "") -> dict[str, Any]:
    """Find relations involving an entity within a time period."""
    if not KG_ENABLED:
        return {"found": False}
    name = entity.strip().lower()
    with _lock:
        db = _get_conn()
        try:
            query = """SELECT source, relation, target, context, strength, created_at FROM relations
                       WHERE (source LIKE ? OR target LIKE ?)"""
            params: list[Any] = [f"%{name}%", f"%{name}%"]
            if start_date:
                query += " AND created_at >= ?"
                params.append(start_date)
            if end_date:
                query += " AND created_at <= ?"
                params.append(end_date)
            query += " ORDER BY created_at DESC LIMIT 50"
            rows = db.execute(query, params).fetchall()
            return {
                "found": len(rows) > 0,
                "relations": [{"source": r[0], "relation": r[1], "target": r[2], "context": r[3], "strength": r[4], "created_at": r[5]} for r in rows],
            }
        finally:
            db.close()


def get_subgraph(entity: str, depth: int = 2) -> dict[str, Any]:
    """Multi-hop graph traversal from an entity."""
    if not KG_ENABLED:
        return {"entities": [], "relations": []}
    name = entity.strip().lower()
    visited: set[str] = set()
    all_entities: list[dict[str, Any]] = []
    all_relations: list[dict[str, Any]] = []
    frontier = {name}

    with _lock:
        db = _get_conn()
        try:
            for _ in range(depth):
                if not frontier:
                    break
                next_frontier: set[str] = set()
                for node in frontier:
                    if node in visited:
                        continue
                    visited.add(node)
                    ent = db.execute("SELECT name, type, description, mention_count FROM entities WHERE name = ?", (node,)).fetchone()
                    if ent:
                        all_entities.append({"name": ent[0], "type": ent[1], "description": _dec(ent[2]), "mention_count": ent[3]})
                    rels = db.execute(
                        "SELECT source, relation, target, context, strength FROM relations WHERE source = ? OR target = ?",
                        (node, node)
                    ).fetchall()
                    for r in rels:
                        rel_dict = {"source": r[0], "relation": r[1], "target": r[2], "context": r[3], "strength": r[4]}
                        if rel_dict not in all_relations:
                            all_relations.append(rel_dict)
                        neighbor = r[2] if r[0] == node else r[0]
                        if neighbor not in visited:
                            next_frontier.add(neighbor)
                frontier = next_frontier
            return {"entities": all_entities, "relations": all_relations}
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
        except Exception:
            return {"enabled": True, "error": "stats query failed"}
        finally:
            db.close()
