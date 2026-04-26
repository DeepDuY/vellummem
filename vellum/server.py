"""VellumMem MCP Server — entry point.

Provides 6 tools for AI memory management via the Model Context Protocol.

Usage:
    python run.py   (from vellum/ project root)
"""

from __future__ import annotations

import json
import os
import re

from fastmcp import FastMCP

from .db import VellumDB
from .session import Session
from .router import Router
from .stores.timeline import TimelineStore
from .stores.semantic import SemanticStore
from .stores.projects import ProjectStore
from .stores.file_map import FileMapStore
from .stores.decisions import DecisionStore
from .stores.tasks import TaskStore
from .stores.patterns import PatternStore
from .stores.reflections import ReflectionStore
from .vector.adapter import create_vector_adapter

# ── Global state (lazy-initialized on first tool call) ─────────

_db: VellumDB | None = None
_session: Session | None = None
_router: Router | None = None
_stores: dict | None = None
_vector = None


def _ensure_init():
    global _db, _session, _router, _stores, _vector
    if _db is not None:
        return
    # Default to project-root/vellum.db regardless of CWD
    _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _default_db = os.path.join(_project_root, "vellum.db")
    db_path = os.environ.get("VELLUM_DB_PATH", _default_db)
    _db = VellumDB(db_path)
    if not _db.is_initialized():
        _db.initialize()
    _session = Session()
    timeline = TimelineStore(_db)
    semantic = SemanticStore(_db)
    projects = ProjectStore(_db)
    file_map = FileMapStore(_db)
    decisions = DecisionStore(_db)
    tasks = TaskStore(_db)
    patterns = PatternStore(_db)
    reflections = ReflectionStore(_db)
    _stores = {
        "timeline": timeline,
        "semantic": semantic,
        "projects": projects,
        "file_map": file_map,
        "decisions": decisions,
        "tasks": tasks,
        "patterns": patterns,
        "reflections": reflections,
    }
    # Vector search (P4) — auto-detect best engine (transformer → LSI)
    _vector = create_vector_adapter(_db)

    _router = Router(timeline, semantic, projects, file_map, decisions, tasks,
                     hub=None, patterns=patterns, reflections=reflections,
                     vector=_vector)


# ── Helper: entity auto-extraction ────────────────────────────

def _extract_noun_terms(text: str) -> list[str]:
    """Extract meaningful noun-like terms from Chinese/English text."""
    terms: set[str] = set()
    for m in re.finditer(r'[\u4e00-\u9fff]{2,6}', text):
        terms.add(m.group())
    for m in re.finditer(r'\b[A-Z][a-z]{2,}\b', text):
        terms.add(m.group())
    return list(terms)


def _auto_store_entities(summary: str, session_id: str):
    """Auto-detect and store entities from summary text."""
    terms = _extract_noun_terms(summary)
    semantic = _stores.get("semantic")
    if not semantic:
        return
    for term in terms[:8]:
        try:
            existing = semantic.find_entity_fuzzy(term)
            if not existing:
                semantic.create_entity(term, type="concept", importance=1,
                                       summary=f"自动提取自: {summary[:60]}...")
        except Exception:
            pass


# ── MCP Server ────────────────────────────────────────────────

mcp = FastMCP("VellumMem")


