"""Mode routing — dispatches queries to the appropriate stores."""

from __future__ import annotations

import typing
from dataclasses import dataclass, field

if typing.TYPE_CHECKING:
    from .stores.timeline import TimelineStore
    from .stores.semantic import SemanticStore
    from .stores.projects import ProjectStore
    from .stores.file_map import FileMapStore
    from .stores.decisions import DecisionStore
    from .stores.tasks import TaskStore
    from .hub import DecisionHub


@dataclass
class QueryResult:
    """Unified search result across domains."""
    source_domain: str          # "human" | "code"
    source_table: str           # "timeline" | "semantic" | "file_map" | etc.
    source_id: str
    summary: str
    score: float = 1.0
    linked: list = field(default_factory=list)  # Decision Hub links


class Router:
    """Routes memory queries based on the current mode.

    Modes:
        hybrid (default) — search both domains + Decision Hub cross-links
        human           — search human memory only (timeline + semantic)
        code            — search project memory only (file_map + decisions + tasks)
    """

    def __init__(self, timeline: TimelineStore, semantic: SemanticStore,
                 projects: ProjectStore, file_map: FileMapStore,
                 decisions: DecisionStore, tasks: TaskStore,
                 hub: DecisionHub | None = None,
                 patterns: Any | None = None,
                 reflections: Any | None = None,
                 vector: VectorAdapter | None = None):
        self._timeline = timeline
        self._semantic = semantic
        self._projects = projects
        self._file_map = file_map
        self._decisions = decisions
        self._tasks = tasks
        self._hub = hub
        self._patterns = patterns
        self._reflections = reflections

    def query(self, query_text: str, mode: str, depth: int = 0) -> dict:
        """Execute a query under the given mode with progressive depth.

        depth controls how deep into human memory to search:
            0 = all levels (backward compatible)
            1 = timeline only
            2 = timeline + semantic
            3 = timeline + semantic + patterns
            4 = timeline + semantic + patterns + reflections

        Returns:
            dict with keys:
              - mode: str
              - depth: int
              - results: list[QueryResult]
              - linked: list (cross-domain links, hybrid only)
        """
        if mode == "human":
            results = self._search_human(query_text, depth)
            return {"mode": mode, "depth": depth, "results": results, "linked": []}

        elif mode == "code":
            results = self._search_code(query_text)
            return {"mode": mode, "depth": depth, "results": results, "linked": []}

        else:  # hybrid (default)
            human_results = self._search_human(query_text, depth)
            code_results = self._search_code(query_text)

            # Cross-link through Decision Hub
            linked = []
            if self._hub:
                for r in human_results:
                    links = self._hub.query_by_human(r.source_table, r.source_id)
                    r.linked = links
                    linked.extend(links)
                for r in code_results:
                    # For file_map results, also check their linked_decisions
                    if r.source_table == "file_map":
                        # Get the actual file record to find linked decisions
                        fm = self._file_map.get_by_id(r.source_id)
                        if fm and fm.get("linked_decisions"):
                            for dec_id in fm["linked_decisions"]:
                                dl = self._hub.query_by_code("decision", dec_id)
                                r.linked.extend(dl)
                                linked.extend(dl)
                    links = self._hub.query_by_code(r.source_table, r.source_id)
                    r.linked = links
                    linked.extend(links)

            return {
                "mode": "hybrid",
                "results": human_results + code_results,
                "linked": linked,
                }

    # ── Domain searches ────────────────────────────────────────

    def _search_human(self, query_text: str, depth: int = 0) -> list[QueryResult]:
        """Search human memory domain with progressive depth.

        depth=0: all levels (backward compatible)
        depth=1: timeline only
        depth=2: timeline + semantic
        depth=3: timeline + semantic + patterns
        depth=4: timeline + semantic + patterns + reflections
        """
        results: list[QueryResult] = []

        # Multi-strategy search: try original query, then individual keywords
        def _multi_search(store_method, query):
            """Search with original query, then with individual terms."""
            found = []
            # Strategy 1: original query
            try:
                found = list(store_method(query) or [])
            except Exception:
                pass
            if found:
                return found
            # Strategy 2: extract individual Chinese/English terms
            terms = set()
            import re
            for m in re.finditer(r'[\u4e00-\u9fff]{2,6}', query):
                terms.add(m.group())
            for m in re.finditer(r'\b[A-Z][a-z]{2,}\b', query):
                terms.add(m.group())
            for term in list(terms)[:5]:
                try:
                    r = store_method(term) or []
                    found.extend(r)
                except Exception:
                    pass
            return found

        # L1: Timeline (always included)
        try:
            entries = _multi_search(self._timeline.search, query_text)
            for e in entries[:5]:
                results.append(QueryResult(
                    source_domain="human",
                    source_table="timeline",
                    source_id=e.get("id", ""),
                    summary=e.get("summary", "")[:200],
                ))
        except Exception:
            pass

        # L2: Semantic net (depth >= 2 or depth=0)
        if depth == 0 or depth >= 2:
            try:
                facts = _multi_search(self._semantic.search, query_text)
                for f in facts[:5]:
                    ename = f.get("entity_name", f.get("entity_id", ""))
                    results.append(QueryResult(
                        source_domain="human",
                        source_table="semantic",
                        source_id=f.get("id", ""),
                        summary=f"{ename} → {f.get('predicate', '')} → {f.get('object_value', '')}",
                    ))
            except Exception:
                pass

        # L3: Patterns (depth >= 3 or depth=0)
        if depth == 0 or depth >= 3:
            try:
                pats = self._patterns.search_by_topic(query_text)
                for p in pats[:3]:
                    results.append(QueryResult(
                        source_domain="human",
                        source_table="patterns",
                        source_id=p.get("id", ""),
                        summary=p.get("description", "")[:200],
                    ))
            except Exception:
                pass

        # L4: Reflections (depth >= 4 or depth=0)
        if depth == 0 or depth >= 4:
            try:
                refs = self._reflections.search(query_text)
                for r in refs[:2]:
                    results.append(QueryResult(
                        source_domain="human",
                        source_table="reflections",
                        source_id=r.get("id", ""),
                        summary=r.get("insight", "")[:200],
                    ))
            except Exception:
                pass

        return results

    def _search_code(self, query_text: str) -> list[QueryResult]:
        results: list[QueryResult] = []

        # File map (most important for code mode)
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

    def _search_semantic(self, query_text: str) -> list[dict]:
        """Vector-based semantic search (P4)."""
        if not self._vector:
            return []
        try:
            semantic_results = self._vector.search(query_text, top_k=3, min_score=0.4)
            enriched = []
            for sr in semantic_results:
                entry = self._timeline.get_by_id(sr["timeline_id"])
                if entry:
                    enriched.append({
                        "source_domain": "human",
                        "source_table": "timeline",
                        "source_id": sr["timeline_id"],
                        "summary": entry.get("summary", "")[:200],
                        "score": sr["score"],
                        "method": sr.get("method", "vector"),
                    })
            return enriched
        except Exception:
            return []

    # ── Shortcuts ─────────────────────────────────────────────

    def get_project_overview(self, project_id: str) -> dict | None:
        """Quick project overview (used by code mode)."""
        proj = self._projects.get_by_id(project_id)
        if not proj:
            return None
        files = self._file_map.query_by_project(project_id, limit=20)
        decisions = self._decisions.query_by_project(project_id)
        tasks = self._tasks.get_active(project_id)
        return {
            "project": proj,
            "file_count": len(files),
            "files": [f.get("path") for f in files[:10]],
            "decisions": [d.get("title") for d in decisions[:5]],
            "active_tasks": [t.get("title") for t in tasks],
        }
