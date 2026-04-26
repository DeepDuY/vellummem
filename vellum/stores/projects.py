"""Project card store — one record per project."""

from __future__ import annotations

import json
import sqlite3
import typing

if typing.TYPE_CHECKING:
    from ..db import VellumDB


class ProjectStore:
    """One row per project, stores high-level metadata."""

    def __init__(self, db: VellumDB):
        self.db = db

    def create(self, name: str, root_path: str, *,
               tech_stack: list | None = None,
               main_modules: list | None = None,
               active_branch: str = "",
               description: str = "") -> dict:
        pid = f"proj_{name.lower().replace(' ', '_')}"
        conn = self.db.connect()
        conn.execute("""
            INSERT OR REPLACE INTO projects
                (id, name, root_path, tech_stack, main_modules,
                 active_branch, description, last_scanned)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            pid, name, root_path,
            json.dumps(tech_stack or [], ensure_ascii=False),
            json.dumps(main_modules or [], ensure_ascii=False),
            active_branch, description,
            datetime.now().isoformat(timespec="seconds"),
        ))
        conn.commit()
        return {"id": pid, "name": name}

    def get_by_id(self, pid: str) -> dict | None:
        conn = self.db.connect()
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
        return _row_to_dict(row) if row else None

    def get_by_path(self, root_path: str) -> dict | None:
        conn = self.db.connect()
        row = conn.execute(
            "SELECT * FROM projects WHERE root_path = ?", (root_path,)
        ).fetchone()
        return _row_to_dict(row) if row else None

    def update(self, pid: str, **kwargs):
        """Update fields: tech_stack, main_modules, active_branch, description."""
        allowed = {"tech_stack", "main_modules", "active_branch", "description"}
        sets = []
        values = []
        for key, val in kwargs.items():
            if key in allowed:
                sets.append(f"{key} = ?")
                values.append(json.dumps(val, ensure_ascii=False) if isinstance(val, list) else val)
        if not sets:
            return
        sets.append("last_scanned = ?")
        values.append(datetime.now().isoformat(timespec="seconds"))
        values.append(pid)
        conn = self.db.connect()
        conn.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id = ?", values)
        conn.commit()

    def list_all(self) -> list[dict]:
        conn = self.db.connect()
        rows = conn.execute("SELECT * FROM projects").fetchall()
        return [_row_to_dict(r) for r in rows]


from datetime import datetime


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for field in ("tech_stack", "main_modules"):
        if isinstance(d.get(field), str):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                pass
    return d
