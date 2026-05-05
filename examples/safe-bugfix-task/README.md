# Example: Safe Bugfix Task

**Risk level:** 🟡 AMBER  
**Agent roles used:** Planner, Builder, Safety Gate, Test Runner, Reviewer, Diff Explainer  
**Estimated time:** 45–60 minutes

---

## The scenario

Users are reporting a 500 error when they view their profile if their account was created before the "display name" field was added to the user model. The endpoint crashes with a null reference when `user.displayName` is accessed.

The error is reported with a stack trace pointing to `src/api/users/profile.ts`, line 47.

---

## Step 1: Write the mission brief

```markdown
# Mission Brief

## Mission title
Fix null reference error on user profile endpoint for legacy accounts

## Risk level
AMBER

## Goal
Add a null guard for user.displayName in the profile endpoint.
Legacy accounts have null displayName. The endpoint should return null
or an empty string for this field rather than crashing.

## In scope
- src/api/users/profile.ts — add null guard at the point of access
- tests/api/users/profile.test.ts — add a test case for the null displayName scenario

## Out of scope
- The User model (src/models/User.ts) — do not modify the model
- Any migration to backfill displayName for legacy accounts (separate task)
- Any other endpoint
- Authentication or session handling

## Done criteria
- [ ] GET /api/users/profile no longer returns 500 for accounts with null displayName
- [ ] Returns displayName as null or "" for legacy accounts (pick one, document it)
- [ ] Existing tests still pass
- [ ] New test case covers the null displayName scenario
- [ ] Safety gate passes

## Branch name
fix/profile-null-display-name

## What to do if something unexpected comes up
If the fix seems to require modifying the User model or a database migration,
stop and flag it. Do not expand scope.
```

---

## Step 2: Planner review

Planner output example:

> Single sub-task: add null guard in profile.ts.
>
> The correct location is where `user.displayName` is first accessed in the endpoint handler, not in the User model.
> This keeps the fix local to this endpoint and avoids affecting other consumers of the User model.
>
> Test case needed: a mock user with `displayName: null` should return a 200 with null/empty displayName, not a 500.
>
> Risk: AMBER — touches a production API endpoint. Recommend running existing integration tests for the profile endpoint after the fix.

Gate 1: Confirmed. Scope is tight. Planner's note about fix location is correct.

---

## Step 3: Builder Agent

Using Claude Code (local):

```
Here is my mission brief:
[paste mission brief]

Here is the current content of src/api/users/profile.ts:
[paste file content]

Here is the current test file tests/api/users/profile.test.ts:
[paste test file content]
```

Agent implements the null guard and adds the test case. Commits to `fix/profile-null-display-name`.

**What the fix looks like (simplified):**

```typescript
// Before
const response = {
  displayName: user.displayName.trim(),  // crashes if null
  ...
};

// After
const response = {
  displayName: user.displayName ? user.displayName.trim() : null,
  ...
};
```

---

## Step 4: Safety gate

```bash
python scripts/safety-gate.py --staged
```

Expected: PASS. The change touches a user API endpoint (WARN on path), but no secrets, no auth logic, no deletions.

If WARN appears for the API path: this is expected. Read it, confirm there is no auth bypass, proceed.

---

## Step 5: Test Runner

```bash
# Run the test suite
npm test -- --testPathPattern=profile

# Or run all tests
npm test
```

Expected: All tests pass including the new null-displayName test case.

If the new test fails: the null guard was not implemented correctly. Return to Builder Agent with the specific failure.

---

## Step 6: Open PR

```
fix: handle null displayName in profile endpoint for legacy accounts

Adds a null guard for user.displayName in GET /api/users/profile.
Legacy accounts created before the displayName field was added
returned 500 due to calling .trim() on null. Now returns null
for these accounts.

Safety gate: PASS
Tests: all pass (1 new test added)
```

---

## Step 7: Reviewer Agent

Paste the diff into the Reviewer Agent prompt. Key things it should check:
- Is the null guard correct? (Does it handle `undefined` as well as `null`?)
- Does the fix affect the response shape for existing accounts? (It should not)
- Are there other places in the same file that access `displayName` without a guard?

If the reviewer finds that `displayName` is accessed without a guard in three other places in the same file, that is useful to know — but fixing those is a new mission, not an expansion of this one. Note it in the PR description.

---

## Step 8: Human review and merge

Human reads the 8-line diff. Confirms the guard is correct. Merges.

---

## What this teaches

- A tight mission brief prevents "while I'm here" scope creep
- The Planner's note about fix location (endpoint, not model) was the most valuable output of the planning step
- The Reviewer finding additional unguarded accesses is useful information — but following the workflow means you note it for a follow-up task rather than expanding this PR

---

## What this is NOT a good example of

This workflow is not appropriate for the following related tasks:
- **Backfilling displayName for legacy users** — that's a data migration, RED level, requires separate careful planning
- **Adding displayName validation on signup** — that touches the auth/signup flow, RED level
