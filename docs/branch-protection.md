# Branch Protection

Branch protection turns the Special Agent Ops workflow from guidance into a
repo-level guardrail. It does not replace human judgment, but it makes the
safe path the default path.

Use this for `main` before you rely on agent-produced pull requests.

## Protect `main`

In GitHub:

1. Open **Settings** for the repository.
2. Go to **Branches**.
3. Add a branch protection rule.
4. Set the branch name pattern to `main`.

If your default branch is named something else, protect that branch instead.

## Require Pull Requests Before Merging

Enable **Require a pull request before merging**.

Recommended settings:

- Require at least one approving review.
- Dismiss stale approvals when new commits are pushed.
- Require review from code owners if your repo uses `CODEOWNERS`.
- Require conversation resolution before merging.

This supports the Gate 2 rule: a human reads the diff before merge.

## Require Status Checks To Pass

Enable **Require status checks to pass before merging**.

After the `Safety checks` workflow has run at least once, select the required
checks from that workflow:

- `Python safety gate`
- `PowerShell secrets check`
- `Bash secrets check`

If GitHub shows check names with the workflow prefix, select the matching
entries for the same three jobs.

Also enable **Require branches to be up to date before merging** if your team
wants every PR checked against the latest `main`.

## Prevent Force Pushes

Leave **Allow force pushes** disabled.

Force pushes can hide review history, rewrite commits after checks pass, and
make rollback harder to reason about. For an agent-assisted workflow, stable PR
history is part of the control system.

## Admin Enforcement

GitHub lets repository admins decide whether rules apply to admins.

Reasons to enforce rules on admins:

- Keeps emergency shortcuts rare and visible.
- Makes solo projects behave like team projects.
- Prevents accidental direct pushes during routine work.
- Ensures every PR follows the same safety path.

Reasons some teams may not enforce rules on admins:

- A small project may need a clear emergency path.
- Maintainers may need to repair a broken protection rule.
- Early-stage repos may still be tuning their workflow.

If admins can bypass rules, write down when bypass is acceptable. A good rule:
bypass only for urgent repository repair, never for ordinary feature work.

## How This Supports Human Approval

Branch protection does not approve code. It creates the conditions for real
approval:

- The agent works on a branch, not `main`.
- CI runs the safety gate and no-secrets checks.
- The PR cannot merge until required checks pass.
- A human still reads the mission, the diff, the check output, and the rollback
  notes.

Passing branch protection means the PR is eligible for a human decision. It
does not mean the PR is automatically safe to merge.

