"""Tests for sao.provenance.ci_issue — CI-side issuance (ci-verified tier)."""

import base64
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

from sao.provenance import attest, ci_issue, envelope, verify_pr

from conftest import init_git_repo, record_committing_mission
from test_verify_pr import rewrite_statement

pytest.importorskip("qrcode", reason="qrcode[pil] required to record missions")

CI_ENV = {
    "GITHUB_ACTIONS": "true",
    "GITHUB_REPOSITORY": "acme/widgets",
    "GITHUB_REPOSITORY_OWNER": "acme",
    "GITHUB_WORKFLOW_REF": "acme/policy/.github/workflows/issuer.yml@refs/heads/main",
    "GITHUB_RUN_ID": "424242",
    "GITHUB_RUN_ATTEMPT": "1",
    "GITHUB_ACTOR": "policy-bot",
    "GITHUB_SHA": "f" * 40,
}


@pytest.fixture
def ci_env(monkeypatch):
    for key, value in CI_ENV.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def local_env(monkeypatch):
    for key in CI_ENV:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def hmac_key(tmp_path: Path) -> Path:
    key_path = tmp_path / "issuance.key"
    key_path.write_text(secrets.token_hex(32), encoding="utf-8")
    return key_path


def get_checks(report: dict) -> dict:
    return {c["name"]: c for c in report["checks"]}


def issued_setup(copy_provenance_repo, hmac_key, mission="mission_b"):
    """Copy the shared repo and return (copied, repo, commit, mission_id)."""
    copied = copy_provenance_repo()
    repo = copied["repo"]
    commit = copied[mission]["attestation"]["note_commit"]
    return copied, repo, commit, copied[mission]["mission_id"]


# ── Happy path ────────────────────────────────────────────────────────────────

class TestIssueHappyPath:
    def test_ci_issue_mints_ci_verified(self, copy_provenance_repo, hmac_key, ci_env):
        copied, repo, commit, mission_id = issued_setup(copy_provenance_repo, hmac_key)
        report = ci_issue.issue(
            repo, commit, signer_kind="hmac", key_file=hmac_key
        )
        assert report["ok"], report["checks"]
        assert report["tier"] == "ci-verified"
        assert report["mission_id"] == mission_id
        assert report["note_attached"]
        checks = get_checks(report)
        assert checks["evidence-seal"]["level"] == "OK"
        assert checks["ledger-inclusion"]["level"] == "OK"
        assert checks["ledger-consistency"]["level"] == "OK"
        assert checks["evidence-note"]["level"] == "OK"
        assert checks["git-reality"]["level"] == "OK"
        assert checks["policy-checks"]["level"] == "OK"
        assert checks["issuer"]["level"] == "OK"

        out_path = report["out_path"]
        assert out_path.exists()
        dsse = json.loads(out_path.read_text(encoding="utf-8"))
        statement, _ = envelope.envelope_payload(dsse)
        assert statement["subject"][0]["digest"]["gitCommit"] == commit
        predicate = statement["predicate"]
        assert predicate["assurance_tier"] == "ci-verified"
        assert predicate["issuer"]["mode"] == "ci"
        assert predicate["issuer"]["claims"]["github_repository"] == "acme/widgets"
        assert predicate["issuer"]["claims"]["github_run_id"] == "424242"
        assert predicate["mission"]["id"] == mission_id
        assert predicate["git_objects"]["commit"] == commit
        assert envelope.statement_sha256(statement) == report["statement_sha256"]

    def test_discovery_note_round_trip(self, copy_provenance_repo, hmac_key, ci_env):
        _, repo, commit, mission_id = issued_setup(copy_provenance_repo, hmac_key)
        report = ci_issue.issue(repo, commit, signer_kind="hmac", key_file=hmac_key)
        assert report["ok"]
        note = attest.read_git_note(repo, commit, ref=ci_issue.CI_NOTES_REF)
        assert note["version"] == "sao-ci-note/1"
        assert note["statement_sha256"] == report["statement_sha256"]
        assert note["tier"] == "ci-verified"
        assert note["mission_id"] == mission_id
        located = repo / note["location"]
        assert located.resolve() == report["out_path"].resolve()
        # And discovery via the note finds the same envelope.
        dsse, source = ci_issue.find_ci_attestation(repo, commit)
        assert dsse is not None
        statement, _ = envelope.envelope_payload(dsse)
        assert envelope.statement_sha256(statement) == report["statement_sha256"]

    def test_explicit_session_flag(self, copy_provenance_repo, hmac_key, ci_env):
        _, repo, commit, mission_id = issued_setup(copy_provenance_repo, hmac_key)
        report = ci_issue.issue(
            repo, commit, mission_id=mission_id,
            signer_kind="hmac", key_file=hmac_key,
        )
        assert report["ok"]
        assert report["tier"] == "ci-verified"


