"""Tests for sao.provenance.checkpoint — signed checkpoints + cosignatures."""

import json
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from sao.provenance import checkpoint, envelope, ledger as ledger_mod

from conftest import init_git_repo

ORIGIN = "example.test/widgets"


def make_ledger_repo(tmp_path: Path, entries: int = 3) -> Path:
    """A git repo with a synthetic ledger of *entries* leaves."""
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    ledger = ledger_mod.Ledger(repo)
    for i in range(entries):
        ledger.append(f"mission_{i}", secrets.token_hex(32))
    return repo


@pytest.fixture
def ledger_repo(tmp_path: Path) -> Path:
    return make_ledger_repo(tmp_path)


@pytest.fixture
def hmac_key(tmp_path: Path) -> Path:
    key = tmp_path / "operator.key"
    key.write_text(secrets.token_hex(32), encoding="utf-8")
    return key


def make_hmac_key(tmp_path: Path, name: str) -> Path:
    key = tmp_path / name
    key.write_text(secrets.token_hex(32), encoding="utf-8")
    return key


def make_ssh_key(tmp_path: Path, name: str) -> Path:
    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen not available")
    key = tmp_path / name
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
        check=True,
    )
    return key


def get_checks(report: dict) -> dict:
    return {c["name"]: c for c in report["checks"]}


# ── Construction / body ───────────────────────────────────────────────────────

class TestBuild:
    def test_checkpoint_matches_ledger(self, ledger_repo):
        cp = checkpoint.build_checkpoint(ledger_repo, origin=ORIGIN)
        ledger = ledger_mod.Ledger(ledger_repo)
        assert cp["version"] == "sao-checkpoint/1"
        assert cp["origin"] == ORIGIN
        assert cp["tree_size"] == ledger.size()
        assert cp["root_hash"] == ledger.root()["root_hash"]
        assert cp["signature"] is None
        assert cp["cosignatures"] == []

    def test_origin_defaults_to_repo_identity(self, ledger_repo):
        cp = checkpoint.build_checkpoint(ledger_repo)
        assert cp["origin"] == ledger_repo.name  # no remote configured

    def test_body_excludes_signatures_and_proofs(self, ledger_repo, hmac_key):
        cp = checkpoint.build_checkpoint(
            ledger_repo, origin=ORIGIN, bundle_proof_from=1
        )
        checkpoint.sign_checkpoint(
            cp, checkpoint.make_operator_signer("hmac", hmac_key)
        )
        body = checkpoint.checkpoint_body(cp)
        assert set(body) == {
            "version", "origin", "tree_size", "root_hash", "timestamp"
        }

    def test_bundled_proof_verifies(self, ledger_repo):
        cp = checkpoint.build_checkpoint(
            ledger_repo, origin=ORIGIN, bundle_proof_from=2
        )
        bundle = cp["bundled_proofs"][0]
        assert bundle["old_size"] == 2
        assert ledger_mod.verify_consistency(
            2, cp["tree_size"], bundle["old_root"], cp["root_hash"],
            bundle["proof"],
        )

    def test_bundle_proof_out_of_range(self, ledger_repo):
        with pytest.raises(ValueError):
            checkpoint.build_checkpoint(
                ledger_repo, origin=ORIGIN, bundle_proof_from=99
            )

    def test_origin_slug_distinct_per_origin(self):
        a = checkpoint.origin_slug("git@example.test:acme/widgets.git")
        b = checkpoint.origin_slug("git@example.test:acme/widgets")
        assert a != b
        assert "/" not in a and ":" not in a and "@" not in a


# ── Operator signature round trips ────────────────────────────────────────────

