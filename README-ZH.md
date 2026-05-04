# VellumMem（羊皮纸记忆）— 持久 AI 记忆系统

> **羊皮纸** — 古老的记录载体，记忆刻在上面。
> 基于 MCP 的 AI 持久记忆系统，让 AI 助手拥有跨会话的长期记忆。

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## 这是什么

VellumMem 是一个 MCP 服务器，为 AI 助手提供跨会话的**持久化、可检索的记忆**。解决 LLM 每次从零开始的根本局限。

| 能力 | 实现方式 |
|------|---------|
| **人类记忆 🧠** | 存储对话摘要 + 标签 + 上下文原文；语义向量检索 |
| **记忆分组** | CPM（支持任意 k，默认 4）派系过滤法自动归组 |
| **后台守护线程** | 定时 TTL 清理 + 可选自动去重扫描 |
| **预合并向量** | 每条记忆 1 个向量，数学等价于多向量评分 |

**核心亮点：**
- **零外部服务** — 单文件 SQLite + 本地模型，无需向量数据库或云 API
- **预合并向量** — 存储量 1/6、速度 4 倍，数学等价
- **MCP 原生** — 适配任何 MCP 主机（DeepChat、Claude Desktop、自定义应用）

---

## 快速开始

### 环境要求
- Python 3.12+

### 安装

```bash
cd vellum
pip install -r requirements.txt
pip install sentence-transformers   # 强烈推荐
```

### 运行

```bash
python run.py
```

### 测试

```bash
pytest tests/ -v
```

---

## MCP 工具参考

### 写入记忆

```
memory_write(data: str) -> str
```
- `summary`（必填，上限 200 字）
- `tags`（必填，必须 5 个）
- `context_text`（选填）
- `category`（必填：`conversation` / `knowledge` / `document` / `preference` / `other`）
- `is_time_sensitive`（选填）

### 查询记忆

```
memory_query(query, top_k=3, score_threshold=0.15) -> str
```
返回按真实余弦相似度（0~1）排序的结果，每项包含 `create_timestamp`、`category`、`is_time_sensitive`、`group_ids`。

### 上下文管理

```
memory_get_context(timeline_id, offset=0, limit=1) -> str
memory_write_context(timeline_id, context_text) -> str
```
按自然分隔符自动分片（标题、代码块、列表、段落），上限 8000 字符/片。

### 记忆分组

```
memory_get_groups(entry_id) -> str
memory_list_groups() -> str
memory_get_group_members(group_id) -> str
memory_rebuild_groups(threshold=0.45) -> str
```
`memory_rebuild_groups` 从 config 读取 `k`（`group_k`），threshold 可选覆盖。

### 状态

```
memory_init() -> str
memory_status() -> str
```

所有工具统一返回 JSON 错误消息，由 `@_tool` 装饰器保障。

---

## 架构

```
┌──────────────────────────────────────────────┐
│             AI 助手（宿主）                     │
│  memory_write / memory_query / ...            │
└─────────────────────┬────────────────────────┘
                      │ MCP (stdio)
┌─────────────────────▼────────────────────────┐
│            VellumMem MCP 服务器                │
│                                                │
│  ┌──────────────┐  ┌──────────────────────┐   │
│  │ 9 个 MCP 工具 │  │  线程安全惰性初始化    │   │
│  │ @_tool 装饰器 │  │  （双检锁）           │   │
│  └──────┬───────┘  └──────────────────────┘   │
│         │                                      │
│  ┌──────▼─────────────────────────────────┐   │
│  │  存储层 + 分组 + 向量引擎                │   │
│  │                                         │   │
│  │  human_timeline.py  — CRUD + 分片       │   │
│  │  groups.py          — CPM 分组（支持任意 k）│   │
│  │  vector/adapter.py  — 预合并向量搜索     │   │
│  │  db.py              — SQLite            │   │
│  │  errors.py          — 异常类型          │   │
│  └─────────────────────────────────────────┘   │
└──────────────────────┬─────────────────────────┘
                       │
              ┌────────▼────────┐
              │  SQLite（1 文件） │
              │  vellum.db       │
              └─────────────────┘
```

---

## 检索设计

### 预合并向量

每条记忆存储 **1 个合并向量**（而非 6 个）：

```
score = (q·摘要 + q·标签₀ + ... + q·标签₄) / 6
      = q · (摘要 + 标签₀ + ... + 标签₄) / 6
```

经 10,000 次随机测试验证，最大浮点误差 1.06e-08（比检索精度小 100 万倍）。

### 记忆分组（CPM 支持任意 k，默认 4）

1. 两两余弦相似度 ≥ threshold → 连边
2. 从 2-clique 逐级扩展至 k-clique（边 → 三角形 → ... → k-clique）
3. 共享 k-1 个节点 → 同一社区
4. 连通分量 → 分组

**`k` 和 `threshold` 从 config 表读取（`group_k`、`group_threshold`）。**
启动时自动构建；可通过 `memory_rebuild_groups()` 覆盖。

---

## 后台守护线程

VellumMem 启动时自动拉起一个守护线程（`_start_daemon()`），定时执行后台任务：

| 任务 | 默认间隔 | Config 键 | 环境变量 |
|------|---------|-----------|---------|
| **TTL 清理** | 30 分钟 | `daemon_interval` | `VELLUM_DAEMON_INTERVAL` |
| **去重扫描** | 30 分钟（同一间隔） | `dedup_enable` + `dedup_threshold` | `VELLUM_DEDUP_ENABLE` + `VELLUM_DEDUP_THRESHOLD` |

### 去重扫描

开启后，守护线程对全库条目做**摘要向量两两余弦比对**（相似度 ≥ `dedup_threshold`，默认 0.9）：
- 跳过已标记 `is_time_sensitive=true` 的条目
- 发现重复：保留创建时间更早的，标记更晚的为 `is_time_sensitive=true`（TTL 默认 3 天）
- 后续 TTL 清理自动删除被标记的重复条目

开启方式：

```bash
set VELLUM_DEDUP_ENABLE=true
# 或通过 config 表：
# INSERT OR REPLACE INTO config (key, value) VALUES ('dedup_enable', 'true');
```

---

## 项目结构

```
vellum/
├── __init__.py               # 版本号
├── server.py                 # MCP 入口 + 10 工具 + @_tool + 守护线程
├── db.py                     # SQLite 连接 + 初始化 + 迁移
├── errors.py                 # 异常层次
├── groups.py                 # CPM 分组（支持任意 k）
├── stores/
│   ├── __init__.py
│   └── human_timeline.py     # 记忆 CRUD + 分片 + 去重辅助
└── vector/
    ├── __init__.py
    └── adapter.py            # 向量引擎 + 预合并向量 + 摘要向量
schemas/
└── schema.sql
tests/
├── __init__.py
├── test_errors.py            # 6 个测试
└── test_stores.py            # 13 个测试
```

---

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `VELLUM_DB_PATH` | `vellum/vellum.db` | SQLite 文件绝对路径 |
| `VELLUM_TRANSFORMER_MODEL` | `BAAI/bge-small-zh-v1.5` | 嵌入模型名称 |
| `VELLUM_DEDUP_ENABLE` | `false` | 启用后台去重扫描 |
| `VELLUM_DEDUP_THRESHOLD` | `0.9` | 去重扫描余弦相似度阈值 |
| `VELLUM_DAEMON_INTERVAL` | `1800` | 守护线程扫描间隔（秒） |
| `VELLUM_DEFAULT_TTL_DAYS` | `3` | time_sensitive 条目默认 TTL（天） |

## 许可证

MIT
