"""TypeScript / JavaScript AST indexer — imports, symbols, and CALLS edges."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from fedora_nexus.graph.engine import DependencyGraph
from fedora_nexus.indexer.base import detect_language


def _ensure_node(graph: DependencyGraph, path: str, language: str) -> None:
    if not graph.has_node(path):
        graph.add_node(path, language=language)


class TypeScriptIndexer:
    """Handles import extraction, symbol extraction, and CALLS detection for
    TypeScript and JavaScript (both share the same AST shapes for these features)."""

    # ── Imports ───────────────────────────────────────────────────────────────

    def extract_imports(
        self,
        rel: str,
        tree: Any,
        root_path: Path,
        current_file: Path,
        graph: DependencyGraph,
    ) -> None:
        self._walk_imports(tree.root_node, rel, root_path, current_file, graph)

    def _walk_imports(
        self,
        node: Any,
        rel: str,
        root_path: Path,
        current_file: Path,
        graph: DependencyGraph,
    ) -> None:
        if node.type == "import_statement":
            source = self._get_string_fragment(node)
            if source and source.startswith("."):
                dep = self._resolve(source, current_file, root_path)
                if dep and dep != rel:
                    _ensure_node(graph, dep, detect_language(dep) or "javascript")
                    graph.add_edge(rel, dep)

        elif node.type == "export_statement":
            source = self._get_string_fragment(node)
            if source and source.startswith("."):
                dep = self._resolve(source, current_file, root_path)
                if dep and dep != rel:
                    _ensure_node(graph, dep, detect_language(dep) or "javascript")
                    graph.add_edge(rel, dep)

        elif node.type == "call_expression":
            func = node.children[0] if node.children else None
            if func and func.type == "identifier" and func.text == b"require":
                args = next((c for c in node.children if c.type == "arguments"), None)
                if args:
                    source = self._get_string_fragment(args)
                    if source and source.startswith("."):
                        dep = self._resolve(source, current_file, root_path)
                        if dep and dep != rel:
                            _ensure_node(graph, dep, detect_language(dep) or "javascript")
                            graph.add_edge(rel, dep)

        for child in node.children:
            self._walk_imports(child, rel, root_path, current_file, graph)

    def _get_string_fragment(self, node: Any) -> str | None:
        for child in node.children:
            if child.type == "string":
                for sc in child.children:
                    if sc.type == "string_fragment":
                        return sc.text.decode("utf-8")
        return None

    def _resolve(self, raw: str, current_file: Path, root_path: Path) -> str | None:
        base = (current_file.parent / raw).resolve()
        for candidate in [
            base,
            base.with_suffix(".ts"),
            base.with_suffix(".tsx"),
            base.with_suffix(".js"),
            base.with_suffix(".jsx"),
            base / "index.ts",
            base / "index.tsx",
            base / "index.js",
        ]:
            if candidate.exists():
                try:
                    return str(PurePosixPath(candidate.relative_to(root_path)))
                except ValueError:
                    return None
        return None

    # ── Symbols ───────────────────────────────────────────────────────────────

    def extract_symbols(
        self, rel: str, tree: Any, graph: DependencyGraph, source: str = ""
    ) -> dict[str, str]:
        top_level: dict[str, str] = {}
        self._walk_symbols(
            tree.root_node, rel, graph,
            parent_id=rel, class_stack=[], parent_node=None,
            source=source, top_level=top_level,
        )
        return top_level

    def _walk_symbols(
        self,
        node: Any,
        rel: str,
        graph: DependencyGraph,
        parent_id: str,
        class_stack: list[str],
        parent_node: Any = None,
        source: str = "",
        top_level: dict[str, str] | None = None,
    ) -> None:
        if node.type == "function_declaration":
            name_node = next((c for c in node.children if c.type == "identifier"), None)
            if name_node:
                name = name_node.text.decode("utf-8")
                is_exported = parent_node is not None and parent_node.type == "export_statement"
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                content = source.encode("utf-8")[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")[:8000]
                sym_id = f"{rel}#function:{name}"
                graph.add_node(sym_id, language="typescript", kind="function",
                               name=name, file_path=rel, start_line=start_line,
                               end_line=end_line, content=content, is_exported=is_exported)
                graph.add_edge(parent_id, sym_id, rel="CONTAINS")
                if top_level is not None and not class_stack:
                    top_level[name] = sym_id
            return

        elif node.type == "class_declaration":
            name_node = next((c for c in node.children if c.type == "type_identifier"), None)
            if name_node:
                class_name = name_node.text.decode("utf-8")
                is_exported = parent_node is not None and parent_node.type == "export_statement"
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                content = source.encode("utf-8")[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")[:8000]
                sym_id = f"{rel}#class:{class_name}"
                graph.add_node(sym_id, language="typescript", kind="class",
                               name=class_name, file_path=rel, start_line=start_line,
                               end_line=end_line, content=content, is_exported=is_exported)
                graph.add_edge(parent_id, sym_id, rel="CONTAINS")
                if top_level is not None and not class_stack:
                    top_level[class_name] = sym_id
                for child in node.children:
                    self._walk_symbols(
                        child, rel, graph, sym_id, class_stack + [class_name],
                        parent_node=node, source=source, top_level=top_level,
                    )
            return

        elif node.type == "method_definition":
            name_node = next((c for c in node.children if c.type == "property_identifier"), None)
            if name_node and class_stack:
                method_name = name_node.text.decode("utf-8")
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                content = source.encode("utf-8")[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")[:8000]
                sym_id = f"{rel}#method:{class_stack[-1]}.{method_name}"
                graph.add_node(sym_id, language="typescript", kind="method",
                               name=method_name, file_path=rel, start_line=start_line,
                               end_line=end_line, content=content,
                               is_exported=False, owner_name=class_stack[-1])
                graph.add_edge(parent_id, sym_id, rel="CONTAINS")
            return

        for child in node.children:
            self._walk_symbols(
                child, rel, graph, parent_id, class_stack,
                parent_node=node, source=source, top_level=top_level,
            )

    # ── CALLS ─────────────────────────────────────────────────────────────────

    def find_calls(
        self,
        node: Any,
        file_syms: dict[str, str],
        imported_symbols: dict[str, str],
        graph: DependencyGraph,
    ) -> None:
        if node.type in ("function_declaration", "method_definition"):
            name_node = next(
                (c for c in node.children if c.type in ("identifier", "property_identifier")), None
            )
            if name_node:
                func_name = name_node.text.decode("utf-8")
                caller_id = file_syms.get(func_name)
                if caller_id:
                    for child in node.children:
                        self._walk_for_calls(child, caller_id, imported_symbols, graph)
            return
        for child in node.children:
            self.find_calls(child, file_syms, imported_symbols, graph)

    def _walk_for_calls(
        self,
        node: Any,
        caller_id: str,
        imported_symbols: dict[str, str],
        graph: DependencyGraph,
    ) -> None:
        if node.type == "call_expression":
            func = node.children[0] if node.children else None
            called_name = None
            if func and func.type == "identifier":
                called_name = func.text.decode("utf-8")
            elif func and func.type == "member_expression":
                prop = next((c for c in func.children if c.type == "property_identifier"), None)
                if prop:
                    called_name = prop.text.decode("utf-8")
            if called_name and called_name in imported_symbols:
                graph.add_edge(caller_id, imported_symbols[called_name], rel="CALLS")
        for child in node.children:
            self._walk_for_calls(child, caller_id, imported_symbols, graph)
