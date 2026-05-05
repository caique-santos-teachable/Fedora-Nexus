"""CLI interface for fedora-nexus — thin HTTP client for the fedora-nexus container server.

Mode selection (in priority order):
  1. FEDORA_NEXUS_SERVER_URL env var is set  → HTTP client mode (talks to container)
  2. Server is reachable at http://localhost:7832  → HTTP client mode (auto-detect)
  3. Neither                              → local in-process mode (direct DB access)

Every subcommand outputs newline-terminated JSON to stdout.
Exit code 0 = success, 1 = error (the JSON body also contains an "error" key).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

import click

_DEFAULT_SERVER_URL = "http://localhost:7832"


def _server_url() -> str | None:
    """Return the server URL to use, or None if local mode should be used."""
    from_env = os.environ.get("FEDORA_NEXUS_SERVER_URL", "").rstrip("/")
    if from_env:
        return from_env
    # Auto-detect: try a cheap /health check against the default port
    try:
        with urlopen(_DEFAULT_SERVER_URL + "/health", timeout=1) as resp:
            if resp.status == 200:
                return _DEFAULT_SERVER_URL
    except Exception:
        pass
    return None


def _http_call(server_url: str, tool: str, args: dict) -> dict:
    """POST {"tool": tool, "args": args} to the server's /call endpoint."""
    payload = json.dumps({"tool": tool, "args": args}).encode()
    req = Request(
        f"{server_url}/call",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=None) as resp:
            return json.loads(resp.read())
    except URLError as exc:
        return {"error": f"Cannot reach fedora-nexus server at {server_url}: {exc.reason}", "code": "SERVER_UNREACHABLE"}
    except Exception as exc:
        return {"error": str(exc), "code": "TOOL_ERROR"}


def _local_call(tool: str, args: dict) -> dict:
    """Call the tool in-process (local DB, no server required)."""
    # Lazy import so server-side heavy deps are only loaded in local mode
    from fedora_nexus.mcp.server import (  # noqa: PLC2701
        _tool_blast_radius,
        _tool_delete_repo,
        _tool_get_dependencies,
        _tool_get_dependents,
        _tool_get_graph,
        _tool_index_repo,
        _tool_list_repos,
        _tool_query_graph,
        _tool_search,
    )
    _DISPATCH = {
        "index_repo":       lambda a: _tool_index_repo(a["root_path"], a.get("languages"), bool(a.get("force_reindex", False)), bool(a.get("with_symbols", False))),
        "get_dependencies": lambda a: _tool_get_dependencies(a["root_path"], a["file_path"], int(a.get("depth", 1))),
        "get_dependents":   lambda a: _tool_get_dependents(a["root_path"], a["file_path"], int(a.get("depth", 1))),
        "blast_radius":     lambda a: _tool_blast_radius(a["root_path"], a["changed_files"], int(a.get("max_depth", 10))),
        "query_graph":      lambda a: _tool_query_graph(a["root_path"], a["cypher"]),
        "get_graph":        lambda a: _tool_get_graph(a["root_path"], a.get("subgraph_paths")),
        "list_repos":       lambda a: _tool_list_repos(),
        "delete_repo":      lambda a: _tool_delete_repo(a["root_path"]),
        "search":           lambda a: _tool_search(a["root_path"], a["query"], int(a.get("limit", 20))),
    }
    fn = _DISPATCH.get(tool)
    if fn is None:
        return {"error": f"Unknown tool: {tool}", "code": "UNKNOWN_TOOL"}
    try:
        return fn(args)
    except Exception as exc:
        return {"error": str(exc), "code": "TOOL_ERROR"}


def _call(tool: str, args: dict, *, server_url: str | None) -> dict:
    """Route to HTTP or local mode."""
    if server_url:
        return _http_call(server_url, tool, args)
    return _local_call(tool, args)


def _emit(data: Any, *, pretty: bool) -> None:
    click.echo(json.dumps(data, default=str, indent=2 if pretty else None))


def _finish(data: dict, *, pretty: bool) -> None:
    _emit(data, pretty=pretty)
    if "error" in data:
        sys.exit(1)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--pretty",
    is_flag=True,
    default=False,
    help="Pretty-print JSON output (human-readable). Compact by default for agent consumption.",
)
@click.option(
    "--server",
    envvar="FEDORA_NEXUS_SERVER_URL",
    default=None,
    metavar="URL",
    help="fedora-nexus server URL (e.g. http://localhost:7832). Auto-detected if omitted. "
         "Set FEDORA_NEXUS_SERVER_URL to force a specific server.",
)
@click.pass_context
def main(ctx: click.Context, pretty: bool, server: str | None) -> None:
    """fedora-nexus — code dependency graph CLI for AI agents.

    Every command outputs JSON to stdout.
    Exit code 0 = success, 1 = error.

    By default the CLI talks to the fedora-nexus container server running on
    localhost:7832. If the server is unreachable it falls back to local
    in-process mode (requires a local database).

    \b
    Quick start (server mode):
        docker compose up -d mcp-server
        fedora-nexus index /path/to/repo
        fedora-nexus blast-radius /path/to/repo src/auth.py

    \b
    Force server URL:
        FEDORA_NEXUS_SERVER_URL=http://my-host:7832 fedora-nexus list
    """
    ctx.ensure_object(dict)
    ctx.obj["pretty"] = pretty
    # Resolve server URL once per invocation
    resolved = server.rstrip("/") if server else _server_url()
    ctx.obj["server_url"] = resolved


# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------


@main.command("index")
@click.argument("root_path")
@click.option(
    "--languages",
    "-l",
    multiple=True,
    type=click.Choice(["python", "typescript", "javascript", "ruby"], case_sensitive=False),
    help="Language to index (repeat flag for multiple). Default: all supported languages.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Re-index even if the repo is already indexed.",
)
@click.option(
    "--with-symbols",
    is_flag=True,
    default=False,
    help="Also index symbols (functions, classes, methods) in addition to files.",
)
@click.pass_context
def cmd_index(
    ctx: click.Context,
    root_path: str,
    languages: tuple[str, ...],
    force: bool,
    with_symbols: bool,
) -> None:
    """Index ROOT_PATH and persist its dependency graph to the database."""
    result = _call("index_repo", {
        "root_path": root_path,
        "languages": list(languages) or None,
        "force_reindex": force,
        "with_symbols": with_symbols,
    }, server_url=ctx.obj["server_url"])
    _finish(result, pretty=ctx.obj["pretty"])


# ---------------------------------------------------------------------------
# deps
# ---------------------------------------------------------------------------


@main.command("deps")
@click.argument("root_path")
@click.argument("file_path")
@click.option(
    "--depth",
    "-d",
    default=1,
    type=int,
    show_default=True,
    help="Traversal depth. 1 = direct imports only; >1 = transitive.",
)
@click.pass_context
def cmd_deps(
    ctx: click.Context, root_path: str, file_path: str, depth: int
) -> None:
    """Return files that FILE_PATH imports/depends on."""
    result = _call("get_dependencies", {
        "root_path": root_path, "file_path": file_path, "depth": depth,
    }, server_url=ctx.obj["server_url"])
    _finish(result, pretty=ctx.obj["pretty"])


# ---------------------------------------------------------------------------
# dependents
# ---------------------------------------------------------------------------


@main.command("dependents")
@click.argument("root_path")
@click.argument("file_path")
@click.option(
    "--depth",
    "-d",
    default=1,
    type=int,
    show_default=True,
    help="Traversal depth. 1 = direct dependents only; >1 = transitive.",
)
@click.pass_context
def cmd_dependents(
    ctx: click.Context, root_path: str, file_path: str, depth: int
) -> None:
    """Return files that import/depend on FILE_PATH (reverse lookup)."""
    result = _call("get_dependents", {
        "root_path": root_path, "file_path": file_path, "depth": depth,
    }, server_url=ctx.obj["server_url"])
    _finish(result, pretty=ctx.obj["pretty"])


# ---------------------------------------------------------------------------
# blast-radius
# ---------------------------------------------------------------------------


@main.command("blast-radius")
@click.argument("root_path")
@click.argument("changed_files", nargs=-1, required=True)
@click.option(
    "--max-depth",
    default=10,
    type=int,
    show_default=True,
    help="Maximum BFS traversal depth.",
)
@click.pass_context
def cmd_blast_radius(
    ctx: click.Context,
    root_path: str,
    changed_files: tuple[str, ...],
    max_depth: int,
) -> None:
    """Return every file affected by changes to CHANGED_FILES (BFS over reverse edges)."""
    result = _call("blast_radius", {
        "root_path": root_path, "changed_files": list(changed_files), "max_depth": max_depth,
    }, server_url=ctx.obj["server_url"])
    _finish(result, pretty=ctx.obj["pretty"])


# ---------------------------------------------------------------------------
# query
# ---------------------------------------------------------------------------


@main.command("query")
@click.argument("root_path")
@click.argument("cypher")
@click.pass_context
def cmd_query(ctx: click.Context, root_path: str, cypher: str) -> None:
    """Execute a read-only Cypher query against the dependency graph.

    \b
    Example:
        fedora-nexus query /path/to/repo "MATCH (f:File) WHERE f.path CONTAINS 'auth' RETURN f"
        fedora-nexus query /path/to/repo "MATCH (c:Class {name:'UserService'})-[r:CodeRelation]->(m:Method) RETURN m.name"
    """
    result = _call("query_graph", {
        "root_path": root_path, "cypher": cypher,
    }, server_url=ctx.obj["server_url"])
    _finish(result, pretty=ctx.obj["pretty"])


# ---------------------------------------------------------------------------
# graph
# ---------------------------------------------------------------------------


