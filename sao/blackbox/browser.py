"""
browser.py — list, inspect, and verify recorded mission sessions.

All functions work against the blackbox/sessions/ directory tree, or
directly against a .zip archive (via verify_archive_file).
No external dependencies — stdlib only.
"""

from __future__ import annotations

import json
import tempfile
import zipfile
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


# ── Archive verification ──────────────────────────────────────────────────────

def extract_archive_to_temp(archive_path: Path) -> Path:
    """Extract *archive_path* into a new temp directory and return its Path.

    The caller owns cleanup (shutil.rmtree or similar).  The temp directory
    root is returned; use find_session_dir_in_extracted_archive() to locate
    the session sub-folder inside it.
    """
    tmp = Path(tempfile.mkdtemp(prefix="sao_verify_"))
    with zipfile.ZipFile(archive_path, "r") as zf:
        zf.extractall(tmp)
    return tmp


def find_session_dir_in_extracted_archive(temp_dir: Path) -> Path:
    """Return the single session sub-directory inside *temp_dir*.

    Mission archives always extract into one top-level folder named after the
    mission ID.  Raises FileNotFoundError if no such folder is found.
    """
    candidates = [p for p in temp_dir.iterdir() if p.is_dir()]
    if not candidates:
        raise FileNotFoundError(
            f"No session directory found inside extracted archive at {temp_dir}"
        )
    return candidates[0]


def _find_seal_for_archive(archive_path: Path) -> dict:
    """Locate and return the seal dict for *archive_path*.

    Search order:
      1. Session folder alongside the archive:
         ``<sessions_dir>/<archive_stem>/seal.json``
      2. Companion file next to the archive:
         ``<archive_path>.seal.json``  (e.g. for portable distribution)

    Raises FileNotFoundError with a helpful message if neither is found.
    """
    # 1. Session folder (the usual case — session folder still present)
    session_seal = archive_path.parent / archive_path.stem / "seal.json"
    if session_seal.exists():
        return _load_json(session_seal, "seal")

    # 2. Portable companion file
    companion = archive_path.with_suffix(".seal.json")
    if companion.exists():
        return _load_json(companion, "seal")

    raise FileNotFoundError(
        f"seal.json not found for archive: {archive_path}\n"
        f"Checked:\n"
        f"  {session_seal}\n"
        f"  {companion}\n"
        f"To verify portably, distribute the archive with its seal.json:\n"
        f"  cp <session_dir>/seal.json <archive_path>.seal.json"
    )


def verify_archive_file(archive_path: Path) -> dict:
    """Verify a mission .zip archive, with or without the session folder.

    Flow:
      1. Hash the provided archive file → compare to archive_sha256 in seal.json.
      2. Extract the archive to a temp directory.
      3. Verify manifest_sha256 from the extracted manifest.json.
      4. Verify session_directory_sha256 from the extracted session content
         (uses the same sha256_directory function and exclusions as the recorder).

    seal.json is located automatically: first from the session folder alongside
    the archive, then from a companion ``<archive>.seal.json`` file.

    Returns a result dict (same shape as verify_mission()) plus 'temp_dir' (Path).
    The temp directory is NOT cleaned up here — the caller owns cleanup so it
    can print results before the files disappear.
    """
    archive_path = archive_path.resolve()

    if not archive_path.exists():
        raise FileNotFoundError(f"Archive not found: {archive_path}")
    if not zipfile.is_zipfile(archive_path):
        raise ValueError(f"Not a valid zip file: {archive_path}")

    # Hash the original archive file — must match seal's archive_sha256.
    computed_archive = sha256_file(archive_path)

    # Find seal.json (session folder or companion file).
    seal = _find_seal_for_archive(archive_path)
    mission_id = seal.get("mission_id", archive_path.stem)

    # Extract the archive so we can verify manifest and directory hashes.
    temp_dir = extract_archive_to_temp(archive_path)
    session_dir = find_session_dir_in_extracted_archive(temp_dir)

    computed_manifest  = sha256_file(session_dir / "manifest.json")
    computed_directory = sha256_directory(session_dir)

    archive_ok   = computed_archive   == seal.get("archive_sha256")
    manifest_ok  = computed_manifest  == seal.get("manifest_sha256")
    directory_ok = computed_directory == seal.get("session_directory_sha256")
    verified     = archive_ok and manifest_ok and directory_ok

    return {
        "mission_id":           mission_id,
        "archive_path":         archive_path,
        "manifest_ok":          manifest_ok,
        "archive_ok":           archive_ok,
        "session_directory_ok": directory_ok,
        "verified":             verified,
        "temp_dir":             temp_dir,
        "detail": {
            "archive_computed":   computed_archive,
            "archive_stored":     seal.get("archive_sha256"),
            "manifest_computed":  computed_manifest,
            "manifest_stored":    seal.get("manifest_sha256"),
            "directory_computed": computed_directory,
            "directory_stored":   seal.get("session_directory_sha256"),
        },
    }
