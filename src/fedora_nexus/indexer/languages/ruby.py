"""Ruby AST indexer — imports, symbols, inheritance, and CALLS edges."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from fedora_nexus.graph.engine import DependencyGraph

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

# https://api.rubyonrails.org/classes/ActiveRecord/Associations/ClassMethods.html
_RAILS_ASSOCIATION_NAMES = frozenset({
    "belongs_to", "has_one", "has_many", "has_and_belongs_to_many",
    "has_one_through", "has_many_through",
})

# https://api.rubyonrails.org/classes/ActiveModel/Validations/ClassMethods.html
_RAILS_VALIDATION_NAMES = frozenset({
    "validate", "validates", "validates_each", "validates_with",
    "validates_presence_of", "validates_uniqueness_of",
    "validates_format_of", "validates_length_of", "validates_numericality_of",
    "validates_inclusion_of", "validates_exclusion_of",
    "validates_confirmation_of", "validates_acceptance_of",
})

_RAILS_SCOPE_NAMES = frozenset({"scope", "default_scope"})
_RUBY_MIXIN_NAMES = frozenset({"include", "extend", "prepend"})
_RUBY_ATTR_NAMES = frozenset({
    "attr_accessor", "attr_reader", "attr_writer",
    # Rails class/module-level attr macros
    "cattr_accessor", "cattr_reader", "cattr_writer",
    "mattr_accessor", "mattr_reader", "mattr_writer",
})
_RAILS_ENUM_NAMES = frozenset({"enum"})
_RAILS_DELEGATION_NAMES = frozenset({"delegate", "delegates"})
_RUBY_ALIAS_METHOD_NAMES = frozenset({"alias_method"})
# store :column, :field1, :field2 — first sym is store column, rest are fields
_RAILS_STORE_ACCESSOR_NAMES = frozenset({"store_accessor"})
# ActionController helper exposure
_RAILS_HELPER_METHOD_NAMES = frozenset({"helper_method"})
# ActionController rescue handler
_RAILS_RESCUE_FROM_NAMES = frozenset({"rescue_from"})
# Dynamic method definition
_RUBY_DEFINE_METHOD_NAMES = frozenset({"define_method"})
# Class/module reopening via eval
_RUBY_CLASS_EVAL_NAMES = frozenset({"class_eval", "module_eval"})
# Visibility modifiers wrapping a def
_RUBY_VISIBILITY_NAMES = frozenset({"private", "protected", "public"})


def _ensure_node(graph: DependencyGraph, path: str, language: str) -> None:
    if not graph.has_node(path):
        graph.add_node(path, language=language)


class RubyIndexer:
    """Handles import extraction, symbol extraction, inheritance, and CALLS
    detection for Ruby (including Rails macros)."""

    # ── Inheritance post-pass ─────────────────────────────────────────────────

    def resolve_inheritance(
        self,
        rel: str,
        graph: DependencyGraph,
        file_symbols: dict[str, dict[str, str]],
    ) -> None:
        """Emit INHERITS edges for Ruby class nodes with a `superclass` attribute."""
        all_syms: dict[str, str] = {}
        for syms in file_symbols.values():
            all_syms.update(syms)

        for sym_id in list(graph.nodes()):
            if not sym_id.startswith(rel + "#class:"):
                continue
            attrs = graph.node_attrs(sym_id)
            superclass = attrs.get("superclass")
            if not superclass:
                continue
            parent_sym_id = all_syms.get(superclass)
            if parent_sym_id and parent_sym_id != sym_id:
                graph.add_edge(sym_id, parent_sym_id, rel="INHERITS")

    # ── Cross-file DEPENDS_ON post-pass ──────────────────────────────────────

    def resolve_cross_file_deps(
        self,
        rel: str,
        graph: DependencyGraph,
        file_symbols: dict[str, dict[str, str]],
        sym_to_file: dict[str, str],
    ) -> None:
        """Emit DEPENDS_ON edges at two levels of precision:

        1. File → File  (course.rb → publishable.rb)
        2. Class/Module → Class/Module  (course.rb#class:Course → publishable.rb#module:Publishable)

        Sources: superclass, mixins (include/extend/prepend), associations
        (belongs_to/has_many/…), and rescue_from exception classes.

        Rails uses Zeitwerk autoloading — most cross-file references never appear
        in require statements. This pass resolves them after all symbols are known.

        sym_to_file: flat map {qualified_name -> file_rel} for every symbol in the repo.
        """
        # Flat {name -> sym_id} for cross-file symbol-level edge resolution.
        all_syms: dict[str, str] = {}
        for syms in file_symbols.values():
            all_syms.update(syms)

        for sym_id in list(graph.nodes()):
            if not sym_id.startswith(rel + "#"):
                continue
            attrs = graph.node_attrs(sym_id)
            kind = attrs.get("kind", "")

            # 1. Superclass / class inheritance
            if kind == "class":
                superclass = attrs.get("superclass")
                if superclass:
                    self._emit_dep(rel, superclass, sym_to_file, graph)
                    # Do NOT emit a sym-to-sym DEPENDS_ON here:
                    # resolve_inheritance already emitted Class→Class INHERITS for this pair.
                    # NetworkX DiGraph only supports one edge per (from, to) pair —
                    # a DEPENDS_ON here would silently overwrite the INHERITS edge.

            # 2. Mixins: include / extend / prepend Mod
            elif kind == "mixin":
                mixin_name = attrs.get("name", "")
                if mixin_name:
                    self._emit_dep(rel, mixin_name, sym_to_file, graph)
                    # enclosing class/module → DEPENDS_ON → target module/class
                    target_sym = all_syms.get(mixin_name)
                    if target_sym and target_sym.split("#")[0] != rel:
                        enclosing = self._enclosing_class(sym_id, graph)
                        if enclosing:
                            graph.add_edge(enclosing, target_sym, rel="DEPENDS_ON")

            # 3. Associations: belongs_to / has_many / etc → target model file
            elif kind == "association":
                assoc_name = attrs.get("name", "")
                if assoc_name:
                    # Rails convention: :school → School, :course_sections → CourseSection
                    const = self._assoc_to_const(assoc_name)
                    self._emit_dep(rel, const, sym_to_file, graph)
                    # enclosing class → DEPENDS_ON → model class
                    target_sym = all_syms.get(const)
                    if target_sym and target_sym.split("#")[0] != rel:
                        enclosing = self._enclosing_class(sym_id, graph)
                        if enclosing:
                            graph.add_edge(enclosing, target_sym, rel="DEPENDS_ON")

            # 4. rescue_from ExceptionClass
            elif kind == "rescue_from":
                exc_name = attrs.get("name", "")
                if exc_name and exc_name != "__unknown__":
                    self._emit_dep(rel, exc_name, sym_to_file, graph)
                    # enclosing class → DEPENDS_ON → exception class
                    target_sym = all_syms.get(exc_name)
                    if target_sym and target_sym.split("#")[0] != rel:
                        enclosing = self._enclosing_class(sym_id, graph)
                        if enclosing:
                            graph.add_edge(enclosing, target_sym, rel="DEPENDS_ON")

            # 5. delegate :method, to: :target — target is a symbol name, not a const,
            #    but if it maps to an association we can resolve transitively.
            # (Skipped — target is a method/attr name, not a constant; too ambiguous)

            # 6. struct / data_class (Struct.new / Data.define) — self-contained, no dep

            # 7. constant assignments that reference known constants on RHS
            # (Would require deeper AST analysis — deferred)

            # 8. Constant references inside method bodies
            #    scope_refs is collected during _walk_symbols by _collect_const_refs.
            #    Only refs that resolve via sym_to_file are emitted — framework constants
            #    (Rails, I18n, Time, …) are silently skipped.
            elif kind in ("method", "class_method"):
                scope_refs: list[str] = attrs.get("scope_refs") or []
                for const_ref in scope_refs:
                    self._emit_dep(rel, const_ref, sym_to_file, graph)
                    # Method → Class CALLS (cross-file only)
                    target_sym = all_syms.get(const_ref)
                    if target_sym and target_sym.split("#")[0] != rel:
                        graph.add_edge(sym_id, target_sym, rel="CALLS")

    @staticmethod
    def _enclosing_class(sym_id: str, graph: DependencyGraph) -> str | None:
        """Return the class or module sym_id that CONTAINS this symbol, if any.

        Traverses the CONTAINS edge backwards (predecessors) to find the enclosing
        class or module node. Returns None if the symbol is file-level.
        """
        for parent in graph.get_dependents(sym_id):
            if "#class:" in parent or "#module:" in parent:
                return parent
        return None

    @staticmethod
    def _assoc_to_const(name: str) -> str:
        """Convert a Rails association name to the expected model constant.

        Examples:
          school          → School
          course_sections → CourseSection  (singularize last word + CamelCase)
          school_id       → School
        """
        # Strip common suffixes
        for suffix in ("_ids", "_id"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                break
        # Naive singularize: strip trailing 's' (handles most Rails cases)
        # and 'es' for words ending in 'es' (boxes → box, etc.)
        parts = name.split("_")
        last = parts[-1]
        if last.endswith("ies"):
            last = last[:-3] + "y"
        elif last.endswith("ses") or last.endswith("xes") or last.endswith("zes"):
            last = last[:-2]
        elif last.endswith("s") and not last.endswith("ss"):
            last = last[:-1]
        parts[-1] = last
        return "".join(p.capitalize() for p in parts)

    @staticmethod
    @staticmethod
    def _collect_const_refs(node: Any) -> set[str]:
        """Collect PascalCase constant references in an AST subtree (method body).

        Returns full scope_resolution texts (e.g. 'Transactions::Refund') and
        standalone PascalCase constants (e.g. 'User'). Constituent constants of
        a scope_resolution are NOT returned separately to avoid duplicates.
        Does NOT recurse into nested class/module definitions — those have their
        own scope and are handled by the class-level post-passes.

        Uses an explicit stack (no recursion) to avoid hitting Python's call
        stack limit on deeply nested method bodies.
        """
        _STOP_TYPES = frozenset({"class", "module", "singleton_class"})
        refs: set[str] = set()
        stack = list(node.children)  # skip the method node itself; walk its children
        while stack:
            n = stack.pop()
            if n.type in _STOP_TYPES:
                continue
            if n.type == "scope_resolution":
                text = n.text.decode("utf-8").strip()
                if text:
                    refs.add(text)
                continue  # skip children — full text already captured
            if n.type == "constant":
                text = n.text.decode("utf-8").strip()
                if text and text[0].isupper():
                    refs.add(text)
                continue  # leaf node
            stack.extend(n.children)
        return refs

    @staticmethod
    def _emit_dep(
        rel: str,
        const_name: str,
        sym_to_file: dict[str, str],
        graph: DependencyGraph,
    ) -> None:
        """Emit DEPENDS_ON from rel to the file that defines const_name (if known and different)."""
        target_file = sym_to_file.get(const_name)
        if target_file and target_file != rel:
            _ensure_node(graph, target_file, "ruby")
            graph.add_edge(rel, target_file)  # networkx DiGraph ignores duplicate edges

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
        if node.type == "call":
            id_node = next((c for c in node.children if c.type == "identifier"), None)
            if id_node:
                method_name = id_node.text.decode("utf-8")
                if method_name in ("require", "require_relative", "require_dependency"):
                    arg_list = next(
                        (c for c in node.children if c.type == "argument_list"), None
                    )
                    if arg_list:
                        raw = self._get_string_content(arg_list)
                        if raw:
                            if method_name == "require_relative":
                                dep = self._resolve_relative(raw, current_file, root_path)
                            else:
                                dep = self._resolve_absolute(raw, root_path)
                            if dep and dep != rel:
                                _ensure_node(graph, dep, "ruby")
                                graph.add_edge(rel, dep)
                elif method_name == "autoload":
                    arg_list = next(
                        (c for c in node.children if c.type == "argument_list"), None
                    )
                    if arg_list:
                        raw = self._get_string_content(arg_list)
                        if raw:
                            dep = self._resolve_absolute(raw, root_path)
                            if dep and dep != rel:
                                _ensure_node(graph, dep, "ruby")
                                graph.add_edge(rel, dep)

        for child in node.children:
            self._walk_imports(child, rel, current_file, root_path, graph)

    def _get_string_content(self, node: Any) -> str | None:
        for child in node.children:
            if child.type == "string":
                for sc in child.children:
                    if sc.type == "string_content":
                        return sc.text.decode("utf-8")
        return None

    def _resolve_relative(
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

    def _resolve_absolute(self, raw: str, root_path: Path) -> str | None:
        base = root_path / raw
        for candidate in [base, base.with_suffix(".rb")]:
            if candidate.exists():
                return str(PurePosixPath(candidate.relative_to(root_path)))
        return None

    # ── Symbols ───────────────────────────────────────────────────────────────

    def extract_symbols(
        self, rel: str, tree: Any, graph: DependencyGraph, source: str = ""
    ) -> dict[str, str]:
        top_level: dict[str, str] = {}
        self._walk_symbols(
            tree.root_node, rel, graph,
            parent_id=rel, scope_stack=[], source=source, top_level=top_level,
        )
        return top_level

    def _walk_symbols(
        self,
        node: Any,
        rel: str,
        graph: DependencyGraph,
        parent_id: str,
        scope_stack: list[tuple[str, str]],
        source: str = "",
        top_level: dict[str, str] | None = None,
        _in_singleton_class: bool = False,
    ) -> None:
        if node.type == "singleton_class":
            # class << self — walk body with flag set; methods inside are class methods.
            # Do NOT push a new scope entry: these methods belong to the enclosing class/module.
            for child in node.children:
                self._walk_symbols(
                    child, rel, graph, parent_id, scope_stack,
                    source=source, top_level=top_level, _in_singleton_class=True,
                )
            return

        if node.type == "class":
            # Accept both a plain constant (class Course) and a scope_resolution
            # name (class Foo::Bar or class PublicApi::V2::Controller).
            name_node = next(
                (c for c in node.children if c.type in ("constant", "scope_resolution")),
                None,
            )
            if name_node:
                class_name = name_node.text.decode("utf-8")   # e.g. "Foo::Bar" or "Course"
                short_name = class_name.split("::")[-1]        # last segment only
                namespace = "::".join(n for _, n in scope_stack)
                qualified_class_name = f"{namespace}::{class_name}" if namespace else class_name
                superclass_node = next((c for c in node.children if c.type == "superclass"), None)
                superclass_name: str | None = None
                if superclass_node:
                    # Also accept scope_resolution superclasses (e.g. < Foo::ApplicationController)
                    sc_node = next(
                        (c for c in superclass_node.children
                         if c.type in ("constant", "scope_resolution")),
                        None,
                    )
                    if sc_node:
                        # Use only the last segment — sym_to_file is keyed by simple names
                        superclass_name = sc_node.text.decode("utf-8").split("::")[-1]
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                content = source.encode("utf-8")[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")[:8000]
                sym_id = f"{rel}#class:{qualified_class_name}"
                node_kwargs: dict[str, Any] = dict(
                    language="ruby", kind="class",
                    name=class_name, file_path=rel, start_line=start_line,
                    end_line=end_line, content=content, is_exported=False,
                )
                if superclass_name:
                    node_kwargs["superclass"] = superclass_name
                graph.add_node(sym_id, **node_kwargs)
                graph.add_edge(parent_id, sym_id, rel="CONTAINS")
                if top_level is not None:
                    top_level[qualified_class_name] = sym_id
                    # Always register the short (last-segment) name so that
                    # resolve_inheritance can look up superclasses by simple name
                    # even when the class is nested inside a module namespace.
                    top_level[short_name] = sym_id
                for child in node.children:
                    self._walk_symbols(
                        child, rel, graph, sym_id,
                        scope_stack + [("class", class_name)],  # full name in scope
                        source=source, top_level=top_level, _in_singleton_class=False,
                    )
            return

        elif node.type == "module":
            # Accept both a plain constant (module Publishable) and a scope_resolution
            # name (module PublicApi::AdminApi::V2).
            name_node = next(
                (c for c in node.children if c.type in ("constant", "scope_resolution")),
                None,
            )
            if name_node:
                mod_name = name_node.text.decode("utf-8")   # e.g. "PublicApi::AdminApi::V2"
                short_name = mod_name.split("::")[-1]        # last segment only
                namespace = "::".join(n for _, n in scope_stack)
                qualified_mod_name = f"{namespace}::{mod_name}" if namespace else mod_name
                start_line = node.start_point[0] + 1
                end_line = node.end_point[0] + 1
                content = source.encode("utf-8")[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")[:8000]

                def _has_concern_extend(n: Any) -> bool:
                    if n.type == "call":
                        id_ch = next((c for c in n.children if c.type == "identifier"), None)
                        if id_ch and id_ch.text.decode("utf-8") == "extend":
                            arg_list = next((c for c in n.children if c.type == "argument_list"), None)
                            if arg_list:
                                return any(
                                    "Concern" in c.text.decode("utf-8")
                                    for c in arg_list.children
                                    if c.type in ("constant", "scope_resolution")
                                )
                    return any(_has_concern_extend(c) for c in n.children)

                kind = "concern" if _has_concern_extend(node) else "module"
                sym_id = f"{rel}#module:{qualified_mod_name}"
                graph.add_node(sym_id, language="ruby", kind=kind,
                               name=mod_name, file_path=rel, start_line=start_line,
                               end_line=end_line, content=content, is_exported=False)
                graph.add_edge(parent_id, sym_id, rel="CONTAINS")
                if top_level is not None:
                    top_level[qualified_mod_name] = sym_id
                    # Always register the short name so sym_to_file resolves simple refs
                    top_level[short_name] = sym_id
                    # When scope_resolution, also register the full (unqualified) name
                    if "::".join(n for _, n in scope_stack) and mod_name not in top_level:
                        top_level[mod_name] = sym_id
                for child in node.children:
                    self._walk_symbols(
                        child, rel, graph, sym_id,
                        scope_stack + [("module", mod_name)],  # full name in scope
                        source=source, top_level=top_level, _in_singleton_class=False,
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
                # owner_name carries the full qualified scope (FQCN) for predictable Cypher queries
                owner_name = prefix
                kind = "class_method" if _in_singleton_class else "method"
                sym_id = f"{rel}#method:{qualified}"
                # Collect PascalCase constant refs from the method body so that
                # the cross-file post-pass can emit File→File DEPENDS_ON and
                # Method→Class CALLS without requiring explicit require statements.
                scope_refs = sorted(self._collect_const_refs(node))
                graph.add_node(sym_id, language="ruby", kind=kind,
                               name=method_name, file_path=rel, start_line=start_line,
                               end_line=end_line, content=content,
                               is_exported=False, owner_name=owner_name,
                               scope_refs=scope_refs)
                graph.add_edge(parent_id, sym_id, rel="CONTAINS")
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
                # owner_name carries the full qualified scope (FQCN) for predictable Cypher queries
                owner_name = prefix
                sym_id = f"{rel}#method:{qualified}"
                scope_refs = sorted(self._collect_const_refs(node))
                graph.add_node(sym_id, language="ruby", kind="class_method",
                               name=method_name, file_path=rel, start_line=start_line,
                               end_line=end_line, content=content,
                               is_exported=False, owner_name=owner_name,
                               scope_refs=scope_refs)
                graph.add_edge(parent_id, sym_id, rel="CONTAINS")
                if top_level is not None and len(scope_stack) == 1:
                    top_level[qualified] = sym_id
            return

        elif node.type == "call":
            id_node = next((c for c in node.children if c.type == "identifier"), None)
            if id_node is None:
                for child in node.children:
                    self._walk_symbols(child, rel, graph, parent_id, scope_stack, source=source, top_level=top_level, _in_singleton_class=_in_singleton_class)
                return
            macro = id_node.text.decode("utf-8")
            start_line = node.start_point[0] + 1
            content = source.encode("utf-8")[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")[:2000]

            if macro in _RAILS_HOOK_NAMES:
                arg_list = next((c for c in node.children if c.type == "argument_list"), None)
                callback_name = "__block__"
                if arg_list:
                    sym_node = next(
                        (c for c in arg_list.children if c.type == "simple_symbol"), None
                    )
                    if sym_node:
                        callback_name = sym_node.text.decode("utf-8").lstrip(":")
                sym_id = f"{rel}#hook:{macro}:{callback_name}"
                if not graph.has_node(sym_id):
                    graph.add_node(sym_id, language="ruby", kind="hook",
                                   name=f"{macro}:{callback_name}", macro=macro,
                                   file_path=rel,
                                   start_line=start_line, content=content)
                    graph.add_edge(parent_id, sym_id, rel="CONTAINS")
                return

            if macro in _RAILS_ASSOCIATION_NAMES:
                arg_list = next((c for c in node.children if c.type == "argument_list"), None)
                assoc_name = "__unknown__"
                if arg_list:
                    sym_node = next(
                        (c for c in arg_list.children if c.type in ("simple_symbol", "string")), None
                    )
                    if sym_node:
                        raw = sym_node.text.decode("utf-8")
                        assoc_name = raw.lstrip(":").strip("'\"")
                sym_id = f"{rel}#association:{macro}:{assoc_name}"
                if not graph.has_node(sym_id):
                    graph.add_node(sym_id, language="ruby", kind="association",
                                   name=assoc_name, macro=macro, file_path=rel,
                                   start_line=start_line, content=content)
                    graph.add_edge(parent_id, sym_id, rel="CONTAINS")
                return

            if macro in _RAILS_VALIDATION_NAMES:
                arg_list = next((c for c in node.children if c.type == "argument_list"), None)
                field_name = "__unknown__"
                if arg_list:
                    first = next(
                        (c for c in arg_list.children if c.type in ("simple_symbol", "string")), None
                    )
                    if first:
                        field_name = first.text.decode("utf-8").lstrip(":").strip("'\"")
                sym_id = f"{rel}#validation:{macro}:{field_name}"
                if not graph.has_node(sym_id):
                    graph.add_node(sym_id, language="ruby", kind="validation",
                                   name=field_name, macro=macro, file_path=rel,
                                   start_line=start_line, content=content)
                    graph.add_edge(parent_id, sym_id, rel="CONTAINS")
                return

            if macro in _RAILS_SCOPE_NAMES:
                arg_list = next((c for c in node.children if c.type == "argument_list"), None)
                scope_name = "__unknown__"
                if arg_list:
                    sym_node = next(
                        (c for c in arg_list.children if c.type in ("simple_symbol", "string")), None
                    )
                    if sym_node:
                        scope_name = sym_node.text.decode("utf-8").lstrip(":").strip("'\"")
                sym_id = f"{rel}#scope:{scope_name}"
                if not graph.has_node(sym_id):
                    graph.add_node(sym_id, language="ruby", kind="scope",
                                   name=scope_name, file_path=rel,
                                   start_line=start_line, content=content)
                    graph.add_edge(parent_id, sym_id, rel="CONTAINS")
                return

            if macro in _RUBY_MIXIN_NAMES:
                arg_list = next((c for c in node.children if c.type == "argument_list"), None)
                mixin_name = "__unknown__"
                if arg_list:
                    const_node = next(
                        (c for c in arg_list.children if c.type in ("constant", "scope_resolution")), None
                    )
                    if const_node:
                        mixin_name = const_node.text.decode("utf-8")
                sym_id = f"{rel}#mixin:{macro}:{mixin_name}"
                if not graph.has_node(sym_id):
                    graph.add_node(sym_id, language="ruby", kind="mixin",
                                   name=mixin_name, macro=macro, file_path=rel,
                                   start_line=start_line, content=content)
                    graph.add_edge(parent_id, sym_id, rel="CONTAINS")
                return

            if macro in _RUBY_ATTR_NAMES:
                arg_list = next((c for c in node.children if c.type == "argument_list"), None)
                if arg_list:
                    for sym_node in arg_list.children:
                        if sym_node.type == "simple_symbol":
                            attr_name = sym_node.text.decode("utf-8").lstrip(":")
                            sym_id = f"{rel}#attr:{attr_name}"
                            if not graph.has_node(sym_id):
                                graph.add_node(sym_id, language="ruby", kind="attr",
                                               name=attr_name, macro=macro,
                                               file_path=rel, start_line=start_line,
                                               content=content)
                                graph.add_edge(parent_id, sym_id, rel="CONTAINS")
                return

            if macro in _RAILS_ENUM_NAMES:
                arg_list = next((c for c in node.children if c.type == "argument_list"), None)
                enum_name = "__unknown__"
                if arg_list:
                    pair_node = next((c for c in arg_list.children if c.type == "pair"), None)
                    sym_node = next((c for c in arg_list.children if c.type == "simple_symbol"), None)
                    if pair_node:
                        key = pair_node.children[0] if pair_node.children else None
                        if key:
                            enum_name = key.text.decode("utf-8").rstrip(":").lstrip(":")
                    elif sym_node:
                        enum_name = sym_node.text.decode("utf-8").lstrip(":")
                sym_id = f"{rel}#enum:{enum_name}"
                if not graph.has_node(sym_id):
                    graph.add_node(sym_id, language="ruby", kind="enum",
                                   name=enum_name, file_path=rel,
                                   start_line=start_line, content=content)
                    graph.add_edge(parent_id, sym_id, rel="CONTAINS")
                return

            if macro in _RAILS_DELEGATION_NAMES:
                arg_list = next((c for c in node.children if c.type == "argument_list"), None)
                method_sym = "__unknown__"
                target_name: str | None = None
                if arg_list:
                    sym_node = next(
                        (c for c in arg_list.children if c.type == "simple_symbol"), None
                    )
                    if sym_node:
                        method_sym = sym_node.text.decode("utf-8").lstrip(":")
                    to_pair = next(
                        (c for c in arg_list.children
                         if c.type == "pair" and c.children and
                         c.children[0].text.decode("utf-8").rstrip(":") == "to"),
                        None,
                    )
                    if to_pair and len(to_pair.children) >= 2:
                        val = to_pair.children[-1]
                        target_name = val.text.decode("utf-8").lstrip(":")
                sym_id = f"{rel}#delegate:{method_sym}"
                if not graph.has_node(sym_id):
                    node_kw: dict[str, Any] = dict(
                        language="ruby", kind="delegate",
                        name=method_sym, file_path=rel,
                        start_line=start_line, content=content,
                    )
                    if target_name:
                        node_kw["target"] = target_name
                    graph.add_node(sym_id, **node_kw)
                    graph.add_edge(parent_id, sym_id, rel="CONTAINS")
                return

            if macro in _RUBY_ALIAS_METHOD_NAMES:
                arg_list = next((c for c in node.children if c.type == "argument_list"), None)
                if arg_list:
                    syms = [c for c in arg_list.children if c.type == "simple_symbol"]
                    if len(syms) >= 2:
                        new_name = syms[0].text.decode("utf-8").lstrip(":")
                        old_name = syms[1].text.decode("utf-8").lstrip(":")
                        sym_id = f"{rel}#alias:{new_name}"
                        if not graph.has_node(sym_id):
                            graph.add_node(sym_id, language="ruby", kind="alias",
                                           name=new_name, original=old_name,
                                           file_path=rel, start_line=start_line,
                                           content=content)
                            graph.add_edge(parent_id, sym_id, rel="CONTAINS")
                return

            # ── store_accessor :col, :field1, :field2 ────────────────────────
            if macro in _RAILS_STORE_ACCESSOR_NAMES:
                arg_list = next((c for c in node.children if c.type == "argument_list"), None)
                if arg_list:
                    syms = [c for c in arg_list.children if c.type == "simple_symbol"]
                    # First symbol is the store column name — skip it
                    store_col = syms[0].text.decode("utf-8").lstrip(":") if syms else "__unknown__"
                    for sym_node in syms[1:]:
                        attr_name = sym_node.text.decode("utf-8").lstrip(":")
                        sym_id = f"{rel}#attr:{attr_name}"
                        if not graph.has_node(sym_id):
                            graph.add_node(sym_id, language="ruby", kind="attr",
                                           name=attr_name, macro=macro, store=store_col,
                                           file_path=rel, start_line=start_line,
                                           content=content)
                            graph.add_edge(parent_id, sym_id, rel="CONTAINS")
                return

            # ── helper_method :name, :other ──────────────────────────────────
            if macro in _RAILS_HELPER_METHOD_NAMES:
                arg_list = next((c for c in node.children if c.type == "argument_list"), None)
                if arg_list:
                    for sym_node in arg_list.children:
                        if sym_node.type == "simple_symbol":
                            helper_name = sym_node.text.decode("utf-8").lstrip(":")
                            sym_id = f"{rel}#helper_method:{helper_name}"
                            if not graph.has_node(sym_id):
                                graph.add_node(sym_id, language="ruby", kind="helper_method",
                                               name=helper_name, file_path=rel,
                                               start_line=start_line, content=content)
                                graph.add_edge(parent_id, sym_id, rel="CONTAINS")
                return

            # ── rescue_from ExcClass, with: :handler ─────────────────────────
            if macro in _RAILS_RESCUE_FROM_NAMES:
                arg_list = next((c for c in node.children if c.type == "argument_list"), None)
                exception_name = "__unknown__"
                handler_name: str | None = None
                if arg_list:
                    exc_node = next(
                        (c for c in arg_list.children
                         if c.type in ("constant", "scope_resolution")), None
                    )
                    if exc_node:
                        exception_name = exc_node.text.decode("utf-8")
                    with_pair = next(
                        (c for c in arg_list.children
                         if c.type == "pair" and c.children and
                         c.children[0].text.decode("utf-8").rstrip(":") == "with"),
                        None,
                    )
                    if with_pair and len(with_pair.children) >= 2:
                        handler_name = with_pair.children[-1].text.decode("utf-8").lstrip(":")
                sym_id = f"{rel}#rescue_from:{exception_name}"
                if not graph.has_node(sym_id):
                    rescue_kw: dict[str, Any] = dict(
                        language="ruby", kind="rescue_from",
                        name=exception_name, file_path=rel,
                        start_line=start_line, content=content,
                    )
                    if handler_name:
                        rescue_kw["handler"] = handler_name
                    graph.add_node(sym_id, **rescue_kw)
                    graph.add_edge(parent_id, sym_id, rel="CONTAINS")
                return

            # ── define_method(:name) { ... } ─────────────────────────────────
            if macro in _RUBY_DEFINE_METHOD_NAMES:
                arg_list = next((c for c in node.children if c.type == "argument_list"), None)
                if arg_list:
                    sym_node = next(
                        (c for c in arg_list.children if c.type == "simple_symbol"), None
                    )
                    if sym_node:
                        method_name = sym_node.text.decode("utf-8").lstrip(":")
                        end_line = node.end_point[0] + 1
                        prefix = "::".join(name for _, name in scope_stack)
                        qualified = f"{prefix}.{method_name}" if prefix else method_name
                        owner_name = prefix
                        sym_id = f"{rel}#method:{qualified}"
                        if not graph.has_node(sym_id):
                            graph.add_node(sym_id, language="ruby", kind="method",
                                           name=method_name, file_path=rel,
                                           start_line=start_line, end_line=end_line,
                                           content=content, is_exported=False,
                                           owner_name=owner_name, is_dynamic=True)
                            graph.add_edge(parent_id, sym_id, rel="CONTAINS")
                            if top_level is not None and len(scope_stack) == 1:
                                top_level[qualified] = sym_id
                return

            # ── Klass.class_eval / Mod.module_eval { ... } ───────────────────
            if macro in _RUBY_CLASS_EVAL_NAMES:
                # Receiver is the first `constant` child (e.g. User in User.class_eval)
                receiver = next((c for c in node.children if c.type == "constant"), None)
                if receiver:
                    recv_name = receiver.text.decode("utf-8")
                    for child in node.children:
                        if child.type in ("do_block", "block"):
                            for body_child in child.children:
                                self._walk_symbols(
                                    body_child, rel, graph, parent_id,
                                    scope_stack + [("class", recv_name)],
                                    source=source, top_level=top_level, _in_singleton_class=False,
                                )
                return

            # ── private/protected/public def foo / private :foo ───────────────
            if macro in _RUBY_VISIBILITY_NAMES:
                arg_list = next((c for c in node.children if c.type == "argument_list"), None)
                if arg_list:
                    method_node = next(
                        (c for c in arg_list.children
                         if c.type in ("method", "singleton_method")), None
                    )
                    if method_node:
                        name_node = next(
                            (c for c in method_node.children if c.type == "identifier"), None
                        )
                        if name_node:
                            method_name = name_node.text.decode("utf-8")
                            start_line_m = method_node.start_point[0] + 1
                            end_line_m = method_node.end_point[0] + 1
                            content_m = source.encode("utf-8")[method_node.start_byte:method_node.end_byte].decode("utf-8", errors="ignore")[:8000]
                            prefix = "::".join(name for _, name in scope_stack)
                            qualified = f"{prefix}.{method_name}" if prefix else method_name
                            # owner_name carries the FQCN for predictable Cypher queries
                            owner_name = prefix
                            kind = "class_method" if (method_node.type == "singleton_method" or _in_singleton_class) else "method"
                            sym_id = f"{rel}#method:{qualified}"
                            if not graph.has_node(sym_id):
                                graph.add_node(sym_id, language="ruby", kind=kind,
                                               name=method_name, file_path=rel,
                                               start_line=start_line_m, end_line=end_line_m,
                                               content=content_m, is_exported=False,
                                               owner_name=owner_name, visibility=macro)
                                graph.add_edge(parent_id, sym_id, rel="CONTAINS")
                                if top_level is not None and len(scope_stack) == 1:
                                    top_level[qualified] = sym_id
                # private :foo / bare private — no new node to create
                return

        elif node.type == "assignment":
            lhs = node.children[0] if node.children else None
            if lhs and lhs.type == "constant":
                const_name = lhs.text.decode("utf-8")
                namespace = "::".join(n for _, n in scope_stack)
                qualified_const = f"{namespace}::{const_name}" if namespace else const_name
                start_line = node.start_point[0] + 1
                content = source.encode("utf-8")[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")[:500]
                # Detect Struct.new / Data.define on the RHS
                rhs = node.children[-1] if len(node.children) >= 3 else None
                kind = "constant"
                sym_id = f"{rel}#constant:{qualified_const}"
                fields: list[str] = []
                if rhs and rhs.type == "call":
                    recv = next((c for c in rhs.children if c.type == "constant"), None)
                    meth = next((c for c in rhs.children if c.type == "identifier"), None)
                    if recv and meth:
                        recv_name = recv.text.decode("utf-8")
                        meth_name = meth.text.decode("utf-8")
                        if recv_name == "Struct" and meth_name == "new":
                            kind = "struct"
                            sym_id = f"{rel}#struct:{qualified_const}"
                            arg_list = next(
                                (c for c in rhs.children if c.type == "argument_list"), None
                            )
                            if arg_list:
                                fields = [
                                    c.text.decode("utf-8").lstrip(":")
                                    for c in arg_list.children
                                    if c.type == "simple_symbol"
                                ]
                        elif recv_name == "Data" and meth_name == "define":
                            kind = "data_class"
                            sym_id = f"{rel}#data_class:{qualified_const}"
                            arg_list = next(
                                (c for c in rhs.children if c.type == "argument_list"), None
                            )
                            if arg_list:
                                fields = [
                                    c.text.decode("utf-8").lstrip(":")
                                    for c in arg_list.children
                                    if c.type == "simple_symbol"
                                ]
                if not graph.has_node(sym_id):
                    node_kw: dict[str, Any] = dict(
                        language="ruby", kind=kind,
                        name=const_name, file_path=rel,
                        start_line=start_line, content=content,
                    )
                    if fields:
                        node_kw["fields"] = fields
                    graph.add_node(sym_id, **node_kw)
                    graph.add_edge(parent_id, sym_id, rel="CONTAINS")
                    if top_level is not None and not scope_stack:
                        top_level[const_name] = sym_id
            return

        elif node.type == "alias":
            id_children = [c for c in node.children if c.type == "identifier"]
            if len(id_children) >= 2:
                new_name = id_children[0].text.decode("utf-8")
                old_name = id_children[1].text.decode("utf-8")
                start_line = node.start_point[0] + 1
                content = source.encode("utf-8")[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")[:500]
                sym_id = f"{rel}#alias:{new_name}"
                if not graph.has_node(sym_id):
                    graph.add_node(sym_id, language="ruby", kind="alias",
                                   name=new_name, original=old_name,
                                   file_path=rel, start_line=start_line,
                                   content=content)
                    graph.add_edge(parent_id, sym_id, rel="CONTAINS")
            return

        for child in node.children:
            self._walk_symbols(child, rel, graph, parent_id, scope_stack, source=source, top_level=top_level, _in_singleton_class=_in_singleton_class)

    # ── CALLS ─────────────────────────────────────────────────────────────────

    def find_calls(
        self,
        node: Any,
        file_syms: dict[str, str],
        imported_symbols: dict[str, str],
        graph: DependencyGraph,
    ) -> None:
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
                    all_call_symbols = {
                        k: v for k, v in {**imported_symbols, **file_syms}.items()
                        if v != caller_id
                    }
                    for child in node.children:
                        self._walk_for_calls(child, caller_id, all_call_symbols, graph)
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
        if node.type == "call":
            method_node = next((c for c in node.children if c.type == "identifier"), None)
            if method_node:
                self._emit_calls_edge(caller_id, method_node.text.decode("utf-8"), imported_symbols, graph)
        elif node.type == "identifier":
            self._emit_calls_edge(caller_id, node.text.decode("utf-8"), imported_symbols, graph)
        for child in node.children:
            self._walk_for_calls(child, caller_id, imported_symbols, graph)

    def _emit_calls_edge(
        self,
        caller_id: str,
        name: str,
        imported_symbols: dict[str, str],
        graph: DependencyGraph,
    ) -> None:
        if name in imported_symbols:
            graph.add_edge(caller_id, imported_symbols[name], rel="CALLS")
        else:
            for k, v in imported_symbols.items():
                if k.endswith(f".{name}"):
                    graph.add_edge(caller_id, v, rel="CALLS")
                    break
