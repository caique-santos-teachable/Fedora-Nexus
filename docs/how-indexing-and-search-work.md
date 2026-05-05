# How Indexing and Search Work

This document explains what happens under the hood when you run
`fedora-nexus index` and `fedora-nexus search`.

---

## Indexing

### 1. Parsing with tree-sitter

When you run `fedora-nexus index .`, the server walks every `.py`, `.ts`,
`.js`, and `.rb` file in the repository and parses each one into an
**Abstract Syntax Tree (AST)** using [tree-sitter](https://tree-sitter.github.io/tree-sitter/) —
the same underlying library that editors use for syntax highlighting and
code navigation.

From each AST, the indexer extracts two things:

**Import/dependency edges** — which files does this file import?

```python
# src/store/kuzu_store.py imports:
from fedora_nexus.graph.engine import DependencyGraph
```

**Symbols** (when `--symbols` is active) — functions, classes, and methods
defined in the file:

```python
class KuzuGraphStore:           # → Class node
    def save_graph(self, ...):  # → Method node
    def load_graph(self, ...):  # → Method node
```

### 2. Building the graph

The parsed data is assembled into a **directed graph** (a `networkx.DiGraph`
in memory):

- **Nodes** represent files, classes, functions, and methods.
- **Edges** represent relationships between them.

There are three edge types:

| Type | Meaning | Example |
|------|---------|---------|
| `DEPENDS_ON` | file A imports file B | `server.py → kuzu_store.py` |
| `CONTAINS` | file/class contains a symbol | `kuzu_store.py → KuzuGraphStore` |
| `CALLS` | symbol A calls symbol B | `_tool_index_repo → save_graph` |

### 3. Persisting to KuzuDB

The in-memory graph is saved to **KuzuDB** — an embedded graph database
(no separate server process, similar to SQLite but designed for graphs).
Data lives at `/data/fedora-nexus.db` inside the container.

KuzuDB supports **Cypher** queries — a query language for graphs, analogous
to SQL but built around nodes and relationships:

```cypher
-- "who imports kuzu_store.py?"
MATCH (a:File)-[:DEPENDS_ON]->(b:File {path: "src/.../kuzu_store.py"})
RETURN a.path
```

This is what powers the `fedora-nexus query` command and the MCP
`query_graph` tool.

### 4. Generating embeddings

In parallel with the graph, every indexed file and symbol is also converted
into a **384-dimensional numeric vector** by
[fastembed](https://github.com/qdrant/fastembed) using the
`BAAI/bge-small-en-v1.5` model — running entirely locally, no external API.

Think of it this way: the model reads the path and content of a file and
produces an array of 384 numbers. Files with similar content end up with
mathematically similar vectors.

```
"kuzu_store.py — saves and loads graphs"      →  [0.12, -0.87, 0.34, ...]
"embedding_store.py — stores vectors"         →  [0.09, -0.81, 0.41, ...]  close!
"cli.py — command-line interface"             →  [0.73,  0.21, -0.55, ...]  far
```

The vectors are stored as compressed NumPy arrays (`.npz` files) in
`/data/embeddings/` inside the container. The filename is a hash of the
repository path, so each repo gets its own embedding index.

---

## Search

When you run `fedora-nexus search . "function that loads the graph"`, the
server runs **two algorithms in parallel** and merges the results.

### Algorithm 1 — BM25 (keyword search)

BM25 is the ranking algorithm behind search engines like Elasticsearch. It
scores documents by counting how often the query's words appear in them,
normalized by document length.

- Fast, no model required
- Great for exact function or class names
- Does not understand synonyms or intent

### Algorithm 2 — Semantic search (embedding similarity)

The query `"function that loads the graph"` is also converted into a
384-dimensional vector by the same fastembed model. The server then computes
the **cosine similarity** between that vector and every stored vector.

Cosine similarity measures the angle between two vectors. The smaller the
angle (the more parallel they are), the more similar the content:

```
cos(query_vector, load_graph_vector)  = 0.92  → very similar ✓
cos(query_vector, save_graph_vector)  = 0.71  → moderately similar
cos(query_vector, cli_main_vector)    = 0.23  → not similar
```

### Merging with RRF (Reciprocal Rank Fusion)

Each algorithm produces a ranked list. RRF combines both lists into a single
ranking using this formula:

$$score(d) = \sum_{i} \frac{1}{k + rank_i(d)}$$

Where `k = 60` is a smoothing constant and $rank_i(d)$ is the position of
document `d` in list `i`. Documents that rank well in **both** lists
receive a high final score.

```
BM25 ranking:      load_graph (#1), save_graph (#3), require_graph (#5)
Semantic ranking:  load_graph (#1), require_graph (#2), save_graph (#4)

RRF final:         load_graph (#1)   ← top in both → high score
                   save_graph (#2)
                   require_graph (#3)
```

### Why hybrid?

Neither algorithm is complete on its own:

- **BM25 alone**: the query `"function that loads the graph"` won't match
  anything if the actual function is named `load_graph` — the word "loads"
  doesn't appear in the code.
- **Semantic alone**: an exact query like `"KuzuGraphStore.save_graph"` may
  not produce strong results if the model's representation of that specific
  name doesn't capture its meaning well.
- **Hybrid**: each algorithm compensates for the other's blind spot.

---

## Data flow summary

```
fedora-nexus index .
        │
        ▼
  tree-sitter parses files
        │
        ├── imports + call sites ──► DependencyGraph (networkx)
        │                                    │
        │                                    ▼
        │                              KuzuDB (.db file)
        │                         (queryable via Cypher)
        │
        └── file paths + content ──► fastembed model
                                            │
                                            ▼
                                     .npz vector files
                                  (384 dims per document)


fedora-nexus search . "query"
        │
        ├── BM25 over stored paths/content ──────────┐
        │                                             ▼
        └── embed query → cosine similarity ──► RRF merge → ranked results
```
