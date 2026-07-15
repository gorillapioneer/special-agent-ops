"""End-to-end CLI tests, invoking `python -m sao.cli` in a real git repo."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import init_git_repo

pytest.importorskip("qrcode", reason="qrcode[pil] required for CLI recording tests")


def run_cli(args, cwd: Path):
    return subprocess.run(
        [sys.executable, "-m", "sao.cli", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


@pytest.fixture(scope="module")
def cli_repo(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """A git repo with one mission recorded through the real CLI."""
    repo = tmp_path_factory.mktemp("cli_repo")
    init_git_repo(repo)
    proc = run_cli(
        ["run", "--name", "cli mission", "--command", "echo cli-recorded"],
        cwd=repo,
    )
    assert proc.returncode == 0, proc.stderr
    sessions = list((repo / "blackbox" / "sessions").iterdir())
    session_dir = next(p for p in sessions if p.is_dir())
    return {"repo": repo, "session_dir": session_dir, "mission_id": session_dir.name}


class TestRun:
    def test_banner_and_artifacts(self, cli_repo):
        # Recorded in the fixture; assert on-disk results here.
        sd = cli_repo["session_dir"]
        manifest = json.loads((sd / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["name"] == "cli mission"
        assert manifest["exit_code"] == 0
        assert "cli-recorded" in (sd / "stdout.txt").read_text(encoding="utf-8")

    def test_exit_code_propagates_failure(self, cli_repo):
        proc = run_cli(
            ["run", "--name", "fails", "--command", "exit 5"],
            cwd=cli_repo["repo"],
        )
        assert proc.returncode == 5
        assert "FAIL" in proc.stdout


class TestWrap:
    def test_wrap_argv_command(self, cli_repo):
        proc = run_cli(
            ["wrap", "--name", "wrapped", "--", sys.executable, "-c", "print('wrapped-out')"],
            cwd=cli_repo["repo"],
        )
        assert proc.returncode == 0, proc.stderr
        assert "Mode:     argv" in proc.stdout
        assert "MISSION COMPLETE" in proc.stdout

    def test_wrap_without_command_errors(self, cli_repo):
        proc = run_cli(["wrap", "--name", "empty", "--"], cwd=cli_repo["repo"])
        assert proc.returncode == 2
        assert "wrap requires a command" in proc.stderr


class TestList:
    def test_lists_recorded_missions(self, cli_repo):
        proc = run_cli(["list"], cwd=cli_repo["repo"])
        assert proc.returncode == 0
        assert cli_repo["mission_id"] in proc.stdout
        assert "PASS" in proc.stdout

    def test_empty_repo_message(self, tmp_path: Path):
        proc = run_cli(["list"], cwd=tmp_path)
        assert proc.returncode == 0
        assert "No missions recorded yet" in proc.stdout


class TestShow:
    def test_show_mission_detail(self, cli_repo):
        proc = run_cli(["show", cli_repo["mission_id"]], cwd=cli_repo["repo"])
        assert proc.returncode == 0
        assert "MISSION DETAIL" in proc.stdout
        assert cli_repo["mission_id"] in proc.stdout
        assert "Status:           PASS" in proc.stdout

    def test_show_unknown_mission(self, cli_repo):
        proc = run_cli(["show", "nope_123"], cwd=cli_repo["repo"])
        assert proc.returncode == 1
        assert "Mission not found" in proc.stderr


class TestVerify:
    def test_verify_ok(self, cli_repo):
        proc = run_cli(["verify", cli_repo["mission_id"]], cwd=cli_repo["repo"])
        assert proc.returncode == 0
        assert "Result: VERIFIED" in proc.stdout

    def test_verify_detects_tampering(self, cli_repo, tmp_path: Path):
        # Copy the whole repo so tampering doesn't pollute the shared fixture.
        import shutil

        repo_copy = tmp_path / "tampered_repo"
        shutil.copytree(cli_repo["repo"], repo_copy)
        session = repo_copy / "blackbox" / "sessions" / cli_repo["mission_id"]
        (session / "stdout.txt").write_text("rewritten history", encoding="utf-8")

        proc = run_cli(["verify", cli_repo["mission_id"]], cwd=repo_copy)
        assert proc.returncode == 1
        assert "Result: FAILED" in proc.stdout

    def test_verify_unknown_mission(self, cli_repo):
        proc = run_cli(["verify", "nope_123"], cwd=cli_repo["repo"])
        assert proc.returncode == 1


class TestVerifyArchive:
    def test_verify_archive_ok(self, cli_repo):
        zip_path = (
            cli_repo["repo"] / "blackbox" / "sessions" / f"{cli_repo['mission_id']}.zip"
        )
        proc = run_cli(["verify-archive", str(zip_path)], cwd=cli_repo["repo"])
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "Result: VERIFIED" in proc.stdout

    def test_verify_archive_missing(self, cli_repo):
        proc = run_cli(["verify-archive", "ghost.zip"], cwd=cli_repo["repo"])
        assert proc.returncode == 1
        assert "Error" in proc.stderr


class TestPrReport:
    def test_report_to_stdout(self, cli_repo):
        proc = run_cli(["pr-report", cli_repo["mission_id"]], cwd=cli_repo["repo"])
        assert proc.returncode == 0
        assert "# Special Agent Ops Mission Report" in proc.stdout
        assert cli_repo["mission_id"] in proc.stdout

    def test_report_to_file_keeps_mission_verifiable(self, cli_repo):
        # Regression for the pr_report.md seal exclusion: writing the report
        # into the session folder must not break `sao verify`.
        out = cli_repo["session_dir"] / "pr_report.md"
        proc = run_cli(
            ["pr-report", cli_repo["mission_id"], "--output", str(out)],
            cwd=cli_repo["repo"],
        )
        assert proc.returncode == 0
        assert out.exists()

        verify = run_cli(["verify", cli_repo["mission_id"]], cwd=cli_repo["repo"])
        assert verify.returncode == 0, verify.stdout
        assert "Result: VERIFIED" in verify.stdout

    def test_report_unknown_mission(self, cli_repo):
        proc = run_cli(["pr-report", "nope_123"], cwd=cli_repo["repo"])
        assert proc.returncode == 1


class TestMap:
    def test_map_generates_html(self, cli_repo):
        proc = run_cli(["map"], cwd=cli_repo["repo"])
        assert proc.returncode == 0
        assert "MAPROOM" in proc.stdout
        map_path = cli_repo["repo"] / "blackbox" / "maproom.html"
        assert map_path.exists()
        assert cli_repo["mission_id"] in map_path.read_text(encoding="utf-8")


class TestParser:
    def test_no_subcommand_errors(self, tmp_path: Path):
        proc = run_cli([], cwd=tmp_path)
        assert proc.returncode == 2

    def test_help(self, tmp_path: Path):
        proc = run_cli(["--help"], cwd=tmp_path)
        assert proc.returncode == 0
        assert "verify-archive" in proc.stdout
