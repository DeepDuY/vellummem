# VellumMem — Persistent Memory for AI

> **Vellum** — ancient parchment, the original memory medium.
> A context-aware AI memory system built on the MCP (Model Context Protocol).

---

## Design Philosophy

### The Core Tension

```
Memory must be "storable and retrievable"
  Store too fine → retrieval slows down
  Store too coarse → find it but lose the details
```

### Design Principles

| Principle | Description |
|-----------|-------------|
| **Progressive cost** | Start cheap (keyword), escalate only when needed (semantic vector) |
| **Progressive depth** | Start at depth=1 (timeline), dig to depth=4 (reflections) on demand |
| **Full search by default** | `hybrid` mode searches both domains — AI narrows down only when needed |
| **AI-driven mode** | Mode switching is explicit via `memory_set_mode()`, no guesswork |
| **Structure first** | Entity/pattern matching beats vector search when it works |
| **Audit trail** | Every fact traces back to its source conversation |
| **Degradable** | Works without any vector search at all |
| **Zero external deps** | Single SQLite file holds everything — no database server |

---

## Architecture

### Dual-Domain Memory System

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
│  Human Memory    │  │  Project Memory  │  │  Decision Hub     │
│  Domain          │  │  Domain          │  │  (link layer)     │
│                  │  │                  │  │                   │
│  timeline        │  │  projects        │  │  timeline ↔       │
│  semantic        │  │  file_map        │  │  decision ↔       │
│  patterns        │  │  decisions       │  │  file_map          │
│  reflections     │  │  tasks           │  │                   │
└──────────────────┘  └──────────────────┘  └──────────────────┘
```

### Human Memory Domain

Five tables storing conversation-derived information:

| Table | Tier | Type | Purpose |
|-------|:----:|------|---------|
| **timeline** | L1 | Append-only | One summary per session, with key_moments and tags |
| **semantic_entities** | L2 | Updatable | Entity registry ("Python", "JWT") with aliases and importance |
| **semantic_facts** | L2 | Versioned | Entity relations ("Python → migrated_to → Go") with evidence chain |
| **patterns** | L3 | Progressive | Cross-session behavioral patterns discovered over time |
| **reflections** | L4 | Compressed | Deep cross-session insights (rarely updated) |

### Project Memory Domain

Four tables for project-related information:

| Table | Purpose |
|-------|---------|
| **projects** | Project cards with path, name, description |
| **file_map** | File index by module/path/function, version-tracked |
| **decisions** | Decision log — rationale, alternatives, affected files |
| **tasks** | Task tracking — status, blockers, progress detail |

### Decision Hub

Cross-domain linking via a lightweight join table:

```
human domain               code domain
    │                          │
    │   timeline ── Hub ── decision
    │   semantic  ── Hub ── file_map
    │                          │
```

When the AI asks "why did we use JWT", hybrid mode:
1. Searches timeline → finds "discussed auth approach"
2. Cross-links via Hub → finds decision "JWT over Session"
3. Cross-links via decision → finds file_map "auth/middleware.ts"

---

## Four-Level Memory Depth

Progressive retrieval depth for human memory:

| Level | Source | Content | Characteristics |
|:-----:|--------|---------|-----------------|
| **L1** | timeline | Raw conversation summaries | Fastest, most recent |
| **L2** | semantic | Entity/relation facts | Cross-session context |
| **L3** | patterns | Behavioral patterns | AI-pushed, topic-triggered |
| **L4** | reflections | Deep insights | Highest compression |

Retrieval flow — start shallow, dig deeper on demand:

```
memory_query(query, depth=1)  → timeline only (fastest)
  ↓ not enough
memory_query(query, depth=2)  → + semantic facts
  ↓ still need more
memory_query(query, depth=3)  → + behavioral patterns
  ↓ give me everything
memory_query(query, depth=4)  → + deep insights
```

---

## Three Search Modes

| Mode | Scope | Typical Use |
|------|-------|-------------|
| **hybrid** (default) | Human + Project + Hub cross-links | **Most cases — no thinking required** |
| human | Human domain only (timeline + semantic + patterns + reflections) | Pure recall / chit-chat |
| code | Project domain only (file_map + decisions + tasks) | Pure coding |

Mode is **sticky** — once set, it persists until explicitly changed:

```
memory_init()                        → mode = hybrid
memory_query("auth module location") → hybrid, both domains
memory_set_mode("code")              → AI realizes it only needs code
memory_query("middleware.ts")        → code, project only
memory_query("why JWT?", "hybrid")   → temporary override, mode stays code
```

---

## Search Pipeline (Degradation Chain)

```
query → LIKE exact match → token extraction → LSI semantic → [Transformer] (optional)
        fastest               slower          offline DL
