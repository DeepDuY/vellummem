# VellumMem — Persistent AI Memory via MCP

> **Vellum** — ancient parchment, the original memory medium.
> A context-aware AI persistent memory system built on the MCP (Model Context Protocol).

---

## What It Is

VellumMem is an MCP server that gives AI assistants persistent, searchable memory.

- **Human Memory** — remembers past conversations ("what did we discuss about X?")
- **Project Memory** — indexes code repositories, decisions, and tasks ("where is the auth module?")

Both domains are searchable via natural language queries.

---

## Architecture

```
┌────────────────────────────────────────────────────┐
│              AI Assistant (DeepChat)                │
│  memory_init / memory_query / memory_write / ...   │
└──────────────────────┬─────────────────────────────┘
                       │ MCP (stdio)
┌──────────────────────▼─────────────────────────────┐
│              VellumMem MCP Server                   │
│              (Python + FastMCP)                     │
│                                                     │
│  mode="human" → vector search (pre-merged)         │
│  mode="code"  → keyword / FTS5 search              │
└──────┬──────────────────────────────────┬──────────┘
       │                                  │
┌──────▼──────────┐            ┌──────────▼──────┐
│  Human Memory    │            │  Project Memory  │
│                  │            │                  │
│  human_timeline  │            │  projects        │
│  conversation_   │            │  file_map        │
│    context       │            │  decisions       │
│  entry_vectors   │            │  tasks           │
└─────────────────┘            └─────────────────┘
```

### Human Domain

| Table | Purpose |
|-------|---------|
| `human_timeline` | One entry per session — summary (≤200 chars) + 5 tags |
| `conversation_context` | Context chunks, auto-split at natural boundaries (≤8000 chars each) |
| `entry_vectors` | Pre-merged embedding vectors (512-dim, 1 vector per entry) |

### Project Domain

| Table | Purpose |
|-------|---------|
| `projects` | Project cards (name, path, tech stack) |
| `file_map` | File index with symbols, dependencies, change history |
| `decisions` | Decision log with rationale, alternatives, affected files |
| `tasks` | Task tracking with status, blockers, progress |

---

## Retrieval

### Human Search (Single-Layer Vector)

```
memory_query("vellummem的开发进度")
→ encode query → BAAI/bge-small-zh-v1.5 (512-dim)
→ 1 dot product per entry (pre-merged vector)
→ filter by score_threshold (default 0.15)
→ sort by score descending
→ return top_k (default 3)
```

Each entry stores **1 pre-merged vector** = `(normalize(summary) + normalize(tag0) + ... + normalize(tag4)) / 6`. Mathematically equivalent to separate 6-vector scoring, but 6x faster.

**Key parameters:**
- `top_k` — result count (default 3; set higher for "greedy mode")
- `score_threshold` — minimum score (default 0.15; below this returns empty)

### Code Search (Keyword / FTS5)

```
memory_query("auth middleware", mode="code")
→ FileMapStore.search() — keyword + FTS5 on path/summary/symbols
→ DecisionStore.search() — keyword match on title + body
→ TaskStore.get_active() — filter by title keyword
```

---

## Quick Start

```bash
pip install -r requirements.txt
python run.py
```

The server starts an MCP endpoint over stdio. Configure DeepChat to launch it:

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

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VELLUM_DB_PATH` | `./vellum.db` | SQLite database path |
| `VELLUM_TRANSFORMER_MODEL` | `BAAI/bge-small-zh-v1.5` | Sentence transformer model |

---

## MCP Tools

| Tool | Purpose |
|------|---------|
| `memory_init` | Initialize session (optional: bind project) |
| `memory_query` | Search memory by natural language |
| `memory_get_context` | Retrieve conversation context chunks (newest first) |
| `memory_set_mode` | Switch between human / code search modes |
| `memory_write` | Store a memory entry (tags: 5 required) |
| `memory_write_context` | Append context to an existing entry |
| `memory_project_sync` | Scan and index project files |
| `memory_status` | Check system health and statistics |

---

## Design Files

- `design/architecture.md` — Original v4 architecture document
- `design/retrieval-redesign.md` — v5 retrieval redesign (current)

---

## Tech Stack

- **Runtime**: Python 3.12+
- **Framework**: FastMCP
- **Vector Engine**: sentence-transformers (BAAI/bge-small-zh-v1.5, 512-dim)
- **Storage**: SQLite (single file)
- **Dependencies**: ~10 packages (see requirements.txt)
