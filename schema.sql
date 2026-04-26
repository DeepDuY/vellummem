-- =============================================================================
-- Vellum — SQLite Schema v1
-- AI 记忆系统：人的记忆域 + 项目记忆域 + Decision Hub 枢纽
-- 一个 .db 文件，零依赖
-- =============================================================================

-- =============================================================================
-- 1. 人的记忆域 — 时间线
-- =============================================================================

CREATE TABLE IF NOT EXISTS timeline (
    id               TEXT PRIMARY KEY,          -- "s_20260426_001"
    mode             TEXT DEFAULT 'hybrid',     -- 该会话时的记忆模式
    project_id       TEXT,                      -- 关联项目（可选）
    session_start    TEXT NOT NULL,             -- ISO 8601 datetime
    session_end      TEXT NOT NULL,
    summary          TEXT NOT NULL,             -- AI 生成的会话摘要
    key_moments      TEXT,                      -- JSON: [{type, content, turn_index}]
    tags             TEXT,                      -- JSON: ["认证", "架构决策"]
    final_state      TEXT,                      -- 会话结束时状态
    linked_decisions TEXT,                      -- JSON: ["dec_001"]
    linked_entities  TEXT,                      -- JSON: ["JWT", "Go"]
    importance       INTEGER DEFAULT 3,         -- 1-5，AI 自动评估
    created_at       TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_timeline_start
    ON timeline(session_start DESC);
CREATE INDEX IF NOT EXISTS idx_timeline_importance
    ON timeline(importance DESC);
CREATE INDEX IF NOT EXISTS idx_timeline_project
    ON timeline(project_id);

-- FTS5 全文搜索索引
CREATE VIRTUAL TABLE IF NOT EXISTS timeline_fts USING fts5(
    id UNINDEXED,
    summary,
    tags,
    content='timeline',
    content_rowid='rowid',
    tokenize='unicode61'
);

-- =============================================================================
-- 2. 人的记忆域 — 语义网（实体）
-- =============================================================================

CREATE TABLE IF NOT EXISTS semantic_entities (
    id         TEXT PRIMARY KEY,                -- "entity_python"
    name       TEXT NOT NULL UNIQUE,            -- 规范名称
    aliases    TEXT,                            -- JSON: ["Python", "py"]
    type       TEXT,                            -- "language" | "tool" | "person" | "concept"
    importance INTEGER DEFAULT 1,               -- 1-5
    summary    TEXT,                            -- 一句话摘要
    created_at TEXT DEFAULT (datetime('now','localtime')),
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);

-- =============================================================================
-- 3. 人的记忆域 — 语义网（事实，带版本链）
-- =============================================================================

CREATE TABLE IF NOT EXISTS semantic_facts (
    id               TEXT PRIMARY KEY,          -- "fact_001"
    entity_id        TEXT NOT NULL REFERENCES semantic_entities(id),
    predicate        TEXT NOT NULL,             -- "喜欢" | "使用" | "属于"
    object_value     TEXT NOT NULL,
    confidence       TEXT DEFAULT 'mid',        -- "high" | "mid" | "low"
    evidence_session TEXT REFERENCES timeline(id),
    valid_from       TEXT,                      -- ISO date, null=未知
    valid_to         TEXT,                      -- null = 至今有效
    previous_version TEXT REFERENCES semantic_facts(id),
    tags             TEXT,                      -- JSON
    created_at       TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_facts_entity
    ON semantic_facts(entity_id);
CREATE INDEX IF NOT EXISTS idx_facts_predicate
    ON semantic_facts(predicate);
CREATE INDEX IF NOT EXISTS idx_facts_entity_predicate
    ON semantic_facts(entity_id, predicate);
CREATE INDEX IF NOT EXISTS idx_facts_valid
    ON semantic_facts(valid_from, valid_to);
CREATE INDEX IF NOT EXISTS idx_facts_evidence
    ON semantic_facts(evidence_session);

-- =============================================================================
-- 4. 人的记忆域 — 模式库
-- =============================================================================

CREATE TABLE IF NOT EXISTS patterns (
    id                TEXT PRIMARY KEY,         -- "pat_001"
    description       TEXT NOT NULL,
    detail            TEXT,
    evidence_sessions TEXT,                     -- JSON: ["s_202404", "s_202604"]
    confidence        REAL DEFAULT 0.5,         -- 0~1
    trigger_topics    TEXT,                     -- JSON: ["技术选型", "迁移"]
    category          TEXT,                     -- "行为模式" | "偏好模式"
    created_at        TEXT DEFAULT (datetime('now','localtime')),
    updated_at        TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_patterns_category
    ON patterns(category);

-- =============================================================================
-- 5. 人的记忆域 — 反射层
-- =============================================================================

CREATE TABLE IF NOT EXISTS reflections (
    id                  TEXT PRIMARY KEY,       -- "ref_001"
    insight             TEXT NOT NULL,
    supporting_sessions TEXT,                   -- JSON: ["s_202404", "s_202501"]
    supporting_patterns TEXT,                   -- JSON: ["pat_001"]
    confidence          TEXT DEFAULT 'mid',
    category            TEXT,                   -- "决策风格" | "技术倾向"
    generated_at        TEXT
);

-- =============================================================================
-- 6. 项目记忆域 — 项目卡片
-- =============================================================================

CREATE TABLE IF NOT EXISTS projects (
    id            TEXT PRIMARY KEY,             -- "proj_deepchat"
    name          TEXT NOT NULL,
    root_path     TEXT NOT NULL UNIQUE,
    tech_stack    TEXT,                         -- JSON: [{name, version, purpose}]
    main_modules  TEXT,                         -- JSON: [{name, path, desc}]
    active_branch TEXT,
    description   TEXT,
    last_scanned  TEXT,                         -- ISO datetime
    created_at    TEXT DEFAULT (datetime('now','localtime')),
    updated_at    TEXT DEFAULT (datetime('now','localtime'))
);

-- =============================================================================
-- 7. 项目记忆域 — 模块/文件索引
-- =============================================================================
-- 最频繁查询的表，索引最全

CREATE TABLE IF NOT EXISTS file_map (
    id               TEXT PRIMARY KEY,          -- "file_auth_middleware"
    project_id       TEXT NOT NULL REFERENCES projects(id),
    path             TEXT NOT NULL,             -- "src/auth/middleware.ts"
    module           TEXT,                      -- "auth"
    summary          TEXT,
    key_symbols      TEXT,                      -- JSON: [{name, type, desc}]
    depends_on       TEXT,                      -- JSON: ["src/utils/jwt.ts"]
    linked_decisions TEXT,                      -- JSON: ["dec_001"]
    last_modified    TEXT,
    change_count     INTEGER DEFAULT 0,
    created_at       TEXT DEFAULT (datetime('now','localtime')),
    updated_at       TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(project_id, path)
);

CREATE INDEX IF NOT EXISTS idx_filemap_project
    ON file_map(project_id);
CREATE INDEX IF NOT EXISTS idx_filemap_module
    ON file_map(module, project_id);
CREATE INDEX IF NOT EXISTS idx_filemap_path
    ON file_map(path);

-- FTS5 全文搜索索引
CREATE VIRTUAL TABLE IF NOT EXISTS file_map_fts USING fts5(
    id UNINDEXED,
    path,
    module,
    summary,
    key_symbols,
    content='file_map',
    content_rowid='rowid',
    tokenize='unicode61'
);

-- =============================================================================
-- 8. 项目记忆域 — 决策日志
-- =============================================================================

CREATE TABLE IF NOT EXISTS decisions (
    id              TEXT PRIMARY KEY,           -- "dec_001"
    project_id      TEXT REFERENCES projects(id),
    title           TEXT NOT NULL,
    body            TEXT,
    alternatives    TEXT,                       -- JSON: [{方案, 否决原因}]
    affected_files  TEXT,                       -- JSON: ["src/auth/middleware.ts"]
    linked_session  TEXT REFERENCES timeline(id),
    tags            TEXT,                       -- JSON: ["认证", "安全"]
    status          TEXT DEFAULT 'planned',     -- "planned" | "implemented" | "abandoned"
    made_at         TEXT,
    created_at      TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_decisions_project
    ON decisions(project_id);
CREATE INDEX IF NOT EXISTS idx_decisions_session
    ON decisions(linked_session);
CREATE INDEX IF NOT EXISTS idx_decisions_status
    ON decisions(status);

-- =============================================================================
-- 9. 项目记忆域 — 任务上下文
-- =============================================================================

CREATE TABLE IF NOT EXISTS tasks (
    id               TEXT PRIMARY KEY,          -- "task_auth_refactor"
    project_id       TEXT NOT NULL REFERENCES projects(id),
    title            TEXT NOT NULL,
    status           TEXT DEFAULT 'planned',    -- "planned" | "wip" | "blocked" | "done"
    progress_pct     INTEGER DEFAULT 0,
    progress_detail  TEXT,
    related_sessions TEXT,                      -- JSON: ["s_20260425"]
    related_files    TEXT,                      -- JSON: ["file_auth_middleware"]
    blockers         TEXT,                      -- JSON: ["等待API文档"]
    next_action      TEXT,
    created_at       TEXT DEFAULT (datetime('now','localtime')),
    updated_at       TEXT DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_tasks_project
    ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status
    ON tasks(status);

-- =============================================================================
-- 10. Decision Hub — 枢纽层（双向耦合）
-- =============================================================================
-- 不存储记忆数据，只存储 "人侧 ↔ 项目侧" 的链接关系

CREATE TABLE IF NOT EXISTS decision_hub (
    id                TEXT PRIMARY KEY,          -- "link_001"
    human_source_type TEXT NOT NULL,             -- "timeline" | "semantic"
    human_source_id   TEXT NOT NULL,
    code_source_type  TEXT NOT NULL,             -- "decision" | "file_map"
    code_source_id    TEXT NOT NULL,
    link_type         TEXT NOT NULL,             -- "决策来源" | "代码体现" | "问题引发"
    rationale         TEXT,
    created_at        TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(human_source_type, human_source_id,
           code_source_type, code_source_id)
);

CREATE INDEX IF NOT EXISTS idx_hub_human
    ON decision_hub(human_source_type, human_source_id);
CREATE INDEX IF NOT EXISTS idx_hub_code
    ON decision_hub(code_source_type, code_source_id);

-- =============================================================================
-- 11. 系统配置
-- =============================================================================

CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- 初始数据
INSERT OR IGNORE INTO config VALUES ('schema_version', '1');
INSERT OR IGNORE INTO config VALUES ('current_project', '');
INSERT OR IGNORE INTO config VALUES ('last_maintenance', '');

-- =============================================================================
-- 12. 视图（便捷查询）
-- =============================================================================

-- 当前活跃任务
CREATE VIEW IF NOT EXISTS v_active_tasks AS
SELECT * FROM tasks
WHERE status IN ('planned', 'wip', 'blocked');

-- 最近时间线
CREATE VIEW IF NOT EXISTS v_recent_timeline AS
SELECT * FROM timeline
ORDER BY session_start DESC
LIMIT 20;

-- 决策及其枢纽关联
CREATE VIEW IF NOT EXISTS v_decisions_linked AS
SELECT
    d.*,
    dh.human_source_type,
    dh.human_source_id,
    dh.link_type,
    dh.rationale
FROM decisions d
LEFT JOIN decision_hub dh
    ON dh.code_source_type = 'decision'
    AND dh.code_source_id = d.id;

-- 当前有效事实（valid_to IS NULL）
CREATE VIEW IF NOT EXISTS v_current_facts AS
SELECT * FROM semantic_facts
WHERE valid_to IS NULL;

-- =============================================================================
-- 结束
-- =============================================================================
