"""Tests for sao.provenance.envelope — in-toto Statements + DSSE envelopes."""

import base64
import json
import secrets
import shutil
import subprocess
from pathlib import Path

import pytest

from sao.provenance import envelope


# ── Statement structure ───────────────────────────────────────────────────────

class TestStatement:
    def test_structure_and_subject_digests(self):
        stmt = envelope.build_statement(
            subject_name="example/repo",
            commit="c" * 40,
            tree="t" * 40,
            predicate={"assurance_tier": "ci-verified"},
        )
        assert stmt["_type"] == "https://in-toto.io/Statement/v1"
        assert stmt["predicateType"] == (
            "https://gorillapioneer.github.io/special-agent-ops/"
            "agent-source-provenance/v1"
        )
        assert len(stmt["subject"]) == 1
        subj = stmt["subject"][0]
        assert subj["name"] == "example/repo"
        assert subj["digest"] == {"gitCommit": "c" * 40, "gitTree": "t" * 40}
        assert stmt["predicate"]["assurance_tier"] == "ci-verified"

    def test_statement_sha256_is_canonical_json_hash(self):
        stmt = envelope.build_statement("r", "c" * 40, "t" * 40, {"k": 1})
        import hashlib

        expected = hashlib.sha256(
            json.dumps(stmt, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        assert envelope.statement_sha256(stmt) == expected

    def test_tier_rank_ordering(self):
        assert envelope.tier_rank("self-recorded") < envelope.tier_rank("locally-signed")
        assert envelope.tier_rank("locally-signed") < envelope.tier_rank("ci-verified")
        assert envelope.tier_rank("ci-verified") < envelope.tier_rank(
            "independently-witnessed"
        )
        assert envelope.tier_rank(None) == -1
        assert envelope.tier_rank("bogus") == -1


# ── DSSE PAE ─────────────────────────────────────────────────────────────────

class TestPAE:
    def test_dsse_spec_vector(self):
        # Known vector from the DSSE specification.
        assert envelope.pae("http://example.com/HelloWorld", b"hello world") == (
            b"DSSEv1 29 http://example.com/HelloWorld 11 hello world"
        )

    def test_empty_payload(self):
        assert envelope.pae("y", b"") == b"DSSEv1 1 y 0 "

    def test_lengths_are_byte_lengths(self):
        # Multi-byte UTF-8 in the type must be counted in bytes, not chars.
        out = envelope.pae("é", b"ab")
        assert out == b"DSSEv1 2 " + "é".encode("utf-8") + b" 2 ab"


# ── Signers ──────────────────────────────────────────────────────────────────

@pytest.fixture
def statement() -> dict:
    return envelope.build_statement(
        "example/repo", "a" * 40, "b" * 40, {"assurance_tier": "ci-verified"}
    )


@pytest.fixture
def hmac_key_file(tmp_path: Path) -> Path:
    key_path = tmp_path / "issuance.key"
    key_path.write_text(secrets.token_hex(32), encoding="utf-8")
    return key_path


class TestNoneSigner:
    def test_unsigned_envelope(self, statement):
        dsse = envelope.wrap_envelope(statement, envelope.NoneSigner())
        assert dsse["payloadType"] == "application/vnd.in-toto+json"
        assert dsse["signatures"] == []
        parsed, payload = envelope.envelope_payload(dsse)
        assert parsed == statement
        assert json.loads(payload) == statement


class TestHmacSigner:
    def test_sign_verify_round_trip(self, statement, hmac_key_file):
        signer = envelope.make_signer("hmac", hmac_key_file)
        dsse = envelope.wrap_envelope(statement, signer)
        assert len(dsse["signatures"]) == 1
        entry = dsse["signatures"][0]
        assert entry["sao_scheme"] == "hmac-sha256"
        assert entry["keyid"].startswith("hmac-sha256:")
        assert envelope.verify_envelope_hmac(dsse, hmac_key_file)

    def test_tampered_payload_detected(self, statement, hmac_key_file):
        signer = envelope.make_signer("hmac", hmac_key_file)
        dsse = envelope.wrap_envelope(statement, signer)
        forged = dict(statement)
        forged["predicate"] = {"assurance_tier": "ci-verified", "forged": True}
        dsse["payload"] = base64.b64encode(
            envelope.canonical_json(forged).encode()
        ).decode()
        assert not envelope.verify_envelope_hmac(dsse, hmac_key_file)

    def test_tampered_signature_detected(self, statement, hmac_key_file):
        signer = envelope.make_signer("hmac", hmac_key_file)
        dsse = envelope.wrap_envelope(statement, signer)
        sig = bytearray(base64.b64decode(dsse["signatures"][0]["sig"]))
        sig[0] ^= 0xFF
        dsse["signatures"][0]["sig"] = base64.b64encode(bytes(sig)).decode()
        assert not envelope.verify_envelope_hmac(dsse, hmac_key_file)

    def test_wrong_key_rejected(self, statement, hmac_key_file, tmp_path):
        signer = envelope.make_signer("hmac", hmac_key_file)
        dsse = envelope.wrap_envelope(statement, signer)
        other = tmp_path / "other.key"
        other.write_text(secrets.token_hex(32), encoding="utf-8")
        assert not envelope.verify_envelope_hmac(dsse, other)

    def test_key_from_env_var(self, statement, hmac_key_file, monkeypatch):
        monkeypatch.setenv("SAO_CI_HMAC_KEY_FILE", str(hmac_key_file))
        signer = envelope.make_signer("hmac")
        dsse = envelope.wrap_envelope(statement, signer)
        assert envelope.verify_envelope_hmac(dsse, hmac_key_file)

    def test_missing_key_file_errors(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SAO_CI_HMAC_KEY_FILE", raising=False)
        with pytest.raises(ValueError):
            envelope.make_signer("hmac")
        with pytest.raises(FileNotFoundError):
            envelope.make_signer("hmac", tmp_path / "nope.key")


class TestSshSigner:
    @pytest.fixture
    def ssh_key(self, tmp_path: Path) -> Path:
        if shutil.which("ssh-keygen") is None:
            pytest.skip("ssh-keygen not available")
        key_path = tmp_path / "ci_ed25519"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key_path)],
            check=True,
        )
        return key_path

    def test_sign_and_structural_verify(self, statement, ssh_key):
        signer = envelope.make_signer("ssh", ssh_key)
        dsse = envelope.wrap_envelope(statement, signer)
        assert dsse["signatures"][0]["sao_scheme"] == "ssh"
        assert envelope.verify_envelope_ssh(dsse) is True

    def test_verify_with_allowed_signers(self, statement, ssh_key, tmp_path):
        signer = envelope.make_signer("ssh", ssh_key)
        dsse = envelope.wrap_envelope(statement, signer)
        pub = Path(str(ssh_key) + ".pub").read_text(encoding="utf-8").strip()
        allowed = tmp_path / "allowed_signers"
        allowed.write_text(f"sao {pub}\n", encoding="utf-8")
        assert envelope.verify_envelope_ssh(dsse, allowed_signers=allowed) is True

    def test_tampered_payload_detected(self, statement, ssh_key):
        signer = envelope.make_signer("ssh", ssh_key)
        dsse = envelope.wrap_envelope(statement, signer)
        forged = dict(statement)
        forged["predicate"] = {"forged": True}
        dsse["payload"] = base64.b64encode(
            envelope.canonical_json(forged).encode()
        ).decode()
        assert envelope.verify_envelope_ssh(dsse) is False

    def test_wrong_signer_rejected_by_allowed_signers(
        self, statement, ssh_key, tmp_path
    ):
        signer = envelope.make_signer("ssh", ssh_key)
        dsse = envelope.wrap_envelope(statement, signer)
        other = tmp_path / "other_ed25519"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(other)],
            check=True,
        )
        pub = Path(str(other) + ".pub").read_text(encoding="utf-8").strip()
        allowed = tmp_path / "allowed_signers"
        allowed.write_text(f"sao {pub}\n", encoding="utf-8")
        assert envelope.verify_envelope_ssh(dsse, allowed_signers=allowed) is False


class TestEnvelopeParsing:
    def test_unknown_signer_kind(self):
        with pytest.raises(ValueError):
            envelope.make_signer("sigstore")

    def test_bad_payload_type_rejected(self, statement):
        dsse = envelope.wrap_envelope(statement, envelope.NoneSigner())
        dsse["payloadType"] = "application/json"
        assert envelope.envelope_payload(dsse) == (None, None)

    def test_bad_base64_rejected(self, statement):
        dsse = envelope.wrap_envelope(statement, envelope.NoneSigner())
        dsse["payload"] = "!!! not base64 !!!"
        assert envelope.envelope_payload(dsse) == (None, None)

    def test_non_dict_envelope_rejected(self):
        assert envelope.envelope_payload("nope") == (None, None)
        assert envelope.envelope_payload(None) == (None, None)
