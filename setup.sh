#!/usr/bin/env bash
# =============================================================================
# fedora-nexus — Full Setup
# =============================================================================
# Runs all setup steps in sequence. Only dependency: Docker + bash.
#
# Usage:
#   bash setup.sh                          # runs all 3 steps interactively
#   bash setup.sh --skip-server            # skip server setup
#   bash setup.sh --skip-cli               # skip CLI build
#   bash setup.sh --skip-ai                # skip AI instructions
#   bash setup.sh --only-server            # run only server setup
#   bash setup.sh --only-cli               # run only CLI build
#   bash setup.sh --only-ai [--all|--claude|--cursor|--copilot|--windsurf]
#
# Individual scripts:
#   setup-server.sh          — builds image, starts container
#   setup-cli.sh             — builds Go CLI via Docker, installs to ~/.local/bin
#   setup-ai-instructions.sh — symlinks ai-instructions/ to agent config dirs
# =============================================================================

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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
divider(){ echo -e "${CYAN}────────────────────────────────────────────${NC}"; }

# ── flag parsing ─────────────────────────────────────────────────────────────
RUN_SERVER=true
RUN_CLI=true
RUN_AI=true
AI_FLAGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-server) RUN_SERVER=false ;;
    --skip-cli)    RUN_CLI=false    ;;
    --skip-ai)     RUN_AI=false     ;;
    --only-server) RUN_CLI=false;  RUN_AI=false    ;;
    --only-cli)    RUN_SERVER=false; RUN_AI=false   ;;
    --only-ai)     RUN_SERVER=false; RUN_CLI=false  ;;
    --all|--claude|--cursor|--copilot|--windsurf)
      AI_FLAGS+=("$1") ;;
    --help|-h)
      grep "^#" "$0" | grep -v "^#!/" | sed 's/^# \?//'
      exit 0
      ;;
    *)
      warn "Unknown flag: $1"
      echo "  Run '$0 --help' for usage."
      exit 1
      ;;
  esac
  shift
done

# ── Main ─────────────────────────────────────────────────────────────────────
echo ""
banner "==========================================="
banner "  fedora-nexus — Setup"
banner "==========================================="
echo ""
echo "  Steps:"
[[ "$RUN_SERVER" == "true" ]] && echo "    ✓ Server  (Docker image + container)" || echo "    – Server  (skipped)"
[[ "$RUN_CLI"    == "true" ]] && echo "    ✓ CLI     (Go build via Docker)"      || echo "    – CLI     (skipped)"
[[ "$RUN_AI"     == "true" ]] && echo "    ✓ AI      (symlinks for agent tools)" || echo "    – AI      (skipped)"
echo ""

if [[ "$RUN_SERVER" == "true" ]]; then
  divider
  echo ""
  bash "$REPO_DIR/scripts/setup-server.sh"
  echo ""
fi

if [[ "$RUN_CLI" == "true" ]]; then
  divider
  echo ""
  bash "$REPO_DIR/scripts/setup-cli.sh"
  echo ""
fi

if [[ "$RUN_AI" == "true" ]]; then
  divider
  echo ""
  if [[ ${#AI_FLAGS[@]} -gt 0 ]]; then
    bash "$REPO_DIR/scripts/setup-ai-instructions.sh" "${AI_FLAGS[@]}"
  else
    bash "$REPO_DIR/scripts/setup-ai-instructions.sh"
  fi
  echo ""
fi

divider
echo ""
banner "  Setup complete!"
echo ""
echo "  Quick start:"
echo "    fedora-nexus index /path/to/your/repo"
echo "    fedora-nexus blast-radius /path/to/your/repo src/main.py"
echo "    fedora-nexus --help"
echo ""
