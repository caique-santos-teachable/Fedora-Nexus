"""Kuzu-backed graph store (embedded, no external service required)."""

from __future__ import annotations

import csv
import logging
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import kuzu

from fedora_nexus.graph.engine import DependencyGraph
from fedora_nexus.store import embedding_store as _emb

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = "/data/fedora-nexus.db"  # used inside Docker; overridden by FEDORA_NEXUS_DB_PATH

def _default_db_path() -> str:
    """Return the platform-appropriate default database path.

    Priority:
    1. FEDORA_NEXUS_DB_PATH env var (explicit override, used by Docker/CI)
    2. ~/.local/share/fedora-nexus/fedora-nexus.db (local CLI default)

    The /data/fedora-nexus.db constant is the default for Docker mounts.
    """
    from_env = os.environ.get("FEDORA_NEXUS_DB_PATH", "")
    if from_env:
        return from_env
    local_dir = Path.home() / ".local" / "share" / "fedora-nexus"
    local_dir.mkdir(parents=True, exist_ok=True)
    return str(local_dir / "fedora-nexus.db")

_KIND_TO_TABLE = {
    "file": "File",
    "function": "Function",
    "class": "Class",
    "module": "Class",    # Ruby modules stored alongside classes in the Class table
    "concern": "Class",  # Rails concerns (module + ActiveSupport::Concern) same as module
    "db_table": "Class", # SQL DDL tables (from structure.sql / schema files)
    "method": "Method",
    "class_method": "Method",  # Ruby class methods stored in Method table
    # Rails DSL macros — stored in Method table with their kind preserved
    "association": "Method",   # has_many, belongs_to, has_one, etc.
    "hook": "Method",          # before_action, after_commit, around_save, etc.
    "scope": "Method",         # scope :name, -> { ... }
    "validation": "Method",    # validates :field, presence: true
    "mixin": "Method",         # include Mod, extend Mod, prepend Mod
    "attr": "Method",          # attr_accessor, attr_reader, attr_writer
    "enum": "Method",          # enum status: [:draft, :published]
    "delegate": "Method",      # delegate :foo, to: :target
    "alias": "Method",         # alias_method :new_name, :old_name
    "helper_method": "Method", # helper_method :current_user
    "rescue_from": "Method",   # rescue_from SomeError, with: :handler
}

# Valid (from_table, to_table) pairs for CodeRelation
_VALID_EDGE_PAIRS = {
    ("File", "File"), ("File", "Function"), ("File", "Class"), ("File", "Method"),
    ("Function", "Function"), ("Function", "Method"),
    ("Class", "Method"), ("Class", "Class"),
    ("Method", "Function"), ("Method", "Method"), ("Method", "Class"),
    ("Function", "Class"), ("Class", "Function"),
}


class SchemaVersionError(Exception):
    pass


