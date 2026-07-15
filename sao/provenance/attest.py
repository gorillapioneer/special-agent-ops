"""
attest.py — git-native attestation statements for recorded missions.

An attestation is a small, versioned JSON statement ("sao-attestation/1")
binding together:

  * the mission (id, name, agent command info, exit code),
  * the git context (repo, branch, head before/after, diff hash),
  * the tamper-evident seal (seal.json manifest hash),
  * the transparency log position (ledger leaf index / root),
  * the previous attestation (hash chain via parent_attestation_sha256),
  * an optional flight plan (sha256 of flightplan.json).

Canonical JSON is ``json.dumps(obj, sort_keys=True, separators=(",", ":"))``
and its SHA256 is the attestation's identity.

Storage:
  * always: ``provenance.json`` in the session folder (written AFTER sealing,
    so it is excluded from the seal's directory hash — see seal.py),
  * when the mission ended on a new commit: a git note under
    ``refs/notes/sao`` on that commit (canonical JSON as the note body).

Optional signing: if ``SAO_SIGNING_KEY_FILE`` points to an ssh private key
and ``ssh-keygen -Y sign`` is available, the canonical JSON is signed
(namespace "sao-attestation") into ``provenance.json.sig``.  Everything
works unsigned; signing is purely additive.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from sao.blackbox.seal import sha256_file
from . import ledger as ledger_mod

ATTESTATION_VERSION = "sao-attestation/1"
NOTES_REF = "refs/notes/sao"
SIGN_NAMESPACE = "sao-attestation"

PROVENANCE_FILENAME = "provenance.json"
SIGNATURE_FILENAME = "provenance.json.sig"


# ── Canonical JSON ────────────────────────────────────────────────────────────

def canonical_json(obj) -> str:
    """Deterministic JSON encoding used for hashing and git notes."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def attestation_sha256(statement: dict) -> str:
    """SHA256 (hex) of the statement's canonical JSON."""
    return hashlib.sha256(canonical_json(statement).encode("utf-8")).hexdigest()


# ── Git helpers ───────────────────────────────────────────────────────────────

def _git(args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _repo_identity(repo_path: Path) -> str:
    """Origin remote URL when available, otherwise the repo directory name."""
    proc = _git(["config", "--get", "remote.origin.url"], cwd=repo_path)
    url = proc.stdout.strip()
    return url if url else Path(repo_path).name


def attach_git_note(repo_path: Path, commit: str, note_text: str) -> bool:
    """Attach *note_text* to *commit* under refs/notes/sao (force-replace).

    Returns True on success.  Falls back to an explicit committer identity
    when the repo has none configured.
    """
    args = ["notes", f"--ref={NOTES_REF}", "add", "-f", "-m", note_text, commit]
    proc = _git(args, cwd=repo_path)
    if proc.returncode == 0:
        return True
    fallback = [
        "-c", "user.name=sao-attest",
        "-c", "user.email=sao-attest@blackbox.invalid",
        *args,
    ]
    return _git(fallback, cwd=repo_path).returncode == 0


def read_git_note(repo_path: Path, commit: str):
    """Return the parsed attestation note on *commit*, or None."""
    proc = _git(["notes", f"--ref={NOTES_REF}", "show", commit], cwd=repo_path)
    if proc.returncode != 0:
        return None
    text = proc.stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


# ── Parent discovery (hash chain) ────────────────────────────────────────────

def _sessions_root(repo_path: Path) -> Path:
    return Path(repo_path) / "blackbox" / "sessions"


def load_attestation(session_dir: Path):
    """Return (statement_dict, canonical_text) from a session, or (None, None)."""
    path = Path(session_dir) / PROVENANCE_FILENAME
    if not path.exists():
        return None, None
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text), text
    except json.JSONDecodeError:
        return None, None


def find_parent_attestation(repo_path: Path, mission_id: str):
    """Return (parent_statement, parent_sha256) — the newest attested session
    whose mission_id sorts before *mission_id* — or (None, None).

    Mission ids start with a timestamp, so lexical order is chronological.
    """
    root = _sessions_root(repo_path)
    if not root.is_dir():
        return None, None
    candidates = sorted(
        (p for p in root.iterdir() if p.is_dir() and p.name < mission_id),
        reverse=True,
    )
    for session_dir in candidates:
        statement, text = load_attestation(session_dir)
        if statement is not None:
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            return statement, digest
    return None, None


# ── Statement construction ───────────────────────────────────────────────────

