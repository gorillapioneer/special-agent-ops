"""Tests for sao.provenance.blame — line-level provenance."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from sao.provenance import blame

pytest.importorskip("qrcode", reason="qrcode[pil] required to record missions")


def run_cli(args, cwd):
    return subprocess.run(
        [sys.executable, "-m", "sao.cli", *args],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8",
    )


class TestParseLinePorcelain:
    def test_parses_real_blame_output(self, provenance_repo):
        repo = provenance_repo["repo"]
        proc = subprocess.run(
            ["git", "blame", "--line-porcelain", "--", "src/alpha.py"],
            cwd=repo, capture_output=True, text=True, encoding="utf-8",
        )
        rows = blame.parse_line_porcelain(proc.stdout)
        assert [r["line"] for r in rows] == [1, 2]
        assert rows[0]["text"] == "ALPHA = 1"
        assert rows[1]["text"] == "BETA = 2"
        assert all(len(r["commit"]) == 40 for r in rows)

    def test_empty_output(self):
        assert blame.parse_line_porcelain("") == []


class TestBlameMapping:
    def test_attested_lines_map_to_mission(self, provenance_repo):
        result = blame.blame_file(provenance_repo["repo"], "src/alpha.py")
        mission_id = provenance_repo["mission_a"]["mission_id"]
        assert result["file"] == "src/alpha.py"
        assert len(result["lines"]) == 2
        for line in result["lines"]:
            assert line["attested"] is True
            assert line["mission_id"] == mission_id
            assert line["commit_short"]
        assert mission_id in result["missions"].values()

    def test_unattested_lines_have_no_mission(self, provenance_repo):
        result = blame.blame_file(provenance_repo["repo"], "docs/notes.txt")
        assert len(result["lines"]) == 1
        line = result["lines"][0]
        assert line["attested"] is False
        assert line["mission_id"] is None
        assert result["missions"] == {}

    def test_mixed_file(self, copy_provenance_repo):
        """A file touched by both a mission and a human shows both origins."""
        from conftest import human_commit

        repo = copy_provenance_repo()["repo"]
        alpha = (repo / "src" / "alpha.py").read_text(encoding="utf-8")
        human_commit(
            repo, "src/alpha.py", alpha + "GAMMA = 3\n", "chore: human tweak"
        )
        result = blame.blame_file(repo, "src/alpha.py")
        attested = [l for l in result["lines"] if l["attested"]]
        unattested = [l for l in result["lines"] if not l["attested"]]
        assert len(attested) == 2
        assert len(unattested) == 1
        assert unattested[0]["text"] == "GAMMA = 3"

    def test_untracked_file_raises(self, provenance_repo):
        with pytest.raises(ValueError):
            blame.blame_file(provenance_repo["repo"], "does/not/exist.py")

    def test_result_carries_derived_confidence(self, provenance_repo):
        """Attribution is a derived, best-effort view and must say so."""
        result = blame.blame_file(provenance_repo["repo"], "src/alpha.py")
        assert result["confidence"] == "derived-best-effort"
        assert "git blame" in result["confidence_note"]


class TestRenderText:
    def test_annotated_listing(self, provenance_repo):
        result = blame.blame_file(provenance_repo["repo"], "src/beta.py")
        text = blame.render_text(result)
        mission_id = provenance_repo["mission_b"]["mission_id"]
        assert "LINE" in text and "MISSION" in text and "COMMIT" in text
        assert mission_id in text
        assert "attributable to" in text
        # Footer honesty note: attribution is derived and best-effort.
        assert "NOTE:" in text
        assert "best-effort" in text

    def test_unattested_shown_as_dash(self, provenance_repo):
        result = blame.blame_file(provenance_repo["repo"], "docs/notes.txt")
        text = blame.render_text(result)
        assert "  -  " in text or " - " in text
        assert "0/1 line(s)" in text

    def test_long_lines_truncated(self):
        result = {
            "file": "f",
            "lines": [{
                "line": 1, "commit": "c" * 40, "commit_short": "c" * 10,
                "mission_id": None, "attested": False, "text": "y" * 200,
            }],
            "missions": {},
        }
        text = blame.render_text(result)
        assert "y" * 200 not in text
        assert "..." in text


class TestBlameCli:
    def test_cli_text_output(self, provenance_repo):
        proc = run_cli(["blame", "src/alpha.py"], cwd=provenance_repo["repo"])
        assert proc.returncode == 0, proc.stderr
        assert provenance_repo["mission_a"]["mission_id"] in proc.stdout

    def test_cli_json_output(self, provenance_repo):
        proc = run_cli(
            ["blame", "--json", "src/beta.py"], cwd=provenance_repo["repo"]
        )
        assert proc.returncode == 0, proc.stderr
        data = json.loads(proc.stdout)
        assert data["file"] == "src/beta.py"
        assert data["confidence"] == "derived-best-effort"
        assert all(
            l["mission_id"] == provenance_repo["mission_b"]["mission_id"]
            for l in data["lines"]
        )

    def test_cli_missing_file(self, provenance_repo):
        proc = run_cli(["blame", "missing.py"], cwd=provenance_repo["repo"])
        assert proc.returncode == 1
        assert "Error:" in proc.stderr
