"""Tests for sao.blackbox.pr_report — PR-ready Markdown mission reports."""

import json
from pathlib import Path

import pytest

from sao.blackbox import pr_report


def _make_minimal_session(tmp_path: Path, exit_code=0) -> Path:
    """A hand-built session directory (no recording needed)."""
    session = tmp_path / "blackbox" / "sessions" / "20260101_120000_demo"
    session.mkdir(parents=True)
    manifest = {
        "mission_id": "20260101_120000_demo",
        "name": "demo mission",
        "repo_path": str(tmp_path),
        "command": "echo `hi`",
        "command_mode": "shell",
        "started_at": "2026-01-01T12:00:00+00:00",
        "ended_at": "2026-01-01T12:00:01+00:00",
        "exit_code": exit_code,
        "git_branch": "main",
        "changed_files_count": 2,
        "changed_files": ["src/app.py", "docs/`notes`.md"],
    }
    (session / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (session / "seal.json").write_text(
        json.dumps({"archive_sha256": "c" * 64, "seal_version": "0.2"}),
        encoding="utf-8",
    )
    (session / "seal_card.md").write_text("card", encoding="utf-8")
    return session


class TestBuildPayload:
    def test_payload_from_manifest_and_seal(self, tmp_path: Path):
        session = _make_minimal_session(tmp_path)
        payload = pr_report.build_pr_report_payload(session)
        assert payload["mission_id"] == "20260101_120000_demo"
        assert payload["mission"] == "demo mission"
        assert payload["status"] == "PASS"
        assert payload["exit_code"] == 0
        assert payload["archive_sha256"] == "c" * 64
        assert payload["seal_version"] == "0.2"
        assert payload["branch"] == "main"
        assert payload["changed_files"] == ["src/app.py", "docs/`notes`.md"]

    def test_fail_status(self, tmp_path: Path):
        session = _make_minimal_session(tmp_path, exit_code=2)
        assert pr_report.build_pr_report_payload(session)["status"] == "FAIL"

    def test_missing_manifest_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            pr_report.build_pr_report_payload(tmp_path)

    def test_artifact_paths_relative_and_existing_only(self, tmp_path: Path):
        session = _make_minimal_session(tmp_path)
        payload = pr_report.build_pr_report_payload(session)
        artifacts = payload["artifact_paths"]
        # Only seal_card.md exists in the minimal session.
        assert set(artifacts) == {"seal_card"}
        assert artifacts["seal_card"] == (
            "blackbox/sessions/20260101_120000_demo/seal_card.md"
        )

    def test_real_mission_payload(self, recorded_mission):
        payload = pr_report.build_pr_report_payload(recorded_mission["session_dir"])
        assert payload["status"] == "PASS"
        assert payload["command_mode"] == "argv"
        assert "archive" in payload["artifact_paths"]
        assert "html_card" in payload["artifact_paths"]
        assert "qr_image" in payload["artifact_paths"]


class TestRenderMarkdown:
    def test_report_sections_and_fields(self, tmp_path: Path):
        session = _make_minimal_session(tmp_path)
        md = pr_report.render_pr_report_markdown(pr_report.build_pr_report_payload(session))
        assert "# Special Agent Ops Mission Report" in md
        assert "## Summary" in md
        assert "## Verification" in md
        assert "## Changed Files" in md
        assert "## Local Artifacts" in md
        assert "sao verify 20260101_120000_demo" in md
        assert "c" * 64 in md
        # No stdout/stderr embedding (privacy promise in the footer).
        assert "does not embed stdout" in md

    def test_backticks_escaped_in_inline_code(self, tmp_path: Path):
        session = _make_minimal_session(tmp_path)
        md = pr_report.render_pr_report_markdown(pr_report.build_pr_report_payload(session))
        assert r"echo \`hi\`" in md
        assert r"docs/\`notes\`.md" in md

    def test_no_changed_files_message(self, tmp_path: Path):
        session = _make_minimal_session(tmp_path)
        manifest_path = session / "manifest.json"
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        m["changed_files"] = []
        m["changed_files_count"] = 0
        manifest_path.write_text(json.dumps(m), encoding="utf-8")
        md = pr_report.render_pr_report_markdown(pr_report.build_pr_report_payload(session))
        assert "No changed files recorded." in md

    def test_archive_verification_command_when_archive_exists(self, recorded_mission):
        payload = pr_report.build_pr_report_payload(recorded_mission["session_dir"])
        md = pr_report.render_pr_report_markdown(payload)
        assert "sao verify-archive" in md


class TestWriteReport:
    def test_default_output_in_session_dir(self, copy_mission):
        copy = copy_mission()
        out = pr_report.write_pr_report(copy["session_dir"])
        assert out == copy["session_dir"] / "pr_report.md"
        assert "Mission Report" in out.read_text(encoding="utf-8")

    def test_explicit_output_path(self, tmp_path: Path):
        session = _make_minimal_session(tmp_path)
        out = pr_report.write_pr_report(session, tmp_path / "reports" / "r.md")
        assert out == tmp_path / "reports" / "r.md"
        assert out.exists()
