"""Minimal Cypher-subset parser and executor using lark."""
# legacy — replaced by native Kuzu Cypher via KuzuGraphStore.execute_cypher(); kept for reference

from __future__ import annotations

import logging
from typing import Any

import networkx as nx
from lark import Lark, Transformer, v_args

from fedora_nexus.graph.engine import DependencyGraph

logger = logging.getLogger(__name__)

_GRAMMAR = r"""
    start: match_clause where_clause? return_clause

    match_clause: "MATCH" node_pattern (rel_pattern node_pattern)*

    node_pattern: "(" CNAME (":" CNAME)? props? ")"
    props: "{" prop ("," prop)* "}"
    prop: CNAME ":" STRING_LIT

    rel_pattern: LEFT_ARROW "[" ":" CNAME hop_spec? "]" DASH
              | DASH "[" ":" CNAME hop_spec? "]" RIGHT_ARROW
              | DASH "[" ":" CNAME hop_spec? "]" DASH

    DASH: "-"

    hop_spec: "*" hop_range?
    hop_range: INT (".." INT?)?

    where_clause: "WHERE" condition (("AND" | "OR") condition)*
    condition: expr op value
    expr: CNAME "." CNAME
    op: OP_CONTAINS | OP_ENDS_WITH | OP_STARTS_WITH | OP_EQ
    value: STRING_LIT

    OP_CONTAINS: "CONTAINS"
    OP_ENDS_WITH: "ENDS WITH"
    OP_STARTS_WITH: "STARTS WITH"
    OP_EQ: "="

    return_clause: "RETURN" return_item ("," return_item)*
    return_item: CNAME

    LEFT_ARROW: "<-"
    RIGHT_ARROW: "->"

    STRING_LIT: /\"[^\"]*\"|\'[^\']*\'/

    %import common.CNAME
    %import common.INT
    %import common.WS
    %ignore WS
"""

_PARSER = Lark(_GRAMMAR, parser="earley", ambiguity="resolve")


@v_args(inline=True)
class _CypherTransformer(Transformer):
    def start(self, match_clause, *rest):
        where = None
        ret = None
        for item in rest:
            if isinstance(item, _WhereClause):
                where = item
            elif isinstance(item, list):
                ret = item
        return _Query(match=match_clause, where=where, returns=ret)

    def match_clause(self, *args):
        nodes = []
        rels = []
        i = 0
        items = list(args)
        while i < len(items):
            if isinstance(items[i], _NodePattern):
                nodes.append(items[i])
            elif isinstance(items[i], _RelPattern):
                rels.append(items[i])
            i += 1
        return _MatchClause(nodes=nodes, rels=rels)

    def node_pattern(self, *args):
        name = str(args[0])
        label = None
        props = {}
        for a in args[1:]:
            if isinstance(a, str):
                label = a
            elif isinstance(a, dict):
                props = a
        return _NodePattern(name=name, label=label, props=props)

    def props(self, *pairs):
        return dict(pairs)

    def prop(self, key, value):
        return (str(key), str(value)[1:-1])  # strip surrounding quotes (" or ')

    def rel_pattern(self, *args):
        rel_type = None
        min_hops = 1
        max_hops = 1
        direction = "right"
        has_left = False
        has_right = False
        for a in args:
            if hasattr(a, "type"):
                if a.type == "LEFT_ARROW":
                    has_left = True
                elif a.type == "RIGHT_ARROW":
                    has_right = True
            elif isinstance(a, str):
                rel_type = a
            elif isinstance(a, _HopSpec):
                min_hops = a.min_hops
                max_hops = a.max_hops
        if has_left and not has_right:
            direction = "left"
        elif has_left and has_right:
            direction = "both"
        return _RelPattern(rel_type=rel_type, min_hops=min_hops, max_hops=max_hops, direction=direction)

    def hop_spec(self, *args):
        if not args:
            return _HopSpec(min_hops=1, max_hops=10)
        return args[0]

    def hop_range(self, *args):
        nums = [int(a) for a in args]
        if len(nums) == 1:
            return _HopSpec(min_hops=nums[0], max_hops=nums[0])
        return _HopSpec(min_hops=nums[0], max_hops=nums[1] if len(nums) > 1 else 10)

    def where_clause(self, *conditions):
        return _WhereClause(conditions=list(conditions))

    def condition(self, expr, op, value):
        return _Condition(var=expr[0], attr=expr[1], op=str(op).strip(), value=str(value)[1:-1])

    def expr(self, var, attr):
        return (str(var), str(attr))

    def op(self, *args):
        return " ".join(str(a) for a in args)

    def value(self, token):
        return str(token)  # keep quotes; condition() strips them

    def return_clause(self, *items):
        return [str(i) for i in items]

    def return_item(self, name):
        return str(name)

    def CNAME(self, token):
        return str(token)