class TestLocalModeNeverCiVerified:
    def test_local_signed_is_locally_signed(
        self, copy_provenance_repo, hmac_key, local_env
    ):
        _, repo, commit, _ = issued_setup(copy_provenance_repo, hmac_key)
        report = ci_issue.issue(repo, commit, signer_kind="hmac", key_file=hmac_key)
        assert report["ok"]
        assert report["tier"] == "locally-signed"
        assert get_checks(report)["issuer"]["level"] == "WARN"
        statement, _ = envelope.envelope_payload(
            json.loads(report["out_path"].read_text(encoding="utf-8"))
        )
        assert statement["predicate"]["assurance_tier"] == "locally-signed"
        assert statement["predicate"]["issuer"]["mode"] == "local"

    def test_local_unsigned_is_self_recorded(self, copy_provenance_repo, local_env, hmac_key):
        _, repo, commit, _ = issued_setup(copy_provenance_repo, hmac_key)
        report = ci_issue.issue(repo, commit, signer_kind="none")
        assert report["ok"]
        assert report["tier"] == "self-recorded"

    def test_ci_env_with_none_signer_is_not_ci_verified(
        self, copy_provenance_repo, ci_env, hmac_key
    ):
        """ci-verified requires mode=ci AND a real signature."""
        _, repo, commit, _ = issued_setup(copy_provenance_repo, hmac_key)
        report = ci_issue.issue(repo, commit, signer_kind="none")
        assert report["ok"]
        assert report["tier"] == "self-recorded"


# ── Refusal paths ─────────────────────────────────────────────────────────────

