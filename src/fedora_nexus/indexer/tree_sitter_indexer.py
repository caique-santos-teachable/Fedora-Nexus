"""Unified tree-sitter indexer for Python, TypeScript, JavaScript, and Ruby."""

from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath
from typing import Any

from fedora_nexus.graph.engine import DependencyGraph
from fedora_nexus.indexer.base import BaseIndexer, detect_language

logger = logging.getLogger(__name__)

_SKIP_DIRS = {
    "vendor", "node_modules", ".git", "__pycache__",
    ".venv", "venv", "dist", "build", ".next",
}

_EXT_TO_LANG = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".rb": "ruby",
}

# Rails/AR callbacks — all before_*/after_*/around_* variants
# https://api.rubyonrails.org/classes/ActiveRecord/Callbacks.html
_RAILS_HOOK_NAMES = frozenset({
    "after_action", "after_commit", "after_create", "after_destroy",
    "after_find", "after_initialize", "after_rollback", "after_save",
    "after_update", "after_validation",
    "around_action", "around_commit", "around_create", "around_destroy",
    "around_save", "around_update", "around_validation",
    "before_action", "before_commit", "before_create", "before_destroy",
    "before_initialize", "before_save", "before_update",
    "before_validation",
    "skip_after_action", "skip_before_action",
})


def _get_parsers() -> dict[str, Any]:
    """Build tree-sitter parsers for each language."""
    from tree_sitter import Language, Parser
    import tree_sitter_python as tspython
    import tree_sitter_javascript as tsjavascript
    import tree_sitter_typescript as tstypescript
    import tree_sitter_ruby as tsruby

    # TypeScript grammar exposes language_typescript() and language_tsx()
    try:
        ts_lang = Language(tstypescript.language_typescript())
        tsx_lang = Language(tstypescript.language_tsx())
    except AttributeError:
        ts_lang = Language(tstypescript.language())
        tsx_lang = ts_lang

    py_lang = Language(tspython.language())
    js_lang = Language(tsjavascript.language())
    rb_lang = Language(tsruby.language())

    return {
        "python": Parser(py_lang),
        "typescript": Parser(ts_lang),
        "typescript_tsx": Parser(tsx_lang),
        "javascript": Parser(js_lang),
        "ruby": Parser(rb_lang),
    }


_PARSERS: dict[str, Any] | None = None
_PARSERS_LOCK = threading.Lock()


def _parsers() -> dict[str, Any]:
    global _PARSERS
    if _PARSERS is None:
        with _PARSERS_LOCK:
            if _PARSERS is None:  # double-checked locking
                _PARSERS = _get_parsers()
    return _PARSERS


def _ensure_node(graph: DependencyGraph, path: str, language: str) -> None:
    if not graph.has_node(path):
        graph.add_node(path, language=language)


