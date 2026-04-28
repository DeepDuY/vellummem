# VellumMem — Persistent AI Memory via MCP

> **Vellum** — ancient parchment, the original memory medium.
> A context-aware AI persistent memory system built on the MCP (Model Context Protocol).

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## Table of Contents

- [What Is It](#what-is-it)
- [Architecture](#architecture)
- [How It Works](#how-it-works)
- [Quick Start](#quick-start)
- [Environment Variables](#environment-variables)
- [MCP Tool Reference](#mcp-tool-reference)
- [Retrieval Design](#retrieval-design)
- [Performance Benchmarks](#performance-benchmarks)
- [Use Cases](#use-cases)
- [Database Schema](#database-schema)
- [Troubleshooting](#troubleshooting)
- [Design Documents](#design-documents)

---

## What Is It

VellumMem is an **MCP server** that gives AI assistants **persistent, searchable memory** across conversations. It solves a fundamental limitation of LLMs: they start every conversation with a blank slate.

| Domain | What It Remembers | How It's Searched |
|--------|------------------|-------------------|
| **Human Memory** 🧠 | Past conversations, summaries, tags, context chunks | Vector search (semantic, natural language queries) |
| **Project Memory** 💻 | Code repositories, file indexes, architectural decisions, tasks | Keyword / FTS5 (precise, code-aware search) |

**Key differentiators:**

- **Zero external services** — single SQLite file, local on-device model. No vector DB, no cloud API.
- **Pre-merged vector design** — 6× less storage, 4× faster than naive multi-vector approaches, with **mathematically identical** search quality.
- **Dual-mode search** — vector for fuzzy semantic recall, keyword for precise code lookups.
- **MCP-native** — plugs into any MCP-compatible host (DeepChat, Claude Desktop, custom apps).

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                   AI Assistant (Host)                         │
│    memory_init / memory_query / memory_write / ...           │
└────────────────────────┬─────────────────────────────────────┘
                         │ MCP (stdio)
┌────────────────────────▼─────────────────────────────────────┐
│                    VellumMem MCP Server                        │
│                    Python + FastMCP                            │
│                                                               │
│    ┌─────────────────────────┐    ┌─────────────────────────┐ │
│    │      Router              │    │     Session              │ │
│    │  dispatches by mode      │◄──►│  config, mode, project   │ │
│    └────────┬────────┬────────┘    └─────────────────────────┘ │
│             │        │                                          │
│      ┌──────▼──┐ ┌──▼────────┐    ┌─────────────────────────┐ │
│      │  Human  │ │  Project  │    │    Vector Adapter         │ │
│      │  Stores │ │  Stores   │    │  bge-small-zh-v1.5        │ │
│      │         │ │           │    │  pre-merged vectors       │ │
│      │ timeline│ │ projects  │    └─────────────────────────┘ │
│      │ context │ │ file_map  │                                 │
│      │ vectors │ │ decisions │                                 │
│      │         │ │ tasks     │                                 │
│      └────┬────┘ └────┬──────┘                                 │
│           │           │                                         │
└───────────┼───────────┼─────────────────────────────────────────┘
            │           │
      ┌─────▼───────────▼──────┐
      │      SQLite (1 file)    │
      │   vellum.db             │
      └─────────────────────────┘
```

### Component Overview

| Component | File | Responsibility |
|-----------|------|----------------|
| **Server** | `server.py` | MCP entry point, 8 tool definitions, lazy init |
| **Router** | `router.py` | Mode dispatch: `human` → vector search, `code` → keyword search |
| **Session** | `session.py` | Sticky mode/project config, persisted to DB config table |
| **Vector Adapter** | `vector/adapter.py` | Sentence-transformer wrapper, pre-merged vector encode/search |
| **Human Timeline Store** | `stores/human_timeline.py` | CRUD for human memories + context chunking |
| **Project Store** | `stores/projects.py` | Project card management |
| **File Map Store** | `stores/file_map.py` | Code file indexing with symbols and dependencies |
| **Decision Store** | `stores/decisions.py` | Architectural decision log |
| **Task Store** | `stores/tasks.py` | Task tracking with status and blockers |
| **Database** | `db.py` | SQLite connection, schema init, config migration |
| **Entry Point** | `run.py` | CLI runner, sets absolute `VELLUM_DB_PATH` |

---

## How It Works

### Retrieval Flow (Human Mode)

```
memory_query("what did we discuss about vellum's architecture?")

  1. Encode query → bge-small-zh-v1.5 (512-dim vector)
  2. For each memory entry:
       score = query_vector · merged_vector   # 1 dot product
       if score >= score_threshold (default 0.15):
           add to results
  3. Sort by score descending
  4. Return top_k results (default 3)
```

Each memory entry stores **1 pre-merged vector**:

```
merged = (normalize(summary) + normalize(tag0) + ... + normalize(tag4)) / 6
```

This is **mathematically equivalent** to separately scoring summary + 5 tags and averaging the 6 scores, because dot product is linear:

```
score = (q·sv + q·tv₀ + ... + q·tv₄) / 6 = q · (sv + tv₀ + ... + tv₄) / 6 = q · merged
```

### Retrieval Flow (Code Mode)

```
memory_query("auth middleware", mode="code")

  1. FileMapStore.search()    — keyword + FTS5 on path/summary/symbols
  2. DecisionStore.search()   — keyword match on title + body
  3. TaskStore.get_active()   — filter by title keyword
  → Union & return all matches
```

---

## Quick Start

### Prerequisites

- **Python 3.12+**
- **Internet** (first run only — downloads the ~36MB embedding model)

### Installation

```bash
# Clone or navigate to the vellum/ directory
cd path/to/vellum

# Install dependencies
pip install -r requirements.txt

# Optional: install sentence-transformers for vector search
pip install sentence-transformers>=3.0.0
```

### Run

```bash
python run.py
```

The server starts an MCP endpoint over stdio. No web server, no ports — it speaks MCP protocol on stdin/stdout.

### Configure Your Host

Add to your MCP host configuration (e.g., DeepChat, Claude Desktop):

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

### Verify It Works

```python
# In your AI assistant, call:
memory_status()
# Expected:
# {"session": {"mode": "human", ...}, "storage": {"human_timeline": 0, ...}}
```

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `VELLUM_DB_PATH` | ❌ No | `./vellum.db` | Path to the SQLite database file. When run via `run.py`, defaults to an absolute path in the vellum project directory. |
| `VELLUM_TRANSFORMER_MODEL` | ❌ No | `BAAI/bge-small-zh-v1.5` | Sentence-transformer model name or local path. Must produce 512-dim embeddings. Other tested models: `all-MiniLM-L6-v2` (384-dim, English-optimized, lower quality for Chinese). |
| `HF_ENDPOINT` | ❌ No | *(not set)* | HuggingFace mirror endpoint for model downloads in restricted networks. Example: `https://hf-mirror.com`. Auto-set internally on download failure. |

### Database Config Table

The SQLite `config` table also stores persistent settings that survive restarts. These are **not** set via environment variables but through the `memory_set_mode` tool or by the host:

| Config Key | Default | Type | Description | Set Via |
|-----------|---------|------|-------------|---------|
| `mode` | `human` | str | Current retrieval mode: `human` (vector search) or `code` (keyword/FTS5) | `memory_set_mode("code")` |
| `project_id` | `""` | str | Currently bound project ID | `memory_init(project_path=...)` |
| `project_path` | `""` | str | Currently bound project root path | `memory_init(project_path=...)` |
| `vector_engine` | `transformer` | str | Vector search engine (always `transformer`) | *(internal)* |
| `score_threshold` | `0.15` | float | Minimum vector similarity score for results | `memory_query(score_threshold=0.2)` |

---

## MCP Tool Reference

### `memory_init`

Initialize (or reset) the session context. Lazy-init on first call: creates DB tables, loads model, sets up stores.

```
Args:
    project_path (str | None): Optional project root path.
                               If provided, creates/binds a project card and scans files.

Returns:
    {"message": "VellumMem ready", "mode": str, "project": str}
```

### `memory_query`

Search memory using natural language.

```
Args:
    query (str):              Natural language query text (required)
    mode (str | None):        "human" or "code". Defaults to session mode.
    top_k (int):              Max results (default 3). Set high for "greedy mode".
    score_threshold (float):  Min score for human mode (default 0.15). Below this = empty.

Returns:
    {"mode": str, "results": [
        {"source_domain": "human", "source_table": "human_timeline",
         "source_id": "...", "summary": "...", "score": 0.55,
         "has_context": true, "total_chunks": 3, "tags": [...]}
    ]}
```

| Parameter | Effect |
|-----------|--------|
| `top_k=1` | Get the single best match |
| `top_k=10` | Greedy mode — cast a wide net |
| `score_threshold=0.3` | Only return high-confidence matches |
| `score_threshold=0.0` | Return everything, no filter |
| `mode="code"` | Search project memory (requires bound project) |

### `memory_get_context`

Retrieve original conversation context chunks for a memory entry.

```
Args:
    timeline_id (str):  Target human_timeline entry ID
    limit (int):        Max chunks to return (default 1)
    offset (int):       Offset from newest (0 = newest chunk)

Returns:
    [{"id": "...", "context": "...", "chunk_index": 0, "create_timestamp": ...}]
```

Chunks are returned newest-first. To paginate through all context:
1. Call `memory_query` → get `total_chunks` and `source_id`
2. Call `memory_get_context(timeline_id=source_id, limit=total_chunks, offset=0)`

### `memory_set_mode`

Switch between human and code search modes. Persisted to DB — survives restarts.

```
Args:
    mode (str): "human" or "code"

Returns:
    {"message": "...", "mode": str}
```

### `memory_write`

Store a memory entry with summary, tags, and optional context.

```
Args:
    data (str): JSON string with fields:
        - summary (str):      Session summary (max 200 chars)
        - tags (list[str]):   5 tags (REQUIRED — fewer or more will be rejected)
        - context_text (str): Initial conversation context (auto-chunked, max ~8000 chars per chunk)

Returns:
    {"message": "Memory stored", "written": [...], "id": str}
```

Tags validation:
- Exactly **5 tags** required (not 4, not 6)
- Tags help the semantic vector search
- Reasonable tag: "architecture", "bug-fix", "frontend", "database", "discussion"

Context chunking:
- Context text is auto-split at natural boundaries (`##`, `-`, `\n\n`, etc.)
- Each chunk ≤ 8000 characters
- Chunks are linked to the parent entry via `conversation_context_link`

### `memory_write_context`

Append more context chunks to an existing timeline entry.

```
Args:
    timeline_id (str):    Target entry ID
    context_text (str):   Additional conversation context (auto-chunked)

Returns:
    {"id": str, "new_context_ids": [str, ...]}
```

### `memory_project_sync`

Scan and index project files. Supports Python, TypeScript, JavaScript, Rust, Go.

```
Args:
    path (str | None):  Project path. Uses session-bound project if omitted.

Returns:
    {"message": "...", "files_scanned": int, "files_indexed": int}
```

### `memory_status`

Check system health and statistics.

```
Args:
    (none)

Returns:
    {"session": {"mode": str, "project_id": str, "project_path": str},
     "storage": {"human_timeline": int, "conversation_context": int, ...}}
```

---

## Performance Benchmarks

Measured on a typical laptop CPU (no GPU) with 1,000 memory entries:

| Metric | Spec |
|--------|------|
| **Query time** | ~38 ms (1,000 entries) |
| **Storage per entry** | 1 vector (1 SQLite row) |
| **Model** | bge-small-zh-v1.5 (512-dim, Chinese-optimized) |
| **Precision** | True 0~1 cosine similarity |

### Query Breakdown (1,000 entries)

| Step | Time |
|------|------|
| Query encode | ~20 ms |
| SQLite read 1,000 BLOBs | ~5 ms |
| Pickle deserialization | ~10 ms |
| 1,000 dot products | ~2 ms |
| Sort | <1 ms |
| **Total** | **~38 ms** |

### Model Comparison

| Model | Dims | Chinese Quality | English Quality | Size |
|-------|------|-----------------|-----------------|------|
| `BAAI/bge-small-zh-v1.5` | 512 | ✅ Excellent | ⚠️ Fair | ~36 MB |
| `all-MiniLM-L6-v2` | 384 | ⚠️ Poor | ✅ Excellent | ~23 MB |
| `BAAI/bge-large-zh-v1.5` | 1024 | ✅ Best | ⚠️ Fair | ~120 MB |

---

## Use Cases

### 1. Long-running Project Conversations

```python
# Session 1: "Let's design the auth module..."
memory_write(data={
    "summary": "Auth module design — JWT + OAuth2 flow",
    "tags": ["architecture", "auth", "jwt", "oauth", "design"],
    "context_text": "We decided to use JWT with 15min expiry..."
})

# Session 2 (next day): "What did we decide about auth?"
results = memory_query("auth module design decision")
# → Returns the previous session with score ~0.52
```

### 2. Codebase Familiarization

```python
# Index a project
memory_init(project_path="/home/user/my-project")
memory_project_sync()

# Ask about code
memory_query("where is the database connection configured", mode="code")
# → Returns relevant file_map entries
```

### 3. Decision Log

```python
# Record a decision
memory_write(data={
    "summary": "Chose PostgreSQL over MongoDB for order service",
    "tags": ["decision", "database", "postgresql", "order", "architecture"],
    "context_text": "Why PostgreSQL: 1) Complex joins needed 2) ACID compliance..."
})

# Later: "Why did we pick PostgreSQL?"
result = memory_query("why postgresql not mongodb")
```

---

## Database Schema

### `human_timeline`
| Column | Type | Description |
|--------|------|-------------|
| `id` | TEXT PK | `YYYYMMDD_HHMMSS_5RAND` |
| `session_start` | TEXT | ISO datetime |
| `session_end` | TEXT | ISO datetime |
| `summary` | TEXT | ≤200 chars |
| `tags` | TEXT | JSON array, exactly 5 |
| `conversation_context_link` | TEXT | JSON array of chunk IDs |
| `create_timestamp` | INTEGER | Unix ms |
| `update_timestamp` | INTEGER | Unix ms |

### `entry_vectors`
| Column | Type | Description |
|--------|------|-------------|
| `entry_id` | TEXT PK → human_timeline(id) ON DELETE CASCADE |
| `merged_blob` | BLOB | `pickle.dumps(np.ndarray(float32, 512))` |

### `conversation_context`
| Column | Type | Description |
|--------|------|-------------|
| `id` | TEXT PK | |
| `timeline_id` | TEXT FK → human_timeline(id) ON DELETE CASCADE |
| `context` | TEXT | Chunk content (≤8000 chars) |
| `chunk_index` | INTEGER | Order within entry |
| `create_timestamp` | INTEGER | Unix ms |

### `config`
| Column | Type | Description |
|--------|------|-------------|
| `key` | TEXT PK | Config key name |
| `value` | TEXT | String value (parsed per `type`) |
| `type` | TEXT | `str`, `float`, `int`, `bool` |
| `description` | TEXT | Human-readable description |
| `created_at` | TEXT | ISO datetime |
| `updated_at` | TEXT | ISO datetime |

### `projects`, `file_map`, `decisions`, `tasks`
See individual store files for schemas. These are used in `code` mode only.

---

## Troubleshooting

### "VellumMem failed to initialize"

Check the server stderr logs. Common causes:

| Symptom | Likely Cause | Solution |
|---------|-------------|----------|
| `ModuleNotFoundError: sentence_transformers` | Missing optional dependency | `pip install sentence-transformers>=3.0.0` |
| Download hangs > 2 min | Network blocked from HuggingFace | Set `HF_ENDPOINT=https://hf-mirror.com` |
| `sqlite3.OperationalError: no such table` | Corrupted DB or schema mismatch | Delete `vellum.db` and restart |
| `memory_write` returns tag error | Wrong number of tags | Provide exactly 5 tags |

### Model Download Issues

VellumMem tries `local_files_only=True` first, then falls back to downloading. If you're offline:

```bash
# Download the model in advance
python -c "
from sentence_transformers import SentenceTransformer
SentenceTransformer('BAAI/bge-small-zh-v1.5')
"
```

### Database Location

By default `vellum.db` is created in the project root. Override with:

```bash
set VELLUM_DB_PATH=C:/my/custom/path/vellum.db
python run.py
```

Or set in the MCP host config under `env`.

---

## Design Documents

- `design/retrieval-redesign.md` — Retrieval architecture design document
- `design/architecture.md` — System architecture document

---

## Tech Stack

- **Runtime**: Python 3.12+
- **Framework**: FastMCP (MCP protocol over stdio)
- **Vector Engine**: sentence-transformers (`BAAI/bge-small-zh-v1.5`, 512-dim)
- **Storage**: SQLite (single file, WAL mode, foreign keys)
- **Dependencies**: ~10 lightweight packages (see `requirements.txt`)
