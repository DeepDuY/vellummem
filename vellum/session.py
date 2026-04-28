"""Session state management — mode sticky with DB persistence.

Design:
    - Key-value config table in SQLite (extensible, no schema changes for new keys)
    - Read-through cache: DB → memory on init, write-through on set
    - Existing property interface preserved for backward compatibility
"""

from __future__ import annotations

import typing

if typing.TYPE_CHECKING:
    from .db import VellumDB


class Session:
    """Holds the current conversation session state, persisted to DB.

    The AI controls mode via memory_set_mode().
    Mode is sticky — persisted to the config table so it survives restarts.

    Modes:
        human (default) — search human memory only
        code            — search project memory only
    """

    VALID_MODES = {"human", "code"}

    # ── Init ──────────────────────────────────────────────────

    def __init__(self, db: VellumDB | None = None):
        self._db = db
        self._cache: dict[str, str] = {}
        if db:
            self._load_from_db()
        else:
            self._cache = {
                "mode": "human",
                "project_id": "",
                "project_path": "",
            }

    def _load_from_db(self):
        """Load all config values from DB into in-memory cache."""
        if not self._db:
            return
        conn = self._db.connect()
        rows = conn.execute("SELECT key, value FROM config").fetchall()
        self._cache = {r["key"]: r["value"] for r in rows}
        # Ensure required keys have defaults
        for k, v in {"mode": "human", "project_id": "", "project_path": ""}.items():
            self._cache.setdefault(k, v)

    def _persist(self, key: str, value: str):
        """Write config value to DB and update in-memory cache."""
        self._cache[key] = value
        if not self._db:
            return
        conn = self._db.connect()
        conn.execute(
            """INSERT INTO config (key, value, type, updated_at)
               VALUES (?, ?, 'str', datetime('now','localtime'))
               ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value,
                   updated_at = datetime('now','localtime')""",
            (key, value)
        )
        conn.commit()

    # ── Generic accessor (for future extensibility) ───────────

    def get(self, key: str, default: str = "") -> str:
        """Read any config value from cache."""
        return self._cache.get(key, default)

    def set(self, key: str, value: str):
        """Write any config value to both cache and DB."""
        self._persist(key, value)

    # ── Mode ──────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        return self._cache.get("mode", "human")

    def set_mode(self, mode: str):
        """Switch mode. Persisted to DB — survives restarts."""
        if mode not in self.VALID_MODES:
            raise ValueError(f"Invalid mode: {mode}. Choose from {self.VALID_MODES}")
        self._persist("mode", mode)

    # ── Project ───────────────────────────────────────────────

    @property
    def project_id(self) -> str | None:
        v = self._cache.get("project_id", "")
        return v if v else None

    @property
    def project_path(self) -> str | None:
        v = self._cache.get("project_path", "")
        return v if v else None

    def set_project(self, project_id: str | None, project_path: str | None = None):
        self._persist("project_id", project_id or "")
        if project_path is not None:
            self._persist("project_path", project_path)

    # ── Lifecycle ─────────────────────────────────────────────

    def reset(self):
        """Reset to defaults in both cache and DB."""
        self._cache = {"mode": "human", "project_id": "", "project_path": ""}
        if self._db:
            conn = self._db.connect()
            conn.execute("DELETE FROM config")
            conn.commit()
            # Re-seed defaults
            conn.executemany(
                "INSERT INTO config (key, value, type, description) VALUES (?, ?, 'str', ?)",
                [("mode", "human", "当前检索模式: human / code"),
                 ("project_id", "", "当前绑定的项目 ID"),
                 ("project_path", "", "当前绑定的项目路径")]
            )
            conn.commit()

    # ── Status ────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "mode": self.mode,
            "project_id": self.project_id,
            "project_path": self.project_path,
        }

    def __repr__(self):
        return f"Session(mode={self.mode}, project={self.project_id})"
