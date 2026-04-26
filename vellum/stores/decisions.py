"""Decision log store — one record per architectural decision."""

from __future__ import annotations

import json
import sqlite3
import typing
from datetime import datetime

if typing.TYPE_CHECKING:
    from ..db import VellumDB


class DecisionStore:
    """Architectural decision records with file-level reverse indexing."""

    def __init__(self, db: VellumDB):
        self.db = db

    def create(self, title: str, *, body: str = "",
               project_id: str | None = None,
               alternatives: list | None = None,
               affected_files: list | None = None,
               linked_session: str | None = None,
               tags: list | None = None,
               status: str = "planned") -> dict:
        did = f"dec_{_next_short_id()}"
        conn = self.db.connect()
        conn.execute("""
            INSERT INTO decisions
                (id, project_id, title, body, alternatives,
                 affected_files, linked_session, tags, status, made_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            did, project_id, title, body,
            json.dumps(alternatives or [], ensure_ascii=False),
            json.dumps(affected_files or [], ensure_ascii=False),
            linked_session,
            json.dumps(tags or [], ensure_ascii=False),
            status,
            datetime.now().isoformat()[:10],
        ))
        conn.commit()
        return {"id": did, "title": title}

    def get_by_id(self, did: str) -> dict | None:
        conn = self.db.connect()
        row = conn.execute("SELECT * FROM decisions WHERE id = ?", (did,)).fetchone()
        return _row_to_dict(row) if row else None

    def query_by_file(self, filepath: str) -> list[dict]:
        """Find decisions that mention this file path."""
        conn = self.db.connect()
        pattern = f"%{filepath}%"
        rows = conn.execute(
            "SELECT * FROM decisions WHERE affected_files LIKE ? "
            "ORDER BY made_at DESC", (pattern,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def query_by_tag(self, tag: str) -> list[dict]:
        conn = self.db.connect()
        pattern = f"%{tag}%"
        rows = conn.execute(
            "SELECT * FROM decisions WHERE tags LIKE ? ORDER BY made_at DESC",
            (pattern,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def query_by_session(self, session_id: str) -> list[dict]:
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT * FROM decisions WHERE linked_session = ? ORDER BY made_at DESC",
            (session_id,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def query_by_project(self, project_id: str) -> list[dict]:
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT * FROM decisions WHERE project_id = ? ORDER BY made_at DESC",
            (project_id,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def update_status(self, did: str, status: str):
        conn = self.db.connect()
        conn.execute("UPDATE decisions SET status = ? WHERE id = ?", (status, did))
        conn.commit()

    def search(self, keyword: str) -> list[dict]:
        conn = self.db.connect()
        pattern = f"%{keyword}%"
        rows = conn.execute("""
            SELECT * FROM decisions
            WHERE title LIKE ? OR body LIKE ? OR tags LIKE ?
            ORDER BY made_at DESC LIMIT 20
        """, (pattern, pattern, pattern)).fetchall()
        return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for field in ("alternatives", "affected_files", "tags"):
        if isinstance(d.get(field), str):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


def _next_short_id() -> str:
    import random
    return f"{datetime.now().strftime('%H%M%S')}_{random.randint(1000, 9999)}"
