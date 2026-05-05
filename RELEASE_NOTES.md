# Release Notes

## v0.1.0

Special Agent Ops v0.1.0 is the first public-ready release of the control kit.
It focuses on practical, human-controlled workflows for using AI coding agents
without treating them as autonomous developers.

### What This Release Includes

- Core mission flow: mission brief, planning gate, branch work, safety checks,
  PR review, release notes, and rollback planning.
- Agent role roster covering planner, builder, reviewer, safety gate,
  no-secrets, test runner, diff explainer, release manager, and rollback roles.
- Risk level guidance for GREEN, AMBER, RED, and BLACK missions.
- Copy-ready templates for mission briefs, repo boundaries, PR checklists,
  handoff reports, agent rules, and Codex review notes.
- Prompt files for common agent roles.
- Example workflows for docs-only work, frontend polish, safe bugfixes, and PR
  review assistance.
- Local safety scanners:
  - `scripts/safety-gate.py`
  - `scripts/check-secrets.ps1`
  - `scripts/check-secrets.sh`

### How To Verify

Run these checks from the repository root before tagging or announcing the
release:

```bash
python scripts/safety-gate.py --tree
```

```powershell
pwsh scripts/check-secrets.ps1 -All
```

Optional Unix shell check:

```bash
bash scripts/check-secrets.sh --all
```

Expected results:

- Safety gate reports `RESULT: PASS`.
- PowerShell no-secrets check reports `RESULT: CLEAN`.
- Bash no-secrets check reports `RESULT: CLEAN`.

### Current Limitations

- This is a workflow kit, not a sandbox, policy engine, or enforcement layer.
- The scanners are lightweight pattern checks. They reduce obvious risk but do
  not replace human review or dedicated security tooling.
- There is no bundled GitHub Actions workflow yet.
- Templates need to be adapted per repository before use.
- Hosted agent privacy, retention, and permission behavior depends on the tool
  provider and account configuration.
- BLACK-level work should still be kept out of agent context entirely.

### Next Roadmap Items

- GitHub Actions examples for running the safety gate and no-secrets checks on
  pull requests.
- A short onboarding guide for teams adopting their first agent workflow.
- More examples for multi-agent handoff and staged review.
- Optional markdown link check script.
- Launch issue template and PR template variants.
- Additional guidance for regulated teams and high-risk repositories.

