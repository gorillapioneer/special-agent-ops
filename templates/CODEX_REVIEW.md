# Codex Review Template

Use this when reviewing output from Codex, GitHub Copilot, or any completion-based AI code tool. These tools generate code in context windows and can produce plausible-looking code that misses edge cases, ignores existing patterns, or makes incorrect assumptions.

---

## Metadata

**Tool used:** _______________  
**Date of review:** _______________  
**Reviewer:** _______________  
**Task described in:** _______________  

---

## What the tool produced

Brief description of what Codex / the completion tool generated:

```
<!-- paste the generated code summary or describe it here -->
```

---

## Review checklist

### Correctness

- [ ] The generated code does what the prompt asked for
- [ ] It handles the intended inputs correctly
- [ ] It handles edge cases (null, empty, out-of-range values)
- [ ] It does not silently discard errors
- [ ] Return types and function signatures are correct

### Assumptions

- [ ] The code does not assume data shapes that may not be correct
- [ ] The code does not assume environment variables exist without validation
- [ ] The code does not assume specific database state
- [ ] External API calls have appropriate error handling

### Security

- [ ] No hardcoded secrets, credentials, or tokens
- [ ] Input is validated at function entry points
- [ ] Output is not rendered without sanitisation (for web output)
- [ ] No SQL string concatenation or command injection risk
- [ ] Auth checks are not accidentally bypassed

### Style and integration

- [ ] Code follows existing conventions in the codebase (naming, formatting, error patterns)
- [ ] Imports reference actual existing modules — not hallucinated paths
- [ ] Function and variable names are consistent with the rest of the codebase
- [ ] The code does not duplicate existing utility functions

### Tests

- [ ] The change is covered by new or existing tests
- [ ] Tests test the behaviour, not just the implementation
- [ ] Edge cases identified in the review have test coverage

---

## Findings

| Issue | Severity | Location | Resolution |
|---|---|---|---|
| | | | |

---

## Decision

- [ ] Accept as-is
- [ ] Accept with minor edits (listed above)
- [ ] Return for rework — specific issues listed above
- [ ] Reject — fundamental problem with the approach

**Reason:**

---

## Notes

<!-- Anything else worth capturing about the generated code quality, surprising patterns, or lessons for future prompts. -->
