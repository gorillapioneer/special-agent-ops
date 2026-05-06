<p align="center">
  <img src="assets/special-agent-ops-banner.svg" alt="Special Agent Ops - Mission control for AI coding agents" width="100%">
</p>

# Special Agent Ops

[![Safety checks](https://github.com/gorillapioneer/special-agent-ops/actions/workflows/safety-checks.yml/badge.svg)](https://github.com/gorillapioneer/special-agent-ops/actions/workflows/safety-checks.yml)

> **Mission control for AI coding agents: give every agent a mission, a boundary, a review gate, and an off switch.**

A practical control kit for coordinating AI coding agents without handing them the keys to the whole codebase.

---

## Black Box Recorder — CLI

Record everything an AI coding agent does in a session: git state, command output, changed files, and a compressed archive you can replay or audit later.

```bash
# Run any command and record it as a mission
python -m sao.cli run --name "pytest baseline" --command "python -m pytest"
```

Output:

```
========================================================
  SPECIAL AGENT OPS — MISSION COMPLETE
========================================================
  Mission ID:     20260506_091500_pytest_baseline
  Command:        python -m pytest
  Exit Code:      0
  Changed Files:  2
  Session Folder: blackbox/sessions/20260506_091500_pytest_baseline
  Archive:        blackbox/sessions/20260506_091500_pytest_baseline.zip
========================================================
```

Each session folder contains:

| File | Contents |
|---|---|
| `manifest.json` | Mission metadata, timing, exit code, branch, changed files |
| `stdout.txt` | Full command stdout |
| `stderr.txt` | Full command stderr |
| `git_status_before.txt` | `git status --short` before the command |
| `git_status_after.txt` | `git status --short` after |
| `git_diff.patch` | Unified diff of all uncommitted changes |
| `mission_summary.md` | Human-readable summary of the session |

The whole folder is also compressed to `<mission_id>.zip` for easy archiving.

### Installation

```bash
# No external dependencies — stdlib only.
# Option 1: run directly (no install needed)
python -m sao.cli run --name "my mission" --command "your-command"

# Option 2: install as a CLI tool
pip install -e .
sao run --name "my mission" --command "your-command"
```

### More examples

```bash
# Record a test run
python -m sao.cli run --name "unit tests" --command "python -m pytest tests/"

# Record a linting pass
python -m sao.cli run --name "lint check" --command "python -m ruff check ."

# Record any shell command — Windows PowerShell example
python -m sao.cli run --name "build check" --command "npm run build"
```

Sessions are stored under `blackbox/sessions/` (excluded from git by `.gitignore`).
Source: [`sao/`](sao/)

### Mission Seal

Each mission generates a SHA256 seal so you can verify the archive has not been changed after recording.

```
SPECIAL AGENT OPS MISSION SEAL
Mission ID: 20260506_091500_pytest_baseline
Created At: 2026-05-06T09:15:05.123456+00:00
Manifest SHA256: e3b0c44298fc1c14...
Archive SHA256:  a665a45920422f9d...
Session Directory SHA256: 2cf24dba5fb0a30e...
Seal Version: 0.2
```

The seal covers:
- **manifest_sha256** — the mission metadata file
- **archive_sha256** — the compressed `.zip` archive
- **session_directory_sha256** — a combined hash of every raw data file in the session folder

To verify an archive manually: compare its SHA256 against `archive_sha256` in `seal.json` or `seal.txt`.

### Mission Card

Each mission also creates a compact seal payload and a Markdown mission card that can be shared in issues, pull requests, release notes, or dashboards.

**`seal_payload.json`** — machine-readable compact snapshot:
```json
{
  "mission_id": "20260506_091500_pytest_baseline",
  "name": "pytest baseline",
  "status": "PASS",
  "exit_code": 0,
  "changed_files_count": 2,
  "archive_sha256": "a665a45920422f9d...",
  "seal_version": "0.2"
}
```

**`seal_card.md`** — shareable Markdown card:
```
# SPECIAL AGENT OPS MISSION CARD

Mission: pytest baseline
Mission ID: 20260506_091500_pytest_baseline
Status: PASS
Command: `python -m pytest`
Changed Files: 2
Archive SHA256: `a665a45920422f9d...`
Seal Version: 0.2

Recorded by Special Agent Ops.
```

---

## How it works

```
Mission Brief
  |
  v
Planner Agent
  |
  v
Human Approves Plan
  |
  v
Builder Agent opens PR
  |
  v
Safety Checks + No-Secrets Scan
  |
  v
Reviewer Agent + Diff Explainer
  |
  v
Human Merge
  |
  v
Release Notes + Rollback Plan
```

The point is simple: agents can move fast, but humans set the mission, approve the plan, review the PR, and own the merge.

---

## Start in 5 minutes

1. Copy the starter templates into your repo.
2. Fill out `MISSION_BRIEF.md` with one clear task and explicit out-of-scope items.
3. Define `SAFE_REPO_BOUNDARIES.md` so the agent knows what it can and cannot touch.
4. Run `python scripts/safety-gate.py --tree`.
5. Open a pull request for the agent-produced change.
6. Require human approval before merge.

Start with a docs-only mission if this is your first run. Then use the [`PR safety demo`](examples/pr-safety-demo/README.md) to see the full loop.

---

## What to copy first

- [`templates/MISSION_BRIEF.md`](templates/MISSION_BRIEF.md)
- [`templates/AGENT_RULES.md`](templates/AGENT_RULES.md)
- [`templates/SAFE_REPO_BOUNDARIES.md`](templates/SAFE_REPO_BOUNDARIES.md)
- [`templates/PR_CHECKLIST.md`](templates/PR_CHECKLIST.md)
- [`prompts/planner-agent.md`](prompts/planner-agent.md)
- [`prompts/codex-reviewer-agent.md`](prompts/codex-reviewer-agent.md)

---

## What this is

Special Agent Ops is a collection of templates, prompts, workflows, and scripts for developers and teams who want to use AI coding agents — Claude, Codex, v0, Devin-style tools — without losing control of their codebase.

The core idea: **give every agent a mission, a boundary, a review gate, and an off switch.**

This repo helps you coordinate:
- Claude (mobile, web, and Claude Code local)
- GitHub Copilot / Codex
- v0 (UI generation)
- Devin-style autonomous agents
- GitHub PR workflows
- Local and private repo workflows

## What this is not

- Not a framework or SDK you install
- Not an autonomous coding system
- Not a replacement for human developers
- Not a claim that agents are reliable enough to ship unsupervised
- Not hype

## Why AI agent control matters

AI coding agents are fast, capable, and increasingly useful. They can also:

- Write plausible-looking code that does the wrong thing
- Accidentally expose API keys or secrets
- Delete or overwrite things outside their intended scope
- Make large sweeping changes that are difficult to review
- Chain multiple actions in ways the original prompt never intended

The problem isn't the agents themselves. The problem is treating them like autonomous contractors when they should be treated more like fast, tireless, occasionally overconfident collaborators — ones who need clear scope, structured supervision, and a human review before anything merges.

This repo gives you the scaffolding to do that well.

---

## Agent Roster

Each "agent" is a role — a focused job assigned to one AI session, tool, or workflow step. No single agent owns the whole codebase.

| Role | Job | Typical Tool |
|---|---|---|
| **Planner Agent** | Break task into scoped sub-tasks, identify risks, produce mission brief | Claude web / mobile |
| **Builder Agent** | Implement one scoped task on a branch | Claude Code local / Codex |
| **Reviewer Agent** | Review the PR diff, flag logic issues and regressions | Claude web / GitHub Copilot |
| **Safety Gate Agent** | Check for secrets, risky file paths, deletion-heavy changes | `scripts/safety-gate.py` + Claude |
| **Diff Explainer Agent** | Produce plain-English summary of every change in the PR | Claude web / mobile |
| **Test Runner Agent** | Run tests, report failures, suggest missing test cases | Claude Code / CI |
| **No-Secrets Agent** | Scan staged files and diff for leaked credentials | `scripts/check-secrets.*` |
| **Release Manager Agent** | Draft release notes, tag version, confirm deploy checklist | Claude web |
| **Rollback Agent** | Identify rollback path, produce revert instructions if deploy fails | Claude web / mobile |

See [`docs/agent-roster.md`](docs/agent-roster.md) for full role descriptions and example prompts for each.

---

## Mission Flow

Every task follows the same sequence. No skipping steps.

```
Task idea
   │
   ▼
Mission Brief (fill MISSION_BRIEF.md template)
   │
   ▼
Planner Agent → scoped sub-tasks + risk flags
   │
   ▼
Human approval ← GATE 1
   │
   ▼
Builder Agent works on feature branch
   │
   ▼
No-Secrets Agent scans staged changes
   │
   ▼
Safety Gate Agent reviews diff for risky paths
   │
   ▼
Test Runner Agent confirms tests pass
   │
   ▼
Pull Request opened
   │
   ▼
Reviewer Agent + Diff Explainer Agent
   │
   ▼
Human review and merge ← GATE 2
   │
   ▼
Release Manager Agent drafts release notes
   │
   ▼
Rollback plan documented before deploy
```

See [`docs/mission-flow.md`](docs/mission-flow.md) for detailed step descriptions.

---

## Risk Levels

Assign a risk level to every mission before you start. The level determines how much human oversight is required.

| Level | Colour | Meaning | Example |
|---|---|---|---|
| **GREEN** | 🟢 | Low risk, well-scoped, easy to revert | Fix a typo in docs, update a README, add a comment |
| **AMBER** | 🟡 | Moderate risk, requires PR review | New feature on a branch, refactor of isolated module |
| **RED** | 🔴 | High risk, requires human sign-off before and after | Auth changes, payment code, database migrations |
| **BLACK** | ⬛ | Do not delegate to an AI agent | Production secrets, live trading logic, compliance-critical code |

BLACK-level code should never be handed to an agent session, even with instructions not to touch it. Remove it from context entirely.

See [`docs/risk-levels.md`](docs/risk-levels.md) for detailed guidance.

---

## Public vs Private Repos

The workflow is the same, but the risks are different.

**Public repos:** Agents can accidentally expose internal file structures, unfinished features, or organisation-specific naming conventions in commits, PR descriptions, and comments. Review everything before it's public.

**Private repos:** Secrets leaking into git history are still a real risk. Private does not mean safe. Run the no-secrets check regardless.

See [`docs/public-vs-private-repos.md`](docs/public-vs-private-repos.md) for a full breakdown.

---

## Example Prompts

Each prompt file in [`prompts/`](prompts/) is a ready-to-use system prompt or instruction block for a specific agent role.

| Prompt file | Use it when |
|---|---|
| [`prompts/planner-agent.md`](prompts/planner-agent.md) | Starting a new task, need a scoped plan |
| [`prompts/builder-agent.md`](prompts/builder-agent.md) | Handing a scoped task to Claude Code or Codex |
| [`prompts/codex-reviewer-agent.md`](prompts/codex-reviewer-agent.md) | Reviewing Codex-generated output |
| [`prompts/safety-gate-agent.md`](prompts/safety-gate-agent.md) | Running a pre-merge safety check |
| [`prompts/diff-explainer-agent.md`](prompts/diff-explainer-agent.md) | Getting a plain-English diff summary |
| [`prompts/test-runner-agent.md`](prompts/test-runner-agent.md) | Confirming test coverage and results |
| [`prompts/no-secrets-agent.md`](prompts/no-secrets-agent.md) | Scanning for leaked credentials |
| [`prompts/release-manager-agent.md`](prompts/release-manager-agent.md) | Drafting release notes |
| [`prompts/rollback-agent.md`](prompts/rollback-agent.md) | Identifying rollback path before deploy |

---

## Scripts

| Script | What it does |
|---|---|
| [`scripts/safety-gate.py`](scripts/safety-gate.py) | Scans git diff or working tree for risky paths and patterns. Outputs PASS / WARN / BLOCK. No dependencies. |
| [`scripts/check-secrets.sh`](scripts/check-secrets.sh) | Bash script: reports likely secrets and risky secret file names. Exits nonzero on findings. No writes. |
| [`scripts/check-secrets.ps1`](scripts/check-secrets.ps1) | PowerShell equivalent for Windows workflows. Reports only; never deletes or modifies files. |

---

## Automated safety checks

GitHub Actions runs the same core checks on every push and pull request:

- Python safety gate: `python scripts/safety-gate.py --tree`
- PowerShell secrets check: `pwsh ./scripts/check-secrets.ps1 -All`
- Bash secrets check: `bash scripts/check-secrets.sh --all`

PRs should pass all three before human review and merge. CI is a filter, not an approval gate: a human still reads the mission, the diff, the safety results, and the rollback notes before merging.

See [`docs/ci-safety-checks.md`](docs/ci-safety-checks.md) for failure handling and review guidance.
See [`docs/branch-protection.md`](docs/branch-protection.md) for making these checks required before merge.

---

## Workflow Examples

| Example | Risk level | Description |
|---|---|---|
| [`examples/docs-only-task/`](examples/docs-only-task/README.md) | 🟢 GREEN | Update documentation with no code changes |
| [`examples/frontend-polish-task/`](examples/frontend-polish-task/README.md) | 🟡 AMBER | UI copy and style tweaks via PR |
| [`examples/safe-bugfix-task/`](examples/safe-bugfix-task/README.md) | 🟡 AMBER | Fix a scoped non-auth bug on a branch |
| [`examples/pr-review-task/`](examples/pr-review-task/README.md) | 🟢 GREEN | Use an agent to review a PR diff before human merge |
| [`examples/pr-safety-demo/`](examples/pr-safety-demo/README.md) | 🟡 AMBER | Walk through mission, safety checks, review, approval, and rollback |

---

## Release Readiness

For v0.1.0 launch prep, use:

- [`RELEASE_NOTES.md`](RELEASE_NOTES.md) for included scope, verification steps, limitations, and roadmap.
- [`docs/launch-checklist.md`](docs/launch-checklist.md) for repo metadata, pre-launch checks, release steps, and post-launch checks.

Do not publish a release, announcement, or launch post until the safety gate and no-secrets checks pass cleanly.

---

## Roadmap

These are directions the project could grow. Contributions welcome.

- [ ] Agent coordination diagram (visual)
- [ ] Checklist for onboarding a new AI tool to an existing team workflow
- [ ] Guidance for multi-agent handoff (agent A passes output to agent B)
- [ ] Integration examples: Linear, Jira, Notion as task sources
- [ ] Example configs for Claude Code project files (`.claude/`)
- [ ] Checklist for regulated industries (finance, health, legal)

---

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Security

See [`SECURITY.md`](SECURITY.md).

## License

MIT — see [`LICENSE`](LICENSE).

---

*Special Agent Ops is a community resource, not a product. There is no guarantee that following these workflows will prevent all agent mistakes. Human review is always required.*
