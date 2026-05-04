# VellumMem — 设计文档 v8 (v1.3)

> 状态：已实装 ✅
> 内核：Human-only 纯记忆系统
> 检索：预合并向量（1 向量/条）
> 分组：Clique Percolation Method (CPM, 支持任意 k，默认 4)
> 后台：守护线程（TTL 清理 + 去重扫描）

---

## 一、设计目标

### 核心问题

AI 需要跨会话的持久记忆，但：
- 每次对话从零开始
- 传统双层检索（关键词 + 向量）在自然语言查询下表现差
- 外部向量数据库增加运维成本

### 设计原则

| 原则 | 说明 |
|------|------|
| **单层语义检索** | 不搞关键词 + 向量两层，统一走余弦相似度 |
| **真实分数** | 返回 0~1 余弦相似度，AI 自行判断相关性 |
| **可配置阈值** | 低于 `score_threshold`（默认 0.15）不返回 |
| **贪婪模式** | `top_k` 可调大获取更多结果 |
| **零外部依赖** | 单 SQLite 文件 + 本地模型，无外部服务 |
| **纯 Human，无 Code 模式** | 删除了 Project/File/Decision/Task 等 code 专用存储 |

---

## 二、检索设计：预合并向量

### 数学原理

每条记忆包含 1 段摘要 + 5 个标签。最精确的评分方式是分别计算每个维度与查询的相似度后取平均：

```
score = (q·s + q·t₀ + q·t₁ + q·t₂ + q·t₃ + q·t₄) / 6
```

向量内积是线性运算，可以合并为一次运算：

```
score = q · (s + t₀ + t₁ + t₂ + t₃ + t₄) / 6
```

**关键约束**：各分量必须事先归一化，合并后**不二次归一化**，否则分数改变。

### 存储对比

| 方案 | 向量/条 | SQLite 行数(1000条) | 查询内积次数 | 检索质量 |
|------|---------|--------------------|-------------|---------|
| 分别存储 6 向量 | 6 | 6000 | 6000 | ✅ 最好 |
| 预合并 ✅ | **1** | **1000** | **1000** | ✅ **数学等价** |

### 写入流程

```python
sv = model.encode(summary, normalize_embeddings=True)   # (512,)
tv = model.encode(tags, normalize_embeddings=True)       # (5, 512)
merged = (sv + tv.sum(axis=0)) / 6.0                     # (512,)
# pickle.dumps → INSERT INTO entry_vectors
```

### 查询流程

```python
qv = model.encode(query, normalize_embeddings=True)      # ~20ms
for entry_vectors:
    score = float(qv @ merged_blob)                      # 1 次内积/条
    if score >= threshold: results.append(...)
return sorted(results, key=-score)[:top_k]
```

### 性能（1000 条）

| 环节 | 耗时 |
|------|------|
| query encode | ~20ms |
| SQLite 读 BLOB + pickle | ~15ms |
| 内积 × 1000 | ~2ms |
| 排序 | <1ms |
| **总计** | **~38ms** |

### 数学等价性验证

10000 次随机向量测试：
- max diff: **1.06e-08**（float32 精度级）
- mean diff: **2.10e-09**
- diff > 1e-7: **0 次**

检索所需精度为 0.01（百分位），差异小 100 万倍，实用层面完全等价。

---

## 三、分组设计：CPM（支持任意 k，默认 4）

### 为什么需要分组

语义检索返回的是分散的记忆条目，分组让 AI 能发现记忆之间的关联结构。
例如：多条关于"架构重构"的记忆自动组成一组，AI 可以一次性拉取整个组的上下文。

### 算法：Clique Percolation Method（通用化）

```
输入：所有条目的预合并向量
  1. 计算两两余弦相似度，相似度 ≥ threshold 的连边
  2. 从 2-clique（边）开始，逐级扩展至 k-clique
     - 候选节点 = 与当前 clique 所有成员的邻接集交集
     - 以去重 frozenset 存储已发现的 clique
  3. 两个 k-clique 共享 k-1 个节点时认为属于同一社区
  4. 在 clique graph 上做连通分量 → 社区
输出：每个社区 = 一个记忆分组
```

### 关键参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `k` | 4 (config: `group_k`) | 团大小，支持任意 k≥2 |
| `threshold` | 0.45 (config: `group_threshold`) | 余弦相似度阈值 |
| 重叠 | 允许 | 一条记忆可属于多个分组 |

**参数持久化**：写入 config 表，重启自动读取。

### 启动构建

`_ensure_init()` 末尾从 config 表读取 `group_k` 和 `group_threshold`，自动调用 `build_groups(k=k, threshold=threshold)`。
新条目写入后需要手动调用 `memory_rebuild_groups` 重新构建。

