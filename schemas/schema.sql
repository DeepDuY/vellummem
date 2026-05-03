-- =============================================================
-- VellumMem 数据库 Schema v6
-- Human-only mode. No code/project tables. No mode dispatch.
-- Memory groups via Clique Percolation Method (CPM, k=3).
-- =============================================================

-- ── 清理旧表（v5 及更早版本） ──────────────────────────────
DROP TABLE IF EXISTS projects;
DROP TABLE IF EXISTS file_map;
DROP TABLE IF EXISTS decisions;
DROP TABLE IF EXISTS tasks;
DROP VIEW  IF EXISTS v_active_tasks;
DROP TABLE IF EXISTS timeline;
DROP TABLE IF EXISTS timeline_fts;
DROP TABLE IF EXISTS timeline_embeddings;
DROP TABLE IF EXISTS semantic_entities;
DROP TABLE IF EXISTS semantic_facts;
DROP TABLE IF EXISTS patterns;
DROP TABLE IF EXISTS reflections;
DROP TABLE IF EXISTS decision_hub;
DROP TABLE IF EXISTS transformer_embeddings;
DROP TABLE IF EXISTS human_timeline_embeddings;
DROP TABLE IF EXISTS human_transformer_embeddings;

-- ── 配置表 ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS config (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL DEFAULT '',
    type        TEXT NOT NULL DEFAULT 'str',
    description TEXT DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now','localtime')),
    updated_at  TEXT DEFAULT (datetime('now','localtime'))
);

-- ── human 记忆核心表 ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS human_timeline (
    id                        TEXT PRIMARY KEY,
    summary                   TEXT DEFAULT '',
    tags                      TEXT DEFAULT '[]',
    conversation_context_link TEXT DEFAULT '[]',
    category                  TEXT DEFAULT 'conversation',
    is_time_sensitive         INTEGER DEFAULT 0,
    create_timestamp          INTEGER NOT NULL,
    ttl_timestamp             INTEGER DEFAULT NULL
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

-- ── 记忆分组表（CPM k=3，启动时自动构建） ─────────────────
CREATE TABLE IF NOT EXISTS memory_groups (
    id               TEXT PRIMARY KEY,
    entry_ids        TEXT NOT NULL DEFAULT '[]',
    member_count     INTEGER DEFAULT 0,
    create_timestamp INTEGER NOT NULL
);
