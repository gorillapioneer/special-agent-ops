# Mission Flow

Every agent-assisted task follows this flow. The sequence is not optional — skipping steps is where things go wrong.

---

## Step 1: Task Idea

Someone has a task. It could come from a backlog, a bug report, a product request, or your own notes.

At this point it is probably vague: "improve the onboarding flow," "fix the login bug," "add dark mode."

**What you do:** Write it down in plain language before you open any agent.

---

## Step 2: Mission Brief

Fill out `templates/MISSION_BRIEF.md`. This forces clarity before any agent touches anything.

A mission brief answers:
- What is the specific, bounded goal?
- What files and paths are in scope?
- What is explicitly out of scope?
- What is the risk level?
- What does "done" look like?
- What should the agent do if it hits something unexpected?

This takes 5–10 minutes and prevents most agent disasters.

---

## Step 3: Planner Agent

Hand the mission brief to the Planner Agent. It will:
- Break the task into specific sub-tasks
- Identify dependencies and risks
- Flag any parts that need RED or BLACK treatment
- Recommend whether the scope makes sense

**Output:** A revised or confirmed mission brief, ready to execute.

---

## Step 4: Human Approval — Gate 1

A human reads the mission brief and the planner's output.

**Questions to answer before approving:**
- Is the scope clear and bounded?
- Are the right files in scope and the wrong files out of scope?
- Is the risk level correct?
- Is there a rollback path if something goes wrong?

If yes: proceed. If no: revise the brief.

**Do not skip this gate.** This is the moment when an agent running with incorrect scope is cheapest to fix.

---

## Step 5: Builder Agent on a Feature Branch

The Builder Agent works on a dedicated feature branch. Never `main`.

The agent:
- Follows the mission brief exactly
- Commits its work as it goes
- Flags anything outside the agreed scope instead of acting on it
- Does not touch secrets, infrastructure, or BLACK-level code

---

## Step 6: No-Secrets Agent

Before opening a PR, run the no-secrets check on staged changes.

```bash
bash scripts/check-secrets.sh
# or on Windows:
pwsh scripts/check-secrets.ps1
```

If any secrets are found: stop. Do not open the PR. Fix the issue. Rotate any exposed credentials immediately.

---

## Step 7: Safety Gate Agent

Run the safety gate on the diff:

```bash
python scripts/safety-gate.py --diff
```

Or paste the diff into the Safety Gate Agent prompt.

**Results:**
- `PASS` — proceed
- `WARN` — human reviews the flagged items before proceeding
- `BLOCK` — stop. Do not open the PR until the issue is resolved.

---

## Step 8: Test Runner Agent

Run your test suite against the branch. If tests fail:
- Log the failure
- Return to the Builder Agent with specific instructions to fix the failing test
- Re-run safety gate after any fixes

Do not merge a branch with failing tests.

---

## Step 9: Pull Request

Open the PR with:
- A clear title that describes the change
- A reference to the mission brief
- The safety gate result
- The test result

Use `templates/PR_CHECKLIST.md` to confirm everything is in order.

---

## Step 10: Reviewer Agent and Diff Explainer Agent

Two passes on the PR before human review:

1. **Diff Explainer:** Produces a plain-English summary of every change. This makes human review faster.
2. **Reviewer Agent:** Looks for logic errors, regressions, and out-of-scope changes.

The output of both goes into the PR description or as comments.

---

## Step 11: Human Review and Merge — Gate 2

A human reads:
- The diff
- The diff explanation
- The reviewer's comments
- The safety gate result

Then merges — or requests changes.

**This is not optional.** No automated merge of agent-produced code.

---

## Step 12: Release Manager Agent

After merge, the Release Manager Agent:
- Drafts release notes from the merged commits
- Confirms the deploy checklist
- Recommends a version number

---

## Step 13: Rollback Plan

Before deploying, the Rollback Agent documents:
- Exactly which commits are being deployed
- How to revert them
- Any changes (migrations, infra) that are not easily reversible

If it cannot produce a clear rollback plan, that is a red flag worth pausing for.

---

## Summary

| Step | Who | Gate? |
|---|---|---|
| Task Idea | Human | — |
| Mission Brief | Human | — |
| Planner | Agent | — |
| Approve Plan | Human | ✅ Gate 1 |
| Build | Agent | — |
| No-Secrets Check | Script + Human | — |
| Safety Gate | Script + Human | — |
| Tests | Agent / CI | — |
| Open PR | Human | — |
| Review + Explain | Agent | — |
| Merge | Human | ✅ Gate 2 |
| Release Notes | Agent | — |
| Rollback Plan | Agent + Human | — |
