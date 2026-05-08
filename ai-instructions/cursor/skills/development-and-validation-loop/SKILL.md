---
name: development-and-validation-loop
description: Enforces the implementation-and-validation loop for work: add/adjust tests with 100% coverage target, build deterministic seed from code understanding (depgraph plus traditional search), execute seed, run curls against the developers base URL, compare results against route and seed expectations, and generate a standardized local Markdown validation report. Use for any Public API V2 implementation task.
priority: 1
alwaysApply: true
---

# Development and Validation Loop (Public API V2)

## Purpose

Use this skill for any implementation.

This skill is mandatory for Public API V2 implementation workflows.

## Mandatory outcome

Only conclude work after all items below are complete:

1. Implementation is done.
2. Unit/request tests were added or updated with a **100% coverage target for changed Public API V2 files**.
3. Deterministic seed is created/updated for runtime validation scenarios.
4. Seed is executed successfully.
5. Curls are executed against the real base URL.
6. Curl outputs are compared against expected behavior derived from routes + seed + API contract.
7. If Jira tasks are provided, Jira description/acceptance criteria/contracts are explicitly validated against curl outcomes.
8. A local standardized Markdown report is written.

## Base URL and execution defaults

- Default base URL: `http://developers.worksonmy.computer:3000/`
- If user provides another URL, prefer user-provided value.
- For Ruby/Rails/Rake commands, prefix with `DISABLE_SPRING=1`.
- For this Fedora setup, prefer `dev --non-interactive exec SERVICE -- COMMAND`.
- Default school for seeds: `advanced school`

## Required workflow

### 1) Understand requirements deeply before seeding

Always combine:

- Depgraph exploration (through mcp or cli if mcp not available) for dependency/flow discovery.
- Traditional search (`rg`, direct file reads) for exact behavior and edge cases.

If api/service contract is provided, minimum evidence to collect:

- Target routes and controller actions.
- Handler validations and filters.
- Serializer fields and conditional logic.
- Ownership/scope/authorization requirements.
- Error contracts (`403`, `404`, `422`, etc.).

### 2) Implement tests with strict coverage target

- Add/update tests for positive, negative, edge, and authorization cases.
- Coverage goal: 100% on changed Public API V2 files.
- If strict 100% is technically blocked, document:
  - exact uncovered lines,
  - reason,
  - mitigation plan.

### 3) Build deterministic seed for validation

Seed must:

- Be idempotent or deterministic across reruns.
- Include all scenarios required by tests and route contracts:
  - success paths,
  - filter/pagination cases,
  - ownership isolation,
  - scope mismatch/forbidden,
  - not found,
  - validation error cases.
- Avoid flaky time dependencies (freeze explicit timestamps where possible).
- Print or persist key IDs used by curl validation.
- Persisted locally under ./tmp/seeds/<ticket-id>/<seed-name>.rb

### 4) Execute seed

- Run seed and verify success.
- If seed fails, fix root cause and rerun until stable.

### 5) Execute curl matrix on real routes

Run direct curl checks for each required scenario using route-accurate paths and headers.

At minimum validate:

- List endpoint success.
- Show endpoint success.
- Relevant query filters.
- Pagination metadata.
- Forbidden on missing/wrong scope.
- Not found for non-owned and non-existent resources.
- Validation error cases (when applicable).

### 6) Compare actual vs expected

For each curl case, assert:

- HTTP status matches expected.
- Key response fields/shape match expected.
- Dataset membership/exclusion matches seed setup.

Do not declare success if any mismatch remains.

### 7) Jira contract validation (when Jira tasks are provided)

If the user provides a Jira key (parent or child), this step is mandatory.

Required actions:

1. Fetch parent issue and all child issues in scope.
2. Extract and normalize:
   - endpoint contract details,
   - acceptance criteria,
   - documented error codes,
   - response shape requirements.
3. Build a traceability mapping:
   - Jira requirement -> curl case(s) + observed response.
4. Mark each requirement as:
   - PASS,
   - FAIL,
   - NOT VALIDATED (with explicit reason).

Important:

- Do not infer PASS without runtime evidence.
- If a Jira contract conflicts with implementation behavior (for example `400` vs `422`), mark as FAIL (or contract mismatch) and document clearly.

### 8) Generate local Markdown report

Write report using the standardized path and naming:

- Directory: `tmp/agents-reports/<ticket-id>/`
- File name: incremental and descriptive, for example:
  - `01-validation-report.md`
  - `02-validation-report.md`
  - `03-final-validation-report.md`

Rules:

- `<ticket-id>` must be normalized from the main task key (for example `IW-3133` -> `iw-3133`).
- If no ticket is provided, use `ad-hoc`.
- Never overwrite a previous report in the same ticket folder; always create the next incremental file.

Use this exact structure:

```markdown
# Public API V2 Validation Report

## Context
- Task:
- Base URL:
- Seed file:
- Execution timestamp:

## Coverage
- Changed files:
- Coverage target:
- Coverage result:
- Gaps/justification (if any):

## Seed Summary
- School/user context:
- Created/reused entities:
- Determinism notes:

## Curl Validation Matrix
| Case | Endpoint | Expected | Actual | Status |
|------|----------|----------|--------|--------|
| ...  | ...      | ...      | ...    | PASS/FAIL |

## Jira Requirements Traceability (when Jira provided)
- Parent issue:
- Child issues in scope:

| Jira Issue | Requirement/Contract | Evidence (curl case) | Observed | Status |
|------------|----------------------|----------------------|----------|--------|
| ...        | ...                  | ...                  | ...      | PASS/FAIL/NOT VALIDATED |

## Failures and Fixes
- Failure:
- Root cause:
- Fix applied:

## Final Verdict
- PASS/FAIL
- Notes:
```

## Robustness improvements to always apply

- Prefer deterministic cleanup of old seed artifacts for the tested scope.
- Avoid broad destructive cleanup.
- Verify that runtime data used by curl corresponds to latest seed run.
- Re-run critical curls after any fix to seed/implementation.
- Keep report aligned to the final successful run only.
- If Jira is provided, never finalize without the Jira traceability section populated.
