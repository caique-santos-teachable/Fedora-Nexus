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
echo '        "-v", "${env:HOME}/.depgraph:/data",'
echo '        "-v", "${env:HOME}/code:/repos:ro",'
echo '        "-e", "HOST_REPOS_PREFIX=${env:HOME}/code",'
echo '        "-e", "CONTAINER_REPOS_PATH=/repos",'
echo '        "depgraph-mcp:latest"'
echo '      ]'
echo '    }'
echo '  }'
echo '}'
