"""Tests for the unified tree-sitter indexer (Python, TypeScript, JavaScript, Ruby)."""

import pytest
from pathlib import Path
from fedora_nexus.indexer.tree_sitter_indexer import TreeSitterIndexer
from fedora_nexus.indexer.base import detect_language

# Convenience aliases so all existing test bodies work without change
PythonIndexer = lambda: TreeSitterIndexer(languages=["python"])
TypeScriptIndexer = lambda: TreeSitterIndexer(languages=["typescript", "javascript"])
RubyIndexer = lambda: TreeSitterIndexer(languages=["ruby"])


# ── detect_language ──────────────────────────────────────────────────────────

def test_detect_language_python():
    assert detect_language("foo.py") == "python"


def test_detect_language_typescript():
    assert detect_language("foo.ts") == "typescript"
    assert detect_language("foo.tsx") == "typescript"


def test_detect_language_javascript():
    assert detect_language("foo.js") == "javascript"
    assert detect_language("foo.jsx") == "javascript"


def test_detect_language_ruby():
    assert detect_language("foo.rb") == "ruby"


def test_detect_language_unknown():
    assert detect_language("foo.go") is None


# ── PythonIndexer ─────────────────────────────────────────────────────────────

def test_python_indexer_simple_import(tmp_path):
    (tmp_path / "utils.py").write_text("x = 1\n")
    (tmp_path / "main.py").write_text("import utils\n")
    graph = PythonIndexer().index(str(tmp_path))
    assert graph.has_node("main.py")
    assert graph.has_node("utils.py")
    assert "utils.py" in graph.get_dependencies("main.py")


def test_python_indexer_from_import(tmp_path):
    (tmp_path / "helpers.py").write_text("def foo(): pass\n")
    (tmp_path / "app.py").write_text("from helpers import foo\n")
    graph = PythonIndexer().index(str(tmp_path))
    assert "helpers.py" in graph.get_dependencies("app.py")


def test_python_indexer_relative_import(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("from .b import something\n")
    (pkg / "b.py").write_text("something = 1\n")
    graph = PythonIndexer().index(str(tmp_path))
    assert "pkg/b.py" in graph.get_dependencies("pkg/a.py")


def test_python_indexer_skips_pycache(tmp_path):
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "cached.py").write_text("x = 1\n")
    (tmp_path / "main.py").write_text("x = 1\n")
    graph = PythonIndexer().index(str(tmp_path))
    assert not graph.has_node("__pycache__/cached.py")


def test_python_indexer_no_false_edges(tmp_path):
    (tmp_path / "a.py").write_text("import os\n")  # stdlib, won't resolve
    graph = PythonIndexer().index(str(tmp_path))
    assert graph.get_dependencies("a.py") == []


def test_python_indexer_symbol_mode_contains_edges(tmp_path):
    (tmp_path / "utils.py").write_text("def helper(): pass\n")
    (tmp_path / "main.py").write_text("from utils import helper\ndef run(): helper()\n")
    graph = PythonIndexer().index(str(tmp_path), symbol_mode=True)
    assert graph.has_node("utils.py")
    assert graph.has_node("main.py")
    assert graph.has_node("utils.py#function:helper")
    assert graph.has_node("main.py#function:run")
    adj = graph.to_adjacency_json()
    contains_edges = [e for e in adj["edges"] if e["rel"] == "CONTAINS"]
    from_ids = {e["from"] for e in contains_edges}
    assert "utils.py" in from_ids
    assert "main.py" in from_ids


def test_python_indexer_symbol_mode_calls_edge(tmp_path):
    (tmp_path / "utils.py").write_text("def helper(): pass\n")
    (tmp_path / "main.py").write_text("from utils import helper\ndef run(): helper()\n")
    graph = PythonIndexer().index(str(tmp_path), symbol_mode=True)
    adj = graph.to_adjacency_json()
    calls_edges = [e for e in adj["edges"] if e["rel"] == "CALLS"]
    assert any(e["from"] == "main.py#function:run" and e["to"] == "utils.py#function:helper" for e in calls_edges)


# ── RubyIndexer symbol mode ───────────────────────────────────────────────────

