#!/usr/bin/env bash
# =============================================================================
# fedora-nexus — Server Setup
# =============================================================================
# Builds the Docker image and starts the MCP server container.
# Only dependency: Docker (with Compose v2 or docker-compose v1).
#
# Usage:
#   bash setup-server.sh              # interactive: prompts for HOST_REPOS_PREFIX
#   bash setup-server.sh /path/repos  # non-interactive: pass prefix as argument
#   bash setup-server.sh --stop       # stop and remove the server container
#   bash setup-server.sh --status     # show container status
# =============================================================================

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_DIR/.env"

# ── colours ──────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
  BOLD='\033[1m'; NC='\033[0m'
else
  GREEN=''; YELLOW=''; CYAN=''; BOLD=''; NC=''
fi

info()  { echo -e "${GREEN}  ✓${NC}  $*"; }
warn()  { echo -e "${YELLOW}  !${NC}  $*"; }
step()  { echo -e "${CYAN}${BOLD}──>${NC} $*"; }
banner(){ echo -e "${BOLD}$*${NC}"; }

# ── Docker / Compose detection ────────────────────────────────────────────────
require_docker() {
  if ! command -v docker &>/dev/null; then
    warn "docker not found."
    warn "Install Docker from https://docs.docker.com/get-docker/ and re-run."
    exit 1
  fi
}

compose_cmd() {
  if docker compose version &>/dev/null 2>&1; then
    echo "docker compose"
  elif command -v docker-compose &>/dev/null; then
    echo "docker-compose"
  else
    warn "Neither 'docker compose' (v2) nor 'docker-compose' (v1) found."
    warn "Upgrade Docker Desktop or install the Compose plugin and re-run."
    exit 1
  fi
}

# ── .env management ───────────────────────────────────────────────────────────
write_env() {
  local prefix="$1"
  # Preserve existing entries not managed here, overwrite HOST_REPOS_PREFIX
  if [[ -f "$ENV_FILE" ]]; then
    # Remove existing HOST_REPOS_PREFIX line if present
    grep -v "^HOST_REPOS_PREFIX=" "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE" || true
  fi
  echo "HOST_REPOS_PREFIX=$prefix" >> "$ENV_FILE"
  info ".env updated: HOST_REPOS_PREFIX=$prefix"
}

resolve_prefix() {
  # 1. CLI argument
  if [[ -n "${1:-}" ]]; then
    echo "$1"
    return
  fi
  # 2. Existing .env
  if [[ -f "$ENV_FILE" ]]; then
    local val
    val="$(grep "^HOST_REPOS_PREFIX=" "$ENV_FILE" 2>/dev/null | cut -d'=' -f2- | tr -d '"' | tr -d "'")"
    if [[ -n "$val" ]]; then
      echo "$val"
      return
    fi
  fi
  # 3. Interactive prompt
  echo ""
  echo "  Where are your code repositories?"
  echo "  The server will mount this directory read-only and index repos inside it."
  echo "  Example: /Users/you/projects"
  echo ""
  read -r -p "  Path to your repos: " prefix
  if [[ -z "$prefix" ]]; then
    warn "Path cannot be empty."
    exit 1
  fi
  echo "$prefix"
}

# ── Actions ───────────────────────────────────────────────────────────────────
do_stop() {
  require_docker
  local cmd; cmd="$(compose_cmd)"
  step "Stopping fedora-nexus server..."
  (cd "$REPO_DIR" && $cmd down)
  info "Server stopped."
}

do_status() {
  require_docker
  local cmd; cmd="$(compose_cmd)"
  (cd "$REPO_DIR" && $cmd ps)
}

do_start() {
  local prefix="$1"
  require_docker
  local cmd; cmd="$(compose_cmd)"

  write_env "$prefix"

  step "Building fedora-nexus server image..."
  (cd "$REPO_DIR" && $cmd build mcp-server)
  info "Image built."

  step "Starting fedora-nexus server (port 7832)..."
  (cd "$REPO_DIR" && $cmd up -d mcp-server)

  # Wait up to 10s for health check
  local i=0
  while [[ $i -lt 10 ]]; do
    sleep 1
    if curl -sf http://localhost:7832/health &>/dev/null; then
      info "Server is healthy at http://localhost:7832"
      break
    fi
    i=$((i + 1))
  done
  if [[ $i -eq 10 ]]; then
    warn "Server did not respond on :7832 within 10s — check logs:"
    warn "  $cmd logs mcp-server"
  fi
}

# ── Main ─────────────────────────────────────────────────────────────────────
echo ""
banner "==========================================="
banner "  fedora-nexus — Server Setup"
banner "==========================================="
echo ""

case "${1:-}" in
  --stop)   do_stop   ; exit 0 ;;
  --status) do_status ; exit 0 ;;
  --help|-h)
    echo "  Usage: $0 [HOST_REPOS_PREFIX | --stop | --status]"
    exit 0
    ;;
esac

PREFIX="$(resolve_prefix "${1:-}")"
do_start "$PREFIX"

echo ""
info "Done. MCP server running on http://localhost:7832"
echo ""
echo "  Commands:"
echo "    Status : bash setup-server.sh --status"
echo "    Stop   : bash setup-server.sh --stop"
echo "    Logs   : docker compose logs -f mcp-server"
echo ""
