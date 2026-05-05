# Mission Brief

Fill this out before any agent session starts. Be specific. Vague briefs produce unpredictable results.

---

## Mission title

<!-- One line. What is this task? -->

## Risk level

<!-- GREEN / AMBER / RED / BLACK — see docs/risk-levels.md -->

## Goal

<!-- What is the specific, bounded outcome? One paragraph. -->

## In scope

<!-- List the specific files, directories, or components the agent is allowed to touch. -->

- 
- 

## Out of scope (explicit)

<!-- List things the agent must NOT touch, even if it looks helpful. This is important. -->

- 
- 
- No secrets, credentials, or environment variables
- No changes to authentication or session logic (unless this mission is specifically about that)
- No direct commits to `main`

## Done criteria

<!-- How do you know the task is complete? Be specific and testable. -->

- [ ] 
- [ ] All existing tests still pass
- [ ] Safety gate passes (PASS or reviewed WARN)
- [ ] No secrets in diff

## What to do if something unexpected comes up

<!-- What should the agent do if it finds something outside scope? -->

Stop and flag it. Do not attempt to fix something that is not in this brief. Leave a comment or note and ask for clarification before proceeding.

## Branch name

<!-- Feature branch to use -->
`feature/`

## Related issue or ticket

<!-- Link to issue, ticket, or conversation if one exists -->

## Additional context

<!-- Anything the agent needs to know that is not obvious from the code -->

---

**Approved by:** _______________  
**Date:** _______________  
**Gate 1 confirmed:** [ ]