def build_attestation(repo_path: Path, session_dir: Path) -> dict:
    """Build the attestation statement for a recorded session.

    Appends the mission to the transparency ledger when it is not there yet
    (the ledger entry is part of the statement).
    """
    repo_path = Path(repo_path)
    session_dir = Path(session_dir)

    manifest = json.loads(
        (session_dir / "manifest.json").read_text(encoding="utf-8")
    )
    seal = json.loads((session_dir / "seal.json").read_text(encoding="utf-8"))
    mission_id = manifest["mission_id"]

    diff_path = session_dir / "git_diff.patch"
    diff_sha256 = sha256_file(diff_path) if diff_path.exists() else None

    flightplan_path = session_dir / "flightplan.json"
    flightplan_sha256 = (
        sha256_file(flightplan_path) if flightplan_path.exists() else None
    )

    # Transparency log: ensure this mission has a leaf, then record position.
    ledger = ledger_mod.Ledger(repo_path)
    entry = ledger.append(mission_id, seal["manifest_sha256"])
    root_info = ledger.root()

    _, parent_sha256 = find_parent_attestation(repo_path, mission_id)

    agent = {
        "command": manifest.get("command"),
        "command_mode": manifest.get("command_mode"),
    }
    if manifest.get("command_argv"):
        agent["command_argv"] = manifest["command_argv"]

    return {
        "version": ATTESTATION_VERSION,
        "mission_id": mission_id,
        "mission_name": manifest.get("name"),
        "agent": agent,
        "repo": _repo_identity(repo_path),
        "branch": manifest.get("git_branch"),
        "head_before": manifest.get("git_commit_before"),
        "head_after": manifest.get("git_commit_after"),
        "diff_sha256": diff_sha256,
        "exit_code": manifest.get("exit_code"),
        "seal_manifest_sha256": seal["manifest_sha256"],
        "ledger": {
            "leaf_index": entry["index"],
            "leaf_hash": entry["leaf_hash"],
            "tree_size": root_info["tree_size"],
            "root": root_info["root_hash"],
        },
        "flightplan_sha256": flightplan_sha256,
        "parent_attestation_sha256": parent_sha256,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Optional ssh signing ─────────────────────────────────────────────────────

def _ssh_keygen_available() -> bool:
    return shutil.which("ssh-keygen") is not None


def sign_attestation(provenance_path: Path):
    """Sign provenance.json with the key in $SAO_SIGNING_KEY_FILE.

    Returns the .sig path on success, None when signing is not configured
    or unavailable.  Never raises — signing is strictly optional.
    """
    key_file = os.environ.get("SAO_SIGNING_KEY_FILE")
    if not key_file or not Path(key_file).exists() or not _ssh_keygen_available():
        return None
    proc = subprocess.run(
        [
            "ssh-keygen", "-Y", "sign",
            "-f", key_file,
            "-n", SIGN_NAMESPACE,
            str(provenance_path),
        ],
        capture_output=True,
        text=True,
    )
    sig_path = Path(str(provenance_path) + ".sig")
    if proc.returncode == 0 and sig_path.exists():
        return sig_path
    return None


def verify_attestation_signature(session_dir: Path):
    """Verify provenance.json.sig for a session.

    Returns True / False, or None when there is no signature to check or
    ssh-keygen is unavailable.  If $SAO_ALLOWED_SIGNERS is set the signature
    is checked against that allowed-signers file (identity from
    $SAO_SIGNER_IDENTITY, default "sao"); otherwise a structural
    ``check-novalidate`` is performed.
    """
    session_dir = Path(session_dir)
    provenance_path = session_dir / PROVENANCE_FILENAME
    sig_path = session_dir / SIGNATURE_FILENAME
    if not sig_path.exists() or not provenance_path.exists():
        return None
    if not _ssh_keygen_available():
        return None

    allowed_signers = os.environ.get("SAO_ALLOWED_SIGNERS")
    if allowed_signers:
        identity = os.environ.get("SAO_SIGNER_IDENTITY", "sao")
        cmd = [
            "ssh-keygen", "-Y", "verify",
            "-f", allowed_signers,
            "-I", identity,
            "-n", SIGN_NAMESPACE,
            "-s", str(sig_path),
        ]
    else:
        cmd = [
            "ssh-keygen", "-Y", "check-novalidate",
            "-n", SIGN_NAMESPACE,
            "-s", str(sig_path),
        ]
    proc = subprocess.run(
        cmd,
        input=provenance_path.read_bytes(),
        capture_output=True,
    )
    return proc.returncode == 0


# ── Orchestration ────────────────────────────────────────────────────────────

def attest_session(repo_path: Path, session_dir: Path) -> dict:
    """Build, store, note-attach, and (optionally) sign an attestation.

    Returns a result dict:
        statement, provenance_path, note_attached (bool),
        note_commit (str or None), signature_path (Path or None).
    """
    repo_path = Path(repo_path)
    session_dir = Path(session_dir)

    statement = build_attestation(repo_path, session_dir)
    text = canonical_json(statement)

    provenance_path = session_dir / PROVENANCE_FILENAME
    provenance_path.write_text(text, encoding="utf-8")

    note_attached = False
    note_commit = None
    head_before = statement.get("head_before")
    head_after = statement.get("head_after")
    if (
        head_after
        and head_after != "unknown"
        and head_after != head_before
    ):
        note_attached = attach_git_note(repo_path, head_after, text)
        note_commit = head_after if note_attached else None

    signature_path = sign_attestation(provenance_path)

    return {
        "statement": statement,
        "attestation_sha256": attestation_sha256(statement),
        "provenance_path": provenance_path,
        "note_attached": note_attached,
        "note_commit": note_commit,
        "signature_path": signature_path,
    }
