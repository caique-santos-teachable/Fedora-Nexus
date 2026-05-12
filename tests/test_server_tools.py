"""Unit tests for MCP server tool implementations (no DB required)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from fedora_nexus.graph.engine import DependencyGraph
from fedora_nexus.mcp.server import (
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

    with patch("fedora_nexus.mcp.server._get_store", return_value=mock_store):
        with pytest.raises(RepoNotFoundError) as exc_info:
            _require_graph("/deleted/repo")
    assert exc_info.value.root_path == "/deleted/repo"


# TC-03: subgraph_paths with missing path → missing_paths reported
def test_get_graph_subgraph_reports_missing_paths():
    g = make_graph()
    mock_store = MagicMock()
    mock_store.load_graph.return_value = g

    with patch("fedora_nexus.mcp.server._get_store", return_value=mock_store):
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

    with patch("fedora_nexus.mcp.server._get_store", return_value=mock_store):
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

    with patch("fedora_nexus.mcp.server._get_store", return_value=mock_store), \
         patch("fedora_nexus.mcp.server._run_index", return_value=mock_graph) as mock_run_index, \
         patch("fedora_nexus.mcp.server._os.path.isdir", return_value=True):
        _tool_index_repo("/some/repo", None, False, symbol_mode=True)

    call_kwargs = mock_run_index.call_args
    # symbol_mode=True must be forwarded
    assert call_kwargs.kwargs.get("symbol_mode") is True or (
        len(call_kwargs.args) >= 3 and call_kwargs.args[2] is True
    )


def test_index_repo_directory_not_found_returns_not_found_error():
    """When the translated path does not exist, return an error with code NOT_FOUND."""
    with patch("fedora_nexus.mcp.server._translate_path", return_value="/nonexistent/path/xyz"):
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

    with patch("fedora_nexus.mcp.server._get_store", return_value=mock_store), \
         caplog.at_level(logging.INFO, logger="fedora_nexus.mcp.server"):
        from fedora_nexus.mcp.server import _dispatch
        asyncio.run(_dispatch("get_dependencies", {"root_path": "/repo", "file_path": "a.py"}))

    assert any("[TOOL] get_dependencies" in r.message for r in caplog.records)


def test_dispatch_logs_result_summary(caplog):
    """_dispatch must emit a result summary log with elapsed time."""
    import asyncio
    import logging

    mock_store = MagicMock()
    mock_store.load_graph.return_value = make_graph()

    with patch("fedora_nexus.mcp.server._get_store", return_value=mock_store), \
         caplog.at_level(logging.INFO, logger="fedora_nexus.mcp.server"):
        from fedora_nexus.mcp.server import _dispatch
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
    with patch("fedora_nexus.mcp.server._get_store", return_value=mock_store), \
         patch("fedora_nexus.mcp.server._os.path.isdir", return_value=True):
        result = _tool_search("/repo", "auth", 10)
    assert "results" in result
    assert result["count"] == 1
    assert result["results"][0]["name"] == "auth"


def test_query_graph_rejects_create():
    mock_store = MagicMock()
    mock_store.repo_exists.return_value = True
    with patch("fedora_nexus.mcp.server._get_store", return_value=mock_store):
        result = _tool_query_graph("/repo", "CREATE (n:File {id: 'x'})")
    assert result.get("code") == "QUERY_NOT_ALLOWED"


def test_query_graph_uses_native_cypher():
    mock_store = MagicMock()
    mock_store.repo_exists.return_value = True
    mock_store.execute_cypher.return_value = [{"name": "foo"}]
    with patch("fedora_nexus.mcp.server._get_store", return_value=mock_store):
        result = _tool_query_graph("/repo", "MATCH (f:Function) RETURN f.name")
    assert result["rows"] == [{"name": "foo"}]
    assert result["count"] == 1


def test_tool_reset_db_calls_store_reset_and_returns_ok():
    mock_store = MagicMock()
    with patch("fedora_nexus.mcp.server._get_store", return_value=mock_store):
        result = _tool_reset_db()
    mock_store.reset_db.assert_called_once()
    assert result["status"] == "ok"
    assert "message" in result


# ------------------------------------------------------------------
# search: kind filter
# ------------------------------------------------------------------

def test_search_tool_kind_filter_forwarded_to_store():
    """kind argument must be passed through to store.search as keyword arg."""
    mock_store = MagicMock()
    mock_store.repo_exists.return_value = True
    mock_store.search.return_value = []
    with patch("fedora_nexus.mcp.server._get_store", return_value=mock_store), \
         patch("fedora_nexus.mcp.server._os.path.isdir", return_value=True):
        _tool_search("/repo", "auth", 10, kind="function")
    mock_store.search.assert_called_once_with("/repo", "auth", 10, kind="function")


def test_search_tool_kind_none_by_default():
    """Omitting kind must call store.search with kind=None (no filter)."""
    mock_store = MagicMock()
    mock_store.repo_exists.return_value = True
    mock_store.search.return_value = []
    with patch("fedora_nexus.mcp.server._get_store", return_value=mock_store), \
         patch("fedora_nexus.mcp.server._os.path.isdir", return_value=True):
        _tool_search("/repo", "auth", 10)
    mock_store.search.assert_called_once_with("/repo", "auth", 10, kind=None)


# ------------------------------------------------------------------
# kuzu_store: embed symbols — no root_path:: prefix, files included
# ------------------------------------------------------------------

def test_symbols_for_embed_have_no_root_path_prefix():
    """Symbol IDs in the embed list must match raw node IDs (no root_path:: prefix).

    The prefix was a bug: BM25 results use raw IDs, so a prefixed semantic ID
    can never match in the RRF fusion meta dict, silently disabling semantic search.
    """
    import time
    from unittest.mock import patch, MagicMock
    from fedora_nexus.store.kuzu_store import KuzuGraphStore
    from fedora_nexus.graph.engine import DependencyGraph

    g = DependencyGraph()
    g.add_node(
        "src/a.py#function:do_thing",
        language="python",
        kind="function",
        name="do_thing",
        file_path="src/a.py",
        content="def do_thing(): pass",
    )

    captured = []

    def fake_build_index(db_path, root_path, symbols):
        captured.extend(symbols)
        return True

    with patch("fedora_nexus.store.kuzu_store._emb.build_index", side_effect=fake_build_index), \
         patch.object(KuzuGraphStore, "_ensure_schema"), \
         patch.object(KuzuGraphStore, "_delete_repo_data"), \
         patch.object(KuzuGraphStore, "_bulk_copy_nodes"):
        store = KuzuGraphStore.__new__(KuzuGraphStore)
        store._db_path = "/fake/db"
        store._embedding_cache = {}
        store._conn = MagicMock()
        store.save_graph("/repo", g)

    # Embedding runs in a background thread — wait for it
    for _ in range(40):
        if captured:
            break
        time.sleep(0.05)

    assert captured, "build_index was not called"
    ids = [s["id"] for s in captured]
    assert all("::" not in sid for sid in ids), f"Prefixed IDs found: {ids}"


def test_file_nodes_included_in_embed_symbols():
    """File-kind nodes must appear in symbols_for_embed so files are semantically searchable."""
    import time
    from unittest.mock import patch, MagicMock
    from fedora_nexus.store.kuzu_store import KuzuGraphStore
    from fedora_nexus.graph.engine import DependencyGraph

    g = DependencyGraph()
    g.add_node("src/a.py", language="python", kind="file", name="a.py", file_path="src/a.py")

    captured = []

    def fake_build_index(db_path, root_path, symbols):
        captured.extend(symbols)
        return True

    with patch("fedora_nexus.store.kuzu_store._emb.build_index", side_effect=fake_build_index), \
         patch.object(KuzuGraphStore, "_ensure_schema"), \
         patch.object(KuzuGraphStore, "_delete_repo_data"), \
         patch.object(KuzuGraphStore, "_bulk_copy_nodes"):
        store = KuzuGraphStore.__new__(KuzuGraphStore)
        store._db_path = "/fake/db"
        store._embedding_cache = {}
        store._conn = MagicMock()
        store.save_graph("/repo", g)

    for _ in range(40):
        if captured:
            break
        time.sleep(0.05)

    assert captured, "build_index was not called"
    file_symbols = [s for s in captured if "file_path" in s]
    assert len(file_symbols) >= 1, "Expected at least one file node in embed symbols"
    assert file_symbols[0]["id"] == "src/a.py"
    assert "::" not in file_symbols[0]["id"]


# ── get_graph max_nodes truncation ────────────────────────────────────────────

def _make_large_graph(n_files: int = 10, n_funcs_per_file: int = 5) -> DependencyGraph:
    g = DependencyGraph()
    for i in range(n_files):
        fid = f"file_{i}.py"
        g.add_node(fid, language="python", kind="file")
        for j in range(n_funcs_per_file):
            sym_id = f"file_{i}.py#function:fn_{j}"
            g.add_node(sym_id, language="python", kind="function")
            g.add_edge(fid, sym_id)
    return g


def test_get_graph_no_truncation_under_limit():
    """When node count <= max_nodes, returns full graph without truncated flag."""
    g = _make_large_graph(n_files=2, n_funcs_per_file=2)  # 2 + 4 = 6 nodes
    mock_store = MagicMock()
    mock_store.load_graph.return_value = g

    with patch("fedora_nexus.mcp.server._get_store", return_value=mock_store):
        result = _tool_get_graph("/repo", None, max_nodes=100)

    assert result.get("truncated") is not True
    assert len(result["nodes"]) == 6


def test_get_graph_truncates_to_max_nodes():
    """When node count > max_nodes, response is capped and truncated=True is set."""
    g = _make_large_graph(n_files=10, n_funcs_per_file=5)  # 10 + 50 = 60 nodes
    mock_store = MagicMock()
    mock_store.load_graph.return_value = g

    with patch("fedora_nexus.mcp.server._get_store", return_value=mock_store):
        result = _tool_get_graph("/repo", None, max_nodes=15)

    assert result["truncated"] is True
    assert len(result["nodes"]) == 15
    assert result["total_nodes"] == 60
    assert "total_edges" in result


def test_get_graph_file_nodes_prioritised_on_truncation():
    """File nodes come first in the truncated list."""
    g = _make_large_graph(n_files=10, n_funcs_per_file=5)  # 10 file + 50 func nodes
    mock_store = MagicMock()
    mock_store.load_graph.return_value = g

    with patch("fedora_nexus.mcp.server._get_store", return_value=mock_store):
        result = _tool_get_graph("/repo", None, max_nodes=5)

    # With only 5 nodes kept and 10 file nodes total, all 5 should be file nodes
    # (or at least file nodes come before symbols when sorted by priority)
    node_kinds = [n.get("kind") for n in result["nodes"]]
    # All kept nodes should be file nodes since max_nodes(5) < n_files(10)
    assert all(k == "file" for k in node_kinds), \
        f"Expected only file nodes with max_nodes=5, got: {node_kinds}"


def test_get_graph_subgraph_ignores_max_nodes():
    """When subgraph_paths is passed, max_nodes must be ignored (no truncation)."""
    g = _make_large_graph(n_files=3, n_funcs_per_file=2)
    mock_store = MagicMock()
    mock_store.load_graph.return_value = g

    with patch("fedora_nexus.mcp.server._get_store", return_value=mock_store):
        result = _tool_get_graph("/repo", ["file_0.py", "file_1.py"], max_nodes=1)

    # subgraph_paths path should bypass truncation
    assert result.get("truncated") is not True