class TestRefusals:
    def test_tampered_tree_claim_refused(self, copy_provenance_repo, hmac_key, ci_env):
        copied, repo, commit, mission_id = issued_setup(copy_provenance_repo, hmac_key)
        session_dir = repo / "blackbox" / "sessions" / mission_id
        statement, _ = attest.load_attestation(session_dir)
        statement["git_objects"]["tree"] = "0" * 40
        rewrite_statement(repo, session_dir, commit, statement)

        report = ci_issue.issue(repo, commit, signer_kind="hmac", key_file=hmac_key)
        assert not report["ok"]
        checks = get_checks(report)
        assert checks["git-reality"]["level"] == "FAIL"
        assert "tree" in checks["git-reality"]["detail"]
        assert report["out_path"] is None
        assert attest.read_git_note(repo, commit, ref=ci_issue.CI_NOTES_REF) is None

    def test_tampered_blob_claim_refused(self, copy_provenance_repo, hmac_key, ci_env):
        copied, repo, commit, mission_id = issued_setup(copy_provenance_repo, hmac_key)
        session_dir = repo / "blackbox" / "sessions" / mission_id
        statement, _ = attest.load_attestation(session_dir)
        statement["git_objects"]["changed"][0]["blob"] = "f" * 40
        rewrite_statement(repo, session_dir, commit, statement)

        report = ci_issue.issue(repo, commit, signer_kind="hmac", key_file=hmac_key)
        assert not report["ok"]
        assert get_checks(report)["git-reality"]["level"] == "FAIL"

    def test_out_of_scope_strict_refused(self, tmp_path, hmac_key, ci_env):
        from sao.provenance import flightplan

        repo = tmp_path / "repo"
        repo.mkdir()
        init_git_repo(repo)
        flightplan.file_flight_plan(
            repo, name="drift", intent="stay in src", scope=["src/*"]
        )
        mission = record_committing_mission(
            repo, "drift", "outside/escape.py", "OOPS = True\n"
        )
        commit = mission["attestation"]["note_commit"]

        report = ci_issue.issue(
            repo, commit, signer_kind="hmac", key_file=hmac_key, strict_scope=True
        )
        assert not report["ok"]
        checks = get_checks(report)
        assert checks["policy-scope"]["level"] == "FAIL"
        assert "outside/escape.py" in checks["policy-scope"]["detail"]

        # Advisory (default): issued, but the drift is recorded as WARN.
        report2 = ci_issue.issue(
            repo, commit, signer_kind="hmac", key_file=hmac_key
        )
        assert report2["ok"]
        assert get_checks(report2)["policy-scope"]["level"] == "WARN"

    def test_failed_checks_refused_unless_allowed(
        self, copy_provenance_repo, hmac_key, ci_env
    ):
        copied, repo, commit, mission_id = issued_setup(copy_provenance_repo, hmac_key)
        session_dir = repo / "blackbox" / "sessions" / mission_id
        statement, _ = attest.load_attestation(session_dir)
        statement["exit_code"] = 2
        rewrite_statement(repo, session_dir, commit, statement)

        report = ci_issue.issue(repo, commit, signer_kind="hmac", key_file=hmac_key)
        assert not report["ok"]
        assert get_checks(report)["policy-checks"]["level"] == "FAIL"

        report2 = ci_issue.issue(
            repo, commit, signer_kind="hmac", key_file=hmac_key,
            allow_failed_checks=True,
        )
        assert report2["ok"]
        assert get_checks(report2)["policy-checks"]["level"] == "WARN"

    def test_missing_session_refused(self, copy_provenance_repo, hmac_key, ci_env):
        copied, repo, commit, mission_id = issued_setup(copy_provenance_repo, hmac_key)
        session_dir = repo / "blackbox" / "sessions" / mission_id
        session_dir.rename(session_dir.parent.parent / "detached_session")

        report = ci_issue.issue(repo, commit, signer_kind="hmac", key_file=hmac_key)
        assert not report["ok"]
        assert get_checks(report)["evidence-bundle"]["level"] == "FAIL"

    def test_unattested_commit_refused(self, copy_provenance_repo, hmac_key, ci_env):
        copied = copy_provenance_repo()
        repo = copied["repo"]
        report = ci_issue.issue(
            repo, copied["human_commit"], signer_kind="hmac", key_file=hmac_key
        )
        assert not report["ok"]
        assert get_checks(report)["evidence-bundle"]["level"] == "FAIL"

    def test_unknown_commit_refused(self, copy_provenance_repo, hmac_key, ci_env):
        copied = copy_provenance_repo()
        report = ci_issue.issue(
            copied["repo"], "0" * 40, signer_kind="hmac", key_file=hmac_key
        )
        assert not report["ok"]
        assert get_checks(report)["commit"]["level"] == "FAIL"


# ── ci-verify ─────────────────────────────────────────────────────────────────

