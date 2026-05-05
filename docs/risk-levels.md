# Risk Levels

Every mission gets a risk level before any agent starts work. The level determines the required oversight, the gate requirements, and whether the mission should be attempted at all.

Assign the level based on the worst-case impact if the agent makes a mistake.

---

## 🟢 GREEN — Low risk

**What it means:** The change is contained, easy to understand, and easy to revert. A mistake here is annoying, not dangerous.

**Typical examples:**
- Fix a typo in documentation
- Update a README
- Add comments to code
- Rename a variable in an isolated utility function
- Update a dependency version (patch release)

**Required process:**
- Mission brief
- Gate 1 (can be a quick self-review)
- Branch + PR
- Gate 2 (quick diff read)
- Merge

**What you can skip:** The safety gate script is optional but still fast to run. Tests should still pass.

---

## 🟡 AMBER — Moderate risk

**What it means:** The change touches production code, has some surface area, or could cause a visible regression. A mistake here affects users or delays a release.

**Typical examples:**
- New feature in an isolated module
- Refactor of a non-critical component
- UI copy and style changes
- Adding a new API endpoint with no auth implications
- Updating a dependency with a minor version bump

**Required process:**
- Mission brief
- Gate 1 (deliberate review)
- Branch + PR
- Full safety gate run
- Tests pass
- PR review by at least one human
- Gate 2 (proper diff read)
- Merge

**What you cannot skip:** Safety gate, test pass, Gate 2 human review.

---

## 🔴 RED — High risk

**What it means:** The change touches code where a mistake could cause a security incident, data loss, or financial impact. These changes require extra caution and extra eyes.

**Typical examples:**
- Authentication or session handling changes
- Password hashing, credential storage
- Payment flow or billing integration
- Database schema migrations (especially destructive ones)
- Permission and access control logic
- Changes that involve user data handling
- Infrastructure changes (server config, deployment scripts)

**Required process:**
- Mission brief reviewed by two humans before any agent starts
- Agent scope explicitly limited — only the minimum necessary
- Gate 1 with second human sign-off
- Branch + PR
- Full safety gate run with manual review of WARN items
- Security-specific review (use `prompts/safety-gate-agent.md`)
- All tests pass
- PR reviewed by at least two humans
- Gate 2 with specific sign-off that security implications were reviewed
- Rollback plan documented before deploy
- Gate 3: deploy requires explicit human confirmation

**What you cannot skip:** Anything. All steps are required.

**Note on agents and RED-level code:** Consider whether the agent should touch this code at all. Sometimes the right answer is to do it manually and use the agent only for explanation and review, not for writing.

---

## ⬛ BLACK — Do not delegate

**What it means:** This code should not be handed to an AI agent under any circumstances in its current form. The risk of a mistake is too high, the code is too sensitive, or the implications are not fully understandable from a diff.

**Typical examples:**
- Live trading execution logic
- Production secrets management (vault integrations, key rotation)
- Cryptographic primitive implementations
- Compliance-critical code in regulated industries
- Code that directly controls physical systems
- Core identity/auth infrastructure that the rest of the system depends on

**Required process:** Do not create a mission. Do not use an agent. Do this manually with full human review.

**What to do instead:** You can use an agent to *explain* or *document* BLACK-level code, but not to modify it. Use an agent to review a human-written diff of this code. Do not give the agent write access to these paths.

**Remove BLACK-level code from agent context entirely.** Do not paste it into a prompt "just to see." If it is in the repo, define it as out-of-bounds in `SAFE_REPO_BOUNDARIES.md`.

---

## How to determine the level

When in doubt, go higher.

Ask:
1. If the agent makes an honest mistake here, what happens to users?
2. If the agent misunderstands the prompt, what is the worst realistic outcome?
3. Is there any way a change here could expose credentials, compromise accounts, or lose data?
4. Can this be reverted in under 5 minutes if something goes wrong after deploy?

If the answer to 3 is "maybe" or the answer to 4 is "no," go up a level.

---

## Level escalation

Missions can change level as the planner breaks them down. A vague task that sounds AMBER may reveal RED sub-tasks when the Planner Agent analyzes it. Always re-evaluate the level after planning, before execution.
