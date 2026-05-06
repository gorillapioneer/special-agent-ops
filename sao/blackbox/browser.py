"""
browser.py — list, inspect, and verify recorded mission sessions.

All functions work against the blackbox/sessions/ directory tree.
No external dependencies — stdlib only.
"""

from __future__ import annotations

import json
from pathlib import Path

from .seal import sha256_file, sha256_directory


# ── Root helper ───────────────────────────────────────────────────────────────

def get_sessions_root(repo_path: Path | None = None) -> Path:
    """Return the blackbox/sessions/ directory for *repo_path* (default: cwd)."""
    base = repo_path if repo_path is not None else Path.cwd()
    return base / "blackbox" / "sessions"


# ── Session discovery ─────────────────────────────────────────────────────────

def list_missions(sessions_root: Path) -> list[dict]:
    """Return a list of mission summary dicts, newest first.

    Each dict contains: mission_id, status, changed_files_count, command, name.
    Missing fields default to "?" so the list never crashes on a partial session.
    """
    if not sessions_root.is_dir():
        return []

    results = []
    for entry in sorted(sessions_root.iterdir(), reverse=True):
        if not entry.is_dir():
            continue
        manifest_path = entry / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        exit_code = m.get("exit_code", -1)
        results.append({
            "mission_id":         m.get("mission_id", entry.name),
            "name":               m.get("name", "?"),
            "status":             "PASS" if exit_code == 0 else "FAIL",
            "changed_files_count": m.get("changed_files_count", "?"),
            "command":            m.get("command", "?"),
            "started_at":         m.get("started_at", "?"),
        })
    return results


def find_mission(sessions_root: Path, mission_id: str) -> Path:
    """Return the session directory for *mission_id*, or raise FileNotFoundError."""
    candidate = sessions_root / mission_id
    if candidate.is_dir():
        return candidate
    raise FileNotFoundError(
        f"Mission not found: {mission_id!r}\n"
        f"Searched in: {sessions_root}"
    )


# ── Loaders ───────────────────────────────────────────────────────────────────

def _load_json(path: Path, label: str) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"{label} not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_manifest(session_dir: Path) -> dict:
    return _load_json(session_dir / "manifest.json", "manifest")


def load_seal(session_dir: Path) -> dict:
    return _load_json(session_dir / "seal.json", "seal")


def load_seal_payload(session_dir: Path) -> dict:
    return _load_json(session_dir / "seal_payload.json", "seal payload")


def load_qr_payload(session_dir: Path) -> dict:
    txt_path = session_dir / "seal_qr_payload.txt"
    if not txt_path.exists():
        raise FileNotFoundError(f"QR payload not found: {txt_path}")
    return json.loads(txt_path.read_text(encoding="utf-8"))


# ── Verification ──────────────────────────────────────────────────────────────

def verify_mission(session_dir: Path) -> dict:
    """Recompute SHA256 hashes and compare against seal.json.

    Returns a dict with keys:
        manifest_ok (bool), archive_ok (bool), session_directory_ok (bool),
        verified (bool), detail (dict of computed vs stored values)

    Raises FileNotFoundError if seal.json or required files are missing.
    """
    seal = load_seal(session_dir)
    manifest_path = session_dir / "manifest.json"
    mission_id = seal.get("mission_id", session_dir.name)

    # Locate archive (.zip lives next to the session directory)
    zip_path = session_dir.parent / f"{session_dir.name}.zip"

    computed_manifest = sha256_file(manifest_path)
    computed_archive = sha256_file(zip_path) if zip_path.exists() else None
    computed_directory = sha256_directory(session_dir)

    manifest_ok   = computed_manifest  == seal.get("manifest_sha256")
    archive_ok    = (computed_archive  == seal.get("archive_sha256")) if computed_archive is not None else False
    directory_ok  = computed_directory == seal.get("session_directory_sha256")
    verified      = manifest_ok and archive_ok and directory_ok

    return {
        "mission_id":        mission_id,
        "manifest_ok":       manifest_ok,
        "archive_ok":        archive_ok,
        "session_directory_ok": directory_ok,
        "verified":          verified,
        "archive_found":     zip_path.exists(),
        "detail": {
            "manifest_computed":   computed_manifest,
            "manifest_stored":     seal.get("manifest_sha256"),
            "archive_computed":    computed_archive,
            "archive_stored":      seal.get("archive_sha256"),
            "directory_computed":  computed_directory,
            "directory_stored":    seal.get("session_directory_sha256"),
        },
    }
