---
description: MCP server development patterns — error handling, API contract design, and testing discipline for Python MCP servers (e.g. depgraph).
applyTo: "**/depgraph/**/*.py,**/mcp/**/*.py"
---

# MCP Server Development — Quality Guardrails

Objective: prevent recurring bugs in MCP tool contracts, error handling, and response shapes.

## Patterns de prevenção de erro

### 1) Stale cache after resource deletion — use a typed guard helper

- **Anti-pattern**: after `delete_repo`, the in-memory (or Redis) cache still returns the graph because the delete only removes the persistent store entry. Callers receive a cached object for a repo that no longer exists.
- **Padrão recomendado**: introduce a domain-specific exception (`RepoNotFoundError`) and a guard helper (`_require_graph`) that validates existence in the authoritative store **before** returning cached data. The cache must be invalidated synchronously on delete.
- **Exemplo**:
  ```python
  # ❌ delete only clears DB — cache returns stale data
  async def delete_repo(repo_id: str) -> None:
      await db.delete(repo_id)          # cache still warm

  # ✅ guard validates existence; cache cleared on delete
  class RepoNotFoundError(Exception):
      pass

  def _require_graph(repo_id: str) -> Graph:
      graph = cache.get(repo_id)
      if graph is None:
          raise RepoNotFoundError(f"repo {repo_id!r} not found")
      return graph

  async def delete_repo(repo_id: str) -> None:
      await db.delete(repo_id)
      cache.evict(repo_id)              # must be synchronous
  ```

### 2) Silent partial results in MCP tools — always include `missing_*` envelope field

- **Anti-pattern**: a tool like `subgraph_paths` silently returns only the paths it found, omitting the ones it couldn't resolve. The caller assumes the result is complete.
- **Padrão recomendado**: every tool that accepts a list of identifiers and resolves them must include a `missing_paths` (or `missing_items`) field in the response, even when empty. Never truncate results silently.
- **Exemplo**:
  ```python
  # ❌ partial result, no indication of what was skipped
  return {"paths": found_paths}

  # ✅ envelope exposes what was not resolved
  return {
      "paths": found_paths,
      "missing_paths": [p for p in requested if p not in resolved],
  }
  ```
- **Regra**: if `missing_paths` is always present (even as `[]`), callers can assert on it without null-checking.

### 3) Unactionable QUERY_ERROR — return structured error with diagnostic fields

- **Anti-pattern**: query failures return a generic string message (`"QUERY_ERROR: syntax error"`). The caller cannot programmatically identify which clause caused the failure.
- **Padrão recomendado**: return a structured error dict with a fixed `error` key and optional diagnostic fields (`unsupported_clauses`, `hint`). Document explicitly which fields are conditional so callers know to null-check them.
- **Exemplo**:
  ```python
  # ❌ opaque string — unactionable
  return "QUERY_ERROR: unsupported filter"

  # ✅ structured — callers can branch on unsupported_clauses
  return {
      "error": "QUERY_ERROR",
      "message": "One or more clauses are not supported",
      "unsupported_clauses": ["kind == 'unknown'"],  # may be None/absent
  }
  ```
- **Atenção**: conditional fields (`unsupported_clauses`) must be **documented** in the tool's docstring as potentially absent. Callers must null-check before accessing.

### 4) Missing metadata fields in mutation responses — always return `indexed_at`

- **Anti-pattern**: `index_repo` completes successfully but returns only `{"status": "ok"}` — the caller cannot tell *when* the index was built, making cache-busting and freshness checks impossible.
- **Padrão recomendado**: every mutation tool that modifies persistent state must return the authoritative timestamp of the new state (`indexed_at`, `updated_at`, etc.).
- **Exemplo**:
  ```python
  # ❌ no timestamp — caller cannot verify freshness
  return {"status": "indexed"}

  # ✅ timestamp included — callers can assert recency
  indexed_at = await store.get_indexed_at(repo_id)
  return {"status": "indexed", "indexed_at": indexed_at.isoformat()}
  ```

### 5) Tool parameter descriptions — label permanent vs. query-scoped filters