---

## 四、数据库 Schema v8

### human_timeline（核心表）

```sql
CREATE TABLE human_timeline (
    id                        TEXT PRIMARY KEY,
    summary                   TEXT DEFAULT '',              -- 上限 200 字
    tags                      TEXT DEFAULT '[]',            -- JSON 数组，固定 5 个
    conversation_context_link TEXT DEFAULT '[]',            -- 有序上下文 ID 数组
    category                  TEXT DEFAULT 'conversation',  -- conversation/knowledge/document/preference/other
    is_time_sensitive         INTEGER DEFAULT 0,
    create_timestamp          INTEGER NOT NULL
);
```


### conversation_context

```sql
CREATE TABLE conversation_context (
    id               TEXT PRIMARY KEY,
    timeline_id      TEXT NOT NULL REFERENCES human_timeline(id) ON DELETE CASCADE,
    context          TEXT NOT NULL,
    chunk_index      INTEGER NOT NULL DEFAULT 0,
    create_timestamp INTEGER NOT NULL
);
```

### entry_vectors

```sql
CREATE TABLE entry_vectors (
    entry_id     TEXT PRIMARY KEY REFERENCES human_timeline(id) ON DELETE CASCADE,
    merged_blob  BLOB NOT NULL,       -- pickle.dumps(np.ndarray(float32, 512))  合并向量
    summary_blob BLOB                 -- pickle.dumps(np.ndarray(float32, 512))  摘要向量（去重用）
);
```

### memory_groups

```sql
CREATE TABLE memory_groups (
    id               TEXT PRIMARY KEY,
    entry_ids        TEXT NOT NULL DEFAULT '[]',  -- JSON 数组
    member_count     INTEGER DEFAULT 0,
    create_timestamp INTEGER NOT NULL
);
```

### config
> 默认键值（启动自动写入，`INSERT OR IGNORE`）：

| key | value | type | 说明 |
|-----|-------|------|------|
| `vector_engine` | `transformer` | str | 向量引擎 |
| `score_threshold` | `0.15` | float | 检索最低匹配分数 |
| `group_threshold` | `0.45` | float | CPM 相似度阈值 |
| `group_k` | `4` | int | CPM k 值 |
| `dedup_enable` | `false` | bool | 后台去重扫描开关 |
| `dedup_threshold` | `0.9` | float | 去重扫描余弦阈值 |
| `daemon_interval` | `1800` | int | 守护线程扫描间隔（秒） |

值读取优先级：环境变量 > config 表 > 代码默认值。

```sql
CREATE TABLE config (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL DEFAULT '',
    type        TEXT NOT NULL DEFAULT 'str',
    description TEXT DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now','localtime')),
    updated_at  TEXT DEFAULT (datetime('now','localtime'))
);
```

---

## 五、MCP 接口

### 记忆写入

```
memory_write(data: str) -> str
  data 字段：
    - summary: str          必填，上限 200 字
    - tags: [str]           必填，必须 5 个
    - context_text: str     选填，上下文原文
    - category: str         必填，conversation/knowledge/document/preference/other
    - is_time_sensitive: bool  选填
```

### 查询

```
memory_query(query: str, top_k: int = 3, score_threshold: float = 0.15) -> str
```

### 上下文管理

```
memory_get_context(timeline_id: str, offset: int = 0, limit: int = 1) -> str
memory_write_context(timeline_id: str, context_text: str) -> str
```

### 分组

```
memory_get_groups(entry_id: str) -> str
memory_list_groups() -> str                        # 列出所有分组
memory_get_group_members(group_id: str) -> str
memory_rebuild_groups(threshold: float = 0.45) -> str  # 从 config 读 k，threshold 可选覆盖
```

### 状态

```
memory_init() -> str
memory_status() -> str
```

---

## 六、架构

### 初始化流程

```
main()
  1. 预热 sentence-transformers（同步 import，确保首次工具调用不卡）
  2. 调用 _ensure_init()（强制初始化，非惰性）
     2a. 解析数据库路径（VELLUM_DB_PATH 环境变量或默认）
     2b. 连接 SQLite + executescript schema.sql
     2c. 迁移 config 表（2列 → 6列）+ 写入默认值
     2d. 迁移 human_timeline 表（补 category/is_time_sensitive；v7 重建去死字段）
     2e. 迁移 entry_vectors 表（补 summary_blob 列）
     2f. 初始化 VectorAdapter（加载 sentence-transformers 模型 + 已有向量）
     2g. 从 config 读取 `group_k` 和 `group_threshold`，构建 CPM 分组
         （`build_groups(k=group_k, threshold=group_threshold)`）
  3. 启动守护线程（_start_daemon）
  4. mcp.run()（阻塞，MCP 开始监听）
```