class TestOperatorSignature:
    def test_hmac_round_trip(self, ledger_repo, hmac_key):
        cp = checkpoint.build_checkpoint(ledger_repo, origin=ORIGIN)
        checkpoint.sign_checkpoint(
            cp, checkpoint.make_operator_signer("hmac", hmac_key)
        )
        assert cp["signature"]["sao_scheme"] == "hmac-sha256"
        assert checkpoint.verify_operator_signature(cp, hmac_key_file=hmac_key)

    def test_hmac_wrong_key_rejected(self, ledger_repo, hmac_key, tmp_path):
        cp = checkpoint.build_checkpoint(ledger_repo, origin=ORIGIN)
        checkpoint.sign_checkpoint(
            cp, checkpoint.make_operator_signer("hmac", hmac_key)
        )
        other = make_hmac_key(tmp_path, "other.key")
        assert checkpoint.verify_operator_signature(cp, hmac_key_file=other) is False

    def test_hmac_tampered_body_rejected(self, ledger_repo, hmac_key):
        cp = checkpoint.build_checkpoint(ledger_repo, origin=ORIGIN)
        checkpoint.sign_checkpoint(
            cp, checkpoint.make_operator_signer("hmac", hmac_key)
        )
        cp["tree_size"] += 1
        assert (
            checkpoint.verify_operator_signature(cp, hmac_key_file=hmac_key)
            is False
        )

    def test_ssh_round_trip(self, ledger_repo, tmp_path):
        key = make_ssh_key(tmp_path, "op_ed25519")
        cp = checkpoint.build_checkpoint(ledger_repo, origin=ORIGIN)
        checkpoint.sign_checkpoint(
            cp, checkpoint.make_operator_signer("ssh", key)
        )
        assert cp["signature"]["sao_scheme"] == "ssh"
        pub = Path(str(key) + ".pub").read_text(encoding="utf-8").strip()
        allowed = tmp_path / "allowed_signers"
        allowed.write_text(f"sao {pub}\n", encoding="utf-8")
        assert checkpoint.verify_operator_signature(cp, allowed_signers=allowed)

    def test_ssh_wrong_signer_rejected(self, ledger_repo, tmp_path):
        key = make_ssh_key(tmp_path, "op_ed25519")
        other = make_ssh_key(tmp_path, "other_ed25519")
        cp = checkpoint.build_checkpoint(ledger_repo, origin=ORIGIN)
        checkpoint.sign_checkpoint(
            cp, checkpoint.make_operator_signer("ssh", key)
        )
        pub = Path(str(other) + ".pub").read_text(encoding="utf-8").strip()
        allowed = tmp_path / "allowed_signers"
        allowed.write_text(f"sao {pub}\n", encoding="utf-8")
        assert (
            checkpoint.verify_operator_signature(cp, allowed_signers=allowed)
            is False
        )

    def test_none_signer_leaves_unsigned(self, ledger_repo):
        cp = checkpoint.build_checkpoint(ledger_repo, origin=ORIGIN)
        checkpoint.sign_checkpoint(cp, checkpoint.make_operator_signer("none"))
        assert cp["signature"] is None
        assert checkpoint.verify_operator_signature(cp) is None

    def test_cosign_signer_refuses_none(self):
        with pytest.raises(ValueError):
            checkpoint.make_cosign_signer("none")


# ── Cosignatures ─────────────────────────────────────────────────────────────

