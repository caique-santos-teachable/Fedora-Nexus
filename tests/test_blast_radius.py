"""Tests for blast_radius BFS calculator."""

import pytest
from depgraph.graph.engine import DependencyGraph
from depgraph.graph.blast_radius import blast_radius


def make_graph() -> DependencyGraph:
    """
    Graph:
        A -> B -> C
        A -> D
        E -> B
    """
    g = DependencyGraph()
    for node in ["A", "B", "C", "D", "E"]:
        g.add_node(node, language="python")
    g.add_edge("A", "B")
    g.add_edge("B", "C")
    g.add_edge("A", "D")
    g.add_edge("E", "B")
    return g


def test_blast_radius_single_node():
    g = make_graph()
    result = blast_radius(g, ["B"])
    assert set(result["affected"]) == {"A", "E"}
    assert result["changed"] == ["B"]
    assert result["total"] == 2


def test_blast_radius_depth_map():
    g = make_graph()
    result = blast_radius(g, ["B"])
    assert result["depth_map"]["A"] == 1
    assert result["depth_map"]["E"] == 1


def test_blast_radius_leaf_node():
    g = make_graph()
    result = blast_radius(g, ["C"])
    assert set(result["affected"]) == {"B", "A", "E"}


def test_blast_radius_multiple_changed():
    g = make_graph()
    result = blast_radius(g, ["C", "D"])
    affected = set(result["affected"])
    assert "B" in affected
    assert "A" in affected


def test_blast_radius_no_dependents():
    g = make_graph()
    result = blast_radius(g, ["A"])
    assert result["affected"] == []
    assert result["total"] == 0


def test_blast_radius_all_unknown_nodes():
    g = make_graph()
    result = blast_radius(g, ["nonexistent1", "nonexistent2"])
    assert result["affected"] == []
    assert result["total"] == 0


def test_blast_radius_max_depth():
    g = make_graph()
    result = blast_radius(g, ["C"], max_depth=1)
    # C <- B (depth 1), B <- A and B <- E (depth 2, exceeds max_depth=1)
    assert set(result["affected"]) == {"B"}


def test_blast_radius_unknown_node():
    g = make_graph()
    result = blast_radius(g, ["nonexistent"])
    assert result["affected"] == []
