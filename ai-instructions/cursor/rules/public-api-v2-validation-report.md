# Public API V2 Validation Report

## Context
- Task: IW-2885 (parent) + subtasks IW-3163, IW-3164, IW-3165, IW-3173
- Base URL: http://developers.worksonmy.computer:3000/
- Seed file: ad-hoc `rails runner` seed attempt via `dev --non-interactive exec fedora -- ...` (blocked by environment dependencies)
- Execution timestamp: 2026-05-07T14:38:00-03:00

## Coverage
- Changed files:
  - `app/controllers/public_api/end_user_api/v2/products/courses/curriculums_controller.rb`
  - `app/services/public_api/end_user_api/v2/courses/curriculum_handler.rb`
  - `app/services/public_api/end_user_api/v2/courses/section_handler.rb`
  - `config/routes.rb`
  - `app/controllers/test/end_user_api/mock_kong_v2_controller.rb`
  - `spec/requests/public_api/end_user_api/v2/products/courses/curriculums_controller_spec.rb`
  - `spec/requests/public_api/end_user_api/v2/products/courses/sections_controller_spec.rb`
  - `spec/requests/public_api/end_user_api/v2/products/courses/sections/lectures_controller_spec.rb`
- Coverage target: 100% for changed Public API V2 files
- Coverage result: request-spec suite covering changed endpoints passed (`79 examples, 0 failures`)
- Gaps/justification (if any): line-level coverage metrics were not generated in this run; behavioral coverage validated by request specs.

## Seed Summary
- School/user context: attempted deterministic school/user/course setup for end-user v2 mock flow (`school_id=1`, `user_id=1123`)
- Created/reused entities: seed execution was blocked by external plan-permission dependency (`school-plan-service`) during object fabrication in dev runtime.
- Determinism notes: seed script is idempotent by `find_or_create` pattern, but runtime completion was not possible in current local environment.

## Curl Validation Matrix
| Case | Endpoint | Expected | Actual | Status |
|------|----------|----------|--------|--------|
| Curriculum success (enrolled user) | `GET /v2/current_user/products/courses/1/curriculum` | `200` with sections+lectures | `curl: (7) Couldn't connect to server` | FAIL |
| Curriculum success (school subdomain fallback) | `GET /v2/current_user/products/courses/1/curriculum` on `business-school.worksonmy.computer` | `200` | `curl: (7) Couldn't connect to server` | FAIL |

## Jira Requirements Traceability (when Jira provided)
- Parent issue: IW-2885
- Child issues in scope: IW-3163, IW-3164, IW-3165, IW-3173

| Jira Issue | Requirement/Contract | Evidence (curl case) | Observed | Status |
|------------|----------------------|----------------------|----------|--------|
| IW-2885 | Add end-user curriculum endpoint (`/products/courses/{course_id}/curriculum`) with student-viewable sections | Request specs (`curriculums_controller_spec`) | Endpoint implemented + specs passing | PASS |
| IW-3163 | Sections endpoint behavior for enrolled users, paging and not-found guards | Request specs (`sections_controller_spec`) | Updated contract behavior validated in specs | PASS |
| IW-3164 | Section lectures list endpoint guards and payload | Request specs (`lectures_controller_spec`, list contexts) | Passing | PASS |
| IW-3173 | Lecture show endpoint includes inactive enrollment `404` scenario | Request specs (`lectures_controller_spec`, show contexts) | Added + passing | PASS |
| IW-3165 | Lecture attachments endpoint behavior and shape | Request specs (`attachments_controller_spec`) | Passing | PASS |
| IW-2885 runtime | Runtime curl proof on local dev base URL | Curl matrix above | Base URL unreachable from current environment | NOT VALIDATED |

## Failures and Fixes
- Failure: Parent contract endpoint missing in end-user v2 (`/products/courses/{course_id}/curriculum`).
- Root cause: Branch had only granular endpoints (`sections`, `lectures`, `attachments`) and no dedicated curriculum controller/route.
- Fix applied: Added curriculum controller + handler + routes + mock-kong dispatch + request specs; aligned section handler contract and lecture show inactive-enrollment coverage.

## Final Verdict
- FAIL
- Notes: Code and request specs are passing; runtime curl validation against required base URL was blocked by local connectivity/environment dependency issues and must be re-run in a reachable dev runtime.
