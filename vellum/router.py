"""Mode routing — dispatches queries to the appropriate stores.

v5: Hybrid mode removed. Only human/code modes.
     Human search uses vector search (pre-merged vectors).
     Code search uses keyword/FTS5 (unchanged).
"""

from __future__ import annotations

import json
import typing
from dataclasses import dataclass, field

if typing.TYPE_CHECKING:
    from .stores.human_timeline import HumanTimelineStore
    from .stores.projects import ProjectStore
    from .stores.file_map import FileMapStore
    from .stores.decisions import DecisionStore
    from .stores.tasks import TaskStore
    from .vector.adapter import VectorAdapter


@dataclass
class QueryResult:
    """Unified search result across domains."""
    source_domain: str          # "human" | "code"
    source_table: str           # "human_timeline" | "file_map" | etc.
    source_id: str
    summary: str
    score: float = 1.0
    has_context: bool = False
    context_link: list = field(default_factory=list)
    total_chunks: int = 0
    tags: list = field(default_factory=list)


class Router:
    """Routes memory queries based on the current mode.

    Modes:
        human (default) — search human memory only (vector search)
        code            — search project memory only (keyword/FTS5)
    """

    def __init__(self, human_timeline: HumanTimelineStore,
                 projects: ProjectStore, file_map: FileMapStore,
                 decisions: DecisionStore, tasks: TaskStore,
                 vector: VectorAdapter = None):
        self._human_timeline = human_timeline
        self._projects = projects
        self._file_map = file_map
        self._decisions = decisions
        self._tasks = tasks
        self._vector = vector

    def query(self, query_text: str, mode: str,
              top_k: int = 3, score_threshold: float = 0.15) -> dict:
        """Execute a query under the given mode.

        Args:
            query_text: natural language query
            mode: "human" or "code"
            top_k: max results to return
            score_threshold: minimum score for human vector search

        Returns:
            dict with keys: mode, results
        """
        if mode == "human":
            results = self._search_human(query_text, top_k, score_threshold)
        elif mode == "code":
            results = self._search_code(query_text)
        else:
            results = []

        return {"mode": mode, "results": results}

    # ── Human search: vector-only ───────────────────────────

    def _search_human(self, query_text: str, top_k: int = 3,
                      score_threshold: float = 0.15) -> list[QueryResult]:
        """Search human memory — single-layer vector search.

        Uses pre-merged vectors for fast dot-product scoring.
        """
        import sys as _sys
        _sys.stderr.write(f"[Router] _search_human query={query_text} "
                          f"top_k={top_k} threshold={score_threshold}\n")
        _sys.stderr.flush()

        results: list[QueryResult] = []

        if not self._vector:
            _sys.stderr.write("[Router]   No vector adapter — returning empty\n")
            _sys.stderr.flush()
            return results

        try:
            hits = self._vector.search(
                query_text,
                top_k=top_k,
                score_threshold=score_threshold,
            )
            _sys.stderr.write(f"[Router]   vector: {len(hits)} hits\n")
            _sys.stderr.flush()

            for hit in hits:
                eid = hit["entry_id"]
                entry = self._human_timeline.get_by_id(eid)
                if not entry:
                    continue
                link = entry.get("conversation_context_link", [])
                results.append(QueryResult(
                    source_domain="human",
                    source_table="human_timeline",
                    source_id=eid,
                    summary=entry.get("summary", "")[:200],
                    score=hit["score"],
                    has_context=bool(link),
                    context_link=link,
                    total_chunks=len(link),
                    tags=entry.get("tags", []),
                ))
        except Exception as e:
            _sys.stderr.write(f"[Router]   exception: {type(e).__name__}: {e}\n")
            _sys.stderr.flush()

        # Sort by score descending
        results.sort(key=lambda x: -x.score)
        _sys.stderr.write(f"[Router] done: {len(results)} results\n")
        _sys.stderr.flush()
        return results

    # ── Code search: keyword/FTS5 (unchanged) ───────────────

    def _search_code(self, query_text: str) -> list[QueryResult]:
        results: list[QueryResult] = []

        # File map
        try:
            files = self._file_map.search(query_text)
            for f in files[:5]:
                results.append(QueryResult(
                    source_domain="code",
                    source_table="file_map",
                    source_id=f.get("id", ""),
                    summary=f.get("path", ""),
                ))
        except Exception:
            pass

        # Decisions
        try:
            decisions = self._decisions.search(query_text)
            for d in decisions[:3]:
                results.append(QueryResult(
                    source_domain="code",
                    source_table="decisions",
                    source_id=d.get("id", ""),
                    summary=d.get("title", ""),
                ))
        except Exception:
            pass

        # Tasks
        try:
            tasks = self._tasks.get_active()
            for t in tasks:
                if query_text.lower() in t.get("title", "").lower():
                    results.append(QueryResult(
                        source_domain="code",
                        source_table="tasks",
                        source_id=t.get("id", ""),
                        summary=t.get("title", ""),
                    ))
        except Exception:
            pass

        return results
