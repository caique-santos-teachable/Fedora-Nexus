---
mode: 'agent'
description: 'Use when the user wants to rename, extract, split, move, or restructure code safely. Examples: "Rename this function", "Extract this into a module", "Refactor this class", "Move this to a separate file"'
---

# Safe Refactoring with fedora-nexus

## When to use

- "Rename this function safely"
- "Extract this into a module"
- "Split this service"
- "Move this to a new file"
- Any rename, extract, split, or restructuring task

## Workflow

```
1. blast_radius({ changed_files: ["<file to refactor>"] })          → Full impact scope
2. get_dependents({ file_path: "<file>", depth: 3 })                → All importers to update
3. search({ query: "<symbol name>" })                               → Find all occurrences
4. query_graph — find symbol-level callers if indexed with symbols
5. Plan update order: core module → importers → tests
6. Make changes; verify with blast_radius after
```

## Checklists

### Rename a function or class

```
- [ ] search({ query: "oldName" }) — find all files that reference it
- [ ] query_graph — find all callers:
      MATCH (caller)-[r:CodeRelation {type: 'CALLS'}]->(f:Function {name: "oldName"})
      RETURN caller.name, caller.file_path
- [ ] blast_radius on the file containing the symbol
- [ ] Rename in definition file first
- [ ] Update all callers (d=1 items from blast_radius)
- [ ] Update imports in all dependent files
- [ ] Run tests
```

### Extract into a new module

```
- [ ] query_graph — find all callers of the symbols being extracted
- [ ] get_dependents on the source file
- [ ] Define the new module's interface
- [ ] Create new file and move code
- [ ] Update imports in all dependent files (d=1 from blast_radius)
- [ ] Run tests
```

### Move a file

```
- [ ] blast_radius({ changed_files: ["<file>"] }) — map all importers
- [ ] Update all d=1 importers to use the new path
- [ ] Move the file
- [ ] Verify with get_dependents({ file_path: "<new path>" })
```

## Tools in practice

**blast_radius** — full impact before any change:
```
blast_radius({
  root_path: "/repos/myapp",
  changed_files: ["src/auth/validators.py"]
})
→ d=1 (must update): login.py, middleware.py, test_auth.py
→ d=2 (verify): routes/auth.py
```

**search** — find all references to a symbol:
```
search({ root_path: "/repos/myapp", query: "validate_user" })
→ Function: validate_user (src/auth/validators.py:12)
→ Method: validate_user (tests/test_auth.py:45)  ← also in tests
```

**query_graph** — precise caller list:
```cypher
MATCH (caller)-[r:CodeRelation {type: 'CALLS'}]->(f:Function {name: "validate_user"})
RETURN caller.name, caller.file_path, caller.start_line
ORDER BY caller.file_path
```

## Example: "Rename validate_user to authenticate_user"

```
1. search({ query: "validate_user" })
   → 3 occurrences: validators.py, login.py, test_auth.py

2. query_graph — callers
   → login_handler (login.py:28), api_middleware (middleware.py:15)

3. blast_radius({ changed_files: ["src/auth/validators.py"] })
   → d=1: login.py, middleware.py, test_auth.py

4. Update order:
   - src/auth/validators.py — rename the function definition
   - src/auth/login.py — update call site (line 28)
   - src/api/middleware.py — update call site (line 15)
   - tests/test_auth.py — update test references

5. Run tests to confirm no breakage
```

---

## CLI fallback (when MCP is unavailable)

If the fedora-nexus MCP server is not running or not configured, use the `fedora-nexus` CLI directly — same operations, JSON output.

```bash
# 1. Full impact before any change
fedora-nexus blast-radius /path/to/repo src/auth/validators.py --max-depth 3

# 2. Find all references to a symbol
fedora-nexus search /path/to/repo "validate_user"

# 3. Precise caller list via Cypher
fedora-nexus query /path/to/repo "MATCH (caller)-[r:CodeRelation {type: 'CALLS'}]->(f:Function {name: 'validate_user'}) RETURN caller.name, caller.file_path, caller.start_line ORDER BY caller.file_path"

# 4. All importers to update
fedora-nexus dependents /path/to/repo src/auth/validators.py --depth 3

# 5. Verify after refactor (re-index with force)
fedora-nexus index /path/to/repo --force
fedora-nexus blast-radius /path/to/repo src/auth/validators.py
```

All commands exit 0 on success, 1 on error. Output is always JSON (add `--json` to suppress TUI).
