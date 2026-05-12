"""MCP server exposing fedora-nexus tools for AI agents."""

from __future__ import annotations

import asyncio
import logging
import os as _os
import sys as _sys
import time
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d %(levelname)-8s [%(name)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=_sys.stderr,
    force=True,
)
# Force line-buffering on stderr so logs appear immediately in real-time
_sys.stderr.reconfigure(line_buffering=True)  # type: ignore[attr-defined]

from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from fedora_nexus.graph.blast_radius import blast_radius as _blast_radius
from fedora_nexus.graph.engine import DependencyGraph
from fedora_nexus.indexer.tree_sitter_indexer import TreeSitterIndexer
from fedora_nexus.store.kuzu_store import KuzuGraphStore

load_dotenv()

logger = logging.getLogger(__name__)

app = Server("fedora-nexus")

_store: KuzuGraphStore | None = None


def _get_store() -> KuzuGraphStore:
    global _store
    if _store is None:
        _store = KuzuGraphStore()
    return _store


class RepoNotFoundError(Exception):
    def __init__(self, root_path: str) -> None:
        self.root_path = root_path
        super().__init__(root_path)


_SUPPORTED_CLAUSES = ["MATCH", "WHERE", "RETURN"]  # legacy — kept for backward compat with existing tests
_UNSUPPORTED_CLAUSES = ["LIMIT", "ORDER BY", "SKIP", "WITH", "UNWIND", "CREATE", "DELETE", "SET", "MERGE"]  # legacy — kept for backward compat with existing tests
_WRITE_CLAUSES = {"CREATE", "DELETE", "SET", "MERGE", "DROP", "ALTER", "DETACH"}


def _translate_path(path: str) -> str:
    """Translate a host-side absolute path to the container mount path.

    Reads HOST_REPOS_PREFIX and CONTAINER_REPOS_PATH from the environment.
    If HOST_REPOS_PREFIX is set and *path* starts with it, the prefix is
    replaced with CONTAINER_REPOS_PATH.  Otherwise the path is returned
    unchanged (no-op in local/test environments).
    """
    host_prefix = _os.environ.get("HOST_REPOS_PREFIX", "")
    container_path = _os.environ.get("CONTAINER_REPOS_PATH", "/repos")
    if host_prefix and path.startswith(host_prefix):
        return container_path + path[len(host_prefix):]
    return path


def _get_unsupported_clauses(query: str) -> list[str]:  # legacy — kept for backward compat with existing tests
    q_upper = query.upper()
    return [c for c in _UNSUPPORTED_CLAUSES if c in q_upper]


def _require_graph(root_path: str) -> DependencyGraph:
    store = _get_store()
    graph = store.load_graph(root_path)
    if graph is None:
        raise RepoNotFoundError(root_path)
    return graph


def _run_index(root_path: str, languages: list[str] | None = None, symbol_mode: bool = False) -> DependencyGraph:
    logger.info(
        "[INDEX] starting root=%r langs=%s symbol_mode=%s",
        root_path,
        languages or "all",
        symbol_mode,
    )
    t0 = time.perf_counter()
    graph = TreeSitterIndexer(languages=languages).index(root_path, symbol_mode=symbol_mode)
    t_index = time.perf_counter()
    logger.info("[INDEX] parse done in %.3fs — saving to DB ...", t_index - t0)
    _get_store().save_graph(root_path, graph)
    t_save = time.perf_counter()
    data = graph.to_adjacency_json()
    logger.info(
        "[INDEX] saved in %.3fs — total=%.3fs nodes=%d edges=%d",
        t_save - t_index,
        t_save - t0,
        len(data["nodes"]),
        len(data["edges"]),
    )
    return graph


