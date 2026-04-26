"""Vector search adapter — semantic search via LSI or sentence-transformers.

Architecture:
    - VectorAdapter: LSI via scikit-learn (always available, zero-download)
    - TransformerAdapter: sentence-transformers (optional, better quality)
    - create_vector_adapter(): factory that picks the best available engine

Strategy (auto-detect):
    sentence-transformers → LSI (fallback)

Environment variables:
    VELLUM_FORCE_VECTOR=LSI   — skip transformer auto-detect, force LSI
    VELLUM_TRANSFORMER_MODEL  — custom model name/path (default: all-MiniLM-L6-v2)
"""

from __future__ import annotations

import os
import pickle
import threading
import typing
import numpy as np

if typing.TYPE_CHECKING:
    from ..db import VellumDB


# ── Factory ────────────────────────────────────────────────────

_DOWNLOAD_TIMEOUT = 120  # seconds for model download

def create_vector_adapter(db: VellumDB):
    """Factory: returns the best available vector adapter.

    Priority:
        1. TransformerAdapter (sentence-transformers) — best quality
        2. VectorAdapter (scikit-learn LSI) — zero-download fallback

    When VELLUM_FORCE_VECTOR=LSI is set, skips transformer entirely.
    """
    # Allow user to force LSI (useful in offline/blocked networks)
    if os.environ.get("VELLUM_FORCE_VECTOR", "").upper() == "LSI":
        va = VectorAdapter(db)
        va.initialize()
        return va

    if TransformerAdapter.is_model_available():
        try:
            ta = TransformerAdapter(db)
            ta.initialize()
            return ta
        except Exception as exc:
            pass
    # Fallback to LSI
    va = VectorAdapter(db)
    va.initialize()
    return va


# ── LSI (scikit-learn, always available) ───────────────────────

