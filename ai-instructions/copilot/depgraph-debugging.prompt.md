---
mode: 'agent'
description: 'Use when the user is debugging a bug, tracing an error, or asking why something fails. Examples: "Why is X failing?", "Where does this error come from?", "Trace this bug", "Who calls this method?"'
---

# Debugging with depgraph

## When to use

- "Why is this function failing?"
- "Trace where this error comes from"
- "Who calls this method?"
- "This endpoint returns 500"
- Investigating bugs or unexpected behavior

## Workflow

```
1. search({ root_path, query: "<error text or suspect function>" })   → Find related symbols
2. query_graph — find callers of the suspect function
3. get_dependencies({ file_path: "<suspect file>", depth: 2 })        → See what it depends on
4. blast_radius({ changed_files: ["<recently changed file>"] })       → Map recent change impact
5. Read source files to confirm root cause
```

## Checklist

```
- [ ] search for error message text, function name, or related keyword
- [ ] Identify the suspect symbol from results
- [ ] query_graph to find all callers of the suspect
- [ ] get_dependencies to trace what the suspect depends on
- [ ] blast_radius if you suspect a recent change caused the regression
- [ ] Read source files to confirm root cause
```

## Debugging patterns

| Symptom | depgraph approach |
|---------|------------------|
| Error in function | `search` for function name → `query_graph` for callers |
| Wrong return value | `query_graph` for callees → trace data flow |
| Import error | `get_dependencies` to see the import chain |
| Recent regression | `blast_radius` on recently changed files |
| Undefined symbol | `search` to find where it's defined |

## Tools in practice

**search** — find the suspect by name or error keyword:
```
search({ root_path: "/repos/myapp", query: "payment validation error" })
→ Method: validate_payment (src/payments/validators.py:22)
→ Function: handle_payment_error (src/payments/errors.py:8)
```

**query_graph** — find callers (who triggers this code):
```cypher
MATCH (caller)-[r:CodeRelation {type: 'CALLS'}]->(f:Function {name: "validate_payment"})
RETURN caller.name, caller.file_path, caller.start_line
```

**query_graph** — find callees (what this code depends on):
```cypher
MATCH (f:Function {name: "validate_payment"})-[r:CodeRelation {type: 'CALLS'}]->(callee)
RETURN callee.name, callee.file_path
```

**get_dependencies** — trace import chain:
```
get_dependencies({ root_path: "/repos/myapp", file_path: "src/payments/validators.py", depth: 3 })
```

## Example: "Payment endpoint returns 500 intermittently"

```
1. search({ query: "payment validation" })
   → validate_payment (src/payments/validators.py:22)
   → PaymentValidator class (src/payments/validators.py:5)

2. query_graph — callers of validate_payment
   → process_checkout (src/checkout/handler.py:45)
   → webhook_handler (src/webhooks/handler.py:88)

3. query_graph — callees of validate_payment
   → fetch_exchange_rates (src/external/rates.py:12)  ← external API call!
   → check_card_expiry (src/payments/card.py:31)

4. Root cause: fetch_exchange_rates makes an external call that fails intermittently.
   Check error handling around that call.
```