### 线程安全

使用双检锁（double-checked locking）确保惰性初始化安全：

```python
_init_lock = threading.Lock()

def _ensure_init():
    if _vector is not None:       # 快速路径（无锁）
        return
    with _init_lock:              # 慢速路径（加锁）
        if _vector is not None:   # 重复检查
            return
        # ... 实际初始化 ...
```

### 异常层次

```
VellumMemError (Exception)
 ├── StoreError        — 存储层异常（无效 category、不足 5 tag）
 ├── VectorError       — 向量引擎异常
 └── InitError         — 初始化异常（模型加载失败、下载超时）
```

`@_tool` 装饰器统一捕获并返回 JSON 错误消息。

---

## 七、守护线程与去重扫描

### 守护线程（Daemon Thread）

`main()` 在 `_ensure_init()` 完成后、`mcp.run()` 之前启动一个 daemon 线程，周期执行后台任务。

```
main()
├── _ensure_init()
├── _start_daemon()         ← 启动守护线程
│   └── daemon 循环:
│       ├── sleep(interval)           ← 默认 1800 秒
│       ├── TTL 清理                   ← 删除过期条目 + 移除向量缓存
│       └── 去重扫描（开光后）          ← 摘要向量 O(N²) 比对
└── mcp.run()
```

### 调度机制

- 线程循环一次 `sleep(interval)` → 依次执行任务 → 再次 sleep
- `interval` 读取自 config 表 `daemon_interval`（默认 1800s=30min）
- 环境变量 `VELLUM_DAEMON_INTERVAL` 可覆盖 config 值
- 所有异常被捕获并写入 stderr，不影响后续循环

### TTL 清理

- 之前：`_ensure_init()` 末尾一次性执行
- 现在：守护线程定时执行 `cleanup_expired()`
- 提前获取过期 ID → 清理 entries → 移除向量缓存
- 每轮清理后会写 stderr 日志

### 去重扫描

**目标**：发现语义重复的记忆条目（如同一件事被反复记录）。

**方法**：
- `VectorAdapter.scan_duplicates(threshold, skip_ids)` — 全库 O(N²) 摘要向量余弦比对
- 只比较 `_summary_vectors` 中存在的条目（摘要非空）
- 跳过已标记 `is_time_sensitive=1` 的条目（即将过期，不必再比）

**阈值**：`dedup_threshold`（默认 0.9），环境变量 `VELLUM_DEDUP_THRESHOLD` 可覆盖。

**发现重复后的处理**：
1. 比较两条条目的 `create_timestamp`
2. 保留时间更早的（keeper），标记时间更晚的（duplicate）为 `is_time_sensitive=true`
3. TTL 使用默认 3 天（`VELLUM_DEFAULT_TTL_DAYS`），到期自动清理

**开关**：`dedup_enable`（默认 false），环境变量 `VELLUM_DEDUP_ENABLE=true` 开启。

### 摘要向量（summary_blob）

`entry_vectors` 表新增 `summary_blob` 列，独立存储摘要的归一化向量：

```python
summary_vec = model.encode(summary, normalize_embeddings=True)  # 单位向量
```

- `store()` 时自动计算并存一份
- 启动时 `initialize()` 自动回填历史数据的缺失摘要向量
- 不使用预合并向量而是独立存一份的原因：去重需要纯摘要语义比对，标签的噪声会降低精度

### 配置读取优先级

```
环境变量 VELLUM_<KEY> > config 表 > 代码默认值
```

由 `_read_config(key, default)` 统一处理。

---

## 八、项目结构

```
vellum/
├── __init__.py               # 版本号
├── server.py                 # MCP 入口 + @mcp.tool() × 10 + @_tool 装饰器 + 守护线程
├── db.py                     # SQLite 连接 + Schema 初始化 + 迁移（含 entry_vectors 迁移）
├── errors.py                 # 异常层次（VellumMemError 基类）
├── groups.py                 # CPM 分组管理器（支持任意 k）
├── stores/
│   ├── __init__.py
│   └── human_timeline.py     # 人类记忆 CRUD + 上下文分片 + 去重辅助（get_time_sensitive_ids / mark_as_time_sensitive）
└── vector/
    ├── __init__.py
    └── adapter.py            # sentence-transformers 适配器 + 预合并向量 + 摘要向量（去重）
schemas/
└── schema.sql                # 统一建表 SQL（v7）
tests/
├── __init__.py
├── test_errors.py            # 异常层级测试（6 tests）
└── test_stores.py            # store CRUD + 上下文分片 + DB 初始化测试（13 tests）
```

---
