---
name: agent-system-authoring
description: "Use when: creating, updating, or incrementing any agent customization file — agents, instructions, prompts, skills, or rules. Covers file placement, frontmatter contracts, naming conventions, modularity rules, and the decision matrix for when to create vs. increment."
---

# Skill: Agent System Authoring

## Context

The agent customization system lives in `ai-instructions/` as a **flat directory**. Every file is identified purely by its extension:

```
ai-instructions/
├── <name>.rule.md    ← always-on or path-scoped behavioral guardrails
├── <name>.skill.md   ← on-demand domain knowledge / workflows
├── <name>.agent.md   ← custom agents and subagents
└── <name>.prompt.md  ← reusable, manually-invoked prompts
```

`setup.sh` symlinks files into each tool's global directory at install time, routing by extension:

| Extension | Claude Code | Cursor | Copilot (VS Code) |
|---|---|---|---|
| `*.rule.md` | `~/.claude/rules/<name>.md` | `~/.cursor/rules/<name>.md` | `<prompts>/<name>.instructions.md` |
| `*.skill.md` | `~/.claude/skills/<name>/SKILL.md` | `~/.cursor/skills/<name>/SKILL.md` | `<prompts>/<name>.prompt.md` + `<prompts>/skills/<name>/SKILL.md` |
| `*.agent.md` | `~/.claude/agents/<name>.agent.md` | `~/.cursor/agents/<name>.agent.md` | `<prompts>/<name>.agent.md` |

**Adding a new file requires only dropping it in `ai-instructions/` with the right extension. No changes to `setup.sh` needed.**

### When to use `*.rule.md` vs `*.skill.md`

| Use `*.rule.md` | Use `*.skill.md` |
|---|---|
| Always-on guardrail (cross-language, path-scoped to a file type) | Architecture reference or large domain knowledge |
| Forces behavior — "always check X when touching Y files" | On-demand — agent invokes when the task is in that domain |
| Short enough to not pollute context (< 200 lines) | Too large or too specific to be always-on |
| e.g. `ruby-rails.rule.md`, `development-quality.rule.md` | e.g. `fedora-nexus.skill.md`, `public-api-v2.skill.md` |

---

## Modularity Rules (Non-negotiable)

1. **One concern per file.** A file must have a single, declarable purpose. If you can't write a one-sentence `description:` for it, it's too broad — split it.
2. **Group by domain, not by agent.** A rule about Ruby goes in `ruby-rails.rule.md`, not in `engineer.agent.md`.
3. **Scope beats duplication.** A narrow `applyTo: "spec/**/*.rb"` in a dedicated file is better than adding Rails-specific rules to a global guardrail.
4. **`*.rule.md` is always-on; `*.skill.md` is on-demand.** If the content only matters in specific situations, it's a skill. If it must always be in context, it's a rule.
5. **Agents orchestrate; they don't accumulate knowledge.** Domain rules belong in `*.rule.md` or `*.skill.md`, not inline in agent files.

---

## Decision Matrix: Create vs. Increment vs. New Type

| Situation | Action |
|---|---|
| New anti-pattern in an existing domain (e.g. Rails N+1 variant) | **Increment** the matching `*.rule.md` |
| New anti-pattern in a cross-language/general domain | **Increment** `development-quality.rule.md` under `## Regra de evolução contínua` |
| New domain large enough for its own guardrail file (≥ 3 rules, distinct `applyTo`) | **Create** new `<name>.rule.md` |
| Repeating multi-step workflow that recurs across sessions | **Create** new `<name>.skill.md` |
| New agent persona or specialization | **Create** new `<name>.agent.md` |
| New reusable prompt template | **Create** new `<name>.skill.md` |
| Small tweak to agent behavior or tool list | **Increment** the target `*.agent.md` |
| Pattern already documented — new nuance only | **Increment** the existing file under the same rule heading |

**Default for new anti-patterns:** increment `development-quality.rule.md`. Only deviate when the domain has an existing dedicated file or clearly warrants its own.

---

