"""Tests for sao.blackbox.seal_card — payload builder and Markdown card."""

import json
from pathlib import Path

from sao.blackbox import seal_card

MANIFEST = {
    "mission_id": "20260101_120000_demo",
    "name": "demo mission",
    "repo_path": "/tmp/repo",
    "started_at": "2026-01-01T12:00:00+00:00",
    "ended_at": "2026-01-01T12:00:01+00:00",
    "command": "echo hi",
}

SEAL = {
    "archive_sha256": "b" * 64,
    "seal_version": "0.2",
}


class TestBuildSealPayload:
    def test_pass_status_for_zero_exit(self):
        p = seal_card.build_seal_payload(MANIFEST, SEAL, exit_code=0, changed_files=[])
        assert p["status"] == "PASS"
        assert p["exit_code"] == 0
        assert p["changed_files_count"] == 0
        assert p["archive_sha256"] == "b" * 64
        assert p["mission_id"] == "20260101_120000_demo"

    def test_fail_status_for_nonzero_exit(self):
        p = seal_card.build_seal_payload(
            MANIFEST, SEAL, exit_code=3, changed_files=["a.py", "b.py"]
        )
        assert p["status"] == "FAIL"
        assert p["changed_files_count"] == 2


class TestRenderSealCard:
    def test_card_contains_key_fields(self):
        p = seal_card.build_seal_payload(MANIFEST, SEAL, exit_code=0, changed_files=["x"])
        card = seal_card.render_seal_card(p)
        assert "SPECIAL AGENT OPS MISSION CARD" in card
        assert "demo mission" in card
        assert "20260101_120000_demo" in card
        assert "Status: PASS" in card
        assert "b" * 64 in card
        assert "Changed Files: 1" in card

    def test_unknown_status_defaults(self):
        card = seal_card.render_seal_card({
            "name": "n", "mission_id": "m", "command": "c",
            "changed_files_count": 0, "archive_sha256": "s", "seal_version": "0.2",
        })
        assert "Status: UNKNOWN" in card


class TestWriteSealCard:
    def test_writes_payload_and_card(self, tmp_path: Path):
        p = seal_card.build_seal_payload(MANIFEST, SEAL, exit_code=0, changed_files=[])
        paths = seal_card.write_seal_card(tmp_path, p)
        payload_path = paths["seal_payload_path"]
        card_path = paths["seal_card_path"]
        assert payload_path == tmp_path / "seal_payload.json"
        assert card_path == tmp_path / "seal_card.md"
        assert json.loads(payload_path.read_text(encoding="utf-8")) == p
        assert "MISSION CARD" in card_path.read_text(encoding="utf-8")