class TestCosignatures:
    def cosigned(self, ledger_repo, tmp_path, witnesses=("w1", "w2")):
        cp = checkpoint.build_checkpoint(ledger_repo, origin=ORIGIN)
        keys = {}
        lines = []
        for name in witnesses:
            key = make_hmac_key(tmp_path, f"{name}.key")
            keys[name] = key
            checkpoint.add_cosignature(
                cp, name, checkpoint.make_cosign_signer("hmac", key)
            )
            lines.append(
                f"{name} hmac-sha256 {key.read_text(encoding='utf-8').strip()}"
            )
        pinned = tmp_path / "witnesses.txt"
        pinned.write_text(
            "# pinned witnesses\n" + "\n".join(lines) + "\n", encoding="utf-8"
        )
        return cp, keys, pinned

    def test_append_does_not_invalidate(self, ledger_repo, tmp_path, hmac_key):
        cp = checkpoint.build_checkpoint(ledger_repo, origin=ORIGIN)
        checkpoint.sign_checkpoint(
            cp, checkpoint.make_operator_signer("hmac", hmac_key)
        )
        w1 = make_hmac_key(tmp_path, "w1.key")
        w2 = make_hmac_key(tmp_path, "w2.key")
        checkpoint.add_cosignature(
            cp, "w1", checkpoint.make_cosign_signer("hmac", w1)
        )
        first = dict(cp["cosignatures"][0])
        checkpoint.add_cosignature(
            cp, "w2", checkpoint.make_cosign_signer("hmac", w2)
        )
        # Operator signature and the first cosignature still verify.
        assert checkpoint.verify_operator_signature(cp, hmac_key_file=hmac_key)
        assert cp["cosignatures"][0] == first
        pinned = tmp_path / "witnesses.txt"
        pinned.write_text(
            f"w1 hmac-sha256 {w1.read_text(encoding='utf-8').strip()}\n"
            f"w2 hmac-sha256 {w2.read_text(encoding='utf-8').strip()}\n",
            encoding="utf-8",
        )
        valid, problems = checkpoint.valid_cosigners(
            cp, checkpoint.load_witness_keys(pinned)
        )
        assert valid == ["w1", "w2"]
        assert problems == []

    def test_same_witness_recosign_replaces(self, ledger_repo, tmp_path):
        cp, keys, pinned = self.cosigned(ledger_repo, tmp_path, ("w1",))
        checkpoint.add_cosignature(
            cp, "w1", checkpoint.make_cosign_signer("hmac", keys["w1"])
        )
        assert len(cp["cosignatures"]) == 1

    def test_n_of_m_quorum(self, ledger_repo, tmp_path):
        cp, keys, pinned = self.cosigned(ledger_repo, tmp_path, ("w1", "w2"))
        report = checkpoint.verify_checkpoint(
            ledger_repo, cp, require_witnesses=2, witness_keys_path=pinned
        )
        assert report["ok"], report["checks"]
        assert report["valid_witnesses"] == ["w1", "w2"]
        report3 = checkpoint.verify_checkpoint(
            ledger_repo, cp, require_witnesses=3, witness_keys_path=pinned
        )
        assert not report3["ok"]
        assert get_checks(report3)["cosignatures"]["level"] == "FAIL"

    def test_unknown_witness_rejected(self, ledger_repo, tmp_path):
        cp, keys, pinned = self.cosigned(ledger_repo, tmp_path, ("w1",))
        rogue = make_hmac_key(tmp_path, "rogue.key")
        checkpoint.add_cosignature(
            cp, "rogue", checkpoint.make_cosign_signer("hmac", rogue)
        )
        valid, problems = checkpoint.valid_cosigners(
            cp, checkpoint.load_witness_keys(pinned)
        )
        assert valid == ["w1"]
        assert any("not in the pinned key set" in p for p in problems)

    def test_forged_cosignature_rejected(self, ledger_repo, tmp_path):
        """A cosignature made with a key other than the pinned one fails."""
        cp, keys, pinned = self.cosigned(ledger_repo, tmp_path, ("w1",))
        imposter = make_hmac_key(tmp_path, "imposter.key")
        checkpoint.add_cosignature(
            cp, "w1", checkpoint.make_cosign_signer("hmac", imposter)
        )
        valid, problems = checkpoint.valid_cosigners(
            cp, checkpoint.load_witness_keys(pinned)
        )
        assert valid == []
        assert any("does not verify" in p for p in problems)

    def test_cosignature_not_replayable_across_names(self, ledger_repo, tmp_path):
        """w1's signature re-labelled as w2 must not verify, even when both
        pin the same key (the witness name is bound into the message)."""
        cp = checkpoint.build_checkpoint(ledger_repo, origin=ORIGIN)
        key = make_hmac_key(tmp_path, "shared.key")
        checkpoint.add_cosignature(
            cp, "w1", checkpoint.make_cosign_signer("hmac", key)
        )
        replay = dict(cp["cosignatures"][0])
        replay["witness"] = "w2"
        cp["cosignatures"].append(replay)
        token = key.read_text(encoding="utf-8").strip()
        pinned = tmp_path / "witnesses.txt"
        pinned.write_text(
            f"w1 hmac-sha256 {token}\nw2 hmac-sha256 {token}\n",
            encoding="utf-8",
        )
        valid, problems = checkpoint.valid_cosigners(
            cp, checkpoint.load_witness_keys(pinned)
        )
        assert valid == ["w1"]

    def test_ssh_cosignature_round_trip(self, ledger_repo, tmp_path):
        key = make_ssh_key(tmp_path, "w1_ed25519")
        cp = checkpoint.build_checkpoint(ledger_repo, origin=ORIGIN)
        checkpoint.add_cosignature(
            cp, "w1", checkpoint.make_cosign_signer("ssh", key)
        )
        pub = Path(str(key) + ".pub").read_text(encoding="utf-8").strip()
        pinned = tmp_path / "witnesses.txt"
        pinned.write_text(f"w1 {pub}\n", encoding="utf-8")
        report = checkpoint.verify_checkpoint(
            ledger_repo, cp, require_witnesses=1, witness_keys_path=pinned
        )
        assert report["ok"], report["checks"]
        assert report["valid_witnesses"] == ["w1"]

    def test_malformed_witness_keys_line(self, tmp_path):
        pinned = tmp_path / "witnesses.txt"
        pinned.write_text("just-a-name\n", encoding="utf-8")
        with pytest.raises(ValueError):
            checkpoint.load_witness_keys(pinned)


# ── verify_checkpoint vs the ledger ───────────────────────────────────────────

