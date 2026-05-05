# PR Checklist

Use this checklist before merging any PR that contains agent-produced code.

Copy it into the PR description, or keep it as a comment on the PR.

---

## Before opening the PR

- [ ] Branch is not `main` or `master`
- [ ] No-secrets check has been run (`scripts/check-secrets.sh` or `.ps1`)
- [ ] Safety gate has been run (`scripts/safety-gate.py --diff`)
- [ ] Safety gate result is `PASS`, or all `WARN` items have been reviewed and accepted with a written reason
- [ ] All tests pass on this branch
- [ ] Diff has been confirmed to match the mission brief — nothing extra, nothing missing

## PR description includes

- [ ] Reference to the Mission Brief or task description
- [ ] Summary of what changed
- [ ] Safety gate result (PASS / WARN with notes)
- [ ] Test results
- [ ] Any out-of-scope issues found and not addressed (with a follow-up plan or issue link)

## Diff review

- [ ] Every changed file has been looked at — not just the summary
- [ ] No secrets, tokens, or credentials visible anywhere in the diff
- [ ] No changes to files listed as restricted or off-limits in `SAFE_REPO_BOUNDARIES.md`
- [ ] Deletions are intentional and match the mission brief
- [ ] New dependencies are listed and intentional
- [ ] Changes to configuration files are intentional and understood

## For AMBER-level missions

- [ ] Diff Explainer Agent output has been reviewed
- [ ] Reviewer Agent output has been reviewed
- [ ] At least one human has read the full diff

## For RED-level missions

- [ ] Two humans have reviewed the diff
- [ ] Security implications have been explicitly reviewed
- [ ] Rollback plan is documented (link or attach `HANDOFF_REPORT.md`)
- [ ] Deploy approval is confirmed

## Sign-off

**Reviewer:** _______________  
**Date:** _______________  
**Decision:** Approve / Request changes  
**Notes:**
