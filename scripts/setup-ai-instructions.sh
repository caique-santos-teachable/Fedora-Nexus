#!/usr/bin/env bash
# =============================================================================
# fedora-nexus — AI Instructions Setup
# =============================================================================
# Creates symlinks from ai-instructions/ to the right locations for each
# AI agent (Claude Code, Cursor, GitHub Copilot, Windsurf).
#
# Usage:
#   bash setup-ai-instructions.sh          # interactive: prompts for agent
#   bash setup-ai-instructions.sh --all    # link for all agents
#   bash setup-ai-instructions.sh --claude
#   bash setup-ai-instructions.sh --cursor
#   bash setup-ai-instructions.sh --copilot
#   bash setup-ai-instructions.sh --windsurf
#
# Routing by file extension:
#   *.rule.md   → Claude: ~/.claude/rules/   | Cursor: ~/.cursor/rules/   | Copilot: *.instructions.md
#   *.skill.md  → Claude: ~/.claude/skills/  | Cursor: ~/.cursor/skills/  | Copilot: *.prompt.md + skills/<name>/SKILL.md
#   *.agent.md  → Claude: ~/.claude/agents/  | Cursor: ~/.cursor/agents/  | Copilot: *.agent.md
#   Windsurf: all → ~/.codeium/windsurf/memories/<name>.md
# =============================================================================

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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

# ── VS Code user prompts directory ───────────────────────────────────────────
vscode_prompts_dir() {
  case "$(uname -s 2>/dev/null)" in
    Darwin)               echo "$HOME/Library/Application Support/Code/User/prompts" ;;
    Linux)                echo "$HOME/.config/Code/User/prompts" ;;
    CYGWIN*|MINGW*|MSYS*) echo "${APPDATA:-$HOME/AppData/Roaming}/Code/User/prompts" ;;
    *)                    echo "$HOME/.config/Code/User/prompts" ;;
  esac
}

