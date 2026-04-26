# Vellum — 记忆系统架构设计 v2

> 羊皮纸 — 古老的记录载体，记忆刻在上面
> 场景感知的 AI 记忆系统
> 人的记忆域 × 项目记忆域 × Decision Hub 双向耦合
> 默认 hybrid 混合模式，AI 显式管理模式切换
> 设计日期：2026-04-26

---

## 目录

1. [设计理念](#1-设计理念)
2. [总体架构](#2-总体架构)
3. [模式定义](#3-模式定义)
4. [MCP 接口设计](#4-mcp-接口设计)
5. [Human Memory Domain — 人的记忆域](#5-human-memory-domain--人的记忆域)
6. [Project Memory Domain — 项目记忆域](#6-project-memory-domain--项目记忆域)
7. [Decision Hub — 枢纽层](#7-decision-hub--枢纽层)
8. [检索协议](#8-检索协议)
9. [写入与合成协议](#9-写入与合成协议)
10. [SQLite 存储设计](#10-sqlite-存储设计)
11. [完整示例流](#11-完整示例流)
12. [落地路径](#12-落地路径)

---

## 1. 设计理念

### 核心矛盾

```
记忆需要"存得下、找得到"
  但存得越细 → 找得越慢
  存得越粗 → 找得到但细节不够
```

### 设计原则

| 原则 | 解释 |
|------|------|
| **渐进成本** | 从最便宜的检索开始，不够再花更大代价 |
| **默认全量** | 默认 hybrid 模式两边都搜，AI 只在需要优化时才缩窄范围 |
| **AI 显式管理** | 模式切换由 AI 主动调用，不依赖规则引擎猜测 |
| **结构化优先** | 实体/路径/时间匹配比向量搜索更快更准，优先使用 |
| **证据链完整** | 每条事实都能追溯回原始会话 |
| **耦合而不合并** | 不同记忆域各自最优存储，通过轻量枢纽跳转 |
| **可降级** | 完全不依赖向量也能正常工作 |
| **零外部依赖** | 一个 SQLite 文件存所有记忆，无需额外数据库服务 |

---

## 2. 总体架构

```
                    ┌──────────────────────────┐
                    │      AI Assistant          │
                    │  (Claude / DeepChat)       │
                    │                            │
                    │  memory_init()             │
                    │  memory_query(query)       │  ← 默认 hybrid
                    │  memory_set_mode("code")   │  ← AI 显式切换
                    │  memory_write(data)        │
                    └───────────┬────────────────┘
                                │  MCP Protocol (stdio)
                                ▼
                    ┌──────────────────────────┐
                    │    Vellum MCP Server      │
                    │    (Python FastMCP)       │
                    │                           │
                    │  会话状态: {               │
                    │    mode: "hybrid",        │  ← 默认
                    │    sticky: true           │  ← 不切换就不变
                    │  }                        │
                    │                           │
                    │  Route by mode:           │
                    │    hybrid → H + P + Hub   │
                    │    human  → H only        │
                    │    code   → P only        │
                    └───────────┬────────────────┘
                                │
         ┌──────────────────────┼──────────────────────┐
         │                      │                      │
         ▼                      ▼                      ▼
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│  人的记忆域        │  │  项目记忆域        │  │  Decision Hub     │
│  Human Memory     │  │  Project Memory  │  │  (枢纽层)          │
│                   │  │                  │  │                   │
│  SQLite 5 表:      │  │  SQLite 4 表:    │  │  SQLite 1 表:     │
│  timeline         │  │  projects        │  │  decision_hub     │
│  semantic_entities│  │  file_map        │  │                   │
│  semantic_facts   │  │  decisions       │  │  不存数据          │
│  patterns         │  │  tasks           │  │  只存链接          │
│  reflections      │  │                  │  │                   │
└──────────────────┘  └──────────────────┘  └──────────────────┘
```

### 关键设计决策

| 决策 | 说明 |
|------|------|
| **默认 hybrid** | 不需要 AI 一开始就选模式，`memory_init()` 无需传参 |
| **AI 管理模式** | 不设规则引擎，AI 通过 `memory_set_mode` 显式切换 |
| **Mode 是状态的，不是实时的** | 设定后保持 sticky，直到下一次显式切换 |
| **"缩窄"而不是"切换"** | hybrid 是完整版，human/code 是优化子集 |
| **一个 SQLite 文件** | 12 表 + 2 FTS5，`vellum.db` 存所有 |

---

## 3. 模式定义

### 三种模式

| 模式 | 含义 | 检索范围 | 典型场景 |
|------|------|---------|---------|
| **`hybrid`**（默认） | 两边都搜 + 枢纽关联 | 人的记忆域 + 项目记忆域 + Decision Hub | **大部分情况**，不用思考 |
| `human` | 只搜人的记忆 | Timeline + Semantic + Pattern + Reflection | 纯回忆/聊天，不想看到文件结果 |
| `code` | 只搜项目的记忆 | Project Card + File Map + Decision Log + Task | 纯写代码，不想看到聊天记录 |

### 模式的 sticky 特性

```
memory_init()                        → mode = hybrid（默认）
memory_query("认证模块在哪")          → hybrid，两边搜
memory_query("上次讨论的方案")        → hybrid，两边搜

memory_set_mode("code")              → AI 发现只需要代码
memory_query("middleware.ts")        → code，只搜项目侧
memory_query("login.tsx 改了啥")      → code，只搜项目侧

memory_set_mode("hybrid")            → AI 需要回忆决策原因
memory_query("为什么用JWT")           → hybrid，两边搜 + 枢纽关联
```

**一个模式用到底，不切也行：**

```
memory_init()
memory_query("帮我看看这个项目")       → hybrid → 返回项目卡片+文件索引+可能的相关讨论
memory_query("auth模块的文件")         → hybrid → 返回文件列表
memory_query("为什么这里用JWT")         → hybrid → 通过 Decision Hub 关联到讨论
                                    ↑ 全程不切模式，hybrid 兜住一切
```

---

## 4. MCP 接口设计

### Tool: memory_init

```
作用: 初始化记忆上下文
参数:
  project_path: str (可选)  → 指定项目路径，启动项目记忆域
说明: 不传 mode，默认 hybrid
      mode 初始化后一直保持，直到 memory_set_mode
```

### Tool: memory_query

```
作用: 检索记忆
参数:
  query: str (必填)         → 检索内容
  mode: str (可选)          → "hybrid" | "human" | "code"
                             临时覆盖当前 mode，不影响 sticky
返回: 根据当前 mode 走对应检索管道
说明: 不传 mode 就用 session 当前 mode
```

### Tool: memory_set_mode

```
作用: 中途切换模式
参数:
  mode: str (必填)          → "human" | "code"
说明: 切回 hybrid 不需要调用这个，直接传 mode 参数就行
      设定后 sticky，直到下一次 memory_set_mode
```

### Tool: memory_write

```
作用: 写入记忆
参数:
  data: dict (必填)         → 写入内容
  mode: str (可选)          → "human" | "code" | "auto"
                            "auto" 让系统自动判断存储位置
说明: 不传 mode 用 session 当前 mode
```

### Tool: memory_project_sync

```
作用: 扫描项目文件，更新模块索引
参数:
  path: str (可选)          → 默认当前项目根路径
```

### Tool: memory_status

```
作用: 查看当前状态
返回:
  mode: str                 → 当前模式
  project: str              → 当前项目
  stats: dict               → 各表记录数
```

### 最简使用路径

```python
# 1. 会话开始
memory_init()

# 2. 检索（默认 hybrid，两边都搜）
memory_query(query="项目概况")
memory_query(query="文件结构")
memory_query(query="上次的决策")

# 3. 写入（会话结束时）
memory_write(data={
    "summary": "讨论了认证方案",
    "key_moments": [...],
    "tags": ["认证", "决策"]
})

# 4. 同步项目文件（按需）
memory_project_sync()
```

### 优化路径

```python
# AI 发现查询结果太多（代码搜索混入了聊天记录）
memory_set_mode("code")

# 之后所有查询只搜项目侧
memory_query(query="middleware.ts")
memory_query(query="login.tsx")

# 需要回忆时，临时覆盖
memory_query(query="为什么用JWT", mode="hybrid")
# 下一次 query 还是 code mode（mode 没变）
memory_query(query="继续看代码")
```

---

## 5. Human Memory Domain — 人的记忆域

### 5.1 工作区（Working Buffer）

```
位置: 内存
持久化: 不持久化，会话结束即丢弃
范围: 当前对话的最后 N 轮（默认 20 轮）

结构:
  entries: [
    { role, content, timestamp, turn_index }
  ]

操作:
  - append(entry)       → 追加到末尾
  - shift()             → 超出 N 轮时丢弃最旧的
  - get_context(n)      → 取最近 n 轮
  - search(keywords)    → 在当前窗口中搜关键词

生命周期:
  每轮对话 → 自动追加 → 会话结束 → 提取关键信息后丢弃
```

### 5.2 时间线（Timeline）

```
位置: timeline 表
类型: 追加式（append-only），不可变
粒度: 每次会话结束时写一条

字段:
  id:               "s_20260426_001"
  session_start/end: ISO datetime
  summary:          AI 生成的会话摘要
  key_moments:      [{type, content, turn_index}]
  tags:             ["认证", "架构决策"]
  final_state:      会话结束时状态
  linked_decisions: ["dec_001"]
  linked_entities:  ["JWT", "Go"]
  importance:       1-5

索引:
  - 时间倒排（idx_timeline_start DESC）
  - 标签匹配（tags 字段, 应用层解析 JSON）
  - FTS5 全文搜索（summary + tags）

检索 API（store/timeline.py）:
  query_by_time(start, end)
  query_by_tags(["认证", "决策"])
  query_by_keyword("JWT")
  semantic_search("认证方案怎么定的")  // FTS5 兜底
```

### 5.3 语义网（Semantic Net）

```
位置: semantic_entities + semantic_facts 表
类型: 实体为中心的关系型存储
特性: 可更新（带版本链）

实体表字段:
  id:       "entity_python"
  name:     "Python编程语言"        ← 规范名
  aliases:  ["Python", "py"]       ← 别名列表
  type:     "language"
  importance: 1-5
  summary:  "用户曾深度使用，后迁移到Go"

事实表字段:
  id:               "fact_001"
  entity_id:        "entity_python"    → FK
  predicate:        "喜欢"
  object_value:     "Go"
  confidence:       "high" | "mid" | "low"
  evidence_session: "s_20260426_001"   → FK timeline
  valid_from:       "2026-04-26"
  valid_to:         null                ← 至今有效
  previous_version: "fact_000"          ← 版本链

版本链示例:
  fact_000: entity="Python"  pred="喜欢"  valid_to="2026-04-19"
  fact_001: entity="Go"      pred="喜欢"  valid_from="2026-04-19"

查询"用户现在喜欢什么":
  SELECT * FROM v_current_facts
  WHERE entity_id='entity_user' AND predicate='喜欢'

检索 API（store/semantic.py）:
  query_entity("用户")            → 该实体所有事实
  query_relation("用户", "喜欢")   → 用户喜欢什么
  query_entity_fuzzy("py")        → 模糊匹配实体名
  query_current_facts()           → 当前有效的事实
```

### 5.4 模式库（Pattern Store）

```
位置: patterns 表
粒度: 一个模式一条记录
特性: 渐进演化，随证据增多而置信度提升

字段:
  id:                "pat_001"
  description:       "用户技术栈迁移周期约2年"
  detail:            "从Java→Python（2024）再到Go（2026）"
  evidence_sessions: ["s_202404", "s_202604"]
  confidence:        0.7
  trigger_topics:    ["技术选型", "迁移", "性能"]
  category:          "行为模式"

生成时机:
  - 定时合成（每收集 N 条相关 Timeline 后）
  - 手动触发

检索: 按 trigger_topics 匹配当前话题 → 主动推送
```

### 5.5 反射层（Reflection Layer）

```
位置: reflections 表
粒度: 一个洞察一条记录
特性: 最高度压缩，最低频更新

字段:
  id:                  "ref_001"
  insight:             "用户做技术决策：性能 > 生态 > 学习成本"
  supporting_sessions: ["s_202404", "s_202501", "s_202604"]
  supporting_patterns: ["pat_001", "pat_002"]
  confidence:          "high"
  category:            "决策风格"

生成时机:
  - 累积 N 个新 Pattern 后自动触发
  - 手动按需生成

检索: 情景触发，不进入常规搜索管线
```

---

## 6. Project Memory Domain — 项目记忆域

### 6.1 项目卡片（Project Card）

```
位置: projects 表
粒度: 一个项目一条记录
特性: 可更新

字段:
  id:            "proj_deepchat"
  name:          "DeepChat"
  root_path:     "C:\\deepchat"
  tech_stack:    [{name, version, purpose}]
  main_modules:  [{name, path, desc}]
  active_branch: "feature/auth-refactor"
  last_scanned:  ISO datetime

检索:
  - 项目名精确匹配
  - 自动加载（AI 启动时读当前项目）
```

### 6.2 模块索引（Module Index / File Map）

```
位置: file_map 表（+ FTS5）
粒度: 一个文件一条记录
特性: 可增量更新

字段:
  id:               "file_auth_middleware"
  path:             "src/auth/middleware.ts"
  module:           "auth"
  summary:          "JWT token 验证中间件"
  key_symbols:      [{name: "verifyToken", type: "function"}]
  depends_on:       ["src/utils/jwt.ts"]
  linked_decisions: ["dec_001"]
  last_modified:    ISO datetime
  change_count:     3

索引:
  - idx_filemap_path → LIKE 'src/auth/%' 前缀匹配
  - idx_filemap_module → 模块名精确匹配
  - FTS5 全文搜索 → path + summary + key_symbols

检索 API（store/file_map.py）:
  query_by_path("src/auth/")        # 路径前缀
  query_by_module("auth")           # 模块名
  query_by_symbol("verifyToken")    # 符号名
  query_by_decision("dec_001")      # 关联决策反向查
  semantic_search("JWT认证逻辑")     # FTS5 兜底
```

### 6.3 决策日志（Decision Log）

```
位置: decisions 表
粒度: 一个决策一条记录
特性: 追加式，一般不修改

字段:
  id:              "dec_001"
  title:           "认证方案选择JWT而非Session"
  body:            "桌面应用场景下..."
  alternatives:    [{方案, 否决原因}]
  affected_files:  ["src/auth/middleware.ts"]
  linked_session:  "s_20260426_001"    → FK timeline
  tags:            ["认证", "安全", "架构决策"]
  status:          "implemented"

检索:
  - 决策标签匹配
  - 文件名反向索引（affected_files）
  - FTS5 全文搜索（title + body）
```

### 6.4 任务上下文（Task Context）

```
位置: tasks 表
粒度: 一个任务一条记录
特性: 可更新，反映当前开发进度

字段:
  id:               "task_auth_refactor"
  title:            "重构认证模块"
  status:           "wip"    // planned | wip | blocked | done
  progress_pct:     60
  progress_detail:  "JWT中间件已完成，登录页面实现中"
  related_sessions: ["s_20260425", "s_20260426"]
  related_files:    ["file_auth_middleware", "file_auth_login"]
  blockers:         ["等待后端API文档更新"]
  next_action:      "完成login.tsx的UI逻辑"

检索:
  - 当前活跃任务自动加载（status != done）
  - 通过文件反向索引
```

---

## 7. Decision Hub — 枢纽层

### 职责

不存储记忆数据本身，只存储"人侧 ↔ 项目侧"的链接关系。

这是 hybrid 模式下实现双向跳转的核心。

### 表结构

```sql
CREATE TABLE decision_hub (
    id                TEXT PRIMARY KEY,    -- "link_001"
    human_source_type TEXT NOT NULL,       -- "timeline" | "semantic"
    human_source_id   TEXT NOT NULL,
    code_source_type  TEXT NOT NULL,       -- "decision" | "file_map"
    code_source_id    TEXT NOT NULL,
    link_type         TEXT NOT NULL,       -- "决策来源" | "代码体现" | "问题引发"
    rationale         TEXT,
    created_at        TEXT,
    UNIQUE(human, code)                   -- 防止重复链接
);
```

### 核心操作

```
从人侧出发:
  memory_query(query="认证方案怎么定的")
  → 查 Timeline → s_20260426_001
  → Decision Hub → human_side match → link_001
  → code_side = dec_001
  → 跳转到决策日志 → 知道影响了哪些文件
  → 返回: 决策理由 + 影响文件列表

从项目侧出发:
  memory_query(query="middleware.ts 为什么用JWT")
  → 查 File Map → middleware.ts → linked_decisions = [dec_001]
  → Decision Hub → code_side = dec_001
  → human_side = s_20260426_001
  → 跳转到 Timeline → 看到完整讨论过程
  → 返回: 代码上下文 + 决策理由 + 讨论过程
```

### hybrid 模式的检索合并逻辑

```python
def hybrid_query(query):
    # 1. 同时搜两边
    human_results = search_human(query)
    code_results = search_project(query)

    # 2. 检查 Decision Hub 是否有链接关联
    linked = []
    for r in human_results:
        links = hub.query_by_human(r.type, r.id)
        linked.extend(links)
    for r in code_results:
        links = hub.query_by_code(r.type, r.id)
        linked.extend(links)

    # 3. 合并结果
    # 如果有 linked 结果，优先展示链接关系
    # 如果没有 linked，各自独立返回
    return merge(human_results, code_results, linked)
```

---

## 8. 检索协议

### 8.1 hybrid 模式（默认）

```
memory_query(query, mode="hybrid")
  │
  ├─ 1. 工作区 → 滚当前窗口
  │    └─ 匹配 → 直接返回 ✅
  │
  ├─ 2. 人的记忆域
  │    ├─ 语义网 → 实体精确 + 模糊匹配
  │    ├─ 时间线 → 关键词 + FTS5
  │    ├─ 模式库 → 按话题主动推送
  │    └─ 反射层 → 深度查询调入
  │
  ├─ 3. 项目记忆域
  │    ├─ 模块索引 → 路径前缀 + 模块 + 符号匹配
  │    ├─ 决策日志 → 文件名反向索引 + 标签
  │    ├─ 任务上下文 → 活跃任务
  │    └─ 项目卡片 → 项目概览
  │
  ├─ 4. Decision Hub 关联
  │    ├─ 检查两边结果是否有跨域链接
  │    └─ 如果有，合并展示
  │
  └─ 5. 返回合并结果
```

### 8.2 human 模式

```
memory_query(query, mode="human")
  │
  ├─ 工作区 → 滚当前窗口
  └─ 人的记忆域（同上，不走项目侧）
```

### 8.3 code 模式

```
memory_query(query, mode="code")
  │
  ├─ 项目卡片 → 加载当前项目概览
  ├─ 模块索引 → 路径/模块/符号匹配
  ├─ 决策日志 → 按文件名/标签索引
  ├─ 任务上下文 → 当前活跃任务
  └─ (兜底) FTS5 全文搜索
```

---

## 9. 写入与合成协议

### 9.1 会话结束时写入

```python
def on_session_end(current_context):
    # 1. 生成 Timeline 记录
    timeline_entry = create_timeline(
        summary=summarize(current_context),
        key_moments=extract_key_moments(current_context)
    )

    # 2. 提取实体事实 → 更新 Semantic Net
    for fact in extract_facts(current_context):
        semantic.upsert_fact(
            entity=fact.entity,
            predicate=fact.predicate,
            value=fact.value,
            evidence=timeline_entry.id
        )

    # 3. 提取决策 → 写入 Decision Log + Decision Hub
    for decision in extract_decisions(current_context):
        log_entry = decision_log.add(decision)
        hub.link(
            human_side=("timeline", timeline_entry.id),
            code_side=("decision_log", log_entry.id),
            link_type="决策来源"
        )

    # 4. 更新模块索引
    file_map.scan_updates(project_root)

    # 5. 更新任务上下文
    task_context.update_progress(current_context)
```

### 9.2 定时合成任务

```python
# 累积 10 条新 Timeline → 模式发现
on_schedule(count=10):
    patterns = discover_patterns(timeline.get_unprocessed(10))
    for p in patterns:
        pattern_store.add_or_merge(p)

# 累积 5 个新 Pattern → 反射合成
on_schedule(count=5):
    insights = synthesize_reflections(pattern_store.get_recent(5))
    for i in insights:
        reflection_layer.add(i)

# 每天一次 → 维护
on_schedule(interval=24h):
    project_card.refresh()
    semantic.decay_low_confidence(threshold=0.3, older_than=90d)
    timeline.archive(older_than=365d)
```

---

## 10. SQLite 存储设计

### 10.1 完整表清单

| 表 | 域 | 记录内容 | 行数预估 |
|----|-----|---------|---------|
| `timeline` | Human | 会话日志 | 按会话数增长 |
| `timeline_fts` | Human | 全文索引 | 同上 |
| `semantic_entities` | Human | 实体注册表 | ~50-200 |
| `semantic_facts` | Human | 事实（带版本链） | ~100-500 |
| `patterns` | Human | 行为模式 | ~10-50 |
| `reflections` | Human | 深层洞察 | ~5-20 |
| `projects` | Project | 项目卡片 | ~1-5 |
| `file_map` | Project | 文件索引 | 按项目文件数 |
| `file_map_fts` | Project | 全文索引 | 同上 |
| `decisions` | Project | 决策日志 | ~20-100 |
| `tasks` | Project | 任务上下文 | ~5-30 |
| `decision_hub` | 枢纽 | 跨域链接 | ~20-100 |
| `config` | 系统 | 配置项 | ~5 |

### 10.2 索引策略

```
人的记忆域:
  timeline:      时间倒排 + importance + project_id + FTS5
  semantic_facts: entity + predicate + 联合索引 + 有效时间

项目记忆域:
  file_map:      project_id + module + path + FTS5
  decisions:     project_id + session + status
  tasks:         project_id + status

枢纽:
  decision_hub:  human_side 复合索引 + code_side 复合索引
```

### 10.3 FTS5 全文搜索

两个虚拟表提供全文搜索：

```sql
-- Timeline 搜索
SELECT * FROM timeline_fts WHERE timeline_fts MATCH 'JWT 认证'

-- File Map 搜索
SELECT * FROM file_map_fts WHERE file_map_fts MATCH 'middleware JWT'
```

FTS5 使用 `unicode61` tokenizer，支持中文 + 英文混合分词。

### 10.4 关键 SQL 查询示例

```sql
-- 最近 10 条会话
SELECT * FROM timeline ORDER BY session_start DESC LIMIT 10;

-- 某个实体的当前有效事实
SELECT * FROM v_current_facts WHERE entity_id = 'entity_user';

-- auth 模块的所有文件
SELECT * FROM file_map WHERE module = 'auth' AND project_id = 'proj_deepchat';

-- 文件路径前缀查询
SELECT * FROM file_map WHERE path LIKE 'src/auth/%';

-- 某个文件关联的决策
SELECT * FROM decisions WHERE id IN (
    SELECT value FROM json_each(
        (SELECT linked_decisions FROM file_map WHERE id = 'file_auth_middleware')
    )
);

-- 决策对应的会话记录（通过 Decision Hub）
SELECT t.* FROM timeline t
JOIN decision_hub dh ON dh.human_source_id = t.id
WHERE dh.code_source_id = 'dec_001'
  AND dh.code_source_type = 'decision';

-- 当前活跃任务
SELECT * FROM v_active_tasks;
```

---

## 11. 完整示例流

### 示例 1：hybrid 默认模式（全程不切）

```
用户: "帮我看看这个项目"

AI:
  memory_init(project_path="C:/deepchat")
  memory_query(query="项目概况")
  → hybrid: 项目卡片 + 文件索引概览

AI: "这是 DeepChat 项目，技术栈 Electron + React + TypeScript..."

───────────────────────────────────────────────

用户: "auth 模块的文件在哪"

AI:
  memory_query(query="auth 模块")
  → hybrid: file_map.module='auth' → 返回文件列表

AI: "auth 模块有 middleware.ts 和 login.tsx..."

───────────────────────────────────────────────

用户: "为什么选了 JWT？"

AI:
  memory_query(query="认证方案 JWT 决策")
  → hybrid: decisions → Decision Hub → timeline
  → 返回: 决策理由 + 讨论过程 + 受影响文件

AI: "4月26日讨论的，因为桌面应用适合无状态方案..."
```

### 示例 2：优化路径（AI 切 code 模式）

```
用户: "帮我改一下 login.tsx"

AI:
  memory_init(project_path="C:/deepchat")
  memory_query(query="login.tsx 当前内容")
  → hybrid 返回了: 文件信息 + 无历史聊天

AI 发现 human side 无结果:
  memory_set_mode("code")    ← 切模式，后续更快

memory_query(query="login.tsx 结构和依赖")
  → code: 只搜项目侧，login.tsx 详情 + 依赖组件

AI: "好，login.tsx 有用户名和密码输入框..."
```

### 示例 3：中途切 hybrid

```
用户: "说起来，为什么 login.tsx 要用受控组件？"

AI:
  memory_query(query="login.tsx 受控组件")
  → code: 项目记忆中无相关决策记录

AI:
  memory_query(query="受控组件 表单 讨论", mode="hybrid")
  → hybrid: 时间线 → "讨论了React受控组件和表单处理"
           语义网 → 用户偏好受控组件

AI: "上次讨论过，你倾向于受控组件因为..."
AI: memory_set_mode("code")    ← 切回 code 继续开发
```

---

## 12. 落地路径

### 阶段一：核心骨架（1-2周）~27K token

```
目标: 最小的可运行版本

文件:
  schema.sql              → 完整建表
  vellum/db.py            → 数据库连接 + 初始化
  vellum/session.py       → 会话状态管理
  vellum/server.py        → MCP Server + 6 个 tool
  vellum/router.py        → mode 路由分发

  stores/timeline.py      → Timeline CRUD + FTS5
  stores/semantic.py      → 实体 + 事实 CRUD
  stores/projects.py      → 项目卡片 CRUD
  stores/file_map.py      → 文件索引 CRUD + 路径扫描
  stores/decisions.py     → 决策日志 CRUD
  stores/tasks.py         → 任务上下文 CRUD

不实现:
  - 模式库、反射层
  - Decision Hub
  - 向量兜底
```

### 阶段二：模式与洞察（第3-4周）~9K token

```
实现:
  stores/patterns.py      → 模式发现 + 存储
  stores/reflections.py   → 反射合成 + 存储
  scheduler.py            → 定时合成任务
  时间衰减逻辑
```

### 阶段三：双向耦合（第5-6周）~6K token

```
实现:
  vellum/hub.py           → Decision Hub CRUD
  hybrid 模式的枢纽关联逻辑
  决策自动提取
  跨会话任务追踪
```

### 阶段四：向量兜底（第7-8周）~4.5K token

```
实现:
  vector/adapter.py       → 可插拔向量接口
  fallback pipeline       → 结构化检索不到时降级
  LRU 缓存
  性能优化
```

---

> **Vellum 架构设计 v2**
>
> 名称由来：Vellum（羊皮纸）— 古老的记录载体，记忆刻在上面
>
> 核心设计决策：
> - 默认 hybrid → 不传参即可使用
> - 三种模式：hybrid（全量）、human（人侧）、code（项目侧）
> - AI 显式管理模式切换，不依赖规则引擎
> - sticky 机制：模式保持到下一次显式切换
> - 一个 SQLite 文件存所有记忆
