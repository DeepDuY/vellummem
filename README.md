# VellumMem — Persistent AI Memory via MCP

> **Vellum** — ancient parchment, the original memory medium.
> A persistent AI memory system built on the MCP (Model Context Protocol).

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## What Is It

VellumMem is an MCP server that gives AI assistants **persistent, searchable memory** across conversations — solving the fundamental limitation of starting every conversation from scratch.

| Capability | How It Works |
|-----------|--------------|
| **Human Memory** 🧠 | Store conversation summaries + tags + full context; search via semantic vectors |
| **Memory Grouping** | Automatic grouping of related memories via CPM (k=3) |
| **Pre-merged Vector** | 1 vector per entry (vs 6), mathematically identical to multi-vector scoring |

**Key differentiators:**
- **Zero external services** — single SQLite file, local on-device model. No vector DB, no cloud API.
- **Pre-merged vector design** — 6× less storage, 4× faster, mathematically identical search quality.
- **MCP-native** — plugs into any MCP host (DeepChat, Claude Desktop, custom apps).

---

## Quick Start

### Requirements
- Python 3.12+

### Install

```bash
cd vellum
pip install -r requirements.txt
pip install sentence-transformers   # strongly recommended
```

### Run

```bash
python run.py
```

### Test

```bash
pytest tests/ -v
```

---

## MCP Tool Reference

### Memory Write

```
memory_write(data: str) -> str
```
- `summary` (required, ≤200 chars)
- `tags` (required, exactly 5)
- `context_text` (optional)
- `category` (required: `conversation`, `knowledge`, `document`, `preference`, `other`)
- `is_time_sensitive` (optional)

### Memory Query

```
memory_query(query, top_k=3, score_threshold=0.15) -> str
```
Returns entries sorted by cosine similarity (real 0–1 score). Each result includes `create_timestamp`.

### Context Management

```
memory_get_context(timeline_id, offset=0, limit=1) -> str
memory_write_context(timeline_id, context_text) -> str
```
Auto-chunked at natural boundaries (headings, code blocks, lists, paragraphs), max 8K chars/chunk.

### Memory Groups

```
memory_get_groups(entry_id) -> str
memory_get_group_members(group_id) -> str
memory_rebuild_groups(threshold=0.8) -> str
```

### Status

```
memory_init() -> str
memory_status() -> str
```

All tools return JSON errors on failure (not raw tracebacks), thanks to the `@_tool` decorator.

---

## Architecture

```
┌──────────────────────────────────────────────┐
│           AI Assistant (Host)                 │
│  memory_write / memory_query / ...            │
└─────────────────────┬────────────────────────┘
                      │ MCP (stdio)
┌─────────────────────▼────────────────────────┐
│            VellumMem MCP Server                │
│                                                │
│  ┌──────────────┐  ┌──────────────────────┐   │
│  │ 9 MCP tools  │  │  Thread-safe lazy    │   │
│  │ @_tool       │  │  init (double-check) │   │
│  └──────┬───────┘  └──────────────────────┘   │
│         │                                      │
│  ┌──────▼─────────────────────────────────┐   │
│  │  Stores + Groups + Vector Adapter       │   │
│  │                                         │   │
│  │  human_timeline.py  — CRUD + chunking   │   │
│  │  groups.py          — CPM grouping      │   │
│  │  vector/adapter.py  — pre-merged search │   │
│  │  db.py              — SQLite + init     │   │
│  │  errors.py          — exception types   │   │
│  └─────────────────────────────────────────┘   │
└──────────────────────┬─────────────────────────┘
                       │
              ┌────────▼────────┐
              │   SQLite (1 file)│
              │   vellum.db      │
              └─────────────────┘
```

| Component | File | Responsibility |
|-----------|------|----------------|
| Server | `server.py` | MCP entry, 9 tools, `@_tool` decorator |
| Database | `db.py` | SQLite wrapper, schema init |
| Human Timeline | `stores/human_timeline.py` | Memory CRUD + context chunking |
| Group Manager | `groups.py` | CPM k=3 memory grouping |
| Vector Adapter | `vector/adapter.py` | Sentence-transformers, pre-merged vectors |
| Exceptions | `errors.py` | VellumMemError hierarchy |

---

## Retrieval Design

### Pre-Merged Vector

Each entry stores **1 merged vector** instead of 6 separate ones:

```
score = (q·s + q·t₀ + ... + q·t₄) / 6  =  q · (s + t₀ + ... + t₄) / 6
```

**Mathematically identical** to multi-vector (verified with 10,000 random tests, max error 1.06e-08).

| Metric | Naive (6 vectors) | Pre-Merged |
|--------|------------------|------------|
| Storage | 6000 rows / 1K entries | **1000 rows** |
| Dot products | 6000 | **1000** |
| Query time (1K) | ~147ms | **~38ms** |

### Memory Grouping (CPM k=3)

1. Pairwise cosine similarity ≥ threshold → edges
2. Find all triangles (3-cliques)
3. Triangles sharing an edge → same community
4. Connected components → groups

---

## Project Structure

```
vellum/
├── __init__.py               # version
├── server.py                 # MCP entry + 9 tools + @_tool
├── db.py                     # SQLite + init
├── errors.py                 # exception hierarchy
├── groups.py                 # CPM k=3 grouping
├── stores/
│   ├── __init__.py
│   └── human_timeline.py     # memory CRUD + chunking
└── vector/
    ├── __init__.py
    └── adapter.py            # transformer wrapper + pre-merged vectors
schemas/
└── schema.sql
tests/
├── __init__.py
├── test_errors.py            # 6 tests
└── test_stores.py            # 13 tests
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VELLUM_DB_PATH` | `vellum/vellum.db` | Absolute path to SQLite file |
| `VELLUM_TRANSFORMER_MODEL` | `BAAI/bge-small-zh-v1.5` | Sentence-transformer model name |

---

## License

MIT
