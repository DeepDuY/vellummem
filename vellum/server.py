"""VellumMem MCP Server — entry point.

Provides 8 tools for AI memory management via the Model Context Protocol.

Usage:
    python run.py   (from vellum/ project root)
"""

from __future__ import annotations

import json
import os

from fastmcp import FastMCP

from .db import VellumDB
from .session import Session
from .router import Router
from .stores.human_timeline import HumanTimelineStore
from .stores.projects import ProjectStore
from .stores.file_map import FileMapStore
from .stores.decisions import DecisionStore
from .stores.tasks import TaskStore
from .vector.adapter import create_vector_adapter

# ── Global state (lazy-initialized on first tool call) ─────────

_db: VellumDB | None = None
_session: Session | None = None
_router: Router | None = None
_stores: dict | None = None
_vector = None


def _migrate_config_table(db: VellumDB):
    """Migrate old config table (key+value only) to new schema.

    Adds type/description/created_at/updated_at columns if missing,
    then seeds default config values for new keys.
    """
    conn = db.connect()
    has_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='config'"
    ).fetchone()
    if not has_table:
        return

    existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(config)").fetchall()}
    new_cols = {
        "type": "TEXT NOT NULL DEFAULT 'str'",
        "description": "TEXT DEFAULT ''",
        "created_at": "TEXT DEFAULT (datetime('now','localtime'))",
        "updated_at": "TEXT DEFAULT (datetime('now','localtime'))",
    }
    for col, dtype in new_cols.items():
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE config ADD COLUMN {col} {dtype}")

    defaults = [
        ("mode", "human", "str", "当前检索模式: human / code"),
        ("project_id", "", "str", "当前绑定的项目 ID"),
        ("project_path", "", "str", "当前绑定的项目路径"),
        ("vector_engine", "transformer", "str", "向量引擎"),
        ("score_threshold", "0.15", "float", "向量检索最低匹配分数"),
    ]
    for key, value, typ, desc in defaults:
        existing = conn.execute(
            "SELECT 1 FROM config WHERE key = ?", (key,)
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO config (key, value, type, description) VALUES (?, ?, ?, ?)",
                (key, value, typ, desc),
            )
    conn.commit()


