"""Unit tests for KuzuGraphStore — no external service required."""

from __future__ import annotations

import os

import pytest

from fedora_nexus.graph.engine import DependencyGraph
from fedora_nexus.store.kuzu_store import KuzuGraphStore


@pytest.fixture()
def store(tmp_path):
    s = KuzuGraphStore(db_path=str(tmp_path / "test.db"))
    s.init_schema()
    return s


@pytest.fixture()
def sample_graph() -> DependencyGraph:
    g = DependencyGraph()
    g.add_node("src/a.py", language="python")
    g.add_node("src/b.py", language="python")
    g.add_node("src/c.py", language="python")
    g.add_edge("src/a.py", "src/b.py")
    g.add_edge("src/b.py", "src/c.py")
    return g


def test_save_and_load(store, sample_graph):
    root = "/tmp/test_repo_001"
    store.save_graph(root, sample_graph)
    loaded = store.load_graph(root)
    assert loaded is not None
    assert loaded.has_node("src/a.py")
    assert loaded.has_node("src/b.py")
    assert loaded.has_node("src/c.py")
    assert "src/b.py" in loaded.get_dependencies("src/a.py")
    store.delete_repo(root)


def test_repo_exists(store, sample_graph):
    root = "/tmp/test_repo_002"
    assert not store.repo_exists(root)
    store.save_graph(root, sample_graph)
    assert store.repo_exists(root)
    store.delete_repo(root)
    assert not store.repo_exists(root)


def test_load_nonexistent_returns_none(store):
    result = store.load_graph("/tmp/does_not_exist_xyz")
    assert result is None


def test_list_repos(store, sample_graph):
    root = "/tmp/test_repo_003"
    store.save_graph(root, sample_graph)
    repos = store.list_repos()
    paths = [r["root_path"] for r in repos]
    assert root in paths
    match = next(r for r in repos if r["root_path"] == root)
    assert match["nodes"] == 3
    assert match["edges"] == 2
    store.delete_repo(root)


def test_list_repos_edges_counts_symbol_level_edges(store):
    """list_repos edge count must include Method→Class CALLS, not just File→File edges.

    Regression: the original query used MATCH (a:File {...})-[:CodeRelation]->()
    which silently dropped all edges originating from Method/Class/Function nodes.
    """
    g = DependencyGraph()
    root = "/tmp/test_repo_edge_count"
    # File nodes
    g.add_node("src/a.py", language="python", kind="file")
    g.add_node("src/b.py", language="python", kind="file")
    # Symbol nodes
    g.add_node("src/a.py#method:Foo.bar", language="python", kind="method",
               name="bar", file_path="src/a.py", content="", start_line=1, end_line=3)
    g.add_node("src/b.py#class:Baz", language="python", kind="class",
               name="Baz", file_path="src/b.py", content="", start_line=1, end_line=5)
    # File-level DEPENDS_ON
    g.add_edge("src/a.py", "src/b.py", rel="DEPENDS_ON")
    # Symbol-level CALLS (Method → Class) — was NOT counted before fix
    g.add_edge("src/a.py#method:Foo.bar", "src/b.py#class:Baz", rel="CALLS")
    # CONTAINS edges
    g.add_edge("src/a.py", "src/a.py#method:Foo.bar", rel="CONTAINS")
    g.add_edge("src/b.py", "src/b.py#class:Baz", rel="CONTAINS")

    store.save_graph(root, g)
    repos = store.list_repos()
    match = next(r for r in repos if r["root_path"] == root)
    # 4 edges total: 1 DEPENDS_ON + 1 CALLS + 2 CONTAINS
    assert match["edges"] == 4, (
        f"Expected 4 edges (including symbol-level CALLS), got {match['edges']}. "
        "Likely cause: edge query filtered to File-origin edges only."
    )
    store.delete_repo(root)


def test_delete_nonexistent_returns_false(store):
    assert not store.delete_repo("/tmp/never_existed_xyz")


