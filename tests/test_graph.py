"""Tests for DependencyGraph engine."""

import json
import pytest
from fedora_nexus.graph.engine import DependencyGraph


def make_simple_graph() -> DependencyGraph:
    g = DependencyGraph()
    g.add_node("a.py", language="python")
    g.add_node("b.py", language="python")
    g.add_node("c.py", language="python")
    g.add_edge("a.py", "b.py")
    g.add_edge("b.py", "c.py")
    return g


def test_add_node_and_has_node():
    g = DependencyGraph()
    g.add_node("foo.py", language="python")
    assert g.has_node("foo.py")
    assert not g.has_node("bar.py")


def test_add_node_attrs():
    g = DependencyGraph()
    g.add_node("foo.py", language="ruby", kind="module")
    attrs = g.node_attrs("foo.py")
    assert attrs["language"] == "ruby"
    assert attrs["kind"] == "module"


def test_add_edge():
    g = make_simple_graph()
    assert "b.py" in g.get_dependencies("a.py")
    assert "c.py" in g.get_dependencies("b.py")


def test_get_dependencies_direct():
    g = make_simple_graph()
    assert g.get_dependencies("a.py") == ["b.py"]
    assert g.get_dependencies("c.py") == []


def test_get_dependents_direct():
    g = make_simple_graph()
    assert g.get_dependents("b.py") == ["a.py"]
    assert g.get_dependents("a.py") == []


def test_to_adjacency_json():
    g = make_simple_graph()
    data = g.to_adjacency_json()
    node_ids = {n["id"] for n in data["nodes"]}
    assert node_ids == {"a.py", "b.py", "c.py"}
    edge_pairs = {(e["from"], e["to"]) for e in data["edges"]}
    assert ("a.py", "b.py") in edge_pairs
    assert ("b.py", "c.py") in edge_pairs


def test_subgraph():
    g = make_simple_graph()
    sub = g.subgraph(["a.py", "b.py"])
    node_ids = {n["id"] for n in sub["nodes"]}
    assert node_ids == {"a.py", "b.py"}
    assert len(sub["edges"]) == 1
    assert sub["edges"][0]["from"] == "a.py"


def test_save_and_load(tmp_path):
    g = make_simple_graph()
    filepath = tmp_path / "graph.json"
    g.save(filepath)
    assert filepath.exists()
    g2 = DependencyGraph.load(filepath)
    assert g2.has_node("a.py")
    assert g2.has_node("c.py")
    assert "b.py" in g2.get_dependencies("a.py")


def test_subgraph_with_missing_path_reports_missing():
    g = make_simple_graph()
    result = g.subgraph(["a.py", "nonexistent.py"])
    assert "missing_paths" in result
    assert "nonexistent.py" in result["missing_paths"]
    node_ids = {n["id"] for n in result["nodes"]}
    assert "a.py" in node_ids
    assert "nonexistent.py" not in node_ids


def test_subgraph_all_valid_no_missing_field():
    g = make_simple_graph()
    result = g.subgraph(["a.py", "b.py"])
    assert "missing_paths" not in result


def test_save_creates_parent_dirs(tmp_path):
    g = make_simple_graph()
    filepath = tmp_path / ".fedora-nexus" / "graph.json"
    g.save(filepath)


def test_to_adjacency_json_includes_extra_kwargs():
    g = DependencyGraph()
    g.add_node("foo.py#function:bar", language="python", kind="function",
               name="bar", start_line=5, end_line=10, content="def bar(): pass")
    data = g.to_adjacency_json()
    node = next(n for n in data["nodes"] if n["id"] == "foo.py#function:bar")
    assert node["name"] == "bar"
    assert node["start_line"] == 5
    assert node["content"] == "def bar(): pass"


def test_add_edge_custom_rel():
    g = DependencyGraph()
    g.add_node("a.py", language="python")
    g.add_node("b.py", language="python")
    g.add_edge("a.py", "b.py", rel="IMPORTS")
    data = g.to_adjacency_json()
    edge = next(e for e in data["edges"] if e["from"] == "a.py")
    assert edge["rel"] == "IMPORTS"


def test_graph_load_preserves_rel(tmp_path):
    g = DependencyGraph()
    g.add_node("a.py", language="python")
    g.add_node("b.py", language="python")
    g.add_edge("a.py", "b.py", rel="IMPORTS")
    p = tmp_path / "graph.json"
    g.save(str(p))
    loaded = DependencyGraph.load(str(p))
    data = loaded.to_adjacency_json()
    edge = next(e for e in data["edges"] if e["from"] == "a.py")
    assert edge["rel"] == "IMPORTS"


def test_transitive_dependencies():
    g = make_simple_graph()
    deps = g.get_transitive_dependencies("a.py", depth=2)
    assert "b.py" in deps
    assert "c.py" in deps


def test_transitive_dependents():
    g = make_simple_graph()
    deps = g.get_transitive_dependents("c.py", depth=2)
    assert "b.py" in deps
    assert "a.py" in deps
