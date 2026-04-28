# VellumMem（羊皮纸记忆）— AI 持久记忆系统

> **羊皮纸** — 古老的记录载体，记忆刻在上面。
> 一个基于 MCP（Model Context Protocol）的 AI 持久记忆系统。

---

## 是什么

VellumMem 是一个 MCP 服务器，为 AI 助手提供持久化、可检索的记忆能力。

- **人的记忆** — 记住过去聊过什么（"我们之前讨论过什么来着？"）
- **项目记忆** — 索引代码库、决策和任务（"auth 模块在哪？"）

两种记忆都可以用自然语言来搜索。

---

## 架构

```
┌────────────────────────────────────────────────────┐
│              AI 助手 (DeepChat)                      │
│  memory_init / memory_query / memory_write / ...   │
└──────────────────────┬─────────────────────────────┘
                       │ MCP (stdio)
┌──────────────────────▼─────────────────────────────┐
│              VellumMem MCP Server                   │
│              (Python + FastMCP)                     │
│                                                     │
│  mode="human" → 向量检索（预合并向量）              │
│  mode="code"  → 关键词 / FTS5 检索                  │
└──────┬──────────────────────────────────┬──────────┘
       │                                  │
┌──────▼──────────┐            ┌──────────▼──────┐
│  人的记忆域       │            │  项目记忆域       │
│                  │            │                  │
│  human_timeline  │            │  projects        │
│  conversation_   │            │  file_map        │
│    context       │            │  decisions       │
│  entry_vectors   │            │  tasks           │
└─────────────────┘            └─────────────────┘
```

### 人的记忆域

| 表 | 功能 |
|----|------|
| `human_timeline` | 每次会话一条记录 — 摘要(≤200字) + 5个标签 |
| `conversation_context` | 会话原文分片，自动按自然分隔符拆分(≤8000字/片) |
| `entry_vectors` | 预合并向量(512维，每条记忆1个向量) |

### 项目记忆域

| 表 | 功能 |
|----|------|
| `projects` | 项目卡片（名称、路径、技术栈） |
| `file_map` | 文件索引（符号、依赖、修改历史） |
| `decisions` | 决策日志（理由、方案、影响文件） |
| `tasks` | 任务追踪（状态、阻塞项、进度） |

---

## 检索方式

### 人类记忆 — 单层向量检索

```
memory_query("vellummem的开发进度")
→ 编码查询 → BAAI/bge-small-zh-v1.5 (512维)
→ 每条记忆 1 次内积（预合并向量）
→ 按 score_threshold 过滤（默认 0.15）
→ 按分数降序排列
→ 返回 top_k 条（默认 3）
```

每条记忆存储 **1 个预合并向量** = `(归一化摘要 + 归一化标签0 + ... + 归一化标签4) / 6`。数学上等价于分别评分，但快了 4 倍。

**关键参数：**
- `top_k` — 返回条数（默认 3，设大值即"贪婪模式"）
- `score_threshold` — 最低分数（默认 0.15，低于此值返回空）

### 项目记忆 — 关键词 / FTS5

```
memory_query("auth 中间件", mode="code")
→ FileMapStore.search() — 关键词 + FTS5 匹配路径/摘要/符号
→ DecisionStore.search() — 关键词匹配标题+正文
→ TaskStore.get_active() — 按标题关键词过滤
```

---

## 快速开始

```bash
pip install -r requirements.txt
python run.py
```

服务器启动 MCP stdio 端点。在 DeepChat 中配置：

```json
{
  "mcpServers": {
    "vellummem": {
      "command": "python",
      "args": ["path/to/vellum/run.py"]
    }
  }
}
```

### 环境变量

| 变量 | 默认值 | 说明 |
|------|-------|------|
| `VELLUM_DB_PATH` | `./vellum.db` | SQLite 数据库路径 |
| `VELLUM_TRANSFORMER_MODEL` | `BAAI/bge-small-zh-v1.5` | 向量模型 |

---

## MCP 工具

| 工具 | 功能 |
|------|------|
| `memory_init` | 初始化会话（可选绑定项目） |
| `memory_query` | 用自然语言检索记忆 |
| `memory_get_context` | 获取对话原文分片（从最新往前翻） |
| `memory_set_mode` | 切换 human / code 检索模式 |
| `memory_write` | 写入记忆条目（tags 必须提供 5 个） |
| `memory_write_context` | 追加上下文分片 |
| `memory_project_sync` | 扫描并索引项目文件 |
| `memory_status` | 查看系统状态 |

---

## 设计文档

- `design/architecture.md` — 原始 v4 架构文档
- `design/retrieval-redesign.md` — v5 检索重构设计（当前）

---

## 技术栈

- **运行环境**：Python 3.12+
- **框架**：FastMCP
- **向量引擎**：sentence-transformers（BAAI/bge-small-zh-v1.5，512 维）
- **存储**：SQLite（单文件）
- **依赖**：约 10 个包（见 requirements.txt）
