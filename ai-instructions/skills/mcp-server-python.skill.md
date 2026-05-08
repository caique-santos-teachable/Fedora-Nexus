---
name: mcp-server-python
description: 'Use when: scaffolding a new MCP server in Python, adding MCP tools, or wiring an existing service as an MCP server. Domain: Python MCP tooling.'
---

# MCP Server — Python Scaffolding Skill

## Context
The Model Context Protocol (MCP) lets AI agents call tools via a standard interface.
This skill covers the canonical pattern for a Python MCP server using the `mcp` SDK.

## Required dependencies
```toml
# pyproject.toml
[project]
dependencies = [
    "mcp>=1.0",
]
```

## Project structure
```
src/<pkg>/
  mcp/
    __init__.py
    server.py   ← all tool definitions here
```

## Canonical server.py pattern
```python
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

app = Server("<server-name>")

@app.list_tools()
async def list_tools() -> list[Tool]:
    """Return pure schema — NO execution logic here."""
    return [
        Tool(
            name="my_tool",
            description="What this tool does.",
            inputSchema={
                "type": "object",
                "properties": {
                    "param": {"type": "string"},
                },
                "required": ["param"],
            },
        ),
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Dispatch to private handlers — keep this thin."""
    if name == "my_tool":
        result = _handle_my_tool(arguments)
        return [TextContent(type="text", text=str(result))]
    return [TextContent(type="text", text=f'Unknown tool: {name}')]

def _handle_my_tool(args: dict) -> dict:
    # Business logic isolated here
    ...

async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

## 6-tool checklist for graph/analysis MCP servers
| Tool | Purpose |
|------|---------|
| `index_repo` | Walk filesystem, build graph, persist cache |
| `get_dependencies` | Forward edges from a node |
| `get_dependents` | Reverse edges to a node |
| `blast_radius` | BFS over reverse edges from changed set |
| `query_graph` | Cypher/query language interface |
| `get_graph` | Export full adjacency JSON |

## Cache pattern
Persist graph to `.fedora-nexus/graph.json` (or equivalent). Use `_load_or_index`:
```python
def _load_or_index(root_path: str) -> Graph:
    path = Path(root_path) / ".fedora-nexus/graph.json"
    if path.exists():
        return Graph.load(path)
    return _run_index(root_path)
```

## AI skill file formats
| Agent | File | Format |
|-------|------|--------|
| GitHub Copilot | `skills/copilot.instructions.md` | YAML frontmatter `applyTo: "**"` required |
| Claude | `skills/CLAUDE.md` | Bare markdown, no frontmatter |
| Cursor | `skills/cursor.mdc` | `.mdc` extension with `description:` frontmatter |

## Blast radius BFS — depth_map pattern
```python
visited: set[str] = set(changed_paths)
queue: deque[tuple[str, int]] = deque((p, 0) for p in changed_paths)
depth_map: dict[str, int] = {}
while queue:
    current, depth = queue.popleft()
    for dep in graph.get_dependents(current):
        if dep not in visited:
            visited.add(dep)
            depth_map[dep] = depth + 1
            queue.append((dep, depth + 1))
```

## Lark parser singleton
```python
# Module-level singleton — compile grammar once, not per query
_PARSER = Lark(_GRAMMAR, parser="earley", ambiguity="resolve")
```
