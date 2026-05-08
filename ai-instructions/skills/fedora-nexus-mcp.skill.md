---
name: fedora-nexus-mcp
description: "Use when: querying or indexing a codebase with the fedora-nexus MCP server. Covers all tools (index_repo, search, get_dependencies, get_dependents, blast_radius, query_graph, get_graph, list_repos, delete_repo), the Kuzu graph schema, and FTS search examples."
---

# Skill: fedora-nexus MCP

## Context
fedora-nexus is an MCP server that builds and queries a dependency graph for source code repositories. It supports Python, TypeScript, JavaScript, and Ruby. Agents use it to understand impact before editing, search symbols by name/content, detect circular dependencies, and navigate large codebases.

The server runs as a persistent HTTP/SSE process (`fedora-nexus-server` Docker container) on port 7832, or as a one-shot stdio process (`fedora-nexus-stdio`).

**Trigger phrases**: "what depends on this file", "blast radius of this change", "index the repo", "query the graph", "find all files in this module", "what does this file import", "search for this function", "who calls this method".

---

## Tools Reference

### `index_repo`
Indexes a repository into the graph store. Must be called before any other tool for a new repo.

| Parameter | Required | Type | Default | Notes |
|---|---|---|---|---|
| `root_path` | yes | string | — | HOST-side absolute path (e.g. `~/code/fedora`) |
| `languages` | no | array | all | `python`, `typescript`, `javascript`, `ruby` |
| `force_reindex` | no | bool | `false` | Re-indexes from scratch |
| `with_symbols` | no | bool | `false` | Also indexes functions/classes/methods as nodes — required for `search` and symbol-level `query_graph` |

**Returns:** `{ status, root_path, nodes, edges, languages, indexed_at }`

**Warnings:**
- Always pass `root_path` as the HOST-side path. The server translates it internally via `HOST_REPOS_PREFIX`.
- `with_symbols=true` is heavier but required for `search`, `blast_radius` on symbols, and Cypher queries against `Function`/`Class`/`Method` tables.
- `force_reindex` with a subset of `languages` permanently removes nodes of other languages from the graph.

---

### `search`
BM25 full-text search across all indexed symbols. Best first step for any exploration or debugging task.

| Parameter | Required | Type | Default |
|---|---|---|---|
| `root_path` | yes | string | — |
| `query` | yes | string | — |
| `limit` | no | int | `20` |

**Returns:** `{ results: [{ id, name, file_path, kind, start_line, end_line, score, rank }], count, query }`

**Notes:**
- Searches `File`, `Function`, `Class`, and `Method` tables simultaneously.
- Results are ranked by BM25 score across `name` and `content` fields.
- Requires `with_symbols=true` indexing for Function/Class/Method results.

---

### `get_dependencies`
Returns files that a given file imports/depends on.

| Parameter | Required | Type | Default |
|---|---|---|---|
| `root_path` | yes | string | — |
| `file_path` | yes | string | — | repo-relative (e.g. `app/models/user.rb`) |
| `depth` | no | int | `1` |

**Returns:** `{ file, depth, dependencies: [...], count }`

---

### `get_dependents`
Returns files that import/depend on a given file.

| Parameter | Required | Type | Default |
|---|---|---|---|
| `root_path` | yes | string | — |
| `file_path` | yes | string | — | repo-relative |
| `depth` | no | int | `1` |

**Returns:** `{ file, depth, dependents: [...], count }`

---

### `blast_radius`
BFS over reverse dependency edges — every file that would be impacted by changes to the given files, with per-file depth distance.

| Parameter | Required | Type | Default |
|---|---|---|---|
| `root_path` | yes | string | — |
| `changed_files` | yes | array | — | repo-relative paths |
| `max_depth` | no | int | `10` |

**Returns:** `{ affected: [{ file, depth }], count }`

| Depth | Risk |
|-------|------|
| d=1 | **WILL BREAK** — direct importers |
| d=2 | LIKELY AFFECTED |
| d=3+ | MAY NEED TESTING |

---

### `query_graph`
Executes native Kuzu Cypher against the graph (read-only). Write clauses (`CREATE`, `DELETE`, `SET`, `MERGE`, `DROP`, `ALTER`) are blocked and return `QUERY_NOT_ALLOWED`.

| Parameter | Required | Type |
|---|---|---|
| `root_path` | yes | string |
| `cypher` | yes | string |

**Returns:** `{ rows: [...], count }`

---

