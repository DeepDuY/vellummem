"""File map store — per-file index with path/name/symbol search."""

from __future__ import annotations

import json
import os
import sqlite3
import typing
from datetime import datetime

if typing.TYPE_CHECKING:
    from ..db import VellumDB


class FileMapStore:
    """Indexes project files for structured retrieval.

    Each row = one file.
    Supports path prefix queries (src/auth/*), module lookup,
    symbol name search, and FTS5 full-text search.
    """

    def __init__(self, db: VellumDB):
        self.db = db

    # ── Write ──────────────────────────────────────────────────

    def add_file(self, project_id: str, path: str, *,
                 module: str = "", summary: str = "",
                 key_symbols: list | None = None,
                 depends_on: list | None = None,
                 linked_decisions: list | None = None) -> dict:
        fid = _path_to_id(path)
        conn = self.db.connect()
        conn.execute("""
            INSERT OR REPLACE INTO file_map
                (id, project_id, path, module, summary,
                 key_symbols, depends_on, linked_decisions,
                 last_modified, change_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 
                    COALESCE((SELECT change_count + 1 FROM file_map WHERE id = ?), 1))
        """, (
            fid, project_id, path, module, summary,
            json.dumps(key_symbols or [], ensure_ascii=False),
            json.dumps(depends_on or [], ensure_ascii=False),
            json.dumps(linked_decisions or [], ensure_ascii=False),
            datetime.now().isoformat(timespec="seconds"),
            fid,
        ))
        conn.commit()
        return {"id": fid, "path": path}

    def update_file(self, path: str, *, project_id: str | None = None,
                    module: str | None = None, summary: str | None = None,
                    key_symbols: list | None = None,
                    linked_decisions: list | None = None):
        """Incremental update — only replaces provided fields."""
        fid = _path_to_id(path)
        sets = []
        values = []
        if project_id is not None:
            sets.append("project_id = ?"); values.append(project_id)
        if module is not None:
            sets.append("module = ?"); values.append(module)
        if summary is not None:
            sets.append("summary = ?"); values.append(summary)
        if key_symbols is not None:
            sets.append("key_symbols = ?")
            values.append(json.dumps(key_symbols, ensure_ascii=False))
        if linked_decisions is not None:
            sets.append("linked_decisions = ?")
            values.append(json.dumps(linked_decisions, ensure_ascii=False))
        if not sets:
            return
        sets.append("last_modified = ?")
        values.append(datetime.now().isoformat(timespec="seconds"))
        sets.append("change_count = change_count + 1")
        values.append(fid)
        conn = self.db.connect()
        conn.execute(f"UPDATE file_map SET {', '.join(sets)} WHERE id = ?", values)
        conn.commit()

    # ── Read ───────────────────────────────────────────────────

    def get_by_id(self, fid: str) -> dict | None:
        conn = self.db.connect()
        row = conn.execute("SELECT * FROM file_map WHERE id = ?", (fid,)).fetchone()
        return _row_to_dict(row) if row else None

    def get_by_path(self, path: str) -> dict | None:
        """Exact path match."""
        conn = self.db.connect()
        row = conn.execute(
            "SELECT * FROM file_map WHERE path = ?", (path,)
        ).fetchone()
        return _row_to_dict(row) if row else None

    def query_by_path_prefix(self, prefix: str) -> list[dict]:
        """e.g. query_by_path_prefix('src/auth/') → all auth files."""
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT * FROM file_map WHERE path LIKE ? ORDER BY path",
            (f"{prefix}%",)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def query_by_module(self, module: str, project_id: str | None = None) -> list[dict]:
        conn = self.db.connect()
        if project_id:
            rows = conn.execute(
                "SELECT * FROM file_map WHERE module = ? AND project_id = ? ORDER BY path",
                (module, project_id)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM file_map WHERE module = ? ORDER BY path", (module,)
            ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def query_by_symbol(self, symbol: str) -> list[dict]:
        """Search by exported function/class name."""
        conn = self.db.connect()
        pattern = f"%{symbol}%"
        rows = conn.execute(
            "SELECT * FROM file_map WHERE key_symbols LIKE ?", (pattern,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def query_by_decision(self, decision_id: str) -> list[dict]:
        """Reverse lookup: which files link to this decision."""
        conn = self.db.connect()
        pattern = f"%{decision_id}%"
        rows = conn.execute(
            "SELECT * FROM file_map WHERE linked_decisions LIKE ?", (pattern,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def query_by_project(self, project_id: str, limit: int = 100) -> list[dict]:
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT * FROM file_map WHERE project_id = ? ORDER BY path LIMIT ?",
            (project_id, limit)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def search(self, keyword: str) -> list[dict]:
        """FTS5 full-text search; fallback to LIKE."""
        conn = self.db.connect()
        try:
            rows = conn.execute("""
                SELECT fm.* FROM file_map fm
                JOIN file_map_fts fts ON fts.id = fm.id
                WHERE file_map_fts MATCH ?
                ORDER BY rank
                LIMIT 20
            """, (keyword,)).fetchall()
            return [_row_to_dict(r) for r in rows]
        except sqlite3.OperationalError:
            return self._like_fallback(keyword)

    def _like_fallback(self, keyword: str) -> list[dict]:
        conn = self.db.connect()
        pattern = f"%{keyword}%"
        rows = conn.execute("""
            SELECT * FROM file_map
            WHERE path LIKE ? OR module LIKE ? OR summary LIKE ?
            ORDER BY path LIMIT 20
        """, (pattern, pattern, pattern)).fetchall()
        return [_row_to_dict(r) for r in rows]

    # ── Scan ───────────────────────────────────────────────────

    def scan_directory(self, project_id: str, root_path: str,
                       extensions: set[str] | None = None) -> dict:
        """Walk the directory tree and index all source files.

        Args:
            project_id: Linked project ID.
            root_path: Absolute path to scan.
            extensions: Set of extensions to include (e.g. {'.py', '.ts'}).
                        None = all files.

        Returns:
            Stats dict with added/updated/total counts.
        """
        stats = {"added": 0, "updated": 0, "total": 0}
        for dirpath, _dirnames, filenames in os.walk(root_path):
            # Skip common ignore dirs
            rel = os.path.relpath(dirpath, root_path)
            if any(part.startswith((".", "_")) or part in ("node_modules", "__pycache__", "venv")
                   for part in rel.split(os.sep)):
                continue
            for fname in filenames:
                if extensions and not any(fname.endswith(ext) for ext in extensions):
                    continue
                full_path = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(full_path, root_path)
                # Simple module detection: first directory level
                parts = rel_path.replace("\\", "/").split("/")
                module = parts[0] if len(parts) > 1 else "root"
                try:
                    existing = self.get_by_path(rel_path)
                    if existing:
                        self.update_file(rel_path, project_id=project_id, module=module)
                        stats["updated"] += 1
                    else:
                        self.add_file(project_id, rel_path, module=module)
                        stats["added"] += 1
                    stats["total"] += 1
                except Exception:
                    pass  # skip problematic files
        return stats


def _path_to_id(path: str) -> str:
    """Convert file path to a stable ID."""
    norm = path.replace("\\", "/").replace(" ", "_")
    return f"file_{norm}"


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for field in ("key_symbols", "depends_on", "linked_decisions"):
        if isinstance(d.get(field), str):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                pass
    return d
