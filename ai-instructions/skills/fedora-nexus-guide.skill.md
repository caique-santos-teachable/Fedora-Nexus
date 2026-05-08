---
name: fedora-nexus-guide
description: "Use when: asked about available fedora-nexus tools, how to query the graph, or needing a reference for all MCP tools and graph schema."
---

# fedora-nexus Guide

Quick reference for all fedora-nexus MCP tools and the knowledge graph schema.

## Always start here

1. Call `list_repos` to see what is indexed and when it was last indexed.
2. Match your task to a skill below and follow the workflow.
3. If a repo is not indexed yet, call `index_repo` with `with_symbols=true`.

## Skills

| Task | Prompt to use |
|------|--------------|
| Understand architecture / "How does X work?" | `fedora-nexus-exploring` |
| Blast radius / "What breaks if I change X?" | `fedora-nexus-impact` |
| Trace bugs / "Why is X failing?" | `fedora-nexus-debugging` |
| Safe refactoring / rename / extract | `fedora-nexus-refactoring` |
| PR review / blast radius of a diff | `fedora-nexus-pr-review` |
| Tools, schema, Cypher reference | `fedora-nexus-guide` (this file) |

## Tools reference

| Tool | What it gives you |
|------|------------------|
| `index_repo` | Index a repo and build the graph. Use `with_symbols=true` for function/class nodes. |
| `search` | BM25 full-text search across all indexed symbols — best first step for any exploration. |
| `get_dependencies` | Files/symbols that a file imports, up to N hops. |
| `get_dependents` | Reverse: files that import the given file. |
| `blast_radius` | BFS over reverse edges — every file affected by a change, with depth distance. |
| `query_graph` | Native Kuzu Cypher queries (read-only). |
| `get_graph` | Full or subgraph adjacency JSON. |
| `list_repos` | All indexed repos with node/edge counts and last-indexed time. |

## Graph schema

**Node tables:** `File`, `Function`, `Class`, `Method`

**Relationships:** single `CodeRelation` table with `type` property.

| type | Meaning |
|------|---------|
| `CONTAINS` | File/Class contains a symbol |
| `CALLS` | Function/Method calls another symbol |
| `DEPENDS_ON` | File imports another file |

**Key properties:**
- All nodes: `id`, `name`, `file_path`, `language`
- Symbol nodes (Function, Class, Method): `start_line`, `end_line`, `content`, `is_exported`
- Method nodes: `owner_name` (parent class name)

## Cypher examples

```cypher
-- All methods of a class
MATCH (c:Class {name: "UserService"})-[r:CodeRelation {type: 'CONTAINS'}]->(m:Method)
RETURN m.name, m.file_path, m.start_line

-- Who calls a function
MATCH (caller)-[r:CodeRelation {type: 'CALLS'}]->(f:Function {name: "authenticate"})
RETURN caller.name, caller.file_path

-- All classes in a directory
MATCH (c:Class) WHERE c.file_path CONTAINS 'controllers'
RETURN c.name, c.file_path

-- Functions exported from a file
MATCH (f:Function {file_path: "src/auth.py", is_exported: true})
RETURN f.name, f.start_line
```
