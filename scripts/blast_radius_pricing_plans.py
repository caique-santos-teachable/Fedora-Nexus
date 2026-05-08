"""Compute blast radius for all pricing_plan related files."""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from depgraph.graph.engine import DependencyGraph
from depgraph.graph.blast_radius import blast_radius

GRAPH_PATH = "/Users/caiquesantos/code/v0/fedora/.depgraph/graph.json"

graph = DependencyGraph.load(GRAPH_PATH)

pricing_plan_nodes = sorted(n for n in graph.nodes() if "pricing_plan" in n.lower())

result = blast_radius(graph, pricing_plan_nodes, max_depth=10)

print(json.dumps(result, indent=2))
