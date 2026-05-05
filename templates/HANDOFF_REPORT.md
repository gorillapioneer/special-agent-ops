# Handoff Report

Use this when passing work from one agent to another, from an agent to a human for review, or when documenting a deploy for rollback purposes.

---

## Handoff metadata

**From:** _______________  (agent role or tool)  
**To:** _______________  (agent role, tool, or human name)  
**Date:** _______________  
**Mission title:** _______________  
**Branch:** _______________  

---

## What was completed

<!-- Describe what the previous agent or step accomplished. Be specific — reference file names and functions. -->

---

## What was changed

| File | Type of change | Notes |
|---|---|---|
| | Added / Modified / Deleted | |

---

## What was not completed

<!-- Any sub-tasks from the mission brief that were not finished, and why. -->

---

## Out-of-scope issues found

<!-- Things discovered during the work that are not in this mission brief. Each one should have a follow-up action. -->

| Issue found | Location | Recommended follow-up |
|---|---|---|
| | | |

---

## Current state of tests

- [ ] All tests pass
- [ ] Some tests fail — listed below:

```
<!-- paste test failure output here -->
```

---

## Safety gate result

- [ ] PASS
- [ ] WARN — notes:
- [ ] BLOCK — not merged

---

## Rollback information

**How to revert this change:**

```bash
# For a merged PR:
git revert <commit-sha>

# For an unmerged branch:
git branch -d feature/branch-name
```

**Are there any changes that cannot be easily reverted?**

- [ ] No — all changes are in code only
- [ ] Yes — describe below:

```
<!-- e.g. database migrations, third-party API calls, infrastructure changes -->
```

**If yes, rollback steps:**

1. 
2. 
3. 

---

## Notes for the next step

<!-- Anything the next agent or human needs to know before they pick up this work. -->

---

## Sign-off

**Prepared by:** _______________  
**Reviewed by:** _______________  
**Ready to proceed:** [ ] Yes  [ ] No — reason:
