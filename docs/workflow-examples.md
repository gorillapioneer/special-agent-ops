# Workflow Examples

These are condensed descriptions of the four example workflows in the `examples/` directory. Each one shows the mission flow applied to a specific type of task.

---

## 1. Docs-only task (GREEN)

**The task:** Update the project README to reflect a new feature that was shipped last week.

**Why it's GREEN:** No code changes. Easy to review. Easy to revert. If the agent produces bad prose, you just edit it.

**What the workflow looks like:**

1. Write a mission brief: "Update README to describe the new export feature. Touch only README.md. Do not modify any code files."
2. Planner confirms scope. Single sub-task. No risks flagged.
3. Builder Agent (Claude web or mobile) drafts the updated README.
4. Human reads it. Edits it. Commits to a branch.
5. PR opened, diff read, merged.

**Key lesson:** Even for GREEN tasks, put it on a branch and use a PR. This habit is cheap to maintain and catches edge cases (like the agent helpfully "improving" other sections of the README that were out of scope).

See: [`examples/docs-only-task/README.md`](../examples/docs-only-task/README.md)

---

## 2. Frontend polish task (AMBER)

**The task:** Improve the spacing and copy on the pricing page.

**Why it's AMBER:** Code changes in a production component. Visible to users. Could regress layout or break responsive behaviour if the agent misreads the component structure.

**What the workflow looks like:**

1. Mission brief: scope limited to the pricing page component file and its stylesheet. No changes to routing, data fetching, or shared components.
2. Planner confirms scope. Notes that the component imports shared layout tokens — the agent should not modify those.
3. Builder Agent (Claude Code or v0) makes the copy and spacing changes on a branch.
4. Safety gate runs. Passes (no secret patterns, no deletion-heavy changes).
5. Tests pass (visual snapshot tests confirm no layout regression).
6. Diff Explainer produces a plain summary of the CSS and copy changes.
7. Human reviews the diff, checks the rendered result.
8. Merged.

**Key lesson:** Even a "simple UI task" can have scope creep. The mission brief's explicit out-of-scope list ("do not touch shared layout tokens") prevented the agent from helpfully refactoring shared styles.

See: [`examples/frontend-polish-task/README.md`](../examples/frontend-polish-task/README.md)

---

## 3. Safe bugfix task (AMBER)

**The task:** Fix a null-reference error in the user profile API endpoint.

**Why it's AMBER:** Code change in a production endpoint. Error handling logic. Touches the user profile model. Potential for regression in related endpoints.

**What the workflow looks like:**

1. Mission brief: the specific error, the specific file and function, and a clear done criterion (the null case is handled, the existing tests still pass).
2. Planner confirms scope. Flags that the profile model is shared — the fix should add a guard at the endpoint level, not modify the model.
3. Builder Agent (Claude Code) implements the null check and writes a test for the null case.
4. Safety gate passes.
5. Tests pass (new test + existing tests).
6. Reviewer Agent confirms the fix does not introduce unexpected behaviour in the null path.
7. Human reviews the diff. 8 lines changed. Merges.

**Key lesson:** The Planner Agent's observation about where to put the guard was valuable. A less structured approach might have led to a model-level change that affected other consumers.

See: [`examples/safe-bugfix-task/README.md`](../examples/safe-bugfix-task/README.md)

---

## 4. PR review task (GREEN)

**The task:** Use an agent to review a PR before a human does the final read.

**Why it's GREEN:** The agent is reading and commenting, not writing. No code changes. No commits.

**What the workflow looks like:**

1. PR is open on a branch (produced by a human developer, not an agent).
2. Diff Explainer Agent reads the diff and produces a plain-English summary. This goes into a PR comment.
3. Reviewer Agent reads the diff and flags any logic concerns. This also goes into a PR comment.
4. Human developer reads both comments alongside the diff.
5. Human approves or requests changes.

**Key lesson:** Using agents for review does not replace human review — it augments it. The agent can read 500 lines of diff quickly and surface the interesting parts. The human still makes the decision.

See: [`examples/pr-review-task/README.md`](../examples/pr-review-task/README.md)

---

## Choosing the right example to start with

If you are new to this workflow: start with the docs-only task. Get comfortable with the branch-PR-merge loop before you add code changes.

If you have an existing codebase: start with the PR review task. No agent writes code — you just add explanation and review to your existing process. It is the lowest-stakes entry point.

If you want to see the full mission flow: the safe-bugfix task uses every step.
