"""Core dependency graph engine backed by networkx.

Supported edge ``rel`` values:
  DEPENDS_ON  — file imports/requires another file (default)
  CONTAINS    — file/class contains a symbol (function/class/method)
  CALLS       — symbol calls another symbol (best-effort, Python only)

Symbol node ID format: ``{rel_path}#{kind}:{qualified_name}``

Examples::

  src/utils.py#function:parse_args
  src/models.py#class:User
  src/models.py#method:User.save

Indexing modes:
  file_only    — symbol_mode=False (default) — one node per file, DEPENDS_ON edges only
  with_symbols — symbol_mode=True — adds symbol nodes, CONTAINS and CALLS edges
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import networkx as nx

logger = logging.getLogger(__name__)


class DependencyGraph:
    def __init__(self) -> None:
        self._g: nx.DiGraph = nx.DiGraph()

    def add_node(self, path: str, language: str, kind: str = "file", **kwargs) -> None:
        self._g.add_node(path, path=path, language=language, kind=kind, **kwargs)

    def add_edge(self, from_path: str, to_path: str, rel: str = "DEPENDS_ON") -> None:
        self._g.add_edge(from_path, to_path, rel=rel)

    def get_dependencies(self, path: str) -> list[str]:
        return list(self._g.successors(path))

    def get_dependents(self, path: str) -> list[str]:
        return list(self._g.predecessors(path))

    def nodes(self) -> list[str]:
        return list(self._g.nodes)

    def node_attrs(self, path: str) -> dict[str, Any]:
        return dict(self._g.nodes[path])

    def has_node(self, path: str) -> bool:
        return self._g.has_node(path)

    def to_adjacency_json(self) -> dict:
        nodes = [
            {"id": n, **data}
            for n, data in self._g.nodes(data=True)
        ]
        edges = [
            {"from": u, "to": v, **data}
            for u, v, data in self._g.edges(data=True)
        ]
        return {"nodes": nodes, "edges": edges}

    def subgraph(self, paths: list[str]) -> dict:
        missing = [p for p in paths if not self._g.has_node(p)]
        sub = self._g.subgraph(paths)
        nodes = [{"id": n, **data} for n, data in sub.nodes(data=True)]
        edges = [{"from": u, "to": v, **data} for u, v, data in sub.edges(data=True)]
        result = {"nodes": nodes, "edges": edges}
        if missing:
            result["missing_paths"] = missing
        return result

    def get_transitive_dependencies(self, path: str, depth: int) -> list[str]:
        if depth <= 0:
            return []
        visited: set[str] = set()
        queue = [(path, 0)]
        while queue:
            current, d = queue.pop(0)
            if d >= depth:
                continue
            for dep in self._g.successors(current):
                if dep not in visited:
                    visited.add(dep)
                    queue.append((dep, d + 1))
        return list(visited)

    def get_transitive_dependents(self, path: str, depth: int) -> list[str]:
        if depth <= 0:
            return []
        visited: set[str] = set()
        queue = [(path, 0)]
        while queue:
            current, d = queue.pop(0)
            if d >= depth:
                continue
            for dep in self._g.predecessors(current):
                if dep not in visited:
                    visited.add(dep)
                    queue.append((dep, d + 1))
        return list(visited)

    def save(self, filepath: str | Path) -> None:
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        data = self.to_adjacency_json()
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f)
        logger.debug("Graph saved to %s", filepath)

    @classmethod
    def load(cls, filepath: str | Path) -> "DependencyGraph":
        filepath = Path(filepath)
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        g = cls()
        for node in data.get("nodes", []):
            node_id = node["id"]
            g._g.add_node(
                node_id,
                path=node.get("path", node_id),
                language=node.get("language", ""),
                kind=node.get("kind", "file"),
            )
        for edge in data.get("edges", []):
            g._g.add_edge(edge["from"], edge["to"], rel=edge.get("rel", "DEPENDS_ON"))
        logger.debug("Graph loaded from %s: %d nodes, %d edges", filepath, len(data["nodes"]), len(data["edges"]))
        return g

    @property
    def networkx_graph(self) -> nx.DiGraph:
        return self._g
