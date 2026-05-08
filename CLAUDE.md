# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

`fedora-nexus` is a dependency graph MCP server for AI agents. It indexes source code (Python, TypeScript, JavaScript, Ruby) using tree-sitter and exposes tools for dependency analysis, hybrid symbol search, and Cypher graph queries. The graph data is stored in a Kuzu embedded graph database.

## Development commands

```bash
# Install Python package in editable mode with dev deps
pip install -e ".[dev]"

# Run all tests
python -m pytest tests/ -q

# Run a single test file
python -m pytest tests/test_graph.py -q

# Run only non-integration tests (no running server or DB required)
python -m pytest tests/ -q -m "not integration"

# Start the server locally via Docker
docker compose up -d mcp-server

# View server logs
docker compose logs -f mcp-server

# Build Go CLI (from cli/ directory)
cd cli && go build -o fedora-nexus .
```

## Architecture

There are two separate CLI implementations that talk to the same server:

- **Python CLI** (`src/fedora_nexus/cli.py`): thin HTTP client using stdlib `urllib`. Auto-detects the server at `localhost:7832`; falls back to local in-process mode if the server is unreachable. This is the packaged `fedora-nexus` entry point.
- **Go CLI** (`cli/`): Cobra + Bubble Tea TUI alternative. Built separately; connects to the same server. Only HTTP mode — no local fallback.

The Python server stack:

```
mcp/server.py          ← MCP tool handlers + HTTP/SSE transport
  graph/engine.py      ← In-memory DependencyGraph (networkx DiGraph)
  graph/blast_radius.py← BFS over reverse edges
  indexer/
    tree_sitter_indexer.py ← Parses files via tree-sitter, extracts imports + symbols
  store/
    kuzu_store.py      ← Persists graph to Kuzu DB; translates host↔container paths
    embedding_store.py ← fastembed (BAAI/bge-small-en-v1.5) for semantic search
  query/cypher.py      ← Validates + executes native Cypher against Kuzu
```

Indexing writes the in-memory `DependencyGraph` into `KuzuGraphStore`. Queries go directly to Kuzu via native Cypher. Search uses BM25 + semantic RRF fusion over stored embeddings.

## Graph schema

Node tables: `File`, `Function`, `Class`, `Method`  
All relationships use a single edge table `CodeRelation` with a `type` property:

| `type` | Connects | Meaning |
|--------|----------|---------|
| `DEPENDS_ON` | File → File | import/require |
| `CONTAINS` | File/Class → Symbol | symbol defined in file |
| `CALLS` | Symbol → Symbol | call sites (best-effort) |

Symbol nodes have IDs in the format `{rel_path}#{kind}:{qualified_name}` (e.g. `src/auth.py#method:User.save`).

## Environment variables

| Variable | Default | Notes |
|----------|---------|-------|
| `FEDORA_NEXUS_DB_PATH` | `/data/fedora-nexus.db` | Kuzu DB path inside container |
| `HOST_REPOS_PREFIX` | *(empty)* | Host path prefix stripped when translating paths — must match the volume mount source in `docker-compose.yml` |
| `CONTAINER_REPOS_PATH` | `/repos` | Container mount point for repos |
| `FEDORA_NEXUS_HTTP_PORT` | `7832` | HTTP server port |
| `FEDORA_NEXUS_SERVER_URL` | *(auto)* | Force a specific server URL in CLI |

`HOST_REPOS_PREFIX` and `CONTAINER_REPOS_PATH` are critical: the store translates absolute host paths (passed by the user) to container paths before writing to the DB, and back on reads. Misconfiguration causes "repo not found" errors.

## Using fedora-nexus on this repo

See `skills/CLAUDE.md` for MCP tool usage patterns (blast radius, dependency traversal, Cypher queries) when working on any indexed codebase.
