"""Tests for sao.provenance.attest — git-native attestation statements."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from sao.provenance import attest, ledger as ledger_mod

from conftest import init_git_repo, record_committing_mission

pytest.importorskip("qrcode", reason="qrcode[pil] required to record missions")


class TestCanonicalJson:
    def test_sorted_and_compact(self):
        text = attest.canonical_json({"b": 1, "a": [2, {"z": None}]})
        assert text == '{"a":[2,{"z":null}],"b":1}'

    def test_sha256_of_canonical_form(self):
        statement = {"x": 1}
        expected = hashlib.sha256(b'{"x":1}').hexdigest()
        assert attest.attestation_sha256(statement) == expected


class TestStatement:
    def test_statement_fields(self, provenance_repo):
        session_dir = provenance_repo["mission_a"]["session_dir"]
        statement, text = attest.load_attestation(session_dir)
        assert statement["version"] == "sao-attestation/1"
        assert statement["mission_id"] == provenance_repo["mission_a"]["mission_id"]
        assert statement["mission_name"] == "mission a"
        assert statement["agent"]["command_mode"] == "argv"
        assert statement["branch"] == "main"
        assert statement["exit_code"] == 0
        assert statement["head_before"] != statement["head_after"]
        assert len(statement["diff_sha256"]) == 64
        assert len(statement["seal_manifest_sha256"]) == 64
        assert statement["ledger"]["leaf_index"] == 0
        assert statement["ledger"]["tree_size"] == 1
        assert statement["flightplan_sha256"]  # mission a had a flight plan
        assert statement["created_at"]
        # Stored copy is canonical JSON.
        assert text == attest.canonical_json(statement)

    def test_seal_manifest_hash_matches_seal(self, provenance_repo):
        session_dir = provenance_repo["mission_a"]["session_dir"]
        statement, _ = attest.load_attestation(session_dir)
        seal = json.loads((session_dir / "seal.json").read_text(encoding="utf-8"))
        assert statement["seal_manifest_sha256"] == seal["manifest_sha256"]
        assert statement["ledger"]["leaf_hash"] == ledger_mod.leaf_hash_for_seal(
            seal["manifest_sha256"]
        )

    def test_hash_chain_links_to_previous(self, provenance_repo):
        a_dir = provenance_repo["mission_a"]["session_dir"]
        b_dir = provenance_repo["mission_b"]["session_dir"]
        statement_a, text_a = attest.load_attestation(a_dir)
        statement_b, _ = attest.load_attestation(b_dir)
        assert statement_a["parent_attestation_sha256"] is None
        expected = hashlib.sha256(text_a.encode("utf-8")).hexdigest()
        assert statement_b["parent_attestation_sha256"] == expected

    def test_flightplan_absent_is_null(self, provenance_repo):
        statement, _ = attest.load_attestation(
            provenance_repo["mission_b"]["session_dir"]
        )
        assert statement["flightplan_sha256"] is None


class TestGitNotes:
    def test_note_attached_to_new_commit(self, provenance_repo):
        repo = provenance_repo["repo"]
        result = provenance_repo["mission_a"]["attestation"]
        assert result["note_attached"]
        note = attest.read_git_note(repo, result["note_commit"])
        assert note["mission_id"] == provenance_repo["mission_a"]["mission_id"]

    def test_note_matches_session_copy(self, provenance_repo):
        repo = provenance_repo["repo"]
        result = provenance_repo["mission_b"]["attestation"]
        note = attest.read_git_note(repo, result["note_commit"])
        _, session_text = attest.load_attestation(
            provenance_repo["mission_b"]["session_dir"]
        )
        assert attest.canonical_json(note) == session_text

    def test_unattested_commit_has_no_note(self, provenance_repo):
        repo = provenance_repo["repo"]
        assert attest.read_git_note(repo, provenance_repo["human_commit"]) is None

    def test_no_commit_no_note(self, git_repo: Path):
        from sao.blackbox import recorder

        result = recorder.record_mission_argv(
            name="no commit",
            command_argv=[sys.executable, "-c", "print('read only')"],
            repo_path=git_repo,
            attest=True,
        )
        attestation = result["attestation"]
        assert attestation is not None
        assert attestation["note_attached"] is False
        assert (result["session_dir"] / "provenance.json").exists()
        statement = attestation["statement"]
        assert statement["head_before"] == statement["head_after"]


class TestSealInteraction:
    def test_seal_still_verifies_after_attest(self, provenance_repo):
        """provenance.json is written after sealing and must be excluded."""
        from sao.blackbox import browser

        for key in ("mission_a", "mission_b"):
            session_dir = provenance_repo[key]["session_dir"]
            assert (session_dir / "provenance.json").exists()
            assert browser.verify_mission(session_dir)["verified"]

    def test_attest_cli_is_idempotent_on_ledger(self, copy_provenance_repo):
        copied = copy_provenance_repo()
        repo = copied["repo"]
        mission_id = copied["mission_a"]["mission_id"]
        before = ledger_mod.Ledger(repo).size()
        proc = subprocess.run(
            [sys.executable, "-m", "sao.cli", "attest", mission_id],
            cwd=repo, capture_output=True, text=True, encoding="utf-8",
        )
        assert proc.returncode == 0, proc.stderr
        assert "ATTESTATION" in proc.stdout
        assert ledger_mod.Ledger(repo).size() == before

    def test_attest_cli_unknown_mission(self, provenance_repo):
        proc = subprocess.run(
            [sys.executable, "-m", "sao.cli", "attest", "nope_20990101"],
            cwd=provenance_repo["repo"],
            capture_output=True, text=True, encoding="utf-8",
        )
        assert proc.returncode == 1
        assert "Mission not found" in proc.stderr


class TestSigning:
    @pytest.fixture
    def ssh_key(self, tmp_path: Path) -> Path:
        if shutil.which("ssh-keygen") is None:
            pytest.skip("ssh-keygen not available")
        key_path = tmp_path / "signing-id"
        proc = subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key_path)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            pytest.skip(f"could not generate test key: {proc.stderr}")
        return key_path

    def test_unsigned_by_default(self, provenance_repo):
        session_dir = provenance_repo["mission_a"]["session_dir"]
        assert not (session_dir / "provenance.json.sig").exists()
        assert attest.verify_attestation_signature(session_dir) is None

    def test_sign_and_verify(self, copy_provenance_repo, ssh_key, monkeypatch):
        copied = copy_provenance_repo()
        repo = copied["repo"]
        mission_id = copied["mission_a"]["mission_id"]
        session_dir = repo / "blackbox" / "sessions" / mission_id
        monkeypatch.setenv("SAO_SIGNING_KEY_FILE", str(ssh_key))
        monkeypatch.delenv("SAO_ALLOWED_SIGNERS", raising=False)

        result = attest.attest_session(repo, session_dir)
        sig_path = result["signature_path"]
        assert sig_path is not None and sig_path.exists()
        assert attest.verify_attestation_signature(session_dir) is True

        # Seal must still verify with the signature file present.
        from sao.blackbox import browser

        assert browser.verify_mission(session_dir)["verified"]

        # Tampering with the statement invalidates the signature.
        provenance_path = session_dir / "provenance.json"
        statement = json.loads(provenance_path.read_text(encoding="utf-8"))
        statement["exit_code"] = 0 if statement["exit_code"] else 1
        provenance_path.write_text(
            attest.canonical_json(statement), encoding="utf-8"
        )
        assert attest.verify_attestation_signature(session_dir) is False

    def test_verify_with_allowed_signers(self, copy_provenance_repo, ssh_key,
                                         monkeypatch, tmp_path):
        copied = copy_provenance_repo()
        repo = copied["repo"]
        mission_id = copied["mission_b"]["mission_id"]
        session_dir = repo / "blackbox" / "sessions" / mission_id
        monkeypatch.setenv("SAO_SIGNING_KEY_FILE", str(ssh_key))
        attest.attest_session(repo, session_dir)

        pub = Path(str(ssh_key) + ".pub").read_text(encoding="utf-8").strip()
        signers = tmp_path / "allowed_signers"
        signers.write_text(f"sao {pub}\n", encoding="utf-8")
        monkeypatch.setenv("SAO_ALLOWED_SIGNERS", str(signers))
        assert attest.verify_attestation_signature(session_dir) is True

        # A different identity in the signers file must not verify.
        monkeypatch.setenv("SAO_SIGNER_IDENTITY", "someone-else")
        assert attest.verify_attestation_signature(session_dir) is False

    def test_missing_key_is_ignored(self, copy_provenance_repo, monkeypatch):
        copied = copy_provenance_repo()
        repo = copied["repo"]
        mission_id = copied["mission_a"]["mission_id"]
        session_dir = repo / "blackbox" / "sessions" / mission_id
        monkeypatch.setenv("SAO_SIGNING_KEY_FILE", str(repo / "no-such-file"))
        result = attest.attest_session(repo, session_dir)
        assert result["signature_path"] is None
