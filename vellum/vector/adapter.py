"""Vector search adapter — semantic search via sentence-transformers.

v6: Human-only. No code/project stores.
    Single-layer vector search with pre-merged vectors.
    Each entry stores 1 merged vector = (summary_vec + tag0_vec + ... + tag4_vec) / 6

Model: BAAI/bge-small-zh-v1.5 (512 dim, Chinese-optimized)
"""

from __future__ import annotations

import json
import os
import pickle
import threading
import typing
import numpy as np

if typing.TYPE_CHECKING:
    from ..db import VellumDB

from ..errors import InitError

_DOWNLOAD_TIMEOUT = 120


def create_vector_adapter(db: VellumDB):
    """Factory: returns the best available vector adapter."""
    va = VectorAdapter(db)
    va.initialize()
    return va


class VectorAdapter:
    """Pre-merged vector search using transformer embeddings."""

    MODEL_NAME = os.environ.get(
        "VELLUM_TRANSFORMER_MODEL",
        "BAAI/bge-small-zh-v1.5"
    )

    def __init__(self, db: VellumDB):
        self.db = db
        self._model = None
        self._vectors: dict[str, np.ndarray] = {}
        self._summary_vectors: dict[str, np.ndarray] = {}
        self._corpus: list[dict] = []

    @property
    def engine(self) -> str:
        return "transformer"

    @property
    def all_vectors(self) -> dict[str, np.ndarray]:
        """Expose in-memory vectors for group building (CPM)."""
        return self._vectors

    # ── Initialization ──────────────────────────────────────

    def initialize(self):
        """Load model and existing merged vectors from entry_vectors table."""
        import sentence_transformers

        model_result = [None]
        error_result = [None]

        def _load():
            try:
                model_result[0] = sentence_transformers.SentenceTransformer(
                    self.MODEL_NAME, local_files_only=True
                )
                return
            except Exception:
                pass
            try:
                old = os.environ.get("HF_ENDPOINT")
                if not old:
                    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
                model_result[0] = sentence_transformers.SentenceTransformer(
                    self.MODEL_NAME
                )
                if not old:
                    del os.environ["HF_ENDPOINT"]
            except Exception as e:
                error_result[0] = e

        loader = threading.Thread(target=_load, daemon=True)
        loader.start()
        loader.join(timeout=_DOWNLOAD_TIMEOUT)

        if error_result[0] is not None:
            raise InitError(f"Failed to load model: {error_result[0]}")
        if model_result[0] is None:
            raise InitError(
                f"Model '{self.MODEL_NAME}' download timed out "
                f"(>{_DOWNLOAD_TIMEOUT}s). Check your network."
            )

        self._model = model_result[0]

        # Load corpus and vectors from DB
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT id, summary, tags, create_timestamp FROM human_timeline "
            "WHERE summary IS NOT NULL AND summary != ''"
        ).fetchall()
        vec_rows = conn.execute(
            "SELECT entry_id, merged_blob, summary_blob FROM entry_vectors"
        ).fetchall()

        self._vectors = {}
        self._summary_vectors = {}
        for vr in vec_rows:
            try:
                self._vectors[vr["entry_id"]] = pickle.loads(vr["merged_blob"])
            except Exception:
                pass
            if vr["summary_blob"] is not None:
                try:
                    self._summary_vectors[vr["entry_id"]] = pickle.loads(vr["summary_blob"])
                except Exception:
                    pass

        self._corpus = []
        for r in rows:
            self._corpus.append({
                "id": r["id"],
                "summary": r["summary"],
                "tags": r["tags"],
                "create_timestamp": r["create_timestamp"],
            })

        # Compute vectors for entries without them
        need_encode = [
            c for c in self._corpus if c["id"] not in self._vectors
        ]
        if need_encode and self._model:
            for item in need_encode:
                vec = self.encode_and_merge(
                    item["summary"],
                    json.loads(item["tags"] or "[]")
                )
                self._vectors[item["id"]] = vec
                conn.execute(
                    "INSERT OR REPLACE INTO entry_vectors (entry_id, merged_blob) "
                    "VALUES (?, ?)",
                    (item["id"], pickle.dumps(vec))
                )
            conn.commit()

        # Backfill missing summary vectors
        need_sv = [
            c for c in self._corpus
            if c["id"] not in self._summary_vectors and c["summary"]
        ]
        if need_sv and self._model:
            for item in need_sv:
                sv = self._model.encode(item["summary"], normalize_embeddings=True)
                self._summary_vectors[item["id"]] = sv
                conn.execute(
                    "UPDATE entry_vectors SET summary_blob = ? WHERE entry_id = ?",
                    (pickle.dumps(sv), item["id"])
                )
            conn.commit()

    # ── Core API ────────────────────────────────────────────

    def encode_and_merge(self, summary: str, tags: list[str]) -> np.ndarray:
        """Encode summary + 5 tags → single merged vector.

        Formula: merged = (normalize(summary) + normalize(tag0) + ... + normalize(tag4)) / 6
        No re-normalization after merging (preserves dot product equivalence).
        """
        sv = self._model.encode(summary, normalize_embeddings=True)
        tv = self._model.encode(tags, normalize_embeddings=True)
        return (sv + tv.sum(axis=0)) / 6.0

    def store(self, entry_id: str, summary: str, tags: list[str]):
        """Compute merged vector + summary vector and persist to SQLite."""
        vec = self.encode_and_merge(summary, tags)
        sv = self._model.encode(summary, normalize_embeddings=True)
        self._vectors[entry_id] = vec
        self._summary_vectors[entry_id] = sv
        conn = self.db.connect()
        # 获取 create_timestamp
        row = conn.execute(
            "SELECT create_timestamp FROM human_timeline WHERE id = ?",
            (entry_id,)
        ).fetchone()
        ts = row["create_timestamp"] if row else 0
        self._corpus = [c for c in self._corpus if c["id"] != entry_id]
        self._corpus.append({
            "id": entry_id, "summary": summary, "tags": tags,
            "create_timestamp": ts,
        })
        conn.execute(
            "INSERT OR REPLACE INTO entry_vectors (entry_id, merged_blob, summary_blob) "
            "VALUES (?, ?, ?)",
            (entry_id, pickle.dumps(vec), pickle.dumps(sv))
        )
        conn.commit()

    def remove(self, entry_id: str):
        """Remove vectors from in-memory cache (DB delete handled by FK cascade)."""
        self._vectors.pop(entry_id, None)
        self._summary_vectors.pop(entry_id, None)
        self._corpus = [c for c in self._corpus if c["id"] != entry_id]

    def search(self, query: str, top_k: int = 3,
               score_threshold: float = 0.15) -> list[dict]:
        """Find semantically similar entries via merged vector dot products."""
        if not self._corpus or self._model is None:
            return []

        qv = self._model.encode(query, normalize_embeddings=True)

        scores = []
        for c in self._corpus:
            merged = self._vectors.get(c["id"])
            if merged is not None:
                score = float(qv @ merged)
                if score >= score_threshold:
                    scores.append((c["id"], score))

        scores.sort(key=lambda x: -x[1])
        return [
            {"entry_id": sid, "score": round(s, 4), "method": "transformer"}
            for sid, s in scores[:top_k]
        ]

    # ── 去重扫描 ──────────────────────────────────────────────────

    def scan_duplicates(self, threshold: float = 0.9,
                        skip_ids: set | None = None) -> list[dict]:
        """扫描全库摘要向量，找出相似度超过阈值的重复条目对。

        Args:
            threshold: 余弦相似度阈值
            skip_ids: 要跳过的 entry_id 集合（如已标记 time_sensitive 的）

        Returns:
            [{"duplicate": str, "keeper": str, "score": float}, ...]
            duplicate 是创建时间更晚的（将被标记为过期），
            keeper 是被保留的（创建时间更早）。
        """
        skip_ids = skip_ids or set()
        if len(self._summary_vectors) < 2:
            return []

        entries = [
            (c["id"], self._summary_vectors.get(c["id"]), c["create_timestamp"])
            for c in self._corpus
            if c["id"] in self._summary_vectors
            and c["summary"]
            and c["id"] not in skip_ids
        ]
        if len(entries) < 2:
            return []

        results = []
        for i in range(len(entries)):
            id_a, va, ts_a = entries[i]
            for j in range(i + 1, len(entries)):
                id_b, vb, ts_b = entries[j]
                score = float(va @ vb)  # 点积 = 余弦（均为单位向量）
                if score >= threshold:
                    if ts_a >= ts_b:
                        dup, keeper = id_a, id_b
                    else:
                        dup, keeper = id_b, id_a
                    results.append({
                        "duplicate": dup,
                        "keeper": keeper,
                        "score": round(score, 4),
                    })

        return results
