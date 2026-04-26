"""Timeline store — append-only session log."""

from __future__ import annotations

import json
import sqlite3
import typing
from datetime import datetime

if typing.TYPE_CHECKING:
    from ..db import VellumDB


class TimelineStore:
    """Append-only log of conversation sessions.

    One entry per conversation session. Immutable after creation.
    """

    def __init__(self, db: VellumDB):
        self.db = db

    # ── Write ──────────────────────────────────────────────────

    def create(self, *, mode: str = "hybrid", project_id: str | None = None,
               summary: str, key_moments: list | None = None,
               tags: list | None = None,
               final_state: str = "",
               linked_decisions: list | None = None,
               linked_entities: list | None = None,
               importance: int = 3) -> dict:
        """Insert a new timeline entry (append-only)."""
        session_id = _next_id("s")
        now = datetime.now().isoformat(timespec="seconds")

        key_moments_json = json.dumps(key_moments or [], ensure_ascii=False)
        tags_json = json.dumps(tags or [], ensure_ascii=False)
        linked_decisions_json = json.dumps(linked_decisions or [], ensure_ascii=False)
        linked_entities_json = json.dumps(linked_entities or [], ensure_ascii=False)

        conn = self.db.connect()
        conn.execute("""
            INSERT INTO timeline
                (id, mode, project_id, session_start, session_end,
                 summary, key_moments, tags, final_state,
                 linked_decisions, linked_entities, importance)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            session_id, mode, project_id, now, now,
            summary, key_moments_json, tags_json, final_state,
            linked_decisions_json, linked_entities_json, importance,
        ))
        conn.commit()

        return {"id": session_id, "summary": summary, "created_at": now}

    # ── Read ───────────────────────────────────────────────────

    def get_by_id(self, session_id: str) -> dict | None:
        conn = self.db.connect()
        row = conn.execute(
            "SELECT * FROM timeline WHERE id = ?", (session_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None

    def list_recent(self, limit: int = 20) -> list[dict]:
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT * FROM timeline ORDER BY session_start DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def query_by_time(self, start: str, end: str) -> list[dict]:
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT * FROM timeline WHERE session_start >= ? AND session_start <= ? "
            "ORDER BY session_start DESC",
            (start, end)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def query_by_tags(self, tags: list[str]) -> list[dict]:
        """Find sessions that contain ANY of the given tags."""
        conn = self.db.connect()
        placeholders = ",".join("?" * len(tags))
        rows = conn.execute(f"""
            SELECT DISTINCT t.* FROM timeline t
            WHERE EXISTS (
                SELECT 1 FROM json_each(t.tags)
                WHERE value IN ({placeholders})
            )
            ORDER BY t.session_start DESC
        """, tags).fetchall()
        return [_row_to_dict(r) for r in rows]

    def query_by_keyword(self, keyword: str) -> list[dict]:
        """Keyword search via LIKE (P1, no FTS5 dependency)."""
        conn = self.db.connect()
        pattern = f"%{keyword}%"
        rows = conn.execute(
            "SELECT * FROM timeline WHERE summary LIKE ? OR tags LIKE ? "
            "ORDER BY session_start DESC LIMIT 20",
            (pattern, pattern)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def search(self, query: str) -> list[dict]:
        """Combined search entry point."""
        return self.query_by_keyword(query)

    # ── Update ─────────────────────────────────────────────────

    def update_session_end(self, session_id: str):
        """Mark the session end time (at conversation close)."""
        now = datetime.now().isoformat(timespec="seconds")
        conn = self.db.connect()
        conn.execute(
            "UPDATE timeline SET session_end = ? WHERE id = ?",
            (now, session_id)
        )
        conn.commit()


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for field in ("key_moments", "tags", "linked_decisions", "linked_entities"):
        if isinstance(d.get(field), str):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


def _next_id(prefix: str = "s") -> str:
    """Generate a unique ID like 's_20260426_001'."""
    import random
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    rnd = random.randint(100, 999)
    return f"{prefix}_{ts}_{rnd}"