def test_stale_lock_file_removed_on_init(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    os.makedirs(db_path, exist_ok=True)
    lock_file = os.path.join(db_path, ".lock")
    open(lock_file, "w").close()  # create fake stale lock file

    import kuzu
    from unittest.mock import MagicMock

    call_count = 0
    mock_db_instance = MagicMock()

    def mock_database(path):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("Could not set lock on file : " + path)
        return mock_db_instance

    monkeypatch.setattr(kuzu, "Database", mock_database)
    monkeypatch.setattr(kuzu, "Connection", lambda db: MagicMock())

    s = KuzuGraphStore(db_path=db_path)
    assert not os.path.exists(lock_file)
    assert call_count == 2
    assert s._db is mock_db_instance


def test_reset_db_wipes_and_reinitializes(tmp_path):
    """After reset_db(), list_repos() returns empty list."""
    store = KuzuGraphStore(db_path=str(tmp_path / "test.db"))
    store.init_schema()
    g = DependencyGraph()
    g.add_node("foo.py", kind="file", language="python")
    store.save_graph("/repo/foo", g)
    assert len(store.list_repos()) == 1
    store._embedding_cache["/repo/foo"] = ("fake", "data")
    store.reset_db()
    assert store.list_repos() == []
    assert store._embedding_cache == {}


def test_checkpoint_called_after_detach_delete(tmp_path, monkeypatch):
    """CHECKPOINT is executed after DETACH DELETE to flush WAL."""
    store = KuzuGraphStore(db_path=str(tmp_path / "test.db"))
    store.init_schema()
    g = DependencyGraph()
    g.add_node("bar.py", kind="file", language="python")
    store.save_graph("/repo/bar", g)

    checkpoint_calls = []
    original_execute = store._conn.execute

    def patched_execute(query, params=None):
        if "CHECKPOINT" in query.upper():
            checkpoint_calls.append(query)
        if params is not None:
            return original_execute(query, params)
        return original_execute(query)

    monkeypatch.setattr(store._conn, "execute", patched_execute)
    store._delete_repo_data("/repo/bar")
    assert len(checkpoint_calls) >= 1


# ------------------------------------------------------------------
# Embedding store unit tests (no fastembed required)
# ------------------------------------------------------------------

def test_rrf_fuse_combines_both_lists():
    """Items in BOTH lists must score higher than items in only ONE list."""
    from fedora_nexus.store.embedding_store import rrf_fuse
    # "a" and "b" appear in both lists → dual boost
    # "c" only in BM25, "d" only in semantic → single boost
    bm25 = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    semantic = [("b", 0.9), ("a", 0.7), ("d", 0.5)]
    fused = rrf_fuse(bm25, semantic, k=60)
    ids = [f[0] for f in fused]

    # Both "a" and "b" appear in both lists — must outrank single-list items
    dual_list_items = {"a", "b"}
    single_list_items = {"c", "d"}
    top2 = set(ids[:2])
    assert top2 == dual_list_items, f"Top 2 should be dual-list items a+b, got {top2}"
    # "c" and "d" must be ranked below "a" and "b"
    assert all(ids.index(s) > 1 for s in single_list_items if s in ids), \
        f"Single-list items must rank below dual-list items: {ids}"


def test_rrf_fuse_empty_semantic_returns_bm25_order():
    """When semantic_results is empty, RRF output must preserve BM25 ranking."""
    from fedora_nexus.store.embedding_store import rrf_fuse
    bm25 = [{"id": "x"}, {"id": "y"}, {"id": "z"}]
    fused = rrf_fuse(bm25, [], k=60)
    ids = [f[0] for f in fused]
    assert ids == ["x", "y", "z"]


def test_rrf_fuse_empty_bm25_returns_semantic_order():
    """When bm25_results is empty, RRF output must reflect semantic ranking."""
    from fedora_nexus.store.embedding_store import rrf_fuse
    semantic = [("p", 0.9), ("q", 0.8), ("r", 0.7)]
    fused = rrf_fuse([], semantic, k=60)
    ids = [f[0] for f in fused]
    assert ids == ["p", "q", "r"]


def test_index_path_is_deterministic():
    """Same (db_path, root_path) must always produce the same index path."""
    from fedora_nexus.store.embedding_store import _index_path
    p1 = _index_path("/data/fedora-nexus.db", "/repos/myapp")
    p2 = _index_path("/data/fedora-nexus.db", "/repos/myapp")
    assert p1 == p2


def test_index_path_differs_for_different_repos():
    """Different root_paths must produce different index paths."""
    from fedora_nexus.store.embedding_store import _index_path
    p1 = _index_path("/data/fedora-nexus.db", "/repos/app1")
    p2 = _index_path("/data/fedora-nexus.db", "/repos/app2")
    assert p1 != p2


def test_search_falls_back_to_bm25_when_no_embedding_index(store, tmp_path):
    """search() must return BM25 results when no embedding index exists."""
    g = DependencyGraph()
    g.add_node("src/auth.py", language="python", kind="file", name="auth.py", content="auth module")
    g.add_node("src/auth.py#function:login", language="python", kind="function",
               name="login", file_path="src/auth.py", start_line=1, end_line=5,
               content="def login(): pass", is_exported=True)
    g.add_edge("src/auth.py", "src/auth.py#function:login", rel="CONTAINS")
    root = "/tmp/test_search_fallback_001"
    store.save_graph(root, g)

    # Ensure no embedding index exists
    from fedora_nexus.store import embedding_store as emb
    emb.delete_index(store._db_path, root)
    store._embedding_cache.pop(root, None)

    results = store.search(root, "login", limit=5)
    assert len(results) >= 1
    assert any(r["name"] == "login" for r in results), f"Expected 'login' in results: {results}"
    store.delete_repo(root)


def test_load_index_returns_none_when_not_built(tmp_path):
    """load_index must return None when no .npz exists."""
    from fedora_nexus.store.embedding_store import load_index
    result = load_index(str(tmp_path / "fedora-nexus.db"), "/repos/nonexistent")
    assert result is None


def test_delete_index_is_noop_when_missing(tmp_path):
    """delete_index must not raise when index file does not exist."""
    from fedora_nexus.store.embedding_store import delete_index
    delete_index(str(tmp_path / "fedora-nexus.db"), "/repos/nonexistent")  # must not raise


def test_non_lock_runtime_error_re_raised(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")

    import kuzu

    monkeypatch.setattr(kuzu, "Database", lambda path: (_ for _ in ()).throw(RuntimeError("some other error")))

    with pytest.raises(RuntimeError, match="some other error"):
        KuzuGraphStore(db_path=db_path)

    lock_file_1 = os.path.join(db_path, ".lock")
    lock_file_2 = os.path.join(db_path, ".db.lock")
    assert not os.path.exists(lock_file_1)
    assert not os.path.exists(lock_file_2)


def test_stale_lock_retry_also_fails(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    os.makedirs(db_path, exist_ok=True)
    lock_file = os.path.join(db_path, ".lock")
    open(lock_file, "w").close()

    import kuzu

    monkeypatch.setattr(
        kuzu,
        "Database",
        lambda path: (_ for _ in ()).throw(RuntimeError("Could not set lock on file : " + path)),
    )

    with pytest.raises(RuntimeError, match="Could not set lock"):
        KuzuGraphStore(db_path=db_path)


# ------------------------------------------------------------------
# semantic_search unit tests (mocked model — no fastembed required)
# ------------------------------------------------------------------

def test_semantic_search_returns_empty_for_zero_norm_query():
    """semantic_search must return [] when query vector norm is zero."""
    import numpy as np
    from fedora_nexus.store.embedding_store import semantic_search
    from unittest.mock import patch, MagicMock

    ids = ["a", "b"]
    vectors = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    zero_vec = np.array([0.0, 0.0], dtype=np.float32)

    mock_model = MagicMock()
    mock_model.embed.return_value = iter([zero_vec])

    mock_fastembed = MagicMock()
    mock_fastembed.TextEmbedding = MagicMock()

    with patch.dict(__import__("sys").modules, {"fastembed": mock_fastembed}), \
         patch("fedora_nexus.store.embedding_store._get_model", return_value=mock_model):
        result = semantic_search(ids, vectors, "anything", k=2)

    assert result == []


def test_semantic_search_ranks_by_cosine_similarity():
    """semantic_search must return ids sorted by cosine similarity descending."""
    import numpy as np
    from fedora_nexus.store.embedding_store import semantic_search
    from unittest.mock import patch, MagicMock

    # id "b" is aligned with query, "a" is orthogonal
    ids = ["a", "b"]
    vectors = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    query_vec = np.array([0.0, 1.0], dtype=np.float32)  # perfectly aligned with "b"

    mock_model = MagicMock()
    mock_model.embed.return_value = iter([query_vec])

    mock_fastembed = MagicMock()
    mock_fastembed.TextEmbedding = MagicMock()

    with patch.dict(__import__("sys").modules, {"fastembed": mock_fastembed}), \
         patch("fedora_nexus.store.embedding_store._get_model", return_value=mock_model):
        result = semantic_search(ids, vectors, "query", k=2)

    assert result[0][0] == "b", f"Expected 'b' as top result, got {result}"
    assert result[1][0] == "a"


# ------------------------------------------------------------------
# build_index unit tests (mocked model — no fastembed required)
# ------------------------------------------------------------------

def test_build_index_returns_false_for_empty_symbols(tmp_path):
    """build_index must return False when no symbols are provided."""
    from fedora_nexus.store import embedding_store as emb
    result = emb.build_index(str(tmp_path / "fedora-nexus.db"), "/repos/test", [])
    assert result is False


def test_build_index_saves_npz_and_returns_true(tmp_path):
    """build_index must save a .npz file and return True when model is available."""
    import numpy as np
    from unittest.mock import patch, MagicMock
    from fedora_nexus.store import embedding_store as emb

    symbols = [
        {"id": "repo::src/a.py#function:foo", "name": "foo", "content": "def foo(): pass"},
        {"id": "repo::src/b.py#class:Bar", "name": "Bar", "content": "class Bar: pass"},
    ]
    fake_vectors = np.array([[0.1] * 384, [0.2] * 384], dtype=np.float32)
    mock_model = MagicMock()
    mock_model.embed.return_value = iter(fake_vectors)

    mock_fastembed = MagicMock()
    mock_fastembed.TextEmbedding = MagicMock()

    db_path = str(tmp_path / "fedora-nexus.db")
    with patch.dict(__import__("sys").modules, {"fastembed": mock_fastembed}), \
         patch("fedora_nexus.store.embedding_store._get_model", return_value=mock_model):
        result = emb.build_index(db_path, "/repos/test", symbols)

    assert result is True
    idx_path = emb._index_path(db_path, "/repos/test")
    assert idx_path.exists(), f"Expected .npz at {idx_path}"
    data = np.load(str(idx_path))
    assert list(data["ids"]) == [s["id"] for s in symbols]
    assert data["vectors"].shape == (2, 384)


def test_overwrite_save(store, sample_graph):
    root = "/tmp/test_repo_004"
    store.save_graph(root, sample_graph)
    # Save a smaller graph over the same root
    g2 = DependencyGraph()
    g2.add_node("only.py", language="python")
    store.save_graph(root, g2)
    loaded = store.load_graph(root)
    assert loaded is not None
    assert loaded.has_node("only.py")
    assert not loaded.has_node("src/a.py")
    store.delete_repo(root)


def test_get_indexed_at(store, sample_graph):
    root = "/tmp/test_repo_005"
    assert store.get_indexed_at(root) is None
    store.save_graph(root, sample_graph)
    indexed_at = store.get_indexed_at(root)
    assert indexed_at is not None
    assert "T" in indexed_at  # ISO format
    store.delete_repo(root)


def test_edge_rel_preservation(store):
    root = "/tmp/test_repo_006"
    g = DependencyGraph()
    g.add_node("a.py", language="python")
    g.add_node("b.py", language="python")
    g.add_edge("a.py", "b.py", rel="IMPORTS")
    store.save_graph(root, g)
    loaded = store.load_graph(root)
    assert loaded is not None
    assert "b.py" in loaded.get_dependencies("a.py")
    store.delete_repo(root)


def test_rich_schema_saves_function_with_content(tmp_path):
    from fedora_nexus.graph.engine import DependencyGraph
    from fedora_nexus.store.kuzu_store import KuzuGraphStore
    g = DependencyGraph()
    g.add_node("src/app.py", language="python", kind="file", name="app.py", content="# app")
    g.add_node("src/app.py#function:greet", language="python", kind="function",
               name="greet", file_path="src/app.py", start_line=1, end_line=3,
               content="def greet(): pass", is_exported=False)
    g.add_edge("src/app.py", "src/app.py#function:greet", rel="CONTAINS")
    store = KuzuGraphStore(db_path=str(tmp_path / "test.db"))
    root = "/repo/test"
    store.save_graph(root, g)
    loaded = store.load_graph(root)
    assert loaded is not None
    assert loaded.has_node("src/app.py#function:greet")
    attrs = loaded.node_attrs("src/app.py#function:greet")
    assert attrs["name"] == "greet"
    assert "greet" in attrs["content"]
    assert attrs["start_line"] == 1


def test_schema_version_error_on_old_schema(tmp_path):
    from fedora_nexus.store.kuzu_store import KuzuGraphStore, SchemaVersionError
    from unittest.mock import MagicMock, patch

    store = KuzuGraphStore(db_path=str(tmp_path / "test.db"))

    original_execute = store._conn.execute

    def fake_execute(query, params=None):
        if "MATCH (n:Node)" in query:
            mock_result = MagicMock()
            mock_result.has_next.return_value = True
            mock_result.get_next.return_value = [0]
            return mock_result
        return original_execute(query, params) if params else original_execute(query)

    with patch.object(store._conn, "execute", side_effect=fake_execute):
        with pytest.raises(SchemaVersionError):
            store.init_schema()


@pytest.mark.integration
def test_search_returns_function_by_name(tmp_path):
    from fedora_nexus.graph.engine import DependencyGraph
    from fedora_nexus.store.kuzu_store import KuzuGraphStore
    g = DependencyGraph()
    g.add_node("src/auth.py", language="python", kind="file", name="auth.py", content="# auth module")
    g.add_node("src/auth.py#function:authenticate", language="python", kind="function",
               name="authenticate", file_path="src/auth.py", start_line=1, end_line=5,
               content="def authenticate(user, pw): return True", is_exported=False)
    g.add_edge("src/auth.py", "src/auth.py#function:authenticate", rel="CONTAINS")
    store = KuzuGraphStore(db_path=str(tmp_path / "test.db"))
    root = "/repo/myapp"
    store.save_graph(root, g)
    results = store.search(root, "authenticate")
    names = [r["name"] for r in results]
    assert "authenticate" in names
    assert results[0]["rank"] == 1


# ------------------------------------------------------------------
# Class node kind column — guardrail tests
# ------------------------------------------------------------------

def test_class_node_persists_kind_class(tmp_path):
    """Class nodes must store their 'kind' = 'class' in the Kuzu Class table."""
    store = KuzuGraphStore(db_path=str(tmp_path / "test.db"))
    store.init_schema()
    g = DependencyGraph()
    g.add_node("src/user.rb", language="ruby", kind="file", name="user.rb", content="")
    g.add_node("src/user.rb#class:User", language="ruby", kind="class", name="User",
               file_path="src/user.rb", content="class User; end", start_line=1, end_line=3)
    root = "/tmp/kind_class_test"
    store.save_graph(root, g)

    raw = store._conn.execute("MATCH (c:Class {name: 'User'}) RETURN c.kind")
    assert raw.has_next()
    assert raw.get_next()[0] == "class"
    store.delete_repo(root)


def test_module_node_persists_kind_module(tmp_path):
    """Module nodes must store their 'kind' = 'module' in the Kuzu Class table."""
    store = KuzuGraphStore(db_path=str(tmp_path / "test.db"))
    store.init_schema()
    g = DependencyGraph()
    g.add_node("app/concerns/auditable.rb", language="ruby", kind="file",
               name="auditable.rb", content="")
    g.add_node("app/concerns/auditable.rb#module:Auditable", language="ruby",
               kind="module", name="Auditable", file_path="app/concerns/auditable.rb",
               content="module Auditable; end", start_line=1, end_line=3)
    root = "/tmp/kind_module_test"
    store.save_graph(root, g)

    raw = store._conn.execute("MATCH (c:Class {name: 'Auditable'}) RETURN c.kind")
    assert raw.has_next()
    assert raw.get_next()[0] == "module"
    store.delete_repo(root)


def test_db_table_node_persists_kind_db_table(tmp_path):
    """SQL db_table nodes must store their 'kind' = 'db_table' in the Kuzu Class table."""
    store = KuzuGraphStore(db_path=str(tmp_path / "test.db"))
    store.init_schema()
    g = DependencyGraph()
    g.add_node("db/structure.sql", language="sql", kind="file",
               name="structure.sql", content="")
    g.add_node("db/structure.sql#db_table:users", language="sql",
               kind="db_table", name="users", file_path="db/structure.sql",
               content="id bigint, email varchar", start_line=1, end_line=5)
    root = "/tmp/kind_db_table_test"
    store.save_graph(root, g)

    raw = store._conn.execute("MATCH (c:Class {name: 'users'}) RETURN c.kind")
    assert raw.has_next()
    assert raw.get_next()[0] == "db_table"
    store.delete_repo(root)


def test_cypher_where_kind_db_table_returns_only_sql_tables(tmp_path):
    """WHERE c.kind = 'db_table' must not raise Binder exception and must filter correctly."""
    store = KuzuGraphStore(db_path=str(tmp_path / "test.db"))
    store.init_schema()
    g = DependencyGraph()
    g.add_node("app/models/course.rb", language="ruby", kind="file",
               name="course.rb", content="")
    g.add_node("app/models/course.rb#class:Course", language="ruby",
               kind="class", name="Course", file_path="app/models/course.rb",
               content="class Course; end", start_line=1, end_line=3)
    g.add_node("db/structure.sql", language="sql", kind="file",
               name="structure.sql", content="")
    g.add_node("db/structure.sql#db_table:courses", language="sql",
               kind="db_table", name="courses", file_path="db/structure.sql",
               content="id bigint, title varchar", start_line=1, end_line=4)
    root = "/tmp/kind_cypher_filter_test"
    store.save_graph(root, g)

    raw = store._conn.execute("MATCH (c:Class) WHERE c.kind = 'db_table' RETURN c.name")
    names = []
    while raw.has_next():
        names.append(raw.get_next()[0])
    assert "courses" in names
    assert "Course" not in names
    store.delete_repo(root)


def test_fts_kind_db_table_returns_only_sql_tables_not_ruby_classes(tmp_path):
    """FTS search with kind='db_table' must NOT return Ruby class/module nodes."""
    store = KuzuGraphStore(db_path=str(tmp_path / "test.db"))
    store.init_schema()
    g = DependencyGraph()
    # Ruby class with 'accounts' in content
    g.add_node("app/models/account.rb", language="ruby", kind="file",
               name="account.rb", content="")
    g.add_node("app/models/account.rb#class:Account", language="ruby",
               kind="class", name="Account", file_path="app/models/account.rb",
               content="class Account; belongs_to :accounts; end",
               start_line=1, end_line=3)
    # SQL table also named 'accounts'
    g.add_node("db/structure.sql", language="sql", kind="file",
               name="structure.sql", content="")
    g.add_node("db/structure.sql#db_table:accounts", language="sql",
               kind="db_table", name="accounts", file_path="db/structure.sql",
               content="id bigint, name varchar", start_line=1, end_line=4)
    root = "/tmp/fts_kind_filter_test"
    store.save_graph(root, g)

    results = store.search(root, "accounts", kind="db_table")
    kinds = {r["kind"] for r in results}
    # Must only contain db_table results — no 'class' results
    assert "db_table" in kinds
    assert "class" not in kinds
    store.delete_repo(root)


def test_kind_column_migration_idempotent(tmp_path):
    """init_schema() must not raise if kind column already exists in Class table."""
    store = KuzuGraphStore(db_path=str(tmp_path / "test.db"))
    store.init_schema()
    # Calling init_schema() again must not raise even though kind already exists
    store.init_schema()  # should be a no-op (IF NOT EXISTS + migration probe)


def test_dsl_association_nodes_persisted_to_kuzu(tmp_path):
    """has_many/belongs_to nodes (kind='association') must appear in the Method table after save_graph.

    Regression: DSL nodes were silently dropped by save_graph because they had no
    elif branch and fell through to the '# other kinds — not stored in DB' comment.
    """
    store = KuzuGraphStore(db_path=str(tmp_path / "test.db"))
    store.init_schema()
    g = DependencyGraph()
    root = "/tmp/test_dsl_assoc"
    g.add_node("app/models/course.rb", language="ruby", kind="file",
               name="course.rb", content="")
    g.add_node("app/models/course.rb#class:Course", language="ruby",
               kind="class", name="Course", file_path="app/models/course.rb",
               content="", start_line=1, end_line=20)
    g.add_node("app/models/course.rb#association:has_many:lectures",
               language="ruby", kind="association",
               name="lectures", macro="has_many",
               file_path="app/models/course.rb", start_line=3, content="has_many :lectures")
    g.add_node("app/models/course.rb#association:belongs_to:school",
               language="ruby", kind="association",
               name="school", macro="belongs_to",
               file_path="app/models/course.rb", start_line=4, content="belongs_to :school")
    g.add_edge("app/models/course.rb", "app/models/course.rb#class:Course", rel="CONTAINS")
    g.add_edge("app/models/course.rb#class:Course",
               "app/models/course.rb#association:has_many:lectures", rel="CONTAINS")
    g.add_edge("app/models/course.rb#class:Course",
               "app/models/course.rb#association:belongs_to:school", rel="CONTAINS")
    store.save_graph(root, g)

    # Verify association nodes are in Kuzu Method table
    res = store._conn.execute(
        "MATCH (m:Method {root_path: $rp}) WHERE m.kind = 'association' RETURN m.name ORDER BY m.name",
        {"rp": root},
    )
    names = []
    while res.has_next():
        names.append(res.get_next()[0])
    assert "lectures" in names, f"Expected 'lectures' in Method table; got {names}"
    assert "school" in names, f"Expected 'school' in Method table; got {names}"
    store.delete_repo(root)


def test_dsl_hook_nodes_persisted_to_kuzu(tmp_path):
    """before_action nodes (kind='hook') must appear in the Method table after save_graph."""
    store = KuzuGraphStore(db_path=str(tmp_path / "test.db"))
    store.init_schema()
    g = DependencyGraph()
    root = "/tmp/test_dsl_hook"
    g.add_node("app/controllers/courses_controller.rb", language="ruby", kind="file",
               name="courses_controller.rb", content="")
    g.add_node("app/controllers/courses_controller.rb#class:CoursesController",
               language="ruby", kind="class", name="CoursesController",
               file_path="app/controllers/courses_controller.rb",
               content="", start_line=1, end_line=10)
    g.add_node("app/controllers/courses_controller.rb#hook:before_action:authenticate_user!",
               language="ruby", kind="hook",
               name="before_action:authenticate_user!",
               file_path="app/controllers/courses_controller.rb",
               start_line=2, content="before_action :authenticate_user!")
    g.add_edge("app/controllers/courses_controller.rb",
               "app/controllers/courses_controller.rb#class:CoursesController", rel="CONTAINS")
    g.add_edge("app/controllers/courses_controller.rb#class:CoursesController",
               "app/controllers/courses_controller.rb#hook:before_action:authenticate_user!",
               rel="CONTAINS")
    store.save_graph(root, g)

    res = store._conn.execute(
        "MATCH (m:Method {root_path: $rp}) WHERE m.kind = 'hook' RETURN m.name",
        {"rp": root},
    )
    names = []
    while res.has_next():
        names.append(res.get_next()[0])
    assert any("before_action" in n for n in names), (
        f"Expected hook method in Method table; got {names}"
    )
    store.delete_repo(root)


def test_method_kind_column_preserved_for_regular_methods(tmp_path):
    """Regular method nodes must have kind='method' in Kuzu after save_graph."""
    store = KuzuGraphStore(db_path=str(tmp_path / "test.db"))
    store.init_schema()
    g = DependencyGraph()
    root = "/tmp/test_method_kind"
    g.add_node("app/models/course.rb", language="ruby", kind="file",
               name="course.rb", content="")
    g.add_node("app/models/course.rb#method:Course.publish", language="ruby",
               kind="method", name="publish", file_path="app/models/course.rb",
               content="def publish; end", start_line=5, end_line=7,
               owner_name="Course", scope_refs=[])
    g.add_edge("app/models/course.rb", "app/models/course.rb#method:Course.publish",
               rel="CONTAINS")
    store.save_graph(root, g)

    res = store._conn.execute(
        "MATCH (m:Method {root_path: $rp, name: 'publish'}) RETURN m.kind",
        {"rp": root},
    )
    assert res.has_next()
    assert res.get_next()[0] == "method"
    store.delete_repo(root)