class TestCiVerify:
    @pytest.fixture
    def issued(self, copy_provenance_repo, hmac_key, ci_env):
        copied, repo, commit, mission_id = issued_setup(copy_provenance_repo, hmac_key)
        report = ci_issue.issue(repo, commit, signer_kind="hmac", key_file=hmac_key)
        assert report["ok"]
        return {
            "repo": repo,
            "commit": commit,
            "out_path": report["out_path"],
            "key": hmac_key,
            "copied": copied,
        }

    def test_accepts_valid(self, issued):
        report = ci_issue.ci_verify(
            issued["repo"], issued["commit"],
            attestation_path=issued["out_path"],
            hmac_key_file=issued["key"],
        )
        assert report["ok"], report["checks"]
        assert report["tier"] == "ci-verified"
        checks = get_checks(report)
        assert checks["signature"]["level"] == "OK"
        assert checks["subject"]["level"] == "OK"
        assert checks["git-reality"]["level"] == "OK"

    def test_rejects_tampered_statement(self, issued):
        dsse = json.loads(issued["out_path"].read_text(encoding="utf-8"))
        statement, _ = envelope.envelope_payload(dsse)
        statement["predicate"]["checks"]["exit_code"] = 0
        statement["predicate"]["mission"]["id"] = "forged_mission"
        dsse["payload"] = base64.b64encode(
            envelope.canonical_json(statement).encode()
        ).decode()
        issued["out_path"].write_text(json.dumps(dsse), encoding="utf-8")

        report = ci_issue.ci_verify(
            issued["repo"], issued["commit"],
            attestation_path=issued["out_path"],
            hmac_key_file=issued["key"],
        )
        assert not report["ok"]
        assert get_checks(report)["signature"]["level"] == "FAIL"

    def test_rejects_tampered_signature(self, issued):
        dsse = json.loads(issued["out_path"].read_text(encoding="utf-8"))
        sig = bytearray(base64.b64decode(dsse["signatures"][0]["sig"]))
        sig[0] ^= 0xFF
        dsse["signatures"][0]["sig"] = base64.b64encode(bytes(sig)).decode()
        issued["out_path"].write_text(json.dumps(dsse), encoding="utf-8")

        report = ci_issue.ci_verify(
            issued["repo"], issued["commit"],
            attestation_path=issued["out_path"],
            hmac_key_file=issued["key"],
        )
        assert not report["ok"]

    def test_rejects_wrong_commit(self, issued):
        other_commit = issued["copied"]["human_commit"]
        report = ci_issue.ci_verify(
            issued["repo"], other_commit,
            attestation_path=issued["out_path"],
            hmac_key_file=issued["key"],
        )
        assert not report["ok"]
        assert get_checks(report)["subject"]["level"] == "FAIL"

    def test_rejects_unsigned_envelope(self, copy_provenance_repo, hmac_key, ci_env):
        _, repo, commit, _ = issued_setup(copy_provenance_repo, hmac_key)
        report = ci_issue.issue(repo, commit, signer_kind="none")
        assert report["ok"]  # issuance at self-recorded is allowed...
        verify = ci_issue.ci_verify(
            repo, commit, attestation_path=report["out_path"]
        )
        # ...but an unsigned envelope can never verify as an issuer claim.
        assert not verify["ok"]
        assert get_checks(verify)["signature"]["level"] == "FAIL"

    def test_local_issue_claiming_ci_is_rejected(
        self, copy_provenance_repo, hmac_key, local_env
    ):
        """A forged predicate claiming ci-verified without issuer mode=ci
        is rejected on tier consistency (before even reaching signatures
        an attacker can't produce)."""
        _, repo, commit, _ = issued_setup(copy_provenance_repo, hmac_key)
        report = ci_issue.issue(repo, commit, signer_kind="hmac", key_file=hmac_key)
        dsse = json.loads(report["out_path"].read_text(encoding="utf-8"))
        statement, _ = envelope.envelope_payload(dsse)
        statement["predicate"]["assurance_tier"] = "ci-verified"
        # Re-sign with the same key (attacker WITH the key but outside CI).
        forged = envelope.wrap_envelope(
            statement, envelope.make_signer("hmac", hmac_key)
        )
        report["out_path"].write_text(json.dumps(forged), encoding="utf-8")

        verify = ci_issue.ci_verify(
            repo, commit,
            attestation_path=report["out_path"],
            hmac_key_file=hmac_key,
        )
        assert not verify["ok"]
        assert get_checks(verify)["tier"]["level"] == "FAIL"


# ── verify-pr tier awareness ─────────────────────────────────────────────────

