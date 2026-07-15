"""
envelope.py — in-toto Statements and DSSE envelopes for CI-issued attestations.

This module is the wire format for the ``CI-verified`` assurance tier
(see docs/THREAT_MODEL.md): a trusted issuer (normally a CI job, *not* the
workstation that recorded the evidence) wraps its verdict in a standard
in-toto Statement and signs it inside a DSSE envelope.

Statement (https://in-toto.io/Statement/v1):

    {
      "_type": "https://in-toto.io/Statement/v1",
      "subject": [{"name": <repo identity>,
                   "digest": {"gitCommit": <oid>, "gitTree": <tree oid>}}],
      "predicateType": ".../agent-source-provenance/v1",
      "predicate": { ... sao evidence + issuer claims ... }
    }

DSSE envelope (https://github.com/secure-systems-lab/dsse):

    {
      "payloadType": "application/vnd.in-toto+json",
      "payload": base64(statement canonical JSON),
      "signatures": [{"keyid": ..., "sig": base64(...), "sao_scheme": ...}]
    }

Signatures are computed over the Pre-Authentication Encoding (PAE):

    PAE(type, payload) = "DSSEv1" SP LEN(type) SP type SP LEN(payload) SP payload

Signers (pluggable, dependency-free):

  * ``none``  — unsigned envelope (empty ``signatures``). Only acceptable
                for local / self-recorded output; a ``ci-verified`` claim
                always requires a real signature.
  * ``ssh``   — ``ssh-keygen -Y sign`` (same mechanism attest.py uses for
                provenance.json), signing the PAE bytes.
  * ``hmac``  — HMAC-SHA256 with a key read from a file (point the CI job
                at a secret-mounted path). Symmetric: verifier needs the
                same key. Suitable for a single trusted control plane.

Future work: a Sigstore/keyless signer slots in as another Signer
implementation (sign the PAE bytes with an ephemeral key bound to the CI
OIDC identity, record the certificate in the signature entry). It is not
implemented here to keep the package dependency-free.

The extra ``sao_scheme`` field on each signature entry is a private
extension naming the scheme so verifiers can dispatch; standard DSSE
consumers ignore unknown fields.
"""

from __future__ import annotations

import base64
import hashlib
import hmac as hmac_mod
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PREDICATE_TYPE = (
    "https://gorillapioneer.github.io/special-agent-ops/"
    "agent-source-provenance/v1"
)
PAYLOAD_TYPE = "application/vnd.in-toto+json"

#: Graduated assurance tiers, weakest to strongest (docs/THREAT_MODEL.md).
TIER_SELF_RECORDED = "self-recorded"
TIER_LOCALLY_SIGNED = "locally-signed"
TIER_CI_VERIFIED = "ci-verified"
TIER_INDEPENDENTLY_WITNESSED = "independently-witnessed"
TIER_ORDER = (
    TIER_SELF_RECORDED,
    TIER_LOCALLY_SIGNED,
    TIER_CI_VERIFIED,
    TIER_INDEPENDENTLY_WITNESSED,
)


def tier_rank(tier) -> int:
    """Numeric rank of an assurance tier (-1 for unknown/None)."""
    try:
        return TIER_ORDER.index(tier)
    except ValueError:
        return -1


SSH_SIGN_NAMESPACE = "sao-ci-attestation"


def canonical_json(obj) -> str:
    """Deterministic JSON encoding (same convention as attest.py)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


# ── Statement ─────────────────────────────────────────────────────────────────

def build_statement(subject_name: str, commit: str, tree: str, predicate: dict) -> dict:
    """Build an in-toto v1 Statement whose subject is a git commit.

    The subject digest carries both the commit OID and the result tree OID,
    so a verifier can match the statement against git reality without
    resolving the commit first.
    """
    return {
        "_type": STATEMENT_TYPE,
        "subject": [
            {
                "name": subject_name,
                "digest": {"gitCommit": commit, "gitTree": tree},
            }
        ],
        "predicateType": PREDICATE_TYPE,
        "predicate": predicate,
    }


def statement_sha256(statement: dict) -> str:
    """SHA256 (hex) of the statement's canonical JSON — its identity."""
    return hashlib.sha256(canonical_json(statement).encode("utf-8")).hexdigest()


