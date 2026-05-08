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
# Canonical source for all agent artifacts:
#   ai-instructions/depgraph/instructions/  — always-apply instructions
#   ai-instructions/depgraph/prompts/       — on-demand skill prompts
#   ai-instructions/depgraph/skills/        — developer reference skills
#
# Supported agents and install destinations:
#   Claude Code    → ~/.claude/CLAUDE.md (instructions, frontmatter stripped)
#                    ~/.claude/commands/depgraph-*.md (slash commands)
#   Cursor         → ~/.cursor/rules/depgraph.mdc (always-apply rule)
#                    ~/.cursor/skills/depgraph-*/SKILL.md (Agent Skills)
#   GitHub Copilot → <VS Code user prompts>/depgraph.instructions.md
#                    <VS Code user prompts>/depgraph-*.prompt.md
#                    .github/prompts/ (workspace sync)
#   Windsurf       → ~/.codeium/windsurf/memories/depgraph*.md
#   Other / CLI    → no config file, just installs the CLI
# =============================================================================

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPGRAPH_DIR="$REPO_DIR/ai-instructions/depgraph"
INSTRUCTIONS_DIR="$DEPGRAPH_DIR/instructions"
PROMPTS_DIR="$DEPGRAPH_DIR/prompts"

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

  # Claude Code expects bare markdown — CLAUDE.md is already in that format
  cp "$INSTRUCTIONS_DIR/CLAUDE.md" "$dest_dir/CLAUDE.md"
  info "Instructions written to: $dest_dir/CLAUDE.md"

  # Prompt files → slash commands (/depgraph-<name>), frontmatter stripped
  for src in "$PROMPTS_DIR"/depgraph-*.prompt.md; do
    local base; base="$(basename "$src" .prompt.md)"
    local dest="$cmd_dir/${base}.md"
    awk '/^---/{if(NR==1){skip=1;next}else if(skip){skip=0;next}} !skip' "$src" > "$dest"
    info "Command written to: $dest"
  done
}

configure_cursor() {
  step "Configuring Cursor..."
  local rules_dir="$HOME/.cursor/rules"
  local skills_dir="$HOME/.cursor/skills"
  mkdir -p "$rules_dir" "$skills_dir"

  # ── depgraph always-apply rule ───────────────────────────────────────────────
  # depgraph.mdc is already in Cursor rule format (description + alwaysApply: true)
  cp "$INSTRUCTIONS_DIR/depgraph.mdc" "$rules_dir/depgraph.mdc"
  info "Rule written to: $rules_dir/depgraph.mdc"

  # ── project Cursor rules (.mdc files only) ───────────────────────────────────
  # Plain .md files in cursor/rules/ are reference docs, not rules — skip them.
  # depgraph*.mdc files are managed by the canonical source above — skip them too.
  for src in "$REPO_DIR/ai-instructions/cursor/rules"/*.mdc; do
    [[ -f "$src" ]] || continue
    local base; base="$(basename "$src")"
    case "$base" in depgraph*.mdc) continue ;; esac
    cp "$src" "$rules_dir/$base"
    info "Rule written to: $rules_dir/$base"
  done

  # ── shared skills → Cursor Agent Skills ─────────────────────────────────────
  # SKILL.md format is Cursor-native: ~/.cursor/skills/<name>/SKILL.md
  for skill_dir in "$REPO_DIR/ai-instructions/copilot/skills"/*/; do
    [[ -d "$skill_dir" ]] || continue
    local skill_name; skill_name="$(basename "$skill_dir")"
    mkdir -p "$skills_dir/$skill_name"
    cp "$skill_dir/SKILL.md" "$skills_dir/$skill_name/SKILL.md"
    info "Skill written to: $skills_dir/$skill_name/SKILL.md"
  done

  # ── depgraph prompt skills (transform VS Code frontmatter → Cursor Skill) ───
  # Location: ~/.cursor/skills/<name>/SKILL.md
  for src in "$PROMPTS_DIR"/depgraph-*.prompt.md; do
    local skill_name; skill_name="$(basename "$src" .prompt.md)"
    local skill_dir="$skills_dir/$skill_name"
    mkdir -p "$skill_dir"
    local skill_desc
    skill_desc="$(awk '/^description:/{gsub(/^description: /,""); gsub(/^'"'"'|'"'"'$/,""); print; exit}' "$src")"
    local skill_body
    skill_body="$(awk '/^---/{if(NR==1){skip=1;next}else if(skip){skip=0;next}} !skip' "$src")"
    printf -- '---\nname: %s\ndescription: %s\n---\n%s\n' "$skill_name" "$skill_desc" "$skill_body" > "$skill_dir/SKILL.md"
    info "Skill written to: $skill_dir/SKILL.md"
  done
}

