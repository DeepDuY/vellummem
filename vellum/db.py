"""Database connection and schema initialization."""

import pathlib
import sqlite3


class VellumDB:
    """SQLite database wrapper for VellumMem memory system."""

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

    # ── Initialization ─────────────────────────────────────────

    def initialize(self, schema_path: str | pathlib.Path | None = None):
        """Create all tables if they don't exist, run migrations.

        Args:
            schema_path: Path to schema.sql. Auto-detects if None.
        """
        if schema_path is None:
            schema_path = (
                pathlib.Path(__file__).resolve().parent.parent
                / "schemas" / "schema.sql"
            )
        if isinstance(schema_path, str):
            schema_path = pathlib.Path(schema_path)
        sql = schema_path.read_text(encoding="utf-8")
        conn = self.connect()
        conn.executescript(sql)
        conn.commit()
        self._migrate_config()
        self._migrate_human_timeline()

    def _migrate_config(self):
        """Upgrade config table from old 2-column to new 6-column schema;
        always ensure required defaults exist."""
        conn = self.connect()
        cols = [r["name"] for r in conn.execute(
            "PRAGMA table_info(config)"
        ).fetchall()]
        required = {"key", "value", "type", "description", "created_at", "updated_at"}
        if set(cols) != required:
            # Old 2-column schema → DROP + rebuild with old data preserved
            old_data = dict(
                conn.execute("SELECT key, value FROM config").fetchall()
            )
            conn.execute("DROP TABLE config")
            conn.execute("""
                CREATE TABLE config (
                    key         TEXT PRIMARY KEY,
                    value       TEXT NOT NULL DEFAULT '',
                    type        TEXT NOT NULL DEFAULT 'str',
                    description TEXT DEFAULT '',
                    created_at  TEXT DEFAULT (datetime('now','localtime')),
                    updated_at  TEXT DEFAULT (datetime('now','localtime'))
                )
            """)
            for k, v in old_data.items():
                conn.execute(
                    "INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)",
                    (k, v)
                )

        # 无论新库还是升级库，始终保证 2 条默认值存在
        defaults = [
            ("vector_engine",    "transformer", "str",   "向量引擎"),
            ("score_threshold",  "0.15",        "float", "向量检索最低匹配分数"),
        ]
        conn.executemany(
            "INSERT OR IGNORE INTO config (key, value, type, description) VALUES (?, ?, ?, ?)",
            defaults
        )
        conn.commit()

    def _migrate_human_timeline(self):
        """Add category/is_time_sensitive columns; rebuild table to drop unused columns."""
        conn = self.connect()
        cols_info = conn.execute("PRAGMA table_info(human_timeline)").fetchall()
        has_old_cols = any(c[1] in ("session_start", "session_end", "update_timestamp") for c in cols_info)
        if has_old_cols:
            # Rebuild table — drop session_start/session_end/update_timestamp
            conn.execute("""
                CREATE TABLE human_timeline_v7 (
                    id                        TEXT PRIMARY KEY,
                    summary                   TEXT DEFAULT '',
                    tags                      TEXT DEFAULT '[]',
                    conversation_context_link TEXT DEFAULT '[]',
                    category                  TEXT DEFAULT 'conversation',
                    is_time_sensitive         INTEGER DEFAULT 0,
                    create_timestamp          INTEGER NOT NULL
                )
            """)
            conn.execute("""
                INSERT INTO human_timeline_v7
                    (id, summary, tags, conversation_context_link,
                     category, is_time_sensitive, create_timestamp)
                SELECT id, summary, tags, conversation_context_link,
                       IFNULL(category, 'conversation'),
                       IFNULL(is_time_sensitive, 0), create_timestamp
                FROM human_timeline
            """)
            conn.execute("DROP TABLE human_timeline")
            conn.execute("ALTER TABLE human_timeline_v7 RENAME TO human_timeline")
            conn.commit()
            return

        # No old columns — just ensure category/is_time_sensitive exist
        existing = {r[1] for r in cols_info}
        if "category" not in existing:
            conn.execute("ALTER TABLE human_timeline ADD COLUMN category TEXT DEFAULT 'conversation'")
        if "is_time_sensitive" not in existing:
            conn.execute("ALTER TABLE human_timeline ADD COLUMN is_time_sensitive INTEGER DEFAULT 0")
        conn.commit()

    # ── Helpers ────────────────────────────────────────────────

    def table_count(self, table: str) -> int:
        """Quick row count for a table."""
        conn = self.connect()
        row = conn.execute(f"SELECT COUNT(*) as cnt FROM {table}").fetchone()
        return row["cnt"] if row else 0

    def stats(self) -> dict:
        """Return row counts for all main tables."""
        tables = [
            "human_timeline", "conversation_context",
            "memory_groups", "config",
        ]
        return {t: self.table_count(t) for t in tables}
