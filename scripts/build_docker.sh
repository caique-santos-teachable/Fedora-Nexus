#!/bin/bash
set -e
cd "$(dirname "$0")/.."
echo "Building fedora-nexus MCP server Docker image..."
docker build -t fedora-nexus-mcp:latest .
echo "Done. Image: fedora-nexus-mcp:latest"
echo ""
echo "Cursor MCP config:"
echo '{'
echo '  "mcpServers": {'
echo '    "fedora-nexus": {'
echo '      "command": "docker",'
echo '      "args": ['
echo '        "run", "--rm", "-i",'
echo '        "-v", "${env:HOME}/.fedora-nexus:/data",'
echo '        "-v", "${env:HOME}/code:/repos:ro",'
echo '        "-e", "HOST_REPOS_PREFIX=${env:HOME}/code",'
echo '        "-e", "CONTAINER_REPOS_PATH=/repos",'
echo '        "fedora-nexus-mcp:latest"'
echo '      ]'
echo '    }'
echo '  }'
echo '}'
