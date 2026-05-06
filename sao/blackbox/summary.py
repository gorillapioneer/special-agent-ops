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
) -> str:
    """Return a markdown string summarising one mission session."""
    m = manifest
    exit_code = m.get("exit_code", "?")
    result_label = "[PASS]" if exit_code == 0 else f"[FAIL — exit {exit_code}]"
    changed = m.get("changed_files", [])

    sections = []

    # ── Header ────────────────────────────────────────────────────────────────
    sections.append(f"# Mission Summary: {m['name']}\n")
    sections.append(
        "\n".join([
            f"| Field | Value |",
            f"|---|---|",
            f"| Mission ID | `{m['mission_id']}` |",
            f"| Result | **{result_label}** |",
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