def _error(msg: str, code: str = "ERROR") -> dict[str, Any]:
    return {"error": msg, "code": code}


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="index_repo",
            description=(
                "Index a repository and persist its dependency graph to the database. "
                "Supports Python, TypeScript, JavaScript, and Ruby. "
                "Pass force_reindex=true to re-scan an already-indexed repo."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "root_path": {
                        "type": "string",
                        "description": "Absolute path to the repository root",
                    },
                    "languages": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["python", "typescript", "javascript", "ruby"],
                        },
                        "description": (
                            "Languages to index. This is a permanent filter: only files of the specified languages "
                            "are scanned and persisted. Omit to index all supported languages (python, typescript, javascript, ruby). "
                            "WARNING: using force_reindex=true with a subset of languages will permanently remove "
                            "previously indexed nodes of other languages from this repo's graph."
                        ),
                    },
                    "force_reindex": {
                        "type": "boolean",
                        "default": False,
                        "description": "Force re-indexing even if the repo is already indexed",
                    },
                    "with_symbols": {
                        "type": "boolean",
                        "default": False,
                        "description": "When true, index symbols (functions, classes) in addition to files. Default false for compatibility.",
                    },
                },
                "required": ["root_path"],
            },
        ),
        Tool(
            name="get_dependencies",
            description=(
                "Return files that a given file imports/depends on. "
                "depth=1 returns direct deps only; depth>1 returns transitive deps up to that level."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "root_path": {"type": "string"},
                    "file_path": {
                        "type": "string",
                        "description": "Relative path within the repo (e.g. src/foo/bar.py)",
                    },
                    "depth": {"type": "integer", "default": 1, "minimum": 1},
                },
                "required": ["root_path", "file_path"],
            },
        ),
        Tool(
            name="get_dependents",
            description=(
                "Return files that import/depend on the given file (reverse lookup). "
                "depth=1 returns direct dependents; depth>1 returns transitive dependents."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "root_path": {"type": "string"},
                    "file_path": {"type": "string"},
                    "depth": {"type": "integer", "default": 1, "minimum": 1},
                },
                "required": ["root_path", "file_path"],
            },
        ),
        Tool(
            name="blast_radius",
            description=(
                "BFS over reverse dependency edges. "
                "Returns every file that would be affected by changes to the specified files, "
                "with per-file depth distance from the changed set."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "root_path": {"type": "string"},
                    "changed_files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Relative paths of changed files",
                    },
                    "max_depth": {"type": "integer", "default": 10, "minimum": 1},
                },
                "required": ["root_path", "changed_files"],
            },
        ),
        Tool(
            name="query_graph",
            description=(
                "Execute a native Kuzu Cypher query against the dependency graph. "
                "Full Kuzu Cypher is supported: MATCH, WHERE, RETURN, WITH, "
                "OPTIONAL MATCH, UNION ALL, ORDER BY, LIMIT, SKIP, IN [...], "
                "collect(), count(), and property predicates (CONTAINS, STARTS WITH, ENDS WITH, =). "
                "Read-only: CREATE/DELETE/SET/MERGE/DROP/ALTER are blocked. "
                "Node tables: File, Class (also stores modules, concerns, db_tables — filter with kind='db_table'), "
                "Function, Method. "
                "Relationship: CodeRelation with type DEPENDS_ON | CONTAINS | CALLS | INHERITS. "
                "All node IDs are prefixed with root_path:: — use file_path or name for human-readable filters. "
                "Examples: "
                "MATCH (f:File)-[r:CodeRelation]->(c:Class) WHERE c.kind='db_table' AND f.root_path=$root RETURN c.name, c.content LIMIT 30 ; "
                "MATCH (a:Class {name:'Course'})-[r:CodeRelation {type:'DEPENDS_ON'}]->(b:Class) RETURN b.name, b.kind ; "
                "MATCH (t:Class {kind:'db_table'})-[r:CodeRelation {type:'DEPENDS_ON'}]->(m:Class {kind:'db_table'}) RETURN t.name AS from_table, m.name AS to_table"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "root_path": {"type": "string"},
                    "cypher": {"type": "string"},
                },
                "required": ["root_path", "cypher"],
            },
        ),
        Tool(
            name="get_graph",
            description=(
                "Return the full dependency graph as adjacency JSON (nodes + edges). "
                "Pass subgraph_paths to get a subgraph of specific nodes."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "root_path": {"type": "string"},
                    "subgraph_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "If provided, returns only the subgraph of these nodes",
                    },
                },
                "required": ["root_path"],
            },
        ),
        Tool(
            name="list_repos",
            description=(
                "List all repositories that have been indexed, "
                "with node/edge counts and last-indexed timestamp."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="delete_repo",
            description="Remove a repository and its entire dependency graph from the database.",
            inputSchema={
                "type": "object",
                "properties": {
                    "root_path": {"type": "string"},
                },
                "required": ["root_path"],
            },
        ),
        Tool(
            name="reset_db",
            description="Wipe the entire database and reinitialize it. Use when the DB is corrupted or to start fresh. All indexed repos will be lost — re-run index_repo afterwards.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="search",
            description=(
                "Hybrid BM25 + semantic search (RRF fusion) across all indexed symbols "
                "(files, functions, classes, methods). "
                "Use the optional kind filter to restrict results to a specific symbol type."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "root_path": {"type": "string"},
                    "query": {
                        "type": "string",
                        "description": "Search query (keywords, function names, file paths, etc.)",
                    },
                    "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
                    "kind": {
                        "type": "string",
                        "enum": ["function", "class", "method", "class_method", "file", "db_table"],
                        "description": "Optional: restrict results to this symbol type only.",
                    },
                },
                "required": ["root_path", "query"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    import json

    result = await _dispatch(name, arguments)
    return [TextContent(type="text", text=json.dumps(result, default=str))]


def _log_result_summary(name: str, result: dict, elapsed: float) -> None:
    if name == "index_repo":
        logger.info(
            "[TOOL] index_repo done in %.3fs — status=%s nodes=%s edges=%s",
            elapsed,
            result.get("status"),
            result.get("nodes"),
            result.get("edges"),
        )
    elif name in ("get_dependencies", "get_dependents"):
        logger.info(
            "[TOOL] %s done in %.3fs — file=%s depth=%s count=%s",
            name,
            elapsed,
            result.get("file"),
            result.get("depth"),
            result.get("count"),
        )
    elif name == "blast_radius":
        affected = result.get("affected", [])
        logger.info(
            "[TOOL] blast_radius done in %.3fs — affected=%d nodes",
            elapsed,
            len(affected) if isinstance(affected, list) else 0,
        )
    elif name == "query_graph":
        logger.info(
            "[TOOL] query_graph done in %.3fs — results=%s",
            elapsed,
            result.get("count"),
        )
    elif name == "get_graph":
        nodes = result.get("nodes", [])
        edges = result.get("edges", [])
        logger.info(
            "[TOOL] get_graph done in %.3fs — nodes=%d edges=%d",
            elapsed,
            len(nodes),
            len(edges),
        )
    elif name == "list_repos":
        logger.info(
            "[TOOL] list_repos done in %.3fs — count=%s",
            elapsed,
            result.get("count"),
        )
    elif name == "delete_repo":
        logger.info(
            "[TOOL] delete_repo done in %.3fs — status=%s",
            elapsed,
            result.get("status"),
        )
    else:
        logger.info("[TOOL] %s done in %.3fs", name, elapsed)


async def _dispatch(name: str, args: dict[str, Any]) -> Any:
    # Redact large/verbose args for the log line
    log_args = {k: v for k, v in args.items() if k not in ("cypher",)}
    logger.info("[TOOL] %s args=%s", name, log_args)
    t0 = time.perf_counter()
    try:
        if name == "index_repo":
            with_symbols = args.get("with_symbols", False)
            result = await asyncio.to_thread(
                _tool_index_repo,
                args["root_path"],
                args.get("languages"),
                bool(args.get("force_reindex", False)),
                bool(with_symbols),
            )
        elif name == "get_dependencies":
            result = await asyncio.to_thread(
                _tool_get_dependencies,
                args["root_path"], args["file_path"], int(args.get("depth", 1))
            )
        elif name == "get_dependents":
            result = await asyncio.to_thread(
                _tool_get_dependents,
                args["root_path"], args["file_path"], int(args.get("depth", 1))
            )
        elif name == "blast_radius":
            result = await asyncio.to_thread(
                _tool_blast_radius,
                args["root_path"], args["changed_files"], int(args.get("max_depth", 10))
            )
        elif name == "query_graph":
            logger.info("[TOOL] query_graph cypher=%r", args.get("cypher", ""))
            result = await asyncio.to_thread(
                _tool_query_graph,
                args["root_path"], args["cypher"]
            )
        elif name == "get_graph":
            result = await asyncio.to_thread(
                _tool_get_graph,
                args["root_path"], args.get("subgraph_paths")
            )
        elif name == "list_repos":
            result = await asyncio.to_thread(_tool_list_repos)
        elif name == "delete_repo":
            result = await asyncio.to_thread(_tool_delete_repo, args["root_path"])
        elif name == "reset_db":
            result = await asyncio.to_thread(_tool_reset_db)
        elif name == "search":
            result = await asyncio.to_thread(
                _tool_search,
                args["root_path"], args["query"], int(args.get("limit", 20)),
                args.get("kind") or None,
            )
        else:
            result = _error(f"Unknown tool: {name}", "UNKNOWN_TOOL")

        elapsed = time.perf_counter() - t0
        if "error" in result:
            logger.warning("[TOOL] %s failed in %.3fs: %s", name, elapsed, result.get("error"))
        else:
            # Log a concise summary based on tool
            _log_result_summary(name, result, elapsed)
        return result
    except RepoNotFoundError as exc:
        elapsed = time.perf_counter() - t0
        logger.warning("[TOOL] %s → REPO_NOT_FOUND %r (%.3fs)", name, exc.root_path, elapsed)
        return {"error": f"Repo not indexed: {exc.root_path}. Call index_repo first.", "code": "REPO_NOT_FOUND", "root_path": exc.root_path}
    except KeyError as exc:
        elapsed = time.perf_counter() - t0
        logger.warning("[TOOL] %s → MISSING_PARAM %s (%.3fs)", name, exc, elapsed)
        return _error(f"Missing required parameter: {exc}", "MISSING_PARAM")
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        logger.exception("[TOOL] %s → EXCEPTION in %.3fs", name, elapsed)
        return _error(str(exc), "TOOL_ERROR")


# ------------------------------------------------------------------
# Tool implementations
# ------------------------------------------------------------------


def _tool_index_repo(
    root_path: str,
    languages: list[str] | None,
    force_reindex: bool,
    symbol_mode: bool = False,
) -> dict:
    root_path = _translate_path(root_path)
    if not _os.path.isdir(root_path):
        return _error(
            f"Directory not found: {root_path}. "
            "Check that HOST_REPOS_PREFIX and CONTAINER_REPOS_PATH are set correctly "
            "and the volume is mounted.",
            "NOT_FOUND",
        )
    store = _get_store()
    if not force_reindex and store.repo_exists(root_path):
        graph = store.load_graph(root_path)
        assert graph is not None
        data = graph.to_adjacency_json()
        lang_counts: dict[str, int] = {}
        for node in data["nodes"]:
            lang = node.get("language", "unknown")
            lang_counts[lang] = lang_counts.get(lang, 0) + 1
        return {
            "status": "already_indexed",
            "nodes": len(data["nodes"]),
            "edges": len(data["edges"]),
            "languages": lang_counts,
            "indexed_at": store.get_indexed_at(root_path),
            "hint": "Pass force_reindex=true to re-scan.",
        }

    graph = _run_index(root_path, languages, symbol_mode=symbol_mode)
    data = graph.to_adjacency_json()
    lang_counts = {}
    for node in data["nodes"]:
        lang = node.get("language", "unknown")
        lang_counts[lang] = lang_counts.get(lang, 0) + 1
    return {
        "status": "indexed",
        "root_path": root_path,
        "nodes": len(data["nodes"]),
        "edges": len(data["edges"]),
        "languages": lang_counts,
        "indexed_at": store.get_indexed_at(root_path),
    }


def _tool_get_dependencies(root_path: str, file_path: str, depth: int) -> dict:
    root_path = _translate_path(root_path)
    graph = _require_graph(root_path)
    if not graph.has_node(file_path):
        return _error(f"File not in graph: {file_path}", "NOT_FOUND")
    deps = (
        graph.get_dependencies(file_path)
        if depth == 1
        else graph.get_transitive_dependencies(file_path, depth)
    )
    return {"file": file_path, "depth": depth, "dependencies": deps, "count": len(deps)}


def _tool_get_dependents(root_path: str, file_path: str, depth: int) -> dict:
    root_path = _translate_path(root_path)
    graph = _require_graph(root_path)
    if not graph.has_node(file_path):
        return _error(f"File not in graph: {file_path}", "NOT_FOUND")
    deps = (
        graph.get_dependents(file_path)
        if depth == 1
        else graph.get_transitive_dependents(file_path, depth)
    )
    return {"file": file_path, "depth": depth, "dependents": deps, "count": len(deps)}


def _tool_blast_radius(root_path: str, changed_files: list[str], max_depth: int) -> dict:
    root_path = _translate_path(root_path)
    graph = _require_graph(root_path)
    return _blast_radius(graph, changed_files, max_depth)


def _tool_query_graph(root_path: str, cypher_query: str) -> dict:
    rp = _translate_path(root_path)
    if not _get_store().repo_exists(rp):
        raise RepoNotFoundError(rp)
    upper = cypher_query.upper()
    blocked = [c for c in _WRITE_CLAUSES if c in upper.split()]
    if blocked:
        return _error(f"Write clauses not allowed: {blocked}", "QUERY_NOT_ALLOWED")
    try:
        rows = _get_store().execute_cypher(cypher_query)
        return {"rows": rows, "count": len(rows)}
    except Exception as exc:
        return _error(str(exc), "QUERY_ERROR")


def _tool_search(root_path: str, query: str, limit: int = 20, kind: str | None = None) -> dict:
    rp = _translate_path(root_path)
    if not _os.path.isdir(rp) and not _get_store().repo_exists(rp):
        return _error(f"Repo not indexed: {rp}. Call index_repo first.", "REPO_NOT_FOUND")
    results = _get_store().search(rp, query, limit, kind=kind)
    return {"results": results, "count": len(results), "query": query}


def _tool_get_graph(root_path: str, subgraph_paths: list[str] | None) -> dict:
    root_path = _translate_path(root_path)
    graph = _require_graph(root_path)
    if subgraph_paths:
        return graph.subgraph(subgraph_paths)
    return graph.to_adjacency_json()


def _tool_list_repos() -> dict:
    repos = _get_store().list_repos()
    return {"repos": repos, "count": len(repos)}


def _tool_delete_repo(root_path: str) -> dict:
    root_path = _translate_path(root_path)
    deleted = _get_store().delete_repo(root_path)
    if deleted:
        return {"status": "deleted", "root_path": root_path}
    return _error(f"Repo not found: {root_path}", "NOT_FOUND")


def _tool_reset_db() -> dict:
    _get_store().reset_db()
    return {
        "status": "ok",
        "message": "Database wiped and reinitialized. Re-run index_repo for each repository.",
    }


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------


def _run_http() -> None:
    try:
        from mcp.server.sse import SseServerTransport
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.responses import JSONResponse
        from starlette.routing import Mount, Route
        import uvicorn
    except ImportError as e:
        logger.error(
            "HTTP mode requires starlette and uvicorn: "
            "pip install starlette uvicorn mcp[cli] — %s", e
        )
        raise SystemExit(1)

    port = int(_os.environ.get("FEDORA_NEXUS_HTTP_PORT", "7832"))
    sse = SseServerTransport("/messages/")

    class SseHandler:
        """ASGI app for the /sse endpoint.

        Using a class (not a plain function) bypasses Starlette's
        request_response() wrapper, which would try to call the return value as
        a Response. connect_sse() sends the HTTP response directly via `send`,
        so the handler must not return a Response — only ASGI apps (classes)
        are allowed to do that in Starlette 1.x.
        """

        async def __call__(self, scope, receive, send):
            async with sse.connect_sse(scope, receive, send) as streams:
                await app.run(streams[0], streams[1], app.create_initialization_options())

    async def handle_call(request: Request) -> JSONResponse:
        """JSON-RPC style endpoint used by the fedora-nexus CLI.

        Request body: {"tool": "<name>", "args": {...}}
        Response:     the same JSON dict that the MCP tool would return.
        """
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body", "code": "BAD_REQUEST"}, status_code=400)
        tool_name = body.get("tool", "")
        args = body.get("args", {})
        if not tool_name:
            return JSONResponse({"error": "Missing 'tool' field", "code": "BAD_REQUEST"}, status_code=400)
        result = await _dispatch(tool_name, args)
        status = 400 if "error" in result else 200
        return JSONResponse(result, status_code=status)

    async def handle_health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    starlette_app = Starlette(
        routes=[
            Route("/sse", endpoint=SseHandler()),
            Mount("/messages/", app=sse.handle_post_message),
            Route("/call", endpoint=handle_call, methods=["POST"]),
            Route("/health", endpoint=handle_health, methods=["GET"]),
        ]
    )
    logger.info("Starting HTTP/SSE MCP server on port %d", port)
    uvicorn.run(starlette_app, host="0.0.0.0", port=port, log_level="info")


async def _run_stdio() -> None:
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


def main() -> None:
    import sys
    logger.info("fedora-nexus MCP server starting — PID=%d", _os.getpid())
    try:
        _get_store().init_schema()
        logger.info("DB schema ready")
    except Exception as exc:
        logger.warning("DB init failed (will retry on first use): %s", exc)
    if len(sys.argv) > 1 and sys.argv[1] == "--http":
        _run_http()
    else:
        asyncio.run(_run_stdio())


if __name__ == "__main__":
    main()
