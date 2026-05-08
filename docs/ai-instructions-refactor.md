# AI Instructions Refactor Report

## What changed

### New canonical source structure

`ai-instructions/fedora-nexus/` now holds all fedora-nexus agent artifacts, organized by type:

```
ai-instructions/fedora-nexus/
  instructions/
    CLAUDE.md                  ← Claude Code format (bare markdown)
    fedora-nexus.mdc               ← Cursor format (description + alwaysApply: true)
    fedora-nexus.instructions.md   ← VS Code/Copilot format (applyTo: "**")
    cli-agent.md               ← Windsurf/CLI format (detailed CLI reference)
  prompts/
    fedora-nexus-guide.prompt.md
    fedora-nexus-exploring.prompt.md
    fedora-nexus-debugging.prompt.md
    fedora-nexus-impact.prompt.md
    fedora-nexus-pr-review.prompt.md
    fedora-nexus-refactoring.prompt.md
  skills/
    mcp-server-python.md       (developer reference — not installed by setup.sh)
```

Each file in `instructions/` is already in the correct format for its target agent. `setup.sh` copies them directly — no format transformation needed for instruction files. Only the prompt files in `prompts/` require per-agent transformation (frontmatter rewriting or stripping).

### `setup.sh` updated

- Replaced `SKILLS_DIR` variable with `FEDORA_NEXUS_DIR`, `INSTRUCTIONS_DIR`, and `PROMPTS_DIR` pointing to the new canonical paths.
- All four `configure_*` functions rewritten to read from `ai-instructions/`.
- `configure_copilot()` expanded to install all project files from `ai-instructions/copilot/` (instructions, prompts, agents).
- `configure_cursor()` expanded to install project rules from `ai-instructions/cursor/rules/` and skills from `ai-instructions/copilot/skills/`.
- Script header updated to document the new source paths and install destinations.

### Pending cleanup

The following files are stale copies, filtered out by `setup.sh` but not yet deleted:

- `skills/` — 5 files moved to `ai-instructions/fedora-nexus/`
- `ai-instructions/copilot/fedora-nexus*.prompt.md` — 12 outdated copies (flat + nested subfolder), missing CLI fallback section
- `ai-instructions/cursor/rules/fedora-nexus*.mdc` — 7 stale Cursor-format files

---

## What gets installed per agent

### Claude Code

| Destination | Source | How |
|-------------|--------|-----|
| `~/.claude/CLAUDE.md` | `ai-instructions/fedora-nexus/instructions/CLAUDE.md` | Direct copy |
| `~/.claude/commands/fedora-nexus-*.md` (×6) | `ai-instructions/fedora-nexus/prompts/fedora-nexus-*.prompt.md` | VS Code frontmatter stripped |

### Cursor

| Destination | Source | How |
|-------------|--------|-----|
| `~/.cursor/rules/fedora-nexus.mdc` | `ai-instructions/fedora-nexus/instructions/fedora-nexus.mdc` | Direct copy |
| `~/.cursor/rules/global-development-quality.mdc` | `ai-instructions/cursor/rules/` | Direct copy |
| `~/.cursor/rules/public-api-v2-skill-enforcement.mdc` | `ai-instructions/cursor/rules/` | Direct copy |
| `~/.cursor/skills/fedora-nexus-mcp/SKILL.md` | `ai-instructions/copilot/skills/fedora-nexus-mcp/` | Direct copy |
| `~/.cursor/skills/tree-sitter-grammar-probing/SKILL.md` | `ai-instructions/copilot/skills/tree-sitter-grammar-probing/` | Direct copy |
| `~/.cursor/skills/fedora-nexus-*/SKILL.md` (×6) | `ai-instructions/fedora-nexus/prompts/fedora-nexus-*.prompt.md` | Frontmatter rewritten to Cursor Skill format |

### GitHub Copilot

Global user prompts directory on macOS: `~/Library/Application Support/Code/User/prompts/`

| File installed | Source | How |
|----------------|--------|-----|
| `fedora-nexus.instructions.md` | `ai-instructions/fedora-nexus/instructions/fedora-nexus.instructions.md` | Direct copy |
| `fedora-nexus-*.prompt.md` (×6) | `ai-instructions/fedora-nexus/prompts/` | Direct copy |
| `development-quality-guardrails.instructions.md` | `ai-instructions/copilot/` | Direct copy |
| `mcp-server-development.instructions.md` | `ai-instructions/copilot/` | Direct copy |
| `public-api-v2.instructions.md` | `ai-instructions/copilot/` | Direct copy |
| `rswag-rspec.instructions.md` | `ai-instructions/copilot/` | Direct copy |
| `ruby-rails.instructions.md` | `ai-instructions/copilot/` | Direct copy |
| `jira-task.prompt.md` | `ai-instructions/copilot/` | Direct copy |
| `engineer.agent.md` | `ai-instructions/copilot/` | Direct copy |
| `orchestrator.agent.md` | `ai-instructions/copilot/` | Direct copy |
| `qa.agent.md` | `ai-instructions/copilot/` | Direct copy |
| `improvement.agent.md` | `ai-instructions/copilot/` | Direct copy |
| `.github/prompts/fedora-nexus-*.prompt.md` (×6) | `ai-instructions/fedora-nexus/prompts/` | Synced for workspace-level Copilot access |

### Windsurf

| Destination | Source | How |
|-------------|--------|-----|
| `~/.codeium/windsurf/memories/fedora-nexus.md` | `ai-instructions/fedora-nexus/instructions/cli-agent.md` | Direct copy |
| `~/.codeium/windsurf/memories/fedora-nexus-*.md` (×6) | `ai-instructions/fedora-nexus/prompts/fedora-nexus-*.prompt.md` | VS Code frontmatter stripped |
