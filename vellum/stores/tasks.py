"""Task context store — tracks ongoing work across sessions."""

from __future__ import annotations

import json
import sqlite3
import typing
from datetime import datetime

if typing.TYPE_CHECKING:
    from ..db import VellumDB


class TaskStore:
    """Tracks task progress across multiple conversations."""

    def __init__(self, db: VellumDB):
        self.db = db

    def create(self, title: str, project_id: str, *,
               status: str = "planned",
               progress_detail: str = "",
               related_sessions: list | None = None,
               related_files: list | None = None,
               blockers: list | None = None,
               next_action: str = "") -> dict:
        tid = f"task_{_next_short_id()}"
        conn = self.db.connect()
        conn.execute("""
            INSERT INTO tasks
                (id, project_id, title, status, progress_detail,
                 related_sessions, related_files, blockers, next_action)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            tid, project_id, title, status, progress_detail,
            json.dumps(related_sessions or [], ensure_ascii=False),
            json.dumps(related_files or [], ensure_ascii=False),
            json.dumps(blockers or [], ensure_ascii=False),
            next_action,
        ))
        conn.commit()
        return {"id": tid, "title": title}

    def get_by_id(self, tid: str) -> dict | None:
        conn = self.db.connect()
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
        return _row_to_dict(row) if row else None

    def update(self, tid: str, **kwargs):
        """Update fields: status, progress_pct, progress_detail, blockers, next_action."""
        allowed = {"status", "progress_pct", "progress_detail",
                   "blockers", "next_action"}
        sets = []
        values = []
        for key, val in kwargs.items():
            if key in allowed:
                sets.append(f"{key} = ?")
                values.append(json.dumps(val, ensure_ascii=False) if isinstance(val, list) else val)
        if not sets:
            return
        sets.append("updated_at = ?")
        values.append(datetime.now().isoformat(timespec="seconds"))
        values.append(tid)
        conn = self.db.connect()
        conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", values)
        conn.commit()

    def get_active(self, project_id: str | None = None) -> list[dict]:
        """Return tasks that are planned, wip, or blocked."""
        conn = self.db.connect()
        if project_id:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status IN ('planned','wip','blocked') "
                "AND project_id = ? ORDER BY updated_at DESC",
                (project_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM v_active_tasks ORDER BY updated_at DESC"
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def query_by_file(self, file_id: str) -> list[dict]:
        conn = self.db.connect()
        pattern = f"%{file_id}%"
        rows = conn.execute(
            "SELECT * FROM tasks WHERE related_files LIKE ? ORDER BY updated_at DESC",
            (pattern,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def list_by_project(self, project_id: str) -> list[dict]:
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT * FROM tasks WHERE project_id = ? ORDER BY updated_at DESC",
            (project_id,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def add_session(self, tid: str, session_id: str):
        """Link a session to this task."""
        task = self.get_by_id(tid)
        if not task:
            return
        sessions = task.get("related_sessions", [])
        if session_id not in sessions:
            sessions.append(session_id)
            conn = self.db.connect()
            conn.execute(
                "UPDATE tasks SET related_sessions = ?, updated_at = ? WHERE id = ?",
                (json.dumps(sessions, ensure_ascii=False),
                 datetime.now().isoformat(timespec="seconds"), tid)
            )
            conn.commit()


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for field in ("related_sessions", "related_files", "blockers"):
        if isinstance(d.get(field), str):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


def _next_short_id() -> str:
    import random
    return f"{datetime.now().strftime('%H%M%S')}_{random.randint(1000, 9999)}"
