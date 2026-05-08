#!/usr/bin/env bash
# =============================================================================
# depgraph — Setup & Agent Configuration
# =============================================================================
# Usage: bash setup.sh
#
# Architecture:
#   The depgraph server (+ Kuzu database) runs in a Docker container.
#   The CLI is a thin HTTP client that talks to the container.
#
#   Server:  docker compose up -d mcp-server   (port 7832)
#   CLI:     depgraph <command>                 (auto-detects the server)
#
#   If the server is unreachable the CLI falls back to local in-process mode.
#   To force a specific server: export DEPGRAPH_SERVER_URL=http://host:7832
#
# This script:
#   1. Installs the depgraph CLI (puts `depgraph` in PATH via ~/.local/bin)
#   2. Copies the appropriate skill/instruction files to the right location
#      based on the AI agent you use (Claude Code, Cursor, Copilot, Windsurf,
#      or other agents that use the CLI directly)
#
# Supported agents:
#   Claude Code    → ~/.claude/CLAUDE.md + ~/.claude/commands/depgraph-*.md
#   Cursor         → ~/.cursor/rules/depgraph.mdc (always-apply)
#                    ~/.cursor/skills/depgraph-*/SKILL.md (Agent Skills)
#   GitHub Copilot → <VS Code user prompts>/depgraph*.{instructions,prompt}.md
#   Windsurf       → ~/.codeium/windsurf/memories/depgraph*.md
#   Other / CLI    → no config file, just installs the CLI
# =============================================================================

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_DIR="$REPO_DIR/skills"

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
    warn "The Python CLI (depgraph) is still available as a fallback."
    return
  fi

  step "Building depgraph TUI CLI (Go + Bubble Tea)..."
  info "Using Go: $(command -v "$go_cmd") ($("$go_cmd" version))"

  local cli_dir="$REPO_DIR/cli"
  local bin_dir="$HOME/.local/bin"
  local data_dir="$HOME/.local/share/depgraph"
  mkdir -p "$bin_dir" "$data_dir"

  # Copy docker-compose.yml to the known data dir so the CLI can find it
  # regardless of the working directory when server commands are run.
  cp "$REPO_DIR/docker-compose.yml" "$data_dir/docker-compose.yml"
  info "docker-compose.yml copied to: $data_dir/docker-compose.yml"

  (
    cd "$cli_dir"
    "$go_cmd" mod tidy --quiet 2>/dev/null || true
    if "$go_cmd" build -o "$bin_dir/depgraph" . ; then
      info "Go CLI built → $bin_dir/depgraph"
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

  step "Building depgraph server image..."
  if (cd "$REPO_DIR" && $compose_cmd build --no-cache mcp-server); then
    info "Image built successfully."
  else
    warn "docker build failed — server container will not be started."
    warn "Check the Docker output above, fix any errors, then re-run setup.sh."
    return
  fi

  step "Starting depgraph server container (detached)..."
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
  step "Installing depgraph package..."

  local py
  if ! py="$(find_python)"; then
    echo ""
    warn "No Python >=3.11 interpreter found."
    warn "Install Python 3.11+ via https://python.org or 'brew install python@3.13', then re-run."
    exit 1
  fi

  info "Using interpreter: $(command -v "$py") ($("$py" --version))"

  local venv_dir="$HOME/.local/depgraph-venv"
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
  ln -sf "$venv_dir/bin/depgraph" "$bin_dir/depgraph"
  info "Symlinked depgraph → $bin_dir/depgraph"

  echo ""
  if command -v depgraph &>/dev/null; then
    info "depgraph CLI available at: $(command -v depgraph)"
  else
    warn "depgraph is installed but ~/.local/bin is not in your PATH."
    warn "Add this line to your ~/.zshrc or ~/.bashrc, then restart your terminal:"
    warn ""
    warn "    export PATH=\"\$HOME/.local/bin:\$PATH\""
  fi
}

# ── Agent configuration functions ────────────────────────────────────────────
configure_claude() {
  step "Configuring Claude Code..."
  local dest_dir="$HOME/.claude"
  local cmd_dir="$dest_dir/commands"
  mkdir -p "$dest_dir" "$cmd_dir"

  # Main instructions file
  cp "$SKILLS_DIR/CLAUDE.md" "$dest_dir/CLAUDE.md"
  info "Instructions written to: $dest_dir/CLAUDE.md"

  # Prompt files → slash commands (strip VS Code frontmatter, keep body)
  # Each file becomes a /depgraph:<name> command in Claude Code
  local PROMPTS_DIR="$REPO_DIR/.github/prompts"
  for src in "$PROMPTS_DIR"/depgraph-*.prompt.md; do
    local base; base="$(basename "$src" .prompt.md)"
    local dest="$cmd_dir/${base}.md"
    # Strip YAML frontmatter (---...---) and write the body
    awk '/^---/{if(NR==1){skip=1;next}else if(skip){skip=0;next}} !skip' "$src" > "$dest"
    info "Command written to: $dest"
  done
}

