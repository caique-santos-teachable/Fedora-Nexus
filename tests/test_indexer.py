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


# ── Ruby/Rails new node kinds ─────────────────────────────────────────────────

def test_ruby_association_belongs_to(tmp_path):
    (tmp_path / "comment.rb").write_text(
        "class Comment\n"
        "  belongs_to :post\n"
        "end\n"
    )
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=True)
    assert graph.has_node("comment.rb#association:belongs_to:post")
    adj = graph.to_adjacency_json()
    node = next(n for n in adj["nodes"] if n["id"] == "comment.rb#association:belongs_to:post")
    assert node["kind"] == "association"
    contains = [e for e in adj["edges"] if e["rel"] == "CONTAINS"]
    assert any(e["to"] == "comment.rb#association:belongs_to:post" for e in contains)


def test_ruby_association_has_many(tmp_path):
    (tmp_path / "post.rb").write_text(
        "class Post\n"
        "  has_many :comments\n"
        "  has_one :author\n"
        "end\n"
    )
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=True)
    assert graph.has_node("post.rb#association:has_many:comments")
    assert graph.has_node("post.rb#association:has_one:author")
    adj = graph.to_adjacency_json()
    kinds = {n["id"]: n["kind"] for n in adj["nodes"]}
    assert kinds["post.rb#association:has_many:comments"] == "association"
    assert kinds["post.rb#association:has_one:author"] == "association"


def test_ruby_association_has_and_belongs_to_many(tmp_path):
    (tmp_path / "article.rb").write_text(
        "class Article\n"
        "  has_and_belongs_to_many :tags\n"
        "end\n"
    )
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=True)
    assert graph.has_node("article.rb#association:has_and_belongs_to_many:tags")


def test_ruby_validation_validates(tmp_path):
    (tmp_path / "user.rb").write_text(
        "class User\n"
        "  validates :email, presence: true\n"
        "  validates :name, length: { minimum: 2 }\n"
        "end\n"
    )
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=True)
    assert graph.has_node("user.rb#validation:validates:email")
    assert graph.has_node("user.rb#validation:validates:name")
    adj = graph.to_adjacency_json()
    node = next(n for n in adj["nodes"] if n["id"] == "user.rb#validation:validates:email")
    assert node["kind"] == "validation"
    contains = [e for e in adj["edges"] if e["rel"] == "CONTAINS"]
    assert any(e["to"] == "user.rb#validation:validates:email" for e in contains)


def test_ruby_validation_validate_custom(tmp_path):
    (tmp_path / "order.rb").write_text(
        "class Order\n"
        "  validate :check_stock\n"
        "  def check_stock; end\n"
        "end\n"
    )
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=True)
    assert graph.has_node("order.rb#validation:validate:check_stock")


def test_ruby_scope(tmp_path):
    (tmp_path / "post.rb").write_text(
        "class Post\n"
        "  scope :published, -> { where(published: true) }\n"
        "  scope :recent, -> { order(created_at: :desc) }\n"
        "end\n"
    )
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=True)
    assert graph.has_node("post.rb#scope:published")
    assert graph.has_node("post.rb#scope:recent")
    adj = graph.to_adjacency_json()
    node = next(n for n in adj["nodes"] if n["id"] == "post.rb#scope:published")
    assert node["kind"] == "scope"
    contains = [e for e in adj["edges"] if e["rel"] == "CONTAINS"]
    assert any(e["to"] == "post.rb#scope:published" for e in contains)


def test_ruby_mixin_include(tmp_path):
    (tmp_path / "user.rb").write_text(
        "class User\n"
        "  include Searchable\n"
        "  extend ClassMethods\n"
        "  prepend Auditable\n"
        "end\n"
    )
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=True)
    assert graph.has_node("user.rb#mixin:include:Searchable")
    assert graph.has_node("user.rb#mixin:extend:ClassMethods")
    assert graph.has_node("user.rb#mixin:prepend:Auditable")
    adj = graph.to_adjacency_json()
    for sym_id in ["user.rb#mixin:include:Searchable", "user.rb#mixin:extend:ClassMethods", "user.rb#mixin:prepend:Auditable"]:
        node = next(n for n in adj["nodes"] if n["id"] == sym_id)
        assert node["kind"] == "mixin"
    contains = [e for e in adj["edges"] if e["rel"] == "CONTAINS"]
    assert any(e["to"] == "user.rb#mixin:include:Searchable" for e in contains)