configure_copilot() {
  step "Configuring GitHub Copilot..."
  local dest_dir; dest_dir="$(vscode_prompts_dir)"
  mkdir -p "$dest_dir"

  # ── depgraph instructions + prompts (from canonical source) ─────────────────
  cp "$INSTRUCTIONS_DIR/depgraph.instructions.md" "$dest_dir/depgraph.instructions.md"
  info "Written: $dest_dir/depgraph.instructions.md"

  for src in "$PROMPTS_DIR"/depgraph-*.prompt.md; do
    local base; base="$(basename "$src")"
    cp "$src" "$dest_dir/$base"
    info "Written: $dest_dir/$base"
  done

  # ── project instructions (.instructions.md) ──────────────────────────────────
  # Covers: dev-quality guardrails, mcp-server-dev, public-api-v2, rswag-rspec,
  #         ruby-rails, etc. — all have applyTo: "**" or scoped glob frontmatter
  for src in "$REPO_DIR/ai-instructions/copilot"/*.instructions.md; do
    [[ -f "$src" ]] || continue
    local base; base="$(basename "$src")"
    [[ "$base" == "depgraph.instructions.md" ]] && continue  # already installed above
    cp "$src" "$dest_dir/$base"
    info "Written: $dest_dir/$base"
  done

  # ── project prompts (.prompt.md) ─────────────────────────────────────────────
  for src in "$REPO_DIR/ai-instructions/copilot"/*.prompt.md; do
    [[ -f "$src" ]] || continue
    local base; base="$(basename "$src")"
    case "$base" in depgraph-*.prompt.md) continue ;; esac  # already installed above
    cp "$src" "$dest_dir/$base"
    info "Written: $dest_dir/$base"
  done

  # ── project agents (.agent.md) ───────────────────────────────────────────────
  # engineer, orchestrator, qa, improvement — VS Code Copilot custom agents
  for src in "$REPO_DIR/ai-instructions/copilot"/*.agent.md; do
    [[ -f "$src" ]] || continue
    local base; base="$(basename "$src")"
    cp "$src" "$dest_dir/$base"
    info "Written: $dest_dir/$base"
  done

  # ── Sync depgraph prompts to .github/prompts/ for workspace-level access ─────
  local github_prompts="$REPO_DIR/.github/prompts"
  mkdir -p "$github_prompts"
  for src in "$PROMPTS_DIR"/depgraph-*.prompt.md; do
    local base; base="$(basename "$src")"
    cp "$src" "$github_prompts/$base"
  done
  info "Workspace prompts synced to: $github_prompts"
}

configure_windsurf() {
  step "Configuring Windsurf..."
  local dest_dir="$HOME/.codeium/windsurf/memories"
  mkdir -p "$dest_dir"

  # Main instructions — cli-agent.md has the full CLI reference Windsurf needs
  cp "$INSTRUCTIONS_DIR/cli-agent.md" "$dest_dir/depgraph.md"
  info "Instructions written to: $dest_dir/depgraph.md"

  # Prompt files → memories (strip VS Code frontmatter, keep body)
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
  info "Reference instructions: $INSTRUCTIONS_DIR/cli-agent.md"
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
    warn "You can re-run setup.sh to configure an agent, or copy skills manually from: $DEPGRAPH_DIR"
    ;;
esac

echo ""
banner "==========================================="
info "Setup complete!"
echo ""
echo "  Run 'depgraph --help' to see all available commands."
echo "  Reference docs: $DEPGRAPH_DIR/"
banner "==========================================="
echo ""
