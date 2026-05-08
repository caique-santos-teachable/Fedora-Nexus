"""Unit tests for MCP server tool implementations (no DB required)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from depgraph.graph.engine import DependencyGraph
from depgraph.mcp.server import (
    RepoNotFoundError,
    _SUPPORTED_CLAUSES,
    _get_unsupported_clauses,
    _require_graph,
    _tool_get_graph,
    _tool_index_repo,
    _tool_query_graph,
    _tool_reset_db,
    _tool_search,
    _translate_path,
)


def make_graph() -> DependencyGraph:
    g = DependencyGraph()
    g.add_node("a.py", language="python")
    g.add_node("b.py", language="python")
    g.add_edge("a.py", "b.py")
    return g


# TC-01: delete + access → REPO_NOT_FOUND
def test_require_graph_raises_when_repo_deleted():
    mock_store = MagicMock()
    mock_store.load_graph.return_value = None

    with patch("depgraph.mcp.server._get_store", return_value=mock_store):
        with pytest.raises(RepoNotFoundError) as exc_info:
            _require_graph("/deleted/repo")
    assert exc_info.value.root_path == "/deleted/repo"


# TC-03: subgraph_paths with missing path → missing_paths reported
def test_get_graph_subgraph_reports_missing_paths():
    g = make_graph()
    mock_store = MagicMock()
    mock_store.load_graph.return_value = g

    with patch("depgraph.mcp.server._get_store", return_value=mock_store):
        result = _tool_get_graph("/some/repo", ["a.py", "nonexistent.py"])

    assert "missing_paths" in result
    assert "nonexistent.py" in result["missing_paths"]
    node_ids = {n["id"] for n in result["nodes"]}
    assert "a.py" in node_ids


# TC-05: query_graph LIMIT — now allowed via native Cypher (returns rows, not blocked error)
def test_query_graph_limit_now_allowed_with_native_cypher():
    mock_store = MagicMock()
    mock_store.repo_exists.return_value = True
    mock_store.execute_cypher.return_value = [{"path": "a.py"}]

    with patch("depgraph.mcp.server._get_store", return_value=mock_store):
        result = _tool_query_graph("/some/repo", "MATCH (n:File) RETURN n.file_path LIMIT 1")

    assert "rows" in result
    assert result["count"] == 1


def test_get_unsupported_clauses_detects_limit():
    assert "LIMIT" in _get_unsupported_clauses("MATCH (n) RETURN n LIMIT 10")


def test_get_unsupported_clauses_empty_for_valid_query():
    assert _get_unsupported_clauses("MATCH (n) WHERE n.path CONTAINS 'foo' RETURN n") == []


def test_index_repo_with_symbols_forwarded_to_run_index():
    """with_symbols=True in args must be passed as symbol_mode=True to _run_index."""
    mock_store = MagicMock()
    mock_store.repo_exists.return_value = False
    mock_graph = MagicMock()
    mock_graph.to_adjacency_json.return_value = {"nodes": [], "edges": []}

    with patch("depgraph.mcp.server._get_store", return_value=mock_store), \
         patch("depgraph.mcp.server._run_index", return_value=mock_graph) as mock_run_index, \
         patch("depgraph.mcp.server._os.path.isdir", return_value=True):
        _tool_index_repo("/some/repo", None, False, symbol_mode=True)

    call_kwargs = mock_run_index.call_args
    # symbol_mode=True must be forwarded
    assert call_kwargs.kwargs.get("symbol_mode") is True or (
        len(call_kwargs.args) >= 3 and call_kwargs.args[2] is True
    )


def test_index_repo_directory_not_found_returns_not_found_error():
    """When the translated path does not exist, return an error with code NOT_FOUND."""
    with patch("depgraph.mcp.server._translate_path", return_value="/nonexistent/path/xyz"):
        result = _tool_index_repo("/host/path/repo", None, False)

    assert result.get("code") == "NOT_FOUND"
    assert "Directory not found" in result.get("error", "")
    assert "HOST_REPOS_PREFIX" in result.get("error", "")


def test_dispatch_logs_tool_call(caplog):
    """_dispatch must emit a [TOOL] log at INFO level for every tool call."""
    import asyncio
    import logging

    mock_store = MagicMock()
    mock_store.load_graph.return_value = make_graph()

    with patch("depgraph.mcp.server._get_store", return_value=mock_store), \
         caplog.at_level(logging.INFO, logger="depgraph.mcp.server"):
        from depgraph.mcp.server import _dispatch
        asyncio.run(_dispatch("get_dependencies", {"root_path": "/repo", "file_path": "a.py"}))

    assert any("[TOOL] get_dependencies" in r.message for r in caplog.records)


def test_dispatch_logs_result_summary(caplog):
    """_dispatch must emit a result summary log with elapsed time."""
    import asyncio
    import logging

    mock_store = MagicMock()
    mock_store.load_graph.return_value = make_graph()

    with patch("depgraph.mcp.server._get_store", return_value=mock_store), \
         caplog.at_level(logging.INFO, logger="depgraph.mcp.server"):
        from depgraph.mcp.server import _dispatch
        asyncio.run(_dispatch("get_dependencies", {"root_path": "/repo", "file_path": "a.py"}))

    done_logs = [r for r in caplog.records if "done in" in r.message]
    assert len(done_logs) >= 1


# ------------------------------------------------------------------
# _translate_path unit tests
# ------------------------------------------------------------------

def test_translate_path_no_op_when_prefix_unset(monkeypatch):
    monkeypatch.delenv("HOST_REPOS_PREFIX", raising=False)
    assert _translate_path("/Users/testuser/code/myrepo") == "/Users/testuser/code/myrepo"


def test_translate_path_replaces_prefix_when_set(monkeypatch):
    monkeypatch.setenv("HOST_REPOS_PREFIX", "/Users/testuser/code")
    monkeypatch.setenv("CONTAINER_REPOS_PATH", "/repos")
    assert _translate_path("/Users/testuser/code/v0/fedora") == "/repos/v0/fedora"


def test_translate_path_no_op_when_prefix_not_matched(monkeypatch):
    monkeypatch.setenv("HOST_REPOS_PREFIX", "/Users/testuser/code")
    monkeypatch.setenv("CONTAINER_REPOS_PATH", "/repos")
    assert _translate_path("/other/path/myrepo") == "/other/path/myrepo"


def test_translate_path_uses_default_container_path(monkeypatch):
    monkeypatch.setenv("HOST_REPOS_PREFIX", "/Users/testuser/code")
    monkeypatch.delenv("CONTAINER_REPOS_PATH", raising=False)
    assert _translate_path("/Users/testuser/code/myrepo") == "/repos/myrepo"


def test_translate_path_no_op_when_prefix_empty(monkeypatch):
    monkeypatch.setenv("HOST_REPOS_PREFIX", "")
    monkeypatch.setenv("CONTAINER_REPOS_PATH", "/repos")
    assert _translate_path("/Users/testuser/code/myrepo") == "/Users/testuser/code/myrepo"


# ------------------------------------------------------------------
# New: search tool, native cypher tests
# ------------------------------------------------------------------

def test_search_tool_returns_results():
    mock_store = MagicMock()
    mock_store.repo_exists.return_value = True
    mock_store.search.return_value = [
        {"name": "auth", "file_path": "app.py", "kind": "function", "score": 1.5, "rank": 1}
    ]
    with patch("depgraph.mcp.server._get_store", return_value=mock_store), \
         patch("depgraph.mcp.server._os.path.isdir", return_value=True):
        result = _tool_search("/repo", "auth", 10)
    assert "results" in result
    assert result["count"] == 1
    assert result["results"][0]["name"] == "auth"


def test_query_graph_rejects_create():
    mock_store = MagicMock()
    mock_store.repo_exists.return_value = True
    with patch("depgraph.mcp.server._get_store", return_value=mock_store):
        result = _tool_query_graph("/repo", "CREATE (n:File {id: 'x'})")
    assert result.get("code") == "QUERY_NOT_ALLOWED"


def test_query_graph_uses_native_cypher():
    mock_store = MagicMock()
    mock_store.repo_exists.return_value = True
    mock_store.execute_cypher.return_value = [{"name": "foo"}]
    with patch("depgraph.mcp.server._get_store", return_value=mock_store):
        result = _tool_query_graph("/repo", "MATCH (f:Function) RETURN f.name")
    assert result["rows"] == [{"name": "foo"}]
    assert result["count"] == 1


def test_tool_reset_db_calls_store_reset_and_returns_ok():
    mock_store = MagicMock()
    with patch("depgraph.mcp.server._get_store", return_value=mock_store):
        result = _tool_reset_db()
    mock_store.reset_db.assert_called_once()
    assert result["status"] == "ok"
    assert "message" in result