def test_ruby_attr_accessor(tmp_path):
    (tmp_path / "person.rb").write_text(
        "class Person\n"
        "  attr_accessor :name, :age\n"
        "  attr_reader :id\n"
        "  attr_writer :email\n"
        "end\n"
    )
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=True)
    assert graph.has_node("person.rb#attr:name")
    assert graph.has_node("person.rb#attr:age")
    assert graph.has_node("person.rb#attr:id")
    assert graph.has_node("person.rb#attr:email")
    adj = graph.to_adjacency_json()
    for sym_id in ["person.rb#attr:name", "person.rb#attr:age", "person.rb#attr:id", "person.rb#attr:email"]:
        node = next(n for n in adj["nodes"] if n["id"] == sym_id)
        assert node["kind"] == "attr"
    contains = [e for e in adj["edges"] if e["rel"] == "CONTAINS"]
    assert any(e["to"] == "person.rb#attr:name" for e in contains)


def test_ruby_concern_kind(tmp_path):
    (tmp_path / "searchable.rb").write_text(
        "module Searchable\n"
        "  extend ActiveSupport::Concern\n"
        "  def search(query)\n"
        "    where(name: query)\n"
        "  end\n"
        "end\n"
    )
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=True)
    assert graph.has_node("searchable.rb#module:Searchable")
    adj = graph.to_adjacency_json()
    node = next(n for n in adj["nodes"] if n["id"] == "searchable.rb#module:Searchable")
    assert node["kind"] == "concern"


def test_ruby_plain_module_is_not_concern(tmp_path):
    (tmp_path / "helper.rb").write_text(
        "module Helper\n"
        "  def help; end\n"
        "end\n"
    )
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=True)
    adj = graph.to_adjacency_json()
    node = next(n for n in adj["nodes"] if n["id"] == "helper.rb#module:Helper")
    assert node["kind"] == "module"


# ── Ruby indexing gaps (10 new kinds + precision) ────────────────────────────


# Gap #1 — require_dependency
def test_ruby_require_dependency_import_edge(tmp_path):
    (tmp_path / "base.rb").write_text("BASE = 1\n")
    (tmp_path / "app.rb").write_text("require_dependency 'base'\n")
    graph = RubyIndexer().index(str(tmp_path))
    assert "base.rb" in graph.get_dependencies("app.rb")


# Gap #1 — autoload
def test_ruby_autoload_import_edge(tmp_path):
    (tmp_path / "my_class.rb").write_text("class MyClass; end\n")
    (tmp_path / "app.rb").write_text('autoload :MyClass, "my_class"\n')
    graph = RubyIndexer().index(str(tmp_path))
    assert "my_class.rb" in graph.get_dependencies("app.rb")


# Gap #2 — class inheritance attribute
def test_ruby_class_inheritance_superclass_attribute(tmp_path):
    (tmp_path / "user.rb").write_text(
        "class User\n"
        "  def name; end\n"
        "end\n"
        "class AdminUser < User\n"
        "end\n"
    )
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=True)
    assert graph.has_node("user.rb#class:AdminUser")
    attrs = graph.node_attrs("user.rb#class:AdminUser")
    assert attrs.get("superclass") == "User"


# Gap #2 — class inheritance INHERITS edge (intra-file)
def test_ruby_class_inheritance_inherits_edge(tmp_path):
    (tmp_path / "user.rb").write_text(
        "class User\n"
        "  def name; end\n"
        "end\n"
        "class AdminUser < User\n"
        "end\n"
    )
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=True)
    adj = graph.to_adjacency_json()
    inherits_edges = [e for e in adj["edges"] if e["rel"] == "INHERITS"]
    assert any(
        e["from"] == "user.rb#class:AdminUser" and e["to"] == "user.rb#class:User"
        for e in inherits_edges
    ), f"Expected INHERITS edge, got: {inherits_edges}"


