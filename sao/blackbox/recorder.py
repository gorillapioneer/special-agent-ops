"""
recorder.py — orchestrates one mission recording session end-to-end.

Flow:
    1. Generate a unique mission_id from timestamp + sanitised name.
    2. Create the session folder under blackbox/sessions/.
    3. Capture git state *before* running the command.
    4. Run the command with subprocess (shell=True for cross-platform support).
    5. Capture git state *after*.
    6. Write raw artefacts (manifest.json, stdout.txt, git_diff.patch, …).
    7. Compress the session folder into a .zip archive.
    8. Write the SHA256 seal (seal.json + seal.txt).
    9. Write mission_summary.md (includes seal hashes).
   10. Return a result dict so the CLI can print the summary.

No external dependencies — stdlib only.
"""

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import compressor, git_tools, seal as seal_mod, summary


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sanitise_name(name: str) -> str:
    """Turn a free-text mission name into a safe path component."""
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)   # non-alphanumeric → underscore
    slug = slug.strip("_")
    return slug[:40]                            # cap at 40 chars


def _make_mission_id(name: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{ts}_{_sanitise_name(name)}"


def _run_command(command: str, cwd=None):
    """Execute *command* in a shell. Returns (stdout, stderr, exit_code).

    shell=True is intentional here: the caller provides the full shell command
    string (e.g. "python -m pytest -x"), and we want the shell to resolve it
    exactly as the user typed it — on Windows this uses cmd.exe, on Unix /bin/sh.
    """
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return proc.stdout, proc.stderr, proc.returncode
    except Exception as exc:
        return "", f"Failed to start command: {exc}", 1


# ── Public API ────────────────────────────────────────────────────────────────

def record_mission(name: str, command: str, repo_path: Path = None) -> dict:
    """Run *command*, record everything, and return a result dict.

    Parameters
    ----------
    name:       Human-readable label for this mission (used in mission_id).
    command:    Shell command to execute (e.g. ``"python -m pytest"``).
    repo_path:  Root directory of the project; defaults to the current directory.

    Returns
    -------
    dict with keys: mission_id, command, exit_code, changed_files_count,
    session_dir (Path), zip_path (Path), seal_path (Path), archive_sha256 (str).
    """
    if repo_path is None:
        repo_path = Path.cwd()

    mission_id = _make_mission_id(name)
    sessions_dir = repo_path / "blackbox" / "sessions"
    session_dir = sessions_dir / mission_id
    session_dir.mkdir(parents=True, exist_ok=True)

    # Prefix used to exclude the recorder's own output files from the
    # "changed files" list (they aren't part of what the command changed).
    sessions_prefix = "blackbox/sessions/"

    # ── Git state before ──────────────────────────────────────────────────────
    branch = git_tools.get_branch(cwd=repo_path)
    commit_before = git_tools.get_commit(cwd=repo_path)
    status_before = git_tools.get_status_short(cwd=repo_path)

    # ── Run the command ───────────────────────────────────────────────────────
    started_at = datetime.now(timezone.utc)
    stdout_text, stderr_text, exit_code = _run_command(command, cwd=repo_path)
    ended_at = datetime.now(timezone.utc)
    duration_seconds = (ended_at - started_at).total_seconds()

    # ── Git state after ───────────────────────────────────────────────────────
    commit_after = git_tools.get_commit(cwd=repo_path)
    status_after = git_tools.get_status_short(cwd=repo_path)
    diff_text = git_tools.get_diff(cwd=repo_path)
    changed_files = git_tools.get_changed_files(
        cwd=repo_path, exclude_prefix=sessions_prefix
    )

    # ── Build manifest ────────────────────────────────────────────────────────
    manifest = {
        "mission_id": mission_id,
        "name": name,
        "repo_path": str(repo_path),
        "command": command,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "duration_seconds": round(duration_seconds, 3),
        "exit_code": exit_code,
        "git_branch": branch,
        "git_commit_before": commit_before,
        "git_commit_after": commit_after,
        "changed_files_count": len(changed_files),
        "changed_files": changed_files,
    }

    # ── Write raw artefacts (no summary yet — seal comes first) ──────────────
    manifest_path = session_dir / "manifest.json"

    def _write(filename: str, content: str) -> None:
        (session_dir / filename).write_text(content, encoding="utf-8")

    _write("manifest.json",         json.dumps(manifest, indent=2))
    _write("stdout.txt",            stdout_text)
    _write("stderr.txt",            stderr_text)
    _write("git_status_before.txt", status_before)
    _write("git_status_after.txt",  status_after)
    _write("git_diff.patch",        diff_text)

    # ── Compress ──────────────────────────────────────────────────────────────
    # Compress before sealing so the archive SHA256 goes into the seal.
    zip_path = compressor.compress_session(session_dir)

    # ── Seal ──────────────────────────────────────────────────────────────────
    # The seal hashes the raw data files and the archive.
    # mission_summary.md is excluded from the directory hash (it is written
    # next and contains the seal hashes — avoiding a circular dependency).
    seal_data = seal_mod.write_seal(
        session_dir=session_dir,
        archive_path=zip_path,
        manifest_path=manifest_path,
    )
    seal_path = session_dir / "seal.json"

    # ── Summary (written last so it can reference the seal) ───────────────────
    _write(
        "mission_summary.md",
        summary.generate_summary(manifest, stdout_text, stderr_text, seal=seal_data),
    )

    return {
        "mission_id": mission_id,
        "command": command,
        "exit_code": exit_code,
        "changed_files_count": len(changed_files),
        "session_dir": session_dir,
        "zip_path": zip_path,
        "seal_path": seal_path,
        "archive_sha256": seal_data["archive_sha256"],
    }
