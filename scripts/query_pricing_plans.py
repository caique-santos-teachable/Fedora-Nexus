"""Query graph for pricing_plan related files and their deps/dependents."""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fedora_nexus.graph.engine import DependencyGraph

GRAPH_PATH = str(Path.home() / "code/v0/fedora/.fedora-nexus/graph.json")

graph = DependencyGraph.load(GRAPH_PATH)

pricing_plan_nodes = [n for n in graph.nodes() if "pricing_plan" in n.lower()]

results = []
for node in sorted(pricing_plan_nodes):
    results.append({
        "file": node,
        "dependencies": graph.get_dependencies(node),
        "dependents": graph.get_dependents(node),
    })

print(json.dumps(results, indent=2))
print(f"\nFound {len(pricing_plan_nodes)} pricing_plan files", file=sys.stderr)