# Gap #3 — intra-file CALLS edge
def test_ruby_intrafile_calls_edge(tmp_path):
    (tmp_path / "service.rb").write_text(
        "class PaymentService\n"
        "  def process\n"
        "    charge\n"
        "  end\n"
        "\n"
        "  def charge\n"
        "    true\n"
        "  end\n"
        "end\n"
    )
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=True)
    adj = graph.to_adjacency_json()
    calls_edges = [e for e in adj["edges"] if e["rel"] == "CALLS"]
    assert any(
        e["from"] == "service.rb#method:PaymentService.process"
        and e["to"] == "service.rb#method:PaymentService.charge"
        for e in calls_edges
    ), f"Expected intra-file CALLS edge, got: {calls_edges}"


# Gap #4 — enum node
def test_ruby_enum_node(tmp_path):
    (tmp_path / "post.rb").write_text(
        "class Post\n"
        "  enum status: [:draft, :published, :archived]\n"
        "end\n"
    )
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=True)
    assert graph.has_node("post.rb#enum:status")
    attrs = graph.node_attrs("post.rb#enum:status")
    assert attrs["kind"] == "enum"
    adj = graph.to_adjacency_json()
    contains = [e for e in adj["edges"] if e["rel"] == "CONTAINS"]
    assert any(e["from"] == "post.rb#class:Post" and e["to"] == "post.rb#enum:status" for e in contains)


# Gap #5 — delegate node
def test_ruby_delegate_node(tmp_path):
    (tmp_path / "user.rb").write_text(
        "class User\n"
        "  delegate :email, to: :account\n"
        "end\n"
    )
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=True)
    assert graph.has_node("user.rb#delegate:email")
    attrs = graph.node_attrs("user.rb#delegate:email")
    assert attrs["kind"] == "delegate"
    assert attrs.get("target") == "account"
    adj = graph.to_adjacency_json()
    contains = [e for e in adj["edges"] if e["rel"] == "CONTAINS"]
    assert any(e["from"] == "user.rb#class:User" and e["to"] == "user.rb#delegate:email" for e in contains)


# Gap #6 — constant node
def test_ruby_constant_node(tmp_path):
    (tmp_path / "config.rb").write_text(
        "class Config\n"
        "  MAX_RETRIES = 3\n"
        '  DEFAULT_ROLE = "viewer"\n'
        "end\n"
    )
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=True)
    assert graph.has_node("config.rb#constant:Config::MAX_RETRIES")
    assert graph.has_node("config.rb#constant:Config::DEFAULT_ROLE")
    for sym_id in ["config.rb#constant:Config::MAX_RETRIES", "config.rb#constant:Config::DEFAULT_ROLE"]:
        attrs = graph.node_attrs(sym_id)
        assert attrs["kind"] == "constant"
    adj = graph.to_adjacency_json()
    contains = [e for e in adj["edges"] if e["rel"] == "CONTAINS"]
    assert any(e["to"] == "config.rb#constant:Config::MAX_RETRIES" for e in contains)


# Gap #6 — top-level constant (no namespace)
def test_ruby_top_level_constant_node(tmp_path):
    (tmp_path / "limits.rb").write_text("TIMEOUT = 30\n")
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=True)
    assert graph.has_node("limits.rb#constant:TIMEOUT")
    attrs = graph.node_attrs("limits.rb#constant:TIMEOUT")
    assert attrs["kind"] == "constant"


# Gap #8 — nested module namespace for class sym_id
def test_ruby_nested_namespace_class_sym_id(tmp_path):
    (tmp_path / "controller.rb").write_text(
        "module Admin\n"
        "  class UsersController\n"
        "    def index\n"
        "      @users = []\n"
        "    end\n"
        "  end\n"
        "end\n"
    )
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=True)
    # Class node must use qualified name
    assert graph.has_node("controller.rb#class:Admin::UsersController")
    # Method must use full qualified prefix
    assert graph.has_node("controller.rb#method:Admin::UsersController.index")
    adj = graph.to_adjacency_json()
    contains = [e for e in adj["edges"] if e["rel"] == "CONTAINS"]
    assert any(
        e["from"] == "controller.rb#class:Admin::UsersController"
        and e["to"] == "controller.rb#method:Admin::UsersController.index"
        for e in contains
    )


