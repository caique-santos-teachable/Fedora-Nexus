---
description: Implement tasks from a README spec file using the full Orchestrator workflow. Use when a README file describes Jira tickets with acceptance criteria, request/response contracts, and test coverage requirements.
---

# Jira Task Implementor

Implement all tasks described in the attached README spec file. Follow the Orchestrator workflow: one complete Engineer → QA loop per task. Each task must be committed exactly once.

**If no README or ticket information is provided**, ask the user to attach the README or paste the ticket descriptions before proceeding.

---

## Workflow Rules

1. **One task = one commit.** Each Jira ticket described in the README is implemented and committed independently before moving to the next.
2. **Commit message format**: `- <action>: <simple sentence>` (e.g. `- feat: implement GET /v2/current_user/transactions index and show endpoints`)
3. **Rswag specs are mandatory** for every new documented API endpoint, created in the same session — but in a dedicated final pass after all tasks are committed.
4. **Do not skip QA.** Every Engineer cycle must be followed by a QA cycle. If QA fails, iterate until passing (max 3 cycles), then escalate to the user.

---

## Phase 1 — Context Gathering

Before writing the implementation brief, gather:
- The existing controller, handler, and serializer patterns used by adjacent endpoints (look at `public_api/end_user_api/v2/` or `public_api/admin_api/v2/` as appropriate).
- The relevant fabricators in `spec/fabricators/` for models involved.
- Existing routes in `config/routes.rb` for the namespace.
- The mock Kong controller at `app/controllers/test/end_user_api/mock_kong_v2_controller.rb` if end-user endpoints are involved.

---

## Phase 2 — Task Loop (repeat for each task)

For each ticket in the README:

### Engineer Prompt Format
```
TASK: <concise description from README title>
CONTEXT:
  - Files involved: [list]
  - Acceptance criteria: [from README]
  - Previous QA issues (if any): [list or empty]
```

### QA Prompt Format
```
TASK: <same description>
ENGINEER_REPORT: <full engineer report>
CYCLE: <cycle number>
```

### After QA passes: commit
```bash
git add <files>
git commit -m "- <action>: <simple sentence>"
```

---

## Phase 3 — Rswag Specs

After all tasks are committed, implement rswag specs for all new endpoints:

1. Read `open_api/rswag/end_user_api/v2/digital_downloads_spec.rb` as the reference pattern.
2. Add required schemas to `open_api/rswag/swagger_doc_configurations/oauth_api_v2.rb`.
3. Create spec files under `open_api/rswag/end_user_api/v2/<resource>_spec.rb`.
4. Use `:earnings_calculated_transaction` fabricator (not `:transaction`) when transactions are involved, to avoid the `before_create ||=` callback issue.
5. Run specs: `bundle exec rspec open_api/rswag/end_user_api/v2/ --format documentation`
6. Run swaggerize SCOPED to end_user_api only: `bundle exec rake rswag:specs:swaggerize PATTERN='open_api/rswag/end_user_api/v2/**/*_spec.rb'`
7. Verify the generated yaml contains ALL end-user paths (not just the new ones).
8. Commit: `- docs: add rswag specs and regenerate end-user API v2 OpenAPI schema`

---

## Phase 4 — Improvement

After all tasks and rswag specs are complete, run the Improvement agent with the full session report.

---

## Key Guardrails (apply throughout)

- **Multi-tenant**: every `find_by`/`exists?`/`where` in end-user handlers must include `school_id:` or be scoped via `school.relation`. See `public-api-v2.instructions.md` §13.8.
- **`before_create ||=` traps**: after fabricating models with callbacks that override nil fields (e.g. `Transaction#requires_earnings_calc`), use `update_column` or a named fabricator (`:earnings_calculated_transaction`). See `ruby-rails.instructions.md` §6.
- **PostgreSQL `where.not(col: 0)` excludes NULLs**: set explicit non-nil values in test data. See `ruby-rails.instructions.md` §7.
- **Rswag 422/403 responses**: document `response 422` for validation endpoints and `response 403` for endpoints with scope guards. See `rswag-rspec.instructions.md` §8, §9.
- **Swaggerize always scoped**: never run `rake rswag:specs:swaggerize` without `PATTERN=`. See `rswag-rspec.instructions.md` §7.
- **Rubocop**: run after every file change. See `ruby-rails.instructions.md` §5.
