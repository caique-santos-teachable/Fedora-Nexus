"""Tests for Cypher-subset query engine."""

import pytest
from depgraph.graph.engine import DependencyGraph
from depgraph.query.cypher import execute


def make_graph() -> DependencyGraph:
    """
    auth.py -> utils.py
    auth.py -> models.py
    app.rb  -> auth.py
    """
    g = DependencyGraph()
    g.add_node("auth.py", language="python", kind="file")
    g.add_node("utils.py", language="python", kind="file")
    g.add_node("models.py", language="python", kind="file")
    g.add_node("app.rb", language="ruby", kind="file")
    g.add_edge("auth.py", "utils.py")
    g.add_edge("auth.py", "models.py")
    g.add_edge("app.rb", "auth.py")
    return g


def test_match_single_node_no_filter():
    g = make_graph()
    results = execute(g, 'MATCH (n) RETURN n')
    assert len(results) == 4


def test_match_node_with_path_prop():
    g = make_graph()
    results = execute(g, 'MATCH (n {path: "auth.py"}) RETURN n')
    assert len(results) == 1
    assert results[0]["n"]["id"] == "auth.py"


def test_where_contains():
    g = make_graph()
    results = execute(g, 'MATCH (n) WHERE n.path CONTAINS "auth" RETURN n')
    ids = [r["n"]["id"] for r in results]
    assert "auth.py" in ids
    assert "utils.py" not in ids


def test_where_ends_with():
    g = make_graph()
    results = execute(g, 'MATCH (n) WHERE n.path ENDS WITH ".rb" RETURN n')
    ids = [r["n"]["id"] for r in results]
    assert ids == ["app.rb"]


def test_where_starts_with():
    g = make_graph()
    results = execute(g, 'MATCH (n) WHERE n.path STARTS WITH "auth" RETURN n')
    ids = [r["n"]["id"] for r in results]
    assert "auth.py" in ids


def test_match_depends_on_direct():
    g = make_graph()
    results = execute(g, 'MATCH (n {path: "auth.py"})-[:DEPENDS_ON*1..1]->(dep) RETURN dep')
    dep_ids = [r["dep"]["id"] for r in results]
    assert set(dep_ids) == {"utils.py", "models.py"}


def test_match_depends_on_transitive():
    # app.rb -> auth.py -> utils.py
    g = make_graph()
    results = execute(g, 'MATCH (n {path: "app.rb"})-[:DEPENDS_ON*1..2]->(dep) RETURN dep')
    dep_ids = {r["dep"]["id"] for r in results}
    assert "auth.py" in dep_ids
    assert "utils.py" in dep_ids


def test_match_reverse_direction():
    g = make_graph()
    results = execute(g, 'MATCH (n {path: "utils.py"})<-[:DEPENDS_ON*1..1]-(src) RETURN src')
    src_ids = [r["src"]["id"] for r in results]
    assert "auth.py" in src_ids


def test_return_multiple_vars():
    g = make_graph()
    results = execute(g, 'MATCH (n {path: "auth.py"})-[:DEPENDS_ON*1..1]->(dep) RETURN n, dep')
    assert len(results) == 2
    for r in results:
        assert "n" in r
        assert "dep" in r
        assert r["n"]["id"] == "auth.py"
