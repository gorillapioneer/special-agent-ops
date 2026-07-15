"""End-to-end tests for sao.blackbox.recorder using real subprocesses and git."""

import json
import re
import sys
import zipfile
from pathlib import Path

import pytest

from sao.blackbox import recorder, seal


class TestSanitiseName:
    def test_lowercases_and_underscores(self):
        assert recorder._sanitise_name("Fix Login Bug!") == "fix_login_bug"

    def test_strips_leading_trailing_underscores(self):
        assert recorder._sanitise_name("--weird--") == "weird"

    def test_caps_at_40_chars(self):
        assert len(recorder._sanitise_name("x" * 100)) == 40

    def test_mission_id_format(self):
        mid = recorder._make_mission_id("My Mission")
        assert re.fullmatch(r"\d{8}_\d{6}_my_mission", mid)


class TestFormatCommandArgv:
    def test_simple_join(self):
        assert recorder.format_command_argv(["python", "--version"]) == "python --version"

    def test_quotes_arguments_with_spaces(self):
        formatted = recorder.format_command_argv(["echo", "hello world"])
        assert "hello world" in formatted
        assert formatted != "echo hello world"  # must be quoted


class TestRecordMissionArgv:
    """Shared read-only assertions against the session-scoped mission."""

    def test_status_pass_and_exit_code(self, recorded_mission):
        assert recorded_mission["exit_code"] == 0
        assert recorded_mission["status"] == "PASS"

    def test_manifest_contents(self, recorded_mission):
        manifest = json.loads(
            (recorded_mission["session_dir"] / "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["mission_id"] == recorded_mission["mission_id"]
        assert manifest["name"] == "Fixture Mission"
        assert manifest["command_mode"] == "argv"
        assert manifest["command_argv"][0] == sys.executable
        assert manifest["exit_code"] == 0
        assert manifest["git_branch"] == "main"
        assert len(manifest["git_commit_before"]) == 40
        assert manifest["git_commit_before"] == manifest["git_commit_after"]
        assert manifest["duration_seconds"] >= 0

    def test_changed_files_capture_command_output(self, recorded_mission):
        manifest = json.loads(
            (recorded_mission["session_dir"] / "manifest.json").read_text(encoding="utf-8")
        )
        assert "artifact.txt" in manifest["changed_files"]
        assert manifest["changed_files_count"] == len(manifest["changed_files"])
        # The recorder's own session files must not be listed.
        assert all(not f.startswith("blackbox/sessions/") for f in manifest["changed_files"])

    def test_stdout_and_stderr_recorded(self, recorded_mission):
        sd = recorded_mission["session_dir"]
        assert "mission stdout line" in (sd / "stdout.txt").read_text(encoding="utf-8")
        assert "mission stderr line" in (sd / "stderr.txt").read_text(encoding="utf-8")

    def test_all_artifacts_written(self, recorded_mission):
        sd = recorded_mission["session_dir"]
        for name in [
            "manifest.json", "stdout.txt", "stderr.txt",
            "git_status_before.txt", "git_status_after.txt", "git_diff.patch",
            "seal.json", "seal.txt", "seal_payload.json", "seal_card.md",
            "seal_qr_payload.json", "seal_qr_payload.txt", "seal_qr.png",
            "seal_card.html", "mission_summary.md",
        ]:
            assert (sd / name).exists(), f"missing artefact: {name}"

    def test_archive_exists_and_hash_matches_seal(self, recorded_mission):
        zip_path = recorded_mission["zip_path"]
        assert zip_path.exists()
        seal_data = json.loads(
            (recorded_mission["session_dir"] / "seal.json").read_text(encoding="utf-8")
        )
        assert seal.sha256_file(zip_path) == seal_data["archive_sha256"]
        assert recorded_mission["archive_sha256"] == seal_data["archive_sha256"]

    def test_archive_contains_raw_files_only(self, recorded_mission):
        # The zip is created before sealing, so derived files stay out of it.
        with zipfile.ZipFile(recorded_mission["zip_path"]) as zf:
            names = {Path(n).name for n in zf.namelist()}
        assert "manifest.json" in names
        assert "stdout.txt" in names
        assert "seal.json" not in names
        assert "mission_summary.md" not in names

    def test_summary_references_seal_hashes(self, recorded_mission):
        sd = recorded_mission["session_dir"]
        summary_text = (sd / "mission_summary.md").read_text(encoding="utf-8")
        seal_data = json.loads((sd / "seal.json").read_text(encoding="utf-8"))
        assert seal_data["archive_sha256"] in summary_text
        assert seal_data["manifest_sha256"] in summary_text
        assert "Fixture Mission" in summary_text

    def test_qr_payload_matches_seal(self, recorded_mission):
        sd = recorded_mission["session_dir"]
        qr = json.loads((sd / "seal_qr_payload.txt").read_text(encoding="utf-8"))
        seal_data = json.loads((sd / "seal.json").read_text(encoding="utf-8"))
        assert qr["sha256"] == seal_data["archive_sha256"]
        assert qr["id"] == recorded_mission["mission_id"]
        assert qr["status"] == "PASS"


class TestRecordMissionFailures:
    def test_failing_command_reports_fail(self, git_repo):
        pytest.importorskip("qrcode")
        result = recorder.record_mission_argv(
            name="failing mission",
            command_argv=[sys.executable, "-c", "import sys; sys.exit(7)"],
            repo_path=git_repo,
        )
        assert result["exit_code"] == 7
        assert result["status"] == "FAIL"
        payload = json.loads(
            (result["session_dir"] / "seal_payload.json").read_text(encoding="utf-8")
        )
        assert payload["status"] == "FAIL"

    def test_shell_mode_records_command_string(self, git_repo):
        pytest.importorskip("qrcode")
        result = recorder.record_mission(
            name="shell mission",
            command="echo shell-mode-output",
            repo_path=git_repo,
        )
        assert result["exit_code"] == 0
        manifest = json.loads(
            (result["session_dir"] / "manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["command_mode"] == "shell"
        assert "command_argv" not in manifest
        stdout = (result["session_dir"] / "stdout.txt").read_text(encoding="utf-8")
        assert "shell-mode-output" in stdout

    def test_nonexistent_argv_command_fails_gracefully(self, git_repo):
        pytest.importorskip("qrcode")
        result = recorder.record_mission_argv(
            name="broken",
            command_argv=["definitely-not-a-real-binary-xyz"],
            repo_path=git_repo,
        )
        assert result["exit_code"] == 1
        assert result["status"] == "FAIL"
        stderr = (result["session_dir"] / "stderr.txt").read_text(encoding="utf-8")
        assert "Failed to start command" in stderr

    def test_invalid_command_mode_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            recorder._record_mission("x", "echo", repo_path=tmp_path, command_mode="bogus")

    def test_argv_mode_requires_argv(self, tmp_path):
        with pytest.raises(ValueError):
            recorder._record_mission("x", "echo", repo_path=tmp_path, command_mode="argv")
