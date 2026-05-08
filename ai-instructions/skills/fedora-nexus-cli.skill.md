---
name: fedora-nexus-cli
description: "Use when: running fedora-nexus from the command line (no MCP server available). Covers all CLI commands, flags, output parsing, and recommended workflows. Domain: fedora-nexus, CLI."
---

# fedora-nexus — CLI Instructions for AI Agents

fedora-nexus is a code dependency graph tool. Use the `fedora-nexus` CLI to understand code before making changes.

**Output contract:** every command outputs JSON to stdout. Exit code 0 = success, 1 = error.

---

## When to use fedora-nexus

- Before editing a file → check blast radius first
- Understanding how code is structured → `search` + `query`
- Debugging a regression → trace callers and transitive dependents
- Reviewing a diff → map changed files to all affected code

---

## Command reference

```bash
# List all indexed repositories
fedora-nexus list

# Index a repo (first time, or after major changes)
fedora-nexus index /path/to/repo

# Index with symbols (functions, classes) for richer queries
fedora-nexus index /path/to/repo --with-symbols

# What does this file import?
fedora-nexus deps /path/to/repo src/auth.py --depth 2

# What would break if I change this file?
fedora-nexus dependents /path/to/repo src/auth.py

# What is the full blast radius of a change?
fedora-nexus blast-radius /path/to/repo src/auth.py src/user.py

# Full-text search across all indexed symbols
fedora-nexus search /path/to/repo "UserService"
fedora-nexus search /path/to/repo "authenticate" --limit 10

# Cypher query (read-only)
fedora-nexus query /path/to/repo "MATCH (f:File) WHERE f.path CONTAINS 'auth' RETURN f"
fedora-nexus query /path/to/repo "MATCH (c:Class {name:'UserService'})-[r:CodeRelation {type:'CONTAINS'}]->(m:Method) RETURN m.name, m.start_line"

# Full graph as adjacency JSON
fedora-nexus graph /path/to/repo

# Subgraph of specific files
fedora-nexus graph /path/to/repo -s src/auth.py -s src/user.py

# Remove a repo from the database
fedora-nexus delete /path/to/repo

# Pretty-print any command output (for humans; compact JSON is default)
fedora-nexus --pretty list
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
fedora-nexus index /path/to/repo

# 2. Check blast radius of the files you plan to touch
fedora-nexus blast-radius /path/to/repo src/auth.py

# 3. Check direct dependents for context
fedora-nexus dependents /path/to/repo src/auth.py --depth 2
```

### Exploring unfamiliar code

```bash
# Find the relevant symbols first
fedora-nexus search /path/to/repo "payment processing"

# Then trace dependencies from what you find
fedora-nexus deps /path/to/repo src/payments/processor.py --depth 3
```

### Reviewing a diff

```bash
# Pass all changed files to blast-radius at once
fedora-nexus blast-radius /path/to/repo src/a.py src/b.py src/c.py
```

---

## Parsing output in scripts

```bash
# Check if a repo is indexed
status=$(fedora-nexus list | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d['repos']))")
echo "Indexed repos: $status"

# Get blast radius count
count=$(fedora-nexus blast-radius /path/to/repo src/auth.py | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('affected', [])))")
echo "Affected files: $count"
```