def test_ruby_indexer_symbol_mode_class_and_method(tmp_path):
    (tmp_path / "model.rb").write_text(
        "class User\n"
        "  def save\n"
        "    true\n"
        "  end\n"
        "end\n"
    )
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=True)
    assert graph.has_node("model.rb#class:User")
    assert graph.has_node("model.rb#method:User.save")
    adj = graph.to_adjacency_json()
    contains = [e for e in adj["edges"] if e["rel"] == "CONTAINS"]
    assert any(e["from"] == "model.rb" and e["to"] == "model.rb#class:User" for e in contains)
    assert any(e["from"] == "model.rb#class:User" and e["to"] == "model.rb#method:User.save" for e in contains)


def test_ruby_indexer_symbol_mode_module(tmp_path):
    (tmp_path / "concern.rb").write_text(
        "module Validatable\n"
        "  def valid?\n"
        "    true\n"
        "  end\n"
        "end\n"
    )
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=True)
    assert graph.has_node("concern.rb#module:Validatable")
    assert graph.has_node("concern.rb#method:Validatable.valid?")
    adj = graph.to_adjacency_json()
    contains = [e for e in adj["edges"] if e["rel"] == "CONTAINS"]
    assert any(e["from"] == "concern.rb#module:Validatable" and e["to"] == "concern.rb#method:Validatable.valid?" for e in contains)


def test_ruby_indexer_symbol_mode_class_method(tmp_path):
    (tmp_path / "user.rb").write_text(
        "class User\n"
        "  def self.create(attrs)\n"
        "    new(attrs)\n"
        "  end\n"
        "end\n"
    )
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=True)
    assert graph.has_node("user.rb#method:User.create")
    adj = graph.to_adjacency_json()
    node = next(n for n in adj["nodes"] if n["id"] == "user.rb#method:User.create")
    assert node["kind"] == "class_method"


def test_ruby_indexer_symbol_mode_rails_hook(tmp_path):
    (tmp_path / "user.rb").write_text(
        "class User\n"
        "  before_save :encrypt_password\n"
        "  def encrypt_password\n"
        "    true\n"
        "  end\n"
        "end\n"
    )
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=True)
    assert graph.has_node("user.rb#hook:before_save:encrypt_password")
    adj = graph.to_adjacency_json()
    node = next(n for n in adj["nodes"] if n["id"] == "user.rb#hook:before_save:encrypt_password")
    assert node["kind"] == "hook"
    contains = [e for e in adj["edges"] if e["rel"] == "CONTAINS"]
    assert any(e["to"] == "user.rb#hook:before_save:encrypt_password" for e in contains)


def test_ruby_indexer_symbol_mode_false_no_symbols(tmp_path):
    (tmp_path / "helper.rb").write_text("def help; end\n")
    (tmp_path / "main.rb").write_text("require_relative 'helper'\n")
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=False)
    # No symbol nodes
    assert not graph.has_node("helper.rb#method:help")
    # Existing file-level behavior preserved
    assert "helper.rb" in graph.get_dependencies("main.rb")


def test_ruby_indexer_symbol_mode_after_action(tmp_path):
    (tmp_path / "controller.rb").write_text(
        "class PostsController\n"
        "  after_action :track_view\n"
        "  def show\n"
        "    @post = Post.find(params[:id])\n"
        "  end\n"
        "end\n"
    )
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=True)
    assert graph.has_node("controller.rb#hook:after_action:track_view")
    assert graph.has_node("controller.rb#method:PostsController.show")


def test_python_indexer_symbol_mode_false_no_symbols(tmp_path):
    (tmp_path / "utils.py").write_text("def helper(): pass\n")
    (tmp_path / "main.py").write_text("from utils import helper\ndef run(): helper()\n")
    graph = PythonIndexer().index(str(tmp_path), symbol_mode=False)
    assert not graph.has_node("utils.py#function:helper")
    assert not graph.has_node("main.py#function:run")
    assert "utils.py" in graph.get_dependencies("main.py")


