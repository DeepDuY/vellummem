# VellumMem — 检索设计 v1

> 设计定稿：2026-04-29
> 状态：已实装 ✅

---

## 一、设计目标

### 核心问题

记忆系统需要让 AI 用自然语言快速找到过去的会话。传统的双层检索（关键词 + 向量）在自然语言查询下表现不佳：

| 问题 | 描述 |
|:----|:------|
| 关键词太宽或太窄 | OR 拆词把噪音全放进来，AND 匹配自然语言永远 0 条 |
| 阈值静默丢弃 | 向量层硬阈值导致语义相似但分数略低的匹配被丢弃 |
| 返回分数无意义 | 关键词命中返回 `score=1.0`，AI 无法判断匹配质量 |
| 存储膨胀 | 每条记忆存 6 个向量（1 summary + 5 tags），1000 条 = 6000 行 |

### 设计原则

| 原则 | 说明 |
|:----|:------|
| **单层检索** | 不搞关键词 + 向量两层，统一走语义向量 |
| **真实分数** | 返回 0~1 的余弦相似度，AI 自行判断相关性 |
| **可配置阈值** | 低于阈值的匹配不返回（默认 0.15） |
| **贪婪模式** | top_k 可调大，需要更多结果时用 |
| **零外部依赖** | 一个 SQLite 文件 + 本地模型，不需外部服务 |

---

## 二、方案选择

### 方案①：5 tag 分别评分

每条记忆存 6 个独立向量（1 summary + 5 tags），查询时分别计算余弦相似度后取平均：

```
score = (query·summary + query·tag0 + ... + query·tag4) / 6
```

✅ 质量好，每个 tag 独立贡献语义
❌ 6 个向量/条，存储大，查询慢

### 方案②：5 tag 拼接评分

5 个 tag 拼接成一个字符串"vellummem v4 重构 transformer bge"然后编码为一个向量：

```
score = (query·summary + query·concat_tags) / 2
```

✅ 存储小（2 向量/条）
❌ 质量不如方案①（拼接后 tag 粒度丢失）

### 方案③：预合并向量 ✅ **最终方案**

核心洞察：向量内积是线性运算。

```
score = (query·summary + query·tag0 + ... + query·tag4) / 6
      = query · (summary + tag0 + ... + tag4) / 6
```

**6 次内积的平均值 = 1 次内积与平均向量的点积。**

| 对比 | 分别评分 | 预合并 ✅ |
|:----|:--------|:---------|
| 存储 | 6 向量/条 | **1 向量/条** |
| SQLite 行数(1000条) | 6000 行 | **1000 行** |
| 查询(1000条) | 6000 次内积 | **1000 次内积** |
| 查询耗时 | ~147ms | **~38ms** |
| 检索质量 | ✅ 最好 | ✅ **数学等价** |
| 实现复杂度 | 复杂（6 向量管理） | **简单（1 向量管理）** |

**关键约束**：预合并后不能二次归一化，否则分数改变。

### 方案②补充验证：5 tag 拼接

| 查询 | 方案①(分别) | 方案②(拼接) | 方案③(纯拼) |
|:----|:-----------|:-----------|:-----------|
| 「vellummem的开发进度」 | ✅ A > C > B | ❌ B > A > C | ❌ B > C > A |
| 「向量引擎如何配置」 | ⚠️ Docker > A | ✅ A > Docker | ❌ Docker > A |
| 「docker 端口映射」 | ✅ Docker > C > A | ✅ Docker > B > C | ✅ Docker > C > B |

拼接方案不稳定，不如分别评分。预合并向量兼具分别评分的质量和拼接方案的存储效率。

### 数学等价性验证

通过 10000 次随机向量测试：

```
max diff:    1.06e-08  (float32 精度级)
mean diff:   2.10e-09
diff > 1e-7: 0 次
```

检索所需的精度是 0.01（百分位），差异小 100 万倍。实用层面完全等价。

---

## 三、检索流程

### 写入

```python
# 强制校验 5 个 tag（不足报错）
assert len(tags) == 5

# 编码
sv = model.encode(summary, normalize_embeddings=True)     # (512,)
tv = model.encode(tags, normalize_embeddings=True)          # (5, 512)

# 预合并（先归一化各分量，再平均，不二次归一化）
merged = (sv + tv.sum(axis=0)) / 6.0                        # (512,)

# 存储
INSERT INTO entry_vectors VALUES (?, pickle.dumps(merged))
```

### 查询

```python
def memory_query(text, top_k=3, score_threshold=0.15):
    qv = model.encode(text, normalize_embeddings=True)       # ~20ms
    
    for entry in load_all():
        merged = pickle.loads(entry.merged_blob)              # ~2KB/条
        score = float(qv @ merged)                            # 1 次内积
        
        if score >= score_threshold:
            results.append((score, entry))
    
    results.sort(key=lambda x: -x[0])
    return results[:top_k]
```

### 性能（1000 条）

| 环节 | 耗时 |
|:----|:----|
| query encode | ~20ms |
| SQLite 读 1000 BLOB | ~5ms |
| pickle 反序列化 | ~10ms |
| 内积运算 × 1000 | ~2ms |
| 排序 | <1ms |
| **总计** | **~38ms** |

---

## 四、数据库设计

### human_timeline 表

