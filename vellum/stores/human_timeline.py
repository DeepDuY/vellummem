"""Human Timeline store — conversation summary + context chunk management.

Design:
  - human_timeline: 会话摘要卡片（sumamry, tags, key_moments）
  - conversation_context: 按分片存储的会话原文（8000字符/片）
  - chunking: 按自然分隔符（标题、代码块、列表、段落）切分
  - conversation_context_link: 有序的上下文 ID 数组，只增不删
"""

from __future__ import annotations

import json
import random
import sqlite3
import string
import time
import typing

if typing.TYPE_CHECKING:
    from ..db import VellumDB

from ..errors import StoreError

# ── 分隔符（按优先级从高到低） ────────────────────────────────

CONTEXT_SEPARATORS = [
    "\n## ",
    "\n### ",
    "\n#### ",
    "\n##### ",
    "\n###### ",
    "\n```",
    "\n- ",
    "\n* ",
    "\n1. ",
    "\n> ",
    "\n\n***\n\n",
    "\n\n---\n\n",
    "\n\n___\n\n",
    "\n\n",
    "\n",
    " ",
]

CHUNK_SIZE = 8000  # 单片字符上限


# ── 工具函数 ──────────────────────────────────────────────────

def _next_human_id() -> str:
    """生成 ID: YYYYMMDD_HHMMSS_5RANDOM"""
    ts = time.strftime("%Y%m%d_%H%M%S")
    rand = "".join(random.choices(string.ascii_letters + string.digits, k=5))
    return f"{ts}_{rand}"


def _now_ms() -> int:
    return int(time.time() * 1000)


def chunk_text(text: str) -> list[str]:
    """按分隔符自然分片，每片不超过 CHUNK_SIZE 字符。"""
    if not text:
        return []
    if len(text) <= CHUNK_SIZE:
        return [text]

    chunks = []
    remaining = text

    while len(remaining) > CHUNK_SIZE:
        split_pos = -1
        for sep in CONTEXT_SEPARATORS:
            pos = remaining.rfind(sep, 0, CHUNK_SIZE)
            if pos > 0:
                split_pos = pos
                break
        if split_pos > 0:
            chunks.append(remaining[:split_pos])
            remaining = remaining[split_pos:]
        else:
            chunks.append(remaining[:CHUNK_SIZE])
            remaining = remaining[CHUNK_SIZE:]

    if remaining:
        chunks.append(remaining)

    return chunks


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for field in ("tags", "conversation_context_link"):
        if isinstance(d.get(field), str):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


# ── Store 类 ──────────────────────────────────────────────────

