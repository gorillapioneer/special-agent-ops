"""
flightplan.py — pre-declared mission scope.

A flight plan is filed BEFORE a mission runs:

    sao flight-plan --name "..." --intent "..." --scope "sao/**" --scope "tests/**"

It is stored as ``blackbox/flightplan.pending.json``.  The next recorded
mission consumes it: the plan is copied into the session folder as
``flightplan.json`` BEFORE sealing (so it is covered by the seal and the
archive) and its sha256 is referenced by the mission's attestation.

Scope semantics: fnmatch-style globs matched against repo-relative POSIX
paths of files changed during the mission.  ``**`` is treated as "any path
segment(s)" (fnmatch's ``*`` already crosses ``/``, so globs behave like
recursive globs).  Paths under ``blackbox/`` (the recorder's own artefacts)
are always considered in scope.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path

FLIGHTPLAN_VERSION = "sao-flightplan/1"
PENDING_FILENAME = "flightplan.pending.json"
SESSION_FILENAME = "flightplan.json"

# Recorder-owned output paths, always in scope regardless of the plan.
_ALWAYS_IN_SCOPE_PREFIX = "blackbox/"


def pending_path(repo_path: Path) -> Path:
    return Path(repo_path) / "blackbox" / PENDING_FILENAME


def file_flight_plan(
    repo_path: Path,
    name: str,
    intent: str,
    scope: list,
) -> Path:
    """Write blackbox/flightplan.pending.json and return its path."""
    if not scope:
        raise ValueError("A flight plan needs at least one --scope glob")
    plan = {
        "version": FLIGHTPLAN_VERSION,
        "name": name,
        "intent": intent,
        "scope": list(scope),
        "filed_at": datetime.now(timezone.utc).isoformat(),
    }
    path = pending_path(repo_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    return path


def load_pending(repo_path: Path):
    """Return the pending flight plan dict, or None."""
    path = pending_path(repo_path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def consume_pending(repo_path: Path, session_dir: Path):
    """Move a pending flight plan into *session_dir* as flightplan.json.

    Called by the recorder BEFORE sealing, so the plan is covered by the
    seal's directory hash and included in the mission archive.
    Returns the plan dict, or None when no pending plan exists.
    """
    plan = load_pending(repo_path)
    if plan is None:
        return None
    plan = dict(plan)
    plan["consumed_at"] = datetime.now(timezone.utc).isoformat()
    (Path(session_dir) / SESSION_FILENAME).write_text(
        json.dumps(plan, indent=2), encoding="utf-8"
    )
    pending_path(repo_path).unlink()
    return plan


def load_session_plan(session_dir: Path):
    """Return the consumed flight plan for a session, or None."""
    path = Path(session_dir) / SESSION_FILENAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


# ── Scope checking ────────────────────────────────────────────────────────────

def path_in_scope(path: str, globs: list) -> bool:
    """True when *path* matches any scope glob (or is a recorder artefact)."""
    norm = path.replace("\\", "/")
    if norm == "blackbox" or norm.startswith(_ALWAYS_IN_SCOPE_PREFIX):
        return True
    for pattern in globs:
        pattern = pattern.replace("\\", "/")
        if fnmatch(norm, pattern):
            return True
        # Convenience: "dir/**" also matches "dir" itself and fnmatch's *
        # already spans "/", so "**" and "*" behave identically here.
    return False


def check_scope(changed_files: list, globs: list) -> dict:
    """Classify changed files against scope globs.

    Returns {"in_scope": [...], "out_of_scope": [...], "ok": bool}.
    """
    in_scope, out_of_scope = [], []
    for f in changed_files:
        (in_scope if path_in_scope(f, globs) else out_of_scope).append(f)
    return {"in_scope": in_scope, "out_of_scope": out_of_scope, "ok": not out_of_scope}
