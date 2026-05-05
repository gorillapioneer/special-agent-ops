# PR Safety Demo

This example shows the full review path for a small AMBER mission. The change
is fictional, but the control flow is the one you want for real pull requests.

## Mission Brief

**Mission title:** Add an API health badge to a dashboard.

**Goal:** Show a small health badge on the dashboard so users can see whether
the API status endpoint is responding. The badge should display a neutral
loading state, a healthy state, and an unavailable state.

**Risk level:** AMBER

**Why AMBER:** The task touches a user-facing dashboard and calls an existing
API status helper. It should be easy to review and revert, but it can still
affect the dashboard experience.

## Planned Files

In scope:

- `src/dashboard/Dashboard.tsx`
- `src/dashboard/ApiHealthBadge.tsx`
- `src/dashboard/ApiHealthBadge.test.tsx`

Out of scope:

- No changes to routing.
- No changes to the API status endpoint.
- No changes to session handling or permissions.
- No new dependencies.
- No environment variable changes.

## Builder Handoff

**Branch:** `feature/api-health-badge`

**Instructions for Builder Agent:**

- Add a small badge component that reads the existing API status helper.
- Keep the visual treatment consistent with existing dashboard controls.
- Add focused tests for loading, healthy, and unavailable states.
- Stop if the existing status helper needs behavior changes.
- Do not modify shared dashboard layout except where the badge is inserted.

**Done criteria:**

- Badge appears in the dashboard header.
- All three display states are covered by tests.
- No unrelated files are changed.
- Safety checks pass before PR review.

## Safety Gate Result

```text
RESULT: PASS
No risky patterns detected.
```

No risky paths, deletion-heavy changes, dynamic execution, or credential-like
content were found.

## Reviewer Result

**Verdict:** Request one small change, then approve.

Findings:

- The component handles the healthy and unavailable states clearly.
- Test coverage matches the stated mission.
- The first version used a generic label; reviewer asked for clearer accessible
  text on the badge.

Resolution:

- Builder updated the badge label.
- Reviewer confirmed the diff still matches the mission.

## Diff Explainer Summary

The PR adds one dashboard badge component, inserts it into the dashboard header,
and adds tests for the expected states. It does not change the API endpoint,
routing, dependencies, or permissions.

The change is easy to revert because it is limited to one new component, one
dashboard insertion point, and one test file.

## Human Approval Checklist

- [ ] Mission brief is linked in the PR.
- [ ] Files changed match the planned files.
- [ ] Safety gate result is `PASS`.
- [ ] PowerShell no-secrets check is `CLEAN`.
- [ ] Bash no-secrets check is `CLEAN`.
- [ ] Tests for the badge states pass.
- [ ] Reviewer finding was resolved.
- [ ] Diff Explainer summary matches the actual diff.
- [ ] No out-of-scope dashboard cleanup was included.
- [ ] Human reviewer read the final diff.

## Rollback Plan

If the badge causes a dashboard issue:

1. Revert the PR that added `feature/api-health-badge`.
2. Confirm the dashboard renders without the badge.
3. Re-run the safety checks and dashboard tests.
4. Open a follow-up issue describing the failed state and the observed behavior.

Because the mission adds a small UI surface and no data migrations, rollback is
expected to be a normal PR revert.

