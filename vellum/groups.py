"""Memory group manager — Clique Percolation Method (CPM, arbitrary k).

Groups semantically similar entries (cosine similarity ≥ threshold)
using the Clique Percolation Method with configurable k.

Two k-cliques belong to the same community when they share k-1 nodes.
Communities can overlap (one entry may belong to multiple groups).
"""

from __future__ import annotations

import json
import random
import string
import time
import typing
from collections import defaultdict
from itertools import combinations

import numpy as np

if typing.TYPE_CHECKING:
    from .db import VellumDB
    from .vector.adapter import VectorAdapter


def _next_short_id() -> str:
    ts = time.strftime("%H%M%S")
    rid = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"{ts}_{rid}"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _find_k_cliques(adj: dict[str, set], k: int, entry_ids: list[str]) -> list[frozenset]:
    """Find all k-cliques in the graph defined by adjacency list.

    Uses bottom-up extension: 2-cliques → 3-cliques → ... → k-cliques.
    Each step finds candidates by intersecting adjacency sets of all members.

    Args:
        adj: {node: {neighbors}}
        k: Target clique size (k >= 2)
        entry_ids: All node IDs

    Returns:
        List of frozensets, each containing k node IDs forming a clique
    """
    if k < 2:
        return []

    # All edges (2-cliques)
    edges = []
    for a in entry_ids:
        for b in adj[a]:
            if b > a:
                edges.append(frozenset({a, b}))
    if k == 2:
        return edges

    cur = edges
    for size in range(3, k + 1):
        nxt = []
        seen = set()
        for clique in cur:
            # Nodes adjacent to ALL members of this clique
            candidates = None
            for m in clique:
                if candidates is None:
                    candidates = set(adj[m])
                else:
                    candidates &= adj[m]
                if not candidates:
                    break
            if candidates:
                for c in candidates:
                    if c in clique:
                        continue
                    nc = frozenset(clique | {c})
                    if nc not in seen:
                        seen.add(nc)
                        nxt.append(nc)
        cur = nxt
        if not cur:
            break

    return cur


class GroupManager:
    """Builds and queries memory groups via CPM (arbitrary k)."""

    def __init__(self, db: VellumDB, vector: VectorAdapter):
        self.db = db
        self._vector = vector

    # ── CPM Group Building ──────────────────────────────────

    def build_groups(self, k: int = 4, threshold: float = 0.8) -> dict:
        """Run CPM and store groups in memory_groups table.

        Args:
            k: Clique size (default 4). Must be >= 2.
            threshold: Minimum cosine similarity for an edge.

        Returns:
            {"groups_built": int, "entries": int}
        """
        vectors = self._vector.all_vectors
        entry_ids = list(vectors.keys())
        k = max(2, k)

        if len(entry_ids) < k:
            conn = self.db.connect()
            conn.execute("DELETE FROM memory_groups")
            conn.commit()
            return {"groups_built": 0, "entries": len(entry_ids)}

        # 1. Build adjacency list from pairwise cosine similarity
        adj = {eid: set() for eid in entry_ids}
        for a, b in combinations(entry_ids, 2):
            sim = float(np.dot(vectors[a], vectors[b]))
            if sim >= threshold:
                adj[a].add(b)
                adj[b].add(a)

        # 2. Find all k-cliques using generalized algorithm
        cliques = _find_k_cliques(adj, k, entry_ids)
        if not cliques:
            conn = self.db.connect()
            conn.execute("DELETE FROM memory_groups")
            conn.commit()
            return {"groups_built": 0, "entries": len(entry_ids)}

        # 3. Build clique adjacency (share k-1 nodes → edge)
        clique_adj = defaultdict(set)
        for i, c1 in enumerate(cliques):
            for j, c2 in enumerate(cliques):
                if i >= j:
                    continue
                if len(c1 & c2) >= k - 1:
                    clique_adj[i].add(j)
                    clique_adj[j].add(i)

        # 4. Connected components in clique graph → communities
        visited = set()
        communities = []
        for i in range(len(cliques)):
            if i in visited:
                continue
            stack = [i]
            community_nodes = set()
            while stack:
                node = stack.pop()
                if node in visited:
                    continue
                visited.add(node)
                community_nodes.update(cliques[node])
                for nb in clique_adj[node]:
                    if nb not in visited:
                        stack.append(nb)
            communities.append(sorted(community_nodes))

        # 5. Store in DB (replace all)
        conn = self.db.connect()
        conn.execute("DELETE FROM memory_groups")
        now = _now_ms()
        for members in communities:
            gid = f"grp_{_next_short_id()}"
            conn.execute("""
                INSERT INTO memory_groups (id, entry_ids, member_count, create_timestamp)
                VALUES (?, ?, ?, ?)
            """, (gid, json.dumps(members, ensure_ascii=False), len(members), now))
        conn.commit()

        return {"groups_built": len(communities), "entries": len(entry_ids)}

    # ── Query ───────────────────────────────────────────────

    def get_groups_for_entry(self, entry_id: str) -> list[dict]:
        """Return all groups containing this entry_id."""
        conn = self.db.connect()
        rows = conn.execute("SELECT * FROM memory_groups").fetchall()
        result = []
        for row in rows:
            members = json.loads(row["entry_ids"] or "[]")
            if entry_id in members:
                result.append({
                    "id": row["id"],
                    "entry_ids": members,
                    "member_count": row["member_count"],
                })
        return result

    def list_groups(self) -> list[dict]:
        """Return all groups with their metadata."""
        conn = self.db.connect()
        rows = conn.execute("SELECT * FROM memory_groups ORDER BY create_timestamp DESC").fetchall()
        return [{
            "id": row["id"],
            "entry_ids": json.loads(row["entry_ids"] or "[]"),
            "member_count": row["member_count"],
            "create_timestamp": row["create_timestamp"],
        } for row in rows]

    def get_group_members(self, group_id: str) -> dict | None:
        """Return entry_ids in this group."""
        conn = self.db.connect()
        row = conn.execute(
            "SELECT * FROM memory_groups WHERE id = ?", (group_id,)
        ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "entry_ids": json.loads(row["entry_ids"] or "[]"),
            "member_count": row["member_count"],
        }

