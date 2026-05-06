"""
summary.py — generate the human-readable mission_summary.md.

Keeps output concise: stdout/stderr are capped at 4 000 characters each so
the summary stays readable without opening stdout.txt directly.
"""

from __future__ import annotations

_STDOUT_CAP = 4_000
_STDERR_CAP = 2_000


def _cap(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n... (truncated — full output in stdout.txt)"


def generate_summary(
    manifest: dict,
    stdout: str,
    stderr: str,
    seal: dict | None = None,
    card_paths: dict | None = None,
    qr_paths: dict | None = None,
    html_card_path=None,
) -> str:
    """Return a markdown string summarising one mission session."""
    m = manifest
    exit_code = m.get("exit_code", "?")
    status = "PASS" if exit_code == 0 else "FAIL"
    result_label = f"[{status}]" if exit_code == 0 else f"[{status} — exit {exit_code}]"
    changed = m.get("changed_files", [])

    sections = []

    # ── Header ────────────────────────────────────────────────────────────────
    sections.append(f"# Mission Summary: {m['name']}\n")
    sections.append(
        "\n".join([
            f"| Field | Value |",
            f"|---|---|",
            f"| Mission ID | `{m['mission_id']}` |",
            f"| Status | **{result_label}** |",
            f"| Command | `{m['command']}` |",
            f"| Branch | `{m.get('git_branch', 'unknown')}` |",
            f"| Started | {m['started_at']} |",
            f"| Ended | {m['ended_at']} |",
            f"| Duration | {m.get('duration_seconds', 0):.2f}s |",
        ])
    )

    # ── Git delta ─────────────────────────────────────────────────────────────
    sections.append("## Git delta\n")
    sections.append(
        "\n".join([
            f"- **Commit before:** `{m.get('git_commit_before', 'unknown')}`",
            f"- **Commit after:**  `{m.get('git_commit_after', 'unknown')}`",
            f"- **Changed files:** {len(changed)}",
        ])
    )

    if changed:
        file_list = "\n".join(f"  - `{f}`" for f in changed)
        sections.append(file_list)
    else:
        sections.append("  - *(no file changes detected)*")

    # ── Seal card references ──────────────────────────────────────────────────
    if card_paths:
        sections.append("## Mission Card\n")
        lines = [
            f"- **Status:** {status}",
            f"- **Seal Card:** `{card_paths.get('seal_card_path', 'n/a')}`",
            f"- **Seal Payload:** `{card_paths.get('seal_payload_path', 'n/a')}`",
        ]
        if qr_paths:
            lines.append(
                f"- **QR Payload:** `{qr_paths.get('qr_payload_json_path', 'n/a')}`"
            )
        if html_card_path:
            lines.append(f"- **HTML Card:** `{html_card_path}`")
        sections.append("\n".join(lines))

    # ── Seal ─────────────────────────────────────────────────────────────────
    if seal:
        sections.append("## Mission Seal\n")
        sections.append(
            "\n".join([
                f"| Field | Value |",
                f"|---|---|",
                f"| Seal Version | `{seal.get('seal_version', '?')}` |",
                f"| Archive SHA256 | `{seal.get('archive_sha256', '?')}` |",
                f"| Manifest SHA256 | `{seal.get('manifest_sha256', '?')}` |",
                f"| Session Dir SHA256 | `{seal.get('session_directory_sha256', '?')}` |",
                f"| Seal Created | {seal.get('created_at', '?')} |",
            ])
        )
        sections.append(
            "_To verify: compare Archive SHA256 against the .zip file on disk._"
        )

    # ── stdout ────────────────────────────────────────────────────────────────
    sections.append("## stdout\n")
    if stdout.strip():
        sections.append(f"```\n{_cap(stdout.strip(), _STDOUT_CAP)}\n```")
    else:
        sections.append("*(empty)*")

    # ── stderr ────────────────────────────────────────────────────────────────
    if stderr.strip():
        sections.append("## stderr\n")
        sections.append(f"```\n{_cap(stderr.strip(), _STDERR_CAP)}\n```")

    return "\n\n".join(sections) + "\n"
