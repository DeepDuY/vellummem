"""Semantic store — entities and facts with version chain."""

from __future__ import annotations

import json
import sqlite3
import typing

if typing.TYPE_CHECKING:
    from ..db import VellumDB


class SemanticStore:
    """Entity registry + facts with temporal versioning.

    Entities store canonical names with aliases for fuzzy matching.
    Facts are subject-predicate-object triples with valid_from/valid_to
    and a previous_version chain for tracking changes over time.
    """

    def __init__(self, db: VellumDB):
        self.db = db

    # ════════════════════════════════════════════════════════════
    # Entities
    # ════════════════════════════════════════════════════════════

    def create_entity(self, name: str, *, aliases: list[str] | None = None,
                      type: str = "concept", importance: int = 1,
                      summary: str = "") -> dict:
        conn = self.db.connect()
        eid = f"entity_{name.lower().replace(' ', '_')}"
        aliases_json = json.dumps(aliases or [], ensure_ascii=False)
        conn.execute("""
            INSERT OR IGNORE INTO semantic_entities
                (id, name, aliases, type, importance, summary)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (eid, name, aliases_json, type, importance, summary))
        conn.commit()
        return {"id": eid, "name": name}

    def find_entity(self, name: str) -> dict | None:
        """Exact match by canonical name."""
        conn = self.db.connect()
        row = conn.execute(
            "SELECT * FROM semantic_entities WHERE name = ?", (name,)
        ).fetchone()
        return _row_to_dict(row) if row else None

    def find_entity_by_id(self, eid: str) -> dict | None:
        conn = self.db.connect()
        row = conn.execute(
            "SELECT * FROM semantic_entities WHERE id = ?", (eid,)
        ).fetchone()
        return _row_to_dict(row) if row else None

    def find_entity_fuzzy(self, fragment: str) -> list[dict]:
        """Fuzzy match: searches name and aliases."""
        conn = self.db.connect()
        pattern = f"%{fragment}%"
        rows = conn.execute("""
            SELECT * FROM semantic_entities
            WHERE name LIKE ?
               OR aliases LIKE ?
            ORDER BY importance DESC
            LIMIT 10
        """, (pattern, pattern)).fetchall()
        return [_row_to_dict(r) for r in rows]

    def list_entities(self, type: str | None = None) -> list[dict]:
        conn = self.db.connect()
        if type:
            rows = conn.execute(
                "SELECT * FROM semantic_entities WHERE type = ? ORDER BY importance DESC",
                (type,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM semantic_entities ORDER BY importance DESC"
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    # ════════════════════════════════════════════════════════════
    # Facts
    # ════════════════════════════════════════════════════════════

    def add_fact(self, entity_id: str, predicate: str, object_value: str,
                 *, confidence: str = "mid",
                 evidence_session: str | None = None,
                 valid_from: str | None = None,
                 tags: list[str] | None = None) -> dict:
        """Add a new fact. If same entity+predicate has an active fact,
        marks it expired (sets valid_to) and chains the new one.
        """
        conn = self.db.connect()
        fid = f"fact_{_next_short_id()}"

        # Expire any currently active fact for same entity+predicate
        existing = conn.execute("""
            SELECT id FROM semantic_facts
            WHERE entity_id = ? AND predicate = ? AND valid_to IS NULL
        """, (entity_id, predicate)).fetchone()

        previous = None
        if existing:
            previous = existing["id"]
            now = datetime.now().isoformat()[:10]
            conn.execute(
                "UPDATE semantic_facts SET valid_to = ? WHERE id = ?",
                (now, existing["id"])
            )

        tags_json = json.dumps(tags or [], ensure_ascii=False)
        conn.execute("""
            INSERT INTO semantic_facts
                (id, entity_id, predicate, object_value,
                 confidence, evidence_session,
                 valid_from, valid_to, previous_version, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
        """, (
            fid, entity_id, predicate, object_value,
            confidence, evidence_session,
            valid_from or datetime.now().isoformat()[:10],
            previous, tags_json,
        ))
        conn.commit()
        return {"id": fid, "entity_id": entity_id, "predicate": predicate}

    def update_fact(self, fact_id: str, new_value: str,
                    evidence_session: str | None = None) -> dict | None:
        """Update a fact by creating a new version (keeps history)."""
        old = self.get_fact(fact_id)
        if not old:
            return None
        return self.add_fact(
            entity_id=old["entity_id"],
            predicate=old["predicate"],
            object_value=new_value,
            confidence=old.get("confidence", "mid"),
            evidence_session=evidence_session or old.get("evidence_session"),
            valid_from=datetime.now().isoformat()[:10],
        )

    def invalidate(self, fact_id: str, until: str | None = None):
        """Mark a fact as no longer true."""
        conn = self.db.connect()
        conn.execute(
            "UPDATE semantic_facts SET valid_to = ? WHERE id = ?",
            (until or datetime.now().isoformat()[:10], fact_id)
        )
        conn.commit()

    def get_fact(self, fact_id: str) -> dict | None:
        conn = self.db.connect()
        row = conn.execute(
            "SELECT * FROM semantic_facts WHERE id = ?", (fact_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None

    def query_entity_facts(self, entity_id: str) -> list[dict]:
        """All facts for an entity (including expired)."""
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT * FROM semantic_facts WHERE entity_id = ? "
            "ORDER BY valid_from DESC",
            (entity_id,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def query_relation(self, entity_id: str, predicate: str) -> list[dict]:
        """Facts matching entity + predicate."""
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT * FROM semantic_facts WHERE entity_id = ? AND predicate = ? "
            "ORDER BY valid_from DESC",
            (entity_id, predicate)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def query_current_facts(self, entity_id: str | None = None) -> list[dict]:
        """Only currently valid facts (valid_to IS NULL)."""
        conn = self.db.connect()
        if entity_id:
            rows = conn.execute(
                "SELECT * FROM v_current_facts WHERE entity_id = ?",
                (entity_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM v_current_facts").fetchall()
        return [_row_to_dict(r) for r in rows]

    def search(self, keyword: str) -> list[dict]:
        """Basic keyword search across entity names, aliases, and facts."""
        conn = self.db.connect()
        pattern = f"%{keyword}%"
        rows = conn.execute("""
            SELECT sf.*, se.name as entity_name
            FROM semantic_facts sf
            JOIN semantic_entities se ON se.id = sf.entity_id
            WHERE se.name LIKE ?
               OR se.aliases LIKE ?
               OR sf.object_value LIKE ?
               OR sf.predicate LIKE ?
            ORDER BY sf.valid_from DESC
            LIMIT 20
        """, (pattern, pattern, pattern, pattern)).fetchall()
        return [_row_to_dict(r) for r in rows]


from datetime import datetime


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for field in ("aliases", "tags"):
        if isinstance(d.get(field), str):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


def _next_short_id() -> str:
    import random
    return f"{datetime.now().strftime('%H%M%S')}_{random.randint(1000, 9999)}"
