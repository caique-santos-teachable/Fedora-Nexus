#!/usr/bin/env bash
# =============================================================================
# fedora-nexus — Setup & Agent Configuration
# =============================================================================
# Usage: bash setup.sh
#
# Architecture:
#   The fedora-nexus server (+ Kuzu database) runs in a Docker container.
#   The CLI is a thin HTTP client that talks to the container.
#
#   Server:  docker compose up -d mcp-server   (port 7832)
#   CLI:     fedora-nexus <command>                 (auto-detects the server)
#
#   If the server is unreachable the CLI falls back to local in-process mode.
#   To force a specific server: export FEDORA_NEXUS_SERVER_URL=http://host:7832
#
# This script:
#   1. Installs the fedora-nexus CLI (puts `fedora-nexus` in PATH via ~/.local/bin)
#   2. Copies the appropriate skill/instruction files to the right location
#      based on the AI agent you use (Claude Code, Cursor, Copilot, Windsurf,
#      or other agents that use the CLI directly)
#
# Canonical source for all agent artifacts:
#   ai-instructions/instructions/  — *.instructions.md (always-on + file-scoped rules)
#   ai-instructions/agents/        — *.agent.md (custom agents / subagents)
#   ai-instructions/prompts/       — *.prompt.md (reusable prompt files)
#   ai-instructions/skills/        — <name>/SKILL.md (agent skills)
#
# Supported agents and install destinations (symlinks, not copies):
#   Claude Code    → ~/.claude/rules/<name>.md          (instructions, .instructions stripped)
#                    ~/.claude/agents/*.agent.md         (subagents)
#                    ~/.claude/skills/<name>/SKILL.md    (skills)
#   Cursor         → ~/.cursor/rules/<name>.md           (instructions, .instructions stripped)
#                    ~/.cursor/agents/*.agent.md         (subagents)
#                    ~/.cursor/skills/<name>/SKILL.md    (skills)
#   GitHub Copilot → <VS Code user prompts>/*.instructions.md  (instructions, no rename)
#                    <VS Code user prompts>/*.agent.md         (custom agents)
#                    <VS Code user prompts>/*.prompt.md        (prompt files)
#                    <VS Code user prompts>/skills/<name>/SKILL.md (skills)
#   Windsurf       → ~/.codeium/windsurf/memories/<name>.md   (instructions, .instructions stripped)
#   Other / CLI    → no config file, just installs the CLI
# =============================================================================

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AI_DIR="$REPO_DIR/ai-instructions"
RULES_DIR="$AI_DIR/rules"
SKILLS_DIR="$AI_DIR/skills"
AGENTS_DIR="$AI_DIR/agents"

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

# ── OS detection ─────────────────────────────────────────────────────────────
detect_os() {
  case "$(uname -s 2>/dev/null)" in
    Darwin)               echo "macos"   ;;
    Linux)                echo "linux"   ;;
    CYGWIN*|MINGW*|MSYS*) echo "windows" ;;
    *)                    echo "unknown" ;;
  esac
}

# ── VS Code user prompts directory ───────────────────────────────────────────
vscode_prompts_dir() {
  local os; os="$(detect_os)"
  case "$os" in
    macos)   echo "$HOME/Library/Application Support/Code/User/prompts" ;;
    linux)   echo "$HOME/.config/Code/User/prompts" ;;
    windows) echo "${APPDATA:-$HOME/AppData/Roaming}/Code/User/prompts" ;;
    *)       echo "$HOME/.config/Code/User/prompts" ;;
  esac
}

