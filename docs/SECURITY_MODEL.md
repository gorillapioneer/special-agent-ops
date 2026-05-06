# Special Agent Ops — Security Model

This document explains what Special Agent Ops records, what it protects, what it does not protect, and how verification works.

---

## What the tool records

When you run `sao run --name "..." --command "..."`, the recorder captures:

| Artefact | What it contains |
|---|---|
| `manifest.json` | Mission name, command string, timestamps, exit code, git branch, commit hashes, changed file list |
| `stdout.txt` | The complete standard output of the command |
| `stderr.txt` | The complete standard error of the command |
| `git_status_before.txt` | `git status --short` before the command ran |
| `git_status_after.txt` | `git status --short` after the command ran |
| `git_diff.patch` | Unified diff of all uncommitted changes at the time of capture |

These six files are compressed into `<mission_id>.zip`. After compression, SHA256 hashes are computed and written to `seal.json`.

---

## What the seal proves

The seal (`seal.json`) contains three SHA256 hashes:

| Hash | What it covers |
|---|---|
| `manifest_sha256` | The `manifest.json` file |
| `archive_sha256` | The `.zip` archive containing the six raw data files |
| `session_directory_sha256` | A combined hash of every raw data file in the session folder (same six files, deterministic sorted order) |

**If any of these hashes match after re-computation, the covered files have not been modified since the session was recorded.**

Running `sao verify <mission_id>` or `sao verify-archive <path>.zip` recomputes all three hashes and compares them to `seal.json`. A VERIFIED result means the raw data files are byte-for-byte identical to what was written at record time.

---

## What the seal does not prove

The seal is a tamper-evidence mechanism, not a trust mechanism. It answers: *"has this file changed since it was recorded?"* It does not answer:

- **Whether the command itself was safe.** The command is user-supplied and executed in a shell (`shell=True`). A malicious or buggy command can do anything the user's shell can do.
- **Whether the output is accurate.** If the command produced incorrect output, the seal faithfully records that incorrect output.
- **Whether the agent's code changes are correct.** The diff captured in `git_diff.patch` shows what changed, but correctness requires human review.
- **Whether secrets were exposed.** The recorder captures stdout and stderr verbatim. If a command outputs an API key or token, that value will be in `stdout.txt`. Always run the no-secrets scan (`scripts/check-secrets.ps1` / `scripts/check-secrets.sh`) before sharing session archives.
- **Whether the recording machine was trusted.** If the host system was compromised, an attacker could alter files before the seal is computed.

---

## Shell command safety

`sao run` passes the `--command` string directly to the operating system shell (`shell=True`). This is by design — it allows the same syntax you use in your terminal.

**Never record commands supplied by untrusted sources.** Treat `--command` the same way you would treat `eval` in a script.

If you are recording agent-generated commands, review the command string before passing it to `sao run`. The mission brief workflow (`MISSION_BRIEF.md`) and safety gate (`scripts/safety-gate.py`) are designed to support this review step.

---

## Dashboard security

`sao dashboard` starts an HTTP server with the following constraints:

- **Loopback only.** The server binds to `127.0.0.1`. It is not accessible from other machines on the network.
- **Allowlisted files only.** Only three filenames can ever be served: `seal_card.html`, `mission_summary.md`, and `seal_qr_payload.txt`.
- **Validated session folders only.** The mission ID in the URL is validated against actual directories in `blackbox/sessions/`. Path traversal sequences (`..`, `/`, `\`) are rejected before any filesystem access.
- **No arbitrary file access.** There is no route that serves an arbitrary path. The server has no equivalent of a static file middleware.

The dashboard is intended for local inspection only. Do not expose it to untrusted networks (e.g., do not use `--host 0.0.0.0`).

---

## Why blackbox/sessions is gitignored

Session folders can contain:

- Full stdout and stderr output of commands (may include sensitive data)
- Git diffs (may reveal unpublished code or configuration)
- Timestamps and branch names that reveal workflow details

`blackbox/sessions/` is excluded from git by `.gitignore`. This is intentional. If you want to share a session, share the specific `.zip` archive and its `seal.json` — not the entire sessions directory.

---

## Why QR payloads contain only compact proof data

The QR payload (`seal_qr_payload.txt`) contains five fields:

```json
{"sao":"0.4","id":"...","status":"PASS","sha256":"...","seal":"0.2"}
```

It intentionally excludes:
- Command strings (may be long or contain sensitive arguments)
- File paths (machine-specific, not useful out of context)
- stdout/stderr content (too large for a QR code)
- Changed file lists (too large, available in the session folder)

The payload is proof-of-record, not a full record. The full record is in the session folder and archive.

---

## Responsible use

Special Agent Ops is a recording and verification tool. It is not a sandbox. It does not prevent agents from doing harmful things — it records what they did so humans can review it. Human review before merging agent-produced changes remains essential.

See [`templates/AGENT_RULES.md`](../templates/AGENT_RULES.md) and [`templates/SAFE_REPO_BOUNDARIES.md`](../templates/SAFE_REPO_BOUNDARIES.md) for guidance on scoping agent sessions before they run.
