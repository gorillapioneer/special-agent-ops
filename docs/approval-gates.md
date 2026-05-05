# Approval Gates

Approval gates are the points in the mission flow where a human must make a decision before work continues. They are not bureaucracy — they are the mechanism that keeps AI-assisted development under control.

There are two mandatory gates in every mission. Additional gates apply to higher-risk work.

---

## Gate 1: Approve the Plan

**When:** After the Planner Agent produces a mission brief and before any code is written.

**Who approves:** The developer or team lead responsible for the change.

**What you check:**

1. **Scope is correct.** The mission brief describes exactly the change you intended — no more, no less.
2. **Boundaries are set.** `SAFE_REPO_BOUNDARIES.md` has been filled out and the in-scope files are appropriate.
3. **Risk level is accurate.** If it looks like AMBER but touches auth or payments, it should be RED.
4. **Out-of-scope is explicit.** The brief says what the agent should NOT do, not just what it should do.
5. **Done criteria are clear.** You know what a successful outcome looks like before the agent starts.

**If Gate 1 is skipped:** The agent may implement the right thing, the wrong thing, or something adjacent to the right thing. You will not know which until you review the output — and by then, reviewing is harder and reverting is more disruptive.

---

## Gate 2: Approve the PR

**When:** After the PR is open, reviewed by the Reviewer Agent, explained by the Diff Explainer, and safety-gated.

**Who approves:** At minimum, one human developer who was not the one who wrote the mission brief.

**What you check:**

1. **Diff matches the mission.** The changes are what the brief called for, nothing extra.
2. **No secrets in the diff.** The no-secrets check passed.
3. **Safety gate passed.** No BLOCK result. WARN results have been reviewed and resolved or accepted.
4. **Tests pass.** All tests green on the branch.
5. **Diff explanation makes sense.** The plain-English summary of the changes is accurate and nothing is surprising.
6. **Reviewer findings are addressed.** Any issues flagged by the Reviewer Agent are resolved or explicitly accepted with a reason.

**If Gate 2 is skipped:** Agent-generated code merges without human eyes on the diff. This is how subtle bugs, unintended behaviour, and security issues reach production.

---

## Additional Gates for RED-Level Work

For missions rated RED (auth, payments, database migrations, security-critical changes):

**Gate 1.5: Scope review by a second human**
Before the builder agent starts, a second person confirms the mission brief. One set of eyes is not enough for high-risk changes.

**Gate 2.5: Security review before merge**
The diff is reviewed specifically for security implications — not just logic correctness. Use `prompts/safety-gate-agent.md` as a guide.

**Gate 3: Deploy approval**
For RED changes, the deploy itself requires a human sign-off. The rollback plan must exist and be confirmed before deployment begins.

---

## What counts as approval

Approval means:
- A human read the relevant material
- Made a conscious decision to proceed
- Is accountable for that decision

It does not mean:
- Clicking "Approve" on a PR without reading it
- Trusting that the agent did it right
- Rubber-stamping because it passed the automated checks

Automated checks (safety gate, no-secrets, tests) are filters, not gates. The gate is the human decision.

---

## Async and solo workflows

Working alone or asynchronously? Gates still apply, but you can be your own approver.

The value of the gate is not the second person — it is the **pause and check.** Require yourself to step away from the agent session, read the mission brief or the diff fresh, and make an explicit decision to continue. This catches most of the mistakes that happen when you are in the flow of prompting and lose track of what you originally intended.

A useful practice: write your gate approval decision in a comment on the PR, even if you are the only reviewer. "Reviewed diff, scope matches brief, safety gate passed, tests green — merging." This creates a record and makes the pause real.