# ── DSSE PAE ─────────────────────────────────────────────────────────────────

def pae(payload_type: str, payload: bytes) -> bytes:
    """DSSE Pre-Authentication Encoding.

    PAE(type, payload) = "DSSEv1" SP LEN(type) SP type SP LEN(payload) SP payload
    where LEN is the decimal byte length and SP a single space (0x20).
    """
    type_bytes = payload_type.encode("utf-8")
    return b"DSSEv1 %d %b %d %b" % (
        len(type_bytes), type_bytes, len(payload), payload,
    )


# ── Signers ──────────────────────────────────────────────────────────────────

class NoneSigner:
    """No signature at all — unsigned envelope.

    Only acceptable for local / self-recorded output. ci_issue refuses to
    label anything ``ci-verified`` when this signer is used.
    """

    kind = "none"

    def sign(self, message: bytes) -> list:
        return []


class HmacSigner:
    """HMAC-SHA256 over the PAE bytes, key read from a file.

    Point ``key_file`` at a path where the CI runner materialised a secret
    (e.g. ``echo "$SAO_CI_HMAC_KEY" > key && sao ci-issue ... `` with the
    env var coming from the repository/organisation secret store). The
    key never appears in the envelope; ``keyid`` is a hash of the key so
    rotation is observable without disclosure.
    """

    kind = "hmac"
    scheme = "hmac-sha256"

    def __init__(self, key_file):
        key_path = Path(key_file)
        if not key_path.exists():
            raise FileNotFoundError(f"HMAC key file not found: {key_path}")
        self.key = key_path.read_bytes().strip()
        if not self.key:
            raise ValueError(f"HMAC key file is empty: {key_path}")
        self.keyid = "hmac-sha256:" + hashlib.sha256(self.key).hexdigest()[:32]

    def sign(self, message: bytes) -> list:
        mac = hmac_mod.new(self.key, message, hashlib.sha256).digest()
        return [{
            "keyid": self.keyid,
            "sig": base64.b64encode(mac).decode("ascii"),
            "sao_scheme": self.scheme,
        }]


class SshSigner:
    """``ssh-keygen -Y sign`` over the PAE bytes (namespace sao-ci-attestation).

    Reuses the same SSH signing mechanism attest.py applies to
    provenance.json, but here the signed message is the DSSE PAE so the
    envelope is self-contained. The armored SSHSIG text is base64-encoded
    into the signature entry.
    """

    kind = "ssh"
    scheme = "ssh"

    def __init__(self, key_file, namespace: str = SSH_SIGN_NAMESPACE):
        key_path = Path(key_file)
        if not key_path.exists():
            raise FileNotFoundError(f"SSH signing key not found: {key_path}")
        if shutil.which("ssh-keygen") is None:
            raise RuntimeError("ssh-keygen not available for ssh signing")
        self.key_file = key_path
        self.namespace = namespace

    def sign(self, message: bytes) -> list:
        with tempfile.TemporaryDirectory(prefix="sao_dsse_") as tmp:
            msg_path = Path(tmp) / "pae.bin"
            msg_path.write_bytes(message)
            proc = subprocess.run(
                [
                    "ssh-keygen", "-Y", "sign",
                    "-f", str(self.key_file),
                    "-n", self.namespace,
                    str(msg_path),
                ],
                capture_output=True,
                text=True,
            )
            sig_path = Path(str(msg_path) + ".sig")
            if proc.returncode != 0 or not sig_path.exists():
                raise RuntimeError(
                    f"ssh-keygen -Y sign failed: {proc.stderr.strip()}"
                )
            armored = sig_path.read_bytes()
        return [{
            "keyid": "ssh:" + self.key_file.name,
            "sig": base64.b64encode(armored).decode("ascii"),
            "sao_scheme": self.scheme,
        }]


