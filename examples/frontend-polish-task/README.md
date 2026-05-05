# Example: Frontend Polish Task

**Risk level:** 🟡 AMBER  
**Agent roles used:** Planner, Builder, Safety Gate, Diff Explainer, Reviewer  
**Estimated time:** 30–45 minutes including review

---

## The scenario

The pricing page has stale copy and inconsistent spacing. The design team provided updated copy in a doc. You want an agent to apply the copy changes and fix the spacing in the component.

---

## Step 1: Write the mission brief

```markdown
# Mission Brief

## Mission title
Update pricing page copy and fix spacing inconsistencies

## Risk level
AMBER

## Goal
Update the copy on the pricing page to match the new copy doc.
Fix the inconsistent vertical spacing between the pricing tiers.
No logic changes. No routing changes. No data fetching changes.

## In scope
- src/components/PricingPage.tsx
- src/styles/pricing.module.css (or equivalent stylesheet)

## Out of scope
- Any shared component (Button, Card, Layout, etc.)
- src/api/ — no API changes
- Any routing or navigation
- Any A/B test flags or feature flags
- Tests (UI copy changes do not require test updates for this task)

## Done criteria
- [ ] All copy matches the provided copy doc
- [ ] Spacing between pricing tiers is consistent
- [ ] No shared components were modified
- [ ] The component still renders correctly (visual check by human)

## Branch name
feature/pricing-page-polish

## What to do if something unexpected comes up
If the component imports something that needs to change for the spacing fix,
stop and flag it rather than modifying the shared import.
```

---

## Step 2: Planner review

Planner output example:

> Sub-tasks:
> 1. Update copy strings in PricingPage.tsx — GREEN
> 2. Fix spacing in pricing.module.css — GREEN to AMBER
>
> Note: PricingPage.tsx imports `PricingCard` from a shared components directory.
> The spacing fix should be in the CSS module, not in PricingCard.
> If fixing spacing requires changing PricingCard, that is a scope expansion — flag it.

Gate 1: Confirmed. The planner's note about PricingCard is important. Added explicitly to out-of-scope.

---

## Step 3: Builder Agent

Using Claude Code (local) or Claude web. Provide:
- Mission brief
- Contents of `PricingPage.tsx`
- Contents of `pricing.module.css`
- The new copy doc

Agent output: updated `PricingPage.tsx` with new copy and updated `pricing.module.css` with spacing fixes.

```bash
git checkout -b feature/pricing-page-polish
# Apply changes
git add src/components/PricingPage.tsx src/styles/pricing.module.css
git commit -m "feat: update pricing page copy and fix tier spacing"
```

---

## Step 4: Safety gate

```bash
python scripts/safety-gate.py --staged
```

Expected result: PASS (copy and CSS changes, no secrets, no risky paths).

If WARN appears, read it. A CSS file is unlikely to contain secrets but confirm.

---

## Step 5: Tests

Run your test suite. For a copy-and-spacing change:
- Unit tests should still pass (no logic changed)
- If you have visual snapshot tests, check them

If snapshots fail because of intentional spacing changes: update the snapshots and commit them.

---

## Step 6: Diff Explainer

Paste the diff into Claude with the Diff Explainer prompt from `prompts/diff-explainer-agent.md`.

Example output:

> **PricingPage.tsx:** Updated 6 copy strings on the pricing page to match the new copy doc.
> Changed the heading from "Choose a plan" to "Simple, honest pricing." Tier descriptions updated.
> **Nothing unusual.**
>
> **pricing.module.css:** Increased `margin-bottom` on `.tier-card` from 16px to 24px.
> Added `gap: 24px` to the `.tier-grid` container.
> **Nothing unusual.**

---

## Step 7: PR and human review

Open the PR with the diff explanation. Human reads the diff (it's short). Merges.

---

## What this teaches

- AMBER tasks are not scary — the extra steps (safety gate, diff explanation) add maybe 10 minutes total
- The planner's scoping note prevented an unintended shared component change
- The diff explanation makes the human review genuinely fast — they know exactly what to look for

---

## What could go wrong (and how the workflow catches it)

| What could happen | How it's caught |
|---|---|
| Agent modifies PricingCard (shared) | Diff shows PricingCard in changed files — caught at human review |
| Agent adds an analytics call to the copy change | Reviewer agent flags the new function call |
| Agent hardcodes a string that should be i18n | Reviewer agent or human notes it during review |