class TestVerifyCheckpoint:
    def test_ok_and_unsigned_warns(self, ledger_repo):
        cp = checkpoint.build_checkpoint(ledger_repo, origin=ORIGIN)
        report = checkpoint.verify_checkpoint(ledger_repo, cp)
        assert report["ok"]
        checks = get_checks(report)
        assert checks["operator-signature"]["level"] == "WARN"
        assert "UNSIGNED" in checks["operator-signature"]["detail"]
        assert checks["ledger-root"]["level"] == "OK"
        assert checks["ledger-consistency"]["level"] == "OK"

    def test_old_checkpoint_still_consistent(self, ledger_repo):
        cp = checkpoint.build_checkpoint(ledger_repo, origin=ORIGIN)
        ledger_mod.Ledger(ledger_repo).append("later", secrets.token_hex(32))
        report = checkpoint.verify_checkpoint(ledger_repo, cp)
        assert report["ok"], report["checks"]

    def test_root_swap_detected(self, ledger_repo):
        cp = checkpoint.build_checkpoint(ledger_repo, origin=ORIGIN)
        cp["root_hash"] = "0" * 64
        report = checkpoint.verify_checkpoint(ledger_repo, cp)
        assert not report["ok"]
        assert get_checks(report)["ledger-root"]["level"] == "FAIL"

    def test_checkpoint_beyond_local_ledger_detected(self, ledger_repo):
        cp = checkpoint.build_checkpoint(ledger_repo, origin=ORIGIN)
        cp["tree_size"] += 5
        report = checkpoint.verify_checkpoint(ledger_repo, cp)
        assert not report["ok"]
        detail = get_checks(report)["ledger-root"]["detail"]
        assert "beyond the local ledger" in detail

    def test_bad_signature_fails(self, ledger_repo, hmac_key, tmp_path):
        cp = checkpoint.build_checkpoint(ledger_repo, origin=ORIGIN)
        checkpoint.sign_checkpoint(
            cp, checkpoint.make_operator_signer("hmac", hmac_key)
        )
        other = make_hmac_key(tmp_path, "other.key")
        report = checkpoint.verify_checkpoint(
            ledger_repo, cp, hmac_key_file=other
        )
        assert not report["ok"]
        assert get_checks(report)["operator-signature"]["level"] == "FAIL"

    def test_require_witnesses_needs_pinned_keys(self, ledger_repo):
        cp = checkpoint.build_checkpoint(ledger_repo, origin=ORIGIN)
        report = checkpoint.verify_checkpoint(
            ledger_repo, cp, require_witnesses=1
        )
        assert not report["ok"]
        assert get_checks(report)["cosignatures"]["level"] == "FAIL"

    def test_write_and_load_round_trip(self, ledger_repo, tmp_path):
        cp = checkpoint.build_checkpoint(ledger_repo, origin=ORIGIN)
        path = tmp_path / "checkpoint.json"
        checkpoint.write_checkpoint(cp, path)
        assert checkpoint.load_checkpoint(path) == cp

    def test_load_rejects_non_checkpoint(self, tmp_path):
        path = tmp_path / "not-a-checkpoint.json"
        path.write_text(json.dumps({"version": "bogus/9"}), encoding="utf-8")
        with pytest.raises(ValueError):
            checkpoint.load_checkpoint(path)


# ── CLI ───────────────────────────────────────────────────────────────────────

def run_cli(args, cwd):
    return subprocess.run(
        [sys.executable, "-m", "sao.cli", *args],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8",
    )


class TestCli:
    def test_emit_and_verify(self, ledger_repo, hmac_key, tmp_path):
        out = tmp_path / "checkpoint.json"
        proc = run_cli(
            [
                "checkpoint", "emit",
                "--origin", ORIGIN,
                "--signer", "hmac", "--key-file", str(hmac_key),
                "--bundle-proof-from", "1",
                "--out", str(out),
            ],
            cwd=ledger_repo,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert out.exists()
        cp = checkpoint.load_checkpoint(out)
        assert cp["origin"] == ORIGIN
        assert cp["signature"] is not None
        assert cp["bundled_proofs"][0]["old_size"] == 1

        proc = run_cli(
            [
                "checkpoint", "verify",
                "--checkpoint", str(out),
                "--hmac-key-file", str(hmac_key),
            ],
            cwd=ledger_repo,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "Result: VERIFIED" in proc.stdout

    def test_verify_fails_on_root_swap(self, ledger_repo, tmp_path):
        out = tmp_path / "checkpoint.json"
        proc = run_cli(
            ["checkpoint", "emit", "--origin", ORIGIN, "--out", str(out)],
            cwd=ledger_repo,
        )
        assert proc.returncode == 0
        cp = checkpoint.load_checkpoint(out)
        cp["root_hash"] = "f" * 64
        checkpoint.write_checkpoint(cp, out)
        proc = run_cli(
            ["checkpoint", "verify", "--checkpoint", str(out)],
            cwd=ledger_repo,
        )
        assert proc.returncode == 1
        assert "Result: FAILED" in proc.stdout