# ── Go CLI installation ───────────────────────────────────────────────────────
# Find a Go interpreter that satisfies >=1.22.
find_go() {
  for candidate in go go1.22 go1.23 go1.24; do
    if command -v "$candidate" &>/dev/null; then
      local ver
      ver="$("$candidate" version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+'| head -1)"
      local major minor
      major="$(echo "$ver" | cut -d'.' -f1)"
      minor="$(echo "$ver" | cut -d'.' -f2)"
      if [[ "$major" -ge 1 && "$minor" -ge 22 ]]; then
        echo "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

install_go_cli() {
  local go_cmd
  if ! go_cmd="$(find_go)"; then
    warn "Go >=1.22 not found — skipping TUI CLI build."
    warn "Install Go from https://go.dev/dl/, then re-run setup.sh."
    warn "The Python CLI (fedora-nexus) is still available as a fallback."
    return
  fi

  step "Building fedora-nexus TUI CLI (Go + Bubble Tea)..."
  info "Using Go: $(command -v "$go_cmd") ($("$go_cmd" version))"

  local cli_dir="$REPO_DIR/cli"
  local bin_dir="$HOME/.local/bin"
  local data_dir="$HOME/.local/share/fedora-nexus"
  mkdir -p "$bin_dir" "$data_dir"

  # Copy docker-compose.yml to the known data dir so the CLI can find it
  # regardless of the working directory when server commands are run.
  cp "$REPO_DIR/docker-compose.yml" "$data_dir/docker-compose.yml"
  info "docker-compose.yml copied to: $data_dir/docker-compose.yml"

  (
    cd "$cli_dir"
    "$go_cmd" mod tidy --quiet 2>/dev/null || true
    if "$go_cmd" build -o "$bin_dir/fedora-nexus" . ; then
      info "Go CLI built → $bin_dir/fedora-nexus"
    else
      warn "Go build failed — Python CLI remains as fallback."
    fi
  )
}

# ── Docker server build + start ──────────────────────────────────────────────
install_docker_server() {
  if ! command -v docker &>/dev/null; then
    warn "docker not found — skipping server build."
    warn "Install Docker from https://docs.docker.com/get-docker/ and re-run setup.sh."
    return
  fi

  local compose_cmd
  if docker compose version &>/dev/null 2>&1; then
    compose_cmd="docker compose"
  elif command -v docker-compose &>/dev/null; then
    compose_cmd="docker-compose"
  else
    warn "Neither 'docker compose' (v2) nor 'docker-compose' (v1) found — skipping server build."
    warn "Upgrade Docker Desktop or install the Compose plugin and re-run setup.sh."
    return
  fi

  step "Building fedora-nexus server image..."
  if (cd "$REPO_DIR" && $compose_cmd build --no-cache mcp-server); then
    info "Image built successfully."
  else
    warn "docker build failed — server container will not be started."
    warn "Check the Docker output above, fix any errors, then re-run setup.sh."
    return
  fi

  step "Starting fedora-nexus server container (detached)..."
  if (cd "$REPO_DIR" && $compose_cmd up -d mcp-server); then
    info "Server container started (port 7832)."
    info "Check status with: docker compose ps"
  else
    warn "docker up failed — container may not be running."
    warn "Check the Docker output above, then run: $compose_cmd up -d mcp-server"
  fi
}

# ── Package installation ──────────────────────────────────────────────────────
# Find a Python interpreter that satisfies >=3.11.
find_python() {
  for candidate in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$candidate" &>/dev/null; then
      local ver
      ver="$("$candidate" -c 'import sys; print(sys.version_info[:2])' 2>/dev/null)"
      # ver looks like "(3, 13)"
      local major minor
      major="$(echo "$ver" | tr -d '() ' | cut -d',' -f1)"
      minor="$(echo "$ver" | tr -d '() ' | cut -d',' -f2)"
      if [[ "$major" -ge 3 && "$minor" -ge 11 ]]; then
        echo "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

install_package() {
  step "Installing fedora-nexus package..."

  local py
  if ! py="$(find_python)"; then
    echo ""
    warn "No Python >=3.11 interpreter found."
    warn "Install Python 3.11+ via https://python.org or 'brew install python@3.13', then re-run."
    exit 1
  fi

  info "Using interpreter: $(command -v "$py") ($("$py" --version))"

  local venv_dir="$HOME/.local/fedora-nexus-venv"
  local bin_dir="$HOME/.local/bin"

  # Create isolated venv if it doesn't exist (or if Python changed)
  if [[ ! -x "$venv_dir/bin/python" ]]; then
    "$py" -m venv "$venv_dir" --upgrade-deps
    info "Created venv: $venv_dir"
  fi

  # Install / upgrade the package
  "$venv_dir/bin/pip" install -e "$REPO_DIR" --quiet
  info "Package installed into venv"

  # Expose the CLI binary via a symlink in ~/.local/bin (which is usually in PATH)
  mkdir -p "$bin_dir"
  ln -sf "$venv_dir/bin/fedora-nexus" "$bin_dir/fedora-nexus"
  info "Symlinked fedora-nexus → $bin_dir/fedora-nexus"

  echo ""
  if command -v fedora-nexus &>/dev/null; then
    info "fedora-nexus CLI available at: $(command -v fedora-nexus)"
  else
    warn "fedora-nexus is installed but ~/.local/bin is not in your PATH."
    warn "Add this line to your ~/.zshrc or ~/.bashrc, then restart your terminal:"
    warn ""
    warn "    export PATH=\"\$HOME/.local/bin:\$PATH\""
  fi
}

# ── Agent configuration functions ─────────────────────────────────────────────
# Routing by file extension:
#   *.rule.md   → Claude: ~/.claude/rules/   | Cursor: ~/.cursor/rules/   | Copilot: *.instructions.md
#   *.skill.md  → Claude: ~/.claude/skills/  | Cursor: ~/.cursor/skills/  | Copilot: *.prompt.md + skills/<name>/SKILL.md
#   *.agent.md  → Claude: ~/.claude/agents/  | Cursor: ~/.cursor/agents/  | Copilot: *.agent.md
configure_claude() {
  step "Configuring Claude Code..."
  local rules_dir="$HOME/.claude/rules"
  local agents_dir="$HOME/.claude/agents"
  local skills_dir="$HOME/.claude/skills"
  mkdir -p "$rules_dir" "$agents_dir" "$skills_dir"

  # rules/*.rule.md → ~/.claude/rules/<name>.md
  for src in "$RULES_DIR"/*.rule.md; do
    [[ -f "$src" ]] || continue
    local name; name="$(basename "$src" .rule.md)"
    ln -sf "$src" "$rules_dir/$name.md"
    info "Rule linked: $rules_dir/$name.md"
  done

  # skills/*.skill.md → ~/.claude/skills/<name>/SKILL.md
  for src in "$SKILLS_DIR"/*.skill.md; do
    [[ -f "$src" ]] || continue
    local name; name="$(basename "$src" .skill.md)"
    mkdir -p "$skills_dir/$name"
    ln -sf "$src" "$skills_dir/$name/SKILL.md"
    info "Skill linked: $skills_dir/$name/SKILL.md"
  done

  # agents/*.agent.md → ~/.claude/agents/*.agent.md
  for src in "$AGENTS_DIR"/*.agent.md; do
    [[ -f "$src" ]] || continue
    ln -sf "$src" "$agents_dir/$(basename "$src")"
    info "Agent linked: $agents_dir/$(basename "$src")"
  done
}

configure_cursor() {
  step "Configuring Cursor..."
  local rules_dir="$HOME/.cursor/rules"
  local agents_dir="$HOME/.cursor/agents"
  local skills_dir="$HOME/.cursor/skills"
  mkdir -p "$rules_dir" "$agents_dir" "$skills_dir"

  # rules/*.rule.md → ~/.cursor/rules/<name>.md
  for src in "$RULES_DIR"/*.rule.md; do
    [[ -f "$src" ]] || continue
    local name; name="$(basename "$src" .rule.md)"
    ln -sf "$src" "$rules_dir/$name.md"
    info "Rule linked: $rules_dir/$name.md"
  done

  # skills/*.skill.md → ~/.cursor/skills/<name>/SKILL.md
  for src in "$SKILLS_DIR"/*.skill.md; do
    [[ -f "$src" ]] || continue
    local name; name="$(basename "$src" .skill.md)"
    mkdir -p "$skills_dir/$name"
    ln -sf "$src" "$skills_dir/$name/SKILL.md"
    info "Skill linked: $skills_dir/$name/SKILL.md"
  done

  # agents/*.agent.md → ~/.cursor/agents/*.agent.md
  for src in "$AGENTS_DIR"/*.agent.md; do
    [[ -f "$src" ]] || continue
    ln -sf "$src" "$agents_dir/$(basename "$src")"
    info "Agent linked: $agents_dir/$(basename "$src")"
  done
}

configure_copilot() {
  step "Configuring GitHub Copilot..."
  local dest_dir; dest_dir="$(vscode_prompts_dir)"
  local skills_dest="$dest_dir/skills"
  mkdir -p "$dest_dir" "$skills_dest"

  # rules/*.rule.md → <prompts>/<name>.instructions.md  (always-on guardrails)
  for src in "$RULES_DIR"/*.rule.md; do
    [[ -f "$src" ]] || continue
    local name; name="$(basename "$src" .rule.md)"
    ln -sf "$src" "$dest_dir/$name.instructions.md"
    info "Rule linked: $dest_dir/$name.instructions.md"
  done

  # skills/*.skill.md → <prompts>/<name>.prompt.md + <prompts>/skills/<name>/SKILL.md  (on-demand)
  for src in "$SKILLS_DIR"/*.skill.md; do
    [[ -f "$src" ]] || continue
    local name; name="$(basename "$src" .skill.md)"
    ln -sf "$src" "$dest_dir/$name.prompt.md"
    info "Skill linked: $dest_dir/$name.prompt.md"
    mkdir -p "$skills_dest/$name"
    ln -sf "$src" "$skills_dest/$name/SKILL.md"
    info "Skill linked: $skills_dest/$name/SKILL.md"
  done

  # agents/*.agent.md → <prompts>/*.agent.md
  for src in "$AGENTS_DIR"/*.agent.md; do
    [[ -f "$src" ]] || continue
    ln -sf "$src" "$dest_dir/$(basename "$src")"
    info "Agent linked: $dest_dir/$(basename "$src")"
  done
}

configure_windsurf() {
  step "Configuring Windsurf..."
  local dest_dir="$HOME/.codeium/windsurf/memories"
  mkdir -p "$dest_dir"

  # rules/*.rule.md and skills/*.skill.md → ~/.codeium/windsurf/memories/<name>.md
  for src in "$RULES_DIR"/*.rule.md "$SKILLS_DIR"/*.skill.md; do
    [[ -f "$src" ]] || continue
    local name
    if [[ "$src" == *.skill.md ]]; then
      name="$(basename "$src" .skill.md)"
    else
      name="$(basename "$src" .rule.md)"
    fi
    ln -sf "$src" "$dest_dir/$name.md"
    info "Memory linked: $dest_dir/$name.md"
  done
}

configure_cli_only() {
  step "CLI-only setup..."
  info "No agent config file needed — the fedora-nexus CLI is your interface."
  info "Reference: $SKILLS_DIR/fedora-nexus.skill.md"
  echo ""
  echo "  Quick start:"
  echo "    fedora-nexus index /path/to/your/repo"
  echo "    fedora-nexus blast-radius /path/to/your/repo src/main.py"
  echo "    fedora-nexus --help"
}

# ── Main ─────────────────────────────────────────────────────────────────────
echo ""
banner "==========================================="
banner "  fedora-nexus — Setup & Agent Configuration"
banner "==========================================="
echo ""
echo "  This script installs the fedora-nexus CLI and configures your AI agent"
echo "  to use it as a code dependency analysis tool."
echo ""
echo "  Which AI agent do you use?"
echo ""
echo "    1)  Claude Code"
echo "    2)  Cursor"
echo "    3)  GitHub Copilot"
echo "    4)  Windsurf"
echo "    5)  Other agent (CLI only — no config file)"
echo "    6)  All of the above"
echo ""
read -r -p "  Enter choice [1-6]: " CHOICE
echo ""

install_package
echo ""

install_go_cli
echo ""

install_docker_server
echo ""

case "$CHOICE" in
  1) configure_claude ;;
  2) configure_cursor ;;
  3) configure_copilot ;;
  4) configure_windsurf ;;
  5) configure_cli_only ;;
  6)
    configure_claude
    echo ""
    configure_cursor
    echo ""
    configure_copilot
    echo ""
    configure_windsurf
    ;;
  *)
    warn "Unknown choice '${CHOICE}'. Package is installed but no agent config was applied."
    warn "You can re-run setup.sh to configure an agent, or browse skills manually in: $AI_DIR"
    ;;
esac

echo ""
banner "==========================================="
info "Setup complete!"
echo ""
echo "  Run 'fedora-nexus --help' to see all available commands."
  echo "  Reference docs: $AI_DIR/"
banner "==========================================="
echo ""