def make_signer(kind: str, key_file=None):
    """Factory: 'none' | 'hmac' | 'ssh' -> Signer instance.

    For 'hmac' the key file defaults to $SAO_CI_HMAC_KEY_FILE; for 'ssh'
    it defaults to $SAO_SIGNING_KEY_FILE (same env attest.py uses).
    """
    if kind == "none":
        return NoneSigner()
    if kind == "hmac":
        key_file = key_file or os.environ.get("SAO_CI_HMAC_KEY_FILE")
        if not key_file:
            raise ValueError(
                "hmac signer needs --key-file or $SAO_CI_HMAC_KEY_FILE"
            )
        return HmacSigner(key_file)
    if kind == "ssh":
        key_file = key_file or os.environ.get("SAO_SIGNING_KEY_FILE")
        if not key_file:
            raise ValueError(
                "ssh signer needs --key-file or $SAO_SIGNING_KEY_FILE"
            )
        return SshSigner(key_file)
    raise ValueError(f"unknown signer kind: {kind!r}")


# ── Envelope construction / parsing ───────────────────────────────────────────

def wrap_envelope(statement: dict, signer) -> dict:
    """Wrap *statement* in a DSSE envelope signed by *signer*."""
    payload = canonical_json(statement).encode("utf-8")
    signatures = signer.sign(pae(PAYLOAD_TYPE, payload))
    return {
        "payloadType": PAYLOAD_TYPE,
        "payload": base64.b64encode(payload).decode("ascii"),
        "signatures": signatures,
    }


def envelope_payload(envelope: dict):
    """Return (statement_dict, payload_bytes) from an envelope, or (None, None).

    Validates payloadType and base64/JSON structure; performs NO signature
    verification.
    """
    if not isinstance(envelope, dict):
        return None, None
    if envelope.get("payloadType") != PAYLOAD_TYPE:
        return None, None
    try:
        payload = base64.b64decode(envelope.get("payload", ""), validate=True)
        statement = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None, None
    if not isinstance(statement, dict):
        return None, None
    return statement, payload


# ── Signature verification ────────────────────────────────────────────────────

def verify_envelope_hmac(envelope: dict, key_file) -> bool:
    """Verify at least one HMAC-SHA256 signature with the key in *key_file*."""
    statement, payload = envelope_payload(envelope)
    if statement is None:
        return False
    key_path = Path(key_file)
    if not key_path.exists():
        return False
    key = key_path.read_bytes().strip()
    expected = hmac_mod.new(
        key, pae(PAYLOAD_TYPE, payload), hashlib.sha256
    ).digest()
    for entry in envelope.get("signatures", []):
        if entry.get("sao_scheme") not in (None, HmacSigner.scheme):
            continue
        try:
            sig = base64.b64decode(entry.get("sig", ""), validate=True)
        except ValueError:
            continue
        if hmac_mod.compare_digest(sig, expected):
            return True
    return False


def verify_envelope_ssh(
    envelope: dict,
    allowed_signers=None,
    identity: str = "sao",
    namespace: str = SSH_SIGN_NAMESPACE,
):
    """Verify an SSHSIG signature on the envelope.

    With *allowed_signers* the signature is verified against that file
    (``ssh-keygen -Y verify``); without it only a structural
    ``check-novalidate`` is performed. Returns True/False, or None when
    ssh-keygen is unavailable or the envelope carries no ssh signature.
    """
    if shutil.which("ssh-keygen") is None:
        return None
    statement, payload = envelope_payload(envelope)
    if statement is None:
        return False
    message = pae(PAYLOAD_TYPE, payload)

    ssh_entries = [
        e for e in envelope.get("signatures", [])
        if e.get("sao_scheme") in (None, SshSigner.scheme)
        and e.get("sig")
    ]
    if not ssh_entries:
        return None

    for entry in ssh_entries:
        try:
            armored = base64.b64decode(entry["sig"], validate=True)
        except ValueError:
            continue
        with tempfile.TemporaryDirectory(prefix="sao_dsse_") as tmp:
            sig_path = Path(tmp) / "envelope.sig"
            sig_path.write_bytes(armored)
            if allowed_signers:
                cmd = [
                    "ssh-keygen", "-Y", "verify",
                    "-f", str(allowed_signers),
                    "-I", identity,
                    "-n", namespace,
                    "-s", str(sig_path),
                ]
            else:
                cmd = [
                    "ssh-keygen", "-Y", "check-novalidate",
                    "-n", namespace,
                    "-s", str(sig_path),
                ]
            proc = subprocess.run(cmd, input=message, capture_output=True)
            if proc.returncode == 0:
                return True
    return False
