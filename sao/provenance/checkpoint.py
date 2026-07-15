"""
checkpoint.py — signed ledger checkpoints and witness cosignatures.

A Merkle tree proves append-only-ness only *relative to a previously
trusted root*. A checkpoint is that root, made portable: a small,
versioned statement of "the ledger for <origin> has <tree_size> entries
and root <root_hash>", signed by the repo operator and — the point of
the exercise — cosignable by independent witnesses (see witness.py).
A client that requires N cosignatures from a pinned witness set turns
"the operator says so" into "the operator AND N parties the operator
does not control say so": equivocation (showing different verifiers
different logs) then requires colluding witnesses. This is the
CT / Go-sumdb / witness-network pattern, stdlib-only.

Checkpoint document ("sao-checkpoint/1"), stored as JSON:

    {
      "version":    "sao-checkpoint/1",
      "origin":     <stable repo/ledger identity string>,
      "tree_size":  N,
      "root_hash":  <hex Merkle root of the first N ledger entries>,
      "timestamp":  <iso8601 — an OPERATOR CLAIM unless witnessed>,
      "signature":  {keyid, sig, sao_scheme} | null (null = UNSIGNED),
      "cosignatures": [{witness, keyid, sig, sao_scheme, cosigned_at}, ...],
      "bundled_proofs": [{old_size, old_root, proof}, ...]   # optional
    }

The SIGNED BODY is only {version, origin, tree_size, root_hash,
timestamp} in canonical JSON. Signatures and cosignatures live outside
the body, so cosignatures append without invalidating the operator
signature or each other. Bundled consistency proofs are also outside
the body: a consistency proof is self-authenticating (it either links
two committed roots or it does not), so it needs no signature — it is
carried for witnesses that lack direct ledger access.

Domain separation (PAE, as in DSSE):

    operator signature over  PAE("sao-checkpoint/1",     body-bytes)
    witness cosignature over PAE("sao-witness-cosign/1", canonical
                                 JSON of {"checkpoint": body,
                                          "witness": <name>})

binding the witness name into its own signature. SSH signatures
additionally use distinct namespaces ("sao-checkpoint" /
"sao-witness-cosign"). Signers are the ones from envelope.py:
``ssh`` (ssh-keygen -Y sign), ``hmac`` (HMAC-SHA256, key from a file),
``none`` (allowed for the operator, but the checkpoint is loudly
marked unsigned; never allowed for cosigning).

Pinned witness keys file (--witness-keys): one witness per line,

    <name> ssh-ed25519 AAAA...            # allowed-signers style
    <name> hmac-sha256 <one-token-key>    # shared-secret witness

'#' comments and blank lines are ignored. The hmac option is symmetric
— whoever holds the pinned file can forge that witness's cosignature —
so it is only suitable when the checkpoint verifier and the witness are
the same trusted control plane. Use ssh keys for genuinely independent
witnesses.

Verification (``sao checkpoint verify``) needs LEDGER ACCESS to check
the root: run it inside the repo (or a clone). A witness verifying
growth needs either its own clone (--ledger-repo) or a bundled proof
(--bundle-proof-from at emit time) — see witness.py.
"""

from __future__ import annotations

import base64
import hashlib
import hmac as hmac_mod
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import attest, envelope as envelope_mod, ledger as ledger_mod

CHECKPOINT_VERSION = "sao-checkpoint/1"
#: PAE context strings — domain separation between the operator signature
#: and witness cosignatures over the same checkpoint body.
CHECKPOINT_CONTEXT = "sao-checkpoint/1"
COSIGN_CONTEXT = "sao-witness-cosign/1"
#: ssh-keygen -Y namespaces (distinct from the DSSE/attestation namespaces).
SSH_CHECKPOINT_NAMESPACE = "sao-checkpoint"
SSH_COSIGN_NAMESPACE = "sao-witness-cosign"

#: Default output location for emitted checkpoints, repo-relative.
DEFAULT_CHECKPOINT_PATH = Path("blackbox") / "checkpoint.json"

#: Fields covered by the operator signature and every cosignature.
BODY_FIELDS = ("version", "origin", "tree_size", "root_hash", "timestamp")

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"
SKIP = "SKIP"


def _check(name: str, level: str, detail: str) -> dict:
    return {"name": name, "level": level, "detail": detail}


