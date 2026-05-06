# Special Agent Ops — Roadmap

This document tracks planned future work. Items are grouped by intended release milestone. Priorities may shift based on community feedback.

---

## v1.1 — QR Image Support

**Goal:** Generate an actual QR code image from the compact seal payload so missions can be printed, embedded in reports, or scanned from a screen.

- [ ] Add `sao/blackbox/qr_image.py` using `qrcode` (optional dependency, gracefully absent)
- [ ] Detect at runtime whether `qrcode` is installed; skip silently if not
- [ ] Write `seal_qr_payload.png` into the session folder when available
- [ ] Add `seal_card.html` embed (inline base64 PNG) when the image exists
- [ ] Update `_DIR_HASH_EXCLUDE` to exclude `seal_qr_payload.png` (derived file)
- [ ] Update session file table in README

**Why:** The compact payload text already fits a QR code — generating the image is the last step to make it scannable without third-party tooling.

---

## v1.2 — MapRoom Repo Graph

**Goal:** Produce a visual map of mission activity across branches and time — a "control room" view of the repository's agent history.

- [ ] Parse all `manifest.json` files in `blackbox/sessions/`
- [ ] Group missions by git branch and date
- [ ] Generate a standalone SVG or HTML timeline (no external charting libs)
- [ ] Add `sao map` CLI command that writes `blackbox/maproom.html`
- [ ] Link each mission node to its dashboard URL

**Why:** When many agents work in parallel across branches, a visual timeline makes it easier to review what happened and in what order.

---

## v1.3 — Agent Wrappers

**Goal:** Thin wrappers that automatically call `sao run` around common AI coding agent invocations so every agent session is recorded without manual intervention.

- [ ] `sao wrap claude-code` — wraps a Claude Code CLI session
- [ ] `sao wrap codex` — wraps an OpenAI Codex CLI session
- [ ] Generic `sao wrap <command>` — records any subprocess session
- [ ] Optional: `--mission-brief <file>` to attach a MISSION_BRIEF to the recorded session

**Why:** Currently the user must explicitly call `sao run`. Wrappers make recording automatic and invisible.

---

## v1.4 — CI and GitHub PR Reports

**Goal:** Make mission records visible inside GitHub pull requests so reviewers can see what the agent did without leaving GitHub.

- [ ] GitHub Actions step that uploads a mission archive as a workflow artifact
- [ ] PR comment bot that posts the `seal_card.md` content as a PR comment
- [ ] Optional: post the HTML card as a GitHub Gist and link it in the PR
- [ ] Badge showing PASS/FAIL seal status in the PR description

**Why:** The most useful place to see what an agent did is the pull request it produced. Bringing the mission card into the PR closes the loop.

---

## Later — Compressed Binary Event Stream

**Goal:** Replace plain-text session files with a compact binary event log for high-frequency agent sessions that produce large amounts of output.

- [ ] Evaluate MessagePack, CBOR, and Zstandard for frame compression
- [ ] Design an append-only event log format (start, stdout chunk, stderr chunk, git event, end)
- [ ] Add `sao replay <mission_id>` to reconstruct stdout/stderr from the log
- [ ] Maintain backward compatibility with the existing plain-text session format
- [ ] Define a migration path for existing sessions

**Why:** For agents that run many commands in a single session, plain-text files become large quickly. A compressed binary log reduces storage by 10–50x while preserving full replay capability.

---

*To suggest a roadmap item, open an issue at https://github.com/gorillapioneer/special-agent-ops/issues*
