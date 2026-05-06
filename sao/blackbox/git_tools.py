"""
git_tools.py — helpers for capturing git state before and after a mission.

All functions return strings (empty string when git is unavailable or the
repo has no commits yet). Nothing here modifies the repo.
"""

import subprocess
from pathlib import Path


def _run(cmd: list, cwd=None) -> str:
    """Run a git command; return stdout. Returns '' on any error."""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return result.stdout
    except (FileNotFoundError, OSError):
        return ""


def get_branch(cwd=None) -> str:
    out = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd).strip()
    return out or "unknown"


def get_commit(cwd=None) -> str:
    out = _run(["git", "rev-parse", "HEAD"], cwd=cwd).strip()
    return out or "unknown"


def get_status_short(cwd=None) -> str:
    return _run(["git", "status", "--short"], cwd=cwd)


def get_diff(cwd=None) -> str:
    """Return unified diff of all uncommitted changes (staged + unstaged)."""
    return _run(["git", "diff", "HEAD"], cwd=cwd)


def get_changed_files(cwd=None, exclude_prefix: str = "") -> list:
    """Return list of files that differ from HEAD, plus untracked files.

    Files under *exclude_prefix* (e.g. 'blackbox/sessions/') are omitted so
    the recorder's own output files don't appear in the mission change list.
    """
    # Modified/staged files vs HEAD
    out = _run(["git", "diff", "HEAD", "--name-only"], cwd=cwd)
    files = [f.strip() for f in out.splitlines() if f.strip()]

    # Untracked files (shown as "?? path" in --short output)
    status = _run(["git", "status", "--short"], cwd=cwd)
    for line in status.splitlines():
        if line.startswith("??"):
            fname = line[3:].strip().rstrip("/")
            if fname not in files:
                files.append(fname)

    if exclude_prefix:
        prefix = exclude_prefix.replace("\\", "/")
        files = [f for f in files if not f.replace("\\", "/").startswith(prefix)]

    return files