```sql
CREATE TABLE human_timeline (
    id                        TEXT PRIMARY KEY,             -- YMD_HMS_5RAND
    session_start             TEXT NOT NULL,                -- ISO datetime
    session_end               TEXT NOT NULL,                -- ISO datetime
    summary                   TEXT DEFAULT '',              -- 上限 200 字
    tags                      TEXT DEFAULT '[]',            -- JSON 数组，固定 5 个
    conversation_context_link TEXT DEFAULT '[]',            -- JSON 数组
    create_timestamp          INTEGER NOT NULL,
    update_timestamp          INTEGER NOT NULL
);
```

### entry_vectors 表

```sql
CREATE TABLE entry_vectors (
    entry_id    TEXT PRIMARY KEY REFERENCES human_timeline(id) ON DELETE CASCADE,
    merged_blob BLOB NOT NULL   -- pickle.dumps(np.ndarray(float32, 512))
);
```

### conversation_context 表

```sql
CREATE TABLE conversation_context (
    id               TEXT PRIMARY KEY,
    timeline_id      TEXT NOT NULL REFERENCES human_timeline(id) ON DELETE CASCADE,
    context          TEXT NOT NULL,
    chunk_index      INTEGER NOT NULL DEFAULT 0,
    create_timestamp INTEGER NOT NULL
);
```

---

## 五、MCP 接口

### memory_query

```json
memory_query(query, mode?, top_k=3, score_threshold=0.15)
```

| 参数 | 说明 |
|:----|:------|
| `query` | 自然语言查询文本 |
| `mode` | "human"（向量检索）或 "code"（关键词/FTS5），默认 session 模式 |
| `top_k` | 返回条数，默认 3；设大值即贪婪模式 |
| `score_threshold` | 最低匹配分数，默认 0.15，低于此值返回空 |

返回每个结果的**真实分数**（0~1 余弦相似度），不再固定返回 1.0。

### memory_write

```json
memory_write(data, mode?)
```

- `data.tags` 在 human 模式下**必须提供 5 个**，不足报错
- 自动计算预合并向量并持久化
- 自动分片存储 conversation_context

### 其他接口

| 接口 | 说明 |
|:----|:------|
| `memory_init` | 初始化会话，可选绑定项目 |
| `memory_get_context` | 获取上下文分片（从最新往前翻） |
| `memory_set_mode` | 切换 human / code 模式 |
| `memory_write_context` | 追加上下文分片 |
| `memory_project_sync` | 同步项目文件索引 |
| `memory_status` | 查看系统状态 |

---

## 六、配置项

| 配置键 | 默认值 | 类型 | 说明 |
|:------|:-------|:----|:------|
| `mode` | `human` | str | 当前检索模式 |
| `vector_engine` | `transformer` | str | 向量引擎 |
| `score_threshold` | `0.15` | float | 向量检索最低匹配分数 |

**环境变量**：

| 变量 | 默认值 | 说明 |
|:-----|:-------|:------|
| `VELLUM_DB_PATH` | `./vellum.db` | SQLite 路径 |
| `VELLUM_TRANSFORMER_MODEL` | `BAAI/bge-small-zh-v1.5` | 向量模型 |

---

## 七、模型选择

**最终选用：BAAI/bge-small-zh-v1.5**（512 维，~36MB）

对比实验 `"vellummem的开发进度"`：

| 排名 | MiniLM | bge | 说明 |
|:----:|:------|:----|:------|
| 1 | B(0.52) | **A(0.467)** | bge 将真正相关的 A 从第 3 提到第 1 |
| 2 | C(0.42) | C(0.451) | |
| 3 | A(0.31) | B(0.443) | |
| 4 | Docker(0.23) | Docker(0.357) | |

bge 的语义理解能力显著优于 MiniLM，对于中文自然语言查询的匹配质量更高。

---

## 八、验证结果

### 端到端链路测试（E2E）

| 测试 | 结果 |
|:----|:------|
| `memory_write` → `memory_query` | ✅ 存储后检索可召回，返回真实分数 0.55+ |
| 相关查询「vellummem 检索方案」 | ✅ 正例排第一，分数 0.58 |
| 不相关查询「红烧肉做法」 | ✅ 分数降至 0.25 |
| `score_threshold=0.25` 过滤 | ✅ 低于阈值返回空 |
| `top_k=5` 贪婪模式 | ✅ 返回 5 条 |
| `memory_get_context` | ✅ 返回正确分片，支持 offset/limit 翻页 |

### 多场景排名验证

| 场景 | 查询数 | 排名一致 |
|:----|:------|:--------|
| 自然会话（旅游/养猫/健身） | 3 | ✅ |
| 技术知识（async/索引/K8s） | 3 | ✅ |
| 项目追踪（登录/支付/推送） | 3 | ✅ |
| 跨域模糊（缓存/限流/日志） | 3 | ✅ |

---

## 九、设计决策

| # | 决策 | 结论 |
|:--|:----|:------|
| 1 | tag 数量 | 固定 **5 个**，不足报错（不自动生成） |
| 2 | 评分方式 | **预合并向量**（1 个内积 = 6 个内积的平均值） |
| 3 | 检索层数 | **单层向量检索**，无关键词降级 |
| 4 | 阈值行为 | **0.15**，可配置，低于阈值返回空 |
| 5 | 返回分数 | **真实余弦相似度**（0~1），非二元命中 |
| 6 | top_k 行为 | 默认 **3**，可调大（贪婪模式） |
| 7 | hybrid 模式 | **移除**，只有 human / code 两种模式 |
| 8 | key_moments | **移除**，human_timeline 不再包含此字段 |
| 9 | LSI 降级 | **不提供**，强制依赖 Transformer 模型 |
| 10 | 旧数据迁移 | **不迁移**，直接重建数据库 |
