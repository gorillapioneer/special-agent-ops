# Example: PR Review Task

**Risk level:** 🟢 GREEN  
**Agent roles used:** Diff Explainer, Reviewer  
**Estimated time:** 10–15 minutes

---

## The scenario

A human developer on your team has opened a PR. It is 200 lines of diff across 4 files. You want to use an agent to produce a plain-English explanation and an initial review before you read it yourself. This makes your review faster and catches things you might miss when reading cold.

This example does not involve the agent writing any code.

---

## What you need

- The git diff of the PR
- The stated purpose of the PR (from the PR description)

---

## Step 1: Get the diff

```bash
# From the feature branch:
git diff main...HEAD

# Or from GitHub: download the diff file from the PR
# https://github.com/org/repo/pull/123.diff
```

---

## Step 2: Run the Diff Explainer

Open Claude (web). Paste the system prompt from `prompts/diff-explainer-agent.md`.

Then paste:

```
Please explain the following PR diff.

The stated purpose of this PR is:
[paste the PR description]

Diff:
[paste the git diff]
```

**Example output:**

> ### src/api/invoices/create.ts
> **What this file does:** Handles invoice creation requests from the billing module.
> **What changed:** Added validation that `lineItems` is a non-empty array before
> attempting to create the invoice. Previously, an empty array would pass through
> and create an invoice with no items, which caused a downstream error in the
> PDF generator.
> **Likely reason:** Bug fix for the empty line items case.
> **Anything to note:** The validation throws a 400 error — confirm this is the
> intended status code for this case.
>
> ### tests/api/invoices/create.test.ts
> **What this file does:** Unit tests for the invoice creation endpoint.
> **What changed:** Added two new test cases — one for empty lineItems array,
> one for a missing lineItems field entirely.
> **Likely reason:** Coverage for the bug fix above.
> **Anything to note:** Nothing unusual.

---

## Step 3: Run the Reviewer Agent

Using the same diff, paste the system prompt from `prompts/codex-reviewer-agent.md`.

Then paste the same diff with the PR purpose.

**Example reviewer output:**

> ### Summary
> This PR adds input validation for the lineItems field in the invoice creation
> endpoint to prevent creating invoices with no line items.
>
> ### Findings
>
> **Severity:** Medium  
> **Location:** src/api/invoices/create.ts:34  
> **Issue:** The validation checks `lineItems.length === 0` but does not check for
> `null` or `undefined`. If the field is missing entirely from the request body,
> `lineItems.length` will throw a TypeError.  
> **Suggestion:** Check `!lineItems || lineItems.length === 0`
>
> **Severity:** Low  
> **Location:** tests/api/invoices/create.test.ts  
> **Issue:** The test for missing lineItems field was added but it tests the wrong
> status code — it expects 422 but the implementation returns 400.  
> **Suggestion:** Align the expected status code with the implementation.
>
> ### Overall assessment
> REQUEST CHANGES — two specific issues to fix (listed above)

---

## Step 4: Post the results to the PR

Add the diff explanation and reviewer findings as PR comments. Label them clearly:

```
**Automated diff explanation:**
[paste the diff explainer output]

**Automated review findings:**
[paste the reviewer output]

Note: These are AI-generated. A human reviewer will make the final decision.
```

---

## Step 5: Human review

You now read the diff with:
- A plain-English map of what each file does
- Two specific issues already flagged (null check, wrong status code in test)

Your review is faster and more focused. You confirm the findings are real, add any observations the agent missed, and make the merge decision.

---

## What this teaches

- Using agents for review does not replace human review — it augments it
- The reviewer caught a real bug (missing null check) that would have been easy to miss reading cold
- The explanation made the review 3x faster because you knew what each file was doing before you read it

---

## When to use this pattern

- When you are reviewing code in a part of the codebase you are less familiar with
- When the diff is long and you want a map before diving in
- When multiple PRs are waiting and you want initial triage

## When not to use it

- Do not use agent review as a reason to spend less time reading RED-level PRs. The agent augments, it does not substitute.
- For security-sensitive changes, the human still needs to read every line.
