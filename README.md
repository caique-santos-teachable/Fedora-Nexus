# fedora-nexus

Dependency graph MCP server for AI agents. Indexes source code (Python, TypeScript, JavaScript, Ruby) and exposes tools for dependency analysis, hybrid symbol search, and Cypher graph queries.

---

## Quick start

```bash
# 1. Install the Go CLI and configure your agent
bash setup.sh

# 2. Start the server
docker compose up -d mcp-server

# 3. Index a repo
fedora-nexus index /path/to/your/repo
```

The server listens at `http://localhost:7832/sse`. **The CLI requires the server to be running** — there is no local fallback mode.

All data (graph DB + embeddings) lives inside the `fedora-nexus-data` Docker managed volume — nothing is written to the host filesystem.

### Upgrading

To reset the database (e.g. after a breaking schema change):

```bash
docker compose down
docker volume rm fedora-nexus_fedora-nexus-data
docker compose up -d mcp-server
```

---

## Connecting to an agent

### VS Code (GitHub Copilot)

Add to `.vscode/mcp.json` (or the global `~/Library/Application Support/Code/User/mcp.json`):

```json
{
  "mcpServers": {
    "fedora-nexus": {
      "url": "http://localhost:7832/sse"
    }
  }
}
```

Reload the window and the `fedora-nexus` server will appear in the Copilot MCP panel.

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "fedora-nexus": {
      "url": "http://localhost:7832/sse",
      "type": "sse"
    }
  }
}
```

### Cursor

Add to `.cursor/mcp.json` at the project root or `~/.cursor/mcp.json` globally:

```json
{
  "mcpServers": {
    "fedora-nexus": {
      "url": "http://localhost:7832/sse"
    }
  }
}
```

---

## CLI reference

The `fedora-nexus` binary is the primary interface. All commands communicate with the running server.

| Command | Flags | Description |
|---------|-------|-------------|
| `fedora-nexus index <root-path>` | `--force` | Index a repo (symbols always included) |
| `fedora-nexus search <root-path> <query>` | `--limit N` | Hybrid search across indexed symbols |
| `fedora-nexus deps <root-path> <file>` | `--depth N` | Files that a given file imports |
| `fedora-nexus dependents <root-path> <file>` | `--depth N` | Files that import a given file |
| `fedora-nexus blast-radius <root-path> <file> [file...]` | `--max-depth N` | Everything affected by a change |
| `fedora-nexus query <root-path> <cypher>` | — | Execute a read-only Cypher query |
| `fedora-nexus graph <root-path>` | — | Print the full adjacency graph as JSON |
| `fedora-nexus list` | — | List all indexed repos with stats |
| `fedora-nexus delete <root-path>` | — | Remove a repo from the graph DB |
| `fedora-nexus server-start` | — | Start the server via Docker |
| `fedora-nexus server-stop` | — | Stop the server |

---

## MCP tools

| Tool | Description |
|------|-------------|
| `index_repo` | Index a repo and persist its dependency graph. Symbols (functions, classes, methods) are always extracted. |
| `search` | Hybrid BM25 + semantic search (BAAI/bge-small-en-v1.5 embeddings, RRF fusion) across all indexed symbols. |
| `get_dependencies` | Files that a given file imports, up to N hops deep. |
| `get_dependents` | Reverse: files that import a given file. |
| `blast_radius` | BFS over reverse edges — everything affected by a change. |
| `query_graph` | Execute native Cypher against the graph (read-only). |
| `get_graph` | Full or partial adjacency JSON. |
| `list_repos` | List all indexed repos with stats. |
| `delete_repo` | Remove a repo from the graph DB. |

### Example session

```
# 1. Index a repo
index_repo({ root_path: "/repos/myapp" })

# 2. Search for a symbol
search({ root_path: "/repos/myapp", query: "authenticate" })

# 3. Find what breaks if auth.py changes
blast_radius({ root_path: "/repos/myapp", changed_files: ["src/auth.py"] })

# 4. Cypher query
query_graph({ root_path: "/repos/myapp", cypher: "MATCH (f:Function {name: 'authenticate'}) RETURN f.file_path, f.start_line" })
```

---

## Graph schema

### Node types

| Type | Properties |
|------|-----------|
| `File` | `path`, `repo_id` |
| `Function` | `name`, `file_path`, `start_line`, `end_line` |
| `Class` | `name`, `file_path`, `start_line`, `end_line` |
| `Method` | `name`, `file_path`, `start_line`, `end_line` |

### Edge types

| Type | Connects | Description |
|------|----------|-------------|
| `DEPENDS_ON` | File → File | Import/require relationships |
| `CONTAINS` | File → Symbol | File contains a function, class, or method |
| `CALLS` | Symbol → Symbol | Function/method call sites (Python, TypeScript, JavaScript, Ruby) |

---

## Containers

| Name | Profile | Purpose |
|------|---------|---------|
| `mcp-server` | *(default)* | Persistent HTTP/SSE server on port 7832 |

```bash
# Start server
docker compose up -d mcp-server

# View logs
docker compose logs -f mcp-server
```

---

## Development

```bash
pip install -e ".[dev]"
python -m pytest tests/ -q
```

The `fastembed` dependency (including the BAAI/bge-small-en-v1.5 embedding model) is baked into the Docker image at build time — no runtime download required.

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `FEDORA_NEXUS_DB_PATH` | `/data/fedora-nexus.db` | Path to the Kuzu DB inside the container |
| `HOST_REPOS_PREFIX` | *(empty)* | Host path prefix to strip when translating paths |
| `CONTAINER_REPOS_PATH` | `/repos` | Container mount point for repos |
| `FEDORA_NEXUS_HTTP_PORT` | `7832` | HTTP server port |
