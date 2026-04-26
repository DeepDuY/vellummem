"""Decision Hub — cross-domain bidirectional linking.

Stores only links between human-side and code-side memories,
not the memory data itself. Enables hybrid-mode cross-domain jumps:

    Timeline (human) ←→ Decision (code)
    Semantic  (human) ←→ File Map (code)
"""

from __future__ import annotations

import typing
from datetime import datetime

if typing.TYPE_CHECKING:
    from .db import VellumDB


class DecisionHub:
    """Cross-domain link registry.

    Each link connects one human-side record to one code-side record,
    enabling bidirectional navigation in hybrid mode.
    """

    VALID_HUMAN_TYPES = {"timeline", "semantic"}
    VALID_CODE_TYPES = {"decision", "file_map"}
    VALID_LINK_TYPES = {"决策来源", "代码体现", "问题引发", "关联"}

    def __init__(self, db: VellumDB):
        self.db = db

    def link(self, human_source_type: str, human_source_id: str,
             code_source_type: str, code_source_id: str,
             link_type: str = "关联", rationale: str = "") -> dict:
        """Create a bidirectional link between human and code memory.

        Args:
            human_source_type: "timeline" | "semantic"
            human_source_id: ID of the human-side record (e.g. session_id)
            code_source_type: "decision" | "file_map"
            code_source_id: ID of the code-side record (e.g. decision_id)
            link_type: 决策来源 | 代码体现 | 问题引发 | 关联
            rationale: Why this link exists (optional)

        Returns:
            dict with link details (or existing link if duplicate)
        """
        self._validate(human_source_type, code_source_type, link_type)

        lid = f"link_{_next_short_id()}"
        conn = self.db.connect()
        try:
            conn.execute("""
                INSERT INTO decision_hub
                    (id, human_source_type, human_source_id,
                     code_source_type, code_source_id,
                     link_type, rationale)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (lid, human_source_type, human_source_id,
                  code_source_type, code_source_id,
                  link_type, rationale))
            conn.commit()
            return {"id": lid, "link_type": link_type, "is_new": True}
        except Exception:
            # UNIQUE constraint — link already exists
            conn.rollback()
            existing = conn.execute("""
                SELECT * FROM decision_hub
                WHERE human_source_type = ? AND human_source_id = ?
                  AND code_source_type = ? AND code_source_id = ?
            """, (human_source_type, human_source_id,
                  code_source_type, code_source_id)).fetchone()
            return _row_to_dict(existing) if existing else {"id": "", "is_new": False}

    def query_by_human(self, source_type: str, source_id: str) -> list[dict]:
        """From human side → find all linked code-side records."""
        conn = self.db.connect()
        rows = conn.execute("""
            SELECT * FROM decision_hub
            WHERE human_source_type = ? AND human_source_id = ?
            ORDER BY created_at DESC
        """, (source_type, source_id)).fetchall()
        return [_row_to_dict(r) for r in rows]

    def query_by_code(self, source_type: str, source_id: str) -> list[dict]:
        """From code side → find all linked human-side records."""
        conn = self.db.connect()
        rows = conn.execute("""
            SELECT * FROM decision_hub
            WHERE code_source_type = ? AND code_source_id = ?
            ORDER BY created_at DESC
        """, (source_type, source_id)).fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_by_id(self, lid: str) -> dict | None:
        conn = self.db.connect()
        row = conn.execute(
            "SELECT * FROM decision_hub WHERE id = ?", (lid,)
        ).fetchone()
        return _row_to_dict(row) if row else None

    def delete(self, lid: str):
        conn = self.db.connect()
        conn.execute("DELETE FROM decision_hub WHERE id = ?", (lid,))
        conn.commit()

    def search(self, keyword: str) -> list[dict]:
        """Search links by rationale or link_type."""
        conn = self.db.connect()
        pattern = f"%{keyword}%"
        rows = conn.execute("""
            SELECT * FROM decision_hub
            WHERE rationale LIKE ? OR link_type LIKE ?
               OR human_source_id LIKE ? OR code_source_id LIKE ?
            ORDER BY created_at DESC LIMIT 20
        """, (pattern, pattern, pattern, pattern)).fetchall()
        return [_row_to_dict(r) for r in rows]

    def _validate(self, human_type, code_type, link_type):
        if human_type not in self.VALID_HUMAN_TYPES:
            raise ValueError(f"Invalid human_source_type: {human_type}")
        if code_type not in self.VALID_CODE_TYPES:
            raise ValueError(f"Invalid code_source_type: {code_type}")
        if link_type not in self.VALID_LINK_TYPES:
            raise ValueError(f"Invalid link_type: {link_type}. Use one of {self.VALID_LINK_TYPES}")


def _row_to_dict(row) -> dict | None:
    if row is None:
        return None
    return dict(row)


def _next_short_id() -> str:
    import random
    return f"{datetime.now().strftime('%H%M%S')}_{random.randint(1000, 9999)}"