canonical_json = attest.canonical_json


# ── Origin identity ───────────────────────────────────────────────────────────

def origin_for_repo(repo_path, origin=None) -> str:
    """Stable identity string for the attested ledger.

    Explicit --origin wins; otherwise the origin remote URL, falling back
    to the repo directory name. Witnesses key their remembered state by
    this string, so pick something stable across clones (set --origin
    explicitly when the repo has no remote).
    """
    if origin:
        return str(origin)
    return attest._repo_identity(Path(repo_path))


def origin_slug(origin: str) -> str:
    """Filesystem/ref-safe slug for an origin (readable prefix + hash tag).

    The hash tag makes distinct origins collision-free even when their
    readable prefixes coincide.
    """
    readable = re.sub(r"[^A-Za-z0-9._-]+", "-", origin).strip("-").lower()
    tag = hashlib.sha256(origin.encode("utf-8")).hexdigest()[:8]
    return f"{readable[-40:] or 'origin'}-{tag}"


# ── Body / messages ──────────────────────────────────────────────────────────

def checkpoint_body(cp: dict) -> dict:
    """The signed subset of a checkpoint document."""
    return {field: cp.get(field) for field in BODY_FIELDS}


def checkpoint_sha256(cp: dict) -> str:
    """SHA256 (hex) of the checkpoint body's canonical JSON — its identity."""
    return hashlib.sha256(
        canonical_json(checkpoint_body(cp)).encode("utf-8")
    ).hexdigest()


def checkpoint_message(cp: dict) -> bytes:
    """Bytes the OPERATOR signs: PAE over the canonical body."""
    body = canonical_json(checkpoint_body(cp)).encode("utf-8")
    return envelope_mod.pae(CHECKPOINT_CONTEXT, body)


def cosign_message(cp: dict, witness_name: str) -> bytes:
    """Bytes a WITNESS signs: PAE over {checkpoint body, witness name}.

    Binding the witness name in prevents one witness's cosignature being
    replayed under another pinned name.
    """
    payload = canonical_json(
        {"checkpoint": checkpoint_body(cp), "witness": witness_name}
    ).encode("utf-8")
    return envelope_mod.pae(COSIGN_CONTEXT, payload)


# ── Construction ──────────────────────────────────────────────────────────────