## File Authoring Contracts

### `*.rule.md`

```markdown
---
description: "One sentence: what this file governs and when it applies."
applyTo: "glob/pattern/**/*.ext"   # omit for always-on (applyTo: "**")
---

## Regra de evolução contínua (obrigatória)
Esta regra deve ser incrementada continuamente. ...

## 1. Rule Title — Short Problem Statement
- **Anti-pattern**: what goes wrong and why.
- **Padrão recomendado**: what to do instead.
- **Exemplo**:
  ```lang
  # ❌ bad
  # ✅ good
  ```
```

**Numbering:** rules are numbered sequentially. Sub-rules use `1.1`, `1.2`. Never renumber existing rules — always append.

**`applyTo` patterns:**
| Scope | Pattern |
|---|---|
| Always-on (all files) | `"**"` |
| Ruby only | `"**/*.rb"` |
| Specs only | `"spec/**/*.rb"` |
| Specific path | `"app/services/public_api/**/*.rb"` |

---

### `*.skill.md`

```markdown
---
name: kebab-case-name
description: "Use when: trigger phrase. Domain: area-of-expertise."
---

# Skill: Human-Readable Title
...
```

**Naming:** `<name>.skill.md` where `<name>` is the kebab-case skill identifier.

---

### `*.agent.md`

```markdown
---
description: "One sentence: what this agent does and when to invoke it."
name: AgentName
tools: [tool1, tool2]
user-invocable: true | false
model: "Model Name (Vendor)"
---

## Hard Rules
Invariant constraints the agent must never violate.

## Workflow
Numbered steps the agent follows on every invocation.

## Output
Exact format (JSON, markdown, etc.) the agent returns.
```

**Agents must not contain domain rules.** If you find yourself adding language-specific conventions to an agent, extract them to a `*.rule.md` or `*.skill.md` and add a `skills:` reference.

---

### `*.prompt.md`

```markdown
---
description: "One sentence: what this prompt does."
mode: 'agent'
tools: [tool1, tool2]
---

# Prompt: Human-Readable Title

## Goal
What the user wants to accomplish.

## Steps
Concrete instructions for the agent to follow.

## Output
Expected result format.
```

---

## Incrementing an Existing File

1. **Locate the right file** — search `ai-instructions/instructions/` by domain keyword.
2. **Find the correct section** — new rules append under `## Regra de evolução contínua`; refinements append under the existing rule heading as a sub-rule (`1.1`, `1.2`).
3. **Follow the existing format** — anti-pattern → recommended pattern → short example. Match the language (PT-BR for guardrail files, EN for skills/agents).
4. **Never renumber** — append only; renumbering breaks cross-references.
5. **Update `description:` frontmatter** if the file's scope expands beyond the original sentence.

---

## Creating a New File

1. Choose the correct type (decision matrix above).
2. Name with `kebab-case.<type>.md` directly in `ai-instructions/`.
3. Use the frontmatter contract for that type (above).
4. For `*.rule.md`: start with `## Regra de evolução contínua` block.
5. **No need to touch `setup.sh`** — it discovers all files by extension glob.

---

## Anti-patterns to Avoid

| Anti-pattern | Correct approach |
|---|---|
| Adding domain rules directly inside an `*.agent.md` | Extract to `*.rule.md` with `applyTo:` |
| Creating one giant `general.rule.md` with mixed domains | One file per domain; narrow `applyTo:` |
| Duplicating a rule that already exists in another file | Reference or extend; never duplicate |
| Creating a skill for a one-time task | Skills are for recurring patterns only |
| Putting agent/rule/skill files inside the repo's app code | Always in `ai-instructions/` |
| Hardcoding absolute user paths inside skills/rules | Use relative references or env vars |

---

## Output Expected from This Skill

When authoring a new or updated customization file, produce:
1. The **full file content** (create) or the **exact diff block** (increment) — no placeholders.
2. The **target path** relative to `ai-instructions/`.
3. A **one-sentence reason** for the change tied to a concrete session observation.
