# CI Safety Checks

Special Agent Ops includes a GitHub Actions workflow at
`.github/workflows/safety-checks.yml`. It runs on every push and pull request.

## What CI Checks

The workflow runs three independent jobs:

- **Python safety gate:** `python scripts/safety-gate.py --tree`
- **PowerShell secrets check:** `pwsh ./scripts/check-secrets.ps1 -All`
- **Bash secrets check:** `bash scripts/check-secrets.sh --all`

Each job checks the full git-tracked tree after checkout. The workflow does not
install project dependencies or upload artifacts.

The safety gate still treats new workflow files as review-worthy. This repo
allows its own `safety-checks.yml` workflow path so the scanner can run in CI;
the workflow contents are still scanned.

## Why This Matters

These checks make the safety baseline visible on every PR. They catch obvious
risk signals before review starts:

- risky file paths
- risky content patterns
- likely credential leaks
- scanner regressions

That does not make a PR safe by itself. It makes the review queue cleaner and
forces obvious problems to be fixed before a human spends time on the diff.

## If A Check Fails

Do not bypass the failure or merge around it.

1. Open the failed job log.
2. Read the exact finding and file path.
3. If the finding is real, fix the change and rotate any exposed credential if
   needed.
4. If it is a false positive, make the smallest possible change. Prefer safe
   placeholder wording in docs, or a narrow scanner allowance for scanner-owned
   pattern definitions only.
5. Re-run the workflow by pushing the fix.

If a safety gate `WARN` is intentional, document why in the PR and resolve it
with the narrowest practical change before merge.

## This Does Not Replace Human Review

CI is a filter, not a decision maker. It cannot confirm that the mission scope
was right, that the implementation is correct, that tests cover the important
behavior, or that rollback notes are complete.

Before merge, a human still reviews:

- the mission brief and boundaries
- every changed file
- safety check output
- test results
- reviewer or diff-explainer notes
- rollback path for risky changes

Passing CI means "ready for review," not "approved."