configure_cursor() {
  step "Configuring Cursor..."
  local rules_dir="$HOME/.cursor/rules"
  local skills_dir="$HOME/.cursor/skills"
  mkdir -p "$rules_dir" "$skills_dir"

  # Main always-apply rule (alwaysApply: true → stays as a Rule, not a Skill)
  cp "$SKILLS_DIR/cursor.mdc" "$rules_dir/depgraph.mdc"
  info "Rule written to: $rules_dir/depgraph.mdc"

  # Prompt files → Cursor Skills (Agent Skills standard)
  # Location: ~/.cursor/skills/<name>/SKILL.md
  # Frontmatter: name (must match folder name) + description
  # Skills are auto-applied when the agent decides they're relevant, and can
  # be invoked explicitly via /depgraph-<name> in the Agent chat.
  local PROMPTS_DIR="$REPO_DIR/.github/prompts"
  for src in "$PROMPTS_DIR"/depgraph-*.prompt.md; do
    local skill_name; skill_name="$(basename "$src" .prompt.md)"
    local skill_dir="$skills_dir/$skill_name"
    mkdir -p "$skill_dir"
    # Extract description from VS Code frontmatter
    local desc
    desc="$(awk '/^description:/{gsub(/^description: /,""); gsub(/^'"'"'|'"'"'$/,""); print; exit}' "$src")"
    # Body without VS Code YAML frontmatter
    local body
    body="$(awk '/^---/{if(NR==1){skip=1;next}else if(skip){skip=0;next}} !skip' "$src")"
    # Write SKILL.md with Agent Skills frontmatter (name must match folder)
    printf -- '---\nname: %s\ndescription: %s\n---\n%s\n' "$skill_name" "$desc" "$body" > "$skill_dir/SKILL.md"
    info "Skill written to: $skill_dir/SKILL.md"
  done
}

configure_copilot() {
  step "Configuring GitHub Copilot..."
  local dest_dir; dest_dir="$(vscode_prompts_dir)"
  mkdir -p "$dest_dir"

  # Main instructions file
  cp "$SKILLS_DIR/copilot.instructions.md" "$dest_dir/depgraph.instructions.md"
  info "Instructions written to: $dest_dir/depgraph.instructions.md"

  # Prompt files → VS Code .prompt.md (copy as-is, format is already correct)
  local PROMPTS_DIR="$REPO_DIR/.github/prompts"
  for src in "$PROMPTS_DIR"/depgraph-*.prompt.md; do
    local base; base="$(basename "$src")"
    cp "$src" "$dest_dir/$base"
    info "Prompt written to: $dest_dir/$base"
  done
}

configure_windsurf() {
  step "Configuring Windsurf..."
  local dest_dir="$HOME/.codeium/windsurf/memories"
  mkdir -p "$dest_dir"

  # Main instructions file
  cp "$SKILLS_DIR/cli-agent.md" "$dest_dir/depgraph.md"
  info "Instructions written to: $dest_dir/depgraph.md"

  # Prompt files → memories (strip VS Code frontmatter, keep body)
  local PROMPTS_DIR="$REPO_DIR/.github/prompts"
  for src in "$PROMPTS_DIR"/depgraph-*.prompt.md; do
    local base; base="$(basename "$src" .prompt.md).md"
    local dest="$dest_dir/$base"
    awk '/^---/{if(NR==1){skip=1;next}else if(skip){skip=0;next}} !skip' "$src" > "$dest"
    info "Memory written to: $dest"
  done
}

configure_cli_only() {
  step "CLI-only setup..."
  info "No agent config file needed — the depgraph CLI is your interface."
  info "Reference instructions: $SKILLS_DIR/cli-agent.md"
  echo ""
  echo "  Quick start:"
  echo "    depgraph index /path/to/your/repo"
  echo "    depgraph blast-radius /path/to/your/repo src/main.py"
  echo "    depgraph --help"
}

# ── Main ─────────────────────────────────────────────────────────────────────
echo ""
banner "==========================================="
banner "  depgraph — Setup & Agent Configuration"
banner "==========================================="
echo ""
echo "  This script installs the depgraph CLI and configures your AI agent"
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
    warn "You can re-run setup.sh to configure an agent, or copy skills manually from: $SKILLS_DIR"
    ;;
esac

echo ""
banner "==========================================="
info "Setup complete!"
echo ""
echo "  Run 'depgraph --help' to see all available commands."
echo "  Reference docs: $SKILLS_DIR/"
banner "==========================================="
echo ""
