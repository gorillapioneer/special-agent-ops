# Changelog

All notable changes to Special Agent Ops are documented here.

---

## v1.0.0 — Initial public release candidate

### Included

| Version | Feature |
|---|---|
| v0.1 | **Black Box Recorder** — `sao run` records git state, stdout, stderr, changed files, and compresses everything into a timestamped archive |
| v0.2 | **Mission Seal** — SHA256 tamper-evident seal covering the manifest, archive, and session directory; written as `seal.json` + `seal.txt` |
| v0.3 | **Mission Card** — compact seal payload (`seal_payload.json`) and shareable Markdown card (`seal_card.md`) |
| v0.4 | **QR Seal Payload** — minimal compact JSON (`seal_qr_payload.json` + `seal_qr_payload.txt`) sized to fit a standard QR code |
| v0.5 | **Mission Browser CLI** — `sao list`, `sao show`, `sao verify` for inspecting and verifying recorded sessions |
| v0.6 | **Archive Verification** — `sao verify-archive` confirms SHA256 integrity of a `.zip` without needing the original session folder |
| v0.7 | **HTML Mission Card** — standalone dark-themed HTML card (`seal_card.html`) with no external assets, safe for attaching to GitHub issues and PRs |
| v0.8 | **Open Mission Card** — `sao open` launches `seal_card.html` in the system default browser via a `file://` URI |
| v0.9 | **Mini Dashboard** — `sao dashboard` starts a local `ThreadingHTTPServer` on `127.0.0.1` with a mission index and validated file routes |

### Design principles

- **No external dependencies** — stdlib only throughout.
- **Windows and Unix compatible** — tested on Windows Server 2025 and Ubuntu.
- **Immutable session records** — sessions are written once; the recorder never modifies an existing session.
- **Loopback-only dashboard** — `sao dashboard` binds to `127.0.0.1` and serves only an explicit allowlist of files.
- **Safety gate** — `scripts/safety-gate.py --tree` scans the working tree for risky patterns before every merge.

---

*Earlier development history is tracked in git log. Use `git log --oneline` to see the full commit history.*