def _ensure_init():
    global _db, _session, _router, _stores, _vector
    if _router is not None:
        return
    import sys as _sys

    def _log(msg):
        _sys.stderr.write(f"[VellumMem INIT] {msg}\n")
        _sys.stderr.flush()

    # Reset all globals
    _db = _session = _router = None
    _stores = None
    _vector = None
    try:
        _log("1/9 Resolving project root & db_path...")
        _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _default_db = os.path.join(_project_root, "vellum.db")
        db_path = os.environ.get("VELLUM_DB_PATH", _default_db)
        _log(f"   db_path={db_path}")

        _log("2/9 Connecting to DB...")
        tmp_db = VellumDB(db_path)
        if not tmp_db.is_initialized():
            _log("   Running schema.sql...")
            tmp_db.initialize()
        _log("3/9 Migrating config table...")
        _migrate_config_table(tmp_db)

        _log("4/9 Checking / creating v5 schema...")
        conn = tmp_db.connect()
        has_ht = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='human_timeline'"
        ).fetchone()
        if not has_ht:
            _log("   Creating tables...")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS human_timeline (
                    id                        TEXT PRIMARY KEY,
                    session_start             TEXT NOT NULL,
                    session_end               TEXT NOT NULL,
                    summary                   TEXT DEFAULT '',
                    tags                      TEXT DEFAULT '[]',
                    conversation_context_link TEXT DEFAULT '[]',
                    create_timestamp          INTEGER NOT NULL,
                    update_timestamp          INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversation_context (
                    id               TEXT PRIMARY KEY,
                    timeline_id      TEXT NOT NULL REFERENCES human_timeline(id) ON DELETE CASCADE,
                    context          TEXT NOT NULL,
                    chunk_index      INTEGER NOT NULL DEFAULT 0,
                    create_timestamp INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS entry_vectors (
                    entry_id    TEXT PRIMARY KEY REFERENCES human_timeline(id) ON DELETE CASCADE,
                    merged_blob BLOB NOT NULL
                );
            """)
            conn.commit()
        else:
            # Migration: add entry_vectors table if missing, ensure no key_moments issues
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS entry_vectors (
                    entry_id    TEXT PRIMARY KEY REFERENCES human_timeline(id) ON DELETE CASCADE,
                    merged_blob BLOB NOT NULL
                );
            """)
            conn.commit()

        _log("5/9 Dropping old unused tables...")
        conn.execute("PRAGMA foreign_keys=OFF")
        for old_table in ["timeline", "timeline_fts", "timeline_embeddings",
                          "semantic_entities", "semantic_facts",
                          "patterns", "reflections", "decision_hub",
                          "transformer_embeddings",
                          "human_timeline_embeddings", "human_transformer_embeddings"]:
            conn.execute(f"DROP TABLE IF EXISTS {old_table}")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.commit()

        _log("6/9 Creating stores...")
        _session = Session(tmp_db)
        ht = HumanTimelineStore(tmp_db)
        projects = ProjectStore(tmp_db)
        file_map = FileMapStore(tmp_db)
        decisions = DecisionStore(tmp_db)
        tasks = TaskStore(tmp_db)
        _stores = {
            "human_timeline": ht,
            "projects": projects,
            "file_map": file_map,
            "decisions": decisions,
            "tasks": tasks,
        }

        _log("7/9 Initializing vector adapter (auto-detect)...")
        _vector = create_vector_adapter(tmp_db)
        _log(f"   vector engine={_vector.engine if _vector else 'NONE'}")

        _log("8/9 Creating router...")
        _router = Router(
            human_timeline=ht,
            projects=projects, file_map=file_map,
            decisions=decisions, tasks=tasks,
            vector=_vector,
        )
        _db = tmp_db

        _log("9/9 Auto-creating default project...")
        try:
            existing = projects.list_all()
            if not existing:
                name = os.path.basename(_project_root)
                proj = projects.create(name, _project_root)
                _session.set_project(proj["id"], _project_root)
        except Exception as e:
            _log(f"   (skipped) {e}")

        _log("✅ Initialization complete")
    except Exception:
        _db = _session = _router = None
        _stores = None
        _vector = None
        import traceback
        traceback.print_exc()
        _sys.stderr.write("[VellumMem INIT] ❌ FAILED\n")
        _sys.stderr.flush()
        # Don't re-raise — tools will check _router and return error


# ── MCP Server ────────────────────────────────────────────────

mcp = FastMCP("VellumMem")


@mcp.tool()
def memory_init(project_path: str | None = None) -> str:
    """初始化 VellumMem 会话上下文。
    
    重置 session 状态到默认值（mode=human），可选绑定项目路径。
    懒初始化：首次调用时自动建库、建表、加载模型。
    
    Args:
        project_path: 可选的项目根路径。提供时会创建/绑定项目卡片并扫描文件。
    
    Returns:
        {"message": "VellumMem ready", "mode": str, "project": str}
    """
    _ensure_init()
    if _router is None:
        return json.dumps({"error": "VellumMem failed to initialize. Check server logs."}, ensure_ascii=False)
    _session.reset()
    if project_path and os.path.isdir(project_path):
        proj = _stores["projects"]
        existing = proj.get_by_path(project_path)
        if not existing:
            name = os.path.basename(project_path)
            existing = proj.create(name, project_path)
        _session.set_project(existing["id"], project_path)
    status = _session.status()
    return json.dumps({
        "message": "VellumMem ready",
        "mode": status["mode"],
        "project": status["project_path"] or "none",
    }, ensure_ascii=False)


@mcp.tool()
def memory_query(query: str, mode: str | None = None,
                 top_k: int = 3, score_threshold: float = 0.15) -> str:
    """检索记忆条目（语义向量搜索）。
    
    使用预合并向量进行单层语义检索，返回按相关度排名的结果。
    支持分数阈值过滤和贪婪模式（调大 top_k 返回更多结果）。
    
    Args:
        query: 自然语言查询文本
        mode: 检索模式（"human" / "code"），默认使用 session 当前模式
        top_k: 返回条数，默认 3；设大值即"贪婪模式"
        score_threshold: 最低匹配分数（默认 0.15），低于此值返回空结果
    
    Returns:
        {"mode": str, "results": [QueryResult...]}
        每个 QueryResult 包含 source_domain, source_id, summary, score, 
        has_context, context_link, total_chunks, tags 等字段。
        score 为 0~1 的真实余弦相似度分数。
    """
    _ensure_init()
    if _router is None:
        return json.dumps({"error": "VellumMem failed to initialize. Check server logs."}, ensure_ascii=False)
    current_mode = mode or _session.mode
    result = _router.query(query, current_mode, top_k=top_k,
                           score_threshold=score_threshold)
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
def memory_get_context(timeline_id: str, offset: int = 0, limit: int = 1) -> str:
    """获取对话上下文分片。
    
    按需拉取 human_timeline 条目关联的对话原文分片。
    从最新分片往前翻（offset=0 为最新），AI 自行判断是否需要查看更多。
    
    Args:
        timeline_id: human_timeline 条目 ID
        offset: 偏移量（0=最新分片，1=次新...）
        limit: 最多返回几个分片（默认 1）
    
    Returns:
        {"timeline_id": str, "summary": str, "chunks": [...], 
         "total_chunks": int, "offset": int, "returned": int, "remaining": int}
        每个 chunk 包含 id, timeline_id, context, chunk_index, create_timestamp
    """
    _ensure_init()
    if _router is None:
        return json.dumps({"error": "VellumMem failed to initialize. Check server logs."}, ensure_ascii=False)
    ht = _stores["human_timeline"]
    entry = ht.get_by_id(timeline_id)
    if not entry:
        return json.dumps({"error": f"Timeline {timeline_id} not found"},
                          ensure_ascii=False)
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
def memory_set_mode(mode: str) -> str:
    """切换检索模式。
    
    模式持久化到 config 表，重启后自动恢复。
    
    Args:
        mode: "human" — 搜索人类记忆（向量检索）
              "code"  — 搜索项目记忆（关键词/FTS5）
    
    Returns:
        {"message": "Mode switched to {mode}", "mode": str}
    """
    _ensure_init()
    if _router is None:
        return json.dumps({"error": "VellumMem failed to initialize. Check server logs."}, ensure_ascii=False)
    _session.set_mode(mode)
    return json.dumps({"message": f"Mode switched to {mode}", "mode": mode},
                      ensure_ascii=False)


@mcp.tool()
def memory_write(data: str, mode: str | None = None) -> str:
    """写入记忆条目。
    
    支持多维度写入：human_timeline（摘要+tags+上下文）、decisions（决策日志）、
    file_map（文件索引）、tasks（任务）。human_timeline 写入时自动计算预合并向量。
    
    **强制要求**：human 模式下 summary 对应的 tags 必须提供 5 个，否则报错。
    
    Args:
        data: JSON 字符串，可含字段：
            - summary: str — 会话摘要（上限 200 字）
            - tags: [str] — 5 个主题标签（human 模式强制）
            - context_text: str — 初始上下文原文
            - decisions: [{title, body, ...}] — 决策日志（code 模式）
            - project_updates: [{path, summary, ...}] — 文件更新（code 模式）
            - task_updates: [{task_id/title, status, ...}] — 任务更新（code 模式）
        mode: 写入模式（默认 session 当前模式）
    
    Returns:
        {"message": "Memory stored", "written": [...], "id": str}
        "written" 列出所有写入的条目 ID 和类型
    """
    _ensure_init()
    if _router is None:
        return json.dumps({"error": "VellumMem failed to initialize. Check server logs."}, ensure_ascii=False)
    try:
        payload = json.loads(data) if isinstance(data, str) else data
    except json.JSONDecodeError:
        return json.dumps({"error": "Invalid JSON data"}, ensure_ascii=False)

    current_mode = mode or _session.mode
    written = []
    tl_id = None

    # Human Timeline entry
    summary = (payload.get("summary") or "")[:200]
    if summary:
        ht = _stores["human_timeline"]
        entry = ht.create(
            summary=summary,
            tags=payload.get("tags"),
            context_text=payload.get("context_text"),
        )
        tl_id = entry["id"]
        written.append(f"human_timeline:{tl_id}")
        for cid in entry.get("context_ids", []):
            written.append(f"context:{cid}")

    # Decisions
    for dec in payload.get("decisions", []):
        d = _stores["decisions"].create(
            title=dec.get("title", ""),
            body=dec.get("body", ""),
            project_id=_session.project_id,
            affected_files=dec.get("affected_files"),
            linked_session=tl_id,
            tags=dec.get("tags"),
            status=dec.get("status", "planned"),
        )
        written.append(f"decision:{d['id']}")

    # File map updates
    for fu in payload.get("project_updates", []):
        if fu.get("path"):
            _stores["file_map"].update_file(
                fu["path"],
                project_id=_session.project_id,
                summary=fu.get("summary", ""),
            )
            written.append(f"file_map:{fu['path']}")

    # Task updates
    for tu in payload.get("task_updates", []):
        tid = tu.get("task_id", "")
        if tid and _stores["tasks"].get_by_id(tid):
            _stores["tasks"].update(tid, **{k: v for k, v in tu.items()
                                             if k in ("status", "progress_pct",
                                                      "progress_detail", "blockers",
                                                      "next_action")})
            written.append(f"task:{tid}")
        elif tu.get("title"):
            t = _stores["tasks"].create(
                title=tu["title"],
                project_id=_session.project_id,
                status=tu.get("status", "wip"),
            )
            written.append(f"task:{t['id']}")

    # Vector embedding (pre-merged vector)
    if tl_id and _vector:
        try:
            tags_list = payload.get("tags", []) or []
            _vector.store(tl_id, summary, tags_list)
        except Exception:
            pass

    result = {"message": "Memory stored", "written": written}
    if tl_id:
        result["id"] = tl_id
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def memory_write_context(timeline_id: str, context_text: str) -> str:
    """追加上下文分片到已有 timeline 条目。
    
    按自然分隔符自动分片（单片 ≤8000 字符），每片生成独立 ID。
    更新 conversation_context_link 数组，不修改已有分片。
    
    Args:
        timeline_id: 目标 human_timeline 条目 ID
        context_text: 追加的对话原文
    
    Returns:
        {"id": str, "new_context_ids": [str], "total_chunks": int}
    """
    _ensure_init()
    if _router is None:
        return json.dumps({"error": "VellumMem failed to initialize. Check server logs."}, ensure_ascii=False)
    ht = _stores["human_timeline"]
    result = ht.append_context(timeline_id, context_text)
    if "error" in result:
        return json.dumps(result, ensure_ascii=False)
    entry = ht.get_by_id(timeline_id)
    total = len(entry.get("conversation_context_link", [])) if entry else 0
    return json.dumps({
        "id": timeline_id,
        "new_context_ids": result.get("new_context_ids", []),
        "total_chunks": total,
    }, ensure_ascii=False)


@mcp.tool()
def memory_project_sync(path: str | None = None) -> str:
    """同步项目文件索引。
    
    递归扫描项目目录，建立/更新 file_map 索引（支持 Python/TS/JS/Rust/Go 等）。
    未绑定项目时需提供 path 参数。
    
    Args:
        path: 可选的项目路径。不传则使用 session 绑定的项目路径。
    
    Returns:
        {"message": "Project synced", "stats": {...}}
    """
    _ensure_init()
    if _router is None:
        return json.dumps({"error": "VellumMem failed to initialize. Check server logs."}, ensure_ascii=False)
    scan_path = path or _session.project_path
    if not scan_path or not os.path.isdir(scan_path):
        return json.dumps({
            "error": "No valid project path. Call memory_init first or provide a path."
        }, ensure_ascii=False)
    pid = _session.project_id or f"proj_{os.path.basename(scan_path)}"
    proj = _stores["projects"]
    if not proj.get_by_id(pid):
        proj.create(os.path.basename(scan_path), scan_path)
        _session.set_project(pid, scan_path)
    stats = _stores["file_map"].scan_directory(pid, scan_path)
    return json.dumps({"message": "Project synced", "stats": stats}, ensure_ascii=False)


@mcp.tool()
def memory_status() -> str:
    """查看 VellumMem 系统状态。
    
    返回当前 Session 状态（mode、绑定的项目）、
    以及所有数据库表的行数统计。
    
    Returns:
        {"session": {"mode": str, "project_id": str, "project_path": str},
         "storage": {table_name: row_count, ...}}
    """
    _ensure_init()
    if _router is None:
        return json.dumps({"error": "VellumMem failed to initialize. Check server logs."}, ensure_ascii=False)
    status = _session.status()
    stats = _db.stats() if _db else {}
    return json.dumps({"session": status, "storage": stats}, ensure_ascii=False)


# ── Entry point ───────────────────────────────────────────────

def main():
    """Start VellumMem MCP server over stdio transport.

    Pre-warms sentence-transformers synchronously before the server
    starts accepting connections, so the first tool call is never
    delayed by a cold import (Python's import lock blocks the main
    thread when a background thread is already importing the same
    module).
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
    mcp.run()
