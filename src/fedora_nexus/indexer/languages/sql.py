"""SQL DDL indexer — regex-based, no tree-sitter dependency.

Extracts:
  - CREATE TABLE → db_table Class nodes with column summary in content
  - FOREIGN KEY ... REFERENCES → DEPENDS_ON edges between table nodes

Supports PostgreSQL (Rails structure.sql default) and MySQL formats.
"""
from __future__ import annotations

import re
from typing import Any

from fedora_nexus.graph.engine import DependencyGraph

# ── Regex patterns ────────────────────────────────────────────────────────────

# Matches: CREATE TABLE [IF NOT EXISTS] [schema.]table_name (
# Handles: "public"."table" / `schema`.`table` / bare_name
_QUOTED = r'(?:"[^"]*"|\`[^\`]*\`|\w+)'
_SCHEMA_TABLE = rf'(?:{_QUOTED}\s*\.\s*)?({_QUOTED})'

_CREATE_TABLE_RE = re.compile(
    r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?' + _SCHEMA_TABLE + r'\s*\(',
    re.IGNORECASE,
)

# Matches ALTER TABLE [ONLY] [schema.]from_table ... FOREIGN KEY (...) REFERENCES [schema.]to_table
_ALTER_FK_RE = re.compile(
    r'ALTER\s+TABLE\s+(?:ONLY\s+)?' + _SCHEMA_TABLE +
    r'[^;]*?'
    r'FOREIGN\s+KEY\s*\([^)]*\)\s+REFERENCES\s+' + _SCHEMA_TABLE,
    re.IGNORECASE | re.DOTALL,
)

# Matches inline FOREIGN KEY inside a CREATE TABLE body
_INLINE_FK_RE = re.compile(
    r'FOREIGN\s+KEY\s*\([^)]*\)\s+REFERENCES\s+' + _SCHEMA_TABLE,
    re.IGNORECASE,
)

# Matches a column definition line: optional_quote identifier optional_quote  type
_COLUMN_RE = re.compile(
    r'^\s*["\`]?(\w+)["\`]?\s+((?:\w+)(?:\s*\([^)]*\))?)',
    re.IGNORECASE,
)

_DDL_KEYWORDS = {
    "CONSTRAINT", "PRIMARY", "UNIQUE", "INDEX", "KEY",
    "CHECK", "FOREIGN", "EXCLUDE", "TABLESPACE",
}


def _unquote(s: str) -> str:
    """Strip SQL identifier quotes: "name" / `name` → name."""
    s = s.strip()
    for ch in ('"', '`', "'"):
        if len(s) >= 2 and s[0] == ch and s[-1] == ch:
            return s[1:-1]
    return s


def _strip_schema(qualified: str) -> str:
    """'public'.'table' or "public"."table" → table (unquoted)."""
    # Split on the first `.` that is not inside quotes
    parts = re.split(r'\s*\.\s*', qualified, maxsplit=1)
    return _unquote(parts[-1])


class SqlIndexer:
    """Regex-based SQL DDL parser. Extracts DB tables, columns, and FK DEPENDS_ON edges."""

    def extract_symbols(
        self,
        rel: str,
        source: str,
        graph: DependencyGraph,
    ) -> dict[str, str]:
        """Parse *source* DDL; add db_table nodes and DEPENDS_ON FK edges to *graph*.

        Returns top-level dict {table_name: sym_id} for cross-file symbol registry.
        """
        top_level: dict[str, str] = {}
        table_bodies: dict[str, str] = {}

        # ── Pass 1: CREATE TABLE → nodes ──────────────────────────────────────
        for m in _CREATE_TABLE_RE.finditer(source):
            raw_name = m.group(1)
            table_name = _strip_schema(raw_name)
            open_paren_pos = m.end() - 1  # position of the '(' matched by \s*\(
            body, _ = self._extract_balanced_body(source, open_paren_pos)
            table_bodies[table_name] = body

            columns = self._parse_columns(body)
            col_summary = ", ".join(f"{c['name']} {c['type']}" for c in columns[:50])
            col_names = [c["name"] for c in columns]
            start_line = source[: m.start()].count("\n") + 1
            sym_id = f"{rel}#db_table:{table_name}"

            if not graph.has_node(sym_id):
                graph.add_node(
                    sym_id,
                    language="sql",
                    kind="db_table",
                    name=table_name,
                    file_path=rel,
                    start_line=start_line,
                    end_line=0,
                    content=col_summary,
                    is_exported=False,
                    columns=col_names,
                )
            graph.add_edge(rel, sym_id, rel="CONTAINS")
            top_level[table_name] = sym_id

        # ── Pass 2: inline FK edges (inside CREATE TABLE body) ────────────────
        for table_name, body in table_bodies.items():
            from_sym = f"{rel}#db_table:{table_name}"
            for fk_m in _INLINE_FK_RE.finditer(body):
                to_table = _strip_schema(fk_m.group(1))
                to_sym = f"{rel}#db_table:{to_table}"
                if to_table != table_name and graph.has_node(from_sym) and graph.has_node(to_sym):
                    graph.add_edge(from_sym, to_sym, rel="DEPENDS_ON")

        # ── Pass 3: ALTER TABLE … ADD CONSTRAINT … FOREIGN KEY edges ─────────
        for m in _ALTER_FK_RE.finditer(source):
            from_table = _strip_schema(m.group(1))
            to_table = _strip_schema(m.group(2))
            from_sym = f"{rel}#db_table:{from_table}"
            to_sym = f"{rel}#db_table:{to_table}"
            if from_table != to_table and graph.has_node(from_sym) and graph.has_node(to_sym):
                graph.add_edge(from_sym, to_sym, rel="DEPENDS_ON")

        return top_level

    @staticmethod
    def _extract_balanced_body(source: str, open_paren_pos: int) -> tuple[str, int]:
        """Return (body_text, end_pos) for the balanced ( ) block starting at open_paren_pos."""
        depth = 0
        i = open_paren_pos
        while i < len(source):
            c = source[i]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return source[open_paren_pos + 1 : i], i + 1
            i += 1
        return source[open_paren_pos + 1 :], len(source)

    @staticmethod
    def _parse_columns(body: str) -> list[dict[str, str]]:
        """Extract column name + type from the body of a CREATE TABLE statement."""
        columns: list[dict[str, str]] = []
        for raw_line in body.splitlines():
            stripped = raw_line.strip().rstrip(",")
            if not stripped:
                continue
            # Skip DDL keywords that open a new clause
            first_word = stripped.split()[0].upper().rstrip(")") if stripped.split() else ""
            if first_word in _DDL_KEYWORDS:
                continue
            m = _COLUMN_RE.match(stripped)
            if m and m.group(1).upper() not in _DDL_KEYWORDS:
                columns.append({"name": m.group(1), "type": m.group(2).lower()})
        return columns