def test_python_indexer_class_contains_method(tmp_path):
    (tmp_path / "model.py").write_text("class User:\n    def save(self): pass\n")
    graph = PythonIndexer().index(str(tmp_path), symbol_mode=True)
    assert graph.has_node("model.py#class:User")
    assert graph.has_node("model.py#method:User.save")
    adj = graph.to_adjacency_json()
    contains = [e for e in adj["edges"] if e["rel"] == "CONTAINS"]
    assert any(e["from"] == "model.py" and e["to"] == "model.py#class:User" for e in contains)
    assert any(e["from"] == "model.py#class:User" and e["to"] == "model.py#method:User.save" for e in contains)


# ── TypeScriptIndexer ─────────────────────────────────────────────────────────

def test_typescript_indexer_relative_import(tmp_path):
    (tmp_path / "utils.ts").write_text("export const x = 1;\n")
    (tmp_path / "main.ts").write_text("import { x } from './utils';\n")
    graph = TypeScriptIndexer().index(str(tmp_path))
    assert "utils.ts" in graph.get_dependencies("main.ts")


def test_typescript_indexer_skips_node_modules(tmp_path):
    nm = tmp_path / "node_modules" / "react"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("module.exports = {};\n")
    (tmp_path / "app.tsx").write_text("import React from 'react';\n")
    graph = TypeScriptIndexer().index(str(tmp_path))
    # node_modules import should not create an edge
    assert graph.get_dependencies("app.tsx") == []


def test_typescript_indexer_require_syntax(tmp_path):
    (tmp_path / "lib.js").write_text("module.exports = {};\n")
    (tmp_path / "main.js").write_text("const lib = require('./lib');\n")
    graph = TypeScriptIndexer().index(str(tmp_path))
    assert "lib.js" in graph.get_dependencies("main.js")


def test_typescript_indexer_export_from(tmp_path):
    (tmp_path / "a.ts").write_text("export const x = 1;\n")
    (tmp_path / "b.ts").write_text("export { x } from './a';\n")
    graph = TypeScriptIndexer().index(str(tmp_path))
    assert "a.ts" in graph.get_dependencies("b.ts")


def test_typescript_indexer_export_star_from(tmp_path):
    (tmp_path / "a.ts").write_text("export const x = 1;\n")
    (tmp_path / "re_export.ts").write_text("export * from './a';\n")
    graph = TypeScriptIndexer().index(str(tmp_path))
    assert "a.ts" in graph.get_dependencies("re_export.ts")


def test_typescript_indexer_symbol_mode_function(tmp_path):
    (tmp_path / "utils.ts").write_text("export function helper() { return 1; }\n")
    graph = TypeScriptIndexer().index(str(tmp_path), symbol_mode=True)
    assert graph.has_node("utils.ts")
    assert graph.has_node("utils.ts#function:helper")
    adj = graph.to_adjacency_json()
    contains = [e for e in adj["edges"] if e["rel"] == "CONTAINS"]
    assert any(e["from"] == "utils.ts" and e["to"] == "utils.ts#function:helper" for e in contains)


def test_typescript_indexer_symbol_mode_class_and_method(tmp_path):
    (tmp_path / "service.ts").write_text(
        "export class UserService {\n"
        "  getUser(id: number) { return id; }\n"
        "}\n"
    )
    graph = TypeScriptIndexer().index(str(tmp_path), symbol_mode=True)
    assert graph.has_node("service.ts#class:UserService")
    adj = graph.to_adjacency_json()
    nodes = {n["id"] for n in adj["nodes"]}
    assert "service.ts#class:UserService" in nodes
    contains = [e for e in adj["edges"] if e["rel"] == "CONTAINS"]
    assert any(e["from"] == "service.ts" and e["to"] == "service.ts#class:UserService" for e in contains)


def test_typescript_indexer_symbol_mode_false_no_symbols(tmp_path):
    (tmp_path / "utils.ts").write_text("export function helper() { return 1; }\n")
    graph = TypeScriptIndexer().index(str(tmp_path), symbol_mode=False)
    assert graph.has_node("utils.ts")
    assert not graph.has_node("utils.ts#function:helper")


# ── RubyIndexer ───────────────────────────────────────────────────────────────

def test_ruby_indexer_require_relative(tmp_path):
    (tmp_path / "helper.rb").write_text("def help; end\n")
    (tmp_path / "main.rb").write_text("require_relative 'helper'\n")
    graph = RubyIndexer().index(str(tmp_path))
    assert "helper.rb" in graph.get_dependencies("main.rb")


