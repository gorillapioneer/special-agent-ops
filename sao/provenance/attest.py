"""
attest.py — git-native attestation statements for recorded missions.

An attestation is a small, versioned JSON statement ("sao-attestation/2")
binding together:

  * the mission (id, name, agent command info, exit code),
  * the git context (repo, branch, head before/after, diff hash),
  * the git object IDs of the result commit (``git_objects``: parent/result
    commit OIDs, the result tree OID, and per-changed-path blob OID + mode)
    — omitted when the mission did not end on a new commit,
  * the tamper-evident seal (seal.json manifest hash),
  * the transparency log position (ledger leaf index / root),
  * the previous attestation (hash chain via parent_attestation_sha256),
  * an optional flight plan (sha256 of flightplan.json).

Canonical JSON is ``json.dumps(obj, sort_keys=True, separators=(",", ":"))``
and its SHA256 is the attestation's identity.

Storage:
  * always: ``provenance.json`` in the session folder (written AFTER sealing,
    so it is excluded from the seal's directory hash — see seal.py). This is
    the DURABLE store of the statement.
  * when the mission ended on a new commit: a git note under
    ``refs/notes/sao`` on that commit. The note is a DISCOVERY INDEX, not a
    durable security store: notes can be force-replaced without changing the
    commit SHA and are not fetched by default. The note body is the canonical
    statement plus a ``payload_sha256`` field (SHA256 of the statement's
    canonical JSON) so the note can be cross-checked against the session copy.

Verifiers must accept both "sao-attestation/1" and "sao-attestation/2"
statements (v1 predates ``git_objects`` and ``payload_sha256``).

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

ATTESTATION_VERSION = "sao-attestation/2"
#: Versions a verifier must accept. v1 statements predate the git_objects
#: section and the note payload_sha256 cross-check field.
SUPPORTED_VERSIONS = ("sao-attestation/1", "sao-attestation/2")
#: Extra field added to the git-note copy only (never part of the statement).
NOTE_PAYLOAD_FIELD = "payload_sha256"
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


def _rev_parse(repo_path: Path, ref: str):
    proc = _git(["rev-parse", "--verify", ref], cwd=repo_path)
    out = proc.stdout.strip()
    return out if proc.returncode == 0 and out else None


def collect_git_objects(repo_path: Path, head_before, head_after):
    """Resolve the git object IDs of the mission's result commit.

    Returns a dict binding the attestation to immutable git objects rather
    than diff text:

        {"parent_commit": <head_before>,
         "commit":        <head_after>,
         "tree":          <result tree OID>,
         "changed": [{"path", "blob", "mode", "status"}, ...]}

    Deleted paths carry ``blob``/``mode`` of None and status "D".
    Returns None when the mission did not end on a new commit (head unknown
    or unchanged) or when the objects cannot be resolved.
    """
    if (
        not head_after
        or head_after == "unknown"
        or head_after == head_before
    ):
        return None
    tree = _rev_parse(repo_path, f"{head_after}^{{tree}}")
    if tree is None:
        return None

    proc = _git(
        ["diff-tree", "--no-commit-id", "--root", "-r", head_after],
        cwd=repo_path,
    )
    changed = []
    for line in proc.stdout.splitlines():
        if not line.startswith(":"):
            continue
        meta, _, path = line.partition("\t")
        parts = meta[1:].split()
        if len(parts) < 5 or not path:
            continue
        _old_mode, new_mode, _old_oid, new_oid, status = parts[:5]
        deleted = status.startswith("D")
        changed.append({
            "path": path,
            "blob": None if deleted else new_oid,
            "mode": None if deleted else new_mode,
            "status": status[:1],
        })
    return {
        "parent_commit": head_before,
        "commit": head_after,
        "tree": tree,
        "changed": changed,
    }


def attach_git_note(repo_path: Path, commit: str, note_text: str, ref: str = NOTES_REF) -> bool:
    """Attach *note_text* to *commit* under *ref* (default refs/notes/sao,
    force-replace).

    Returns True on success.  Falls back to an explicit committer identity
    when the repo has none configured.
    """
    args = ["notes", f"--ref={ref}", "add", "-f", "-m", note_text, commit]
    proc = _git(args, cwd=repo_path)
    if proc.returncode == 0:
        return True
    fallback = [
        "-c", "user.name=sao-attest",
        "-c", "user.email=sao-attest@blackbox.invalid",
        *args,
    ]
    return _git(fallback, cwd=repo_path).returncode == 0


def read_git_note(repo_path: Path, commit: str, ref: str = NOTES_REF):
    """Return the parsed attestation note on *commit* under *ref*, or None."""
    proc = _git(["notes", f"--ref={ref}", "show", commit], cwd=repo_path)
    if proc.returncode != 0:
        return None
    text = proc.stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def note_statement_and_payload(note):
    """Split a parsed git note into (statement, payload_sha256).

    v2 notes carry an extra ``payload_sha256`` field (the SHA256 of the
    statement's canonical JSON) so the note can be cross-checked against
    the session's durable ``provenance.json`` copy.  v1 notes have no such
    field: the payload half is None and the statement is the note itself.
    """
    if not isinstance(note, dict):
        return note, None
    payload = note.get(NOTE_PAYLOAD_FIELD)
    statement = {k: v for k, v in note.items() if k != NOTE_PAYLOAD_FIELD}
    return statement, payload


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

    # Bind to git object IDs, not just diff text: the result commit's tree
    # OID and each changed path's blob OID + mode.  Omitted when the mission
    # did not end on a new commit.
    git_objects = collect_git_objects(
        repo_path,
        manifest.get("git_commit_before"),
        manifest.get("git_commit_after"),
    )

    statement = {
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
    if git_objects is not None:
        statement["git_objects"] = git_objects
    return statement


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
        # The note is a discovery index: the statement plus payload_sha256
        # (hash of the statement's canonical JSON) so verifiers can
        # cross-check the note against the durable session copy.
        note_body = canonical_json(
            {**statement, NOTE_PAYLOAD_FIELD: attestation_sha256(statement)}
        )
        note_attached = attach_git_note(repo_path, head_after, note_body)
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