### `get_graph`
Returns nodes and edges for the full graph or a subgraph.

| Parameter | Required | Type | Notes |
|---|---|---|---|
| `root_path` | yes | string | — |
| `subgraph_paths` | no | array | Returns only the subgraph of the listed node IDs |

**Returns:** `{ nodes: [...], edges: [...] }`

---

### `list_repos`
Lists all indexed repositories.

No required parameters.

**Returns:** `{ repos: [{ root_path, nodes, edges, indexed_at, breakdown: { File, Function, Class, Method } }], count }`

---

### `delete_repo`
Removes an indexed repository from the store.

| Parameter | Required | Type |
|---|---|---|
| `root_path` | yes | string |

**Returns:** `{ status: "deleted" | "not_found" }`

---

## Graph Schema

### Node tables

| Table | Key properties |
|-------|----------------|
| `File` | `id`, `name`, `file_path`, `language`, `content` (first 2000 chars) |
| `Function` | `id`, `name`, `file_path`, `language`, `start_line`, `end_line`, `content`, `is_exported` |
| `Class` | `id`, `name`, `file_path`, `language`, `start_line`, `end_line`, `content`, `is_exported` |
| `Method` | `id`, `name`, `file_path`, `language`, `start_line`, `end_line`, `content`, `is_exported`, `owner_name` |

All symbol node IDs follow the format: `{rel_path}#{kind}:{name}` — e.g. `src/auth.py#function:validate_user`

### Relationship table

Single `CodeRelation` table with `type` property:

| type | Meaning |
|------|---------|
| `CONTAINS` | File contains a symbol; Class contains a method |
| `CALLS` | Function/Method calls another symbol |
| `DEPENDS_ON` | File imports another file |

---

## Cypher Examples

```cypher
-- All methods of a class
MATCH (c:Class {name: "UserService"})-[r:CodeRelation {type: 'CONTAINS'}]->(m:Method)
RETURN m.name, m.file_path, m.start_line, m.end_line
```

```cypher
-- Who calls a function
MATCH (caller)-[r:CodeRelation {type: 'CALLS'}]->(f:Function {name: "validate_user"})
RETURN caller.name, caller.file_path, caller.start_line
```

```cypher
-- What a function calls
MATCH (f:Function {name: "validate_user"})-[r:CodeRelation {type: 'CALLS'}]->(callee)
RETURN callee.name, callee.file_path
```

```cypher
-- All exported functions in a file
MATCH (f:Function {file_path: "src/auth/validators.py", is_exported: true})
RETURN f.name, f.start_line
```

```cypher
-- All files that import a given file
MATCH (src:File {file_path: "src/auth/validators.py"})<-[r:CodeRelation {type: 'DEPENDS_ON'}]-(importer:File)
RETURN importer.file_path
```

```cypher
-- All classes in a directory
MATCH (c:Class) WHERE c.file_path CONTAINS 'controllers'
RETURN c.name, c.file_path, c.start_line
```

```cypher
-- Transitive callers of a function (up to 3 hops)
MATCH path = (caller)-[r:CodeRelation {type: 'CALLS'}*1..3]->(f:Function {name: "process_payment"})
RETURN [n IN nodes(path) | n.name] AS call_chain
```

```cypher
-- All Ruby method nodes
MATCH (m:Method) WHERE m.language = 'ruby'
RETURN m.name, m.owner_name, m.file_path
```

---

## Common Workflows

### Before refactoring a file
1. `blast_radius` — full impact surface.
2. `get_dependents` — direct importers that will break.
3. `search` — find all symbol occurrences by name.
4. `query_graph` — precise caller list for affected functions.

### Debugging: why is X failing?
1. `search({ query: "<function name or error keyword>" })` — locate the suspect.
2. `query_graph` — find all callers of the suspect.
3. `get_dependencies({ file_path: "<suspect file>", depth: 2 })` — trace what it depends on.

### PR review
1. `blast_radius({ changed_files: [<all changed files>] })` — full blast radius.
2. Check d=1 items — are they updated in the PR?
3. `query_graph` — caller chains for non-trivial changed functions.
4. Assess risk: <5 files LOW, 5–15 MEDIUM, >15 HIGH, auth/payments CRITICAL.

### Exploring architecture
1. `list_repos` — verify indexed and not stale.
2. `search({ query: "<concept>" })` — find relevant symbols.
3. `query_graph` — callers, callees, class members.
4. `get_dependencies({ depth: 2 })` — module dependency map.
