# Agent Integrations

## Core idea

Special Agent Ops can wrap any local command or AI coding agent with:

```bash
sao wrap --name "mission name" -- <command> [args...]
```

This records stdout, stderr, git diff, exit code, mission seal, QR image, archive, HTML card, and dashboard entry.

## Claude Code

Example:

```bash
sao wrap --name "claude code mission" -- claude
```

Use the actual Claude Code command installed on your system. If your command is different, replace `claude`.

## Codex

Example:

```bash
sao wrap --name "codex mission" -- codex
```

Use your local Codex CLI command. If Codex runs inside another launcher, wrap that launcher command instead.

## Devin-style agents

Example:

```bash
sao wrap --name "devin style agent mission" -- your-agent-command
```

For remote agents, use Special Agent Ops around any local handoff script, test runner, sync command, or verification command.

## Cursor / Copilot / manual shell workflows

Example:

```bash
sao wrap --name "manual fix verification" -- python -m pytest
```

Even if the code was written in an editor, SAO can still record the verification and diff.

## Generic Command

Examples:

```bash
sao wrap --name "pytest" -- python -m pytest
sao wrap --name "repo validation" -- python scripts/safety-gate.py --tree
sao wrap --name "node tests" -- npm test
```

## Why wrap beats run

- `sao run` accepts a shell string.
- `sao wrap` accepts argv after `--`.
- `sao wrap` uses `shell=False`.
- `sao wrap` is safer for arguments and avoids shell parsing surprises.

Use `sao run` when you intentionally need shell features such as pipes, redirects, environment assignment, or compound commands.

## Recommended agent workflow

1. Create a branch.
2. Ask the agent to make changes.
3. Run `sao wrap` around the test/validation command or agent invocation.
4. Run `sao verify <mission_id>`.
5. Open the HTML card.
6. Link the mission card in the PR.
7. After several missions, run `sao map --open` to review the agent timeline.
