---
applyTo: "**"
---
# depgraph MCP — Copilot Instructions

depgraph is a code dependency graph MCP server. Use its tools to understand code before making changes.

## When to use depgraph

- Before editing a file → check blast radius
- Understanding how code works → explore with search + query_graph
- Debugging a regression → trace callers and dependencies
- Reviewing a PR → map changed files to affected code

## Quick reference

```
list_repos()                                               → See indexed repos
index_repo({ root_path, with_symbols: true })              → Index a repo
search({ root_path, query: "keyword" })                    → Find symbols by name/content
blast_radius({ root_path, changed_files: ["..."] })        → Impact of a change
get_dependencies({ root_path, file_path, depth: 2 })       → What a file imports
get_dependents({ root_path, file_path })                   → What imports a file
query_graph({ root_path, cypher: "MATCH ..." })            → Native Cypher queries
```

## Graph schema (for query_graph)

Node tables: `File`, `Function`, `Class`, `Method`
Relationships: `CodeRelation` with `type` — values: `CONTAINS`, `CALLS`, `DEPENDS_ON`

```cypher
-- Callers of a function
MATCH (caller)-[r:CodeRelation {type: 'CALLS'}]->(f:Function {name: "my_func"})
RETURN caller.name, caller.file_path

-- Methods of a class
MATCH (c:Class {name: "MyClass"})-[r:CodeRelation {type: 'CONTAINS'}]->(m:Method)
RETURN m.name, m.start_line
```

## Skill prompts

For specific workflows, use these prompts (available via Copilot prompt picker):

| Prompt | Use for |
|--------|---------|
| `depgraph-guide` | Full tools + schema reference |
| `depgraph-exploring` | Understanding how code works |
| `depgraph-impact` | Blast radius before changing code |
| `depgraph-debugging` | Tracing bugs and errors |
| `depgraph-refactoring` | Safe rename / extract / move |
| `depgraph-pr-review` | Reviewing PRs for risk |
---

## CLI alternative (no MCP required)

If MCP is unavailable, use the `depgraph` CLI — same tools, JSON output:

```bash
depgraph index /path/to/repo
depgraph blast-radius /path/to/repo src/auth.py src/user.py
depgraph deps /path/to/repo src/auth.py --depth 2
depgraph dependents /path/to/repo src/auth.py
depgraph search /path/to/repo "UserService"
depgraph query /path/to/repo "MATCH (f:File) WHERE f.path CONTAINS 'auth' RETURN f"
depgraph list
```

Exit code 0 = success, 1 = error. Output is always JSON.