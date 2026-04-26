"""Pattern store — discovers behavioral patterns from Timeline entries."""

from __future__ import annotations

import json
import sqlite3
import typing
from datetime import datetime

if typing.TYPE_CHECKING:
    from ..db import VellumDB


class PatternStore:
    """Behavioral patterns discovered from multiple Timeline entries.

    A pattern represents a recurring behavior or tendency observed
    across multiple conversation sessions. Patterns are generated
    by consolidation tasks and improve in confidence over time.
    """

    def __init__(self, db: VellumDB):
        self.db = db

    def add_or_merge(self, description: str, *,
                     detail: str = "",
                     evidence_sessions: list[str] | None = None,
                     trigger_topics: list[str] | None = None,
                     category: str = "行为模式") -> dict:
        """Add a new pattern or merge with an existing one.

        If a pattern with the same description exists, merges evidence
        and increases confidence.
        """
        existing = self._find_by_description(description)
        if existing:
            return self._merge(existing, evidence_sessions, trigger_topics)

        pid = f"pat_{_next_short_id()}"
        conn = self.db.connect()
        conn.execute("""
            INSERT INTO patterns
                (id, description, detail, evidence_sessions, confidence,
                 trigger_topics, category)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            pid, description, detail,
            json.dumps(evidence_sessions or [], ensure_ascii=False),
            0.3,  # initial confidence
            json.dumps(trigger_topics or [], ensure_ascii=False),
            category,
        ))
        conn.commit()
        return {"id": pid, "description": description, "is_new": True}

    def get_by_id(self, pid: str) -> dict | None:
        conn = self.db.connect()
        row = conn.execute(
            "SELECT * FROM patterns WHERE id = ?", (pid,)
        ).fetchone()
        return _row_to_dict(row) if row else None

    def get_active(self, min_confidence: float = 0.3) -> list[dict]:
        """Return patterns with at least the given confidence."""
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT * FROM patterns WHERE confidence >= ? ORDER BY confidence DESC",
            (min_confidence,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def search_by_topic(self, topic: str) -> list[dict]:
        """Find patterns relevant to a topic."""
        conn = self.db.connect()
        pattern = f"%{topic}%"
        rows = conn.execute("""
            SELECT * FROM patterns
            WHERE description LIKE ?
               OR detail LIKE ?
               OR trigger_topics LIKE ?
            ORDER BY confidence DESC
            LIMIT 10
        """, (pattern, pattern, pattern)).fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_by_category(self, category: str) -> list[dict]:
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT * FROM patterns WHERE category = ? ORDER BY confidence DESC",
            (category,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def all(self) -> list[dict]:
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT * FROM patterns ORDER BY confidence DESC"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def _find_by_description(self, description: str) -> dict | None:
        conn = self.db.connect()
        row = conn.execute(
            "SELECT * FROM patterns WHERE description = ?", (description,)
        ).fetchone()
        return _row_to_dict(row) if row else None

    def _merge(self, existing: dict, new_sessions: list[str] | None,
               new_topics: list[str] | None) -> dict:
        """Merge new evidence into an existing pattern, increasing confidence."""
        conn = self.db.connect()
        pid = existing["id"]

        # Merge sessions (deduplicate)
        sessions = set(existing.get("evidence_sessions", []))
        if new_sessions:
            sessions.update(new_sessions)

        # Merge topics (deduplicate)
        topics = set(existing.get("trigger_topics", []))
        if new_topics:
            topics.update(new_topics)

        # Increase confidence (diminishing returns)
        old_confidence = existing.get("confidence", 0.3)
        new_confidence = min(0.95, old_confidence + 0.15)

        conn.execute("""
            UPDATE patterns
            SET evidence_sessions = ?, trigger_topics = ?,
                confidence = ?, updated_at = ?
            WHERE id = ?
        """, (
            json.dumps(list(sessions), ensure_ascii=False),
            json.dumps(list(topics), ensure_ascii=False),
            new_confidence,
            datetime.now().isoformat(timespec="seconds"),
            pid,
        ))
        conn.commit()
        return {"id": pid, "description": existing["description"], "is_new": False}


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for field in ("evidence_sessions", "trigger_topics"):
        if isinstance(d.get(field), str):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


def _next_short_id() -> str:
    import random
    return f"{datetime.now().strftime('%H%M%S')}_{random.randint(1000, 9999)}"
