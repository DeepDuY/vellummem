# VellumMem（羊皮纸记忆）— AI 记忆系统

> **羊皮纸** — 古老的记录载体，记忆刻在上面。
> 一个场景感知的 AI 持久记忆系统，基于 MCP（Model Context Protocol）构建。

---

## 设计理念

### 核心矛盾

```
记忆需要"存得下、找得到"
  但存得越细 → 找得越慢
  存得越粗 → 找得到但细节不够
```

### 设计原则

| 原则 | 说明 |
|------|------|
| **渐进成本** | 从最便宜的检索开始（关键词 → 拆词 → LSI → Transformer），不够再花更大代价 |
| **渐进深度** | depth=1 搜 timeline，不够 depth=2 加 semantic，不够 depth=3 加 patterns，不够 depth=4 全量 |
| **默认全量** | hybrid 模式默认两边都搜，AI 只在需要优化时才缩窄范围 |
| **AI 显式管理** | 模式切换由 AI 主动调用，不依赖规则引擎猜测 |
| **结构化优先** | 实体/路径/时间匹配比向量搜索更快更准，优先使用 |
| **证据链完整** | 每条事实都能追溯回原始会话 |
| **可降级** | 完全不依赖向量搜索也能正常工作 |
| **零外部依赖** | 一个 SQLite 文件存所有记忆，无需额外数据库服务 |

---

## 架构

### 双域记忆系统

```
                    ┌──────────────────────────┐
                    │      AI Assistant          │
                    │  (Claude / DeepChat)       │
                    │                            │
                    │  memory_init()             │
                    │  memory_query(query)       │
                    │  memory_set_mode("code")   │
                    │  memory_write(data)        │
                    └───────────┬────────────────┘
                                │  MCP (stdio)
                                ▼
                    ┌──────────────────────────┐
                    │    VellumMem MCP Server    │
                    │    (Python FastMCP)       │
                    │                           │
                    │  Route: mode → domain     │
                    │  hybrid → H + P + Hub     │
                    │  human  → H only          │
                    │  code   → P only          │
                    └───────────┬────────────────┘
                                │
         ┌──────────────────────┼──────────────────────┐
         ▼                      ▼                      ▼
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│  人的记忆域        │  │  项目记忆域        │  │  Decision Hub     │
│  Human Memory     │  │  Project Memory  │  │  (枢纽层)          │
│                   │  │                  │  │                   │
│  timeline         │  │  projects        │  │  timeline ↔       │
│  semantic         │  │  file_map        │  │  decision ↔       │
│  patterns         │  │  decisions       │  │  file_map          │
│  reflections      │  │  tasks           │  │                   │
└──────────────────┘  └──────────────────┘  └──────────────────┘
```

### 人的记忆域（Human Memory Domain）

五张表：

| 表 | 层级 | 类型 | 功能 |
|----|:----:|------|------|
| **timeline** | L1 | 追加式，不可变 | 每次会话一条摘要，含 key_moments、tags |
| **semantic_entities** | L2 | 可更新 | 实体注册表（"Python""JWT"），含别名和重要性 |
| **semantic_facts** | L2 | 版本链 | 实体关系（"Python → 迁移 → Go"），带证据链 |
| **patterns** | L3 | 渐进演化 | 跨会话发现的行为规律 |
| **reflections** | L4 | 高度压缩 | 跨会话的深度洞察 |

### 项目记忆域（Project Memory Domain）

四张表：

| 表 | 功能 |
|----|------|
| **projects** | 项目卡片，含路径、名称、描述 |
| **file_map** | 文件索引，按模块/路径/函数名检索 |
| **decisions** | 决策日志，含理由、方案、影响文件 |
| **tasks** | 任务追踪，含状态、阻塞项、进展 |

### Decision Hub（枢纽层）

轻量级链接表实现跨域双向耦合：

```
human domain               code domain
    │                          │
    │   timeline ── Hub ── decision
    │   semantic  ── Hub ── file_map
    │                          │
```

AI 问"为什么用 JWT"，hybrid 模式会：
1. 搜 timeline → "讨论了认证方案"
2. 通过 Hub 找到决策 → "JWT 认证方案"
3. 通过决策找到文件 → "auth/middleware.ts"

---

## 四级记忆深度

| 层级 | 来源 | 内容 | 特点 |
|:----:|------|------|:----:|
| **L1** | timeline | 原始会话记录 | 最新、最轻量、最快 |
| **L2** | semantic | 实体/关系事实 | 跨会话上下文 |
| **L3** | patterns | 行为模式 | 规律发现，AI 主动推送 |
| **L4** | reflections | 深度洞察 | 最高层合成，最低频更新 |

先浅后深，不够再挖：

```
memory_query(query, depth=1)  → 只看 timeline（最快）
  ↓ 不够
memory_query(query, depth=2)  → + semantic 事实
  ↓ 还要
memory_query(query, depth=3)  → + patterns 模式
  ↓ 全要
memory_query(query, depth=4)  → + reflections 洞察
```

---

## 三种检索模式

| 模式 | 检索范围 | 典型场景 |
|------|---------|---------|
| **hybrid**（默认） | 人 + 项目 + 枢纽关联 | **大部分情况，不用思考** |
| human | 仅人的记忆 | 纯回忆/聊天 |
| code | 仅项目记忆 | 纯写代码 |

模式是 **sticky（粘性）** 的，不切换就不变：

