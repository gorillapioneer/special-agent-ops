# Agent Rules

Copy this file into your project repo and adapt it to your codebase. Include it in any agent session prompt for that project.

---

## For any AI coding agent working in this repository

These rules apply to every agent session in this project. Read them before starting any task.

### Scope

1. You work on the task in your Mission Brief only. Nothing else.
2. If you notice something broken outside your scope, **stop and note it** — do not fix it.
3. If you are unsure whether a file is in scope, ask before touching it.

### Branches

4. Never commit to `main` or `master` directly.
5. Always work on a feature branch named in the Mission Brief.
6. Do not create additional branches without human confirmation.

### Secrets and credentials

7. Never write API keys, passwords, tokens, or credentials anywhere in the code.
8. If you need to reference a secret value in your implementation, use an environment variable and document the variable name only.
9. If you find a secret already in the code, flag it immediately and do not proceed until a human has addressed it.

### Deletions

10. Do not delete files unless the Mission Brief explicitly says to delete a specific, named file.
11. Do not drop database tables, truncate data, or remove migrations without explicit instruction.
12. If your implementation requires a significant deletion, stop and ask for confirmation.

### Dependencies

13. Do not add new package dependencies without noting them explicitly in your output for human review.
14. Do not upgrade existing dependencies beyond what the Mission Brief specifies.

### Infrastructure and configuration

15. Do not modify deployment scripts, CI/CD configuration, server config, or environment configuration unless the Mission Brief explicitly covers this.
16. Do not modify `.env.example` or any environment file without flagging it.

### Stopping conditions

Stop immediately and ask for human input if you encounter:
- Authentication or session handling code outside your scope
- Payment or financial transaction code
- Code that directly reads, writes, or deletes user data at scale
- Anything that looks like it might be a secret or credential
- A situation where completing the task seems to require something the brief says is out of scope

### What "done" looks like

When you believe the task is complete:
- Summarise what you changed and why
- List any files you created, modified, or deleted
- Note any out-of-scope issues you encountered and did not address
- Confirm that you have not touched any out-of-scope paths

---

## Project-specific rules

<!-- Add project-specific rules below this line. -->
<!-- Examples: -->
<!-- - The `legacy/` directory is read-only. Never modify files there. -->
<!-- - All new functions require a unit test in the corresponding `__tests__/` directory. -->
<!-- - Do not change the public API surface without updating CHANGELOG.md. -->
