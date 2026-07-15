"""Tests for sao.provenance.flightplan — pre-declared mission scope."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from sao.provenance import flightplan

from conftest import init_git_repo, record_committing_mission


class TestFileFlightPlan:
    def test_writes_pending_file(self, tmp_path: Path):
        path = flightplan.file_flight_plan(
            tmp_path, name="m", intent="do things", scope=["src/*", "tests/*"]
        )
        assert path == tmp_path / "blackbox" / "flightplan.pending.json"
        plan = json.loads(path.read_text(encoding="utf-8"))
        assert plan["version"] == "sao-flightplan/1"
        assert plan["name"] == "m"
        assert plan["intent"] == "do things"
        assert plan["scope"] == ["src/*", "tests/*"]
        assert plan["filed_at"]

    def test_empty_scope_rejected(self, tmp_path: Path):
        with pytest.raises(ValueError):
            flightplan.file_flight_plan(tmp_path, name="m", intent="i", scope=[])

    def test_load_pending_roundtrip(self, tmp_path: Path):
        assert flightplan.load_pending(tmp_path) is None
        flightplan.file_flight_plan(tmp_path, name="m", intent="i", scope=["*"])
        assert flightplan.load_pending(tmp_path)["name"] == "m"


class TestConsumePending:
    def test_consume_moves_into_session(self, tmp_path: Path):
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        flightplan.file_flight_plan(tmp_path, name="m", intent="i", scope=["src/*"])
        plan = flightplan.consume_pending(tmp_path, session_dir)
        assert plan["consumed_at"]
        assert not flightplan.pending_path(tmp_path).exists()
        stored = flightplan.load_session_plan(session_dir)
        assert stored["scope"] == ["src/*"]

    def test_consume_without_pending_is_noop(self, tmp_path: Path):
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        assert flightplan.consume_pending(tmp_path, session_dir) is None
        assert flightplan.load_session_plan(session_dir) is None


class TestScope:
    def test_glob_matching(self):
        globs = ["src/*", "docs/README.md"]
        assert flightplan.path_in_scope("src/mod.py", globs)
        assert flightplan.path_in_scope("src/pkg/deep.py", globs)  # * spans /
        assert flightplan.path_in_scope("docs/README.md", globs)
        assert not flightplan.path_in_scope("setup.py", globs)
        assert not flightplan.path_in_scope("docs/other.md", globs)

    def test_recorder_artifacts_always_in_scope(self):
        assert flightplan.path_in_scope("blackbox/ledger.jsonl", ["src/*"])
        assert flightplan.path_in_scope("blackbox", ["src/*"])

    def test_backslash_paths_normalised(self):
        assert flightplan.path_in_scope("src\\mod.py", ["src/*"])

    def test_check_scope_classification(self):
        result = flightplan.check_scope(
            ["src/a.py", "README.md", "blackbox/ledger.jsonl"], ["src/*"]
        )
        assert result["in_scope"] == ["src/a.py", "blackbox/ledger.jsonl"]
        assert result["out_of_scope"] == ["README.md"]
        assert not result["ok"]

    def test_check_scope_all_in(self):
        result = flightplan.check_scope(["src/a.py"], ["src/*"])
        assert result["ok"]


class TestRecorderIntegration:
    """The recorder consumes a pending plan into the sealed session."""

    def test_plan_consumed_and_sealed(self, git_repo: Path):
        pytest.importorskip("qrcode", reason="qrcode[pil] required to record missions")
        from sao.blackbox import browser

        flightplan.file_flight_plan(
            git_repo, name="scoped", intent="add module", scope=["src/*"]
        )
        result = record_committing_mission(
            git_repo, "scoped", "src/mod.py", "X = 1\n", attest=False
        )
        assert result["flightplan_consumed"] is True
        assert not flightplan.pending_path(git_repo).exists()

        session_dir = result["session_dir"]
        plan = flightplan.load_session_plan(session_dir)
        assert plan["name"] == "scoped"

        # flightplan.json is written BEFORE sealing, so it is covered:
        assert browser.verify_mission(session_dir)["verified"]
        plan["scope"] = ["**"]  # widen scope after the fact
        (session_dir / "flightplan.json").write_text(
            json.dumps(plan, indent=2), encoding="utf-8"
        )
        assert not browser.verify_mission(session_dir)["session_directory_ok"]

    def test_no_plan_means_no_flightplan_file(self, git_repo: Path):
        pytest.importorskip("qrcode", reason="qrcode[pil] required to record missions")
        result = record_committing_mission(
            git_repo, "unplanned", "src/other.py", "Y = 2\n", attest=False
        )
        assert result["flightplan_consumed"] is False
        assert not (result["session_dir"] / "flightplan.json").exists()


class TestFlightPlanCli:
    def test_cli_files_plan(self, git_repo: Path):
        proc = subprocess.run(
            [
                sys.executable, "-m", "sao.cli", "flight-plan",
                "--name", "cli plan", "--intent", "test the cli",
                "--scope", "src/*", "--scope", "tests/*",
            ],
            cwd=git_repo, capture_output=True, text=True, encoding="utf-8",
        )
        assert proc.returncode == 0, proc.stderr
        assert "FLIGHT PLAN FILED" in proc.stdout
        plan = flightplan.load_pending(git_repo)
        assert plan["scope"] == ["src/*", "tests/*"]