@mcp.tool()
def memory_init(project_path: str | None = None) -> str:
    """❗每次新对话开始时，必须先调用此工具初始化记忆系统。

    如果不传任何参数，默认以 hybrid 模式启动（同时检索聊天记录和项目文件）。
    如果你知道这是一个编程/开发对话，建议传 project_path，这样系统会自动加载该项目
    的文件索引、过往决策记录和任务进度。

    调用时机：
      - ✅ 每次新对话开始时调用一次
      - ✅ 当对话主题切换到另一个项目时再次调用

    参数说明：
      project_path: 项目根目录的绝对路径（可选）。
        - 不传 → 纯对话模式，只管理人的记忆
        - 传了 → 对话 + 项目双模式，AI 可以查到项目文件

    调用示例：
      memory_init()                                    # 纯聊天
      memory_init(project_path="C:/Users/me/myapp")    # 编程项目

    不调用的后果：
      - memory_query 可以正常使用，但搜不到项目相关的记忆
      - memory_project_sync 会报错，因为没有项目路径
    """
    _ensure_init()
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
def memory_query(query: str, mode: str | None = None, depth: int = 0) -> str:
    """🔍 检索记忆系统。支持渐进式深度检索。

    这是最核心的 tool，当用户问"还记得吗""上次说了什么""找一下某个文件"
    或任何涉及历史信息的问题时，都应该调用这个 tool。

    调用时机：
      - ✅ 用户问起过去讨论过的话题
      - ✅ 用户想找某个文件、决策、代码片段
      - ✅ 用户问关于自己的偏好、习惯、做过的事
      - ✅ 在 memory_write 写过内容后，下个会话查询

    模式说明：
      - 不传 mode（默认）→ hybrid：同时搜聊天记录 + 项目文件 + 决策日志
      - mode="human"    → 只搜聊天记录和时间线，适合纯回忆场景
      - mode="code"     → 只搜项目文件和代码决策，适合纯编程场景

    深度说明（渐进式检索，仅 human/hybrid 模式）：
      先浅后深，AI 决定要不要继续挖：
        depth=0（默认）→ 搜全部层级（向后兼容）
        depth=1       → L1 timeline（原始会话记录，最新/最轻量）
        depth=2       → L1 + L2 semantic（加上实体/关系事实）
        depth=3       → L1 + L2 + L3 patterns（加上行为模式）
        depth=4       → L1 + L2 + L3 + L4 reflections（全部深度）

      ╔════════════════════════════════════════╗
      ║  推荐流程：                             ║
      ║  1. memory_query(query, depth=1)       ║
      ║     → 看 timeline 原文够不够            ║
      ║  2. 不够再 depth=2                      ║
      ║     → 加上语义事实                      ║
      ║  3. 还要 depth=3                        ║
      ║     → 加上行为模式                      ║
      ║  4. 全要 depth=4                        ║
      ║     → 加上深度洞察                      ║
      ╚════════════════════════════════════════╝

    参数说明：
      query: 检索关键词，用自然语言描述你想找的内容
      mode:  可选，临时覆盖当前会话模式（不影响 sticky 状态）
      depth: 可选，渐进深度（0=全量, 1-4=逐级增加）

    调用示例：
      memory_query(query="JWT认证", depth=1)            # 先看原文
      memory_query(query="JWT认证", depth=2)            # 不够再加事实
      memory_query(query="JWT认证", depth=4)            # 全量
      memory_query(query="middleware.ts", mode="code")  # 纯代码
      memory_query(query="认证方案", mode="hybrid")      # 默认深度

    注意事项：
      - query 不要太短（一个词可能搜不到），最好是一句自然语言
      - 如果 depth=1 搜不到，逐步增加 depth 再试
      - 非 hybrid 模式下搜不到时，可以切回 hybrid 再试
    """
    _ensure_init()
    current_mode = mode or _session.mode
    result = _router.query(query, current_mode, depth)
    return json.dumps(result, ensure_ascii=False, default=str)


@mcp.tool()
def memory_set_mode(mode: str) -> str:
    """🔄 切换当前会话的记忆检索模式。模式会一直保持直到下次切换。

    默认是 hybrid（全搜），只有当发现查询结果太多或太少时才需要切换。

    调用时机：
      - ✅ 查询结果混杂了太多聊天记录和代码结果 → 切 code 模式
      - ✅ 用户只想回忆不想看代码 → 切 human 模式
      - ✅ 明确知道当前场景只需要搜一边

    参数说明：
      mode:
        - "human" → 只搜聊天记录（时间线、语义网、模式、洞察）
        - "code"  → 只搜项目文件（文件索引、决策日志、任务）

    调用示例：
      memory_set_mode(mode="code")     # 纯编程对话
      memory_set_mode(mode="human")    # 纯回忆聊天

    注意：
      - 要切回 hybrid 不需要调这个，直接在 memory_query 传 mode="hybrid" 即可
      - 如果是临时搜一次另一边，不要调 set_mode，直接在 query 传 mode 参数
    """
    _ensure_init()
    _session.set_mode(mode)
    return json.dumps({"message": f"Mode switched to {mode}", "mode": mode},
                      ensure_ascii=False)


