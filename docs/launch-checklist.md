# Launch Checklist

Use this checklist before publishing the repository, tagging v0.1.0, or posting
about Special Agent Ops publicly.

Do not post publicly until every required check below passes.

## Repo Description

Mission control for AI coding agents: give every agent a mission, a boundary, a
review gate, and an off switch.

## Suggested GitHub Topics

- ai-agents
- ai-coding
- ai-safety
- code-review
- developer-tools
- devops
- github-workflow
- prompts
- pull-requests
- secure-coding

## Pre-Launch Checks

- [ ] README has a clear one-line description and no overhyped autonomy claims.
- [ ] README quick start explains templates, boundaries, PR review, and local
      checks.
- [ ] Docs and templates use safe placeholders, not real credentials or
      destructive command examples.
- [ ] Internal Markdown links resolve.
- [ ] Scanner scripts are dependency-light and use only Python stdlib,
      PowerShell built-ins, or Bash plus common Unix tools.
- [ ] Safety scanners have not been weakened to make launch pass.
- [ ] The working tree has no accidental local-only files staged for release.
- [ ] License, contributing guidance, and security policy are present.

## Release Checklist

- [ ] Run `python scripts/safety-gate.py --tree`.
- [ ] Run `pwsh scripts/check-secrets.ps1 -All`.
- [ ] Run `bash scripts/check-secrets.sh --all` where Bash is available.
- [ ] Confirm all required checks report PASS or CLEAN.
- [ ] Review `RELEASE_NOTES.md` for v0.1.0 scope, limitations, and roadmap.
- [ ] Confirm version tag is `v0.1.0`.
- [ ] Create the GitHub release from `RELEASE_NOTES.md`.
- [ ] Do a final human review of the public GitHub page before sharing.

## Post-Launch Checks

- [ ] Confirm README, docs, templates, prompts, examples, and scripts render
      correctly on GitHub.
- [ ] Confirm suggested topics were added.
- [ ] Confirm the release notes link is visible from the repo landing page.
- [ ] Watch the first issues and PRs for confusing instructions or repeated
      questions.
- [ ] Track scanner false positives and improve narrowly, without removing
      useful detection patterns.
- [ ] Keep launch posts practical: this is a control kit for human-reviewed
      agent work, not a promise of safe autonomous coding.