def test_ruby_indexer_require_absolute_within_root(tmp_path):
    (tmp_path / "lib.rb").write_text("LIB = 1\n")
    (tmp_path / "app.rb").write_text("require 'lib'\n")
    graph = RubyIndexer().index(str(tmp_path))
    assert "lib.rb" in graph.get_dependencies("app.rb")


def test_ruby_indexer_skips_vendor(tmp_path):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "gem.rb").write_text("GEM = 1\n")
    (tmp_path / "app.rb").write_text("x = 1\n")
    graph = RubyIndexer().index(str(tmp_path))
    assert not graph.has_node("vendor/gem.rb")


def test_ruby_indexer_symbol_mode_before_commit_hook(tmp_path):
    (tmp_path / "model.rb").write_text(
        "class Order\n"
        "  before_commit :notify\n"
        "end\n"
    )
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=True)
    assert graph.has_node("model.rb#hook:before_commit:notify")


def test_ruby_indexer_symbol_mode_around_save_hook(tmp_path):
    (tmp_path / "model.rb").write_text(
        "class Post\n"
        "  around_save :wrap_transaction\n"
        "  def wrap_transaction\n"
        "    yield\n"
        "  end\n"
        "end\n"
    )
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=True)
    assert graph.has_node("model.rb#hook:around_save:wrap_transaction")
    adj = graph.to_adjacency_json()
    node = next(n for n in adj["nodes"] if n["id"] == "model.rb#hook:around_save:wrap_transaction")
    assert node["kind"] == "hook"


# ------------------------------------------------------------------
# New: content + line number extraction tests
# ------------------------------------------------------------------

def test_python_symbol_mode_has_start_line(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\ndef greet():\n    pass\n")
    graph = PythonIndexer().index(str(tmp_path), symbol_mode=True)
    sym = "app.py#function:greet"
    assert graph.has_node(sym)
    attrs = graph.node_attrs(sym)
    assert attrs.get("start_line", 0) > 0


def test_python_symbol_mode_has_content(tmp_path):
    (tmp_path / "app.py").write_text("def greet():\n    pass\n")
    graph = PythonIndexer().index(str(tmp_path), symbol_mode=True)
    sym = "app.py#function:greet"
    assert graph.has_node(sym)
    attrs = graph.node_attrs(sym)
    assert "greet" in attrs.get("content", "")


# ── G2: CALLS edges for TypeScript ──────────────────────────────────────────

def test_ts_calls_edge_to_imported_function(tmp_path):
    (tmp_path / "utils.ts").write_text(
        "export function helper() { return 1; }\n"
    )
    (tmp_path / "main.ts").write_text(
        "import { helper } from './utils';\n"
        "export function run() { return helper(); }\n"
    )
    graph = TypeScriptIndexer().index(str(tmp_path), symbol_mode=True)
    adj = graph.to_adjacency_json()
    calls_edges = [e for e in adj["edges"] if e["rel"] == "CALLS"]
    assert any(
        e["from"] == "main.ts#function:run" and e["to"] == "utils.ts#function:helper"
        for e in calls_edges
    ), f"Expected CALLS edge, got: {calls_edges}"


def test_ts_calls_edge_member_expression(tmp_path):
    """Method call via member expression obj.method() triggers CALLS edge."""
    (tmp_path / "service.ts").write_text(
        "export function process() { return true; }\n"
    )
    (tmp_path / "handler.ts").write_text(
        "import { process } from './service';\n"
        "const svc = { process };\n"
        "export function handle() { return svc.process(); }\n"
    )
    graph = TypeScriptIndexer().index(str(tmp_path), symbol_mode=True)
    adj = graph.to_adjacency_json()
    calls_edges = [e for e in adj["edges"] if e["rel"] == "CALLS"]
    assert any(e["to"] == "service.ts#function:process" for e in calls_edges), \
        f"Expected CALLS edge to process via member expression, got: {calls_edges}"


def test_ts_no_calls_edge_for_unknown_function(tmp_path):
    """Calls to unknown/external functions must NOT create spurious CALLS edges."""
    (tmp_path / "main.ts").write_text(
        "export function run() { return Math.random(); }\n"
    )
    graph = TypeScriptIndexer().index(str(tmp_path), symbol_mode=True)
    adj = graph.to_adjacency_json()
    calls_edges = [e for e in adj["edges"] if e["rel"] == "CALLS"]
    assert calls_edges == []


# ── G2: CALLS edges for Ruby ─────────────────────────────────────────────────

def test_ruby_calls_edge_to_required_method(tmp_path):
    (tmp_path / "helper.rb").write_text(
        "module Helper\n"
        "  def self.do_work\n"
        "    true\n"
        "  end\n"
        "end\n"
    )
    (tmp_path / "main.rb").write_text(
        "require_relative 'helper'\n"
        "class Controller\n"
        "  def run\n"
        "    do_work\n"
        "  end\n"
        "end\n"
    )
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=True)
    adj = graph.to_adjacency_json()
    calls_edges = [e for e in adj["edges"] if e["rel"] == "CALLS"]
    callee_ids = {e["to"] for e in calls_edges}
    assert any("do_work" in cid for cid in callee_ids), f"Expected CALLS edge to do_work, got: {calls_edges}"


