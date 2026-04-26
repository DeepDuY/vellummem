"""Reflection store — cross-session synthesized insights."""

from __future__ import annotations

import json
import sqlite3
import typing
from datetime import datetime

if typing.TYPE_CHECKING:
    from ..db import VellumDB


class ReflectionStore:
    """Cross-session synthesized insights.

    A reflection is a high-level insight drawn from multiple sessions
    and patterns. Unlike patterns (which are behavioral), reflections
    are interpretive — they answer "what does this mean about the user?"
    """

    def __init__(self, db: VellumDB):
        self.db = db

    def add(self, insight: str, *,
            supporting_sessions: list[str] | None = None,
            supporting_patterns: list[str] | None = None,
            confidence: str = "mid",
            category: str = "综合洞察") -> dict:
        rid = f"ref_{_next_short_id()}"
        conn = self.db.connect()
        conn.execute("""
            INSERT INTO reflections
                (id, insight, supporting_sessions, supporting_patterns,
                 confidence, category, generated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            rid, insight,
            json.dumps(supporting_sessions or [], ensure_ascii=False),
            json.dumps(supporting_patterns or [], ensure_ascii=False),
            confidence, category,
            datetime.now().isoformat()[:10],
        ))
        conn.commit()
        return {"id": rid, "insight": insight}

    def get_by_id(self, rid: str) -> dict | None:
        conn = self.db.connect()
        row = conn.execute(
            "SELECT * FROM reflections WHERE id = ?", (rid,)
        ).fetchone()
        return _row_to_dict(row) if row else None

    def get_by_category(self, category: str) -> list[dict]:
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT * FROM reflections WHERE category = ? ORDER BY generated_at DESC",
            (category,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def search(self, keyword: str) -> list[dict]:
        conn = self.db.connect()
        pattern = f"%{keyword}%"
        rows = conn.execute("""
            SELECT * FROM reflections
            WHERE insight LIKE ? OR category LIKE ?
            ORDER BY generated_at DESC LIMIT 10
        """, (pattern, pattern)).fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_recent(self, limit: int = 10) -> list[dict]:
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT * FROM reflections ORDER BY generated_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def all(self) -> list[dict]:
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT * FROM reflections ORDER BY generated_at DESC"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for field in ("supporting_sessions", "supporting_patterns"):
        if isinstance(d.get(field), str):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


def _next_short_id() -> str:
    import random
    return f"{datetime.now().strftime('%H%M%S')}_{random.randint(1000, 9999)}"
