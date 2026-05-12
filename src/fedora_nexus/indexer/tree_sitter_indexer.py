"""Unified tree-sitter indexer — coordinates per-language modules."""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath
from typing import Any

from fedora_nexus.graph.engine import DependencyGraph
from fedora_nexus.indexer.base import BaseIndexer, detect_language
from fedora_nexus.indexer.languages.python import PythonIndexer
from fedora_nexus.indexer.languages.ruby import RubyIndexer
from fedora_nexus.indexer.languages.sql import SqlIndexer
from fedora_nexus.indexer.languages.typescript import TypeScriptIndexer

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
    ".sql": "sql",
}


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
    """Coordinates per-language indexers (Python, TypeScript/JS, Ruby).

    Public API is unchanged: ``index(root, symbol_mode=False) -> DependencyGraph``.
    Language-specific AST logic lives in ``indexer/languages/<lang>.py``.
    """

    def __init__(self, languages: list[str] | None = None) -> None:
        self._lang_filter: set[str] | None = set(languages) if languages else None
        self._python = PythonIndexer()
        self._typescript = TypeScriptIndexer()
        self._ruby = RubyIndexer()
        self._sql = SqlIndexer()

    def index(self, root: str, *, symbol_mode: bool = False) -> DependencyGraph:
        root_path = Path(root).resolve()
        graph = DependencyGraph()

        # ── File discovery ────────────────────────────────────────────
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

        # ── Parallel parse ────────────────────────────────────────────
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
            if lang == "sql":
                # SQL has no tree-sitter parser; body is processed directly in symbol pass.
                return rel, lang, f, source, None
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

        n_workers = max(min(os.cpu_count() or 4, max(len(all_files), 1), 16), 1)
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = {pool.submit(_read_and_parse_file, lf): lf for lf in all_files}
            for future in as_completed(futures):
                result = future.result()
                if result is None:
                    continue
                rel, lang, f, source, tree = result
                parsed_trees[rel] = (lang, tree, f, source)
                _parse_count += 1
                graph.add_node(rel, language=lang, name=f.name, content=source[:2000])
                if _parse_count % 50 == 0:
                    logger.info("[INDEXER] progress: parsed %d/%d files ...", _parse_count, total_files)
                # Import extraction runs in the main thread (graph is not thread-safe)
                if lang == "python":
                    self._python.extract_imports(rel, tree, f, root_path, graph)
                elif lang in ("typescript", "javascript"):
                    self._typescript.extract_imports(rel, tree, root_path, f, graph)
                elif lang == "ruby":
                    self._ruby.extract_imports(rel, tree, f, root_path, graph)
                # SQL: no import extraction needed

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

        # ── Symbol extraction pass ──────────────────────────────────────────
        file_symbols: dict[str, dict[str, str]] = {}
        for rel, (lang, tree, f, source) in parsed_trees.items():
            if lang == "python":
                file_symbols[rel] = self._python.extract_symbols(rel, tree, source, graph)
            elif lang in ("typescript", "javascript"):
                file_symbols[rel] = self._typescript.extract_symbols(rel, tree, graph, source=source)
            elif lang == "ruby":
                file_symbols[rel] = self._ruby.extract_symbols(rel, tree, graph, source=source)
            elif lang == "sql":
                file_symbols[rel] = self._sql.extract_symbols(rel, source, graph)

        sym_data = graph.to_adjacency_json()
        logger.info(
            "[INDEXER] symbol extraction done — nodes=%d (symbols=%d) edges=%d",
            len(sym_data["nodes"]),
            len(sym_data["nodes"]) - parsed_count,
            len(sym_data["edges"]),
        )

        # ── Ruby inheritance post-pass ────────────────────────────────────────────
        # Must run after all file_symbols are populated so cross-file superclasses resolve.
        for rel, (lang, _tree, _f, _source) in parsed_trees.items():
            if lang == "ruby":
                self._ruby.resolve_inheritance(rel, graph, file_symbols)

        # ── Ruby cross-file DEPENDS_ON post-pass ─────────────────────────────────
        # Rails uses Zeitwerk autoloading — most cross-file references (superclass,
        # mixins, associations) never appear in require statements. Resolve them now
        # that all file_symbols across the repo are known.
        ruby_files = [rel for rel, (lang, *_) in parsed_trees.items() if lang == "ruby"]
        if ruby_files:
            # Build a flat {qualified_name -> file_rel} map for all Ruby symbols.
            sym_to_file: dict[str, str] = {}
            for file_rel, syms in file_symbols.items():
                for name in syms:
                    # Only register the first file that defines a given name (stable order).
                    if name not in sym_to_file:
                        sym_to_file[name] = file_rel
            for rel in ruby_files:
                self._ruby.resolve_cross_file_deps(rel, graph, file_symbols, sym_to_file)

        # ── CALLS pass ────────────────────────────────────────────────────────
        for rel, (lang, tree, _f, _source) in parsed_trees.items():
            imported_symbols = self._collect_imported_symbols(rel, graph, file_symbols, depth=2)
            file_syms_for_rel = file_symbols.get(rel, {})
            if not imported_symbols and not file_syms_for_rel:
                continue
            if lang == "python":
                self._python.find_calls(
                    tree.root_node, file_symbols.get(rel, {}), imported_symbols, graph
                )
            elif lang in ("typescript", "javascript"):
                self._typescript.find_calls(
                    tree.root_node, file_symbols.get(rel, {}), imported_symbols, graph
                )
            elif lang == "ruby":
                self._ruby.find_calls(
                    tree.root_node, file_symbols.get(rel, {}), imported_symbols, graph
                )

        return graph

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _collect_imported_symbols(
        self,
        rel: str,
        graph: DependencyGraph,
        file_symbols: dict[str, dict[str, str]],
        depth: int = 2,
    ) -> dict[str, str]:
        """Collect symbols from files imported by rel, up to depth hops.

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

