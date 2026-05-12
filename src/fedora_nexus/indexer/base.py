"""Abstract base indexer."""

from __future__ import annotations

from abc import ABC, abstractmethod

from fedora_nexus.graph.engine import DependencyGraph

_EXT_MAP: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".rb": "ruby",
    ".sql": "sql",
}


def detect_language(path: str) -> str | None:
    from pathlib import Path
    return _EXT_MAP.get(Path(path).suffix)


class BaseIndexer(ABC):
    @abstractmethod
    def index(self, root: str, *, symbol_mode: bool = False) -> DependencyGraph:
        """Walk root and build a DependencyGraph."""
