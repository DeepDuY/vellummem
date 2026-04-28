"""Vector search adapter — semantic search via sentence-transformers.

v5 redesign (2026-04-29):
    - Single-layer vector search with pre-merged vectors
    - Each entry stores 1 merged vector = (summary_vec + tag0_vec + ... + tag4_vec) / 6
    - No LSI fallback, no keyword fallback
    - Mathematically equivalent to separate 6-vector scoring, but 6x faster

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

_DOWNLOAD_TIMEOUT = 120


def create_vector_adapter(db: VellumDB):
    """Factory: returns the best available vector adapter.

    v5: Only transformer model is supported. LSI is removed.
    """
    va = VectorAdapter(db)
    va.initialize()
    return va


class VectorAdapter:
    """Pre-merged vector search using transformer embeddings.

    Each entry stores 1 merged vector = avg(summary_vec + 5 tag_vecs).
    Query does 1 dot product per entry — mathematically equivalent to
    the old 6-vector scoring approach, but 6x faster.

    Model: BAAI/bge-small-zh-v1.5 (512 dim, ~36MB)
    """

    MODEL_NAME = os.environ.get(
        "VELLUM_TRANSFORMER_MODEL",
        "BAAI/bge-small-zh-v1.5"
    )

    def __init__(self, db: VellumDB):
        self.db = db
        self._model = None
        self._vectors: dict[str, np.ndarray] = {}
        self._corpus: list[dict] = []

    @property
    def engine(self) -> str:
        return "transformer"

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
            raise RuntimeError(f"Failed to load model: {error_result[0]}")
        if model_result[0] is None:
            raise TimeoutError(
                f"Model '{self.MODEL_NAME}' download timed out "
                f"(>{_DOWNLOAD_TIMEOUT}s). Check your network."
            )

        self._model = model_result[0]

        # Load corpus and vectors from DB
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT id, summary, tags FROM human_timeline "
            "WHERE summary IS NOT NULL AND summary != ''"
        ).fetchall()
        vec_rows = conn.execute(
            "SELECT entry_id, merged_blob FROM entry_vectors"
        ).fetchall()

        self._vectors = {}
        for vr in vec_rows:
            try:
                self._vectors[vr["entry_id"]] = pickle.loads(vr["merged_blob"])
            except Exception:
                pass

        self._corpus = []
        for r in rows:
            self._corpus.append({
                "id": r["id"],
                "summary": r["summary"],
                "tags": r["tags"],
            })

        # Compute vectors for entries without them (new entries from schema change)
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
        """Compute merged vector and persist to SQLite."""
        vec = self.encode_and_merge(summary, tags)
        self._vectors[entry_id] = vec
        self._corpus = [c for c in self._corpus if c["id"] != entry_id]
        self._corpus.append({"id": entry_id, "summary": summary, "tags": tags})
        conn = self.db.connect()
        conn.execute(
            "INSERT OR REPLACE INTO entry_vectors (entry_id, merged_blob) VALUES (?, ?)",
            (entry_id, pickle.dumps(vec))
        )
        conn.commit()

    def delete(self, entry_id: str):
        """Remove entry from memory and DB."""
        self._corpus = [c for c in self._corpus if c["id"] != entry_id]
        self._vectors.pop(entry_id, None)
        conn = self.db.connect()
        conn.execute("DELETE FROM entry_vectors WHERE entry_id = ?", (entry_id,))
        conn.commit()

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

    @classmethod
    def is_model_available(cls) -> bool:
        try:
            import sentence_transformers
            return True
        except ImportError:
            return False
