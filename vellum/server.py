"""VellumMem MCP Server — entry point.

v6: Human-only mode. No code/project stores, no mode dispatch.
     Memory groups via CPM (k=3) built at startup.
"""

from __future__ import annotations

import functools
import json
import os
import threading

from fastmcp import FastMCP

from .db import VellumDB
from .stores.human_timeline import HumanTimelineStore
from .vector.adapter import create_vector_adapter
from .groups import GroupManager
from .errors import VellumMemError

# ── Global state (lazy-initialized on first tool call) ─────────

_db: VellumDB | None = None
_vector = None
_stores: dict | None = None
_groups: GroupManager | None = None

_init_lock = threading.Lock()


def _log_call(msg: str):
    """Log a message to stderr (init phase or runtime tool call)."""
    import sys
    sys.stderr.write(f"[VellumMem] {msg}\n")
    sys.stderr.flush()


def _tool(fn):
    """Decorator: thread-safe init check + runtime call log.

    Apply as the INNER decorator (below @mcp.tool()).

        @mcp.tool()
        @_tool
        def my_tool(...):
            ...
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        _ensure_init()
        if _vector is None:
            return json.dumps({
                "error": "VellumMem failed to initialize. Check server logs."
            })
        _log_call(fn.__name__)
        try:
            return fn(*args, **kwargs)
        except VellumMemError as e:
            return json.dumps({"error": str(e)})
        except Exception as e:
            return json.dumps({"error": f"{type(e).__name__}: {e}"})
    return wrapper


def _ensure_init():
    """Thread-safe lazy initialization (double-checked locking)."""
    global _db, _vector, _stores, _groups
    if _vector is not None:
        return
    with _init_lock:
        if _vector is not None:
            return

        _db = _vector = None
        _stores = None
        _groups = None
        try:
            _log_call("INIT 1/4 Resolving project root & db_path...")
            _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            _default_db = os.path.join(_project_root, "vellum.db")
            db_path = os.environ.get("VELLUM_DB_PATH", _default_db)
            _log_call(f"   db_path={db_path}")

            _log_call("INIT 2/4 Connecting to DB & applying schema...")
            tmp_db = VellumDB(db_path)
            tmp_db.initialize()

            _log_call("INIT 3/4 Initializing vector adapter...")
            vector = create_vector_adapter(tmp_db)
            _log_call(f"   engine={vector.engine}  entries={len(vector.all_vectors)}")

            _log_call("INIT 4/4 Creating stores & building groups...")
            ht = HumanTimelineStore(tmp_db)
            stores = {"human_timeline": ht}
            grp = GroupManager(tmp_db, vector)
            # 从 config 表读取分组参数
            cfg = tmp_db.connect()
            row_th = cfg.execute(
                "SELECT value FROM config WHERE key = ?", ("group_threshold",)
            ).fetchone()
            group_threshold = float(row_th["value"]) if row_th else 0.45
            row_k = cfg.execute(
                "SELECT value FROM config WHERE key = ?", ("group_k",)
            ).fetchone()
            group_k = int(row_k["value"]) if row_k else 4
            result = grp.build_groups(k=group_k, threshold=group_threshold)
            _log_call(f"   k={group_k} th={group_threshold} → {result.get('groups_built', 0)} groups")

            _db = tmp_db
            _vector = vector
            _stores = stores
            _groups = grp

            _log_call("INIT ✅ Initialization complete")

        except Exception:
            _db = _vector = _stores = _groups = None
            import traceback
            traceback.print_exc()
            _log_call("INIT ❌ FAILED")


def _read_config(key: str, default: str = "") -> str:
    """从 config 表读取配置值（环境变量优先）。"""
    env_key = f"VELLUM_{key.upper()}"
    env_val = os.environ.get(env_key)
    if env_val is not None:
        return env_val
    try:
        row = _db.connect().execute(
            "SELECT value FROM config WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default
    except Exception:
        return default


def _start_daemon():
    """启动后台守护线程：定时 TTL 清理 + 去重扫描。"""
    import sys as _sys
    import time as _time

    def _loop():
        while True:
            interval = int(float(_read_config("daemon_interval", "1800")))
            _time.sleep(interval)

            try:
                # ── 1. TTL 清理 ──
                conn = _db.connect()
                now = int(__import__("time").time() * 1000)
                expired = conn.execute(
                    "SELECT id FROM human_timeline "
                    "WHERE ttl_timestamp IS NOT NULL AND ttl_timestamp <= ?",
                    (now,)
                ).fetchall()
                expired_ids = [r["id"] for r in expired]
                if expired_ids:
                    removed = _stores["human_timeline"].cleanup_expired()
                    if removed > 0 and _vector:
                        for eid in expired_ids:
                            _vector.remove(eid)
                    _sys.stderr.write(
                        f"[VellumMem Daemon] cleanup: removed {removed} expired entries\n"
                    )
                    _sys.stderr.flush()
            except Exception as e:
                _sys.stderr.write(f"[VellumMem Daemon] cleanup error: {e}\n")
                _sys.stderr.flush()

            try:
                # ── 2. 去重扫描 ──
                enable = _read_config("dedup_enable", "false")
                if enable.lower() == "true" and _vector:
                    threshold = float(_read_config("dedup_threshold", "0.9"))
                    skip = _stores["human_timeline"].get_time_sensitive_ids()
                    dupes = _vector.scan_duplicates(threshold=threshold, skip_ids=skip)
                    for d in dupes:
                        _stores["human_timeline"].mark_as_time_sensitive(d["duplicate"])
                        _sys.stderr.write(
                            f"[VellumMem Daemon] dedup: {d['duplicate']} ~ "
                            f"{d['keeper']} ({d['score']})\n"
                        )
                        _sys.stderr.flush()
            except Exception as e:
                _sys.stderr.write(f"[VellumMem Daemon] dedup error: {e}\n")
                _sys.stderr.flush()

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    _sys.stderr.write("[VellumMem Daemon] started\n")
    _sys.stderr.flush()


# ── MCP Server ────────────────────────────────────────────────

mcp = FastMCP("VellumMem")


@mcp.tool()
@_tool
def memory_init() -> str:
    """初始化 VellumMem 会话上下文。

    懒初始化：首次调用时自动建库、建表、加载模型、构建记忆分组。

    Returns:
        {"message": "VellumMem ready"}
    """
    return json.dumps({"message": "VellumMem ready"}, ensure_ascii=False)


@mcp.tool()
@_tool
def memory_query(query: str, top_k: int = 3,
                 score_threshold: float = 0.15) -> str:
    """检索记忆条目（语义向量搜索）。

    使用预合并向量进行单层语义检索，返回按相关度排名的结果。
    支持分数阈值过滤和贪婪模式（调大 top_k 返回更多结果）。

    Args:
        query: 自然语言查询文本
        top_k: 返回条数，默认 3；设大值即"贪婪模式"
        score_threshold: 最低匹配分数（默认 0.15），低于此值返回空结果

    Returns:
        {"results": [QueryResult...]}
        每个 QueryResult 包含 source_id, summary, score,
        create_timestamp, has_context, context_link, total_chunks,
        category, is_time_sensitive, group_ids 等字段。
    """
    hits = _vector.search(query, top_k=top_k, score_threshold=score_threshold)
    results = []
    for hit in hits:
        eid = hit["entry_id"]
        entry = _stores["human_timeline"].get_by_id(eid)
        if not entry:
            continue
        link = entry.get("conversation_context_link", [])
        # 查分组
        entry_groups = _groups.get_groups_for_entry(eid)
        results.append({
            "source_id": eid,
            "summary": entry.get("summary", "")[:200],
            "score": hit["score"],
            "create_timestamp": entry.get("create_timestamp"),
            "has_context": bool(link),
            "context_link": link,
            "total_chunks": len(link),
            "category": entry.get("category", "conversation"),
            "is_time_sensitive": bool(entry.get("is_time_sensitive", 0)),
            "group_ids": [g["id"] for g in entry_groups],
        })

    return json.dumps({"results": results}, ensure_ascii=False, default=str)


@mcp.tool()
@_tool
def memory_get_context(timeline_id: str, offset: int = 0, limit: int = 1) -> str:
    """获取记忆原文片段。

    按需拉取 human_timeline 条目关联的记忆原文分片。
    从最新分片往前翻（offset=0 为最新），AI 自行判断是否需要查看更多。

    Args:
        timeline_id: human_timeline 条目 ID
        offset: 偏移量（0=最新分片，1=次新...）
        limit: 最多返回几个分片（默认 1）

    Returns:
        {"timeline_id": str, "summary": str, "chunks": [...],
         "total_chunks": int, "offset": int, "returned": int, "remaining": int}
    """
    ht = _stores["human_timeline"]
    entry = ht.get_by_id(timeline_id)
    if not entry:
        return json.dumps({"error": f"Timeline {timeline_id} not found"})
    link = entry.get("conversation_context_link", [])
    total = len(link)
    chunks = ht.get_context_chunks(timeline_id, offset=offset, limit=limit)
    return json.dumps({
        "timeline_id": timeline_id,
        "summary": entry.get("summary", ""),
        "chunks": chunks,
        "total_chunks": total,
        "offset": offset,
        "returned": len(chunks),
        "remaining": max(0, total - offset - len(chunks)),
    }, ensure_ascii=False, default=str)


@mcp.tool()
@_tool
def memory_write(data: str) -> str:
    """写入记忆条目。

    写入 human_timeline（摘要+tags+原文），自动计算预合并向量。

    **强制要求**：summary 对应的 tags 必须提供 5 个，否则报错。
    如果 summary 装不下关键细节，必须用 context_text 补存原文！！！

    Args:
        data: JSON 字符串，可含字段：
            - summary: str — 会话摘要（上限 200 字），必填
            - tags: [str] — 5 个主题标签，必填
            - context_text: str — 会话原文，必填
            - category: str — 记忆类型（conversation/knowledge/document/preference/other）
            - is_time_sensitive: bool — 内容是否随时间可能失效

    Returns:
        {"message": "Memory stored", "id": str, "context_ids": [str]}
    """
    try:
        payload = json.loads(data) if isinstance(data, str) else data
    except json.JSONDecodeError:
        return json.dumps({"error": "Invalid JSON data"})

    summary = (payload.get("summary") or "")[:200]
    if not summary:
        return json.dumps({"error": "summary is required"})

    category = payload.get("category")
    if not category:
        return json.dumps({"error": "category is required (conversation/knowledge/document/preference/other)"})

    ht = _stores["human_timeline"]
    entry = ht.create(
        summary=summary,
        tags=payload.get("tags"),
        context_text=payload.get("context_text"),
        category=category,
        is_time_sensitive=payload.get("is_time_sensitive", False),
    )
    tl_id = entry["id"]

    # Vector embedding (pre-merged vector)
    if _vector:
        try:
            _vector.store(tl_id, summary, payload.get("tags", []) or [])
        except Exception:
            pass

    return json.dumps({
        "message": "Memory stored",
        "id": tl_id,
        "context_ids": entry.get("context_ids", []),
    }, ensure_ascii=False)


@mcp.tool()
@_tool
def memory_write_context(timeline_id: str, context_text: str) -> str:
    """追存入更多的记忆内容到已有 timeline 条目。

    按自然分隔符自动分片（单片 ≤8000 字符），每片生成独立 ID。
    更新 conversation_context_link 数组，不修改已有分片。

    Args:
        timeline_id: 目标 human_timeline 条目 ID
        context_text: 追加的记忆内容

    Returns:
        {"id": str, "new_context_ids": [str], "total_chunks": int}
    """
    ht = _stores["human_timeline"]
    result = ht.append_context(timeline_id, context_text)
    if "error" in result:
        return json.dumps(result)
    entry = ht.get_by_id(timeline_id)
    total = len(entry.get("conversation_context_link", [])) if entry else 0
    return json.dumps({
        "id": timeline_id,
        "new_context_ids": result.get("new_context_ids", []),
        "total_chunks": total,
    }, ensure_ascii=False)


@mcp.tool()
@_tool
def memory_status() -> str:
    """查看 VellumMem 系统状态。

    返回所有数据库表的行数统计。

    Returns:
        {"storage": {table_name: row_count, ...}}
    """
    stats = _db.stats()
    return json.dumps({"storage": stats})


# ── Delete / Update Tools ─────────────────────────────────────


@mcp.tool()
@_tool
def memory_delete(entry_id: str, force: bool = False) -> str:
    """删除记忆条目。

    不会删除 is_time_sensitive=True 的条目（除非 force=True）。
    time-sensitive 条目会在 TTL 到期后自动清理。

    Args:
        entry_id: human_timeline 条目 ID
        force: 是否强制删除 time-sensitive 条目（默认 False）

    Returns:
        {"message": "Memory deleted", "id": str} 或 {"error": ...}
    """
    entry = _stores["human_timeline"].get_by_id(entry_id)
    if not entry:
        return json.dumps({"error": f"Entry {entry_id} not found"})
    if entry.get("is_time_sensitive") and not force:
        return json.dumps({
            "error": f"Entry {entry_id} is time-sensitive and cannot be manually deleted. "
                     "Use force=True to override, or wait for TTL auto-cleanup."
        })
    ht = _stores["human_timeline"]
    ok = ht.delete(entry_id)
    if not ok:
        return json.dumps({"error": f"Failed to delete {entry_id}"})
    _vector.remove(entry_id)
    return json.dumps({"message": "Memory deleted", "id": entry_id}, ensure_ascii=False)


@mcp.tool()
@_tool
def memory_update(entry_id: str, data: str) -> str:
    """更新记忆条目。

    支持部分字段更新。如果 summary 或 tags 变化，自动重新计算合并向量。
    如果提供 context_text，替换所有已有上下文分片（不追加）。

    Args:
        entry_id: human_timeline 条目 ID
        data: JSON 字符串，可选字段：
            - summary: str
            - tags: [str]（必须提供 5 个）
            - category: str
            - is_time_sensitive: bool
            - context_text: str（替换全部原文）

    Returns:
        {"message": "Memory updated", "id": str, "context_ids": [str]}
    """
    try:
        payload = json.loads(data) if isinstance(data, str) else data
    except json.JSONDecodeError:
        return json.dumps({"error": "Invalid JSON data"})

    ht = _stores["human_timeline"]
    result = ht.update(
        entry_id,
        summary=payload.get("summary"),
        tags=payload.get("tags"),
        category=payload.get("category"),
        is_time_sensitive=payload.get("is_time_sensitive"),
        context_text=payload.get("context_text"),
    )
    if "error" in result:
        return json.dumps(result)

    if result.get("vector_changed") and _vector:
        entry = ht.get_by_id(entry_id)
        if entry:
            try:
                tags_raw = entry.get("tags", []) or []
                if isinstance(tags_raw, str):
                    tags_raw = json.loads(tags_raw)
                _vector.store(entry_id, entry.get("summary", ""), tags_raw)
            except Exception:
                pass

    return json.dumps({
        "message": "Memory updated",
        "id": result["id"],
        "context_ids": result.get("context_ids", []),
    }, ensure_ascii=False)


# ── Group Tools ────────────────────────────────────────────────

@mcp.tool()
@_tool
def memory_get_groups(entry_id: str) -> str:
    """查询某条记忆属于哪些分组。

    Args:
        entry_id: human_timeline 条目 ID

    Returns:
        {"entry_id": str, "groups": [{"id": str, "member_count": int, "entry_ids": [str]}]}
    """
    groups = _groups.get_groups_for_entry(entry_id)
    return json.dumps({"entry_id": entry_id, "groups": groups}, ensure_ascii=False)


@mcp.tool()
@_tool
def memory_list_groups() -> str:
    """列出所有记忆分组。

    Returns:
        {"groups": [{"id": str, "member_count": int, "entry_ids": [str], "create_timestamp": int}]}
    """
    groups = _groups.list_groups()
    return json.dumps({"groups": groups}, ensure_ascii=False, default=str)


@mcp.tool()
@_tool
def memory_get_group_members(group_id: str) -> str:
    """查询某分组内有哪些记忆。

    Args:
        group_id: 分组 ID

    Returns:
        {"id": str, "entry_ids": [str], "member_count": int}
    """
    group = _groups.get_group_members(group_id)
    if group is None:
        return json.dumps({"error": f"Group {group_id} not found"})
    return json.dumps(group, ensure_ascii=False)


@mcp.tool()
@_tool
def memory_rebuild_groups(threshold: float = 0.45) -> str:
    """重新构建记忆分组（CPM，支持任意 k 值）。

    基于所有记忆的向量余弦相似度，用派系过滤法重新计算分组。
    k 值从 config 表读取（group_k），threshold 可手动覆盖。
    已有分组会被替换。

    Args:
        threshold: 余弦相似度阈值（默认 0.45）

    Returns:
        {"groups_built": int, "entries": int}
    """
    k_row = _db.connect().execute(
        "SELECT value FROM config WHERE key = ?", ("group_k",)
    ).fetchone()
    k = int(k_row["value"]) if k_row else 4
    result = _groups.build_groups(k=k, threshold=threshold)
    return json.dumps(result)


# ── Entry point ───────────────────────────────────────────────

def main():
    """Start VellumMem MCP server over stdio transport.

    Pre-warms sentence-transformers, initializes stores & vectors,
    starts the background daemon thread, then runs the MCP server.
    """
    import sys as _sys
    try:
        _sys.stderr.write("[VellumMem] Pre-warming sentence-transformers (sync)...\n")
        _sys.stderr.flush()
        import sentence_transformers  # noqa: F401
        _sys.stderr.write("[VellumMem]   sentence-transformers ready\n")
        _sys.stderr.flush()
    except ImportError:
        _sys.stderr.write("[VellumMem]   sentence-transformers not available\n")
        _sys.stderr.flush()
    except Exception:
        _sys.stderr.write("[VellumMem]   sentence-transformers pre-warm failed\n")
        _sys.stderr.flush()

    # 主动初始化（DB、向量模型、分组）
    _ensure_init()

    # 启动后台守护线程（TTL 清理 + 去重扫描）
    _start_daemon()

    mcp.run()