class TreeSitterIndexer(BaseIndexer):
    """Unified indexer using tree-sitter for all supported languages.

    Probed node types (tree-sitter 0.25):
      Python:     import_statement, import_from_statement, relative_import,
                  import_prefix, dotted_name, function_definition, class_definition
      TypeScript: import_statement, export_statement, call_expression (require),
                  function_declaration, class_declaration, method_definition,
                  string, string_fragment, type_identifier, property_identifier
      JavaScript: same as TypeScript + call_expression for require()
      Ruby:       call (require/require_relative/hooks), class, module, method,
                  singleton_method, argument_list, simple_symbol, string, string_content
    """

    def __init__(self, languages: list[str] | None = None) -> None:
        self._lang_filter: set[str] | None = set(languages) if languages else None

    def index(self, root: str, *, symbol_mode: bool = False) -> DependencyGraph:
        root_path = Path(root).resolve()
        graph = DependencyGraph()

        # Collect source files grouped by language
        files_by_lang: dict[str, list[Path]] = {}
        for f in root_path.rglob("*"):
            if not f.is_file():
                continue
            if any(part in _SKIP_DIRS for part in f.parts):
                continue
            lang = _EXT_TO_LANG.get(f.suffix)
            if lang is None:
                continue
            if self._lang_filter and lang not in self._lang_filter:
                continue
            files_by_lang.setdefault(lang, []).append(f)

        total_files = sum(len(v) for v in files_by_lang.values())
        logger.info(
            "[INDEXER] %r — found %d files: %s",
            root,
            total_files,
            ", ".join(f"{lang}={len(files)}" for lang, files in sorted(files_by_lang.items())),
        )

        # Add file nodes (minimal — content added in parse loop below)
        for lang, files in files_by_lang.items():
            for f in files:
                rel = str(PurePosixPath(f.relative_to(root_path)))
                graph.add_node(rel, language=lang)

        # Parse files in parallel (G4): read + parse is CPU/IO-bound; tree-sitter
        # releases the GIL so threading gives real parallelism.
        # Graph mutation (add_node/add_edge) happens in the main thread only.
        parsed_trees: dict[str, tuple[str, Any, Path, str]] = {}
        _parse_count = 0

        all_files: list[tuple[str, Path]] = [
            (lang, f) for lang, files in files_by_lang.items() for f in files
        ]

        def _read_and_parse_file(lang_f: tuple[str, Path]) -> tuple[str, str, Path, str, Any] | None:
            lang, f = lang_f
            rel = str(PurePosixPath(f.relative_to(root_path)))
            try:
                source = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                return None
            p = _parsers()
            if lang == "typescript" and f.suffix == ".tsx":
                parser = p.get("typescript_tsx", p["typescript"])
            else:
                parser = p[lang]
            try:
                tree = parser.parse(source.encode("utf-8"))
            except Exception:
                logger.warning("[INDEXER] parse error (skipped): %s", f)
                return None
            return rel, lang, f, source, tree

        n_workers = min(os.cpu_count() or 4, len(all_files), 16)
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = {pool.submit(_read_and_parse_file, lf): lf for lf in all_files}
            for future in as_completed(futures):
                result = future.result()
                if result is None:
                    continue
                rel, lang, f, source, tree = result
                parsed_trees[rel] = (lang, tree, f, source)
                _parse_count += 1
                # Enrich file node with name and content (main thread — graph not thread-safe)
                graph.add_node(rel, language=lang, name=f.name, content=source[:2000])
                if _parse_count % 50 == 0:
                    logger.info("[INDEXER] progress: parsed %d/%d files ...", _parse_count, total_files)
                # Import extraction must happen in main thread
                if lang == "python":
                    self._extract_python_imports(rel, tree, f, root_path, graph)
                elif lang in ("typescript", "javascript"):
                    self._extract_ts_imports(rel, tree, root_path, f, graph)
                elif lang == "ruby":
                    self._extract_ruby_imports(rel, tree, f, root_path, graph)

        parsed_count = len(parsed_trees)
        logger.info(
            "[INDEXER] parsed %d/%d files (%.1f%%) — extracting imports ...",
            parsed_count,
            total_files,
            100 * parsed_count / total_files if total_files else 0,
        )
        data_preview = graph.to_adjacency_json()
        logger.info(
            "[INDEXER] import edges done — nodes=%d edges=%d",
            len(data_preview["nodes"]),
            len(data_preview["edges"]),
        )

        if not symbol_mode:
            return graph

        # Symbol extraction pass (G2: TS/JS and Ruby now return dict[str, str])
        file_symbols: dict[str, dict[str, str]] = {}
        for rel, (lang, tree, f, source) in parsed_trees.items():
            if lang == "python":
                file_symbols[rel] = self._extract_python_symbols(rel, tree, source, graph)
            elif lang in ("typescript", "javascript"):
                file_symbols[rel] = self._extract_ts_symbols(rel, tree, graph, source=source)
            elif lang == "ruby":
                file_symbols[rel] = self._extract_ruby_symbols(rel, tree, graph, source=source)

        sym_data = graph.to_adjacency_json()
        logger.info(
            "[INDEXER] symbol extraction done — nodes=%d (symbols=%d) edges=%d",
            len(sym_data["nodes"]),
            len(sym_data["nodes"]) - parsed_count,
            len(sym_data["edges"]),
        )

        # CALLS pass — Python, TypeScript, JavaScript, Ruby (G2 + G3)
        for rel, (lang, tree, _f, _source) in parsed_trees.items():
            # G3: use transitive imports (depth=2) for symbol resolution
            imported_symbols = self._collect_imported_symbols(rel, graph, file_symbols, depth=2)
            if not imported_symbols:
                continue
            if lang == "python":
                self._find_python_calls(
                    tree.root_node, file_symbols.get(rel, {}), imported_symbols, graph
                )
            elif lang in ("typescript", "javascript"):
                self._find_ts_calls(
                    tree.root_node, file_symbols.get(rel, {}), imported_symbols, graph
                )
            elif lang == "ruby":
                self._find_ruby_calls(
                    tree.root_node, file_symbols.get(rel, {}), imported_symbols, graph
                )

        return graph

    # ── Python ───────────────────────────────────────────────────────────────

    def _extract_python_imports(
        self,
        rel: str,
        tree: Any,
        current_file: Path,
        root_path: Path,
        graph: DependencyGraph,
    ) -> None:
        self._walk_python_imports(tree.root_node, rel, current_file, root_path, graph)

    def _walk_python_imports(
        self,
        node: Any,
        rel: str,
        current_file: Path,
        root_path: Path,
        graph: DependencyGraph,
    ) -> None:
        if node.type == "import_statement":
            # Each dotted_name child is a module being imported
            for child in node.children:
                if child.type == "dotted_name":
                    module = child.text.decode("utf-8")
                    dep = self._resolve_python_absolute(module, root_path)
                    if dep and dep != rel:
                        _ensure_node(graph, dep, "python")
                        graph.add_edge(rel, dep)

        elif node.type == "import_from_statement":
            # Structure: from <module|relative_import> import <names>
            # First non-keyword child is either dotted_name (absolute) or relative_import
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
                dep = self._resolve_python_relative(module or "", level, current_file, root_path)
            else:
                dep = self._resolve_python_absolute(module, root_path) if module else None

            if dep and dep != rel:
                _ensure_node(graph, dep, "python")
                graph.add_edge(rel, dep)

        for child in node.children:
            self._walk_python_imports(child, rel, current_file, root_path, graph)

    def _resolve_python_absolute(self, module: str, root_path: Path) -> str | None:
        parts = module.split(".")
        for search_root in [root_path, root_path / "src"]:
            for candidate in [
                search_root.joinpath(*parts).with_suffix(".py"),
                search_root.joinpath(*parts, "__init__.py"),
            ]:
                if candidate.exists():
                    return str(PurePosixPath(candidate.relative_to(root_path)))
        return None

    def _resolve_python_relative(
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

    def _extract_python_symbols(
        self, rel: str, tree: Any, source: str, graph: DependencyGraph
    ) -> dict[str, str]:
        """Extract function/class symbols. Returns top-level {name: sym_id} for CALLS pass."""
        top_level: dict[str, str] = {}
        self._walk_python_symbols(
            tree.root_node, rel, source, graph, parent_id=rel, class_stack=[], top_level=top_level
        )
        return top_level

    def _walk_python_symbols(
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
                    self._walk_python_symbols(
                        child, rel, source, graph, sym_id, class_stack + [class_name], top_level
                    )
            return

        for child in node.children:
            self._walk_python_symbols(child, rel, source, graph, parent_id, class_stack, top_level)

    def _find_python_calls(
        self,
        node: Any,
        syms_for_rel: dict[str, str],
        imported_symbols: dict[str, str],
        graph: DependencyGraph,
    ) -> None:
        """Walk top-level function_definition nodes and find CALLS to imported symbols."""
        if node.type == "function_definition":
            name_node = next((c for c in node.children if c.type == "identifier"), None)
            if name_node:
                func_name = name_node.text.decode("utf-8")
                caller_id = syms_for_rel.get(func_name)
                if caller_id:
                    for child in node.children:
                        self._walk_for_calls(child, caller_id, imported_symbols, graph)
            return  # don't descend further (avoids nested defs)

        for child in node.children:
            self._find_python_calls(child, syms_for_rel, imported_symbols, graph)

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

    def _collect_imported_symbols(
        self,
        rel: str,
        graph: DependencyGraph,
        file_symbols: dict[str, dict[str, str]],
        depth: int = 2,
    ) -> dict[str, str]:
        """Collect symbols from files imported by rel, up to depth hops (G3).

        Returns a flat {name: sym_id} map including symbols from transitive imports.
        """
        visited: set[str] = set()
        result: dict[str, str] = {}
        queue = [(dep, 1) for dep in graph.get_dependencies(rel) if "#" not in dep]
        while queue:
            dep_rel, d = queue.pop(0)
            if dep_rel in visited:
                continue
            visited.add(dep_rel)
            result.update(file_symbols.get(dep_rel, {}))
            if d < depth:
                for transitive in graph.get_dependencies(dep_rel):
                    if "#" not in transitive and transitive not in visited:
                        queue.append((transitive, d + 1))
        return result

    def _find_ts_calls(self, node: Any, file_syms: dict[str, str], imported_symbols: dict[str, str], graph: DependencyGraph) -> None:
        """Walk function_declaration/method_definition nodes and emit CALLS edges for known symbols."""
        if node.type in ("function_declaration", "method_definition"):
            name_node = next((c for c in node.children if c.type in ("identifier", "property_identifier")), None)
            if name_node:
                func_name = name_node.text.decode("utf-8")
                caller_id = file_syms.get(func_name)
                if caller_id:
                    for child in node.children:
                        self._walk_ts_for_calls(child, caller_id, imported_symbols, graph)
            return  # don't descend into function body past this
        for child in node.children:
            self._find_ts_calls(child, file_syms, imported_symbols, graph)

    def _walk_ts_for_calls(self, node: Any, caller_id: str, imported_symbols: dict[str, str], graph: DependencyGraph) -> None:
        """Walk AST looking for call_expression nodes and emit CALLS edges."""
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
            self._walk_ts_for_calls(child, caller_id, imported_symbols, graph)

    def _find_ruby_calls(self, node: Any, file_syms: dict[str, str], imported_symbols: dict[str, str], graph: DependencyGraph) -> None:
        """Walk method/singleton_method nodes and emit CALLS edges."""
        if node.type in ("method", "singleton_method"):
            name_node = next((c for c in node.children if c.type == "identifier"), None)
            if name_node:
                method_name = name_node.text.decode("utf-8")
                caller_id = file_syms.get(method_name)
                if not caller_id:
                    for k, v in file_syms.items():
                        if k.endswith(f".{method_name}"):
                            caller_id = v
                            break
                if caller_id:
                    for child in node.children:
                        self._walk_ruby_for_calls(child, caller_id, imported_symbols, graph)
            return
        for child in node.children:
            self._find_ruby_calls(child, file_syms, imported_symbols, graph)

    def _walk_ruby_for_calls(self, node: Any, caller_id: str, imported_symbols: dict[str, str], graph: DependencyGraph) -> None:
        """Walk Ruby AST looking for call nodes and emit CALLS edges.

        Handles both explicit call nodes (do_work()) and bare identifiers (do_work)
        which tree-sitter-ruby represents as identifier nodes in statement position.
        Also performs suffix matching for qualified names (e.g., "Helper.do_work").
        """
        if node.type == "call":
            method_node = next((c for c in node.children if c.type == "identifier"), None)
            if method_node:
                name = method_node.text.decode("utf-8")
                self._emit_ruby_calls_edge(caller_id, name, imported_symbols, graph)
        elif node.type == "identifier":
            name = node.text.decode("utf-8")
            self._emit_ruby_calls_edge(caller_id, name, imported_symbols, graph)
        for child in node.children:
            self._walk_ruby_for_calls(child, caller_id, imported_symbols, graph)

    def _emit_ruby_calls_edge(
        self, caller_id: str, name: str, imported_symbols: dict[str, str], graph: DependencyGraph
    ) -> None:
        """Emit a CALLS edge if name (or a qualified variant) is in imported_symbols."""
        if name in imported_symbols:
            graph.add_edge(caller_id, imported_symbols[name], rel="CALLS")
        else:
            # Suffix match for qualified names like "Helper.do_work" → "do_work"
            for k, v in imported_symbols.items():
                if k.endswith(f".{name}"):
                    graph.add_edge(caller_id, v, rel="CALLS")
                    break

    # ── TypeScript / JavaScript ───────────────────────────────────────────────

    def _extract_ts_imports(
        self,
        rel: str,
        tree: Any,
        root_path: Path,
        current_file: Path,
        graph: DependencyGraph,
    ) -> None:
        self._walk_ts_imports(tree.root_node, rel, root_path, current_file, graph)

    def _walk_ts_imports(
        self,
        node: Any,
        rel: str,
        root_path: Path,
        current_file: Path,
        graph: DependencyGraph,
    ) -> None:
        if node.type == "import_statement":
            # string is a direct child: import { x } from './a'
            source = self._get_ts_string_fragment(node)
            if source and source.startswith("."):
                dep = self._resolve_ts(source, current_file, root_path)
                if dep and dep != rel:
                    _ensure_node(graph, dep, detect_language(dep) or "javascript")
                    graph.add_edge(rel, dep)

        elif node.type == "export_statement":
            # export { y } from './b' or export * from './c'
            source = self._get_ts_string_fragment(node)
            if source and source.startswith("."):
                dep = self._resolve_ts(source, current_file, root_path)
                if dep and dep != rel:
                    _ensure_node(graph, dep, detect_language(dep) or "javascript")
                    graph.add_edge(rel, dep)

        elif node.type == "call_expression":
            # const x = require('./lib')
            func = node.children[0] if node.children else None
            if func and func.type == "identifier" and func.text == b"require":
                args = next((c for c in node.children if c.type == "arguments"), None)
                if args:
                    source = self._get_ts_string_fragment(args)
                    if source and source.startswith("."):
                        dep = self._resolve_ts(source, current_file, root_path)
                        if dep and dep != rel:
                            _ensure_node(graph, dep, detect_language(dep) or "javascript")
                            graph.add_edge(rel, dep)

        for child in node.children:
            self._walk_ts_imports(child, rel, root_path, current_file, graph)

    def _get_ts_string_fragment(self, node: Any) -> str | None:
        """Find the first string child of node and return its string_fragment text."""
        for child in node.children:
            if child.type == "string":
                for sc in child.children:
                    if sc.type == "string_fragment":
                        return sc.text.decode("utf-8")
        return None

    def _resolve_ts(self, raw: str, current_file: Path, root_path: Path) -> str | None:
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

    def _extract_ts_symbols(
        self, rel: str, tree: Any, graph: DependencyGraph, source: str = ""
    ) -> dict[str, str]:
        top_level: dict[str, str] = {}
        self._walk_ts_symbols(
            tree.root_node, rel, graph, parent_id=rel, class_stack=[], parent_node=None, source=source, top_level=top_level
        )
        return top_level

    def _walk_ts_symbols(
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
            name_node = next(
                (c for c in node.children if c.type == "type_identifier"), None
            )
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
                    self._walk_ts_symbols(
                        child, rel, graph, sym_id, class_stack + [class_name],
                        parent_node=node, source=source, top_level=top_level
                    )
            return

        elif node.type == "method_definition":
            name_node = next(
                (c for c in node.children if c.type == "property_identifier"), None
            )
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
            self._walk_ts_symbols(child, rel, graph, parent_id, class_stack, parent_node=node, source=source, top_level=top_level)

    # ── Ruby ─────────────────────────────────────────────────────────────────

    def _extract_ruby_imports(
        self,
        rel: str,
        tree: Any,
        current_file: Path,
        root_path: Path,
        graph: DependencyGraph,
    ) -> None:
        self._walk_ruby_imports(tree.root_node, rel, current_file, root_path, graph)

    def _walk_ruby_imports(
        self,
        node: Any,
        rel: str,
        current_file: Path,
        root_path: Path,
        graph: DependencyGraph,
    ) -> None:
        if node.type == "call":
            id_node = next((c for c in node.children if c.type == "identifier"), None)
            if id_node:
                method_name = id_node.text.decode("utf-8")
                if method_name in ("require", "require_relative"):
                    arg_list = next(
                        (c for c in node.children if c.type == "argument_list"), None
                    )
                    if arg_list:
                        raw = self._get_ruby_string_content(arg_list)
                        if raw:
                            if method_name == "require_relative":
                                dep = self._resolve_ruby_relative(raw, current_file, root_path)
                            else:
                                dep = self._resolve_ruby_absolute(raw, root_path)
                            if dep and dep != rel:
                                _ensure_node(graph, dep, "ruby")
                                graph.add_edge(rel, dep)

        for child in node.children:
            self._walk_ruby_imports(child, rel, current_file, root_path, graph)

    def _get_ruby_string_content(self, node: Any) -> str | None:
        """Find string_content inside the first string child of node."""
        for child in node.children:
            if child.type == "string":
                for sc in child.children:
                    if sc.type == "string_content":
                        return sc.text.decode("utf-8")
        return None

    def _resolve_ruby_relative(
        self, raw: str, current_file: Path, root_path: Path
    ) -> str | None:
        base = (current_file.parent / raw).resolve()
        for candidate in [base, base.with_suffix(".rb")]:
            if candidate.exists():
                try:
                    return str(PurePosixPath(candidate.relative_to(root_path)))
                except ValueError:
                    return None
        return None

    def _resolve_ruby_absolute(self, raw: str, root_path: Path) -> str | None:
        base = root_path / raw
        for candidate in [base, base.with_suffix(".rb")]:
            if candidate.exists():
                return str(PurePosixPath(candidate.relative_to(root_path)))
        return None

    def _extract_ruby_symbols(
        self, rel: str, tree: Any, graph: DependencyGraph, source: str = ""
    ) -> dict[str, str]:
        top_level: dict[str, str] = {}
        self._walk_ruby_symbols(
            tree.root_node, rel, graph, parent_id=rel, scope_stack=[], source=source, top_level=top_level
        )
        return top_level

    def _walk_ruby_symbols(
        self,
        node: Any,
        rel: str,
        graph: DependencyGraph,
        parent_id: str,
        scope_stack: list[tuple[str, str]],
        source: str = "",
        top_level: dict[str, str] | None = None,
    ) -> None:
        if node.type == "class":
            name_node = next((c for c in node.children if c.type == "constant"), None)
            if name_node:
                class_name = name_node.text.decode("utf-8")
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                content = source.encode("utf-8")[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")[:8000]
                sym_id = f"{rel}#class:{class_name}"
                graph.add_node(sym_id, language="ruby", kind="class",
                               name=class_name, file_path=rel, start_line=start_line,
                               end_line=end_line, content=content, is_exported=False)
                graph.add_edge(parent_id, sym_id, rel="CONTAINS")
                if top_level is not None and not scope_stack:
                    top_level[class_name] = sym_id
                for child in node.children:
                    self._walk_ruby_symbols(
                        child, rel, graph, sym_id, scope_stack + [("class", class_name)], source=source, top_level=top_level
                    )
            return

        elif node.type == "module":
            name_node = next((c for c in node.children if c.type == "constant"), None)
            if name_node:
                mod_name = name_node.text.decode("utf-8")
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                content = source.encode("utf-8")[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")[:8000]
                sym_id = f"{rel}#module:{mod_name}"
                graph.add_node(sym_id, language="ruby", kind="module",
                               name=mod_name, file_path=rel, start_line=start_line,
                               end_line=end_line, content=content, is_exported=False)
                graph.add_edge(parent_id, sym_id, rel="CONTAINS")
                if top_level is not None and not scope_stack:
                    top_level[mod_name] = sym_id
                for child in node.children:
                    self._walk_ruby_symbols(
                        child, rel, graph, sym_id, scope_stack + [("module", mod_name)], source=source, top_level=top_level
                    )
            return

        elif node.type == "method":
            name_node = next((c for c in node.children if c.type == "identifier"), None)
            if name_node:
                method_name = name_node.text.decode("utf-8")
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                content = source.encode("utf-8")[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")[:8000]
                prefix = "::".join(name for _, name in scope_stack)
                qualified = f"{prefix}.{method_name}" if prefix else method_name
                owner_name = scope_stack[-1][1] if scope_stack else ""
                sym_id = f"{rel}#method:{qualified}"
                graph.add_node(sym_id, language="ruby", kind="method",
                               name=method_name, file_path=rel, start_line=start_line,
                               end_line=end_line, content=content,
                               is_exported=False, owner_name=owner_name)
                graph.add_edge(parent_id, sym_id, rel="CONTAINS")
                # Add qualified name to top_level when owner is a top-level class/module
                if top_level is not None and len(scope_stack) == 1:
                    top_level[qualified] = sym_id
            return

        elif node.type == "singleton_method":
            name_node = next((c for c in node.children if c.type == "identifier"), None)
            if name_node:
                method_name = name_node.text.decode("utf-8")
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                content = source.encode("utf-8")[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")[:8000]
                prefix = "::".join(name for _, name in scope_stack)
                qualified = f"{prefix}.{method_name}" if prefix else method_name
                owner_name = scope_stack[-1][1] if scope_stack else ""
                sym_id = f"{rel}#method:{qualified}"
                graph.add_node(sym_id, language="ruby", kind="class_method",
                               name=method_name, file_path=rel, start_line=start_line,
                               end_line=end_line, content=content,
                               is_exported=False, owner_name=owner_name)
                graph.add_edge(parent_id, sym_id, rel="CONTAINS")
                # Add qualified name to top_level when owner is a top-level class/module
                if top_level is not None and len(scope_stack) == 1:
                    top_level[qualified] = sym_id
            return

        elif node.type == "call":
            id_node = next((c for c in node.children if c.type == "identifier"), None)
            if id_node and id_node.text.decode("utf-8") in _RAILS_HOOK_NAMES:
                hook_type = id_node.text.decode("utf-8")
                arg_list = next(
                    (c for c in node.children if c.type == "argument_list"), None
                )
                callback_name = "__block__"
                if arg_list:
                    sym_node = next(
                        (c for c in arg_list.children if c.type == "simple_symbol"), None
                    )
                    if sym_node:
                        callback_name = sym_node.text.decode("utf-8").lstrip(":")
                sym_id = f"{rel}#hook:{hook_type}:{callback_name}"
                if not graph.has_node(sym_id):
                    graph.add_node(sym_id, language="ruby", kind="hook")
                    graph.add_edge(parent_id, sym_id, rel="CONTAINS")
                return

        for child in node.children:
            self._walk_ruby_symbols(child, rel, graph, parent_id, scope_stack, source=source, top_level=top_level)
