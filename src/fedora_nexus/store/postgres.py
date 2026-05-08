"""PostgreSQL-backed graph store."""

from __future__ import annotations

import logging
import os

import psycopg
from psycopg.rows import dict_row

from fedora_nexus.graph.engine import DependencyGraph

logger = logging.getLogger(__name__)

_DEFAULT_DSN = "postgresql://fedora_nexus:fedora_nexus@localhost:5432/fedora_nexus"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS repos (
    id          SERIAL PRIMARY KEY,
    root_path   TEXT UNIQUE NOT NULL,
    indexed_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS nodes (
    id       SERIAL PRIMARY KEY,
    repo_id  INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    path     TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT '',
    kind     TEXT NOT NULL DEFAULT 'file',
    UNIQUE (repo_id, path)
);

CREATE TABLE IF NOT EXISTS edges (
    id        SERIAL PRIMARY KEY,
    repo_id   INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    from_path TEXT NOT NULL,
    to_path   TEXT NOT NULL,
    rel       TEXT NOT NULL DEFAULT 'DEPENDS_ON',
    UNIQUE (repo_id, from_path, to_path)
);

CREATE INDEX IF NOT EXISTS idx_nodes_repo ON nodes(repo_id);
CREATE INDEX IF NOT EXISTS idx_edges_repo ON edges(repo_id);
"""


class PostgresGraphStore:
    """Persist and retrieve DependencyGraph instances in PostgreSQL."""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or os.environ.get("DATABASE_URL", _DEFAULT_DSN)

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self._dsn, row_factory=dict_row)

    def init_schema(self) -> None:
        """Create tables and indexes if they do not exist."""
        with self._connect() as conn:
            conn.execute(_SCHEMA_SQL)
            conn.commit()
        logger.info("DB schema ready")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _upsert_repo(self, conn: psycopg.Connection, root_path: str) -> int:
        row = conn.execute(
            """
            INSERT INTO repos (root_path, indexed_at)
            VALUES (%s, now())
            ON CONFLICT (root_path) DO UPDATE SET indexed_at = now()
            RETURNING id
            """,
            (root_path,),
        ).fetchone()
        return row["id"]  # type: ignore[index]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save_graph(self, root_path: str, graph: DependencyGraph) -> None:
        """Persist a graph, replacing any previous data for this repo."""
        data = graph.to_adjacency_json()
        with self._connect() as conn:
            repo_id = self._upsert_repo(conn, root_path)
            conn.execute("DELETE FROM nodes WHERE repo_id = %s", (repo_id,))
            conn.execute("DELETE FROM edges WHERE repo_id = %s", (repo_id,))
            with conn.cursor() as cur:
                if data["nodes"]:
                    cur.executemany(
                        """
                        INSERT INTO nodes (repo_id, path, language, kind)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        [
                            (repo_id, n["id"], n.get("language", ""), n.get("kind", "file"))
                            for n in data["nodes"]
                        ],
                    )
                if data["edges"]:
                    cur.executemany(
                        """
                        INSERT INTO edges (repo_id, from_path, to_path, rel)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        [
                            (repo_id, e["from"], e["to"], e.get("rel", "DEPENDS_ON"))
                            for e in data["edges"]
                        ],
                    )
            conn.commit()
        logger.info(
            "Saved graph for %s: %d nodes, %d edges",
            root_path,
            len(data["nodes"]),
            len(data["edges"]),
        )

    def load_graph(self, root_path: str) -> DependencyGraph | None:
        """Load a previously saved graph. Returns None if not found."""
        with self._connect() as conn:
            repo = conn.execute(
                "SELECT id FROM repos WHERE root_path = %s",
                (root_path,),
            ).fetchone()
            if repo is None:
                return None
            repo_id = repo["id"]
            nodes = conn.execute(
                "SELECT path, language, kind FROM nodes WHERE repo_id = %s",
                (repo_id,),
            ).fetchall()
            edges = conn.execute(
                "SELECT from_path, to_path, rel FROM edges WHERE repo_id = %s",
                (repo_id,),
            ).fetchall()

        graph = DependencyGraph()
        for n in nodes:
            graph.add_node(n["path"], language=n["language"], kind=n["kind"])
        for e in edges:
            graph.add_edge(e["from_path"], e["to_path"], rel=e.get("rel", "DEPENDS_ON"))
        logger.info(
            "Loaded graph for %s: %d nodes, %d edges",
            root_path,
            len(nodes),
            len(edges),
        )
        return graph

    def repo_exists(self, root_path: str) -> bool:
        """Return True if this repo has already been indexed."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM repos WHERE root_path = %s",
                (root_path,),
            ).fetchone()
        return row is not None

    def list_repos(self) -> list[dict]:
        """Return all indexed repos with stats."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    r.root_path,
                    r.indexed_at,
                    (SELECT COUNT(*) FROM nodes WHERE repo_id = r.id) AS node_count,
                    (SELECT COUNT(*) FROM edges WHERE repo_id = r.id) AS edge_count
                FROM repos r
                ORDER BY r.indexed_at DESC
                """
            ).fetchall()
        return [
            {
                "root_path": r["root_path"],
                "indexed_at": r["indexed_at"].isoformat() if r["indexed_at"] else None,
                "nodes": r["node_count"],
                "edges": r["edge_count"],
            }
            for r in rows
        ]

    def delete_repo(self, root_path: str) -> bool:
        """Delete a repo and all its graph data. Returns True if found."""
        with self._connect() as conn:
            result = conn.execute(
                "DELETE FROM repos WHERE root_path = %s RETURNING id",
                (root_path,),
            ).fetchone()
            conn.commit()
        return result is not None

    def get_indexed_at(self, root_path: str) -> str | None:
        """Return the indexed_at timestamp for a repo as an ISO string, or None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT indexed_at FROM repos WHERE root_path = %s",
                (root_path,),
            ).fetchone()
        if row is None:
            return None
        return row["indexed_at"].isoformat() if row["indexed_at"] else None
