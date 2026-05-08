#!/bin/bash
set -e
cd "$(dirname "$0")/.."
echo "Building depgraph MCP server Docker image..."
docker build -t depgraph-mcp:latest .
echo "Done. Image: depgraph-mcp:latest"
echo ""
echo "Cursor MCP config:"
echo '{'
echo '  "mcpServers": {'
echo '    "depgraph": {'
echo '      "command": "docker",'
echo '      "args": ['
echo '        "run", "--rm", "-i",'
echo '        "--network", "depgraph_default",'
echo '        "-v", "/Users/caiquesantos/code:/repos:ro",'
echo '        "-e", "DATABASE_URL=postgresql://depgraph:depgraph@depgraph-postgres:5432/depgraph",'
echo '        "depgraph-mcp:latest"'
echo '      ]'
echo '    }'
echo '  }'
echo '}'
