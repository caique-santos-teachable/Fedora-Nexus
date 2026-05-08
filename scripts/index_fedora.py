"""Index the fedora project and save graph to .fedora-nexus/graph.json."""

import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

logging.basicConfig(level=logging.WARNING)

from fedora_nexus.graph.engine import DependencyGraph
from fedora_nexus.indexer.ruby_indexer import RubyIndexer

ROOT = str(Path.home() / "code/v0/fedora")
GRAPH_PATH = Path(ROOT) / ".fedora-nexus" / "graph.json"

print(f"Indexing Ruby files in {ROOT} ...")
graph = RubyIndexer().index(ROOT)

data = graph.to_adjacency_json()
lang_counts: dict[str, int] = {}
for node in data["nodes"]:
    lang = node.get("language", "unknown")
    lang_counts[lang] = lang_counts.get(lang, 0) + 1

GRAPH_PATH.parent.mkdir(parents=True, exist_ok=True)
graph.save(GRAPH_PATH)

print(f"Nodes: {len(data['nodes'])}")
print(f"Edges: {len(data['edges'])}")
print(f"Languages: {lang_counts}")
print(f"Graph saved to {GRAPH_PATH}")