- **Anti-pattern**: a parameter like `languages` is described only as "filter by language" without clarifying whether it applies at index time (permanent, affects stored data) or query time (temporary, affects only that call). Callers may misuse it.
- **Padrão recomendado**: always label filter parameters with their scope in the description string. Use explicit wording: `"Permanent index-level filter — ..."` vs. `"Query-scoped filter — ..."`.
- **Exemplo**:
  ```python
  # ❌ ambiguous
  "languages": "filter by programming language"

  # ✅ scope is explicit
  "languages": (
      "Permanent index-level filter. Only nodes matching these languages "
      "are stored in the graph. Cannot be changed without re-indexing. "
      "Example: ['python', 'typescript']"
  )

### 6) No-op params in indexer subclasses — document with explicit comment, not silence

- **Anti-pattern**: a new optional param (e.g. `symbol_mode`) is added to `BaseIndexer.index()`. Subclasses that haven't implemented it yet accept the param but ignore it without any comment — behavior gap is invisible to callers and reviewers.
- **Padrão recomendado**: every subclass that receives a param without implementing it **must include a one-line comment** naming the param and explaining the gap. This must be consistent across all non-implementing subclasses.
- **Exemplo**:
  ```python
  # ❌ silently ignores symbol_mode — QA flags inconsistency
  def index(self, repo_path: str, *, symbol_mode: bool = False) -> Graph:
      for file in self._find_files(repo_path):
          ...

  # ✅ gap documented inline
  def index(self, repo_path: str, *, symbol_mode: bool = False) -> Graph:
      # symbol_mode not implemented for TypeScript; file-level only
      for file in self._find_files(repo_path):
          ...
  ```
- **Regra**: if one subclass documents the gap (e.g. Ruby) but another doesn't (e.g. TypeScript), QA will flag the inconsistency as a warning. Standardize across all non-implementing subclasses.
  ```

### 7) Migrating from external to embedded persistence (e.g. PostgreSQL → Kuzu) — checklist

- **Anti-pattern**: old store file (e.g. `postgres.py`) is left as dead code after the migration. References to env vars like `DATABASE_URL` may still exist in Docker/config files. Tests that required a running service are ported but still carry integration-style `@pytest.mark.skip` guards.
- **Padrão recomendado**: when replacing an external-service store with an embedded one, treat the following as a single atomic deliverable in one cycle:
  1. Implement the new embedded store with identical public API.
  2. **Delete** the old store file in the same PR/cycle — do not leave it as dead code.
  3. Remove all Docker Compose services and env vars specific to the old backend.
  4. Rewrite all store tests to run without external services (use `tmp_path` for embedded DBs).
  5. Verify no references to old client packages (e.g. `psycopg`, `asyncpg`) remain in active code or `pyproject.toml`.
- **Node ID namespacing**: embedded graph DBs (Kuzu, DuckDB) use table-scoped primary keys. When multiple repos share a single database, namespace node IDs as `{root_path}::{relative_id}` to avoid PK collisions across repos.
- **Schema initialization safety**: call `_ensure_schema()` (or `init_schema()`) as a guard at the start of every public method — embedded DBs may be opened fresh in test contexts where `__init__` ran against a different tmp path.
- **Exemplo**:
  ```python
  # ❌ node ID collision across repos — two repos with file "app/main.py"
  node_id = n["id"]   # "app/main.py" — collides

  # ✅ repo-namespaced — globally unique
  node_id = f"{root_path}::{n['id']}"   # "/repos/a::app/main.py"
  ```

### 8) Schema version detection — `SchemaVersionError` pattern with dedicated unit test

- **Anti-pattern**: a `KuzuStore` (or any embedded-DB store) opens an existing database that was created with an older schema. Columns are missing or mismatched. The error surfaces as a cryptic `RuntimeError` from the query engine mid-operation, not at startup.
- **Padrão recomendado**: embed a schema version constant in the store (e.g. `SCHEMA_VERSION = 2`). In `_ensure_schema()`, write the version to a metadata table on creation; on open, read it and compare. If the stored version is lower, raise `SchemaVersionError` immediately with a message that tells the user to delete the DB directory and re-index.
- **Checklist**:
  1. `SchemaVersionError` must be a named exception class (not a plain `RuntimeError` subtype) so callers can catch it specifically.
  2. Write a dedicated unit test that creates a DB with an old schema (mock or `tmp_path`), opens it with the new store, and asserts that `SchemaVersionError` is raised.
  3. The error message must include the expected version, the found version, and the path to the DB.