# ── per-agent configure functions ─────────────────────────────────────────────
configure_claude() {
  step "Configuring Claude Code..."
  local rules_dir="$HOME/.claude/rules"
  local agents_dir="$HOME/.claude/agents"
  local skills_dir="$HOME/.claude/skills"
  mkdir -p "$rules_dir" "$agents_dir" "$skills_dir"

  for src in "$RULES_DIR"/*.rule.md; do
    [[ -f "$src" ]] || continue
    local name; name="$(basename "$src" .rule.md)"
    ln -sf "$src" "$rules_dir/$name.md"
    info "Rule  → $rules_dir/$name.md"
  done

  for src in "$SKILLS_DIR"/*.skill.md; do
    [[ -f "$src" ]] || continue
    local name; name="$(basename "$src" .skill.md)"
    mkdir -p "$skills_dir/$name"
    ln -sf "$src" "$skills_dir/$name/SKILL.md"
    info "Skill → $skills_dir/$name/SKILL.md"
  done

  for src in "$AGENTS_DIR"/*.agent.md; do
    [[ -f "$src" ]] || continue
    ln -sf "$src" "$agents_dir/$(basename "$src")"
    info "Agent → $agents_dir/$(basename "$src")"
  done
}

configure_cursor() {
  step "Configuring Cursor..."
  local rules_dir="$HOME/.cursor/rules"
  local agents_dir="$HOME/.cursor/agents"
  local skills_dir="$HOME/.cursor/skills"
  mkdir -p "$rules_dir" "$agents_dir" "$skills_dir"

  for src in "$RULES_DIR"/*.rule.md; do
    [[ -f "$src" ]] || continue
    local name; name="$(basename "$src" .rule.md)"
    ln -sf "$src" "$rules_dir/$name.md"
    info "Rule  → $rules_dir/$name.md"
  done

  for src in "$SKILLS_DIR"/*.skill.md; do
    [[ -f "$src" ]] || continue
    local name; name="$(basename "$src" .skill.md)"
    mkdir -p "$skills_dir/$name"
    ln -sf "$src" "$skills_dir/$name/SKILL.md"
    info "Skill → $skills_dir/$name/SKILL.md"
  done

  for src in "$AGENTS_DIR"/*.agent.md; do
    [[ -f "$src" ]] || continue
    ln -sf "$src" "$agents_dir/$(basename "$src")"
    info "Agent → $agents_dir/$(basename "$src")"
  done
}

configure_copilot() {
  step "Configuring GitHub Copilot..."
  local dest_dir; dest_dir="$(vscode_prompts_dir)"
  local skills_dest="$dest_dir/skills"
  mkdir -p "$dest_dir" "$skills_dest"

  for src in "$RULES_DIR"/*.rule.md; do
    [[ -f "$src" ]] || continue
    local name; name="$(basename "$src" .rule.md)"
    ln -sf "$src" "$dest_dir/$name.instructions.md"
    info "Rule  → $dest_dir/$name.instructions.md"
  done

  for src in "$SKILLS_DIR"/*.skill.md; do
    [[ -f "$src" ]] || continue
    local name; name="$(basename "$src" .skill.md)"
    ln -sf "$src" "$dest_dir/$name.prompt.md"
    info "Skill → $dest_dir/$name.prompt.md"
    mkdir -p "$skills_dest/$name"
    ln -sf "$src" "$skills_dest/$name/SKILL.md"
    info "Skill → $skills_dest/$name/SKILL.md"
  done

  for src in "$AGENTS_DIR"/*.agent.md; do
    [[ -f "$src" ]] || continue
    ln -sf "$src" "$dest_dir/$(basename "$src")"
    info "Agent → $dest_dir/$(basename "$src")"
  done
}

configure_windsurf() {
  step "Configuring Windsurf..."
  local dest_dir="$HOME/.codeium/windsurf/memories"
  mkdir -p "$dest_dir"

  for src in "$RULES_DIR"/*.rule.md "$SKILLS_DIR"/*.skill.md; do
    [[ -f "$src" ]] || continue
    local name
    if [[ "$src" == *.skill.md ]]; then
      name="$(basename "$src" .skill.md)"
    else
      name="$(basename "$src" .rule.md)"
    fi
    ln -sf "$src" "$dest_dir/$name.md"
    info "Memory → $dest_dir/$name.md"
  done
}

# ── Main ─────────────────────────────────────────────────────────────────────
echo ""
banner "==========================================="
banner "  fedora-nexus — AI Instructions Setup"
banner "==========================================="
echo ""

# Parse flags
if [[ $# -gt 0 ]]; then
  for arg in "$@"; do
    case "$arg" in
      --claude)   configure_claude   ;;
      --cursor)   configure_cursor   ;;
      --copilot)  configure_copilot  ;;
      --windsurf) configure_windsurf ;;
      --all)
        configure_claude
        echo ""
        configure_cursor
        echo ""
        configure_copilot
        echo ""
        configure_windsurf
        ;;
      *)
        warn "Unknown flag: $arg"
        echo "  Usage: $0 [--claude] [--cursor] [--copilot] [--windsurf] [--all]"
        exit 1
        ;;
    esac
  done
else
  # Interactive
  echo "  Which AI agent do you use?"
  echo ""
  echo "    1)  Claude Code"
  echo "    2)  Cursor"
  echo "    3)  GitHub Copilot"
  echo "    4)  Windsurf"
  echo "    5)  All of the above"
  echo ""
  read -r -p "  Enter choice [1-5]: " CHOICE
  echo ""

  case "$CHOICE" in
    1) configure_claude   ;;
    2) configure_cursor   ;;
    3) configure_copilot  ;;
    4) configure_windsurf ;;
    5)
      configure_claude
      echo ""
      configure_cursor
      echo ""
      configure_copilot
      echo ""
      configure_windsurf
      ;;
    *)
      warn "Unknown choice '${CHOICE}'."
      exit 1
      ;;
  esac
fi

echo ""
info "AI instructions configured. Open a new chat in your agent to activate."
echo ""
