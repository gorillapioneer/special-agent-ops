# Agent Roster

Each entry describes a role, its job, what it should and should not do, and which tools are commonly used to fill it.

A "role" is a focused job for one AI session. You do not need a different tool for each role — you can use Claude for all of them. What matters is that each session has a clearly scoped purpose.

---

## Planner Agent

**Job:** Takes a task idea and produces a structured mission brief. Identifies sub-tasks, flags risks, estimates scope, and recommends a risk level.

**Does:**
- Break down a vague request into specific, verifiable sub-tasks
- Flag which parts of the codebase will be touched
- Identify dependencies and potential conflicts
- Recommend which sub-tasks require RED or BLACK treatment
- Produce a draft `MISSION_BRIEF.md`

**Does not:**
- Write code
- Push to any branch
- Make assumptions about secret management

**Typical tool:** Claude web or mobile (keeps it in planning mode, not execution mode)

**Prompt:** See [`prompts/planner-agent.md`](../prompts/planner-agent.md)

---

## Builder Agent

**Job:** Implements one scoped sub-task on a feature branch. Follows the mission brief exactly. Stops and flags anything outside scope.

**Does:**
- Write code for the assigned sub-task
- Create or modify files within the allowed paths
- Write or update tests for the changed code
- Commit to a feature branch (never `main`)
- Flag blockers and out-of-scope discoveries for human review

**Does not:**
- Modify files outside the agreed boundaries
- Touch secrets, auth, or payment code unless explicitly in scope
- Push to `main` or merge anything
- Make architectural decisions beyond the sub-task

**Typical tools:** Claude Code (local), GitHub Copilot / Codex, v0 (for UI tasks)

**Prompt:** See [`prompts/builder-agent.md`](../prompts/builder-agent.md)

---

## Reviewer Agent

**Job:** Reviews the pull request diff and identifies problems before human review.

**Does:**
- Read the diff and understand what changed
- Flag logic errors, edge cases, and regressions
- Check that the change matches the stated mission brief
- Note anything that looks like it goes beyond scope
- Identify test gaps

**Does not:**
- Approve or merge the PR
- Run code
- Access production systems

**Typical tools:** Claude web, GitHub Copilot review mode

**Prompt:** See [`prompts/codex-reviewer-agent.md`](../prompts/codex-reviewer-agent.md)

---

## Safety Gate Agent

**Job:** Checks the diff or staged changes for risky patterns before the PR is opened.

**Does:**
- Flag changes to `.env` files, secret-adjacent paths, and credential handling
- Flag deletion-heavy changes (large file removals, DROP TABLE, rm -rf patterns)
- Flag changes to auth, payment, or trading logic
- Produce a PASS / WARN / BLOCK report

**Does not:**
- Approve code for production
- Run or execute any code
- Make changes of its own

**Typical tools:** `scripts/safety-gate.py` + Claude for analysis, or Claude alone with the diff pasted in

**Prompt:** See [`prompts/safety-gate-agent.md`](../prompts/safety-gate-agent.md)

---

## Diff Explainer Agent

**Job:** Produces a plain-English summary of every change in the PR, suitable for a non-technical reviewer or a developer unfamiliar with that part of the codebase.

**Does:**
- Walk through each changed file and explain what was done and why
- Flag anything surprising or hard to understand
- Identify changes that may have non-obvious downstream effects

**Does not:**
- Judge whether the change is correct (that's the Reviewer Agent's job)
- Make or suggest any code changes

**Typical tool:** Claude web or mobile with the diff pasted in

**Prompt:** See [`prompts/diff-explainer-agent.md`](../prompts/diff-explainer-agent.md)

---

## Test Runner Agent

**Job:** Confirms test coverage, runs tests, and identifies gaps.

**Does:**
- Run the test suite against the branch
- Report failures with context
- Identify code paths in the diff that lack test coverage
- Suggest specific test cases for uncovered logic

**Does not:**
- Fix failing tests automatically (flags for human or Builder Agent)
- Skip tests

**Typical tools:** Claude Code (local), CI pipeline

**Prompt:** See [`prompts/test-runner-agent.md`](../prompts/test-runner-agent.md)

---

## No-Secrets Agent

**Job:** Scans staged files and the git diff for leaked credentials, API keys, tokens, or other secrets.

**Does:**
- Check for common secret patterns (API key formats, token prefixes, base64-encoded credentials)
- Flag any file that should not be committed (`.env`, `credentials.json`, etc.)
- Produce a clear list of findings

**Does not:**
- Delete or modify any files
- Make commits

**Typical tools:** `scripts/check-secrets.sh` or `scripts/check-secrets.ps1`, plus Claude for pattern review

**Prompt:** See [`prompts/no-secrets-agent.md`](../prompts/no-secrets-agent.md)

---

## Release Manager Agent

**Job:** Prepares the release — drafts release notes, confirms the deploy checklist, and identifies what version this should be.

**Does:**
- Read the merged diff and produce human-readable release notes
- Confirm all items on the deploy checklist are complete
- Suggest a version number based on the nature of changes
- Identify any dependencies that need updating

**Does not:**
- Deploy to production
- Tag the release without human confirmation

**Typical tool:** Claude web

**Prompt:** See [`prompts/release-manager-agent.md`](../prompts/release-manager-agent.md)

---

## Rollback Agent

**Job:** Identifies the rollback path before a deploy happens, so if something breaks, the team knows exactly what to do.

**Does:**
- Identify which commits are being deployed
- Confirm that `git revert` or a direct rollback is viable
- List any database migrations or infrastructure changes that are not easily reversible
- Produce a rollback runbook for this specific deploy

**Does not:**
- Execute the rollback
- Make any changes

**Typical tool:** Claude web or mobile

**Prompt:** See [`prompts/rollback-agent.md`](../prompts/rollback-agent.md)