class TestVerifyPrMinTier:
    def test_default_min_tier_preserves_behaviour(self, copy_provenance_repo):
        copied = copy_provenance_repo()
        report = verify_pr.verify_pr(
            copied["repo"], base=copied["base_commit"], head="HEAD"
        )
        assert report["ok"]
        assert report["min_tier"] == "self-recorded"
        attested = [c for c in report["commits"] if c["attested"]]
        assert all(c["tier"] == "self-recorded" for c in attested)

    def test_min_tier_ci_verified_fails_without_ci_attestation(
        self, copy_provenance_repo, hmac_key
    ):
        copied = copy_provenance_repo()
        commit = copied["mission_b"]["attestation"]["note_commit"]
        report = verify_pr.verify_pr(
            copied["repo"], base=f"{commit}^", head=commit,
            min_tier="ci-verified", ci_hmac_key_file=hmac_key,
        )
        assert not report["ok"]
        entry = report["commits"][0]
        checks = {c["name"]: c for c in entry["checks"]}
        assert checks["ci-attestation"]["level"] == "SKIP"
        assert checks["tier"]["level"] == "FAIL"
        assert entry["tier"] == "self-recorded"

    def test_min_tier_ci_verified_passes_with_ci_attestation(
        self, copy_provenance_repo, hmac_key, ci_env
    ):
        _, repo, commit, _ = issued_setup(copy_provenance_repo, hmac_key)
        issued = ci_issue.issue(repo, commit, signer_kind="hmac", key_file=hmac_key)
        assert issued["ok"]

        report = verify_pr.verify_pr(
            repo, base=f"{commit}^", head=commit,
            min_tier="ci-verified", ci_hmac_key_file=hmac_key,
        )
        assert report["ok"], report["commits"][0]["checks"]
        entry = report["commits"][0]
        assert entry["tier"] == "ci-verified"
        checks = {c["name"]: c for c in entry["checks"]}
        assert checks["ci-attestation"]["level"] == "OK"
        assert checks["tier"]["level"] == "OK"

    def test_ci_attestations_dir_discovery(
        self, copy_provenance_repo, hmac_key, ci_env, tmp_path
    ):
        """An envelope in --ci-attestations-dir is found even without the
        refs/notes/sao-ci discovery note (e.g. artifact download in CI)."""
        _, repo, commit, _ = issued_setup(copy_provenance_repo, hmac_key)
        out = tmp_path / "artifacts" / "attestation.json"
        issued = ci_issue.issue(
            repo, commit, signer_kind="hmac", key_file=hmac_key, out_path=out
        )
        assert issued["ok"]
        # Drop the discovery note; only the directory remains.
        subprocess.run(
            ["git", "-C", str(repo), "update-ref", "-d", ci_issue.CI_NOTES_REF],
            check=True,
        )
        report = verify_pr.verify_pr(
            repo, base=f"{commit}^", head=commit,
            min_tier="ci-verified", ci_hmac_key_file=hmac_key,
            ci_attestations_dir=out.parent,
        )
        assert report["ok"], report["commits"][0]["checks"]
        assert report["commits"][0]["tier"] == "ci-verified"

    def test_tampered_ci_attestation_fails_loudly(
        self, copy_provenance_repo, hmac_key, ci_env
    ):
        _, repo, commit, _ = issued_setup(copy_provenance_repo, hmac_key)
        issued = ci_issue.issue(repo, commit, signer_kind="hmac", key_file=hmac_key)
        dsse = json.loads(issued["out_path"].read_text(encoding="utf-8"))
        statement, _ = envelope.envelope_payload(dsse)
        statement["predicate"]["mission"]["id"] = "forged"
        dsse["payload"] = base64.b64encode(
            envelope.canonical_json(statement).encode()
        ).decode()
        issued["out_path"].write_text(json.dumps(dsse), encoding="utf-8")

        # Even at the DEFAULT min tier a bad CI attestation is a FAIL.
        report = verify_pr.verify_pr(
            repo, base=f"{commit}^", head=commit, ci_hmac_key_file=hmac_key
        )
        assert not report["ok"]
        checks = {c["name"]: c for c in report["commits"][0]["checks"]}
        assert checks["ci-attestation"]["level"] == "FAIL"

    def test_unattested_commit_fails_min_tier(self, copy_provenance_repo, hmac_key):
        copied = copy_provenance_repo()
        report = verify_pr.verify_pr(
            copied["repo"], base=copied["base_commit"], head="HEAD",
            min_tier="ci-verified", ci_hmac_key_file=hmac_key,
        )
        assert not report["ok"]
        human = next(
            c for c in report["commits"]
            if c["commit"] == copied["human_commit"]
        )
        checks = {c["name"]: c for c in human["checks"]}
        assert checks["tier"]["level"] == "FAIL"

    def test_local_issued_attestation_does_not_reach_ci_verified(
        self, copy_provenance_repo, hmac_key, local_env
    ):
        _, repo, commit, _ = issued_setup(copy_provenance_repo, hmac_key)
        issued = ci_issue.issue(repo, commit, signer_kind="hmac", key_file=hmac_key)
        assert issued["tier"] == "locally-signed"
        report = verify_pr.verify_pr(
            repo, base=f"{commit}^", head=commit,
            min_tier="ci-verified", ci_hmac_key_file=hmac_key,
        )
        assert not report["ok"]
        checks = {c["name"]: c for c in report["commits"][0]["checks"]}
        assert checks["tier"]["level"] == "FAIL"

    def test_unknown_min_tier_rejected(self, copy_provenance_repo):
        copied = copy_provenance_repo()
        with pytest.raises(ValueError):
            verify_pr.verify_pr(
                copied["repo"], base="HEAD", head="HEAD", min_tier="platinum"
            )

    def test_tier_in_reports(self, copy_provenance_repo, hmac_key, ci_env):
        _, repo, commit, _ = issued_setup(copy_provenance_repo, hmac_key)
        assert ci_issue.issue(
            repo, commit, signer_kind="hmac", key_file=hmac_key
        )["ok"]
        report = verify_pr.verify_pr(
            repo, base=f"{commit}^", head=commit,
            min_tier="ci-verified", ci_hmac_key_file=hmac_key,
        )
        text = verify_pr.render_text(report)
        assert "Min Tier:   ci-verified" in text
        assert "[ci-verified]" in text
        md = verify_pr.render_markdown(report)
        assert "| Commit | Mission | Tier | Check | Level | Detail |" in md
        assert "**Minimum tier:** ci-verified" in md
        assert "| ci-verified |" in md


