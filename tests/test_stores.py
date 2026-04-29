"""Test HumanTimelineStore with in-memory SQLite."""

import json
import os
import tempfile

import pytest

from vellum.db import VellumDB
from vellum.errors import StoreError
from vellum.stores.human_timeline import HumanTimelineStore


@pytest.fixture
def db():
    """Create a fresh in-memory DB for each test."""
    tmp = VellumDB(":memory:")
    schema = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "schemas", "schema.sql"
    )
    tmp.initialize(schema)
    return tmp


@pytest.fixture
def store(db):
    return HumanTimelineStore(db)


class TestTagsValidation:
    def test_requires_5_tags(self, store):
        """create() 必须提供 5 个 tag."""
        with pytest.raises(StoreError, match="必须提供 5 个 tag"):
            store.create(
                summary="test",
                tags=["a", "b"],
                category="conversation",
            )

    def test_requires_tags_not_none(self, store):
        with pytest.raises(StoreError, match="必须提供 5 个 tag"):
            store.create(
                summary="test",
                tags=None,
                category="conversation",
            )

    def test_empty_tags_rejected(self, store):
        with pytest.raises(StoreError, match="必须提供 5 个 tag"):
            store.create(
                summary="test",
                tags=[],
                category="conversation",
            )


class TestCategoryValidation:
    def test_invalid_category_raises(self, store):
        with pytest.raises(StoreError, match="无效 category"):
            store.create(
                summary="test",
                tags=["a", "b", "c", "d", "e"],
                category="invalid_cat",
            )

    def test_valid_categories_accepted(self, store):
        for cat in ("conversation", "knowledge", "document", "preference", "other"):
            entry = store.create(
                summary=f"test_{cat}",
                tags=["a", "b", "c", "d", "e"],
                category=cat,
            )
            assert "id" in entry


class TestCreateAndRetrieve:
    def test_create_returns_id(self, store):
        entry = store.create(
            summary="hello world",
            tags=["tag1", "tag2", "tag3", "tag4", "tag5"],
            category="conversation",
        )
        assert "id" in entry
        assert entry["id"] is not None

    def test_get_by_id_returns_entry(self, store):
        entry = store.create(
            summary="get test",
            tags=["a", "b", "c", "d", "e"],
            category="knowledge",
        )
        retrieved = store.get_by_id(entry["id"])
        assert retrieved is not None
        assert retrieved["summary"] == "get test"
        assert retrieved["category"] == "knowledge"

    def test_get_by_id_nonexistent(self, store):
        assert store.get_by_id("nonexistent") is None


class TestContextChunking:
    def test_context_text_stored(self, store):
        text = "这是一段测试上下文。"
        entry = store.create(
            summary="context test",
            tags=["a", "b", "c", "d", "e"],
            category="conversation",
            context_text=text,
        )
        assert "context_ids" in entry
        assert len(entry["context_ids"]) >= 1

    def test_append_context(self, store):
        entry = store.create(
            summary="append test",
            tags=["a", "b", "c", "d", "e"],
            category="conversation",
        )
        result = store.append_context(entry["id"], "追加内容")
        assert "new_context_ids" in result
        assert len(result["new_context_ids"]) >= 1

    def test_get_context_chunks(self, store):
        entry = store.create(
            summary="get context",
            tags=["a", "b", "c", "d", "e"],
            category="conversation",
            context_text="第一段。",
        )
        chunks = store.get_context_chunks(entry["id"])
        assert len(chunks) >= 1


class TestDBInit:
    def test_initialize_twice_is_idempotent(self):
        """initialize() 是幂等的."""
        tmp = VellumDB(":memory:")
        schema = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "schemas", "schema.sql"
        )
        tmp.initialize(schema)  # first
        tmp.initialize(schema)  # second — should not raise
        stats = tmp.stats()
        assert "human_timeline" in stats
        assert "config" in stats

    def test_config_defaults_exist(self, db):
        """initialize() 后 config 表有 2 条默认值."""
        conn = db.connect()
        rows = conn.execute("SELECT key, value FROM config").fetchall()
        keys = {r["key"] for r in rows}
        assert "vector_engine" in keys
        assert "score_threshold" in keys
