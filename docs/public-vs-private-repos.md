# Public vs Private Repos

The mission flow and agent roles are the same whether your repo is public or private. The risk profile is different.

---

## Private repos

Most developers assume private means safe. It does not.

### Risks specific to private repos

**Git history is permanent.** A secret committed to a private repo is still exposed to:
- Everyone with current repo access
- Everyone with past repo access
- Backup systems
- Anyone who gains access in the future

If a secret is committed, treat it as compromised. Revoke and rotate it before cleaning the history.

**Internal file structure reveals your architecture.** Agent sessions that include private file paths, internal naming conventions, or infrastructure details may expose these in:
- PR descriptions that get copy-pasted
- Agent-produced comments and commit messages
- Screenshots shared during code review

Review agent output as if it will eventually be seen publicly. It probably will be.

**Agents infer context from what they can see.** If you paste internal credentials, API keys, or sensitive config into an agent session "just so it understands the structure," you have exposed them to that service's logging infrastructure. Do not do this.

### Private repo checklist

- [ ] Secrets are in environment variables or a vault, never in the repo
- [ ] `.env` and similar files are in `.gitignore` before any agent session begins
- [ ] Agent sessions do not receive credentials or secret values as input
- [ ] `SAFE_REPO_BOUNDARIES.md` defines which paths agents can touch
- [ ] Git history is checked after any agent session for accidental secret commits

---

## Public repos

Public repos have an additional layer of exposure: anything committed is immediately visible to anyone, forever, including automated scrapers that look for leaked credentials.

### Risks specific to public repos

**Secrets are indexed within minutes.** If a key is committed to a public repo, assume it has been found by an automated scanner even if you delete it seconds later. Revoke immediately, no exceptions.

**Agent-produced content carries your name.** PR descriptions, commit messages, and code comments written by an agent will appear under your GitHub account. Review them before they become public record.

**Scope creep is visible.** If an agent makes a change outside its intended scope and it merges to a public repo, that change is part of your public project history. It may confuse contributors or expose unintended design decisions.

**Issues and PR comments are public.** Be careful about what context you include in issues or PR descriptions when discussing agent-assisted work on a public repo. Internal architecture details, business logic, or unfinished features mentioned in passing become public.

### Public repo checklist

- [ ] No secrets in any file, ever — use GitHub Actions secrets, environment variables, or a vault
- [ ] Confirm `.gitignore` is correct before the first commit
- [ ] Review all agent-produced commit messages and PR descriptions before they go public
- [ ] Check the diff carefully before merging — what's in the diff is what's public
- [ ] Safety gate and no-secrets check are non-negotiable before any PR merge

---

## The one rule that applies to both

**Run the no-secrets check every time, regardless of repo visibility.**

The check takes seconds. The cost of missing a secret is not.

```bash
bash scripts/check-secrets.sh
```

or

```powershell
pwsh scripts/check-secrets.ps1
```

---

## Working with external agent tools (Devin, v0, hosted services)

When you use a hosted agent service that accesses your repo directly:

1. **Review the service's data retention policy.** Code you hand to a hosted agent may be stored, used for training, or accessible to service operators.
2. **Limit repository access scope.** Grant the minimum permissions required. Do not give write access to `main`.
3. **For private repos:** Consider whether the IP exposure is acceptable before connecting a hosted service.
4. **Always review what they push.** Treat a hosted agent's PR exactly like any other agent PR — read the diff, run the safety gate, check for secrets.

No hosted agent tool has inherent access to your secrets unless you explicitly grant it. Check what you have granted.