```
memory_init()                        → mode = hybrid
memory_query("认证模块在哪")          → hybrid，两边搜
memory_set_mode("code")              → 只需要代码
memory_query("middleware.ts")        → code，只搜项目侧
memory_query("为什么用JWT", "hybrid") → 临时覆盖，用完还是 code
```

---

## 搜索管道（多策略降级）

```
query → LIKE 精确匹配 → 拆词兜底 → LSI 语义 → [Transformer]（可选）
        最快                较慢         离线深度学习
```

VellumMem 自动选择最强引擎：

```
sentence-transformers 已安装？ → Transformer（384 维语义向量）
          ↓ 否
        LSI（scikit-learn TruncatedSVD，零下载）
```

通过 `VELLUM_FORCE_VECTOR=LSI` 强制降级。

---

## MCP 工具

| 工具 | 参数 | 功能 |
|------|------|------|
| `memory_init` | `project_path?` | 初始化记忆系统 |
| `memory_query` | `query`, `mode?`, `depth?` | 检索记忆，支持渐进深度 |
| `memory_set_mode` | `mode` | 切换 human / code 模式 |
| `memory_write` | `data`, `mode?` | 写入记忆（会话结束前必须调用） |
| `memory_project_sync` | `path?` | 扫描项目文件，更新索引 |
| `memory_status` | 无 | 查看当前模式、项目、存储统计 |

### 最简使用路径

```
# 1. 会话开始
memory_init()

# 2. 检索
memory_query(query="项目概况")       # 默认 hybrid + 全深度
memory_query(query="JWT", depth=1)   # 只看 timeline，不够再 depth=2

# 3. 写入（会话结束前）
memory_write(data={
    "summary": "讨论了认证方案，决定用JWT替代Session",
    "decisions": [{"title": "JWT方案", "body": "无状态更适合桌面"}],
    "tags": ["认证", "决策", "JWT"]
})
```

---

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动 MCP Server
python run.py

# 3. 可选：升级到 Transformer 引擎
pip install sentence-transformers  # 装完自动启用
```

### DeepChat MCP 配置

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

自定义数据库路径：

```json
"env": {
  "VELLUM_DB_PATH": "/path/to/vellum.db",
  "VELLUM_FORCE_VECTOR": "LSI",
  "VELLUM_TRANSFORMER_MODEL": "all-MiniLM-L6-v2",
  "HF_ENDPOINT": "https://hf-mirror.com"
}
```

---

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `VELLUM_DB_PATH` | 数据库文件路径 | `./vellum.db` |
| `VELLUM_FORCE_VECTOR` | 强制 LSI（跳过 Transformer 检测） | 空（自动检测） |
| `VELLUM_TRANSFORMER_MODEL` | 自定义 Transformer 模型名 | `all-MiniLM-L6-v2` |
| `HF_ENDPOINT` | Hugging Face 镜像地址 | 空（官方源） |

---

## 向量引擎对比

| 维度 | LSI（scikit-learn） | Transformer（sentence-transformers） |
|------|-------------------|-------------------------------------|
| 依赖 | `scikit-learn`（必装） | `sentence-transformers`（可选） |
| 模型 | 无 | `all-MiniLM-L6-v2`（~80MB，自动缓存） |
| 向量维度 | ~50（SVD 降维） | **384**（原生） |
| 短文本语义 | 较弱 | 强 |
| 网络 | 离线 | 首次需下载 |
| 速度 | 快 | 快（推理优化） |

---

## 项目结构

```
vellum/（仓库根目录）
├── run.py                   # MCP Server 入口
├── schema.sql               # 数据库表结构（12 张表）
├── requirements.txt         # 依赖清单
├── .gitignore
├── README.md                # 英文文档
├── README-ZH.md             # 中文文档
├── design/
│   ├── architecture.md       # 完整架构设计文档
│   └── devlog.md             # 开发日志
└── vellum/（Python 包）
    ├── server.py             # MCP Server，6 个 tools
    ├── router.py             # 模式路由 + 多策略搜索 + 渐进深度
    ├── db.py                 # SQLite 连接管理
    ├── session.py            # 会话状态（mode sticky）
    ├── hub.py                # Decision Hub 跨域链接
    ├── stores/
    │   ├── timeline.py       # L1: 原始会话记录
    │   ├── semantic.py       # L2: 实体/关系事实
    │   ├── patterns.py       # L3: 行为模式
    │   ├── reflections.py    # L4: 深度洞察
    │   ├── decisions.py      # 决策日志
    │   ├── tasks.py          # 任务追踪
    │   ├── projects.py       # 项目卡片
    │   └── file_map.py       # 文件索引
    └── vector/
        └── adapter.py        # TransformerAdapter + VectorAdapter（LSI）
```

---

## 关键设计决策

| 决策 | 理由 |
|------|------|
| **默认 hybrid 模式** | AI 不需要一开始就选模式，`memory_init()` 无需传参 |
| **渐进深度检索** | depth=1 搜 timeline → depth=4 全量，AI 逐级决定 |
| **AI 显式管理模式切换** | 不设规则引擎猜测，避免误判 |
| **Mode sticky** | 设定后保持，不切换就不变，减少冗余调用 |
| **单一 SQLite 文件** | 零依赖，`vellum.db` 一个文件存所有记忆 |
| **LSI 兜底 + Transformer 可选** | 保证零网络也能跑，有网络就自动升级 |
| **搜索多策略降级** | LIKE → 拆词 → LSI → Transformer，逐级兜底 |
