# PR: Consolidate `ai-instructions` and standardize agent workflows

## Context
This PR reorganizes and consolidates the AI instructions structure to reduce duplication across legacy sources, simplify maintenance, and make setup/execution flows more predictable for `fedora-nexus` CLI/MCP usage.

## Summary
- Consolidates instructions under `ai-instructions/agents`, `ai-instructions/rules`, and `ai-instructions/skills`, with renames/moves to a unified naming scheme (`*.agent.md`, `*.rule.md`, `*.skill.md`).
- Removes old/duplicated artifacts from legacy paths (`ai-instructions/copilot`, `ai-instructions/cursor/rules`, and `skills/`).
- Updates supporting scripts/configs (`setup.sh`, `docker-compose.yml`, `.vscode/mcp.json`, utility scripts) and adds refactor documentation.

## Main changes

### 1) Structural reorganization
- Migrates agent files to `ai-instructions/agents`.
- Migrates rules to `ai-instructions/rules`.
- Migrates/converts skills to `ai-instructions/skills`.
- Cleans up legacy directories/files to avoid multiple sources of truth.

### 2) Documentation and guardrails
- Adds `docs/ai-instructions-refactor.md` with the rationale for the reorganization.
- Adds root `CLAUDE.md` with repository operational guidance.
- Adds/updates rules and guardrails to standardize agent behavior.

### 3) Local execution updates
- Updates `.env.example` and `.gitignore`.
- Updates `setup.sh` and related local-flow scripts.
- Updates `docker-compose.yml` and `.vscode/mcp.json` to reflect new paths.

### 4) Tests
- Updates `tests/test_server_tools.py` for compatibility with the new structure.
- Completed local validation:
  - `./.venv/bin/fedora-nexus --help` ✅
  - `./.venv/bin/python -m pytest tests/test_server_tools.py -q` ✅ (`18 passed`)

## Expected impact
- Lower maintenance cost for prompts/rules/skills.
- Lower risk of divergence across duplicated instruction sources.
- Smoother onboarding and local operation.

## Risks / attention points
- High volume of `rename/delete` operations may make review harder in default diff mode.
- External tools referencing old paths may require updates.

## How to validate locally
```bash
python3.13 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -e ".[dev]"
./.venv/bin/fedora-nexus --help
./.venv/bin/python -m pytest tests/test_server_tools.py -q
```

## Checklist
- [x] Structure consolidated under `ai-instructions/*`
- [x] Legacy artifacts removed
- [x] Scripts/configs updated
- [x] Server tools tests passing
- [x] Refactor documentation included