class KuzuGraphStore:
    """Persist and retrieve DependencyGraph instances in an embedded Kuzu database."""

    _LOCK_CANDIDATES = (".lock", ".db.lock")

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or _default_db_path()
        try:
            self._db = kuzu.Database(self._db_path)
        except RuntimeError as exc:
            if "lock" not in str(exc).lower():
                raise
            removed_any = False
            for candidate in self._LOCK_CANDIDATES:
                lock_path = os.path.join(self._db_path, candidate)
                if os.path.exists(lock_path):
                    os.remove(lock_path)
                    logger.warning("Removed stale lock file: %s", lock_path)
                    removed_any = True
            if not removed_any:
                logger.warning(
                    "Lock error but no stale lock file found — another process may still hold the DB at %s",
                    self._db_path,
                )
            self._db = kuzu.Database(self._db_path)
        self._conn = kuzu.Connection(self._db)
        self._embedding_cache: dict = {}

    def init_schema(self) -> None:
        """Create node/edge tables if they do not exist."""
        # Detect old schema (pre-rich-schema Node table)
        try:
            self._conn.execute("MATCH (n:Node) RETURN count(n) LIMIT 1")
            raise SchemaVersionError(
                "Old schema detected. Delete the DB and restart: rm -rf /data/fedora-nexus.db"
            )
        except SchemaVersionError:
            raise
        except Exception:
            pass  # No Node table — fresh DB, proceed with new schema

        self._conn.execute(
            "CREATE NODE TABLE IF NOT EXISTS Repo("
            "root_path STRING, indexed_at STRING, PRIMARY KEY(root_path))"
        )
        self._conn.execute(
            "CREATE NODE TABLE IF NOT EXISTS File("
            "id STRING, root_path STRING, name STRING, file_path STRING, "
            "language STRING, content STRING, PRIMARY KEY(id))"
        )
        self._conn.execute(
            "CREATE NODE TABLE IF NOT EXISTS Function("
            "id STRING, root_path STRING, name STRING, file_path STRING, "
            "language STRING, start_line INT64, end_line INT64, content STRING, "
            "is_exported BOOLEAN, PRIMARY KEY(id))"
        )
        self._conn.execute(
            "CREATE NODE TABLE IF NOT EXISTS Class("
            "id STRING, root_path STRING, name STRING, file_path STRING, "
            "language STRING, start_line INT64, end_line INT64, content STRING, "
            "is_exported BOOLEAN, kind STRING, PRIMARY KEY(id))"
        )
        # Migration: add kind column to existing Class tables that predate this schema version.
        try:
            self._conn.execute("MATCH (c:Class) RETURN c.kind LIMIT 0")
        except Exception:
            try:
                self._conn.execute("ALTER TABLE Class ADD kind STRING DEFAULT ''")
                logger.info("Migrated Class table: added 'kind' column")
            except Exception as migrate_exc:
                logger.warning(
                    "Could not migrate Class table (kind column): %s. "
                    "Run reset_db to get a clean schema.", migrate_exc
                )
        self._conn.execute(
            "CREATE NODE TABLE IF NOT EXISTS Method("
            "id STRING, root_path STRING, name STRING, file_path STRING, "
            "language STRING, start_line INT64, end_line INT64, content STRING, "
            "is_exported BOOLEAN, owner_name STRING, scope_refs STRING, "
            "kind STRING, dsl_macro STRING, PRIMARY KEY(id))"
        )
        # Migration: add scope_refs column to existing Method tables.
        try:
            self._conn.execute("MATCH (m:Method) RETURN m.scope_refs LIMIT 0")
        except Exception:
            try:
                self._conn.execute("ALTER TABLE Method ADD scope_refs STRING DEFAULT ''")
                logger.info("Migrated Method table: added 'scope_refs' column")
            except Exception as migrate_exc:
                logger.warning(
                    "Could not migrate Method table (scope_refs column): %s. "
                    "Run reset_db to get a clean schema.", migrate_exc
                )
        # Migration: add kind column to distinguish method/class_method/association/hook/etc.
        try:
            self._conn.execute("MATCH (m:Method) RETURN m.kind LIMIT 0")
        except Exception:
            try:
                self._conn.execute("ALTER TABLE Method ADD kind STRING DEFAULT 'method'")
                logger.info("Migrated Method table: added 'kind' column")
            except Exception as migrate_exc:
                logger.warning(
                    "Could not migrate Method table (kind column): %s. "
                    "Run reset_db to get a clean schema.", migrate_exc
                )
        # Migration: add macro column (the DSL macro name, e.g. 'has_many', 'before_action').
        try:
            self._conn.execute("MATCH (m:Method) RETURN m.dsl_macro LIMIT 0")
        except Exception:
            try:
                self._conn.execute("ALTER TABLE Method ADD dsl_macro STRING DEFAULT ''")
                logger.info("Migrated Method table: added 'dsl_macro' column")
            except Exception as migrate_exc:
                logger.warning(
                    "Could not migrate Method table (macro column): %s. "
                    "Run reset_db to get a clean schema.", migrate_exc
                )
        self._conn.execute(
            "CREATE REL TABLE IF NOT EXISTS CodeRelation("
            "FROM File TO File, FROM File TO Function, FROM File TO Class, FROM File TO Method, "
            "FROM Function TO Function, FROM Function TO Method, "
            "FROM Class TO Method, FROM Class TO Class, "
            "FROM Method TO Function, FROM Method TO Method, FROM Method TO Class, "
            "FROM Function TO Class, FROM Class TO Function, "
            "type STRING)"
        )
        logger.info("Kuzu schema ready at %s", self._db_path)

    def save_graph(self, root_path: str, graph: DependencyGraph) -> None:
        """Persist a graph, replacing any previous data for this repo."""
        self._ensure_schema()
        data = graph.to_adjacency_json()
        self._delete_repo_data(root_path)
        indexed_at = datetime.now(tz=timezone.utc).isoformat()
        self._conn.execute(
            "CREATE (:Repo {root_path: $root_path, indexed_at: $indexed_at})",
            {"root_path": root_path, "indexed_at": indexed_at},
        )

        # ── Collect nodes by type ─────────────────────────────────────────────
        node_kinds: dict[str, str] = {}
        files: list[dict] = []
        functions: list[dict] = []
        classes: list[dict] = []
        methods: list[dict] = []
        seen_ids: set[str] = set()

        for n in data["nodes"]:
            path = n["id"]
            kind = n.get("kind", "file")
            node_id = f"{root_path}::{path}"
            language = n.get("language", "")
            node_kinds[n["id"]] = kind

            if node_id in seen_ids:
                logger.warning("Duplicate node ID skipped: %s", node_id)
                continue
            seen_ids.add(node_id)

            if kind == "file":
                files.append({
                    "id": node_id, "root_path": root_path,
                    "name": n.get("name") or Path(path).name,
                    "file_path": path, "language": language,
                    "content": str(n.get("content", "") or "")[:8000],
                })
            elif kind == "function":
                functions.append({
                    "id": node_id, "root_path": root_path,
                    "name": n.get("name", ""), "file_path": n.get("file_path", ""),
                    "language": language,
                    "start_line": int(n.get("start_line", 0)),
                    "end_line": int(n.get("end_line", 0)),
                    "content": str(n.get("content", "") or "")[:8000],
                    "is_exported": "true" if n.get("is_exported") else "false",
                })
            elif kind in ("class", "module", "concern", "db_table"):
                classes.append({
                    "id": node_id, "root_path": root_path,
                    "name": n.get("name", ""), "file_path": n.get("file_path", ""),
                    "language": language,
                    "start_line": int(n.get("start_line", 0)),
                    "end_line": int(n.get("end_line", 0)),
                    "content": str(n.get("content", "") or "")[:8000],
                    "is_exported": "true" if n.get("is_exported") else "false",
                    "kind": kind,
                })
            elif kind in ("method", "class_method"):
                import json as _json
                methods.append({
                    "id": node_id, "root_path": root_path,
                    "name": n.get("name", ""), "file_path": n.get("file_path", ""),
                    "language": language,
                    "start_line": int(n.get("start_line", 0)),
                    "end_line": int(n.get("end_line", 0)),
                    "content": str(n.get("content", "") or "")[:8000],
                    "is_exported": "true" if n.get("is_exported") else "false",
                    "owner_name": n.get("owner_name", ""),
                    "scope_refs": _json.dumps(n.get("scope_refs") or []),
                    "kind": kind,
                    "dsl_macro": "",
                })
            elif kind in (
                "association", "hook", "scope", "validation",
                "mixin", "attr", "enum", "delegate", "alias",
                "helper_method", "rescue_from",
            ):
                # Rails DSL macros — stored in Method table with their kind preserved.
                # These are class-body declarations (not def blocks), so end_line,
                # is_exported, owner_name, and scope_refs use safe defaults.
                methods.append({
                    "id": node_id, "root_path": root_path,
                    "name": n.get("name", ""), "file_path": n.get("file_path", ""),
                    "language": language,
                    "start_line": int(n.get("start_line", 0)),
                    "end_line": int(n.get("start_line", 0)),  # single-line declaration
                    "content": str(n.get("content", "") or "")[:2000],
                    "is_exported": "false",
                    "owner_name": "",
                    "scope_refs": "[]",
                    "kind": kind,
                    "dsl_macro": str(n.get("macro", "") or ""),
                })

        # ── Bulk node inserts via COPY FROM CSV ───────────────────────────────
        # Individual autocommit inserts are ~6ms/row on disk (WAL fsync per tx).
        # COPY FROM CSV is a single bulk transaction: ~540x faster.
        t_nodes = time.perf_counter()
        with tempfile.TemporaryDirectory() as tmp_dir:
            self._bulk_copy_nodes(tmp_dir, "File", files,
                ["id", "root_path", "name", "file_path", "language", "content"])
            self._bulk_copy_nodes(tmp_dir, "Function", functions,
                ["id", "root_path", "name", "file_path", "language",
                 "start_line", "end_line", "content", "is_exported"])
            self._bulk_copy_nodes(tmp_dir, "Class", classes,
                ["id", "root_path", "name", "file_path", "language",
                 "start_line", "end_line", "content", "is_exported", "kind"])
            self._bulk_copy_nodes(tmp_dir, "Method", methods,
                ["id", "root_path", "name", "file_path", "language",
                 "start_line", "end_line", "content", "is_exported", "owner_name", "scope_refs", "kind", "dsl_macro"])
        logger.info(
            "[SAVE] Node bulk insert: %d files + %d functions + %d classes + %d methods in %.2fs",
            len(files), len(functions), len(classes), len(methods),
            time.perf_counter() - t_nodes,
        )

        # ── Bulk edge inserts via UNWIND grouped by table pair ────────────────
        edges_by_pair: dict[tuple[str, str], list[dict]] = {}
        for e in data["edges"]:
            from_path = e["from"]
            to_path = e["to"]
            from_kind = node_kinds.get(from_path, "file")
            to_kind = node_kinds.get(to_path, "file")
            from_table = _KIND_TO_TABLE.get(from_kind)
            to_table = _KIND_TO_TABLE.get(to_kind)
            if not from_table or not to_table:
                continue
            if (from_table, to_table) not in _VALID_EDGE_PAIRS:
                continue
            edges_by_pair.setdefault((from_table, to_table), []).append({
                "a": f"{root_path}::{from_path}",
                "b": f"{root_path}::{to_path}",
                "t": e.get("rel", "DEPENDS_ON"),
            })

        t_edges = time.perf_counter()
        total_edges = 0
        for (from_table, to_table), rows in edges_by_pair.items():
            try:
                self._conn.execute(
                    f"UNWIND $rows AS row "
                    f"MATCH (a:{from_table} {{id: row.a}}), (b:{to_table} {{id: row.b}}) "
                    f"CREATE (a)-[:CodeRelation {{type: row.t}}]->(b)",
                    {"rows": rows},
                )
                total_edges += len(rows)
            except Exception as exc:
                logger.debug("Edge batch insert failed (%s→%s): %s", from_table, to_table, exc)
        logger.info(
            "[SAVE] Edge bulk insert: %d edges in %.2fs",
            total_edges, time.perf_counter() - t_edges,
        )

        # ── FTS indexes ───────────────────────────────────────────────────────
        for table, idx in [
            ("File", "file_fts"),
            ("Function", "function_fts"),
            ("Class", "class_fts"),
            ("Method", "method_fts"),
        ]:
            try:
                self._conn.execute(
                    f"CALL CREATE_FTS_INDEX('{table}', '{idx}', ['name', 'file_path', 'content'])"
                )
            except Exception:
                # Index already exists — drop and recreate (also picks up field changes).
                try:
                    self._conn.execute(f"CALL DROP_FTS_INDEX('{table}', '{idx}')")
                except Exception:
                    pass
                try:
                    self._conn.execute(
                        f"CALL CREATE_FTS_INDEX('{table}', '{idx}', ['name', 'file_path', 'content'])"
                    )
                except Exception as exc:
                    logger.warning(
                        "[FTS] FTS index creation failed for %s (likely orphaned backing "
                        "tables from a previous interrupted run). Call reset_db to restore "
                        "full-text search. Error: %s",
                        table, exc,
                    )

        # ── Embedding index (background thread) ───────────────────────────────
        symbols_for_embed = []
        for n in data["nodes"]:
            if n.get("kind") in ("function", "class", "method", "class_method"):
                symbols_for_embed.append({
                    "id": n["id"],  # raw node ID — matches BM25 result IDs for RRF fusion
                    "name": n.get("name", ""),
                    "content": n.get("content", ""),
                })
            elif n.get("kind") == "file":
                symbols_for_embed.append({
                    "id": n["id"],
                    "name": n.get("name", ""),
                    "file_path": n.get("file_path", ""),  # path is the meaningful signal for files
                    "content": "",
                })
        if symbols_for_embed:
            db_path = self._db_path
            cache = self._embedding_cache

            def _build_embed_bg() -> None:
                # Lower this thread's OS priority so fastembed ONNX inference
                # doesn't starve the parser's ThreadPoolExecutor if a second
                # index request arrives while embedding is still running.
                try:
                    os.nice(10)
                except Exception:
                    pass  # nice() unavailable on some platforms — non-fatal
                try:
                    _emb.build_index(db_path, root_path, symbols_for_embed)
                    cache.pop(root_path, None)
                    logger.info("[EMBED] Background embedding complete for %s", root_path)
                except Exception as exc:
                    logger.warning("[EMBED] Background embedding failed for %s: %s", root_path, exc)

            threading.Thread(target=_build_embed_bg, daemon=True, name="embed-build").start()
            logger.info("[EMBED] Embedding %d symbols in background ...", len(symbols_for_embed))

        logger.info(
            "Saved graph for %s: %d nodes, %d edges",
            root_path,
            len(data["nodes"]),
            len(data["edges"]),
        )

    def _bulk_copy_nodes(
        self,
        tmp_dir: str,
        table: str,
        rows: list[dict],
        columns: list[str],
    ) -> None:
        """Write rows to a temp CSV and bulk-load them with COPY FROM."""
        if not rows:
            return
        csv_path = os.path.join(tmp_dir, f"{table.lower()}.csv")
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
        # PARALLEL=FALSE is required to support content strings with embedded newlines.
        self._conn.execute(f"COPY {table} FROM '{csv_path}' (HEADER=TRUE, PARALLEL=FALSE)")

    def load_graph(self, root_path: str) -> DependencyGraph | None:
        """Load a previously saved graph. Returns None if not found."""
        self._ensure_schema()
        if not self.repo_exists(root_path):
            return None
        graph = DependencyGraph()
        id_to_key: dict[str, str] = {}  # kuzu id → graph node key

        for table in ["File", "Function", "Class", "Method"]:
            try:
                res = self._conn.execute(
                    f"MATCH (n:{table} {{root_path: $rp}}) RETURN n",
                    {"rp": root_path},
                )
                while res.has_next():
                    row = res.get_next()
                    n = row[0]
                    kind = table.lower()
                    kuzu_id = n["id"]
                    if kind == "file":
                        key = n["file_path"]
                        graph.add_node(
                            key, language=n["language"], kind="file",
                            name=n["name"], content=n.get("content", ""),
                            file_path=n["file_path"],
                        )
                    else:
                        prefix = root_path + "::"
                        key = kuzu_id[len(prefix):] if kuzu_id.startswith(prefix) else kuzu_id
                        graph.add_node(
                            key, language=n["language"], kind=kind,
                            name=n["name"], file_path=n["file_path"],
                            start_line=n.get("start_line", 0),
                            end_line=n.get("end_line", 0),
                            content=n.get("content", ""),
                            is_exported=n.get("is_exported", False),
                            owner_name=n.get("owner_name", ""),
                        )
                    id_to_key[kuzu_id] = key
            except Exception as exc:
                logger.debug("load_graph table %s failed: %s", table, exc)

        try:
            res = self._conn.execute(
                "MATCH (a)-[r:CodeRelation]->(b) "
                "WHERE a.root_path = $rp AND b.root_path = $rp "
                "RETURN a.id, b.id, r.type",
                {"rp": root_path},
            )
            while res.has_next():
                row = res.get_next()
                from_key = id_to_key.get(row[0])
                to_key = id_to_key.get(row[1])
                if from_key and to_key:
                    graph.add_edge(from_key, to_key, rel=row[2])
        except Exception as exc:
            logger.debug("load_graph edges failed: %s", exc)

        return graph

    def repo_exists(self, root_path: str) -> bool:
        self._ensure_schema()
        res = self._conn.execute(
            "MATCH (r:Repo {root_path: $root_path}) RETURN count(r)",
            {"root_path": root_path},
        )
        return res.get_next()[0] > 0

    def list_repos(self) -> list[dict]:
        self._ensure_schema()
        res = self._conn.execute("MATCH (r:Repo) RETURN r.root_path, r.indexed_at")
        repos = []
        while res.has_next():
            row = res.get_next()
            root_path, indexed_at = row[0], row[1]
            counts: dict[str, int] = {}
            for table in ["File", "Function", "Class", "Method"]:
                try:
                    r2 = self._conn.execute(
                        f"MATCH (n:{table} {{root_path: $rp}}) RETURN count(n)",
                        {"rp": root_path},
                    )
                    counts[table] = r2.get_next()[0]
                except Exception:
                    counts[table] = 0
            try:
                r3 = self._conn.execute(
                    "MATCH (a)-[:CodeRelation]->() WHERE a.root_path = $rp RETURN count(*)",
                    {"rp": root_path},
                )
                edge_count = r3.get_next()[0] if r3.has_next() else 0
            except Exception:
                edge_count = 0
            nodes = sum(counts.values())
            repos.append({
                "root_path": root_path,
                "indexed_at": indexed_at,
                "nodes": nodes,
                "edges": edge_count,
                "breakdown": counts,
            })
        return repos

    def delete_repo(self, root_path: str) -> bool:
        self._ensure_schema()
        if not self.repo_exists(root_path):
            return False
        self._delete_repo_data(root_path)
        _emb.delete_index(self._db_path, root_path)
        self._embedding_cache.pop(root_path, None)
        return True

    def reset_db(self) -> None:
        """Wipe the entire database and reinitialize. Recovery path for corruption."""
        import shutil
        try:
            self._conn.close()
        except Exception:
            pass
        try:
            self._db.close()
        except Exception:
            pass
        try:
            if os.path.isdir(self._db_path):
                shutil.rmtree(self._db_path, ignore_errors=True)
            elif os.path.isfile(self._db_path):
                os.remove(self._db_path)
        except Exception as exc:
            logger.warning("reset_db: failed to remove DB at %s (continuing): %s", self._db_path, exc)
        self._db = kuzu.Database(self._db_path)
        self._conn = kuzu.Connection(self._db)
        self._embedding_cache = {}
        self.init_schema()
        logger.info("Database reset and reinitialized at %s", self._db_path)

    def get_indexed_at(self, root_path: str) -> str | None:
        self._ensure_schema()
        res = self._conn.execute(
            "MATCH (r:Repo {root_path: $root_path}) RETURN r.indexed_at",
            {"root_path": root_path},
        )
        if res.has_next():
            return res.get_next()[0]
        return None

    # Maps kind name -> (FTS table, fallback_kind, kind_where) for search filtering.
    # kind_where: if not None, post-filter FTS results to nodes where node.kind == kind_where.
    # This is needed because Class table stores class/module/concern/db_table together.
    _FTS_TABLES: list[tuple[str, str, str | None]] = [
        ("Function", "function", None),
        ("Class",    "class",    None),
        ("Method",   "method",   None),
        ("File",     "file",     None),
    ]
    _KIND_TO_FTS_TABLE: dict[str, tuple[str, str, str | None]] = {
        "function":    ("Function", "function", None),
        "class":       ("Class",    "class",    None),
        "module":      ("Class",    "module",   "module"),      # filter to module nodes
        "concern":     ("Class",    "concern",  "concern"),     # filter to concern nodes
        "db_table":    ("Class",    "db_table", "db_table"),    # filter to db_table nodes
        "method":      ("Method",   "method",   None),
        "class_method": ("Method",  "method",   None),  # Ruby class methods stored in Method table
        "file":        ("File",     "file",     None),
        # Rails DSL macros — all stored in Method table, filterable by kind column
        "association":   ("Method", "association",   "association"),
        "hook":          ("Method", "hook",           "hook"),
        "scope":         ("Method", "scope",          "scope"),
        "validation":    ("Method", "validation",     "validation"),
        "mixin":         ("Method", "mixin",          "mixin"),
        "attr":          ("Method", "attr",           "attr"),
        "enum":          ("Method", "enum",           "enum"),
        "delegate":      ("Method", "delegate",       "delegate"),
        "alias":         ("Method", "alias",          "alias"),
        "helper_method": ("Method", "helper_method",  "helper_method"),
        "rescue_from":   ("Method", "rescue_from",    "rescue_from"),
    }

    def search(self, root_path: str, query: str, limit: int = 20, kind: str | None = None) -> list[dict]:
        """Hybrid BM25 + semantic search with RRF fusion.

        kind: optional filter — one of 'function', 'class', 'method', 'class_method', 'file'.
        Falls back to BM25-only if fastembed is not installed or no embedding index exists.
        """
        # --- BM25 via Kuzu FTS ---
        escaped = query.replace("\\", "\\\\").replace("'", "''")
        bm25_results: list[dict] = []
        bm25_fetch = max(limit * 3, 50)  # fetch more for RRF fusion
        # kind_filter saved before loop variable shadows it
        kind_filter = kind
        fts_tables = (
            [self._KIND_TO_FTS_TABLE[kind_filter]] if kind_filter and kind_filter in self._KIND_TO_FTS_TABLE
            else self._FTS_TABLES
        )
        for table, fallback_kind, kind_where in fts_tables:
            try:
                cypher = (
                    f"CALL QUERY_FTS_INDEX('{table}', '{table.lower()}_fts', '{escaped}', "
                    f"conjunctive := false) "
                    f"RETURN node, score ORDER BY score DESC LIMIT {bm25_fetch}"
                )
                res = self._conn.execute(cypher)
                while res.has_next():
                    row = res.get_next()
                    node, score = row[0], row[1]
                    if node.get("root_path", "") != root_path:
                        continue
                    # Use the stored kind column if available (Class table has it);
                    # fall back to the FTS table's fallback_kind for other tables.
                    actual_kind = node.get("kind") or fallback_kind
                    # Post-filter: when kind_where is set, skip nodes of a different kind.
                    if kind_where and actual_kind != kind_where:
                        continue
                    bm25_results.append({
                        "id": node.get("id", ""),
                        "name": node.get("name", ""),
                        "file_path": node.get("file_path", ""),
                        "kind": actual_kind,
                        "start_line": node.get("start_line", 0),
                        "end_line": node.get("end_line", 0),
                        "score": float(score),
                    })
            except Exception as exc:
                logger.debug("FTS search failed for %s: %s", table, exc)

        bm25_results.sort(key=lambda r: r["score"], reverse=True)

        # --- Semantic search (if embedding index available) ---
        semantic_results: list[tuple[str, float]] = []
        try:
            if root_path not in self._embedding_cache:
                loaded = _emb.load_index(self._db_path, root_path)
                if loaded is not None:
                    self._embedding_cache[root_path] = loaded
            if root_path in self._embedding_cache:
                ids, vectors = self._embedding_cache[root_path]
                semantic_results = _emb.semantic_search(ids, vectors, query, k=max(limit * 3, 50))
        except Exception as exc:
            logger.debug("Semantic search failed: %s", exc)

        # --- RRF fusion or BM25-only ---
        if not semantic_results:
            # No semantic index — return BM25 results as-is
            top = bm25_results[:limit]
            for i, r in enumerate(top):
                r["rank"] = i + 1
            return top

        fused = _emb.rrf_fuse(bm25_results, semantic_results, k=60)

        # Build ID → metadata map from BM25 results (already have metadata)
        meta: dict[str, dict] = {r["id"]: r for r in bm25_results}

        # For IDs only in semantic results (not in BM25), fetch metadata from Kuzu
        semantic_only_ids = [sid for sid, _ in fused[:limit] if sid not in meta]
        if semantic_only_ids:
            for sid in semantic_only_ids:
                for table, fallback_kind, _ in [("Function", "function", None), ("Class", "class", None), ("Method", "method", None), ("File", "file", None)]:
                    try:
                        res = self._conn.execute(
                            f"MATCH (n:{table} {{id: $id}}) RETURN n",
                            {"id": sid},
                        )
                        if res.has_next():
                            n = res.get_next()[0]
                            actual_kind = n.get("kind") or fallback_kind
                            meta[sid] = {
                                "id": sid,
                                "name": n.get("name", ""),
                                "file_path": n.get("file_path", ""),
                                "kind": actual_kind,
                                "start_line": n.get("start_line", 0),
                                "end_line": n.get("end_line", 0),
                                "score": 0.0,
                            }
                            break
                    except Exception:
                        pass

        results = []
        for rank, (sid, rrf_score) in enumerate(fused[:limit], 1):
            if sid not in meta:
                continue
            entry = {**meta[sid], "score": rrf_score, "rank": rank}
            results.append(entry)

        return results

    def execute_cypher(self, cypher: str) -> list[dict]:
        """Execute raw Cypher and return rows as list of dicts."""
        try:
            res = self._conn.execute(cypher)
            cols = res.get_column_names()
            rows = []
            while res.has_next():
                row = res.get_next()
                rows.append(dict(zip(cols, row)))
            return rows
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_schema(self) -> None:
        """Idempotently create tables; safe to call multiple times."""
        self.init_schema()

    def _delete_repo_data(self, root_path: str) -> None:
        """Remove all nodes (and their attached edges) for a repo via DETACH DELETE."""
        for table in ["File", "Function", "Class", "Method", "Repo"]:
            try:
                self._conn.execute(
                    f"MATCH (n:{table} {{root_path: $rp}}) DETACH DELETE n",
                    {"rp": root_path},
                )
            except Exception as exc:
                logger.warning("DETACH DELETE failed for table %s (root_path=%s): %s", table, root_path, exc)
        # Flush WAL so Kuzu reclaims freed node IDs before the next INSERT cycle.
        try:
            self._conn.execute("CHECKPOINT")
        except Exception as exc:
            logger.warning("CHECKPOINT after delete failed (non-fatal): %s", exc)