- **Exemplo**:
  ```python
  # ❌ silent schema mismatch — crash at query time with unrelated error
  class KuzuStore:
      def _ensure_schema(self) -> None:
          conn.execute("CREATE TABLE IF NOT EXISTS File (id STRING, ...)")

  # ✅ version guard raises early with actionable message
  SCHEMA_VERSION = 2

  class SchemaVersionError(RuntimeError):
      pass

  class KuzuStore:
      def _ensure_schema(self) -> None:
          conn.execute("CREATE TABLE IF NOT EXISTS _meta (version INT)")
          row = conn.execute("MATCH (m:_meta) RETURN m.version").fetchone()
          if row and row[0] != SCHEMA_VERSION:
              raise SchemaVersionError(
                  f"Schema version mismatch: expected {SCHEMA_VERSION}, found {row[0]}. "
                  f"Delete {self._db_path!r} and re-index."
              )

  # unit test (no real DB needed — mock the conn response)
  def test_schema_version_error_on_old_schema(tmp_path):
      store = KuzuStore(tmp_path / "db")
      store._write_schema_version(1)   # simulate stale DB
      with pytest.raises(SchemaVersionError, match="expected 2, found 1"):
          KuzuStore(tmp_path / "db")._ensure_schema()
  ```

### 9) FTS index lifecycle in Kuzu — `CREATE_FTS_INDEX` must live in `_ensure_schema()`

- **Anti-pattern**: `CALL QUERY_FTS_INDEX(...)` is used in the search tool without a corresponding `CREATE_FTS_INDEX` call in the schema initialization path. The FTS index is missing on a fresh DB, and the first search call raises a runtime error with no clear diagnostic.
- **Padrão recomendado**: call `CREATE_FTS_INDEX(table, ['col1', 'col2'])` immediately after the `CREATE TABLE` statement inside `_ensure_schema()`. Use `IF NOT EXISTS` semantics (or catch "already exists" errors) so that re-running schema init is safe. On schema migration (`SchemaVersionError`), the user is forced to drop and re-create, which also re-creates the FTS index.
- **Ranked result field**: `QUERY_FTS_INDEX` returns a `score` or `rank` field. This field **must have a dedicated unit test** asserting its presence and numeric type — it's a computed field and can disappear silently if the index is dropped (see rule 1 in guardrails).
- **Checklist**:
  1. `CREATE_FTS_INDEX` call is in `_ensure_schema()`, not in `__init__` or in the search method.
  2. `CREATE_FTS_INDEX` is idempotent (safe to call on an existing index).
  3. `test_search_returns_results_with_rank` asserts `result["rank"]` (or `result["score"]`) is present and is a float/int.
  4. `test_search_returns_function_by_name` asserts the top result matches the indexed symbol name.
- **Exemplo**:
  ```python
  # ❌ FTS index created lazily — first search on fresh DB crashes
  def search(self, query: str) -> list[dict]:
      return conn.execute(
          "CALL QUERY_FTS_INDEX('Function', 'fts_idx', $q) RETURN *", {"q": query}
      ).fetchall()

  # ✅ FTS index created in schema init — always available at search time
  def _ensure_schema(self) -> None:
      conn.execute("CREATE TABLE IF NOT EXISTS Function (id STRING, name STRING, content STRING)")
      conn.execute("CALL CREATE_FTS_INDEX('Function', 'fts_idx', ['name', 'content'])")

  def search(self, query: str) -> list[dict]:
      rows = conn.execute(
          "CALL QUERY_FTS_INDEX('Function', 'fts_idx', $q) YIELD node, score "
          "RETURN node.id, node.name, score", {"q": query}
      ).fetchall()
      return [{"id": r[0], "name": r[1], "rank": r[2]} for r in rows]

  # unit test — rank field protected
  def test_search_returns_rank_field(store_with_function):
      results = store_with_function.search("my_function")
      assert results, "expected at least one result"
      assert "rank" in results[0]
      assert isinstance(results[0]["rank"], (int, float))
  ```

### 10) Replacing a custom query parser with native DB execution — dead code marking checklist