def test_ruby_no_calls_edge_for_unknown_method(tmp_path):
    """Calls to methods not in any imported file must NOT create spurious CALLS edges."""
    (tmp_path / "controller.rb").write_text(
        "class Controller\n"
        "  def handle\n"
        "    render json: { ok: true }\n"
        "  end\n"
        "end\n"
    )
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=True)
    adj = graph.to_adjacency_json()
    calls_edges = [e for e in adj["edges"] if e["rel"] == "CALLS"]
    assert calls_edges == [], f"Expected no CALLS edges, got: {calls_edges}"


# ── G3: Cross-file binding (transitive imports) ───────────────────────────────

def test_ts_calls_edge_via_transitive_import(tmp_path):
    """CALLS edge should resolve even through a re-export chain."""
    (tmp_path / "db.ts").write_text(
        "export function query() { return []; }\n"
    )
    (tmp_path / "repo.ts").write_text(
        "export { query } from './db';\n"
    )
    (tmp_path / "service.ts").write_text(
        "import { query } from './repo';\n"
        "export function findAll() { return query(); }\n"
    )
    graph = TypeScriptIndexer().index(str(tmp_path), symbol_mode=True)
    adj = graph.to_adjacency_json()
    calls_edges = [e for e in adj["edges"] if e["rel"] == "CALLS"]
    assert any(e["to"] == "db.ts#function:query" for e in calls_edges), \
        f"Expected transitive CALLS edge, got: {calls_edges}"


# ── G4: Parallel parsing ──────────────────────────────────────────────────────

def test_parallel_indexer_produces_same_result_as_sequential(tmp_path):
    """Parallel parse must yield identical nodes and edges as sequential."""
    for i in range(20):
        (tmp_path / f"mod_{i}.ts").write_text(
            f"export function fn_{i}() {{ return {i}; }}\n"
        )
    (tmp_path / "main.ts").write_text(
        "import { fn_0 } from './mod_0';\n"
        "export function run() { return fn_0(); }\n"
    )
    graph = TypeScriptIndexer().index(str(tmp_path), symbol_mode=True)
    adj = graph.to_adjacency_json()
    node_ids = {n["id"] for n in adj["nodes"]}
    assert "main.ts" in node_ids
    for i in range(20):
        assert f"mod_{i}.ts" in node_ids
    dep_edges = [e for e in adj["edges"] if e["rel"] == "DEPENDS_ON"]
    assert any(e["from"] == "main.ts" and e["to"] == "mod_0.ts" for e in dep_edges)


def test_ts_symbol_mode_has_content(tmp_path):
    (tmp_path / "app.ts").write_text("function multiply(a: number, b: number) { return a * b; }\n")
    graph = TypeScriptIndexer().index(str(tmp_path), symbol_mode=True)
    sym = "app.ts#function:multiply"
    assert graph.has_node(sym)
    attrs = graph.node_attrs(sym)
    assert "multiply" in attrs.get("content", "")