class HumanTimelineStore:
    """人类会话摘要 + 上下文分片管理。"""

    def __init__(self, db: VellumDB):
        self.db = db

    # ── 写入 ──────────────────────────────────────────────────

    def create(self, *, summary: str = "",
               tags: list | None = None,
               context_text: str | None = None,
               category: str,
               is_time_sensitive: bool = False,
               ttl_timestamp: int | None = None) -> dict:
        """创建一条 human_timeline 记录。

        Args:
            summary: 会话摘要（上限 200 字）
            tags: 5 个主题标签（强制 len==5，否则报错）
            context_text: 初始上下文原文
            category: 记忆类型（conversation/knowledge/document/preference/other）
            is_time_sensitive: 内容是否随时间可能失效
            ttl_timestamp: 可选，过期时间戳（ms）。is_time_sensitive=True 且未传时自动计算

        Raises:
            ValueError: tags 不足 5 个时抛出
            ValueError: category 无效时抛出
        """
        valid_categories = {"conversation", "knowledge", "document", "preference", "other"}
        if category not in valid_categories:
            raise StoreError(
                f"无效 category: {category}，可选 {valid_categories}"
            )

        # 强制校验 5 个 tag
        if not tags or len(tags) != 5:
            raise StoreError(
                f"memory_write 必须提供 5 个 tag，当前 {len(tags) if tags else 0} 个"
            )

        hid = _next_human_id()
        now = _now_ms()

        # 自动计算 TTL
        if is_time_sensitive and ttl_timestamp is None:
            import os as _os
            days = int(_os.environ.get("VELLUM_DEFAULT_TTL_DAYS", "3"))
            ttl_timestamp = now + days * 86400 * 1000

        conn = self.db.connect()
        conn.execute("""
            INSERT INTO human_timeline
                (id, summary, tags, conversation_context_link,
                 category, is_time_sensitive, create_timestamp, ttl_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            hid,
            (summary or "")[:200],
            json.dumps(tags, ensure_ascii=False),
            "[]",
            category, int(is_time_sensitive),
            now, ttl_timestamp,
        ))
        conn.commit()

        ctx_ids = []
        if context_text:
            ctx_ids = self._write_context_chunks(conn, context_text, hid)
            if ctx_ids:
                self._extend_link(conn, hid, ctx_ids)

        return {"id": hid, "context_ids": ctx_ids}

    def append_context(self, timeline_id: str, context_text: str) -> dict:
        """追加上下文分片到已有 timeline。"""
        conn = self.db.connect()
        entry = self.get_by_id(timeline_id)
        if not entry:
            return {"error": f"Timeline {timeline_id} not found"}

        ctx_ids = self._write_context_chunks(conn, context_text, timeline_id)
        if ctx_ids:
            self._extend_link(conn, timeline_id, ctx_ids)

        return {"id": timeline_id, "new_context_ids": ctx_ids}

    def _write_context_chunks(self, conn, text: str, timeline_id: str) -> list[str]:
        """分片并写入 conversation_context 表。"""
        chunks = chunk_text(text)
        ctx_ids = []
        for idx, chunk in enumerate(chunks):
            cid = _next_human_id()
            conn.execute("""
                INSERT INTO conversation_context (id, timeline_id, context, chunk_index, create_timestamp)
                VALUES (?, ?, ?, ?, ?)
            """, (cid, timeline_id, chunk, idx, _now_ms()))
            ctx_ids.append(cid)
        conn.commit()
        return ctx_ids

    def _extend_link(self, conn, timeline_id: str, new_ids: list[str]):
        """在 conversation_context_link 末尾追加新 ID。"""
        row = conn.execute(
            "SELECT conversation_context_link FROM human_timeline WHERE id = ?",
            (timeline_id,)
        ).fetchone()
        if not row:
            return
        link = json.loads(row["conversation_context_link"] or "[]")
        link.extend(new_ids)
        conn.execute("""
            UPDATE human_timeline
            SET conversation_context_link = ?
            WHERE id = ?
        """, (json.dumps(link, ensure_ascii=False), timeline_id))
        conn.commit()

    # ── 读取 ──────────────────────────────────────────────────

    def get_by_id(self, tid: str) -> dict | None:
        conn = self.db.connect()
        row = conn.execute(
            "SELECT * FROM human_timeline WHERE id = ?", (tid,)
        ).fetchone()
        return _row_to_dict(row) if row else None

    # ── 上下文读取 ────────────────────────────────────────────

    def get_context_chunks(self, timeline_id: str,
                           offset: int = 0, limit: int = 1) -> list[dict]:
        """获取上下文分片，从最新的开始返回。

        Args:
            timeline_id: human_timeline ID
            offset: 0=最新分片, 1=次新, ...
            limit: 最多返回几片

        Returns:
            list[dict] — 每个 dict 含 id, context, create_timestamp
        """
        entry = self.get_by_id(timeline_id)
        if not entry:
            return []
        link = entry.get("conversation_context_link", [])
        if not link:
            return []
        # 最新在前
        link_rev = list(reversed(link))
        selected = link_rev[offset:offset + limit]
        if not selected:
            return []

        conn = self.db.connect()
        placeholders = ",".join("?" * len(selected))
        rows = conn.execute(
            f"SELECT * FROM conversation_context WHERE id IN ({placeholders})",
            selected
        ).fetchall()
        result_map = {r["id"]: dict(r) for r in rows}
        # 保持 selected 顺序
        return [result_map[cid] for cid in selected if cid in result_map]

    # ── 删除 ──────────────────────────────────────────────────

    def delete(self, entry_id: str) -> bool:
        """删除一条记忆（级联清除上下文 + 向量）。同时从分组中移除。"""
        conn = self.db.connect()
        entry = _row_to_dict(conn.execute(
            "SELECT id FROM human_timeline WHERE id = ?", (entry_id,)
        ).fetchone())
        if not entry:
            return False

        # 从 memory_groups 移除
        self._remove_from_groups(conn, entry_id)

        # 删除（级联到 conversation_context + entry_vectors）
        conn.execute("DELETE FROM human_timeline WHERE id = ?", (entry_id,))
        conn.commit()
        return True

    # ── 更新 ──────────────────────────────────────────────────

    def update(self, entry_id: str, *, summary: str | None = None,
               tags: list | None = None,
               category: str | None = None,
               is_time_sensitive: bool | None = None,
               context_text: str | None = None) -> dict:
        """更新一条记忆的部分字段。返回更新结果及 vector_changed 标志。

        Returns:
            {"id": str, "context_ids": [str], "vector_changed": bool}
            或 {"error": str}
        """
        entry = self.get_by_id(entry_id)
        if not entry:
            return {"error": f"Timeline {entry_id} not found"}

        import os as _os
        sets: list[str] = []
        params: list = []
        now = _now_ms()
        vector_changed = False

        if summary is not None:
            sets.append("summary = ?")
            params.append(summary[:200])
            vector_changed = True
        if tags is not None:
            if len(tags) != 5:
                return {"error": "tags must be exactly 5"}
            sets.append("tags = ?")
            params.append(json.dumps(tags, ensure_ascii=False))
            vector_changed = True
        if category is not None:
            valid = {"conversation", "knowledge", "document", "preference", "other"}
            if category not in valid:
                return {"error": f"invalid category: {category}"}
            sets.append("category = ?")
            params.append(category)
        if is_time_sensitive is not None:
            sets.append("is_time_sensitive = ?")
            params.append(int(is_time_sensitive))
            if is_time_sensitive:
                days = int(_os.environ.get("VELLUM_DEFAULT_TTL_DAYS", "3"))
                sets.append("ttl_timestamp = ?")
                params.append(now + days * 86400 * 1000)
            else:
                sets.append("ttl_timestamp = ?")
                params.append(None)

        ctx_ids: list[str] = []
        if context_text is not None:
            conn = self.db.connect()
            # 清旧分片
            conn.execute("DELETE FROM conversation_context WHERE timeline_id = ?", (entry_id,))
            # 写新分片
            ctx_ids = self._write_context_chunks(conn, context_text, entry_id)
            sets.append("conversation_context_link = ?")
            params.append(json.dumps(ctx_ids, ensure_ascii=False))

        if not sets:
            return {"id": entry_id, "context_ids": ctx_ids, "vector_changed": False}

        params.append(entry_id)
        conn = self.db.connect()
        conn.execute(
            f"UPDATE human_timeline SET {', '.join(sets)} WHERE id = ?",
            params
        )
        conn.commit()
        return {"id": entry_id, "context_ids": ctx_ids, "vector_changed": vector_changed}

    # ── TTL 清理 ──────────────────────────────────────────────

    def cleanup_expired(self) -> int:
        """删除所有已过期的 time-sensitive 条目。返回删除数量。"""
        conn = self.db.connect()
        now = _now_ms()
        expired = conn.execute(
            "SELECT id FROM human_timeline WHERE ttl_timestamp IS NOT NULL AND ttl_timestamp <= ?",
            (now,)
        ).fetchall()
        ids = [r["id"] for r in expired]
        if not ids:
            return 0
        for eid in ids:
            self._remove_from_groups(conn, eid)
            conn.execute("DELETE FROM human_timeline WHERE id = ?", (eid,))
        conn.commit()
        import sys as _sys
        _sys.stderr.write(f"[VellumMem] cleanup: removed {len(ids)} expired entries\n")
        _sys.stderr.flush()
        return len(ids)

    # ── 去重辅助 ────────────────────────────────────────────────

    def get_time_sensitive_ids(self) -> set[str]:
        """返回所有标记为 time_sensitive 且尚未过期的 entry_id 集合。"""
        conn = self.db.connect()
        now = _now_ms()
        rows = conn.execute(
            "SELECT id FROM human_timeline "
            "WHERE is_time_sensitive = 1 "
            "AND (ttl_timestamp IS NULL OR ttl_timestamp > ?)",
            (now,)
        ).fetchall()
        return {r["id"] for r in rows}

    def mark_as_time_sensitive(self, entry_id: str) -> bool:
        """将指定条目标记为 time_sensitive 并设置 TTL。"""
        import os as _os
        days = int(_os.environ.get("VELLUM_DEFAULT_TTL_DAYS", "3"))
        ttl = _now_ms() + days * 86400 * 1000
        conn = self.db.connect()
        conn.execute(
            "UPDATE human_timeline SET is_time_sensitive = 1, ttl_timestamp = ? WHERE id = ?",
            (ttl, entry_id)
        )
        conn.commit()
        return True

    # ── 内部工具 ──────────────────────────────────────────────

    @staticmethod
    def _remove_from_groups(conn, entry_id: str):
        """从所有分组的 entry_ids 中移除 entry_id。"""
        groups = conn.execute("SELECT * FROM memory_groups").fetchall()
        for g in groups:
            members = json.loads(g["entry_ids"])
            if entry_id in members:
                members.remove(entry_id)
                if members:
                    conn.execute(
                        "UPDATE memory_groups SET entry_ids = ?, member_count = ? WHERE id = ?",
                        (json.dumps(members, ensure_ascii=False), len(members), g["id"])
                    )
                else:
                    conn.execute("DELETE FROM memory_groups WHERE id = ?", (g["id"],))