class _NodePattern:
    def __init__(self, name: str, label: str | None, props: dict):
        self.name = name
        self.label = label
        self.props = props


class _RelPattern:
    def __init__(self, rel_type: str | None, min_hops: int, max_hops: int, direction: str):
        self.rel_type = rel_type
        self.min_hops = min_hops
        self.max_hops = max_hops
        self.direction = direction


class _HopSpec:
    def __init__(self, min_hops: int, max_hops: int):
        self.min_hops = min_hops
        self.max_hops = max_hops


class _Condition:
    def __init__(self, var: str, attr: str, op: str, value: str):
        self.var = var
        self.attr = attr
        self.op = op
        self.value = value

    def matches(self, node_attrs: dict) -> bool:
        val = node_attrs.get(self.attr, "")
        if self.op == "CONTAINS":
            return self.value in str(val)
        elif self.op == "ENDS WITH":
            return str(val).endswith(self.value)
        elif self.op == "STARTS WITH":
            return str(val).startswith(self.value)
        elif self.op == "=":
            return str(val) == self.value
        return False


class _WhereClause:
    def __init__(self, conditions: list[_Condition]):
        self.conditions = conditions

    def matches(self, bindings: dict[str, dict]) -> bool:
        for cond in self.conditions:
            attrs = bindings.get(cond.var, {})
            if not cond.matches(attrs):
                return False
        return True


class _MatchClause:
    def __init__(self, nodes: list[_NodePattern], rels: list[_RelPattern]):
        self.nodes = nodes
        self.rels = rels


class _Query:
    def __init__(self, match: _MatchClause, where: _WhereClause | None, returns: list[str] | None):
        self.match = match
        self.where = where
        self.returns = returns or []


def execute(graph: DependencyGraph, cypher: str) -> list[dict[str, Any]]:
    """Parse and execute a Cypher-subset query against the graph."""
    tree = _PARSER.parse(cypher)
    query: _Query = _CypherTransformer().transform(tree)
    return _execute_query(graph, query)


def _node_matches_pattern(node: str, attrs: dict, pattern: _NodePattern) -> bool:
    if pattern.label and attrs.get("kind", "file").lower() != pattern.label.lower():
        # Allow "File" to match kind="file"
        pass  # label matching is loose for now
    for k, v in pattern.props.items():
        if str(attrs.get(k, "")) != v:
            return False
    return True


def _execute_query(graph: DependencyGraph, query: _Query) -> list[dict[str, Any]]:
    g = graph.networkx_graph
    nodes_list = list(g.nodes(data=True))

    match = query.match

    # Single node pattern (no relationship)
    if not match.rels:
        pattern = match.nodes[0]
        results = []
        for node, attrs in nodes_list:
            if _node_matches_pattern(node, attrs, pattern):
                bindings = {pattern.name: {"id": node, **attrs}}
                if query.where and not query.where.matches(bindings):
                    continue
                result = {}
                for var in query.returns:
                    if var == pattern.name:
                        result[var] = {"id": node, **attrs}
                results.append(result)
        return results

    # Pattern with relationship: (a)-[rel]->(b)
    if len(match.nodes) >= 2 and len(match.rels) >= 1:
        src_pattern = match.nodes[0]
        dst_pattern = match.nodes[1]
        rel_pattern = match.rels[0]
        results = []

        for src_node, src_attrs in nodes_list:
            if not _node_matches_pattern(src_node, src_attrs, src_pattern):
                continue

            max_hops = rel_pattern.max_hops if rel_pattern.max_hops < 100 else 10
            min_hops = rel_pattern.min_hops

            if rel_pattern.direction in ("right", "both"):
                reachable = nx.single_source_shortest_path_length(g, src_node, cutoff=max_hops)
            else:
                reachable = nx.single_source_shortest_path_length(g.reverse(), src_node, cutoff=max_hops)

            for dst_node, depth in reachable.items():
                if depth < min_hops or dst_node == src_node:
                    continue
                if not g.has_node(dst_node):
                    continue
                dst_attrs = dict(g.nodes[dst_node])
                if not _node_matches_pattern(dst_node, dst_attrs, dst_pattern):
                    continue
                bindings = {
                    src_pattern.name: {"id": src_node, **src_attrs},
                    dst_pattern.name: {"id": dst_node, **dst_attrs},
                }
                if query.where and not query.where.matches(bindings):
                    continue
                result = {}
                for var in query.returns:
                    if var == src_pattern.name:
                        result[var] = {"id": src_node, **src_attrs}
                    elif var == dst_pattern.name:
                        result[var] = {"id": dst_node, **dst_attrs}
                results.append(result)
        return results

    return []
