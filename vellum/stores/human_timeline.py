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
               session_start: str | None = None,
               context_text: str | None = None) -> dict:
        """创建一条 human_timeline 记录。

        Args:
            summary: 会话摘要（上限 200 字）
            tags: 5 个主题标签（强制 len==5，否则报错）
            session_start: ISO datetime
            context_text: 初始上下文原文

        Raises:
            ValueError: tags 不足 5 个时抛出
        """
        # 强制校验 5 个 tag
        if not tags or len(tags) != 5:
            raise ValueError(
                f"memory_write 必须提供 5 个 tag，当前 {len(tags) if tags else 0} 个"
            )

        hid = _next_human_id()
        now = _now_ms()
        start = session_start or time.strftime("%Y-%m-%dT%H:%M:%S")

        conn = self.db.connect()
        conn.execute("""
            INSERT INTO human_timeline
                (id, session_start, session_end, summary,
                 tags, conversation_context_link,
                 create_timestamp, update_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            hid, start, start,
            (summary or "")[:200],
            json.dumps(tags, ensure_ascii=False),
            "[]",
            now, now,
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
        now = _now_ms()
        conn.execute("""
            UPDATE human_timeline
            SET conversation_context_link = ?, update_timestamp = ?
            WHERE id = ?
        """, (json.dumps(link, ensure_ascii=False), now, timeline_id))
        conn.commit()

    def update_session_end(self, timeline_id: str):
        """标记会话结束时间。"""
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        ts = _now_ms()
        conn = self.db.connect()
        conn.execute("""
            UPDATE human_timeline
            SET session_end = ?, update_timestamp = ?
            WHERE id = ?
        """, (now, ts, timeline_id))
        conn.commit()

    # ── 读取 ──────────────────────────────────────────────────

    def get_by_id(self, tid: str) -> dict | None:
        conn = self.db.connect()
        row = conn.execute(
            "SELECT * FROM human_timeline WHERE id = ?", (tid,)
        ).fetchone()
        return _row_to_dict(row) if row else None

    def list_recent(self, limit: int = 20) -> list[dict]:
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT * FROM human_timeline ORDER BY create_timestamp DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

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