# ── CLI ───────────────────────────────────────────────────────────────────────

def run_cli(args, cwd, extra_env=None):
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "sao.cli", *args],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8", env=env,
    )


class TestCli:
    def test_ci_issue_and_verify_cli(self, copy_provenance_repo, hmac_key):
        copied = copy_provenance_repo()
        repo = copied["repo"]
        commit = copied["mission_b"]["attestation"]["note_commit"]
        out = repo / "attestation.dsse.json"

        proc = run_cli(
            [
                "ci-issue", "--commit", commit,
                "--signer", "hmac", "--key-file", str(hmac_key),
                "--out", str(out),
            ],
            cwd=repo,
            extra_env=CI_ENV,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "tier ci-verified" in proc.stdout
        assert out.exists()

        proc = run_cli(
            [
                "ci-verify", "--commit", commit,
                "--attestation", str(out),
                "--hmac-key-file", str(hmac_key),
            ],
            cwd=repo,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "VERIFIED" in proc.stdout

        proc = run_cli(
            [
                "verify-pr", "--base", f"{commit}^", "--head", commit,
                "--min-tier", "ci-verified",
                "--ci-hmac-key-file", str(hmac_key),
            ],
            cwd=repo,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "Result: PASS" in proc.stdout

    def test_ci_issue_cli_refusal_exit_code(self, copy_provenance_repo, hmac_key):
        copied = copy_provenance_repo()
        repo = copied["repo"]
        proc = run_cli(
            [
                "ci-issue", "--commit", copied["human_commit"],
                "--signer", "hmac", "--key-file", str(hmac_key),
            ],
            cwd=repo,
            extra_env=CI_ENV,
        )
        assert proc.returncode == 1
        assert "REFUSED" in proc.stdout

    def test_verify_pr_min_tier_cli_fails_without_ci(self, copy_provenance_repo):
        copied = copy_provenance_repo()
        commit = copied["mission_b"]["attestation"]["note_commit"]
        proc = run_cli(
            [
                "verify-pr", "--base", f"{commit}^", "--head", commit,
                "--min-tier", "ci-verified",
            ],
            cwd=copied["repo"],
        )
        assert proc.returncode == 1
        assert "Result: FAIL" in proc.stdout