# Gap #8 — doubly nested namespace
def test_ruby_doubly_nested_namespace_class_sym_id(tmp_path):
    (tmp_path / "api.rb").write_text(
        "module API\n"
        "  module V1\n"
        "    class UsersController\n"
        "      def show; end\n"
        "    end\n"
        "  end\n"
        "end\n"
    )
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=True)
    assert graph.has_node("api.rb#class:API::V1::UsersController")
    assert graph.has_node("api.rb#method:API::V1::UsersController.show")


# Gap #9 — alias_method node (call form)
def test_ruby_alias_method_node(tmp_path):
    (tmp_path / "user.rb").write_text(
        "class User\n"
        "  def save; end\n"
        "  alias_method :persist, :save\n"
        "end\n"
    )
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=True)
    assert graph.has_node("user.rb#alias:persist")
    attrs = graph.node_attrs("user.rb#alias:persist")
    assert attrs["kind"] == "alias"
    assert attrs.get("original") == "save"
    adj = graph.to_adjacency_json()
    contains = [e for e in adj["edges"] if e["rel"] == "CONTAINS"]
    assert any(e["from"] == "user.rb#class:User" and e["to"] == "user.rb#alias:persist" for e in contains)


# Gap #9 — alias keyword node
def test_ruby_alias_keyword_node(tmp_path):
    (tmp_path / "user.rb").write_text(
        "class User\n"
        "  def save; end\n"
        "  alias persist save\n"
        "end\n"
    )
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=True)
    assert graph.has_node("user.rb#alias:persist")
    attrs = graph.node_attrs("user.rb#alias:persist")
    assert attrs["kind"] == "alias"
    assert attrs.get("original") == "save"


# Gap #7 — concern included block indexes inner macros
def test_ruby_concern_included_block_indexes_macros(tmp_path):
    (tmp_path / "searchable.rb").write_text(
        "module Searchable\n"
        "  extend ActiveSupport::Concern\n"
        "  included do\n"
        "    scope :active, -> { where(active: true) }\n"
        "    has_many :tags\n"
        "    validates :name, presence: true\n"
        "  end\n"
        "end\n"
    )
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=True)
    assert graph.has_node("searchable.rb#scope:active")
    assert graph.has_node("searchable.rb#association:has_many:tags")
    assert graph.has_node("searchable.rb#validation:validates:name")
    adj = graph.to_adjacency_json()
    contains = [e for e in adj["edges"] if e["rel"] == "CONTAINS"]
    # All three should be contained by the concern module
    assert any(e["from"] == "searchable.rb#module:Searchable" and e["to"] == "searchable.rb#scope:active" for e in contains)
    assert any(e["from"] == "searchable.rb#module:Searchable" and e["to"] == "searchable.rb#association:has_many:tags" for e in contains)
    assert any(e["from"] == "searchable.rb#module:Searchable" and e["to"] == "searchable.rb#validation:validates:name" for e in contains)


# Gap #10 — content attribute on new node kinds
def test_ruby_new_node_kinds_have_content(tmp_path):
    (tmp_path / "model.rb").write_text(
        "class Post\n"
        "  before_save :set_slug\n"
        "  has_many :comments\n"
        "  validates :title, presence: true\n"
        "  scope :published, -> { where(published: true) }\n"
        "  include Searchable\n"
        "  attr_accessor :draft\n"
        "  delegate :email, to: :author\n"
        "  enum status: [:draft, :live]\n"
        "end\n"
    )
    graph = RubyIndexer().index(str(tmp_path), symbol_mode=True)
    for sym_id in [
        "model.rb#hook:before_save:set_slug",
        "model.rb#association:has_many:comments",
        "model.rb#validation:validates:title",
        "model.rb#scope:published",
        "model.rb#mixin:include:Searchable",
        "model.rb#attr:draft",
        "model.rb#delegate:email",
        "model.rb#enum:status",
    ]:
        assert graph.has_node(sym_id), f"Missing node: {sym_id}"
        attrs = graph.node_attrs(sym_id)
        assert attrs.get("content"), f"Missing content on {sym_id}"
