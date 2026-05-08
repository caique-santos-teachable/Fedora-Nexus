# depgraph — CLI Instructions for AI Agents

depgraph is a code dependency graph tool. Use the `depgraph` CLI to understand code before making changes.

**Output contract:** every command outputs JSON to stdout. Exit code 0 = success, 1 = error.

---

## When to use depgraph

- Before editing a file → check blast radius first
- Understanding how code is structured → `search` + `query`
- Debugging a regression → trace callers and transitive dependents
- Reviewing a diff → map changed files to all affected code

---

## Command reference

```bash
# List all indexed repositories
depgraph list

# Index a repo (first time, or after major changes)
depgraph index /path/to/repo

# Index with symbols (functions, classes) for richer queries
depgraph index /path/to/repo --with-symbols

# What does this file import?
depgraph deps /path/to/repo src/auth.py --depth 2

# What would break if I change this file?
depgraph dependents /path/to/repo src/auth.py

# What is the full blast radius of a change?
depgraph blast-radius /path/to/repo src/auth.py src/user.py

# Full-text search across all indexed symbols
depgraph search /path/to/repo "UserService"
depgraph search /path/to/repo "authenticate" --limit 10

# Cypher query (read-only)
depgraph query /path/to/repo "MATCH (f:File) WHERE f.path CONTAINS 'auth' RETURN f"
depgraph query /path/to/repo "MATCH (c:Class {name:'UserService'})-[r:CodeRelation {type:'CONTAINS'}]->(m:Method) RETURN m.name, m.start_line"

# Full graph as adjacency JSON
depgraph graph /path/to/repo

# Subgraph of specific files
depgraph graph /path/to/repo -s src/auth.py -s src/user.py

# Remove a repo from the database
depgraph delete /path/to/repo

# Pretty-print any command output (for humans; compact JSON is default)
depgraph --pretty list
```

---

## Graph schema (for `query`)

Node tables: `File`, `Function`, `Class`, `Method`
Relationship table: `CodeRelation` with `type` property — values: `CONTAINS`, `CALLS`, `DEPENDS_ON`

```cypher
-- All callers of a specific function
MATCH (caller)-[r:CodeRelation {type: 'CALLS'}]->(f:Function {name: "handle_auth"})
RETURN caller.name, caller.file_path

-- Methods of a class
MATCH (c:Class {name: "UserService"})-[r:CodeRelation {type: 'CONTAINS'}]->(m:Method)
RETURN m.name, m.start_line, m.end_line

-- Files in a specific directory
MATCH (f:File) WHERE f.path STARTS WITH 'src/api/' RETURN f.path

-- Transitive dependencies up to 3 hops
MATCH (f:File {path: 'src/auth.py'})-[:DEPENDS_ON*1..3]->(dep:File) RETURN dep.path
```

---

## Recommended workflow

### Before making a change

```bash
# 1. Ensure the repo is indexed
depgraph index /path/to/repo

# 2. Check blast radius of the files you plan to touch
depgraph blast-radius /path/to/repo src/auth.py

# 3. Check direct dependents for context
depgraph dependents /path/to/repo src/auth.py --depth 2
```

### Exploring unfamiliar code

```bash
# Find the relevant symbols first
depgraph search /path/to/repo "payment processing"

# Then trace dependencies from what you find
depgraph deps /path/to/repo src/payments/processor.py --depth 3
```

### Reviewing a diff

```bash
# Pass all changed files to blast-radius at once
depgraph blast-radius /path/to/repo src/a.py src/b.py src/c.py
```

---

## Parsing output in scripts

```bash
# Check if a repo is indexed
status=$(depgraph list | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d['repos']))")
echo "Indexed repos: $status"

# Get blast radius count
count=$(depgraph blast-radius /path/to/repo src/auth.py | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('affected', [])))")
echo "Affected files: $count"
```
