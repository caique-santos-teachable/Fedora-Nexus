#!/usr/bin/env bash
# =============================================================================
# fedora-nexus — CLI Setup
# =============================================================================
# Builds the Go CLI inside a Docker container (no Go installation required)
# and installs it to ~/.local/bin/fedora-nexus.
#
# The binary is cached at .cli/fedora-nexus inside the repo (.gitignored).
# Re-running this script rebuilds and reinstalls.
#
# Only dependency: Docker.
#
# Usage:
#   bash setup-cli.sh
# =============================================================================

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLI_DIR="$REPO_DIR/cli"
BUILD_CACHE_DIR="$REPO_DIR/.cli"
BIN_DEST="$HOME/.local/bin/fedora-nexus"
DATA_DIR="$HOME/.local/share/fedora-nexus"

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

# ── OS / arch detection ───────────────────────────────────────────────────────
detect_goos() {
  case "$(uname -s)" in
    Darwin)  echo "darwin"  ;;
    Linux)   echo "linux"   ;;
    *)       echo "linux"   ;;
  esac
}

detect_goarch() {
  case "$(uname -m)" in
    arm64|aarch64) echo "arm64" ;;
    x86_64)        echo "amd64" ;;
    *)             echo "amd64" ;;
  esac
}

# ── Main ─────────────────────────────────────────────────────────────────────
echo ""
banner "==========================================="
banner "  fedora-nexus — CLI Setup"
banner "==========================================="
echo ""

if ! command -v docker &>/dev/null; then
  warn "docker not found."
  warn "Install Docker from https://docs.docker.com/get-docker/ and re-run."
  exit 1
fi

GOOS="$(detect_goos)"
GOARCH="$(detect_goarch)"
info "Target platform: $GOOS/$GOARCH"

mkdir -p "$BUILD_CACHE_DIR"

step "Building fedora-nexus CLI (Go 1.22, inside Docker)..."

# Extract version from pyproject.toml (single source of truth)
CLI_VERSION=$(grep '^version' "$REPO_DIR/pyproject.toml" | sed 's/.*= *"//' | sed 's/"//')
info "Version: $CLI_VERSION"

docker run --rm \
  -v "$CLI_DIR":/src:ro \
  -v "$BUILD_CACHE_DIR":/out \
  -e GOOS="$GOOS" \
  -e GOARCH="$GOARCH" \
  -e CGO_ENABLED=0 \
  golang:1.22-alpine \
  sh -c "cd /src && go mod download && go build -ldflags=\"-s -w -X 'fedora-nexus/cmd.Version=${CLI_VERSION}'\" -o /out/fedora-nexus ."

info "Binary built → $BUILD_CACHE_DIR/fedora-nexus"

# Install to ~/.local/bin
mkdir -p "$(dirname "$BIN_DEST")"
cp "$BUILD_CACHE_DIR/fedora-nexus" "$BIN_DEST"
chmod +x "$BIN_DEST"
info "Installed → $BIN_DEST"

# Copy docker-compose.yml for server commands (server start/stop/status)
mkdir -p "$DATA_DIR"
cp "$REPO_DIR/docker-compose.yml" "$DATA_DIR/docker-compose.yml"
info "docker-compose.yml copied → $DATA_DIR/docker-compose.yml"

echo ""
if command -v fedora-nexus &>/dev/null || [[ -x "$BIN_DEST" ]]; then
  info "fedora-nexus CLI ready."
else
  warn "Installed but ~/.local/bin is not in your PATH."
  warn "Add this line to your ~/.zshrc or ~/.bashrc, then restart your terminal:"
  warn ""
  warn "    export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

echo ""
echo "  Try: fedora-nexus --help"
echo ""
