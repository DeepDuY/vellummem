# VellumMem（羊皮纸记忆）— AI 持久记忆系统

> **羊皮纸** — 古老的记录载体，记忆刻在上面。
> 一个基于 MCP（Model Context Protocol）的 AI 持久记忆系统，让 AI 助手拥有长期记忆能力。

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## 目录

- [这是什么](#这是什么)
- [架构总览](#架构总览)
- [工作原理](#工作原理)
- [快速开始](#快速开始)
- [环境变量参考](#环境变量参考)
- [MCP 工具完整参考](#mcp-工具完整参考)
- [检索设计详解](#检索设计详解)
- [性能基准](#性能基准)
- [使用场景](#使用场景)
- [数据库结构](#数据库结构)
- [常见问题](#常见问题)
- [设计文档](#设计文档)

---

## 这是什么

VellumMem 是一个 **MCP 服务器**，为 AI 助手提供跨会话的**持久化、可检索的记忆**。它解决了 LLM 的一个根本局限：每次对话都是从零开始。

| 记忆域 | 记住什么 | 检索方式 |
|:------|:--------|:--------|
| **人的记忆 🧠** | 过去的对话摘要、标签、上下文原文 | 语义向量检索（自然语言查询） |
| **项目记忆 💻** | 代码仓库、文件索引、架构决策、任务 | 关键词 / FTS5（精准、代码感知） |

**核心亮点：**

- **零外部服务** — 单文件 SQLite + 本地模型。不需要向量数据库、不需要云 API。
- **预合并向量设计** — 存储量 1/6、速度 4 倍，**数学等价**于传统多向量方案。
- **双模式检索** — 向量模式做模糊语义回忆，关键词模式做精准代码查找。
- **MCP 原生** — 适配任何 MCP 主机（DeepChat、Claude Desktop、自定义应用）。

---

## 架构总览

```
┌──────────────────────────────────────────────────────────────┐
│                      AI 助手（宿主）                            │
│    memory_init / memory_query / memory_write / ...           │
└────────────────────────┬─────────────────────────────────────┘
                         │ MCP (stdio)
┌────────────────────────▼─────────────────────────────────────┐
│                    VellumMem MCP 服务器                        │
│                    Python + FastMCP                           │
│                                                               │
│    ┌──────────────────────┐    ┌──────────────────────────┐   │
│    │      Router 路由器    │    │     Session 会话         │   │
│    │  根据 mode 分发查询    │◄──►│  模式/项目持久化         │   │
│    └────────┬────────┬────┘    └──────────────────────────┘   │
│             │        │                                         │
│      ┌──────▼──┐ ┌──▼──────┐  ┌──────────────────────────┐   │
│      │ 人的记忆  │ │ 项目记忆 │  │    Vector Adapter         │   │
│      │  存储    │ │  存储   │  │  向量引擎适配器             │   │
│      │         │ │         │  │  bge-small-zh-v1.5         │   │
│      │ timeline│ │ projects│  │  预合并向量                 │   │
│      │ context │ │ file_map│  └──────────────────────────┘   │
│      │ vectors │ │ decisions│                                 │
│      │         │ │ tasks    │                                 │
│      └────┬────┘ └────┬────┘                                 │
│           │           │                                       │
└───────────┼───────────┼───────────────────────────────────────┘
            │           │
      ┌─────▼───────────▼──────┐
      │   SQLite（1 个文件）      │
      │   vellum.db             │
      └─────────────────────────┘
```

### 组件说明

| 组件 | 文件 | 职责 |
|:----|:----|:-----|
| **Server 服务器** | `server.py` | MCP 入口，8 个工具的声明和实现，惰性初始化 |
| **Router 路由器** | `router.py` | 模式分发：`human` → 向量检索，`code` → 关键词检索 |
| **Session 会话** | `session.py` | 模式/项目配置持久化，存到 DB config 表，重启不丢 |
| **Vector Adapter 向量引擎** | `vector/adapter.py` | sentence-transformers 封装，预合并向量编/解码和搜索 |
| **Human Timeline 记忆存储** | `stores/human_timeline.py` | 人的记忆 CRUD + 上下文分片管理 |
| **Project Store 项目存储** | `stores/projects.py` | 项目卡片管理 |
| **File Map 文件索引** | `stores/file_map.py` | 代码文件索引，含符号和依赖信息 |
| **Decision Store 决策日志** | `stores/decisions.py` | 架构决策日志 |
| **Task Store 任务管理** | `stores/tasks.py` | 任务追踪（状态、阻塞项） |
| **Database 数据库** | `db.py` | SQLite 连接、Schema 初始化、配置迁移 |
| **Entry Point 启动入口** | `run.py` | CLI 启动器，自动设置绝对路径的 `VELLUM_DB_PATH` |

---

## 工作原理

### 人类记忆检索流程

```
memory_query("我们之前讨论过 vellum 的架构吗？")

  1. 编码查询 → bge-small-zh-v1.5（512 维向量）
  2. 对每条记忆：
       分数 = 查询向量 · 预合并向量   # 1 次内积运算
       如果分数 >= score_threshold（默认 0.15）：
           加入结果集
  3. 按分数降序排列
  4. 返回 top_k 条结果（默认 3）
```

每条记忆存储 **1 个预合并向量**：

```
预合并向量 = (归一化摘要 + 归一化标签0 + ... + 归一化标签4) / 6
```

**数学等价性证明**：因为向量内积是线性运算，所以：

```
分数 = (q·s + q·t₀ + ... + q·t₄) / 6 = q · (s + t₀ + ... + t₄) / 6 = q · 预合并向量
```

即：**分别计算 6 个向量分数再平均 = 1 次内积与预合并向量的分数**。这经 10,000 次随机向量测试验证，最大浮点误差仅 1.06e-08（比检索所需精度小 100 万倍）。

### 项目记忆检索流程

```
memory_query("auth middleware", mode="code")

  1. FileMapStore.search()    — 关键词 + FTS5 匹配路径/摘要/符号
  2. DecisionStore.search()   — 关键词匹配标题+正文
  3. TaskStore.get_active()   — 按标题关键词过滤
  → 合并所有匹配结果返回
```

---

## 快速开始

### 环境要求

- **Python 3.12+**
- **网络连接**（仅首次运行需要——下载约 36MB 的嵌入模型）

### 安装

```bash
# 进入 vellum 目录
cd path/to/vellum

# 安装基础依赖
pip install -r requirements.txt

# 安装向量引擎（可选但强烈推荐）
pip install sentence-transformers>=3.0.0
```

### 启动

```bash
python run.py
```

服务器通过 stdio 启动 MCP 端点。没有 Web 服务器、没有端口——纯 MCP 协议通信。

### 配置宿主

在 MCP 宿主（如 DeepChat、Claude Desktop）的配置中添加：

```json
{
  "mcpServers": {
    "vellummem": {
      "command": "python",
      "args": ["path/to/vellum/run.py"],
      "env": {
        "VELLUM_DB_PATH": "C:/data/vellum.db",
        "VELLUM_TRANSFORMER_MODEL": "BAAI/bge-small-zh-v1.5"
      }
    }
  }
}
```

### 验证是否正常工作

```python
# 在 AI 助手中调用：
memory_status()
# 期望输出：
# {"session": {"mode": "human", ...}, "storage": {"human_timeline": 0, ...}}
```

---

## 环境变量参考

| 变量名 | 必填 | 默认值 | 说明 |
|:------|:----|:------|:-----|
| `VELLUM_DB_PATH` | ❌ 否 | `./vellum.db` | SQLite 数据库文件路径。通过 `run.py` 启动时，默认为 vellum 项目目录的绝对路径。 |
| `VELLUM_TRANSFORMER_MODEL` | ❌ 否 | `BAAI/bge-small-zh-v1.5` | Sentence-transformer 模型名称或本地路径。必须是 512 维输出。其他可用的模型：`all-MiniLM-L6-v2`（384 维，中文差）。 |
| `HF_ENDPOINT` | ❌ 否 | *(未设置)* | HuggingFace 镜像地址，用于受限网络下的模型下载。示例：`https://hf-mirror.com`。首次下载失败时自动切换。 |

### 数据库 Config 表

SQLite 的 `config` 表还存储了一些**持久化配置**，重启后保留。这些**不是**通过环境变量设置，而是通过 `memory_set_mode` 等工具设置：

| 配置键 | 默认值 | 类型 | 说明 | 通过什么设置 |
|:------|:------|:----|:-----|:-----------|
| `mode` | `human` | str | 当前检索模式：`human`（向量检索）或 `code`（关键词/FTS5） | `memory_set_mode("code")` |
| `project_id` | `""` | str | 当前绑定的项目 ID | `memory_init(project_path=...)` |
| `project_path` | `""` | str | 当前绑定的项目根路径 | `memory_init(project_path=...)` |
| `vector_engine` | `transformer` | str | 向量搜索引擎（固定为 `transformer`） | *(内部使用)* |
| `score_threshold` | `0.15` | float | 向量检索最低匹配分数 | `memory_query(score_threshold=0.2)` |

---

## MCP 工具完整参考

### `memory_init` — 初始化会话

初始化（或重置）会话上下文。首次调用时惰性初始化：建表、加载模型、创建存储。

```
参数：
    project_path (str | None): 可选的项目根路径。
                                传入时会创建/绑定项目卡片并扫描文件。

返回：
    {"message": "VellumMem ready", "mode": str, "project": str}
```

### `memory_query` — 检索记忆

用自然语言搜索记忆。

```
参数：
    query (str):              自然语言查询文本（必填）
    mode (str | None):        "human" 或 "code"。默认使用会话模式。
    top_k (int):              返回条数（默认 3）。设大值即"贪婪模式"。
    score_threshold (float):  human 模式最低分数（默认 0.15）。低于此值返回空。

返回：
    {"mode": str, "results": [
        {"source_domain": "human", "source_table": "human_timeline",
         "source_id": "...", "summary": "...", "score": 0.55,
         "has_context": true, "total_chunks": 3, "tags": [...]}
    ]}
```

| 参数设置 | 效果 |
|:--------|:-----|
| `top_k=1` | 获取最匹配的一条 |
| `top_k=10` | 贪婪模式——撒大网 |
| `score_threshold=0.3` | 只返回高置信度匹配 |
| `score_threshold=0.0` | 返回所有结果，不过滤 |
| `mode="code"` | 搜索项目记忆（需先绑定项目） |

### `memory_get_context` — 获取上下文

获取某条记忆的原始对话上下文分片。

```
参数：
    timeline_id (str):  目标 human_timeline 条目 ID
    limit (int):        最多返回几个分片（默认 1）
    offset (int):       偏移量（0=最新分片，1=次新...）

返回：
    [{"id": "...", "context": "...", "chunk_index": 0, "create_timestamp": ...}]
```

分片从最新开始返回。要翻出所有上下文：
1. 先调 `memory_query` → 获取 `total_chunks` 和 `source_id`
2. 再调 `memory_get_context(timeline_id=source_id, limit=total_chunks, offset=0)`

### `memory_set_mode` — 切换模式

在 human 和 code 模式之间切换。结果持久化到数据库，重启后保留。

```
参数：
    mode (str): "human" 或 "code"

返回：
    {"message": "...", "mode": str}
```

### `memory_write` — 写入记忆

存储一条记忆条目，包含摘要、标签和可选上下文。

```
参数：
    data (str): JSON 字符串，包含字段：
        - summary (str):      会话摘要（上限 200 字）
        - tags (list[str]):   5 个标签（**强制**，不足或超出会被拒绝）
        - context_text (str): 初始对话上下文（自动分片，每片上限约 8000 字符）

返回：
    {"message": "Memory stored", "written": [...], "id": str}
```

标签要求：
- 必须恰好 **5 个标签**
- 标签帮助语义向量检索
- 建议：`["架构", "bug修复", "前端", "数据库", "讨论"]`

上下文分片：
- 上下文文本按自然分隔符（`##`、`-`、段落等）自动拆分
- 每片上限 8000 字符
- 分片通过 `conversation_context_link` 链接到父条目

### `memory_write_context` — 追加上下文

向已有条目追加更多上下文分片。

```
参数：
    timeline_id (str):    目标条目 ID
    context_text (str):   追加的对话上下文（自动分片）

返回：
    {"id": str, "new_context_ids": [str, ...]}
```

### `memory_project_sync` — 同步项目

扫描并索引项目文件。支持 Python、TypeScript、JavaScript、Rust、Go。

```
参数：
    path (str | None):  项目路径。不传时使用会话绑定的项目。

返回：
    {"message": "...", "files_scanned": int, "files_indexed": int}
```

### `memory_status` — 系统状态

查看系统健康状态和统计信息。

```
参数：
    (无)

返回：
    {"session": {"mode": str, "project_id": str, "project_path": str},
     "storage": {"human_timeline": int, "conversation_context": int, ...}}
```

---

## 检索设计详解

### 单层向量检索

VellumMem 采用**单层向量检索**方案，每条记忆存储 **1 个预合并向量**——摘要向量和 5 个标签向量的加权平均。

### 预合并向量的数学原理

核心洞察：向量内积是线性运算。

```
score = (q·s + q·t₀ + q·t₁ + q·t₂ + q·t₃ + q·t₄) / 6
      = q · (s + t₀ + t₁ + t₂ + t₃ + t₄) / 6
      = q · merged
```

所以**6 次内积的平均值 = 1 次内积与平均向量的点积**。通过 10,000 次随机向量测试验证：

```
最大差异:  1.06e-08  (float32 精度级)
平均差异:  2.10e-09
差异 > 1e-7: 0 次
```

检索所需的精度是 0.01（百分位），差异小了 100 万倍。**实用层面完全等价**。

---

## 性能基准

在普通笔记本 CPU（无 GPU）上测试，1,000 条记忆：

### 端到端查询耗时

| 步骤 | 耗时 |
|:----|:----|
| 查询向量编码 | ~20 ms |
| SQLite 读取 1000 个 BLOB | ~5 ms |
| Pickle 反序列化 | ~10 ms |
| 1000 次内积运算 | ~2 ms |
| 排序 | <1 ms |
| **总计** | **~38 ms** |

### 模型对比

| 模型 | 维度 | 中文质量 | 英文质量 | 大小 |
|:----|:----|:--------|:--------|:----|
| `BAAI/bge-small-zh-v1.5` | 512 | ✅ 优秀 | ⚠️ 一般 | ~36 MB |
| `all-MiniLM-L6-v2` | 384 | ⚠️ 差 | ✅ 优秀 | ~23 MB |
| `BAAI/bge-large-zh-v1.5` | 1024 | ✅ 最佳 | ⚠️ 一般 | ~120 MB |

### 检索准确度实测

以下是用当前记忆库实际运行测试的结果（详见 [测试报告](#)）：

| 测试维度 | 得分 | 说明 |
|:--------|:---|:-----|
| 🔍 精准匹配（场景名称直查） | 10/10 | 100% 命中第一名 |
| 🧠 语义相似（换说法查） | 10/10 | 5/5 变体全部正确定位 |
| 🚫 跨域区分（不该匹配的排除） | 9/10 | 分数差距普遍 > 0.05 |
| 🎯 细粒度检索（查具体概念） | 8/10 | SOLID/Scrum 精准命中，番茄工作法需改进 |
| 🔲 边界测试（阈值过滤、无关查询） | 10/10 | 不相关内容返回空，阈值过滤完美 |

---

## 使用场景

### 1. 跨会话持续对话

```python
# 会话 1："我们来设计 auth 模块..."
memory_write(data={
    "summary": "Auth 模块设计 — JWT + OAuth2 流程",
    "tags": ["架构", "认证", "jwt", "oauth", "设计"],
    "context_text": "我们决定用 JWT 15 分钟有效期..."
})

# 第二天会话 2："上次关于 auth 模块我们怎么定的？"
results = memory_query("auth 模块设计决策")
# → 返回上条会话，分数 ~0.52
```

### 2. 代码库索引与问答

```python
# 索引项目
memory_init(project_path="/home/user/my-project")
memory_project_sync()

# 提问代码
memory_query("数据库连接在哪里配置的", mode="code")
# → 返回相关的 file_map 条目
```

### 3. 决策日志

```python
# 记录决策
memory_write(data={
    "summary": "订单服务选 PostgreSQL 而非 MongoDB",
    "tags": ["决策", "数据库", "postgresql", "订单", "架构"],
    "context_text": "选 PostgreSQL 的原因：1) 需要复杂 JOIN 2) ACID 事务..."
})

# 三周后："为什么选了 PostgreSQL 而不是 MongoDB？"
result = memory_query("为什么选 postgresql 不选 mongodb")
```

### 4. 个人知识管理

将阅读笔记、学习心得结构化存储：

```python
memory_write(data={
    "summary": "系统设计面试 — 分布式事务 Saga 模式",
    "tags": ["学习", "系统设计", "分布式事务", "Saga", "笔记"],
    "context_text": "Saga 模式有 Choreography 和 Orchestration 两种..."
})
```

---

## 数据库结构

### `human_timeline` — 记忆条目表

| 列名 | 类型 | 说明 |
|:----|:----|:-----|
| `id` | TEXT PK | `YYYYMMDD_HHMMSS_5随机字符` |
| `session_start` | TEXT | 会话开始时间（ISO 格式） |
| `session_end` | TEXT | 会话结束时间（ISO 格式） |
| `summary` | TEXT | 摘要（上限 200 字） |
| `tags` | TEXT | JSON 数组，必须恰好 5 个 |
| `conversation_context_link` | TEXT | JSON 数组，上下文分片 ID 列表 |
| `create_timestamp` | INTEGER | 创建时间戳（Unix 毫秒） |
| `update_timestamp` | INTEGER | 更新时间戳（Unix 毫秒） |

### `entry_vectors` — 向量存储表

| 列名 | 类型 | 说明 |
|:----|:----|:-----|
| `entry_id` | TEXT PK | FK → human_timeline(id)，级联删除 |
| `merged_blob` | BLOB | `pickle.dumps(np.ndarray(float32, 512))` |

### `conversation_context` — 上下文分片表

| 列名 | 类型 | 说明 |
|:----|:----|:-----|
| `id` | TEXT PK | 分片 ID |
| `timeline_id` | TEXT FK | FK → human_timeline(id)，级联删除 |
| `context` | TEXT | 分片内容（上限 8000 字符） |
| `chunk_index` | INTEGER | 在条目内的顺序 |
| `create_timestamp` | INTEGER | 创建时间戳（Unix 毫秒） |

### `config` — 配置表

| 列名 | 类型 | 说明 |
|:----|:----|:-----|
| `key` | TEXT PK | 配置键名 |
| `value` | TEXT | 字符串值（按 type 解析） |
| `type` | TEXT | 值类型：`str`、`float`、`int`、`bool` |
| `description` | TEXT | 人类可读的描述 |
| `created_at` | TEXT | ISO 创建时间 |
| `updated_at` | TEXT | ISO 更新时间 |

### `projects`、`file_map`、`decisions`、`tasks`

这些表仅在 `code` 模式下使用，详见各 store 文件。

---

## 常见问题

### "VellumMem failed to initialize" 初始化失败

查看服务器 stderr 日志。常见原因：

| 症状 | 可能原因 | 解决方案 |
|:----|:--------|:--------|
| `ModuleNotFoundError: sentence_transformers` | 缺少可选依赖 | `pip install sentence-transformers>=3.0.0` |
| 下载模型卡住超过 2 分钟 | 网络无法访问 HuggingFace | 设置 `HF_ENDPOINT=https://hf-mirror.com` |
| `sqlite3.OperationalError: no such table` | 数据库损坏或 schema 不匹配 | 删除 `vellum.db` 重启 |
| `memory_write` 报 tag 错误 | 标签数量不对 | 提供恰好 5 个标签 |

### 模型下载问题

VellumMem 先尝试 `local_files_only=True`（本地缓存），失败后自动下载。如果完全离线：

```bash
# 提前下载模型
python -c "
from sentence_transformers import SentenceTransformer
SentenceTransformer('BAAI/bge-small-zh-v1.5')
"
```

### 数据库位置

默认 `vellum.db` 创建在项目根目录。修改方法：

```bash
set VELLUM_DB_PATH=C:/my/custom/path/vellum.db
python run.py
```

或者在 MCP 宿主配置的 `env` 中设置。

### `memory_write` 标签数量要求

**必须恰好 5 个标签**，不是 4 个也不是 6 个。原因：预合并向量公式 `(summary + tag0 + ... + tag4) / 6` 依赖固定的 6 个分量。

好的标签示例：`["架构", "bug修复", "前端", "数据库", "讨论"]`

### 如何删除记忆？

当前不支持通过 MCP 工具删除。可以手动操作 SQLite：

```sql
-- 删除某条记忆及其向量和上下文
DELETE FROM human_timeline WHERE id = '20260429_010323_St5Zu';
-- 级联删除会自动处理 entry_vectors 和 conversation_context
```

### 备份迁移

直接复制 `vellum.db` 文件即可：

```bash
copy vellum.db vellum_backup.db
```

模型首次下载后会缓存到 HuggingFace 的缓存目录（通常 `~/.cache/huggingface/`），迁移时不需要重新下载。

---

## 设计文档

- `design/retrieval-redesign.md` — 检索架构设计文档
- `design/architecture.md` — 系统架构文档

---

## 技术栈

- **运行环境**：Python 3.12+
- **框架**：FastMCP（MCP 协议 over stdio）
- **向量引擎**：sentence-transformers（`BAAI/bge-small-zh-v1.5`，512 维）
- **存储**：SQLite（单文件，WAL 模式，外键约束）
- **依赖**：约 10 个轻量包（详见 `requirements.txt`）