def build_checkpoint(repo_path, origin=None, bundle_proof_from=None) -> dict:
    """Build an (unsigned) checkpoint of the repo's current ledger.

    *bundle_proof_from* embeds a consistency proof from that older tree
    size, so a witness without its own ledger clone can still verify
    append-only growth from its remembered checkpoint.
    """
    ledger = ledger_mod.Ledger(repo_path)
    root_info = ledger.root()
    cp = {
        "version": CHECKPOINT_VERSION,
        "origin": origin_for_repo(repo_path, origin),
        "tree_size": root_info["tree_size"],
        "root_hash": root_info["root_hash"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "signature": None,
        "cosignatures": [],
    }
    if bundle_proof_from is not None:
        size = int(bundle_proof_from)
        if size < 0 or size > root_info["tree_size"]:
            raise ValueError(
                f"--bundle-proof-from {size} out of range for "
                f"ledger size {root_info['tree_size']}"
            )
        cp["bundled_proofs"] = [{
            "old_size": size,
            "old_root": ledger.root_at(size),
            "proof": ledger.consistency_proof(size),
        }]
    return cp


def make_operator_signer(kind: str, key_file=None):
    """envelope.py signer for checkpoint bodies (ssh namespace adjusted)."""
    if kind == "ssh":
        key_file = key_file or os.environ.get("SAO_SIGNING_KEY_FILE")
        if not key_file:
            raise ValueError("ssh signer needs --key-file or $SAO_SIGNING_KEY_FILE")
        return envelope_mod.SshSigner(key_file, namespace=SSH_CHECKPOINT_NAMESPACE)
    return envelope_mod.make_signer(kind, key_file)


def make_cosign_signer(kind: str, key_file=None):
    """envelope.py signer for witness cosignatures ('none' is refused)."""
    if kind == "none":
        raise ValueError("a witness cosignature cannot use the 'none' signer")
    if kind == "ssh":
        key_file = key_file or os.environ.get("SAO_SIGNING_KEY_FILE")
        if not key_file:
            raise ValueError("ssh signer needs --key-file or $SAO_SIGNING_KEY_FILE")
        return envelope_mod.SshSigner(key_file, namespace=SSH_COSIGN_NAMESPACE)
    return envelope_mod.make_signer(kind, key_file)


def sign_checkpoint(cp: dict, signer) -> dict:
    """Attach the operator signature (in place). 'none' leaves it null."""
    entries = signer.sign(checkpoint_message(cp))
    cp["signature"] = entries[0] if entries else None
    return cp


def add_cosignature(cp: dict, witness_name: str, signer) -> dict:
    """Cosign *cp* as *witness_name* (in place); returns the new entry.

    An existing cosignature by the same witness is replaced — one entry
    per witness name. Other cosignatures and the operator signature are
    untouched (the signed body never changes).
    """
    entries = signer.sign(cosign_message(cp, witness_name))
    if not entries:
        raise ValueError("cosigning requires a real signer")
    entry = {
        "witness": witness_name,
        **entries[0],
        "cosigned_at": datetime.now(timezone.utc).isoformat(),
    }
    cp["cosignatures"] = [
        e for e in cp.get("cosignatures", []) if e.get("witness") != witness_name
    ]
    cp["cosignatures"].append(entry)
    return entry


# ── File I/O ──────────────────────────────────────────────────────────────────

def load_checkpoint(path) -> dict:
    """Read and structurally validate a checkpoint document."""
    cp = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(cp, dict) or cp.get("version") != CHECKPOINT_VERSION:
        raise ValueError(
            f"not a {CHECKPOINT_VERSION} checkpoint: {path}"
        )
    return cp


def write_checkpoint(cp: dict, path) -> Path:
    """Write a checkpoint document (atomically) and return the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(cp, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)
    return path


# ── Signature verification ────────────────────────────────────────────────────

def _verify_hmac_entry(entry: dict, message: bytes, key: bytes) -> bool:
    try:
        sig = base64.b64decode(entry.get("sig", ""), validate=True)
    except (ValueError, TypeError):
        return False
    expected = hmac_mod.new(key, message, hashlib.sha256).digest()
    return hmac_mod.compare_digest(sig, expected)


def _verify_ssh_entry(
    entry: dict, message: bytes, allowed_signers_line: str,
    identity: str, namespace: str,
):
    """Verify one SSHSIG entry against a single allowed-signers line.

    Returns True/False, or None when ssh-keygen is unavailable.
    """
    if shutil.which("ssh-keygen") is None:
        return None
    try:
        armored = base64.b64decode(entry.get("sig", ""), validate=True)
    except (ValueError, TypeError):
        return False
    with tempfile.TemporaryDirectory(prefix="sao_ckpt_") as tmp:
        sig_path = Path(tmp) / "checkpoint.sig"
        sig_path.write_bytes(armored)
        allowed_path = Path(tmp) / "allowed_signers"
        allowed_path.write_text(allowed_signers_line.rstrip() + "\n",
                                encoding="utf-8")
        proc = subprocess.run(
            [
                "ssh-keygen", "-Y", "verify",
                "-f", str(allowed_path),
                "-I", identity,
                "-n", namespace,
                "-s", str(sig_path),
            ],
            input=message,
            capture_output=True,
        )
    return proc.returncode == 0


def verify_operator_signature(
    cp: dict, hmac_key_file=None, allowed_signers=None, identity: str = "sao",
):
    """Verify the operator signature on a checkpoint.

    Returns True/False, or None when the checkpoint is unsigned or no
    usable key material was provided.
    """
    entry = cp.get("signature")
    if not entry:
        return None
    message = checkpoint_message(cp)
    scheme = entry.get("sao_scheme")
    if scheme == envelope_mod.HmacSigner.scheme:
        if not hmac_key_file:
            return None
        key_path = Path(hmac_key_file)
        if not key_path.exists():
            return False
        return _verify_hmac_entry(entry, message, key_path.read_bytes().strip())
    if scheme == envelope_mod.SshSigner.scheme:
        if not allowed_signers:
            return None
        text = Path(allowed_signers).read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            result = _verify_ssh_entry(
                entry, message, line, identity, SSH_CHECKPOINT_NAMESPACE
            )
            if result:
                return True
            if result is None:
                return None
        return False
    return False


# ── Pinned witness keys ───────────────────────────────────────────────────────

def load_witness_keys(path) -> dict:
    """Parse a pinned witness keys file.

    Returns {name: ("hmac-sha256", key_bytes) | ("ssh", allowed_line)}.
    Duplicate names: last line wins.
    """
    keys = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            raise ValueError(f"malformed witness-keys line: {raw!r}")
        name, scheme, material = parts
        if scheme == "hmac-sha256":
            keys[name] = ("hmac-sha256", material.strip().encode("utf-8"))
        else:
            # allowed-signers style: "<name> <keytype> <base64> [comment]"
            keys[name] = ("ssh", line)
    return keys


def verify_cosignature(cp: dict, entry: dict, witness_keys: dict):
    """Verify one cosignature entry against the pinned witness set.

    Returns (ok: bool, problem: str or None).
    """
    name = entry.get("witness")
    if not name:
        return False, "cosignature entry carries no witness name"
    pinned = witness_keys.get(name)
    if pinned is None:
        return False, f"witness {name!r} is not in the pinned key set"
    message = cosign_message(cp, name)
    kind, material = pinned
    scheme = entry.get("sao_scheme")
    if kind == "hmac-sha256":
        if scheme not in (None, envelope_mod.HmacSigner.scheme):
            return False, f"witness {name!r}: pinned hmac key, {scheme} signature"
        if _verify_hmac_entry(entry, message, material):
            return True, None
        return False, f"witness {name!r}: cosignature does not verify"
    if scheme not in (None, envelope_mod.SshSigner.scheme):
        return False, f"witness {name!r}: pinned ssh key, {scheme} signature"
    result = _verify_ssh_entry(
        entry, message, material, identity=name,
        namespace=SSH_COSIGN_NAMESPACE,
    )
    if result is None:
        return False, "ssh-keygen unavailable to verify ssh cosignatures"
    if result:
        return True, None
    return False, f"witness {name!r}: cosignature does not verify"


def valid_cosigners(cp: dict, witness_keys: dict):
    """Return (sorted list of distinct valid witness names, [problems])."""
    valid = set()
    problems = []
    for entry in cp.get("cosignatures", []):
        ok, problem = verify_cosignature(cp, entry, witness_keys)
        if ok:
            valid.add(entry["witness"])
        else:
            problems.append(problem)
    return sorted(valid), problems


# ── Full checkpoint verification ─────────────────────────────────────────────

def verify_checkpoint(
    repo_path,
    cp: dict,
    require_witnesses: int = 0,
    witness_keys_path=None,
    hmac_key_file=None,
    allowed_signers=None,
    identity: str = "sao",
) -> dict:
    """Verify a checkpoint against the local ledger and pinned witnesses.

    Checks: structure, operator signature (verified when key material is
    given, WARN when unsigned or unverifiable), root vs the local ledger
    at that size, append-only consistency to the current ledger, and
    >= *require_witnesses* valid cosignatures from the pinned key file.

    Returns {"ok", "checks", "origin", "tree_size", "root_hash",
             "valid_witnesses"}.
    """
    checks = []
    report = {
        "ok": False,
        "checks": checks,
        "origin": cp.get("origin"),
        "tree_size": cp.get("tree_size"),
        "root_hash": cp.get("root_hash"),
        "valid_witnesses": [],
    }

    if cp.get("version") != CHECKPOINT_VERSION:
        checks.append(_check(
            "structure", FAIL, f"unsupported version {cp.get('version')!r}"
        ))
        return report
    checks.append(_check(
        "structure", OK,
        f"{CHECKPOINT_VERSION} for origin {cp.get('origin')!r} "
        f"(size {cp.get('tree_size')})",
    ))

    # Operator signature.
    if not cp.get("signature"):
        checks.append(_check(
            "operator-signature", WARN,
            "checkpoint is UNSIGNED (emitted with --signer none): the size/"
            "root claim carries no operator identity",
        ))
    else:
        sig_ok = verify_operator_signature(
            cp, hmac_key_file=hmac_key_file,
            allowed_signers=allowed_signers, identity=identity,
        )
        if sig_ok is None:
            checks.append(_check(
                "operator-signature", WARN,
                "signature present but no key material provided "
                "(--hmac-key-file / --allowed-signers) to verify it",
            ))
        elif sig_ok:
            checks.append(_check(
                "operator-signature", OK, "operator signature verifies"
            ))
        else:
            checks.append(_check(
                "operator-signature", FAIL,
                "operator signature does not verify",
            ))

    # Root vs the local ledger.
    ledger = ledger_mod.Ledger(repo_path)
    current = ledger.root()
    tree_size = cp.get("tree_size")
    if not isinstance(tree_size, int) or tree_size < 0:
        checks.append(_check("ledger-root", FAIL, "invalid tree_size"))
    elif tree_size > current["tree_size"]:
        checks.append(_check(
            "ledger-root", FAIL,
            f"checkpoint size {tree_size} is beyond the local ledger "
            f"(size {current['tree_size']}) — stale clone or possible "
            "fork/rollback of the local ledger",
        ))
    elif ledger.root_at(tree_size) != cp.get("root_hash"):
        checks.append(_check(
            "ledger-root", FAIL,
            f"root at size {tree_size} does not match the local ledger — "
            "possible fork/equivocation",
        ))
    else:
        checks.append(_check(
            "ledger-root", OK,
            f"root matches the local ledger at size {tree_size}",
        ))
        proof = ledger.consistency_proof(tree_size)
        cons_ok = ledger_mod.verify_consistency(
            tree_size, current["tree_size"], cp["root_hash"],
            current["root_hash"], proof,
        )
        checks.append(_check(
            "ledger-consistency",
            OK if cons_ok else FAIL,
            f"checkpoint size {tree_size} -> current size "
            f"{current['tree_size']}",
        ))

    # Cosignatures against the pinned witness set.
    require_witnesses = int(require_witnesses or 0)
    if witness_keys_path:
        try:
            witness_keys = load_witness_keys(witness_keys_path)
        except (OSError, ValueError) as e:
            checks.append(_check(
                "cosignatures", FAIL, f"cannot read witness keys: {e}"
            ))
            witness_keys = None
        if witness_keys is not None:
            valid, problems = valid_cosigners(cp, witness_keys)
            report["valid_witnesses"] = valid
            detail = (
                f"{len(valid)} valid cosignature(s) from the pinned set of "
                f"{len(witness_keys)}"
            )
            if valid:
                detail += f": {', '.join(valid)}"
            if problems:
                detail += " | rejected: " + "; ".join(problems[:5])
            if len(valid) >= require_witnesses:
                checks.append(_check(
                    "cosignatures",
                    OK if require_witnesses > 0 else WARN,
                    detail + (
                        f" (required {require_witnesses})"
                        if require_witnesses > 0
                        else " (no quorum required)"
                    ),
                ))
            else:
                checks.append(_check(
                    "cosignatures", FAIL,
                    detail + f" — required {require_witnesses}",
                ))
    elif require_witnesses > 0:
        checks.append(_check(
            "cosignatures", FAIL,
            f"--require-witnesses {require_witnesses} needs a pinned "
            "--witness-keys file",
        ))
    else:
        checks.append(_check(
            "cosignatures", SKIP,
            f"{len(cp.get('cosignatures', []))} cosignature(s) present but "
            "unverified (no --witness-keys pinned)",
        ))

    report["ok"] = not any(c["level"] == FAIL for c in checks)
    return report


# ── Rendering ─────────────────────────────────────────────────────────────────

def render_report(report: dict, title: str, result_word: str = "VERIFIED") -> str:
    bar = "=" * 64
    lines = ["", bar, f"  SPECIAL AGENT OPS — {title}", bar]
    if report.get("origin"):
        lines.append(f"  Origin:     {report['origin']}")
    if report.get("tree_size") is not None:
        lines.append(f"  Tree Size:  {report['tree_size']}")
    if report.get("root_hash"):
        lines.append(f"  Root Hash:  {report['root_hash']}")
    for check in report["checks"]:
        lines.append(f"  [{check['level']:<4}] {check['name']}: {check['detail']}")
    lines.append(bar)
    lines.append(f"  Result: {result_word if report['ok'] else 'FAILED'}")
    lines.append(bar)
    lines.append("")
    return "\n".join(lines)
