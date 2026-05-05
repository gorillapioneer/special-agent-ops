# Safe Repo Boundaries

Fill this out for your repository. Share it with any human or agent involved in a mission.

The purpose of this file is to make explicit what is safe to delegate, what requires extra care, and what should never be touched by an agent.

---

## Repository overview

**Repo name:** _______________  
**Primary language(s):** _______________  
**Deployment target:** _______________  

---

## Agent-accessible paths (GREEN)

These paths can be worked on in GREEN or AMBER missions without special escalation.

```
# Examples — replace with your actual paths
src/components/
src/pages/
docs/
tests/unit/
public/
```

---

## Restricted paths (AMBER — extra review required)

These paths can be worked on but require careful review of the diff before merge. Flag any change here in the PR description.

```
# Examples
src/api/
src/hooks/
src/utils/
database/migrations/
.github/workflows/
```

---

## Protected paths (RED — human must review every change)

These paths require an explicit human to review and approve every change before merge. Do not delegate to an agent without a specific, scoped mission brief approved at Gate 1 by a second human.

```
# Examples
src/auth/
src/payments/
src/middleware/
config/
infrastructure/
```

---

## Off-limits paths (BLACK — agents must not touch these)

These paths are out of bounds for all agent sessions. Remove them from agent context when possible. Never paste their contents into a prompt.

```
# Examples
.env
.env.*
secrets/
vault/
src/crypto/
src/trading/execution/
```

---

## File types that are always restricted

Regardless of path, these file types require human review before any agent session can include or modify them:

- `.env` and all `.env.*` variants
- `*.pem`, `*.key`, `*.p12` — certificates and private keys
- `credentials.*` — credential files in any format
- `*secret*`, `*token*` in the filename
- Database seed files with production data

---

## Operations that always require human approval

Regardless of risk level, these operations require explicit human confirmation before an agent proceeds:

- Deleting any file
- Dropping or truncating a database table
- Modifying an existing migration
- Changing a public API response structure
- Adding a new third-party service integration
- Modifying rate limiting or access control logic

---

## Notes for this project

<!-- Add anything project-specific that doesn't fit the categories above. -->
<!-- For example: -->
<!-- - The `legacy/` directory is effectively a BLACK zone even though it's not listed above — do not touch it. -->
<!-- - All new API routes must go through the router in src/api/router.ts — do not create ad-hoc routes. -->