- **Anti-pattern**: a custom Lark/regex-based Cypher parser is replaced with native Kuzu Cypher execution. The old validation constants (`_SUPPORTED_CLAUSES`, `_UNSUPPORTED_CLAUSES`) are left in the source file without any `# legacy` marker — reviewers and future engineers cannot tell if they are still active.
- **Padrão recomendado**: when replacing a parser/validator with a native DB engine, treat old allowlist constants as dead code in the same cycle. Either delete them (preferred) or mark each one with `# legacy — replaced by <description>; kept for reference`. Never leave them unmarked.
- **Checklist** when replacing a custom parser:
  1. Grep for `_SUPPORTED`, `_UNSUPPORTED`, `_ALLOWED`, `_BLOCKED` constants in the file being changed.
  2. Is each constant still referenced by live code? If not → delete in the same cycle.
  3. If kept for rollback reference → add `# legacy — replaced by native Kuzu Cypher; kept for reference` above the constant definition.
  4. Write `test_query_graph_uses_native_cypher` — assert the new path executes without the old parser being invoked.
  5. Write `test_query_graph_rejects_write_clauses` — assert that `CREATE`/`MERGE`/`SET`/`DELETE` are still blocked (either by the DB, or by an explicit allowlist check if the DB doesn't block them).
- **Exemplo**:
  ```python
  # ❌ dead constants left unmarked after native migration — QA flags on cycle 2
  _UNSUPPORTED_CLAUSES = {"CREATE", "MERGE", "SET", "DELETE"}
  _SUPPORTED_CLAUSES   = {"MATCH", "RETURN", "WHERE", "WITH", "UNWIND"}

  # ✅ marked in same cycle as the parser replacement
  # legacy — replaced by native Kuzu Cypher; kept for reference
  _UNSUPPORTED_CLAUSES = {"CREATE", "MERGE", "SET", "DELETE"}
  # legacy — replaced by native Kuzu Cypher; kept for reference
  _SUPPORTED_CLAUSES   = {"MATCH", "RETURN", "WHERE", "WITH", "UNWIND"}
  ```

## Checklist obrigatório antes de concluir um MCP tool

1. Every new response field has a **dedicated unit test** (not only integration/DB tests that may be skipped).
2. Every new **input parameter** in the MCP schema has a unit test asserting it is forwarded to the internal layer (e.g. `symbol_mode=True` → `run_index(..., symbol_mode=True)`).
3. Every tool accepting a list of IDs returns a `missing_*` field for unresolved items.
4. Every mutation response includes a freshness timestamp (`indexed_at`, `updated_at`).
5. Every error shape documents which fields are conditional (null-check required by callers).
6. Cache invalidation is **synchronous** on delete — never rely on TTL expiry alone.
7. Filter parameter descriptions label scope as `permanent` or `query-scoped`.
8. `CREATE_FTS_INDEX` is called in `_ensure_schema()` — never lazy-initialized at search time.
9. FTS `rank`/`score` field has a dedicated unit test asserting presence and numeric type.
10. Schema version is checked on DB open; `SchemaVersionError` is raised with a human-readable migration message.
11. Dead parser constants (`_SUPPORTED_*`, `_UNSUPPORTED_*`) are deleted or marked `# legacy` in the same cycle they become unreferenced.
8. Every indexer subclass that accepts but does not implement an optional param includes an explicit inline comment explaining the gap.
9. Whenever a previously no-op/stub param or method is promoted to a real implementation, treat it as a **new feature** for coverage — unit tests for each new branch are mandatory in the same cycle (see guardrail #7). The param already existing in the signature does **not** waive this obligation.
10. Every MCP tool emits a `[TOOL] <name> args=<safe_args>` log line at call start and a `_log_result_summary` line at completion with `%.3fs` elapsed time (see patterns #7–#8 below).
11. Sensitive or oversized args (raw queries, file content) are **redacted** from start log lines — log key name/presence only.
12. Every `async def _handle_*` function dispatches sync I/O via `asyncio.to_thread()` — no direct sync calls that touch DB, disk, or network.
13. `logging.basicConfig()` is at **module level** with `stream=sys.stderr, force=True` — never inside `main()` or `run()`.
14. Startup-time external dependencies (`init_schema`, cache warm-up) are wrapped in `try/except` — server must reach its event loop even if dependencies are unavailable.

### 7) MCP tool logging — every call must emit start + result summary with elapsed time

- **Anti-pattern**: tool implementations log ad-hoc messages (or nothing). Elapsed time is unknown; operators cannot correlate slow calls or detect regressions.
- **Padrão recomendado**: use a `_dispatch` wrapper that logs `[TOOL] <name> args=<safe_args>` **before** the handler and calls `_log_result_summary(name, result, elapsed)` **after**. Measure elapsed with `time.perf_counter()`. Redact large/sensitive args.
- **Exemplo**:
  ```python
  async def _dispatch(name: str, args: dict) -> Any:
      log_args = {k: v for k, v in args.items() if k not in ("cypher",)}  # redact large/sensitive
      logger.info("[TOOL] %s args=%s", name, log_args)
      t0 = time.perf_counter()
      result = await _route(name, args)
      elapsed = time.perf_counter() - t0
      _log_result_summary(name, result, elapsed)
      return result

  def _log_result_summary(name: str, result: dict, elapsed: float) -> None:
      # Log scalar fields only — never dump full node/edge arrays
      if name == "index_repo":
          logger.info("[TOOL] index_repo done in %.3fs — nodes=%s edges=%s",
                      elapsed, result.get("nodes"), result.get("edges"))
      elif name == "blast_radius":
          logger.info("[TOOL] blast_radius done in %.3fs — affected=%d",
                      elapsed, len(result.get("affected", [])))
      else:
          logger.info("[TOOL] %s done in %.3fs", name, elapsed)
  ```
- **Regras**:
  1. `[TOOL]` prefix on every line — enables `grep '[TOOL]'` to isolate tool traces in mixed logs.
  2. Result summary logs **scalar fields only** (counts, status strings) — never dump full arrays.
  3. `_log_result_summary` must be a **separate function**, not inline — keeps dispatch minimal and summaries unit-testable.

### 8) Indexer progress logging — three-phase `[INDEX]` pattern

- **Anti-pattern**: indexer runs silently for minutes on large repos — operators cannot tell if it hung on parse or on DB write.
- **Padrão recomendado**: emit three `[INDEX]` log lines: (1) start with params, (2) parse done with elapsed, (3) save done with total elapsed + node/edge counts. Parse errors must be logged at `ERROR` level (not `DEBUG`/`INFO`).
- **Exemplo**:
  ```python
  def _run_index(root_path, languages, symbol_mode):
      logger.info("[INDEX] starting root=%r langs=%s symbol_mode=%s",
                  root_path, languages or "all", symbol_mode)
      t0 = time.perf_counter()
      graph = TreeSitterIndexer(languages=languages).index(root_path, symbol_mode=symbol_mode)
      t_index = time.perf_counter()
      logger.info("[INDEX] parse done in %.3fs — saving to DB ...", t_index - t0)
      store.save_graph(root_path, graph)
      t_save = time.perf_counter()
      data = graph.to_adjacency_json()
      logger.info("[INDEX] saved in %.3fs — total=%.3fs nodes=%d edges=%d",
                  t_save - t_index, t_save - t0, len(data["nodes"]), len(data["edges"]))
  ```

### 9) Blocking I/O in async MCP tool handlers — always wrap with asyncio.to_thread()

- **Anti-pattern**: an async tool handler calls a synchronous function (DB query, indexer, file I/O) directly. The call blocks the event loop, freezing the entire MCP stdio server — Cursor/Claude receive no response and time out.
- **Padrão recomendado**: every synchronous blocking call inside an `async def` handler **must** be dispatched with `asyncio.to_thread()`. This applies to DB reads/writes, indexer runs, and any filesystem I/O.
- **Exemplo**:
  ```python
  # ❌ blocks event loop — server hangs in Cursor
  async def _handle_index_repo(args: dict) -> dict:
      graph = run_index(args["repo_id"])  # sync, blocks loop
      return graph.to_dict()

  # ✅ offloaded to thread — event loop stays free
  async def _handle_index_repo(args: dict) -> dict:
      graph = await asyncio.to_thread(run_index, args["repo_id"])
      return graph.to_dict()
  ```
- **Regra**: treat any non-`async` call that touches IO (DB, disk, network) inside an async handler as a blocking bug. Grep for `def _handle_` and audit every direct call inside.

### 10) logging.basicConfig placement — module level only, never inside main()

- **Anti-pattern**: `logging.basicConfig()` is called inside `main()` or `async def run()`. On MCP stdio servers the loop may start before initialization completes, causing log lines to be swallowed or written to the wrong stream.
- **Padrão recomendado**: call `logging.basicConfig()` at **module level**, immediately after imports. For stdio MCP servers force `stream=sys.stderr` and `line_buffering=True` so lines are never lost on crash or truncation. Use `force=True` to override any prior basicConfig calls from third-party libs.
- **Exemplo**:
  ```python
  # ❌ configured inside main — may be too late
  async def main():
      logging.basicConfig(level=logging.INFO)
      await server.run()

  # ✅ module level — guaranteed before any handler fires
  import sys, logging

  logging.basicConfig(level=logging.INFO, stream=sys.stderr, force=True)
  sys.stderr.reconfigure(line_buffering=True)
  logger = logging.getLogger(__name__)
  ```

### 11) Blocking startup dependencies — server must start even if DB is down

- **Anti-pattern**: `init_schema()` (or any DB/network setup) is called unconditionally at startup. If the DB is unreachable the MCP server process exits before Cursor/Claude can connect — the failure mode is invisible to the user.
- **Padrão recomendado**: wrap every startup-time external dependency in `try/except` and log a warning on failure. The server **must reach its listen loop** regardless of DB availability. Tools requiring DB fail gracefully at call time.
- **Exemplo**:
  ```python
  # ❌ DB failure kills process before server is ready
  async def main():
      init_schema()        # raises if DB is down → server never starts
      await server.run()

  # ✅ fault-tolerant — server starts, tools fail gracefully later
  async def main():
      try:
          init_schema()
          logger.info("DB schema ready")
      except Exception as exc:
          logger.warning("DB init failed (%s) — starting in degraded mode", exc)
      await server.run()
  ```
- **Regra**: apply to every external resource initialized at startup (DB, cache, config service).

## Regra de evolução contínua

Adicionar novos itens neste arquivo sempre que um bug de contrato MCP, erro de shape, ou falha de cache for encontrado. Seguir o formato: anti-pattern → padrão recomendado → exemplo curto.

### 12) MCP stdio transport in Docker — use `-i` only, never `-t`

- **Anti-pattern**: running a containerized MCP stdio server with `docker run -it` (or `-ti`). The `-t` flag allocates a pseudo-TTY, which corrupts binary/newline-framed stdio frames that MCP clients (Cursor, Claude Desktop) exchange with the server — the client hangs or receives malformed JSON.
- **Padrão recomendado**: always use `docker run --rm -i` (stdin attached, no TTY). Add this as an explicit comment in any run script or README that documents how to launch the MCP container.
- **Exemplo**:
  ```bash
  # ❌ -t allocates TTY — corrupts MCP stdio framing
  docker run --rm -it depgraph-mcp

  # ✅ -i only — clean binary stdio
  docker run --rm -i \
    -e DATABASE_URL="$DATABASE_URL" \
    -v /repos:/repos:ro \
    depgraph-mcp
  ```
- **Regra**: any generated Cursor/Claude Desktop `mcp_config.json` or `mcpServers` block must pass `["docker", "run", "--rm", "-i", ...]` — never include `"-t"` in that array.

### 13) docker-compose for MCP services — postgres-only default, debug profile for MCP container

- **Anti-pattern**: including the MCP server as a default service in `docker-compose.yml`. Developers running `docker compose up` to start only the DB also start the MCP container, which may fail if `DATABASE_URL` is not yet configured or the image is not built.
- **Padrão recomendado**: gate the MCP container behind a named profile (e.g. `profiles: [debug]`). The default `docker compose up` brings up only infrastructure services (postgres, redis). Developers opt in to the full stack explicitly.
- **Exemplo**:
  ```yaml
  # ✅ postgres starts by default; mcp only with --profile debug
  services:
    postgres:
      image: pgvector/pgvector:pg16
      # ... no profiles key — always starts

    mcp:
      build: .
      profiles: [debug]
      depends_on: [postgres]
      environment:
        DATABASE_URL: postgresql://...
      volumes:
        - /repos:/repos:ro
      stdin_open: true   # equivalent to -i
      tty: false         # never true for MCP stdio
  ```
- **Checklist**: confirm `tty: false` (or absent) when `stdin_open: true` in any MCP service definition.

### 14) Container volume mount convention for repo indexing — use `/repos`

- **Anti-pattern**: mounting host repo paths at arbitrary or user-specific locations (e.g. `/home/user/code`, `/workspace`) inside the MCP container. Different developers have different layouts; paths baked into Cursor config or README become non-portable.
- **Padrão recomendado**: standardize the in-container mount point as `/repos` (read-only). Host paths vary; the container-side path is always `/repos`. Document this as the convention in `README.md` and in any `mcp_config.json` template.
- **Exemplo**:
  ```bash
  # ✅ portable — host path varies, /repos is constant
  docker run --rm -i \
    -v "$HOME/projects:/repos:ro" \
    depgraph-mcp

  # Cursor mcpServers entry:
  # "args": ["run", "--rm", "-i", "-v", "/Users/alice/projects:/repos:ro", "depgraph-mcp"]
  ```
- **Regra**: all tool calls that accept a `repo_path` must expect paths rooted at `/repos/...` when running in the container. Document this mapping in the tool's description string.

---

### 7) Migrating from external to embedded persistence (e.g. PostgreSQL → Kuzu) — checklist

- **Anti-pattern**: leaving the old store file (e.g. `postgres.py`) as dead code after migration — requires a follow-up cycle to clean up.
- **Padrão recomendado**: delete the replaced file in the **same cycle** as the migration. Treat dead store files as blockers, not cosmetics.
- **Node ID namespacing**: when using an embedded DB shared across repos (e.g. Kuzu), namespace node IDs as `{root_path}::{relative_path}` to avoid primary key collisions across repos.
- **Schema init safety**: call `_ensure_schema()` (or equivalent) at the start of every public method so tests with fresh `tmp_path` databases work without explicit `init_schema()` setup.
- **Clean sweep checklist** — in one cycle:
  1. Delete old store file
  2. Remove old client package from `pyproject.toml`
  3. Remove env vars (`DATABASE_URL`) from `server.py`, `docker-compose.yml`, `.env.example`
  4. Remove external service from `docker-compose.yml` (postgres service + named volume)
  5. Rewrite `test_store.py` to use `tmp_path` — no external service needed

### 15) Kuzu DETACH DELETE + CHECKPOINT — required before re-index to prevent PK collision

- **Anti-pattern**: calling `DETACH DELETE` in a loop to remove all nodes for a repo, then immediately running `MERGE`/`CREATE` to re-index. Kuzu does not auto-flush buffered deletions to disk — the re-insert runs before the WAL is flushed and Kuzu raises `Found duplicated primary key value` because the new node conflicts with the not-yet-flushed deleted node.
- **Padrão recomendado**: call `conn.execute("CHECKPOINT")` explicitly after the last `DETACH DELETE` and **before** any `MERGE`/`CREATE`. This forces Kuzu to flush the WAL and clear the stale PK entries.
- **Recovery command**: expose a `reset_db()` method (and `reset_db` MCP tool) that: (1) closes the connection, (2) deletes the DB directory via `shutil.rmtree`, (3) creates a fresh `kuzu.Connection`, (4) calls `_ensure_schema()`, and (5) clears **all** in-memory caches. This is the operator recovery path for a corrupt/stale DB.
- **Checklist** for any batch-delete + re-insert cycle in Kuzu:
  1. Is there a `CHECKPOINT` call after the last `DETACH DELETE` and before the first `MERGE`/`CREATE`? → required.
  2. Does `reset_db()` clear **every** in-memory cache? Grep `self._*cache*` and `self._*index*` in the store class.
  3. Is there a unit test that calls `reset_db()` then re-indexes the same repo and asserts no PK collision? → required.
  4. Does `test_reset_db_wipes_and_reinitializes` assert each cache is `{}` or empty? → required.
  5. Does `_log_result_summary` in the dispatch layer have a handler for `reset_db`? → required.
- **Exemplo**:
  ```python
  # ❌ DELETE then INSERT without CHECKPOINT — raises "Found duplicated primary key value"
  def _delete_repo_data(self, root_path: str) -> None:
      for table in TABLES:
          self.conn.execute(f"MATCH (n:{table} {{root_path: $rp}}) DETACH DELETE n", {"rp": rp})
      # continues to insert immediately — WAL not flushed

  # ✅ CHECKPOINT after DELETE loop — WAL flushed, PKs clear before re-insert
  def _delete_repo_data(self, root_path: str) -> None:
      for table in TABLES:
          self.conn.execute(f"MATCH (n:{table} {{root_path: $rp}}) DETACH DELETE n", {"rp": rp})
      self.conn.execute("CHECKPOINT")   # flush WAL before any CREATE

  # ✅ reset_db — full lifecycle reset for corrupt/stale DB
  def reset_db(self) -> None:
      del self._conn
      shutil.rmtree(self._db_path, ignore_errors=True)
      self._db = kuzu.Database(self._db_path)
      self._conn = kuzu.Connection(self._db)
      self._ensure_schema()
      self._embedding_cache.clear()   # clear ALL in-memory caches
  ```
