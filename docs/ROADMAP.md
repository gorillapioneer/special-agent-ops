# Special Agent Ops - Roadmap

This document tracks completed and planned work. Items are grouped by release milestone. Priorities may shift based on community feedback.

---

## Completed

### v1.1 - QR Image Support

**Goal:** Generate an actual QR code image from the compact seal payload so missions can be printed, embedded in reports, or scanned from a screen.

- [x] Add `sao/blackbox/qr_image.py` using `qrcode[pil]`
- [x] Write `seal_qr.png` into each session folder
- [x] Display `seal_qr.png` from `seal_card.html`
- [x] Serve `seal_qr.png` from the local dashboard through a validated route
- [x] Exclude `seal_qr.png` from `session_directory_sha256` because it is a derived artifact
- [x] Update README and security model docs

**Why:** The compact payload text already fits a QR code. Generating the image makes it scannable without third-party tooling.

---

### v1.2 - Agent Wrapper CLI

**Goal:** Add a safer wrapper command that records agents and local commands without requiring users to build shell strings.

- [x] Add `sao wrap --name "..." -- <command> [args...]`
- [x] Run wrapped commands as argv lists with `shell=False`
- [x] Keep legacy `sao run --command "..."` for shell-string workflows
- [x] Record `command_mode` in each mission manifest
- [x] Record `command_argv` for wrapped missions
- [x] Keep mission summaries, cards, dashboard, and browser output readable

**Why:** Argument-list execution avoids shell parsing surprises while preserving the existing mission recording pipeline.

---

### v1.3 - Agent Integration Docs

**Goal:** Provide copy/paste documentation for common AI coding agents and local verification workflows.

- [x] Add `docs/AGENT_INTEGRATIONS.md`
- [x] Document Claude Code, Codex, Devin-style agents, Cursor/Copilot workflows, and generic local commands
- [x] Explain when to use `sao wrap` instead of `sao run`
- [x] Add a recommended agent workflow for PR handoff
- [x] Link integration examples from README

**Why:** Clear examples make it easier to record real agent work consistently across different tools.

---

### v1.4 - MapRoom Repo Graph

**Goal:** Produce a visual map of mission activity across branches and time - a control-room view of the repository's agent history.

- [x] Parse all `manifest.json` files in `blackbox/sessions/`
- [x] Group missions by git branch and date
- [x] Generate a standalone HTML timeline with no external charting libraries
- [x] Add `sao map` CLI command that writes `blackbox/maproom.html`
- [x] Link each mission row to local card, summary, and QR image files when present

**Why:** When many agents work in parallel across branches, a visual timeline makes it easier to review what happened and in what order.

---

### v1.5 - PR Mission Reports

**Goal:** Generate a paste-ready Markdown report from a recorded mission for GitHub pull requests.

- [x] Add `sao pr-report <mission_id>`
- [x] Print PR-ready Markdown to stdout by default
- [x] Support `--output <path>` for writing the report to a file
- [x] Include mission summary, verification commands, changed files, and local artifact paths
- [x] Avoid embedding stdout, stderr, diffs, secrets, or archive contents

**Why:** Reviewers need a compact summary of what the agent ran and how to verify it without digging through local session folders.

---

## Planned

### v1.6 - CI and GitHub PR Reports

**Goal:** Make mission records visible inside GitHub pull requests so reviewers can see what the agent did without leaving GitHub.

- [ ] GitHub Actions step that uploads a mission archive as a workflow artifact
- [ ] PR comment bot that posts the `seal_card.md` content as a PR comment
- [ ] Optional: post the HTML card as a GitHub Gist and link it in the PR
- [ ] Badge showing PASS/FAIL seal status in the PR description

**Why:** The most useful place to see what an agent did is the pull request it produced. Bringing the mission card into the PR closes the loop.

---

## Later - Compressed Binary Event Stream

**Goal:** Replace plain-text session files with a compact binary event log for high-frequency agent sessions that produce large amounts of output.

- [ ] Evaluate MessagePack, CBOR, and Zstandard for frame compression
- [ ] Design an append-only event log format (start, stdout chunk, stderr chunk, git event, end)
- [ ] Add `sao replay <mission_id>` to reconstruct stdout/stderr from the log
- [ ] Maintain backward compatibility with the existing plain-text session format
- [ ] Define a migration path for existing sessions

**Why:** For agents that run many commands in a single session, plain-text files become large quickly. A compressed binary log reduces storage while preserving full replay capability.

---

*To suggest a roadmap item, open an issue at https://github.com/gorillapioneer/special-agent-ops/issues*