@main.command("graph")
@click.argument("root_path")
@click.option(
    "--subgraph",
    "-s",
    multiple=True,
    metavar="FILE_PATH",
    help="Restrict output to the subgraph of these file paths (repeat for multiple).",
)
@click.pass_context
def cmd_graph(
    ctx: click.Context, root_path: str, subgraph: tuple[str, ...]
) -> None:
    """Return the full dependency graph as adjacency JSON (nodes + edges)."""
    result = _call("get_graph", {
        "root_path": root_path,
        "subgraph_paths": list(subgraph) or None,
    }, server_url=ctx.obj["server_url"])
    _finish(result, pretty=ctx.obj["pretty"])


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@main.command("list")
@click.pass_context
def cmd_list(ctx: click.Context) -> None:
    """List all indexed repositories with node/edge counts and last-indexed timestamp."""
    result = _call("list_repos", {}, server_url=ctx.obj["server_url"])
    _finish(result, pretty=ctx.obj["pretty"])


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


@main.command("delete")
@click.argument("root_path")
@click.pass_context
def cmd_delete(ctx: click.Context, root_path: str) -> None:
    """Remove ROOT_PATH and its entire dependency graph from the database."""
    result = _call("delete_repo", {"root_path": root_path}, server_url=ctx.obj["server_url"])
    _finish(result, pretty=ctx.obj["pretty"])


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


@main.command("search")
@click.argument("root_path")
@click.argument("query")
@click.option(
    "--limit",
    "-n",
    default=20,
    type=int,
    show_default=True,
    help="Maximum number of BM25-ranked results to return.",
)
@click.pass_context
def cmd_search(
    ctx: click.Context, root_path: str, query: str, limit: int
) -> None:
    """Full-text BM25 search across all indexed symbols (files, functions, classes, methods)."""
    result = _call("search", {
        "root_path": root_path, "query": query, "limit": limit,
    }, server_url=ctx.obj["server_url"])
    _finish(result, pretty=ctx.obj["pretty"])


# ---------------------------------------------------------------------------
# server management helpers
# ---------------------------------------------------------------------------

def _compose_file() -> Path:
    """Return the absolute path to the docker-compose.yml bundled with fedora-nexus."""
    # Walk up from this file's location to find docker-compose.yml
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidate = parent / "docker-compose.yml"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "docker-compose.yml not found. "
        "Make sure you installed fedora-nexus from the repository, not a wheel."
    )


def _docker_compose(*args: str) -> int:
    """Run `docker compose` with the fedora-nexus compose file, streaming output."""
    try:
        compose = _compose_file()
    except FileNotFoundError as exc:
        click.echo(f"Error: {exc}", err=True)
        return 1
    cmd = ["docker", "compose", "-f", str(compose), *args]
    result = subprocess.run(cmd)
    return result.returncode


# ---------------------------------------------------------------------------
# server start
# ---------------------------------------------------------------------------


@main.command("server-start")
@click.option(
    "--build",
    is_flag=True,
    default=False,
    help="Rebuild the image before starting.",
)
def cmd_server_start(build: bool) -> None:
    """Start the fedora-nexus container server (docker compose up).

    The server exposes port 7832. Once running, the CLI automatically routes
    all commands through it instead of the local database.
    """
    args = ["up", "-d", "mcp-server"]
    if build:
        args.insert(1, "--build")
    rc = _docker_compose(*args)
    if rc == 0:
        click.echo("fedora-nexus server started. Run 'fedora-nexus list' to verify.")
    sys.exit(rc)


# ---------------------------------------------------------------------------
# server stop
# ---------------------------------------------------------------------------


@main.command("server-stop")
def cmd_server_stop() -> None:
    """Stop the fedora-nexus container server (docker compose stop)."""
    rc = _docker_compose("stop", "mcp-server")
    sys.exit(rc)


# ---------------------------------------------------------------------------
# server remove
# ---------------------------------------------------------------------------


@main.command("server-remove")
@click.option(
    "--volumes",
    is_flag=True,
    default=False,
    help="Also remove the persistent data volume (WARNING: deletes all indexed graphs).",
)
def cmd_server_remove(volumes: bool) -> None:
    """Remove the fedora-nexus container (docker compose down).

    By default the data volume is kept so indexed graphs survive the removal.
    Pass --volumes to wipe everything including the database.
    """
    args = ["down", "mcp-server"]
    if volumes:
        args.append("--volumes")
    rc = _docker_compose(*args)
    sys.exit(rc)


# ---------------------------------------------------------------------------
# server status
# ---------------------------------------------------------------------------


@main.command("server-status")
@click.pass_context
def cmd_server_status(ctx: click.Context) -> None:
    """Show whether the fedora-nexus server is reachable and return its /health response."""
    url = ctx.obj.get("server_url") or _DEFAULT_SERVER_URL
    try:
        with urlopen(f"{url}/health", timeout=3) as resp:
            body = json.loads(resp.read())
            body["url"] = url
            body["reachable"] = True
            _emit(body, pretty=ctx.obj["pretty"])
    except Exception as exc:
        _emit({"reachable": False, "url": url, "error": str(exc)}, pretty=ctx.obj["pretty"])