class VectorAdapter:
    """Lightweight semantic search via LSI (scikit-learn).

    Uses TfidfVectorizer + TruncatedSVD for Latent Semantic Indexing.
    Zero downloads needed. Works on CPU, fast for small corpora.
    """

    def __init__(self, db: VellumDB):
        self.db = db
        self._corpus: list[dict] = []
        self._dirty = True
        self._vec = None
        self._svd = None
        self._X = None

    @property
    def engine(self) -> str:
        """Identifier for the current search engine."""
        return "lsi"

    # ── Public API ─────────────────────────────────────────────

    def initialize(self):
        """Load all existing timeline entries into the index."""
        conn = self.db.connect()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS timeline_embeddings (
                timeline_id TEXT PRIMARY KEY REFERENCES timeline(id),
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        conn.commit()
        rows = conn.execute(
            "SELECT id, summary FROM timeline WHERE summary IS NOT NULL AND summary != ''"
        ).fetchall()
        self._corpus = [{"id": r["id"], "text": r["summary"]} for r in rows]
        self._dirty = True

    def store(self, timeline_id: str, text: str):
        """Add an entry to the index."""
        self._corpus = [c for c in self._corpus if c["id"] != timeline_id]
        self._corpus.append({"id": timeline_id, "text": text})
        self._dirty = True
        conn = self.db.connect()
        conn.execute(
            "INSERT OR REPLACE INTO timeline_embeddings (timeline_id) VALUES (?)",
            (timeline_id,)
        )
        conn.commit()

    def delete(self, timeline_id: str):
        """Remove an entry."""
        self._corpus = [c for c in self._corpus if c["id"] != timeline_id]
        self._dirty = True

    def search(self, query: str, top_k: int = 5,
               min_score: float = 0.3) -> list[dict]:
        """Find semantically similar entries via LSI."""
        if not self._corpus:
            return []
        if self._dirty:
            self._build()
        if self._X is None:
            return []

        q = self._vec.transform([query])
        q_lsi = self._svd.transform(q)
        from sklearn.preprocessing import Normalizer
        Normalizer(copy=False).fit_transform(q_lsi)
        scores = (self._X @ q_lsi.T).flatten()

        results = [
            {"timeline_id": self._corpus[i]["id"], "score": float(scores[i]),
             "method": "lsi"}
            for i in range(len(scores)) if scores[i] >= min_score
        ]
        results.sort(key=lambda x: -x["score"])
        return results[:top_k]

    @classmethod
    def is_model_available(cls) -> bool:
        """Always True — LSI works with sklearn which is bundled."""
        return True

    # ── LSI internals ──────────────────────────────────────────

    def _build(self):
        """Build LSI model from corpus."""
        if not self._corpus:
            self._X = None
            return
        texts = [c["text"] for c in self._corpus]
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.decomposition import TruncatedSVD
        from sklearn.preprocessing import Normalizer
        self._vec = TfidfVectorizer(
            analyzer="char_wb", ngram_range=(2, 4),
            max_features=1000, sublinear_tf=True,
        )
        X = self._vec.fit_transform(texts)
        n = max(2, min(50, X.shape[0] - 1, X.shape[1] - 1))
        self._svd = TruncatedSVD(n_components=n, random_state=42)
        self._X = self._svd.fit_transform(X)
        Normalizer(copy=False).fit_transform(self._X)
        self._dirty = False


# ── sentence-transformers (optional, better quality) ───────────

class TransformerAdapter:
    """Semantic search via sentence-transformers (deep learning).

    Uses a pre-trained transformer model for sentence embeddings.
    Significantly better semantic understanding than LSI, especially
    for short text (<20 chars) and cross-lingual/cross-phrasing queries.

    Requires: pip install sentence-transformers
    Model: all-MiniLM-L6-v2 (~80MB, auto-downloaded on first use)

    Embeddings are persisted to SQLite as pickle BLOBs so they survive
    server restarts without re-encoding the entire corpus.
    """

    MODEL_NAME = os.environ.get("VELLUM_TRANSFORMER_MODEL", "all-MiniLM-L6-v2")

    def __init__(self, db: VellumDB):
        self.db = db
        self._model = None
        self._embeddings: dict[str, np.ndarray] = {}
        self._corpus: list[dict] = []

    @property
    def engine(self) -> str:
        """Identifier for the current search engine."""
        return "transformer"

    # ── Public API ─────────────────────────────────────────────

    def initialize(self):
        """Load model and existing embeddings.

        The model download is wrapped in a thread timeout so that
        blocked/offline networks don't hang the factory forever.
        After the first download, the model is cached locally by
        Hugging Face Hub (~80MB in {cache}/models--sentence-transformers--all-MiniLM-L6-v2).
        """
        import sentence_transformers

        # Load model with download timeout — use threading so we can
        # abort if Hugging Face is unreachable.
        model_result = [None]
        error_result = [None]

        def _load():
            try:
                # Try local cache first (no network, avoids blocked sites)
                model_result[0] = sentence_transformers.SentenceTransformer(
                    self.MODEL_NAME, local_files_only=True
                )
                return
            except Exception:
                pass  # Not cached — try network below
            try:
                # Network: use HF_ENDPOINT mirror if available, else hf-mirror.com fallback
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
            raise RuntimeError(f"Failed to load transformer model: {error_result[0]}")
        if model_result[0] is None:
            raise TimeoutError(
                f"Transformer model '{self.MODEL_NAME}' download timed out "
                f"(>{_DOWNLOAD_TIMEOUT}s). "
                f"Set VELLUM_FORCE_VECTOR=LSI to skip, or check your network."
            )

        self._model = model_result[0]

        # Ensure storage table
        conn = self.db.connect()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS transformer_embeddings (
                timeline_id TEXT PRIMARY KEY,
                vector BLOB,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        conn.commit()

        # Load existing corpus from timeline
        rows = conn.execute(
            "SELECT id, summary FROM timeline WHERE summary IS NOT NULL AND summary != ''"
        ).fetchall()

        # Load persisted embeddings
        emb_rows = conn.execute(
            "SELECT timeline_id, vector FROM transformer_embeddings"
        ).fetchall()
        emb_map = {r["timeline_id"]: r["vector"] for r in emb_rows}

        self._corpus = []
        self._embeddings = {}

        for r in rows:
            self._corpus.append({"id": r["id"], "text": r["summary"]})
            if r["id"] in emb_map:
                try:
                    self._embeddings[r["id"]] = pickle.loads(emb_map[r["id"]])
                except Exception:
                    pass

        # For any corpus entries without embeddings, compute them now
        need_encode = [c for c in self._corpus if c["id"] not in self._embeddings]
        if need_encode and self._model:
            texts = [c["text"] for c in need_encode]
            vecs = self._model.encode(texts, normalize_embeddings=True,
                                      show_progress_bar=False)
            for c, vec in zip(need_encode, vecs):
                self._embeddings[c["id"]] = vec.astype(np.float32)

    def store(self, timeline_id: str, text: str):
        """Add an entry to the index and persist embedding."""
        # Update in-memory
        self._corpus = [c for c in self._corpus if c["id"] != timeline_id]
        self._corpus.append({"id": timeline_id, "text": text})

        # Compute & cache embedding
        if self._model:
            vec = self._model.encode([text], normalize_embeddings=True,
                                     show_progress_bar=False)[0]
            self._embeddings[timeline_id] = vec.astype(np.float32)

            # Persist to SQLite
            conn = self.db.connect()
            conn.execute(
                "INSERT OR REPLACE INTO transformer_embeddings (timeline_id, vector) VALUES (?, ?)",
                (timeline_id, pickle.dumps(vec.astype(np.float32)))
            )
            conn.commit()

    def delete(self, timeline_id: str):
        """Remove an entry from memory and database."""
        self._corpus = [c for c in self._corpus if c["id"] != timeline_id]
        self._embeddings.pop(timeline_id, None)
        conn = self.db.connect()
        conn.execute(
            "DELETE FROM transformer_embeddings WHERE timeline_id = ?",
            (timeline_id,)
        )
        conn.commit()

    def search(self, query: str, top_k: int = 5,
               min_score: float = 0.3) -> list[dict]:
        """Find semantically similar entries via transformer embeddings.

        Uses cosine similarity on normalized embeddings.
        """
        if not self._corpus or self._model is None:
            return []

        q_vec = self._model.encode([query], normalize_embeddings=True,
                                   show_progress_bar=False)[0]

        scores = []
        for c in self._corpus:
            emb = self._embeddings.get(c["id"])
            if emb is not None:
                score = float(q_vec @ emb)  # cosine similarity (normalized)
                if score >= min_score:
                    scores.append((c["id"], score))

        scores.sort(key=lambda x: -x[1])
        return [
            {"timeline_id": sid, "score": score, "method": "transformer"}
            for sid, score in scores[:top_k]
        ]

    @classmethod
    def is_model_available(cls) -> bool:
        """Check if sentence-transformers is installed."""
        try:
            import sentence_transformers  # noqa: F401
            return True
        except ImportError:
            return False
