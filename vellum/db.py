"""Database connection and schema initialization."""

import pathlib
import sqlite3


class VellumDB:
    """SQLite database wrapper for VellumMem memory system.

    Usage:
        db = VellumDB("vellum.db")
        db.initialize()  # creates tables if not exist
        with db.connect() as conn:
            conn.execute("SELECT ...")
    """

    def __init__(self, db_path: str = "vellum.db"):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    # ── Connection ─────────────────────────────────────────────

    def connect(self) -> sqlite3.Connection:
        """Get or create a persistent connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Initialization ─────────────────────────────────────────

    def initialize(self, schema_path: str | pathlib.Path | None = None):
        """Create all tables if they don't exist.

        Args:
            schema_path: Path to schema.sql. Auto-detects if None.
        """
        if schema_path is None:
            schema_path = (
                pathlib.Path(__file__).resolve().parent.parent
                / "schema.sql"
            )
        if isinstance(schema_path, str):
            schema_path = pathlib.Path(schema_path)
        sql = schema_path.read_text(encoding="utf-8")
        conn = self.connect()
        conn.executescript(sql)
        conn.commit()

    def is_initialized(self) -> bool:
        """Check if the database has been initialized."""
        conn = self.connect()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='config'"
        ).fetchone()
        return row is not None

    # ── Helpers ────────────────────────────────────────────────

    def table_count(self, table: str) -> int:
        """Quick row count for a table."""
        conn = self.connect()
        row = conn.execute(f"SELECT COUNT(*) as cnt FROM {table}").fetchone()
        return row["cnt"] if row else 0

    def stats(self) -> dict:
        """Return row counts for all main tables."""
        tables = [
            "timeline", "semantic_entities", "semantic_facts",
            "patterns", "reflections", "projects",
            "file_map", "decisions", "tasks", "decision_hub",
        ]
        return {t: self.table_count(t) for t in tables}
