-- VellumMem Schema v5 (2026-04-29)
-- Retrieval redesign: single-layer vector search with pre-merged vectors
-- Code domain remains unchanged

-- ── Human Memory ─────────────────────────────────────────

CREATE TABLE IF NOT EXISTS human_timeline (
    id                        TEXT PRIMARY KEY,             -- YMD_HMS_5RAND
    session_start             TEXT NOT NULL,                -- ISO datetime
    session_end               TEXT NOT NULL,                -- ISO datetime
    summary                   TEXT DEFAULT '',              -- 上限 200 字
    tags                      TEXT DEFAULT '[]',            -- JSON 数组，固定 5 个
    conversation_context_link TEXT DEFAULT '[]',            -- JSON 数组
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
    merged_blob BLOB NOT NULL   -- pickle.dumps(np.ndarray(float32, 512))
);

-- ── Project Memory (unchanged) ───────────────────────────

CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    project_id  TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    root_path   TEXT NOT NULL DEFAULT '',
    description TEXT DEFAULT '',
    tech_stack  TEXT DEFAULT '[]',
    created_at  TEXT DEFAULT (datetime('now','localtime')),
    updated_at  TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS file_map (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    path            TEXT NOT NULL,
    module          TEXT DEFAULT '',
    summary         TEXT DEFAULT '',
    key_symbols     TEXT DEFAULT '[]',
    depends_on      TEXT DEFAULT '[]',
    linked_decisions TEXT DEFAULT '[]',
    last_modified   TEXT DEFAULT '',
    change_count    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS decisions (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    body            TEXT DEFAULT '',
    alternatives    TEXT DEFAULT '[]',
    affected_files  TEXT DEFAULT '[]',
    linked_session  TEXT DEFAULT '',
    tags            TEXT DEFAULT '[]',
    status          TEXT DEFAULT 'planned',
    created_at      TEXT DEFAULT (datetime('now','localtime')),
    updated_at      TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS tasks (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    status          TEXT DEFAULT 'planned',
    progress_pct    INTEGER DEFAULT 0,
    progress_detail TEXT DEFAULT '',
    related_sessions TEXT DEFAULT '[]',
    related_files   TEXT DEFAULT '[]',
    blockers        TEXT DEFAULT '[]',
    next_action     TEXT DEFAULT '',
    created_at      TEXT DEFAULT (datetime('now','localtime')),
    updated_at      TEXT DEFAULT (datetime('now','localtime'))
);

-- ── System Config ────────────────────────────────────────

CREATE TABLE IF NOT EXISTS config (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL DEFAULT '',
    type        TEXT NOT NULL DEFAULT 'str',
    description TEXT DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now','localtime')),
    updated_at  TEXT DEFAULT (datetime('now','localtime'))
);