@mcp.tool()
def memory_write(data: str, mode: str | None = None) -> str:
    """💾 【关键】在每次会话结束前调用此工具，保存本次对话的内容到记忆系统。

    如果不调用这个 tool，本次对话的内容不会被记住，下次开启新对话时 memory_query
    搜不到这次的内容。AI 会自动从 summary 中提取关键词存入语义网。

    调用时机：
      - ✅ 必须：每次对话结束前调用一次
      - ✅ 对话中出现了重要决策、技术选型、用户偏好等信息时
      - ✅ 用户明确说了"记住""记一下""记录下来"时
      - ✅ 写入了代码或修改了文件时

    参数说明：
      data: JSON 字符串，包含以下字段：
        summary (必填): 本次对话的完整摘要，用自然语言描述发生了什么
        tags (可选): 主题标签列表，如 ["技术选型", "认证", "Go语言"]
        key_moments (可选): 重要时刻列表，每个含 type 和 content
          示例: [{"type": "decision", "content": "决定用JWT代替Session"}]
        decisions (可选): 做出的决策列表，每个含 title, body, affected_files
          示例: [{"title": "选JWT", "body": "无状态方案更适合桌面", "affected_files": ["src/auth/middleware.ts"]}]
        project_updates (可选): 文件变更，每个含 path 和 summary
        task_updates (可选): 任务进度，含 title/task_id, status, blockers 等

    调用示例：
      memory_write(data={
        "summary": "讨论了认证方案，决定用JWT，重构了middleware.ts",
        "tags": ["认证", "架构决策"],
        "key_moments": [{"type": "decision", "content": "选了JWT"}],
        "decisions": [{"title": "JWT方案", "body": "...", "affected_files": ["middleware.ts"]}]
      })

    注意：
      - summary 是唯一必填字段，有它就能存
      - 其他字段可选，但填得越详细未来检索效果越好
      - AI 会自动从 summary 中提取实体关键词
    """
    _ensure_init()
    try:
        payload = json.loads(data) if isinstance(data, str) else data
    except json.JSONDecodeError:
        return json.dumps({"error": "Invalid JSON data"}, ensure_ascii=False)

    current_mode = mode or _session.mode
    written = []
    summary = payload.get("summary", "")
    session_id = None

    # Timeline write
    if summary:
        entry = _stores["timeline"].create(
            mode=current_mode,
            project_id=_session.project_id,
            summary=summary,
            key_moments=payload.get("key_moments"),
            tags=payload.get("tags"),
        )
        session_id = entry["id"]
        written.append(f"timeline:{session_id}")

        # Auto-extract entities
        entity_terms = _extract_noun_terms(summary)
        for term in entity_terms[:5]:
            try:
                _stores["semantic"].create_entity(term, type="concept", importance=1)
            except Exception:
                pass

        # Generate vector embedding for semantic search
        if session_id and _vector:
            try:
                _vector.store(session_id, summary)
            except Exception:
                pass

    # Decisions
    for dec in payload.get("decisions", []):
        d = _stores["decisions"].create(
            title=dec.get("title", ""),
            body=dec.get("body", ""),
            project_id=_session.project_id,
            affected_files=dec.get("affected_files"),
            linked_session=session_id,
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
                project_id=_session.project_id or "unknown",
                status=tu.get("status", "wip"),
            )
            written.append(f"task:{t['id']}")

    return json.dumps({"message": "Memory stored", "written": written},
                      ensure_ascii=False)


@mcp.tool()
def memory_project_sync(path: str | None = None) -> str:
    """📂 扫描项目目录并索引所有源文件。新项目首次使用时必须调用。

    调用后，可以通过 memory_query 按路径前缀（如 src/auth/）、模块名、函数名
    来检索文件。建议在 memory_init 传了 project_path 后立即调用。

    调用时机：
      - ✅ 必须：首次接触一个新项目时，在 memory_init 之后立即调用
      - ✅ 项目文件结构发生大的变化时重新调用
      - ❌ 不需要每次对话都调，索引一次就够了

    参数说明：
      path: 项目根目录的绝对路径（可选）。
        - 不传 → 使用 memory_init 传过的项目路径
        - 传了 → 扫描该路径并设为当前项目

    调用示例：
      memory_project_sync()   # 使用 memory_init 已设置的项目路径
      memory_project_sync(path="C:/Users/me/myproject")  # 指定新项目

    不调用的后果：
      - 文件检索（按路径、模块、函数名搜索）不可用
      - memory_query 搜不到项目文件
    """
    _ensure_init()
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
    proj.update(pid, last_scanned="now")

    return json.dumps({"message": "Project scan complete", "stats": stats},
                      ensure_ascii=False)


@mcp.tool()
def memory_status() -> str:
    """📊 查看当前记忆系统的状态，用于调试和确认系统是否正常运行。

    返回内容包括：
      - 当前模式（hybrid / human / code）
      - 当前项目路径
      - 各存储表的记录数

    调用时机：
      - ✅ 首次配置后检查连接是否正常
      - ✅ 不确定当前模式和项目时
      - ✅ 想了解系统已经存储了多少记忆时
    """

    _ensure_init()
    return json.dumps({
        "session": _session.status(),
        "storage": _db.stats(),
    }, ensure_ascii=False)


# ── Entry Point ────────────────────────────────────────────────

def main():
    _ensure_init()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
