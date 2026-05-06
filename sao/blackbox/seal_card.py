"""
seal_card.py — compact shareable payload and Markdown mission card.

After the SHA256 seal is written, build_seal_payload() assembles a
lightweight JSON snapshot that contains only the fields needed to share
or display a mission result.  render_seal_card() turns that payload into
a Markdown card suitable for pasting into a GitHub issue, PR, release
note, or future dashboard.

Stdlib only — no external dependencies.
"""

from __future__ import annotations

import json
from pathlib import Path


# ── Payload builder ───────────────────────────────────────────────────────────

def build_seal_payload(
    manifest: dict,
    seal: dict,
    exit_code: int,
    changed_files: list[str],
) -> dict:
    """Return a compact dict summarising the mission for sharing.

    Parameters
    ----------
    manifest:       Full manifest dict from recorder.py.
    seal:           Seal dict returned by seal.write_seal().
    exit_code:      Integer exit code of the recorded command.
    changed_files:  List of files that changed during the mission.
    """
    return {
        "mission_id":          manifest["mission_id"],
        "name":                manifest["name"],
        "repo_path":           manifest["repo_path"],
        "started_at":          manifest["started_at"],
        "ended_at":            manifest["ended_at"],
        "command":             manifest["command"],
        "exit_code":           exit_code,
        "status":              "PASS" if exit_code == 0 else "FAIL",
        "changed_files_count": len(changed_files),
        "archive_sha256":      seal["archive_sha256"],
        "seal_version":        seal["seal_version"],
    }


# ── Card renderer ─────────────────────────────────────────────────────────────

def render_seal_card(payload: dict) -> str:
    """Return a Markdown string for the mission seal card.

    The card is intentionally minimal so it can be pasted anywhere without
    overwhelming the reader — just the facts needed to identify and verify
    the mission.
    """
    status = payload.get("status", "UNKNOWN")
    return (
        f"# SPECIAL AGENT OPS MISSION CARD\n"
        f"\n"
        f"Mission: {payload['name']}\n"
        f"Mission ID: {payload['mission_id']}\n"
        f"Status: {status}\n"
        f"Command: `{payload['command']}`\n"
        f"Changed Files: {payload['changed_files_count']}\n"
        f"Archive SHA256: `{payload['archive_sha256']}`\n"
        f"Seal Version: {payload['seal_version']}\n"
        f"\n"
        f"Recorded by Special Agent Ops.\n"
    )


# ── Writer ────────────────────────────────────────────────────────────────────

def write_seal_card(session_dir: Path, payload: dict) -> dict:
    """Write seal_payload.json and seal_card.md into *session_dir*.

    Parameters
    ----------
    session_dir:  The mission session folder.
    payload:      Compact payload dict from build_seal_payload().

    Returns
    -------
    dict with keys seal_payload_path and seal_card_path (both Path objects).
    """
    payload_path = session_dir / "seal_payload.json"
    card_path = session_dir / "seal_card.md"

    payload_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    card_path.write_text(render_seal_card(payload), encoding="utf-8")

    return {
        "seal_payload_path": payload_path,
        "seal_card_path": card_path,
    }