```

VellumMem auto-selects the best available vector engine:

```
sentence-transformers installed? → Transformer (384-dim native embeddings)
          ↓ no
        LSI (scikit-learn TruncatedSVD, zero download)
```

Force LSI with `VELLUM_FORCE_VECTOR=LSI`.

---

## MCP Tools

| Tool | Parameters | Purpose |
|------|-----------|---------|
| `memory_init` | `project_path?` | Initialize memory system, optional project path |
| `memory_query` | `query`, `mode?`, `depth?` | Search memory with progressive depth |
| `memory_set_mode` | `mode` | Switch to human/code mode |
| `memory_write` | `data`, `mode?` | Save session data (must call before session ends) |
| `memory_project_sync` | `path?` | Scan project directory, update file index |
| `memory_status` | none | Show current mode, project, storage stats |

### Minimal Usage Flow

```
# 1. Session start
memory_init()

# 2. Search
memory_query(query="project overview")     # default: hybrid + full depth
memory_query(query="JWT", depth=1)         # timeline only, escalate if needed

# 3. Write (before session ends)
memory_write(data={
    "summary": "Discussed auth approach, decided on JWT over Session",
    "decisions": [{"title": "JWT", "body": "Stateless fits desktop better"}],
    "tags": ["auth", "decision", "JWT"]
})
```

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start MCP Server
python run.py

# 3. Optional: upgrade to Transformer engine
pip install sentence-transformers  # auto-detected after install
```

### DeepChat MCP Config

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

Custom database path and engine:

```json
"env": {
  "VELLUM_DB_PATH": "/path/to/vellum.db",
  "VELLUM_FORCE_VECTOR": "LSI",
  "VELLUM_TRANSFORMER_MODEL": "all-MiniLM-L6-v2",
  "HF_ENDPOINT": "https://hf-mirror.com"
}
```

---

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `VELLUM_DB_PATH` | Database file path | `./vellum.db` |
| `VELLUM_FORCE_VECTOR` | Force LSI (skip Transformer detection) | empty (auto-detect) |
| `VELLUM_TRANSFORMER_MODEL` | Custom Transformer model name | `all-MiniLM-L6-v2` |
| `HF_ENDPOINT` | Hugging Face mirror URL | empty (official source) |

---

## Vector Engine Comparison

| Dimension | LSI (scikit-learn) | Transformer (sentence-transformers) |
|-----------|-------------------|-------------------------------------|
| Dependency | `scikit-learn` (required) | `sentence-transformers` (optional) |
| Model | None | `all-MiniLM-L6-v2` (~80MB, auto-cached) |
| Vector dims | ~50 (SVD-reduced) | **384** (native) |
| Short text | Weak | Strong |
| Network | Offline | First download needed |
| Speed | Fast | Fast (inference optimized) |

---

## Project Structure

```
vellum/ (repository root)
├── run.py                   # MCP Server entry point
├── schema.sql               # Database schema (12 tables)
├── requirements.txt         # Dependencies
├── .gitignore
├── design/
│   ├── architecture.md       # Full architecture document
│   └── devlog.md             # Development log
└── vellum/ (Python package)
    ├── server.py             # MCP Server, 6 tools
    ├── router.py             # Mode routing + multi-strategy search + depth
    ├── db.py                 # SQLite connection management
    ├── session.py            # Session state (mode sticky)
    ├── hub.py                # Decision Hub cross-domain linking
    ├── stores/
    │   ├── timeline.py       # L1: raw conversation records
    │   ├── semantic.py       # L2: entity/relation facts
    │   ├── patterns.py       # L3: behavioral patterns
    │   ├── reflections.py    # L4: deep insights
    │   ├── decisions.py      # decision log
    │   ├── tasks.py          # task tracking
    │   ├── projects.py       # project cards
    │   └── file_map.py       # file index
    └── vector/
        └── adapter.py        # TransformerAdapter + VectorAdapter (LSI)
```

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| **Default hybrid mode** | AI doesn't need to choose upfront — `memory_init()` takes no mode param |
| **Progressive depth** | Start shallow (depth=1), escalate on demand — no wasted compute |
| **AI-driven mode switching** | No rule engine — AI calls `memory_set_mode()` explicitly |
| **Sticky mode** | Once set, persists — less redundant calls |
| **Single SQLite file** | Zero ops — one file holds everything |
| **LSI fallback + Transformer optional** | Works offline; upgrades automatically when available |
| **Multi-strategy search** | LIKE → token → LSI → Transformer, degrade gracefully |
