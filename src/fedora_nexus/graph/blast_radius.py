"""BFS blast radius calculator."""

from __future__ import annotations

from collections import deque

from fedora_nexus.graph.engine import DependencyGraph


def blast_radius(
    graph: DependencyGraph,
    changed_paths: list[str],
    max_depth: int = 10,
) -> dict:
    """BFS over reverse edges to find all files affected by changes."""
    depth_map: dict[str, int] = {}
    visited: set[str] = set(changed_paths)
    queue: deque[tuple[str, int]] = deque((p, 0) for p in changed_paths)

    affected: list[str] = []

    while queue:
        current, depth = queue.popleft()
        if depth >= max_depth:
            continue
        if not graph.has_node(current):
            continue
        for dependent in graph.get_dependents(current):
            if dependent not in visited:
                visited.add(dependent)
                affected.append(dependent)
                depth_map[dependent] = depth + 1
                queue.append((dependent, depth + 1))

    return {
        "changed": list(changed_paths),
        "affected": affected,
        "depth_map": depth_map,
        "total": len(affected),
    }
