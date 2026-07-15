"""Tests for sao.provenance.verify_pr — the PR enforcement gate."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from sao.provenance import attest, verify_pr

from conftest import init_git_repo, record_committing_mission, human_commit

pytest.importorskip("qrcode", reason="qrcode[pil] required to record missions")


def get_checks(report: dict, commit: str) -> dict:
    entry = next(c for c in report["commits"] if c["commit"] == commit)
    return {c["name"]: c for c in entry["checks"]}


def run_cli(args, cwd):
    return subprocess.run(
        [sys.executable, "-m", "sao.cli", *args],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8",
    )


class TestHappyPath:
    def test_all_attested_commits_pass(self, provenance_repo):
        report = verify_pr.verify_pr(
            provenance_repo["repo"],
            base=provenance_repo["base_commit"],
            head="HEAD",
        )
        assert report["ok"]
        assert report["commit_count"] == 3  # A, B, human
        assert report["counts"]["attested"] == 2
        assert report["counts"]["unattested"] == 1
        assert report["counts"]["failed"] == 0

    def test_attested_commit_checks(self, provenance_repo):
        report = verify_pr.verify_pr(
            provenance_repo["repo"],
            base=provenance_repo["base_commit"],
            head="HEAD",
        )
        commit_a = provenance_repo["mission_a"]["attestation"]["note_commit"]
        checks = get_checks(report, commit_a)
        assert checks["hash-chain"]["level"] == "OK"
        assert checks["ledger-inclusion"]["level"] == "OK"
        assert checks["ledger-consistency"]["level"] == "OK"
        assert checks["diff"]["level"] == "OK"
        assert checks["session-copy"]["level"] == "OK"
        assert checks["signature"]["level"] == "SKIP"
        assert checks["scope"]["level"] == "OK"  # mission a had a flight plan

        commit_b = provenance_repo["mission_b"]["attestation"]["note_commit"]
        checks_b = get_checks(report, commit_b)
        assert checks_b["hash-chain"]["level"] == "OK"
        assert checks_b["scope"]["level"] == "SKIP"  # no flight plan filed

    def test_second_mission_chain_links(self, provenance_repo):
        report = verify_pr.verify_pr(
            provenance_repo["repo"],
            base=provenance_repo["base_commit"],
            head="HEAD",
        )
        commit_b = provenance_repo["mission_b"]["attestation"]["note_commit"]
        checks = get_checks(report, commit_b)
        assert provenance_repo["mission_a"]["mission_id"] in checks["hash-chain"]["detail"]


class TestUnattestedCommits:
    def test_warn_by_default(self, provenance_repo):
        report = verify_pr.verify_pr(
            provenance_repo["repo"],
            base=provenance_repo["base_commit"],
            head="HEAD",
        )
        checks = get_checks(report, provenance_repo["human_commit"])
        assert checks["attestation"]["level"] == "WARN"
        assert report["ok"]

    def test_fail_with_require_attestation(self, provenance_repo):
        report = verify_pr.verify_pr(
            provenance_repo["repo"],
            base=provenance_repo["base_commit"],
            head="HEAD",
            require_attestation=True,
        )
        checks = get_checks(report, provenance_repo["human_commit"])
        assert checks["attestation"]["level"] == "FAIL"
        assert not report["ok"]


class TestScopeDrift:
    @pytest.fixture
    def drifting_repo(self, tmp_path: Path) -> dict:
        from sao.provenance import flightplan

        repo = tmp_path / "repo"
        repo.mkdir()
        init_git_repo(repo)
        base = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        flightplan.file_flight_plan(
            repo, name="drift", intent="stay in src", scope=["src/*"]
        )
        # Mission declares src/* but writes outside it.
        mission = record_committing_mission(
            repo, "drift", "outside/escape.py", "OOPS = True\n"
        )
        return {"repo": repo, "base": base, "mission": mission}

    def test_drift_is_warn_by_default(self, drifting_repo):
        report = verify_pr.verify_pr(
            drifting_repo["repo"], base=drifting_repo["base"], head="HEAD"
        )
        commit = drifting_repo["mission"]["attestation"]["note_commit"]
        checks = get_checks(report, commit)
        assert checks["scope"]["level"] == "WARN"
        assert "outside/escape.py" in checks["scope"]["detail"]
        assert report["ok"]

    def test_drift_fails_with_strict_scope(self, drifting_repo):
        report = verify_pr.verify_pr(
            drifting_repo["repo"], base=drifting_repo["base"], head="HEAD",
            strict_scope=True,
        )
        commit = drifting_repo["mission"]["attestation"]["note_commit"]
        assert get_checks(report, commit)["scope"]["level"] == "FAIL"
        assert not report["ok"]


class TestTampering:
    def test_tampered_session_diff_fails(self, copy_provenance_repo):
        copied = copy_provenance_repo()
        repo = copied["repo"]
        mission_id = copied["mission_a"]["mission_id"]
        diff_path = repo / "blackbox" / "sessions" / mission_id / "git_diff.patch"
        diff_path.write_text("--- forged diff ---\n", encoding="utf-8")

        report = verify_pr.verify_pr(
            repo, base=copied["base_commit"], head="HEAD"
        )
        commit = copied["mission_a"]["attestation"]["note_commit"]
        assert get_checks(report, commit)["diff"]["level"] == "FAIL"
        assert not report["ok"]

    def test_broken_hash_chain_fails(self, copy_provenance_repo):
        copied = copy_provenance_repo()
        repo = copied["repo"]
        mission_id = copied["mission_b"]["mission_id"]
        session_dir = repo / "blackbox" / "sessions" / mission_id

        # Rewrite mission B's parent pointer consistently in BOTH the session
        # copy and the git note, so only the chain check can catch it.
        statement, _ = attest.load_attestation(session_dir)
        statement["parent_attestation_sha256"] = hashlib.sha256(
            b"someone rewrote history"
        ).hexdigest()
        text = attest.canonical_json(statement)
        (session_dir / "provenance.json").write_text(text, encoding="utf-8")
        commit = copied["mission_b"]["attestation"]["note_commit"]
        assert attest.attach_git_note(repo, commit, text)

        report = verify_pr.verify_pr(
            repo, base=copied["base_commit"], head="HEAD"
        )
        checks = get_checks(report, commit)
        assert checks["hash-chain"]["level"] == "FAIL"
        assert checks["session-copy"]["level"] == "OK"
        assert not report["ok"]

    def test_note_differing_from_session_copy_fails(self, copy_provenance_repo):
        copied = copy_provenance_repo()
        repo = copied["repo"]
        mission_id = copied["mission_a"]["mission_id"]
        session_dir = repo / "blackbox" / "sessions" / mission_id
        statement, _ = attest.load_attestation(session_dir)
        statement["exit_code"] = 99  # tamper the note only
        commit = copied["mission_a"]["attestation"]["note_commit"]
        assert attest.attach_git_note(
            repo, commit, attest.canonical_json(statement)
        )
        report = verify_pr.verify_pr(
            repo, base=copied["base_commit"], head="HEAD"
        )
        assert get_checks(report, commit)["session-copy"]["level"] == "FAIL"
        assert not report["ok"]

    def test_leaf_not_in_ledger_fails(self, copy_provenance_repo):
        copied = copy_provenance_repo()
        repo = copied["repo"]
        # Truncate the ledger to zero entries: recorded positions dangle.
        (repo / "blackbox" / "ledger.jsonl").write_text("", encoding="utf-8")
        report = verify_pr.verify_pr(
            repo, base=copied["base_commit"], head="HEAD"
        )
        commit = copied["mission_a"]["attestation"]["note_commit"]
        assert get_checks(report, commit)["ledger-inclusion"]["level"] == "FAIL"
        assert not report["ok"]


class TestCliAndReports:
    def test_cli_pass_and_markdown(self, provenance_repo, tmp_path: Path):
        md_path = tmp_path / "report.md"
        proc = run_cli(
            [
                "verify-pr",
                "--base", provenance_repo["base_commit"],
                "--head", "HEAD",
                "--markdown", str(md_path),
            ],
            cwd=provenance_repo["repo"],
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "Result: PASS" in proc.stdout
        md = md_path.read_text(encoding="utf-8")
        assert md.startswith("# sao verify-pr — PASS")
        assert provenance_repo["mission_a"]["mission_id"] in md
        assert "| Commit | Mission | Check | Level | Detail |" in md

    def test_cli_fails_when_attestation_required(self, provenance_repo):
        proc = run_cli(
            [
                "verify-pr",
                "--base", provenance_repo["base_commit"],
                "--head", "HEAD",
                "--require-attestation",
            ],
            cwd=provenance_repo["repo"],
        )
        assert proc.returncode == 1
        assert "Result: FAIL" in proc.stdout

    def test_cli_bad_range(self, provenance_repo):
        proc = run_cli(
            ["verify-pr", "--base", "no-such-ref", "--head", "HEAD"],
            cwd=provenance_repo["repo"],
        )
        assert proc.returncode == 1
        assert "Error:" in proc.stderr

    def test_empty_range_passes(self, provenance_repo):
        report = verify_pr.verify_pr(
            provenance_repo["repo"], base="HEAD", head="HEAD"
        )
        assert report["ok"]
        assert report["commit_count"] == 0
