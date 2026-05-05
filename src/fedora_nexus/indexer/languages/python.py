"""Python AST indexer — imports, symbols, and CALLS edges."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from fedora_nexus.graph.engine import DependencyGraph


def _ensure_node(graph: DependencyGraph, path: str, language: str) -> None:
    if not graph.has_node(path):
        graph.add_node(path, language=language)


class PythonIndexer:
    """Handles import extraction, symbol extraction, and CALLS detection for Python."""

    # ── Imports ───────────────────────────────────────────────────────────────

    def extract_imports(
        self,
        rel: str,
        tree: Any,
        current_file: Path,
        root_path: Path,
        graph: DependencyGraph,
    ) -> None:
        self._walk_imports(tree.root_node, rel, current_file, root_path, graph)

    def _walk_imports(
        self,
        node: Any,
        rel: str,
        current_file: Path,
        root_path: Path,
        graph: DependencyGraph,
    ) -> None:
        if node.type == "import_statement":
            for child in node.children:
                if child.type == "dotted_name":
                    module = child.text.decode("utf-8")
                    dep = self._resolve_absolute(module, root_path)
                    if dep and dep != rel:
                        _ensure_node(graph, dep, "python")
                        graph.add_edge(rel, dep)

        elif node.type == "import_from_statement":
            module: str | None = None
            level = 0
            for child in node.children:
                if child.type == "relative_import":
                    for sub in child.children:
                        if sub.type == "import_prefix":
                            level = sum(1 for c in sub.children if c.type == ".")
                        elif sub.type == "dotted_name":
                            module = sub.text.decode("utf-8")
                    break
                elif child.type == "dotted_name":
                    module = child.text.decode("utf-8")
                    break

            if level > 0:
                dep = self._resolve_relative(module or "", level, current_file, root_path)
            else:
                dep = self._resolve_absolute(module, root_path) if module else None

            if dep and dep != rel:
                _ensure_node(graph, dep, "python")
                graph.add_edge(rel, dep)

        for child in node.children:
            self._walk_imports(child, rel, current_file, root_path, graph)

    def _resolve_absolute(self, module: str | None, root_path: Path) -> str | None:
        if not module:
            return None
        parts = module.split(".")
        for search_root in [root_path, root_path / "src"]:
            for candidate in [
                search_root.joinpath(*parts).with_suffix(".py"),
                search_root.joinpath(*parts, "__init__.py"),
            ]:
                if candidate.exists():
                    return str(PurePosixPath(candidate.relative_to(root_path)))
        return None

    def _resolve_relative(
        self, module: str, level: int, current_file: Path, root_path: Path
    ) -> str | None:
        anchor = current_file.parent
        for _ in range(level - 1):
            anchor = anchor.parent
        if module:
            parts = module.split(".")
            candidates: list[Path] = [
                anchor.joinpath(*parts).with_suffix(".py"),
                anchor.joinpath(*parts, "__init__.py"),
            ]
        else:
            candidates = [anchor / "__init__.py"]
        for candidate in candidates:
            if candidate.exists():
                try:
                    return str(PurePosixPath(candidate.relative_to(root_path)))
                except ValueError:
                    return None
        return None

    # ── Symbols ───────────────────────────────────────────────────────────────

    def extract_symbols(
        self, rel: str, tree: Any, source: str, graph: DependencyGraph
    ) -> dict[str, str]:
        top_level: dict[str, str] = {}
        self._walk_symbols(
            tree.root_node, rel, source, graph,
            parent_id=rel, class_stack=[], top_level=top_level,
        )
        return top_level

    def _walk_symbols(
        self,
        node: Any,
        rel: str,
        source: str,
        graph: DependencyGraph,
        parent_id: str,
        class_stack: list[str],
        top_level: dict[str, str],
    ) -> None:
        if node.type == "function_definition":
            name_node = next((c for c in node.children if c.type == "identifier"), None)
            if name_node:
                name = name_node.text.decode("utf-8")
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                source_bytes = source.encode("utf-8")
                content = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")[:8000]
                if class_stack:
                    sym_id = f"{rel}#method:{class_stack[-1]}.{name}"
                    graph.add_node(sym_id, language="python", kind="method",
                                   name=name, file_path=rel, start_line=start_line,
                                   end_line=end_line, content=content,
                                   is_exported=False, owner_name=class_stack[-1])
                    graph.add_edge(parent_id, sym_id, rel="CONTAINS")
                else:
                    sym_id = f"{rel}#function:{name}"
                    graph.add_node(sym_id, language="python", kind="function",
                                   name=name, file_path=rel, start_line=start_line,
                                   end_line=end_line, content=content, is_exported=False)
                    graph.add_edge(parent_id, sym_id, rel="CONTAINS")
                    top_level[name] = sym_id
            return  # don't recurse into function body

        elif node.type == "class_definition":
            name_node = next((c for c in node.children if c.type == "identifier"), None)
            if name_node:
                class_name = name_node.text.decode("utf-8")
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                source_bytes = source.encode("utf-8")
                content = source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")[:8000]
                sym_id = f"{rel}#class:{class_name}"
                graph.add_node(sym_id, language="python", kind="class",
                               name=class_name, file_path=rel, start_line=start_line,
                               end_line=end_line, content=content, is_exported=False)
                graph.add_edge(parent_id, sym_id, rel="CONTAINS")
                if not class_stack:
                    top_level[class_name] = sym_id
                for child in node.children:
                    self._walk_symbols(
                        child, rel, source, graph, sym_id, class_stack + [class_name], top_level
                    )
            return

        for child in node.children:
            self._walk_symbols(child, rel, source, graph, parent_id, class_stack, top_level)

    # ── CALLS ─────────────────────────────────────────────────────────────────

    def find_calls(
        self,
        node: Any,
        file_syms: dict[str, str],
        imported_symbols: dict[str, str],
        graph: DependencyGraph,
    ) -> None:
        if node.type == "function_definition":
            name_node = next((c for c in node.children if c.type == "identifier"), None)
            if name_node:
                func_name = name_node.text.decode("utf-8")
                caller_id = file_syms.get(func_name)
                if caller_id:
                    for child in node.children:
                        self._walk_for_calls(child, caller_id, imported_symbols, graph)
            return  # don't descend further (avoids nested defs)

        for child in node.children:
            self.find_calls(child, file_syms, imported_symbols, graph)

    def _walk_for_calls(
        self,
        node: Any,
        caller_id: str,
        imported_symbols: dict[str, str],
        graph: DependencyGraph,
    ) -> None:
        if node.type == "call":
            func = node.children[0] if node.children else None
            if func and func.type == "identifier":
                name = func.text.decode("utf-8")
                if name in imported_symbols:
                    graph.add_edge(caller_id, imported_symbols[name], rel="CALLS")
        for child in node.children:
            self._walk_for_calls(child, caller_id, imported_symbols, graph)
